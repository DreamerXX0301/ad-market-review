"""Manual, resumable market supplements outside immutable base generations.

Each accepted day is content checked and reusable; only the final index activates
this run. API reads never fetch remote data. Failed dates retain explicit errors
and may be retried manually without redownloading complete accepted dates.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import duckdb

from app.core import atomic_json, connect, published_dir, digest, dumps, get_market, hash_file, now_ms
from app.adapters.providers import ProviderError
from app.adapters.public_market import PublicMarketClient, normalize_limit_pool
from app.services.task_service import TaskCancelled, TaskPaused
from app.services.limit_history import fetch_fuyao_pool, annual_coverage, pool_spec, _midnight, NORMALIZATION_VERSION

CHINA=ZoneInfo('Asia/Shanghai')
VERSION='market-supplement-v1'


def _load(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def _root(): return published_dir()/'market-supplement'


def load_supplement(snapshot_id=None):
    head=_load(_root()/'head.json',{})
    if snapshot_id and head.get('snapshot_id')!=snapshot_id:return {}
    if not head:return {}
    relative=head.get('artifact'); path=(_root()/str(relative)).resolve()
    if not path.is_relative_to(_root().resolve()) or not path.is_file() or hash_file(path)!=head.get('sha256'):return {'quality':'unknown','failure_reason':'补充数据索引缺失或校验失败'}
    result=_load(path,{})
    return result if result.get('version')==VERSION else {'quality':'unknown','failure_reason':'补充数据版本不匹配'}


class SupplementCache:
    def __init__(self, root=None):
        self.root=Path(root or _root()); self.root.mkdir(parents=True,exist_ok=True)

    def reuse(self, source, capability, day, signature, *, allow_partial=False):
        pointer=_load(self.root/'accepted'/source/capability/(day+'.json'),{})
        if pointer.get('signature')!=signature or pointer.get('version')!=VERSION:return None
        path=(self.root/str(pointer.get('artifact',''))).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file() or hash_file(path)!=pointer.get('sha256'):raise ValueError('补充批次缓存损坏，拒绝复用；请移出损坏文件后手动重试')
        result=_load(path,{})
        raw=(self.root/str(result.get('raw_artifact',''))).resolve()
        if not raw.is_relative_to(self.root.resolve()) or not raw.is_file() or hash_file(raw)!=result.get('raw_sha256'):raise ValueError('补充原始证据缓存缺失或损坏，拒绝复用')
        return result if result.get('complete') is True or allow_partial else None

    def accept(self, source, capability, day, signature, result, raw):
        raw_hash=digest(raw); raw_path=self.root/'raw'/(raw_hash+'.json')
        if raw_path.exists():
            try:valid=digest(_load(raw_path))==raw_hash
            except (ValueError,OSError):valid=False
            if not valid:raise ValueError('补充原始证据对象已损坏，拒绝将新响应关联到损坏缓存')
        else:atomic_json(raw_path,raw)
        result={**result,'fetched_at':result.get('fetched_at') or datetime.now(CHINA).isoformat(),'raw_sha256':hash_file(raw_path),'raw_artifact':str(raw_path.relative_to(self.root)),'validation_version':VERSION}
        content=digest(result); path=self.root/'artifacts'/(content+'.json')
        atomic_json(path,result)
        atomic_json(self.root/'accepted'/source/capability/(day+'.json'),{'version':VERSION,'signature':signature,'normalization_version':result.get('normalization_version',VERSION),'artifact':str(path.relative_to(self.root)),'sha256':hash_file(path)})
        return result

    def publish(self, document, guard=None):
        content=digest(document); path=self.root/'indexes'/(content+'.json')
        atomic_json(path,document)
        if guard:guard()
        atomic_json(self.root/'head.json',{'snapshot_id':document['snapshot_id'],'version':VERSION,'artifact':str(path.relative_to(self.root)),'sha256':hash_file(path),'published_at_ms':now_ms()})


def fetch_history_for_market(public, market, target, cache, *, progress=None, guard=None):
    from app.adapters.public_fund_history import fetch_sector_fund_history
    calendar=market.calendar();expected=[d for d in calendar if d<=target][-20:]
    def identity(code):return digest({'source':'eastmoney-public','capability':'industry-fund-history','code':code,'target':target,'expected_dates':expected,'contract':'public-fund-history-v1'})
    def lookup(code,day):
        if guard:guard()
        result=cache.reuse('eastmoney','fund-board',day+'-'+code,identity(code))
        return _load(cache.root/result['raw_artifact']) if result else None
    def save(code,day,normalized,raw):
        if guard:guard()
        cache.accept('eastmoney','fund-board',day+'-'+code,identity(code),{**normalized,'date':day,'topic_code':code,'complete':normalized['history_complete']},raw)
    summaries=[];raw_rounds=[];used=0;result=None
    for round_no in (1,2):
        remaining=200-used
        if remaining<2:break
        if guard:guard()
        def on_progress(done,total,code):
            if guard:guard()
            if progress:progress(done,total,'第'+str(round_no)+'轮'+('补缺' if round_no==2 else '采集')+' · '+code)
        try:
            current=fetch_sector_fund_history(public,target,scope='industry',limit=None,max_requests=remaining,calendar=calendar,progress=on_progress,cache_lookup=lookup,cache_save=save)
        except ProviderError as error:
            # Directory I/O/shape failures happen before per-board calls. Count
            # that logical request too, and retain the failed round explicitly.
            current={'items':[],'history':[],'date':target,'source':'eastmoney-public','complete':False,'coverage':{'directory_count':None,'selected_count':0,'accepted_count':0,'failed_count':1,'unrequested_count':0,'cached_count':0,'logical_request_count':1,'complete':False,'history_20d_complete':False},'raw_pages':[],'failures':[{'stage':'directory','reason':str(error)}],'intraday_sequence_available':False}
        coverage=current.get('coverage',{});spent=coverage.get('logical_request_count')
        if isinstance(spent,bool) or not isinstance(spent,int) or not 1<=spent<=remaining:raise ProviderError('资金流适配器请求计数超出本任务剩余预算')
        used+=spent
        summary={'round':round_no,'budget_before':remaining,'logical_request_count':spent,'budget_remaining':200-used,'cached_count':coverage.get('cached_count',0),'accepted_count':coverage.get('accepted_count',0),'failed_count':coverage.get('failed_count',0),'unrequested_count':coverage.get('unrequested_count',0),'complete':bool(coverage.get('complete')),'history_20d_complete':bool(coverage.get('history_20d_complete')),'failures':list(current.get('failures',[]))}
        summaries.append(summary)
        raw_rounds.append({'kind':'fund_history_round','round':round_no,'coverage':dict(coverage),'failures':list(current.get('failures',[])),'pages':current.pop('raw_pages',[])})
        # A second round may fail to read its directory after the first already
        # accepted dated records. Keep that data and its explicit missing scope.
        if result is None or current.get('items') or not result.get('items'):result=current
        if coverage.get('complete') and coverage.get('history_20d_complete'):break
    if result is None:raise ProviderError('资金流请求预算不足，未启动额外重试')
    result['rounds']=summaries;result['raw_pages']=raw_rounds
    result['coverage']={**result.get('coverage',{}),'logical_request_count':used,'max_logical_requests':200,'max_transport_attempts':600,'round_count':len(summaries),'retry_policy':'同任务最多两轮；第二轮仅补失败且复用已验SHA缓存；两轮合计最多200个逻辑请求','rounds':summaries}
    return result


def refresh_for_market(market, target, *, year_history=False, recent_days=1, progress=None, guard=None, job_id=None, sector_history=False):
    status=market.status(); calendar=market.calendar()
    if target not in calendar:raise ValueError('补充目标日期必须是已留存交易日')
    if target!=status['data_end']:raise ValueError('补充任务以当前冻结行情日为锚；历史盘面从该任务留存的逐日数据读取')
    root=_root(); cache=SupplementCache(root); old=load_supplement()
    doc={'version':VERSION,'snapshot_id':status['snapshot_id'],'target_date':target,'job_id':job_id,'created_at_ms':now_ms(),'public_pools':{},'annual_pools':{},'annual_down_pools':{},'fund_flows':{d:r for d,r in old.get('fund_flows',{}).items() if d in calendar and d<=target},'sector_index_history':{d:r for d,r in old.get('sector_index_history',{}).items() if d in calendar and d<=target},'failures':[]}
    original_progress=progress
    if sector_history and progress:
        progress=lambda stage,pct,message=None,details=None: original_progress(stage,int(pct*.55),message,details)
    master=[r for r in market.instruments(limit=10000) if r.get('kind')=='stock']
    identity_hash=digest({r['code']:r['instrument_id'] for r in master})
    dates=[d for d in calendar if d[:4]==target[:4] and d<=target] if year_history else [target]
    recent=calendar[max(0,calendar.index(target)-max(1,min(int(recent_days),30))+1):calendar.index(target)+1]
    pool_batches=len(dates)*2
    planned=pool_batches+len(recent)+1; done=0
    def tick(label,message=None):
        if guard:guard()
        if done<pool_batches:pct=int(done/max(1,pool_batches)*60)
        elif done<pool_batches+len(recent):pct=60+int((done-pool_batches)/max(1,len(recent))*15)
        else:pct=98 if done>=planned else 75
        if progress:progress(label,pct,message or label,{'actual_completed_batches':done,'actual_total_batches':planned})
    con=duckdb.connect(':memory:')
    try:
        con.execute('CREATE TABLE quotes AS SELECT instrument_id,date,close FROM read_parquet(?)',[status['path']+'/daily.parquet'])
        def prices(day):return dict(con.execute('SELECT instrument_id,close FROM quotes WHERE date=?',[day]).fetchall())
        def pool_signature(day,source,closes,pool_kind=None):
            value={'quotes':closes,'master':identity_hash,'day':day,'source':source}
            # Existing up signatures remain compatible. Down batches get an
            # explicit kind as well as their separate accepted namespace.
            if pool_kind=='limitdown':value['pool_kind']=pool_kind
            return digest(value)
        def signature_for(day,source,pool_kind=None):return pool_signature(day,source,prices(day),pool_kind)
        def validate_cached(record,day,pool_kind=None):
            if not record:return
            if record.get('date')!=day:raise ValueError('补充批次缓存日期与请求日期不符，拒绝复用')
            if pool_kind and record.get('pool_kind','limitup')!=pool_kind:raise ValueError('补充批次缓存涨跌停类型与请求不符，拒绝复用')

        # A new base generation must not discard validated history, nor blindly
        # inherit stale prices. Verify accepted chunks against its actual quotes.
        for key,source,capability,signature_source in [('annual_pools','fuyao','limitup','fuyao'),('annual_down_pools','fuyao','limitdown','fuyao'),('public_pools','eastmoney','limitup','eastmoney-public')]:
            for day in old.get(key,{}):
                if day not in calendar or day>target or (key in ('annual_pools','annual_down_pools') and day[:4]!=target[:4]):continue
                if guard:guard()
                kind=capability if key in ('annual_pools','annual_down_pools') else None
                signature=signature_for(day,signature_source,kind)
                record=cache.reuse(source,capability,day,signature,allow_partial=True)
                validate_cached(record,day,kind)
                if record is not None and kind and record.get('normalization_version')!=NORMALIZATION_VERSION:
                    try:
                        cached=record;raw=_load(root/cached['raw_artifact'])
                        # Revalidate inherited history locally, including dates
                        # outside this manual incremental run's remote scope.
                        record,_=fetch_fuyao_pool(None,day,prices(day),master,payload_pages=raw,pool_kind=kind)
                        record.update(fetched_at=cached.get('fetched_at'),validated_at=datetime.now(CHINA).isoformat())
                        record=cache.accept(source,capability,day,signature,record,raw)
                    except (ValueError,ProviderError) as error:
                        doc['failures'].append({'capability':pool_spec(kind)['failure_capability'],'pool_kind':kind,'date':day,'reason':'历史补充原始证据离线重验失败：'+str(error)})
                        continue
                if record is not None:doc[key][day]=record
                else:doc['failures'].append({'capability':pool_spec(kind)['failure_capability'] if kind else 'seal_details','date':day,'reason':'基础行情或证券身份签名变化，该历史补充未继承；请手动运行年度补充以重验'})
        if dates:
            from app.services.source_service import client
            adapter=client('fuyao')
            for day in dates:
                closes=prices(day)
                for pool_kind in ('limitup','limitdown'):
                    spec=pool_spec(pool_kind)
                    tick('回补年内'+spec['label']+'池',day+' · 校验分页、证券身份和收盘价')
                    signature=pool_signature(day,'fuyao',closes,pool_kind);cached=None
                    try:
                        record=cache.reuse('fuyao',pool_kind,day,signature,allow_partial=True)
                        validate_cached(record,day,pool_kind)
                        if record and record.get('normalization_version')!=NORMALIZATION_VERSION:
                            cached=record;raw=_load(root/cached['raw_artifact'])
                            record,_=fetch_fuyao_pool(adapter,day,closes,master,payload_pages=raw,pool_kind=pool_kind)
                            record.update(fetched_at=cached.get('fetched_at'),validated_at=datetime.now(CHINA).isoformat())
                            record=cache.accept('fuyao',pool_kind,day,signature,record,raw)
                        elif record is None or not record.get('complete'):
                            record,raw=fetch_fuyao_pool(adapter,day,closes,master,pool_kind=pool_kind)
                            record=cache.accept('fuyao',pool_kind,day,signature,record,raw)
                        doc[spec['document_key']][day]=record
                    except (ValueError,ProviderError) as error:
                        failure={'capability':spec['failure_capability'],'pool_kind':pool_kind,'date':day,'reason':str(error)}
                        captured=getattr(error,'raw_pages',None)
                        if captured:
                            evidence={'date':day,'pool_kind':pool_kind,'items':[],'complete':False,'state':'unknown','reason':str(error)}
                            if cached:evidence.update(fetched_at=cached.get('fetched_at'),validated_at=datetime.now(CHINA).isoformat())
                            rejected=cache.accept('fuyao',pool_kind+'-failed',day,signature,evidence,captured)
                            failure.update(raw_artifact=rejected['raw_artifact'],raw_sha256=rejected['raw_sha256'],fetched_at=rejected['fetched_at'])
                        doc['failures'].append(failure)
                    done+=1
        public=PublicMarketClient()
        for day in recent:
            tick('同步封板明细',day+' · 公开数据补充与逐价验证')
            closes=prices(day); signature=digest({'quotes':closes,'master':identity_hash,'day':day,'source':'eastmoney-public'})
            try:
                record=cache.reuse('eastmoney','limitup',day,signature)
                if record is None:
                    raw=public.limit_pool(day); record=normalize_limit_pool(raw,day,closes,master)
                    record=cache.accept('eastmoney','limitup',day,signature,record,raw)
                doc['public_pools'][day]=record
            except (ValueError,ProviderError) as error:
                doc['failures'].append({'capability':'seal_details','date':day,'reason':str(error)})
            done+=1
        tick('核验行业日级资金',target+' · 按实际日期读取行业历史序列')
        fund_total=0
        def fund_progress(processed,total,code):
            nonlocal planned,fund_total
            fund_total=total;planned=pool_batches+len(recent)+total+1
            if guard:guard()
            if progress:progress('核验行业日级资金',75+int(processed/max(1,total)*23),code+' · '+str(processed)+'/'+str(total),{'actual_completed_batches':done+processed,'actual_total_batches':planned,'fund_processed':processed,'fund_catalog_count':total})
        try:
            record=fetch_history_for_market(public,market,target,cache,progress=fund_progress,guard=guard)
            raw=record.pop('raw_pages')
            record=cache.accept('eastmoney','fund-flow',target,digest({'day':target,'scope':'industry-history'}),record,raw)
            if record.get('items'):
                doc['fund_flows'][target]=record
            for failure in record.get('failures',[]):doc['failures'].append({'capability':'fund_flow','date':target,**failure})
            if not record.get('items'):doc['failures'].append({'capability':'fund_flow','date':target,'reason':'没有取得目标日可核验行业资金流，保留未知'})
            fund_total=record.get('coverage',{}).get('selected_count',fund_total)
        except (ValueError,ProviderError) as error:
            doc['failures'].append({'capability':'fund_flow','date':target,'reason':str(error)})
        planned=pool_batches+len(recent)+fund_total+1;done=planned
    finally:con.close()
    if sector_history:
        from app.services.sector_history import collect_sector_history
        from app.services.source_service import client
        base_batches=done
        def sector_progress(completed,total,code):
            if guard:guard()
            if original_progress:
                original_progress('补充行业与概念日行情',55+int(completed/max(1,total)*43),
                                  target+' · '+str(code)+' · '+str(completed)+'/'+str(total),
                                  {'actual_completed_batches':base_batches+completed,
                                   'actual_total_batches':base_batches+total,
                                   'sector_processed':completed,'sector_catalog_count':total})
        try:
            record=collect_sector_history(market,target,cache,client('fuyao'),progress=sector_progress,guard=guard)
            if guard:guard()
            if record.get('items'):
                # Keep the head small: each dated board-price table is an
                # immutable object, loaded only for the requested market day.
                lineage={'kind':'sector-index-raw-manifest','date':target,
                         'entries':[{key:row.get(key) for key in ('topic_code','raw_artifact','raw_sha256','request_signature')}
                                    for row in record['items']]}
                signature=digest({'day':target,'scope':'sector-index-history','normalization_version':record.get('normalization_version')})
                accepted=cache.accept('fuyao','sector-day',target,signature,record,lineage)
                pointer=_load(root/'accepted/fuyao/sector-day'/(target+'.json'))
                doc['sector_index_history'][target]={
                    'date':target,'source':'fuyao','normalization_version':accepted['normalization_version'],
                    'artifact':pointer['artifact'],'sha256':pointer['sha256'],
                    'complete':accepted.get('complete') is True,'coverage':accepted.get('coverage',{})}
            for failure in record.get('failures',[]):
                doc['failures'].append({'capability':'sector_index_history','date':target,**failure})
            if not record.get('items'):
                doc['failures'].append({'capability':'sector_index_history','date':target,'reason':'未取得该日可核验板块指数行情，保留原已发布数据及缺口'})
            planned=base_batches+record.get('coverage',{}).get('selected_count',0);done=planned
        except (ValueError,ProviderError) as error:
            doc['failures'].append({'capability':'sector_index_history','date':target,'reason':str(error)})
        progress=original_progress
    doc['annual_coverage']=annual_coverage(doc,calendar,target)
    doc['annual_down_coverage']=annual_coverage(doc,calendar,target,pool_kind='limitdown')
    doc['quality']='partial' if doc['failures'] or not doc['annual_coverage']['complete'] or not doc['annual_down_coverage']['complete'] else 'complete'
    doc['message']='来源池已核验；年内涨停 '+str(doc['annual_coverage']['covered_days'])+'/'+str(doc['annual_coverage']['expected_days'])+'，跌停 '+str(doc['annual_down_coverage']['covered_days'])+'/'+str(doc['annual_down_coverage']['expected_days'])+' 个交易日完整；当年之外未回补，缺口保留并可重试'
    if sector_history:
        coverage=doc['sector_index_history'].get(target,{}).get('coverage',{})
        doc['message']+='；板块日行情 '+str(coverage.get('accepted_count',0))+'/'+str(coverage.get('catalog_count',0))+' 个，历史成分另行核验'
    tick('发布补充数据索引',doc['message']);cache.publish(doc,guard)
    return doc


def execute_supplement(job_id, progress=None):
    with connect() as connection:
        row=connection.execute('SELECT parameters_json,target_date FROM job WHERE job_id=?',(job_id,)).fetchone()
    if not row:raise ValueError('补充任务不存在')
    params=json.loads(row['parameters_json']);market=get_market();target=row['target_date'] or market.status().get('data_end')
    def guard():
        with connect() as connection:state=connection.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()
        if not state or state[0]=='cancelled':raise TaskCancelled()
        if state[0]=='paused':raise TaskPaused()
        if get_market().status().get('snapshot_id')!=market.status().get('snapshot_id'):raise ValueError('基础行情代际已变化，补充任务保留缓存并需手动重试')
    return refresh_for_market(market,target,year_history=bool(params.get('year_history',True)),recent_days=params.get('recent_days',20),progress=progress,guard=guard,job_id=job_id,sector_history=params.get('sector_history') is True)
