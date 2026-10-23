"""Explicit worker verification of retained files; reads never fetch or repair."""
from bisect import bisect_left, bisect_right
from datetime import date
import json
from pathlib import Path
import tempfile

import duckdb
from fastapi import HTTPException

from app.core import atomic_json, connect, digest, get_market, hash_file, now_ms, published_dir, work_dir
from app.services import task_service as tasks
from app.services import gap_diagnosis, listing_status
from app.analytics.market_storage import OBJECT_ROOT, parquet_input

VERSION='local-coverage-v3-gap-diagnosis'
MAX_GAP_DATES=20000
MAX_REPORT_BYTES=24*1024*1024


def valid_date(value):
    try:return isinstance(value,str) and date.fromisoformat(value).isoformat()==value
    except ValueError:return False


def stock_scope(reference,begin,end,*,quote_presence=None,confirmations=None,resolved=None):
    """Calendar opportunities, not a claim every opportunity should trade."""
    calendar=sorted({day for day in reference.get('calendar',[]) if valid_date(day) and begin<=day<=end})
    stocks={symbol:row for symbol,row in reference.get('master',{}).items() if row.get('kind')=='stock'}
    resolved=resolved or listing_status.resolve(reference,end,quote_presence,confirmations=confirmations or {})
    ranges=[];unknown=[]
    for symbol,row in stocks.items():
        classification=resolved['items'][symbol]
        if classification['listing_status']=='pending':continue
        listed=row.get('list_date');ended=row.get('end_date')
        if classification['listing_status']!='listed' or not valid_date(listed) or (ended is not None and not valid_date(ended)):
            unknown.append({'instrument_id':symbol,'name':row.get('name') or symbol,'reason':classification.get('reason') or ('来源未提供有效上市日期' if not valid_date(listed) else '来源退市日期无效')})
            continue
        start=max(begin,listed);finish=min(end,ended or end)
        expected=max(0,bisect_right(calendar,finish)-bisect_left(calendar,start))
        ranges.append((symbol,start,finish,expected))
    return calendar,stocks,ranges,unknown


def _root():return work_dir()/'local-coverage'


def _context(market):
    status=market.status()
    if not status.get('ready'):raise HTTPException(409,'请先初始化行情，再核验本地覆盖')
    reference=market.reference()
    with connect() as connection:
        connection.execute('BEGIN')
        heads=[dict(row) for row in connection.execute('SELECT module,scope_key,snapshot_id FROM published_head ORDER BY module,scope_key')]
        listing_revision=listing_status.read_revision(connection)
        listing_confirmations=listing_status.confirmations(connection)
    files=[];root=published_dir().resolve();generation=(root/status['snapshot_id']).resolve()
    for item in status.get('files',[]):
        relative=Path(item.get('path',''))
        if relative.parts and relative.parts[0]=='published':relative=Path(*relative.parts[1:])
        path=(root/relative).resolve()
        if not (path.is_relative_to(generation) or path.is_relative_to(root/OBJECT_ROOT)):raise ValueError('行情文件路径不属于当前不可变代际')
        stamp=path.stat() if path.is_file() else None
        files.append({'role':item.get('role') or path.name,'path':path,'relative':str(relative),
                      'sha256':item.get('sha256'),'bytes':item.get('bytes'),
                      'stat':[stamp.st_size,stamp.st_mtime_ns] if stamp else None})
    supplement=root/'market-supplement/head.json'
    supplement_head=json.loads(supplement.read_text()) if supplement.is_file() else {}
    signature=digest({'version':VERSION,'generation':status['snapshot_id'],'heads':heads,
                      'files':[{key:value for key,value in item.items() if key!='path'} for item in files],
                      'supplement':supplement_head,'listing_revision':listing_revision})
    return {'status':status,'reference':reference,'heads':heads,'files':files,'signature':signature,
            'listing_revision':listing_revision,'listing_confirmations':listing_confirmations}


def _read_report(context):
    path=_root()/'latest.json'
    if not path.is_file():return None,False
    try:
        pointer=json.loads(path.read_text());artifact=(_root()/pointer['artifact']).resolve()
        if not artifact.is_relative_to(_root().resolve()) or not artifact.is_file() or artifact.stat().st_size>MAX_REPORT_BYTES or hash_file(artifact)!=pointer['sha256']:return None,True
        report=json.loads(artifact.read_text())
        if report.get('version')!=VERSION or report.get('source_signature')!=context['signature']:return None,True
        return report,False
    except (OSError,ValueError,KeyError,TypeError):return None,True


def read_view(market=None,*,full=False):
    market=market or get_market()
    if not market.status().get('ready'):return {'available':False,'stale':False,'report':None}
    context=_context(market);report,stale=_read_report(context)
    if report is not None and full:
        # Read only the saved source receipt. Calling its read_view here would
        # recurse through this module's current report/context resolution.
        from .quote_gap_source_check import _read_evidence
        evidence=_read_evidence()
        if evidence is not None:report=gap_diagnosis.overlay_remote_verification(report,evidence)
    if report is not None and not full:
        report={**report,'daily':{key:value for key,value in report.get('daily',{}).items() if key!='gaps'}}
        if report['daily'].get('gap_diagnosis'):
            report['daily']['gap_diagnosis']={key:value for key,value in report['daily']['gap_diagnosis'].items() if key!='stocks'}
    return {'available':True,'plan_id':context['signature'],'snapshot_id':context['status']['snapshot_id'],
            'target_date':context['status']['data_end'],'stale':stale,'report':report,
            'check_scope':['校验当前代际文件SHA和字段结构','按已知上市区间与本地交易日历核验逐股报价','检查公司行动身份、日期与已提供字段；不声称来源事件全集'],
            'network':False}


def create_check(payload):
    if not isinstance(payload,dict):raise HTTPException(422,'本地核验请求须为JSON对象')
    if set(payload)-{'request_key','plan_id'}:raise HTTPException(422,'本地核验仅接受计划与请求标识')
    key=payload.get('request_key')
    if key is not None and (not isinstance(key,str) or not 1<=len(key)<=200 or not key.strip()):
        raise HTTPException(422,'请求标识须为1至200字符')
    plan_id=payload.get('plan_id')
    if plan_id is not None and (not isinstance(plan_id,str) or not 1<=len(plan_id)<=200 or not plan_id.strip()):
        raise HTTPException(422,'核验计划标识须为1至200字符')
    context=_context(get_market())
    if payload.get('plan_id') is not None and payload['plan_id']!=context['signature']:raise HTTPException(409,'行情或来源证据已更新，请刷新核验计划')
    return tasks.create_task('source_check',{'operation':'local_coverage_check','source_id':'local-files',
        'market_snapshot_id':context['status']['snapshot_id'],'source_signature':context['signature']},
        target_date=context['status']['data_end'],request_key=payload.get('request_key'))


def repair_parameters(plan_id):
    context=_context(get_market());report,_=_read_report(context)
    if not isinstance(plan_id,str) or plan_id!=context['signature'] or not report:
        raise HTTPException(409,'核验计划已过期，请刷新并重新核验当前数据')
    return {**report['repair_plan']['payload'],'market_snapshot_id':context['status']['snapshot_id'],
            'coverage_plan_id':context['signature']}


def _daily(con,path,reference,begin,end,*,raw_path=None,listing_confirmations=None,market_snapshot_id=None,source_signature=None):
    required={'instrument_id','date','open','high','low','close','volume','amount'}
    fields={row[0] for row in con.execute('DESCRIBE SELECT * FROM read_parquet(?)',[parquet_input(path)]).fetchall()}
    if not required<=fields:raise ValueError('分析行情缺少必要字段：'+','.join(sorted(required-fields)))
    calendar=sorted({day for day in reference.get('calendar',[]) if valid_date(day) and begin<=day<=end})
    con.execute('CREATE TABLE calendar(day VARCHAR)')
    if calendar:con.executemany('INSERT INTO calendar VALUES (?)',[(day,) for day in calendar])
    valid="instrument_id IS NOT NULL AND date IS NOT NULL AND regexp_full_match(instrument_id,'[0-9]{6}\\.(SH|SZ|BJ)') AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL AND volume IS NOT NULL AND amount IS NOT NULL AND isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close) AND isfinite(volume) AND isfinite(amount) AND least(open,high,low,close)>0 AND volume>=0 AND amount>=0 AND high>=greatest(open,low,close) AND low<=least(open,high,close)"
    con.execute('CREATE TEMP TABLE quote_keys AS SELECT instrument_id,date,count(*) copies,bool_and('+valid+') AS is_valid FROM read_parquet(?) GROUP BY instrument_id,date',[parquet_input(path)])
    total,duplicates,invalid=con.execute('SELECT coalesce(sum(copies),0),coalesce(sum(copies-1),0),count(*) FILTER(WHERE is_valid IS NOT TRUE) FROM quote_keys').fetchone()
    valid_key='q.is_valid AND q.copies=1 AND c.day IS NOT NULL'
    present={symbol:{'valid_quote_count':count,'first_quote_date':first,'last_quote_date':last,
                     'analysis_observed_rows':rows,'ambiguous_quote_keys':ambiguous}
             for symbol,count,first,last,rows,ambiguous in con.execute('SELECT instrument_id,count(*) FILTER(WHERE '+valid_key+'),min(date) FILTER(WHERE '+valid_key+'),max(date) FILTER(WHERE '+valid_key+'),sum(copies),count(*) FILTER(WHERE q.is_valid IS NOT TRUE OR q.copies!=1 OR c.day IS NULL) FROM quote_keys q LEFT JOIN calendar c ON q.date=c.day GROUP BY instrument_id').fetchall()}
    raw_counts=dict(con.execute('SELECT upper(thscode),count(*) FROM read_parquet(?) GROUP BY upper(thscode)',[parquet_input(raw_path)]).fetchall()) if raw_path is not None else {}
    quote_presence={}
    for symbol,row in reference.get('master',{}).items():
        if row.get('kind')!='stock':continue
        proof=present.get(symbol,{'valid_quote_count':0,'first_quote_date':None,'last_quote_date':None,'analysis_observed_rows':0,'ambiguous_quote_keys':0})
        proof.update(raw_observed_rows=raw_counts.get(symbol,0) if raw_path is not None else None,
                     observed_quote_rows=proof['analysis_observed_rows']+raw_counts.get(symbol,0),absence_verified=raw_path is not None)
        quote_presence[symbol]=proof
    identities=listing_status.resolve(reference,end,quote_presence,confirmations=listing_confirmations or {},market_snapshot_id=market_snapshot_id)
    calendar,stocks,ranges,unknown=stock_scope(reference,begin,end,resolved=identities)
    con.execute('CREATE TABLE scope(instrument_id VARCHAR,start_date VARCHAR,end_date VARCHAR,expected BIGINT)')
    if ranges:con.executemany('INSERT INTO scope VALUES (?,?,?,?)',ranges)
    counts=dict(con.execute('SELECT q.instrument_id,count(*) FROM quote_keys q JOIN scope s USING(instrument_id) JOIN calendar c ON q.date=c.day WHERE q.date BETWEEN s.start_date AND s.end_date AND q.is_valid AND q.copies=1 GROUP BY q.instrument_id').fetchall())
    expected=sum(row[3] for row in ranges);observed=sum(counts.values())
    gaps=[];date_budget=MAX_GAP_DATES;diagnosis_dates=MAX_GAP_DATES;episode_budget=gap_diagnosis.MAX_EPISODES;diagnosis_stocks=[]
    for symbol,start,finish,wanted in sorted(ranges,key=lambda row:(-(row[3]-counts.get(row[0],0)),row[0])):
        missing=wanted-counts.get(symbol,0)
        if missing<=0:continue
        gaps.append({'instrument_id':symbol,'name':stocks[symbol].get('name') or symbol,'missing_sessions':missing,
                     'start':start,'end':finish,'classification':'unquoted_unknown','dates':[]})
    # Bound Python records to 128 stocks. Read raw source evidence for all
    # reported missing pairs in one join, shared by raw and analysis modules.
    for offset in range(0,len(gaps),128):
        batch=gaps[offset:offset+128];ids=[row['instrument_id'] for row in batch]
        quoted={symbol:set() for symbol in ids}
        for symbol,day in con.execute('SELECT instrument_id,date FROM quote_keys q JOIN calendar c ON q.date=c.day WHERE is_valid AND copies=1 AND instrument_id IN (SELECT unnest(?))',[ids]).fetchall():quoted[symbol].add(day)
        for gap in batch:
            missing_dates=[day for day in calendar if gap['start']<=day<=gap['end'] and day not in quoted[gap['instrument_id']]]
            gap['dates']=missing_dates[:date_budget];date_budget-=len(gap['dates'])
            gap['dates_truncated']=len(gap['dates'])<len(missing_dates)
            stock,used=gap_diagnosis.stock_episodes(calendar,missing_dates,quoted[gap['instrument_id']],gap['instrument_id'],gap['name'],date_budget=diagnosis_dates,episode_budget=episode_budget)
            diagnosis_dates-=used;episode_budget-=len(stock['episodes']);diagnosis_stocks.append(stock)
    raw_presence=gap_diagnosis.raw_comparison(con,raw_path,diagnosis_stocks)
    gap_diagnosis.attach_local_evidence(diagnosis_stocks,raw_presence,raw_verified=raw_path is not None)
    diagnosis=gap_diagnosis.summary(diagnosis_stocks,source_signature=source_signature)
    return {'ready':True,'observed_count':observed,'expected_count':expected,'unquoted_sessions':expected-observed,
            'quote_rows':total,'duplicate_rows':duplicates,'invalid_price_keys':invalid,
            'known_stock_count':len(ranges),'stock_count':len(stocks),'unknown_identities':unknown,
            'pending_listing_count':identities['pending_listing_count'],
            'gap_stock_count':len(gaps),'gaps':gaps,'reported_gap_dates':MAX_GAP_DATES-date_budget,
            'gap_dates_truncated':any(row['dates_truncated'] for row in gaps),'gap_diagnosis':diagnosis,
            '_listing_quote_presence':quote_presence,'_listing_identities':{key:value for key,value in identities.items() if key!='items'},
            'scope':'当前目录中上市/退市有效期可界定的已上市股票 × 已留存交易日；可核验待上市证券不计入，未提供退市日按观察日截断',
            'reason':'已按连续市场交易日合并区间并对照本地原始包；无报价仍须结合来源或正式材料核验原因，不认定停牌，不填造K线'}


def _actions(con,path,reference,begin,end):
    fields={row[0] for row in con.execute('DESCRIBE SELECT * FROM read_parquet(?)',[parquet_input(path)]).fetchall()}
    required={'thscode','ex_date_ms','dividend_per_share','per_share_bonus','allotment_ratio','allotment_price','currency'}
    if not required<=fields:raise ValueError('公司行动缺少必要字段：'+','.join(sorted(required-fields)))
    numeric=('dividend_per_share','per_share_bonus','allotment_ratio','allotment_price')
    nonfinite=' OR '.join('('+field+' IS NOT NULL AND NOT isfinite('+field+'))' for field in numeric)
    negative=' OR '.join('('+field+' IS NOT NULL AND '+field+'<0)' for field in numeric)
    structural="thscode IS NULL OR NOT regexp_full_match(thscode,'[0-9]{6}\\.(SH|SZ|BJ)') OR event_date IS NULL OR "+nonfinite
    # Negative source fields can represent special capital actions. Preserve
    # them, but never silently feed them to the nonnegative adjustment model.
    con.execute("CREATE TEMP TABLE action_checks AS SELECT *, ("+structural+") AS structural_issue,("+negative+") AS special_action FROM (SELECT *,try(strftime(to_timestamp(ex_date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d')) AS event_date FROM read_parquet(?))",[parquet_input(path)])
    total,bad,securities,in_window,window_bad,structural_bad,special,window_special,outside_special,undated=con.execute("""SELECT count(*),count(*) FILTER(WHERE structural_issue OR special_action),count(distinct thscode),
        count(*) FILTER(WHERE event_date BETWEEN ? AND ?),
        count(*) FILTER(WHERE event_date BETWEEN ? AND ? AND (structural_issue OR special_action)),
        count(*) FILTER(WHERE structural_issue),count(*) FILTER(WHERE special_action),
        count(*) FILTER(WHERE special_action AND event_date BETWEEN ? AND ?),
        count(*) FILTER(WHERE special_action AND (event_date<? OR event_date>?)),
        count(*) FILTER(WHERE event_date IS NULL) FROM action_checks""",[begin,end,begin,end,begin,end,begin,end]).fetchone()
    problem_rows=con.execute('SELECT thscode,event_date,structural_issue,special_action FROM action_checks WHERE structural_issue OR special_action ORDER BY event_date,thscode LIMIT 20').fetchall()
    special_items=[];invalid_items=[]
    for symbol,day,invalid,is_special in problem_rows:
        item={'instrument_id':symbol,'code':symbol[:6] if isinstance(symbol,str) else None,'date':day,
              'scope':'window' if day and begin<=day<=end else 'outside_window' if day else 'unknown_date',
              'reason':'身份、日期或已提供有限数值需核验' if invalid else '负送转或配股字段属于当前模型未支持的特殊行动，需要单独核验；保留原值'}
        (invalid_items if invalid else special_items).append(item)
    symbols={row[0] for row in con.execute('SELECT DISTINCT thscode FROM action_checks WHERE thscode IS NOT NULL').fetchall()}
    directory={symbol for symbol,row in reference.get('master',{}).items() if row.get('kind')=='stock'}
    return {'ready':bad==0,'file_ready':bad==0,'schema_verified':True,'window_schema_verified':True,
            'window_ready':window_bad==0 and undated==0,'window_begin':begin,'window_end':end,
            'file_rows':total,'window_rows':in_window,'invalid_rows':bad,'file_invalid_rows':bad,
            'window_invalid_rows':window_bad,'file_structural_invalid_rows':structural_bad,
            'file_special_rows':special,'window_special_rows':window_special,'outside_window_special_rows':outside_special,
            'unscoped_date_rows':undated,'special_items':special_items,'invalid_items':invalid_items,
            'items_truncated':bad>len(problem_rows),'source_security_count':securities,'source_expected_count':None,
            'known_current_stock_count':len(symbols & directory),'outside_current_directory_count':len(symbols-directory),
            'scope':'窗口内字段核验与来源全文件特殊行动分别统计；不改写原值，不自动应用未支持的负送转字段',
            'reason':'来源未提供全市场应有事件总数；窗口字段通过不代表全文件行动模型或全部公司行动已验收'}


def verify(market,context=None,*,progress=None,guard=None,listing_callback=None):
    context=context or _context(market);status=context['status'];reference=context['reference']
    total=len(context['files'])+3;done=0;checks=[];failures=[];valid_roles={}
    def tick(stage,message):
        if guard:guard()
        if progress:progress(stage,min(99,int(done/max(1,total)*99)),message,{'actual_completed_batches':done,'actual_total_batches':total})
    tick('核验本地文件','只读当前发布数据，不访问凭据、不联网、不改写行情')
    for item in context['files']:
        path=item['path'];ok=path.is_file() and path.stat().st_size==item['bytes'] and hash_file(path)==item['sha256']
        checks.append({'role':item['role'],'sha256_verified':ok})
        if ok:valid_roles[item['role']]=path
        else:failures.append(item['role']+' 文件缺失、大小或SHA不匹配')
        done+=1;tick('核验本地文件',item['role']+' · '+('通过' if ok else '未通过'))
    report={'version':VERSION,'snapshot_id':status['snapshot_id'],'source_signature':context['signature'],
            'listing_revision':context['listing_revision'],
            'target_date':status['data_end'],'checked_at_ms':now_ms(),'file_checks':checks,'failures':failures,
            'daily':{'ready':False,'reason':'分析行情文件未通过SHA核验'},
            'corporate_actions':{'ready':False,'reason':'公司行动文件未通过SHA核验'}}
    initial=listing_status.resolve(reference,status['data_end'],confirmations=context['listing_confirmations'],market_snapshot_id=status['snapshot_id'])
    report['identities']={key:value for key,value in initial.items() if key!='items'}
    done+=1;tick('核验证券有效期','名录与上市身份分开统计；随后用本地唯一有效报价核对待上市状态')
    _root().mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='scan-',dir=_root()) as spill:
        con=duckdb.connect(':memory:')
        try:
            con.execute('SET threads=1');con.execute("SET memory_limit='512MB'");con.execute('SET temp_directory=?',[spill])
            raw=valid_roles.get('raw-daily.parquet');raw_ok=False
            if raw:
                fields={row[0] for row in con.execute('DESCRIBE SELECT * FROM read_parquet(?)',[parquet_input(raw)]).fetchall()}
                required={'thscode','date_ms','open_price','high_price','low_price','close_price','volume','turnover'}
                raw_ok=required<=fields
                for item in checks:
                    if item['role']=='raw-daily.parquet':item['schema_verified']=raw_ok
                if not raw_ok:failures.append('原始行情缺少必要字段：'+','.join(sorted(required-fields)))
            for key,role,operation,label in [('daily','daily.parquet',_daily,'核验逐股报价日期'),('corporate_actions','corporate-actions.parquet',_actions,'核验公司行动字段')]:
                if role in valid_roles:
                    try:
                        options={'raw_path':raw if raw_ok else None,'listing_confirmations':context['listing_confirmations'],
                                 'market_snapshot_id':status['snapshot_id'],'source_signature':context['signature']} if key=='daily' else {}
                        report[key]=operation(con,valid_roles[role],reference,status['data_begin'],status['data_end'],**options)
                        if key=='daily':
                            report['identities']=report[key].pop('_listing_identities',report['identities'])
                            quote_presence=report[key].pop('_listing_quote_presence',None)
                            if listing_callback is not None and quote_presence is not None:listing_callback(quote_presence)
                    except (ValueError,duckdb.Error) as error:report[key]={'ready':False,'reason':str(error)};failures.append(label+'未通过：'+str(error))
                done+=1;tick(label,'本地结构与日期范围核验完成；缺口保持原状')
        finally:con.close()
    from .market_supplement import load_supplement
    supplement=load_supplement(status['snapshot_id'])
    absent=[]
    for kind,key in [('limitup','annual_pools'),('limitdown','annual_down_pools')]:
        for day in market.calendar():
            if day[:4]==status['data_end'][:4] and day<=status['data_end'] and day not in supplement.get(key,{}):absent.append({'date':day,'pool_kind':kind})
    def brief(value):return {key:value.get(key) for key in ('observed_days','covered_days','expected_days','complete')}
    report['market_pools']={'missing_batches':absent,'up':brief(supplement.get('annual_coverage',{})),'down':brief(supplement.get('annual_down_coverage',{}))}
    # The remote action intentionally reuses the existing supplement task. It
    # declares that task's broader partial-pool, public-detail and fund scope.
    report['repair_plan']={'operation':'market_supplement','target_date':status['data_end'],
        'endpoint':'/sync/market-supplement','payload':{'year_history':True,'sector_history':False,'recent_days':1},
        'missing_batches':absent,
        'scope':['复用完整来源池，重新请求本年不完整或缺失的涨停/跌停批次','更新当前行情日封板明细及近20个留存交易日行业资金；不重新下载全库板块K线'],
        'reason':'需要已配置来源并联网；来源若仍返回空或无法核验，继续保持未知，不能保证补齐。日K无报价日和未知上市日期不由此任务填补'}
    daily=report['daily'];actions=report['corporate_actions']
    if daily.get('duplicate_rows',0):failures.append('发现'+str(daily['duplicate_rows'])+'条重复证券日期键，未作为唯一有效报价计入')
    if daily.get('invalid_price_keys',0):failures.append('发现'+str(daily['invalid_price_keys'])+'个无效身份/价格键，未作为有效报价计入')
    if actions.get('file_structural_invalid_rows',0):failures.append('公司行动全文件有'+str(actions['file_structural_invalid_rows'])+'条身份、日期或有限数值需核验')
    report['notices']=['来源全文件另有'+str(actions['file_special_rows'])+'条特殊公司行动需单独核验，原值保留，不强行复权'] if actions.get('file_special_rows',0) else []
    unknown=report['identities']['unknown']
    partial=(failures or unknown or absent or daily.get('ready') is not True or actions.get('ready') is not True
             or daily.get('unquoted_sessions',0) or daily.get('duplicate_rows',0) or daily.get('invalid_price_keys',0))
    report['quality']='partial' if partial else 'complete'
    report['message']='本地核验完成；无报价 '+str(report['daily'].get('unquoted_sessions','待核验'))+' 日，上市日期未知 '+str(len(unknown))+' 只；'+('另有'+str(len(failures))+'项本地校验问题；' if failures else '')+'未联网，未改写快照质量'
    if guard:guard()
    return report


def execute_check(identifier,progress=None):
    with connect() as connection:row=connection.execute('SELECT parameters_json FROM job WHERE job_id=?',(identifier,)).fetchone()
    if not row:raise ValueError('本地核验任务不存在')
    params=json.loads(row[0]);market=get_market();context=_context(market)
    def guard():
        with connect() as connection:state=connection.execute('SELECT status FROM job WHERE job_id=?',(identifier,)).fetchone()
        if not state or state[0]=='cancelled':raise tasks.TaskCancelled()
        if state[0]=='paused':raise tasks.TaskPaused()
        if _context(get_market())['signature']!=params['source_signature']:raise ValueError('行情或来源证据已变化，请刷新后重新核验')
    listing_evidence={}
    guard();report=verify(market,context,progress=progress,guard=guard,listing_callback=lambda value:listing_evidence.update(value));report['job_id']=identifier
    guard()
    if listing_evidence:
        listing_status.snapshot_summary(context['reference'],context['status']['data_end'],listing_evidence,
            context['status']['snapshot_id'],expected_revision=context['listing_revision'])
    artifact=_root()/'reports'/(digest(report)+'.json');atomic_json(artifact,report)
    guard();atomic_json(_root()/'latest.json',{'artifact':str(artifact.relative_to(_root())),'sha256':hash_file(artifact)})
    return report
