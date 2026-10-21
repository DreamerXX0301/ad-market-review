"""Manual Fuyao synchronization, resumable accepted batches and grouped publication."""
from __future__ import annotations
from datetime import datetime,date,time,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import shutil
import re
from math import isfinite
import duckdb
import pandas as pd

from app.core import connect,data_dir,raw_dir,get_market,setting,now_ms,new_id,dumps,digest,hash_file,atomic_json
from app.analytics.market import MarketData,INDEX_NAMES,MEMBERS,date_ms,pool_coverage
from app.services import source_service
from app.services.bootstrap import register_market
from app.services.task_service import TaskPaused,TaskCancelled,finish
from app.services.three_year_calendar import validate_history_calendar,index_dates,index_window_begin as index_history_begin
from app.services.history_window import resolve_initial_window,window_start
from app.services.sync_clock import safe_completion_date, require_new_increment, validate_frozen_target

CHINA=ZoneInfo('Asia/Shanghai')
INDEX_REQUEST_PLAN = 'bounded-window-v2'
MASTER_REQUEST_PARAMS = {'asset_type':'a-share','exchange':'SH,SZ,BJ','limit':10000,'offset':0}
RAW_COLUMNS='thscode,currency,interval,adjusted,date_ms,open_price,high_price,low_price,close_price,volume,turnover'


def now_china():return datetime.now(CHINA)
def midnight_ms(day):return int(datetime.combine(date.fromisoformat(day),time(),CHINA).timestamp()*1000)
def response_items(payload):
    rows=payload.get('data',{}).get('item')
    if not isinstance(rows,list):raise ValueError('来源item格式变化，拒绝将未知结构当空数据')
    return rows


def frozen_master_request_params(plan):
    # No marker means the exact legacy request. Adding exchange to an accepted
    # old batch would change its signature and make paused jobs unresumable.
    if 'master_request_params' not in plan:
        return {'asset_type':'a-share','limit':10000,'offset':0}
    params=plan['master_request_params']
    if (not isinstance(params,dict) or params!={'asset_type':'a-share','exchange':'SH,SZ,BJ','limit':10000,'offset':0}
            or type(params.get('limit')) is not int or type(params.get('offset')) is not int):
        raise ValueError('冻结证券目录请求参数不受支持，拒绝改变已有请求签名')
    return dict(params)


def safe_payload(value):
    if isinstance(value,dict):return {k:safe_payload(v) for k,v in value.items() if k.lower() not in ('authorization','api_key','apikey','token','presigned_url','presigned_url_expires_at')}
    if isinstance(value,list):return [safe_payload(v) for v in value]
    return value


class Collector:
    def __init__(self,job_id,adapter,progress=None):
        self.job_id=job_id;self.adapter=adapter;self.progress=progress
        self.root=raw_dir()/'sync'/job_id;self.root.mkdir(parents=True,exist_ok=True)
        self.state_path=self.root/'checkpoint.json'
        self.state=json.loads(self.state_path.read_text()) if self.state_path.exists() else {'version':1,'files':{}}
        (self.root/'accepted').mkdir(exist_ok=True);(self.root/'raw').mkdir(exist_ok=True)

    def tick(self,stage,pct):
        if self.progress:self.progress(stage,pct)
        with connect() as c:row=c.execute('SELECT status FROM job WHERE job_id=?',(self.job_id,)).fetchone()
        if not row or row[0]=='cancelled':raise TaskCancelled()
        if row[0]=='paused':raise TaskPaused()

    def _existing(self,name,signature):
        accepted=self.state['files'].get(name)
        if not accepted:return None
        path=self.root/'accepted'/name
        if accepted['signature']!=signature or not path.is_file() or hash_file(path)!=accepted['sha256']:raise ValueError('已确认批次内容或请求依据变化，请手动重试为新任务；不复用损坏缓存')
        return path

    def _accept(self,name,path,signature,rows=0):
        sha=hash_file(path)
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute("SELECT content_sha256 FROM sync_batch WHERE job_id=? AND batch_key=? AND status='accepted'",(self.job_id,name)).fetchone()
            if old and old[0]!=sha:raise ValueError('同任务已验收批次hash冲突')
            if not old:
                attempt=c.execute('SELECT coalesce(max(attempt_no),0)+1 FROM sync_batch WHERE job_id=? AND batch_key=?',(self.job_id,name)).fetchone()[0]
                c.execute("INSERT INTO sync_batch(job_id,capability,batch_key,attempt_no,status,fetched_at_ms,row_count,content_sha256,diagnostic_json) VALUES(?,?,?,?,?,?,?,?,?)",(self.job_id,'sync',name,attempt,'accepted',now_ms(),rows,sha,dumps({'request_signature':signature,'bytes':path.stat().st_size})))
        self.state['files'][name]={'signature':signature,'sha256':sha,'rows':rows,'fetched_at_ms':now_ms(),'observed_at':now_china().isoformat()}
        atomic_json(self.state_path,self.state)

    def request(self,name,path,params=None,pct=10):
        self.tick('同步'+name,pct);signature=digest({'path':path,'params':params or {}})
        cached=self._existing(name,signature)
        if cached:return json.loads(cached.read_text())
        payload=safe_payload(self.adapter.request(path,params or {}))
        target=self.root/'accepted'/name;atomic_json(target,payload)
        rows=len(payload.get('data',{}).get('item',[])) if isinstance(payload.get('data'),dict) else 0
        self._accept(name,target,signature,rows)
        return payload

    def download(self,kind,pct=20):
        name=kind+'.parquet';signature=digest({'kind':kind,'provider':'fuyao'})
        self.tick('下载'+kind,pct);cached=self._existing(name,signature)
        if cached:return cached
        target=self.root/'accepted'/name
        last=-1
        def on_bytes(done,total):
            nonlocal last
            step=min(49,pct+int((25*done/total) if total else 0))
            if step!=last:self.tick('下载'+kind,step);last=step
        self.adapter.download(kind,target,on_bytes)
        con=duckdb.connect(':memory:')
        try:rows=con.execute('SELECT count(*) FROM read_parquet(?)',[str(target)]).fetchone()[0]
        finally:con.close()
        if not rows:raise ValueError('下载数据包为空，未确认批次')
        self._accept(name,target,signature,rows)
        return target

    def canonical_json(self,name,payload):atomic_json(self.root/'raw'/name,payload)


def paged(collector,name,path,params=None,pct=55,size=200):
    records=[];page=1;total=None
    while True:
        payload=collector.request(name+f'.page-{page}.json',path,{**(params or {}),'page':page,'size':size},pct)
        batch=response_items(payload);records.extend(batch)
        pagination=payload.get('data',{}).get('pagination',{})
        declared=pagination.get('total')
        if declared is not None:
            if isinstance(declared,bool) or not isinstance(declared,int) or declared<0:raise ValueError('分页总量格式无效')
            if total is not None and total!=declared:raise ValueError('分页期间总量变化，请稍后重试')
            total=declared
        if (total is not None and len(records)>=total) or (total is None and len(batch)<size):break
        if not batch or page>=100:raise ValueError('分页不完整或超出保护上限')
        page+=1
    if total is not None and len(records)!=total:raise ValueError('分页累计条数与来源总量不符')
    result={**payload,'data':{**payload['data'],'item':records,'pagination':{'total':len(records),'pages':page,'complete':True}}}
    collector.canonical_json(name,result)
    return result


def validate_raw(con,table):
    invalid=con.execute(f"SELECT count(*) FROM {table} WHERE thscode IS NULL OR date_ms IS NULL OR adjusted IS DISTINCT FROM 'none' OR interval IS DISTINCT FROM '1d' OR currency IS DISTINCT FROM 'CNY' OR open_price IS NULL OR close_price IS NULL OR high_price IS NULL OR low_price IS NULL OR volume IS NULL OR turnover IS NULL OR NOT isfinite(open_price) OR NOT isfinite(close_price) OR NOT isfinite(high_price) OR NOT isfinite(low_price) OR NOT isfinite(volume) OR NOT isfinite(turnover) OR least(open_price,high_price,low_price,close_price)<=0 OR volume<0 OR turnover<0 OR high_price<greatest(open_price,low_price,close_price) OR low_price>least(open_price,high_price,close_price)").fetchone()[0]
    duplicate=con.execute(f'SELECT count(*)-count(distinct(thscode,date_ms)) FROM {table}').fetchone()[0]
    if invalid or duplicate:raise ValueError(f'原始行情校验失败：无效OHLC/口径{invalid}行，重复键{duplicate}行')


def merge_daily(incoming,previous,output,start,end,calendar,master):
    con=duckdb.connect(':memory:')
    try:
        con.execute("SET memory_limit='512MB'");con.execute('SET threads=2')
        con.execute('SET preserve_insertion_order=false')
        spill=output.parent/'.duckdb-spill';spill.mkdir(parents=True,exist_ok=True)
        con.execute("SET temp_directory='"+str(spill).replace("'","''")+"'")
        # Keep Parquet scans lazy. Materialising old + merged three-year wide
        # tables in an in-memory database exhausted the fixed memory budget.
        # Incoming keys are unique after validation, so an anti join preserves
        # latest-source priority without ranking the entire retained history.
        con.read_parquet(str(incoming)).filter(f'date_ms>={midnight_ms(start)} AND date_ms<={midnight_ms(end)+86400000-1}').project(RAW_COLUMNS).create_view('incoming')
        validate_raw(con,'incoming')
        if previous:
            con.read_parquet(str(previous)).project(RAW_COLUMNS).create_view('old')
            validate_raw(con,'old')
            overlap,changed=con.execute('SELECT count(*),count(*) FILTER(WHERE i.open_price<>o.open_price OR i.high_price<>o.high_price OR i.low_price<>o.low_price OR i.close_price<>o.close_price OR i.volume<>o.volume OR i.turnover<>o.turnover) FROM incoming i JOIN old o USING(thscode,date_ms)').fetchone()
            con.execute('CREATE VIEW merged_all AS SELECT o.* FROM old o ANTI JOIN incoming i USING(thscode,date_ms) UNION ALL SELECT * FROM incoming')
        else:
            overlap=changed=0;con.execute('CREATE VIEW merged_all AS SELECT * FROM incoming')
        con.table('merged_all').filter(f'date_ms>={midnight_ms(start)} AND date_ms<={midnight_ms(end)+86400000-1}').create_view('merged')
        count,actual_start,actual_end=con.execute('SELECT count(*),min(date_ms),max(date_ms) FROM merged').fetchone()
        if not count or date_ms(actual_start)!=start or date_ms(actual_end)!=end:raise ValueError('行情包尚未覆盖所需起止日，保留旧发布数据')
        actual_dates={date_ms(r[0]) for r in con.execute('SELECT distinct date_ms FROM merged').fetchall()}
        if not set(calendar).issubset(actual_dates):raise ValueError('全市场行情缺少完整市场交易日，不能发布')
        con.register('master_rows',pd.DataFrame([{'thscode':r['thscode'],'list_date':r.get('list_date'),'end_date':r.get('end_date')} for r in master]))
        con.register('calendar_rows',pd.DataFrame({'date':calendar}))
        missing=con.execute("SELECT count(*) FROM master_rows m CROSS JOIN calendar_rows c LEFT JOIN merged b ON b.thscode=m.thscode AND strftime(to_timestamp(b.date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d')=c.date WHERE m.list_date IS NOT NULL AND CAST(m.list_date AS VARCHAR)<=c.date AND (m.end_date IS NULL OR CAST(m.end_date AS VARCHAR)>=c.date) AND b.thscode IS NULL").fetchone()[0]
        output.parent.mkdir(parents=True,exist_ok=True)
        con.execute('COPY (SELECT * FROM merged ORDER BY thscode,date_ms) TO ? (FORMAT PARQUET,COMPRESSION ZSTD)',[str(output)])
        unknown_identity=[r['thscode'] for r in master if not r.get('list_date')]
        return {'rows':count,'unexplained_missing':None if unknown_identity else missing,'confirmed_missing_lower_bound':missing,'identity_unknown_count':len(unknown_identity),'identity_unknown_symbols':unknown_identity,'expected_coverage_state':'unknown' if unknown_identity else 'partial','overlap_rows':overlap,'revised_overlap_rows':changed,'missing_classification':'上市日期未知时总缺口不可核验；已知身份中的缺口尚未区分停牌/数据缺失，不填0'}
    finally:con.close()



def merge_retained_index(payload,symbol,retained,begin,target,calendar,request_begin):
    """Keep verified old raw index rows while refreshing only an in-range suffix.

    Empty incoming histories and incomplete merged calendars fail closed. Saved
    reference rows are immutable raw daily OHLC, never chart-adjusted values.
    """
    if not isinstance(retained,list) or not retained:
        raise ValueError(symbol+'旧指数原始日线缺失，不能用近期数据截短历史')
    rows=[]
    for row in retained:
        if not isinstance(row,dict) or row.get('source')!='fuyao' or row.get('price_basis')!='none':
            raise ValueError(symbol+'旧指数来源或原始价格口径不可核验')
        try:
            day=row['date']
            if day>target:raise ValueError('future')
            rows.append({'thscode':symbol,'date_ms':midnight_ms(day),
                         **{raw:row[field] for raw,field in (('open_price','open'),('high_price','high'),('low_price','low'),('close_price','close'),('volume','volume'),('turnover','amount'))}})
        except (KeyError,TypeError,ValueError):
            raise ValueError(symbol+'旧指数原始日期或OHLC缺失，拒绝合并') from None
    old={'data':{'thscode':symbol,'interval':'1d','item':rows}}
    old_target=max(row['date'] for row in retained)
    old_begin=min(row['date'] for row in retained)
    # Validation also rejects duplicate retained dates and corrupt finite/price values.
    index_dates(old,symbol,old_begin,old_target)
    incoming=response_items(payload)
    observed=index_dates(payload,symbol,request_begin,target)
    expected_suffix=[day for day in calendar if request_begin<=day<=target]
    if observed != expected_suffix:
        raise ValueError(symbol+'近期指数缺少市场交易日，拒绝用旧行掩盖来源缺口')
    merged={row['date_ms']:row for row in rows if begin<=date_ms(row['date_ms'])<=target}
    merged.update({row['date_ms']:row for row in incoming})
    result={**payload,'data':{**payload['data'],'item':[merged[key] for key in sorted(merged)]},
            'lineage':{'source':'fuyao','price_basis':'none','index_request_plan':INDEX_REQUEST_PLAN,
                       'retained_start':old_begin,'retained_end':old_target,'refresh_start':request_begin,
                       'refresh_end':target,'merge':'incoming_daily_revision_wins'}}
    if index_dates(result,symbol,begin,target) != calendar:
        raise ValueError(symbol+'合并指数与留存市场日历不一致，拒绝丢失历史')
    return result


def execute_sync(job_id,progress=None):
    with connect() as c:
        row=c.execute('SELECT * FROM job WHERE job_id=?',(job_id,)).fetchone()
        if not row:raise ValueError('同步任务不存在')
        params=json.loads(row['parameters_json']);kind=row['kind'];requested=row['target_date']
    if kind not in ('initialise','increment'):raise ValueError('不是手动初始化或增量任务')
    provider=params.get('source_id') or setting('active_source','fuyao')
    if provider!='fuyao':raise ValueError('当前全市场初始化/增量仅已适配扶摇批量数据；TickFlow可用于个别历史核验，不能冒充全市场快速同步')
    # Recover the frozen plan before credentials/network access. A new increment
    # starts only after 18:00; a paused older-date plan may resume unchanged.
    collector=Collector(job_id,None,progress)
    plan=collector.state.get('plan')
    if plan:
        if plan.get('kind')!=kind:raise ValueError('恢复任务检查点类型与任务不符')
        validate_frozen_target(plan.get('target'),now_china())
        frozen_master_request_params(plan)
        if kind=='initialise' and plan.get('window') is not None:resolve_initial_window({'window':plan['window']})
    elif kind=='increment':require_new_increment(now_china())
    else:resolve_initial_window(params,for_new_task=True)
    collector.adapter=source_service.client(provider)
    calendar_payload=collector.request('calendar.json','/api/a-share/calendar/trading-days',pct=2)
    source_calendar=sorted({date_ms(r['date_ms']) for r in response_items(calendar_payload)})
    current=get_market();old=current.status()
    if not plan:
        safe=safe_completion_date(now_china())
        completed=[d for d in source_calendar if d<=safe]
        if not completed:raise ValueError('来源未提供可验证的已收盘交易日')
        target=requested or completed[-1]
        if target not in completed:raise ValueError('请求日尚未到盘后安全时间或不在真实交易日历内')
        if old.get('ready') and target<old['data_end']:raise ValueError('不能用较早目标覆盖已发布业务日')
        if kind=='increment' and not old.get('ready'):raise ValueError('请先手动初始化行情')
        if kind=='increment' and old['data_end']<source_calendar[0]:raise ValueError('漏跑区间已超出来源当前交易日历覆盖，需手动重新初始化，不能伪造缺失区间日历')
        window=resolve_initial_window(params)['window'] if kind=='initialise' else 'inherited'
        historical_window=window in ('near-three-years','near-five-years','near-ten-years')
        wanted=window_start(target,window) if historical_window else (date.fromisoformat(target)-timedelta(days=365)).isoformat()
        begin=old['data_begin'] if kind=='increment' else wanted if historical_window else next((d for d in completed if d>=wanted),completed[0])
        if kind=='initialise' and window=='near-one-year' and ((date.fromisoformat(target)-date.fromisoformat(begin)).days<330 or sum(begin<=d<=target for d in completed)<200):raise ValueError('来源交易日历不足近一年，不能冒充完成首次一年初始化')
        plan={'target':target,'begin':begin,'wanted':wanted,'window':window,'kind':kind,'previous_generation':old.get('snapshot_id') if kind=='increment' else None,'previous_end':old.get('data_end') if kind=='increment' else None,'provider':'fuyao','master_request_params':dict(MASTER_REQUEST_PARAMS)}
        if window=='near-five-years' or (kind=='increment' and current.reference().get('coverage',{}).get('index_request_plan')==INDEX_REQUEST_PLAN):
            plan['index_request_plan']=INDEX_REQUEST_PLAN
            plan['index_request_begin']=wanted if kind=='initialise' else max(begin,source_calendar[0])
        plan['completion_cutoff']='18:00 Asia/Shanghai'
        collector.state['plan']=plan;atomic_json(collector.state_path,collector.state)
    target,begin=plan['target'],plan['begin']
    if old.get('ready') and old['data_end']>target:raise ValueError('恢复任务目标早于当前发布水位，请手动新建增量')
    prior=MarketData(data_dir(),plan['previous_generation']) if plan['previous_generation'] else None
    # A checkpoint predating window metadata is a frozen one-year plan. Never
    # reinterpret its accepted request signatures using the new default.
    historical_initial=kind=='initialise' and plan.get('window') in ('near-three-years','near-five-years','near-ten-years')
    cal=sorted(set(d for d in source_calendar+(prior.calendar() if prior else []) if begin<=d<=target))
    master_params=frozen_master_request_params(plan)
    master_payload=collector.request('master.json','/api/meta/tickers/list',master_params,5)
    master=response_items(master_payload)
    if not master or len(master)>=10000:raise ValueError('证券代码表为空或达到分页上限，不能当完整名单')
    symbols=[row.get('thscode') for row in master]
    if any(not isinstance(symbol,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',symbol) for symbol in symbols) or len(set(symbols))!=len(symbols):raise ValueError('证券目录有无效或重复代码，拒绝发布')
    from app.services.security_identity import recover_listing_dates
    prior_reference=(prior or current).reference() if old.get('ready') else {}
    prior_master=prior_reference.get('master',{})
    master,inherited_dates=recover_listing_dates(master,prior_master,plan.get('previous_generation') or old.get('snapshot_id'))
    collector.canonical_json('fuyao_stock_master.json',{**master_payload,'data':{**master_payload['data'],'item':master},'identity_lineage':{'inherited_listing_dates':inherited_dates,'raw_evidence':'accepted/master.json','master_request_params':master_params}})
    prior_symbols={symbol for symbol,row in prior_master.items() if row.get('kind')=='stock'}
    identity_changed=set(symbols)!=prior_symbols or any(any(row.get(key)!=prior_master.get(row['thscode'],{}).get(key) for key in ('name','list_date','end_date','exchange','asset_type')) for row in master)
    downloaded=collector.download('daily-k-10d' if prior else 'daily-k',12)
    if prior:
        con=duckdb.connect(':memory:')
        try:incoming_dates={date_ms(r[0]) for r in con.execute('SELECT DISTINCT date_ms FROM read_parquet(?)',[str(downloaded)]).fetchall()}
        finally:con.close()
        needed={d for d in cal if d>plan['previous_end']}
        if not needed.issubset(incoming_dates):downloaded=collector.download('daily-k',20)
    index_payloads={}
    def collect_indices(index_window_begin,pct):
        for i,symbol in enumerate(INDEX_NAMES):
            bounded=plan.get('index_request_plan')==INDEX_REQUEST_PLAN
            effective_begin=index_history_begin(symbol,plan['index_request_begin'] if bounded else index_window_begin)
            index_begin=(date.fromisoformat(effective_begin)-timedelta(days=140)).isoformat() if symbol=='000001.SH' and not bounded else effective_begin
            payload=collector.request('index-'+symbol+'.json','/api/a-share-index/prices/historical',{'thscode':symbol,'interval':'1d','start':midnight_ms(index_begin),'end':midnight_ms(target)+86400000-1},pct+i)
            observed=index_dates(payload,symbol,effective_begin,target,index_begin)
            if bounded and prior:
                payload=merge_retained_index(payload,symbol,prior.reference().get('indices',{}).get(symbol),begin,target,cal,effective_begin)
            elif bounded and observed != [day for day in cal if effective_begin<=day<=target] and not historical_initial:
                raise ValueError(symbol+'指数来源与目标交易日期不一致')
            collector.canonical_json('fuyao_index_year_'+symbol.replace('.','_')+'.json',payload)
            index_payloads[symbol]=payload
    calendar_provenance=None
    if historical_initial:
        # Use frozen wanted rather than the later first market day for stable
        # request signatures when a paused task resumes after date validation.
        wanted=plan['wanted']
        collect_indices(wanted,44)
        years=resolve_initial_window({'window':plan['window']})['years']
        collector.tick(f'交叉核验近{years}年交易日期：全市场包、长历史指数、科创可用后缀、近期官方日历',49)
        cal,calendar_provenance=validate_history_calendar(downloaded,wanted,target,index_payloads,source_calendar,years=years,index_prewarm_days=0 if plan.get('index_request_plan')==INDEX_REQUEST_PLAN else 140)
        begin=cal[0]
        if plan.get('calendar_verified') and (plan['begin']!=begin or plan.get('calendar_provenance')!=calendar_provenance):raise ValueError('已确认历史日历或来源依据变化，拒绝改变恢复任务窗口')
        plan.update(begin=begin,calendar_verified=True,calendar_provenance=calendar_provenance)
        collector.state['plan']=plan;atomic_json(collector.state_path,collector.state)
    elif prior:
        prior_provenance=prior.reference().get('coverage',{}).get('calendar_provenance')
        if prior_provenance:
            calendar_provenance={**prior_provenance,'recent_extension':{'previous_generation':plan['previous_generation'],'previous_end':plan['previous_end'],'end':target,'official_calendar_start':source_calendar[0],'official_calendar_end':source_calendar[-1],'appended_dates':[d for d in cal if d>plan['previous_end']]},'retained_calendar_count':len(cal),'retained_calendar_sha256':digest(cal)}
    lineage={'source':'fuyao','merged_verified_calendar':True,'previous_generation':plan['previous_generation']}
    if calendar_provenance:lineage.update(calendar_provenance=calendar_provenance,official_history=calendar_provenance.get('official_history',False))
    collector.canonical_json('fuyao_calendar_year.json',{'code':0,'data':{'item':[{'date_ms':midnight_ms(d),'date':d.replace('-','')} for d in cal]},'lineage':lineage})
    previous=Path(prior.status()['path'])/'raw-daily.parquet' if prior else None
    collector.tick('校验、合并历史行情与重叠修订',50)
    coverage=merge_daily(downloaded,previous,collector.root/'year.parquet',begin,target,cal,master)
    coverage['master_request_params']=master_params
    coverage['inherited_listing_dates']=len(inherited_dates)
    if calendar_provenance:coverage['calendar_provenance']=calendar_provenance
    if plan.get('index_request_plan')==INDEX_REQUEST_PLAN:coverage['index_request_plan']=INDEX_REQUEST_PLAN
    coverage['initial_window']=prior.reference().get('coverage',{}).get('initial_window','inherited') if prior else plan.get('window','near-one-year')
    if kind=='increment':
        new_dates=[value for value in cal if plan['previous_end']<value<=target]
        coverage['increment_new_trade_dates']=new_dates
        coverage['event_supplement_target_dates']=[target]
        coverage['intermediate_event_gap_dates']=new_dates[:-1]
        coverage['event_supplement_scope']='每日增量自动补目标日封板与资金；若漏跑多个交易日，中间事件日仍未知，可手动补缺；不重复首次全年统计回补'
        if new_dates[:-1]:collector.tick(f'行情已补{len(new_dates)}个交易日；中间{len(new_dates)-1}日事件仍未知，随后仅自动补目标日',51)
    if prior and target==plan['previous_end'] and coverage['revised_overlap_rows']==0 and coverage['rows']==prior.status()['rows'] and not identity_changed:
        finish(job_id,'no_change','目标交易日行情已留存，重叠窗口无修订；本次未刷新题材成员和研究资料')
        return {'status':'no_change','quality':prior.status().get('quality','partial'),'message':'已有当日行情且重叠无修订；题材/研究资料需单独手动刷新','coverage':coverage,'data_status':prior.status()}
    actions=collector.download('adjustment-factors',52)
    shutil.copyfile(actions,collector.root/'raw/fuyao_bulk_adjustments.parquet')
    if not index_payloads:collect_indices(begin,55)
    for name,tag in [('fuyao_concept_catalog.json','cn_concept'),('fuyao_industry_catalog.json','industry')]:
        payload=collector.request(name,'/api/a-share-index/catalog/ths-index-list',{'tag':tag},60)
        if not response_items(payload):raise ValueError('题材/行业目录为空')
        collector.canonical_json(name,payload)
    topic_specs=[]
    topic_base=prior or current
    prior_topics=topic_base.topics_data()
    if prior_topics:
        for topic in prior_topics:
            code=topic['topic_code'];file='members-'+code+'.json'
            collector.canonical_json(file,{'code':0,'data':{'item':[{'thscode':symbol} for symbol in topic['instrument_ids']]},'lineage':{'copied_from_snapshot':topic_base.status().get('snapshot_id'),'observed_date':topic.get('observed_date')}})
            topic_specs.append({'file':file,'topic_code':code,'name':topic['name'],'kind':topic.get('kind','industry' if code.startswith(('881','884')) else 'concept'),'observed_date':topic.get('observed_date'),'complete':topic.get('members_complete',False)})
    else:
        for suffix,(code,name) in MEMBERS.items():
            file='fuyao_members_'+suffix+'.json'
            payload=collector.request(file,'/api/a-share-index/constituents/ths-stock-list',{'thscode':code},65)
            if not response_items(payload):raise ValueError(name+'题材成员为空')
            collector.canonical_json(file,payload);topic_specs.append({'file':file,'topic_code':code,'name':name,'kind':'industry' if code.startswith(('881','884')) else 'concept','observed_date':collector.state['files'][file]['observed_at'][:10],'complete':True})
    for name,endpoint in [('fuyao_limitup_latest.json','limit-up-pool'),('fuyao_limitdown_latest.json','limit-down-pool'),('fuyao_breakpool_latest.json','limit-break-pool')]:
        paged(collector,name,'/api/a-share/special-data/'+endpoint,{'date_ms':midnight_ms(target)},68)
    ladder=collector.request('ladder.json','/api/a-share/special-data/limit-up-ladder',pct=72)
    if target not in [r.get('date') for r in response_items(ladder)]:raise ValueError('连板梯队不含目标交易日，不能把当前梯队冒充历史')
    collector.canonical_json('fuyao_ladder_30d.json',ladder)
    abnormal=paged(collector,'fuyao_abnormal_current.json','/api/a-share/special-data/abnormal-monitor-list',{'state':'current'},73,size=30)
    abnormal_observed=collector.state['files']['fuyao_abnormal_current.json.page-1.json']['observed_at']
    sector_codes=list(dict.fromkeys(t['topic_code'] for t in topic_specs))
    sector_items=[]
    for offset in range(0,len(sector_codes),50):
        sectors=collector.request('sectors-'+str(offset)+'.json','/api/a-share-index/prices/snapshot',{'thscodes':','.join(sector_codes[offset:offset+50])},74)
        sector_items.extend(response_items(sectors))
    actual_sector_codes={r.get('thscode') for r in sector_items}
    coverage.update(sector_expected=len(sector_codes),sector_observed=len(actual_sector_codes),sector_missing_codes=sorted(set(sector_codes)-actual_sector_codes))
    collector.canonical_json('fuyao_sectors_snapshot_sample.json',{'code':0,'data':{'item':sector_items,'total':len(sector_items)},'coverage':{'requested':len(sector_codes),'observed':len(actual_sector_codes)}})
    # Pool envelopes only expose fetch time. Verify symbol prices against the requested day.
    con=duckdb.connect(':memory:')
    try:
        closes=dict(con.execute('SELECT thscode,close_price FROM read_parquet(?) WHERE date_ms=?',[str(collector.root/'year.parquet'),midnight_ms(target)]).fetchall())
        quarantine_counts={}
        for name in ('fuyao_limitup_latest.json','fuyao_limitdown_latest.json','fuyao_breakpool_latest.json'):
            body=json.loads((collector.root/'raw'/name).read_text());pool=response_items(body);verified=[];quarantine=[]
            for stock in pool:
                symbol=stock.get('thscode');price=stock.get('last_price')
                if not isinstance(symbol,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',symbol.upper()):quarantine.append({'record':stock,'reason':'缺少可核对的历史证券代码；禁止按当前名称猜测'});continue
                symbol=symbol.upper()
                if symbol not in closes:quarantine.append({'record':stock,'reason':'请求交易日缺少该证券报价，不能验证事件价格'});continue
                if isinstance(price,bool) or not isinstance(price,(int,float)) or not isfinite(price):quarantine.append({'record':stock,'reason':'事件价格无有效数值，不能核对请求日'});continue
                if abs(price-closes[symbol])>.011:raise ValueError('涨跌停/炸板池价格与请求交易日收盘不符，拒绝发布')
                verified.append({**stock,'thscode':symbol})
            if quarantine:
                body['data']['item']=verified;body['data']['quarantine']=quarantine
                body['data'].setdefault('pagination',{})['complete']=False
                collector.canonical_json(name,body)
            quarantine_counts[name]=len(quarantine)
        coverage['pool_quarantine_counts']=quarantine_counts
    finally:con.close()
    if 'collected_at' not in collector.state:
        collector.state['collected_at']=now_china().isoformat();atomic_json(collector.state_path,collector.state)
    # Preserve retained event days from the actual currently-published generation.
    # Unknown modules are omitted, never converted into proven empty pools.
    previous_reference=current.reference() if old.get('ready') else {}
    event_history=dict(previous_reference.get('pool_history',{}))
    old_pool_day=previous_reference.get('pool_trade_date')
    if old_pool_day:
        previous_pools={key:previous_reference[key] for key in ('limitup','limitdown','breakpool') if key in previous_reference}
        previous_pools.update(source='fuyao',fetched_at=previous_reference.get('pool_fetched_at'),coverage=previous_reference.get('pool_coverage',{}),quarantine=previous_reference.get('pool_quarantine',{}))
        event_history.setdefault(old_pool_day,previous_pools)
    event_history[target]={key:response_items(json.loads((collector.root/'raw'/file).read_text())) for key,file in [('limitup','fuyao_limitup_latest.json'),('limitdown','fuyao_limitdown_latest.json'),('breakpool','fuyao_breakpool_latest.json')]}
    event_history[target].update(source='fuyao',fetched_at=collector.state['collected_at'])
    event_history[target]['coverage']={key:pool_coverage(json.loads((collector.root/'raw'/file).read_text())) for key,file in [('limitup','fuyao_limitup_latest.json'),('limitdown','fuyao_limitdown_latest.json'),('breakpool','fuyao_breakpool_latest.json')]}
    event_history[target]['quarantine']={key:json.loads((collector.root/'raw'/file).read_text()).get('data',{}).get('quarantine',[]) for key,file in [('limitup','fuyao_limitup_latest.json'),('limitdown','fuyao_limitdown_latest.json'),('breakpool','fuyao_breakpool_latest.json')]}
    atomic_json(collector.root/'raw/fuyao_pool_history.json',event_history)
    missing_notice=(f"{coverage['identity_unknown_count']}只证券上市日期未知，总预期记录缺口待核验；已知身份中至少{coverage['confirmed_missing_lower_bound']}条缺口未分类。" if coverage['identity_unknown_count'] else f"{coverage['unexplained_missing']}条预期记录缺口未分类（可能包括停牌）；不填0、不判定全市场历史完整。")
    warnings=[missing_notice,'退市/历史ST身份、完整历史题材和换手率覆盖不足。','原始未复权价格；公司行动附近技术指标重启预热，未经验证不伪造完整复权。',f"当前留存{len(topic_specs)}组题材成员，各组观察日独立；板块快照{coverage['sector_observed']}/{coverage['sector_expected']}组，不冒称未采集板块的全市场排名。",'异动监控是本次抓取的current标签，非历史正式监管披露；监管原文与盘中资金时序未随本任务取得。']
    if calendar_provenance and calendar_provenance.get('basis')=='source_derived_cross_checked':warnings.append('较早交易日期由全市场行情包与长历史指数交叉推导，科创50仅核验可用后缀，近期与官方日历一致；不冒称交易所官方完整历史日历。')
    if coverage['revised_overlap_rows']:warnings.append(f"重叠窗口{coverage['revised_overlap_rows']}条源修订已生成新版本，旧快照保留。")
    if sum(coverage.get('pool_quarantine_counts',{}).values()):warnings.append('事件池存在身份或价格缺失记录已隔离；未知不等于未涨停，详见pool_quarantine。')
    sector_observed=[collector.state['files'][f'sectors-{offset}.json']['observed_at'] for offset in range(0,len(sector_codes),50)]
    bundle={'source':'fuyao','price_basis':'none','daily_file':'year.parquet','collected_at':collector.state['collected_at'],'pool_trade_date':target,'pool_history_file':'fuyao_pool_history.json','abnormal_observed_date':str(abnormal_observed)[:10],'abnormal_fetched_at':abnormal_observed,'sectors_snapshot_observed_at':max(sector_observed) if sector_observed else None,'sectors_snapshot_date_semantics':'current_unverified_trade_date','quality_warnings':warnings,'unexplained_missing':coverage['unexplained_missing'],'coverage':coverage,'topic_files':topic_specs,'topic_coverage':f'{len(topic_specs)}组已请求或复用的真实当前成员，各组采集日独立；不是全题材历史成员库'}
    atomic_json(collector.root/'bundle.json',bundle)
    collector.tick('发布前核验数据包',76)
    validate_frozen_target(target,now_china())
    status=MarketData(data_dir()).import_audit(collector.root,lambda stage,pct:collector.tick(stage,76+int(pct*.05)))
    collector.tick('登记并原子切换全部模块',81)
    validate_frozen_target(target,now_china())
    registration=register_market(status,job_id,lambda stage,pct:collector.tick(stage,pct))
    return {'status':'partial','quality':'partial','message':'真实同步已发布；缺口与功能覆盖见数据质量','data_status':status,'registration':registration,'coverage':coverage}
