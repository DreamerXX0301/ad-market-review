"""Explicit, resumable source comparison for a frozen local gap diagnosis.

Remote absence is evidence about this source, never proof of suspension. This
operation only diagnoses; accepted OHLC is retained for a separate publication
review and cannot mutate an existing published generation.
"""
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from fastapi import HTTPException

from app.core import atomic_json, connect, digest, dumps, get_market, hash_file, now_ms, raw_dir, setting, work_dir
from app.services import local_coverage_check as local, source_service, task_service as tasks
from app.services.sync_service import midnight_ms, safe_payload

VERSION='quote-gap-source-v1'
OPERATION='quote_gap_source_check'
CHINA=ZoneInfo('Asia/Shanghai')
MAX_REPORT_BYTES=12_000_000


def _root():return work_dir()/'quote-gap-source'


def _current():
    market=get_market();context=local._context(market);report,_=local._read_report(context)
    if not report or not report.get('daily',{}).get('gap_diagnosis'):
        raise HTTPException(409,'请先完成当前代际的本地数据核验，再向来源核验报价缺口')
    diagnosis=report['daily']['gap_diagnosis']
    if not diagnosis.get('details_complete'):
        raise HTTPException(409,'当前缺口明细被截断，不能把不完整计划当作全量核验')
    return market,context,report,diagnosis


def _read_evidence():
    path=_root()/'latest.json'
    if not path.is_file():return None
    try:
        pointer=json.loads(path.read_text());artifact=(_root()/pointer['artifact']).resolve()
        if not artifact.is_relative_to(_root().resolve()) or not artifact.is_file() or artifact.stat().st_size>MAX_REPORT_BYTES or hash_file(artifact)!=pointer['sha256']:return None
        result=json.loads(artifact.read_text())
        if result.get('version')!=VERSION:return None
        return {**result,'receipt_id':artifact.stem,'receipt_sha256':pointer['sha256']}
    except (OSError,ValueError,KeyError,TypeError):return None


def read_view(*,full=False):
    try:_,context,_,diagnosis=_current()
    except HTTPException as error:return {'available':False,'reason':error.detail,'evidence':None}
    evidence=_read_evidence()
    current=bool(evidence and evidence.get('source_signature')==context['signature'] and evidence.get('snapshot_id')==context['status']['snapshot_id'] and evidence.get('diagnosis_id')==diagnosis['diagnosis_id'])
    result={'available':True,'plan_id':context['signature'],'diagnosis_id':diagnosis['diagnosis_id'],
            'snapshot_id':context['status']['snapshot_id'],'stock_count':diagnosis['stock_count'],
            'missing_sessions':diagnosis['total_missing_sessions'],'is_current':current,'stale':bool(evidence and not current),
            'network':True,'operation_scope':'逐股请求实际未复权日行情；只核验不改写已发布行情；空结果不等于停牌',
            'evidence':evidence if current else None}
    if result['evidence'] and not full:result['evidence']={key:value for key,value in evidence.items() if key not in ('gaps','stocks')}
    return result


def create_check(payload):
    if not isinstance(payload,dict) or set(payload)-{'request_key','plan_id','instrument_ids'}:raise HTTPException(422,'来源核验只接受计划、股票范围与请求标识')
    key=payload.get('request_key')
    if key is not None and (not isinstance(key,str) or not key.strip() or len(key)>200):raise HTTPException(422,'请求标识须为1至200字符')
    _,context,_,diagnosis=_current()
    if payload.get('plan_id')!=context['signature']:raise HTTPException(409,'行情或本地核验计划已更新，请刷新后重试')
    available={row['instrument_id'] for row in diagnosis['stocks']}
    selected=payload.get('instrument_ids')
    if selected is not None and (not isinstance(selected,list) or not selected or len(selected)>10000 or any(not isinstance(value,str) or value not in available for value in selected)):
        raise HTTPException(422,'股票范围必须来自当前报价缺口清单')
    identifiers=sorted(set(selected) if selected is not None else available)
    if not identifiers:raise HTTPException(409,'当前范围没有需要核验的无报价日')
    adapter=source_service.client('fuyao')
    if not adapter.key:raise HTTPException(422,'请先配置扶摇来源Key，再核验历史报价')
    parameters={'operation':OPERATION,'version':VERSION,'source_id':'fuyao','source_signature':context['signature'],
                'market_snapshot_id':context['status']['snapshot_id'],'diagnosis_id':diagnosis['diagnosis_id'],
                'credential_revision':setting('source-credential-revision:fuyao','legacy'),'instrument_ids':identifiers}
    return tasks.create_task('source_check',parameters,target_date=context['status']['data_end'],request_key=key)


def _guard(connection,job,parameters):
    row=connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
    if row and row['status']=='paused':raise tasks.TaskPaused()
    if not row or row['status']!='running' or row['lease_owner']!=job['lease_owner']:raise tasks.TaskCancelled()
    # Only the small head/metadata fingerprint is read; never a historical scan.
    if local._context(get_market())['signature']!=parameters['source_signature']:
        raise ValueError('行情或人工身份已更新，旧来源核验不继续提交；请刷新计划后重跑')
    if setting('source-credential-revision:fuyao','legacy')!=parameters['credential_revision']:
        raise ValueError('来源凭据版本已变化，请用新计划重新核验')


def _quotes(payload,symbol,start,end):
    data=payload.get('data')
    if not isinstance(data,dict) or not isinstance(data.get('item'),list):raise ValueError('来源历史行情结构无效，不能当作无报价')
    if (data.get('thscode') not in (None,symbol) or data.get('instrument_id') not in (None,symbol)
        or data.get('interval') not in (None,'1d') or data.get('adjust') not in (None,'none')
        or data.get('adjusted') not in (None,'none') or data.get('currency') not in (None,'CNY')):
        raise ValueError('来源返回股票或价格口径不匹配')
    if data.get('pagination') or data.get('has_more') or data.get('truncated'):raise ValueError('来源响应出现分页或截断标识，不能作为完整区间证据')
    result={}
    for row in data['item']:
        if not isinstance(row,dict) or any(row.get(key) not in (None,symbol) for key in ('thscode','instrument_id')):
            raise ValueError('来源逐行股票身份不匹配')
        if (row.get('interval') not in (None,'1d') or row.get('adjust') not in (None,'none')
            or row.get('adjusted') not in (None,'none') or row.get('currency') not in (None,'CNY')):
            raise ValueError('来源逐行价格口径不匹配')
        stamp=row.get('date_ms')
        if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp):raise ValueError('来源日期无效')
        day=datetime.fromtimestamp(stamp/1000,CHINA).date().isoformat()
        if not start<=day<=end:raise ValueError('来源返回请求范围外报价')
        values={name:row.get(key) for name,key in [('open','open_price'),('high','high_price'),('low','low_price'),('close','close_price'),('volume','volume'),('amount','turnover')]}
        if (any(isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) for value in values.values())
            or min(values[key] for key in ('open','high','low','close'))<=0 or values['volume']<0 or values['amount']<0
            or values['high']<max(values['open'],values['low'],values['close']) or values['low']>min(values['open'],values['high'],values['close'])):
            raise ValueError('来源OHLC或量额无效，不能标为可补报价')
        if day in result:raise ValueError('来源同一交易日出现重复报价')
        result[day]=values
    return result


def request_windows(dates,calendar):
    """Only query gap-bearing windows, capped at 96 calendar dates each.

    The endpoint declares ten-year history without paging. Short requests also
    avoid treating a possible long-response row cap as an absent early history.
    Adjacent real market sessions are requested for a price overlap comparison.
    """
    groups=[]
    for day in sorted(set(dates)):
        if not groups or date.fromisoformat(day)-date.fromisoformat(groups[-1][0])>timedelta(days=89):groups.append([day])
        else:groups[-1].append(day)
    calendar=sorted(set(calendar));result=[]
    for group in groups:
        before=[day for day in calendar if day<group[0]];after=[day for day in calendar if day>group[-1]]
        start=before[-1] if before else group[0];end=after[0] if after else group[-1]
        # A long exchange holiday can put a neighboring session far away; it
        # is context only and never broadens the frozen gap request unboundedly.
        start=max(start,(date.fromisoformat(group[0])-timedelta(days=3)).isoformat())
        end=min(end,(date.fromisoformat(group[-1])+timedelta(days=3)).isoformat())
        result.append((start,end))
    return result


def compare(stock,source_quotes,local_quotes,checked_at):
    gaps={};fillable=absent=conflicts=0
    for episode in stock['episodes']:
        dates=episode['dates'];returned=[day for day in dates if day in source_quotes]
        missing=[day for day in dates if day not in source_quotes]
        conflict=[day for day in returned if day in local_quotes and source_quotes[day]!=local_quotes[day]]
        fillable+=len(returned);absent+=len(missing);conflicts+=len(conflict)
        gaps[episode['gap_id']]={'status':'local_missing' if returned and not missing else 'partial' if returned else 'source_no_quote',
            'checked_at_ms':checked_at,'missing_dates':missing,'returned_dates':returned,'matched_dates':[],
            'local_difference_dates':conflict,'source':'fuyao','confirmed_suspension':None,
            'reason':'来源返回了本地缺口日的有效实际报价，可进入补充发布核验' if returned else '本次来源同区间也没有这些日期的报价；来源未提供停牌证明，无须反复增量重试，也不补造K线',
            'next_action':'保留原始响应与价格，单独核验并发布新代际' if returned else '该来源无可补报价；若需确定停牌原因，仍需交易所或公司公告证据'}
    overlap=set(source_quotes)&set(local_quotes)
    differences=[day for day in sorted(overlap) if source_quotes[day]!=local_quotes[day]]
    return {'gaps':gaps,'fillable_sessions':fillable,'source_absent_sessions':absent,'conflict_sessions':conflicts,
            'overlap_sessions':len(overlap),'overlap_difference_dates':differences}


def _accepted(job):
    with connect() as connection:
        rows=connection.execute("SELECT diagnostic_json,content_sha256 FROM sync_batch WHERE job_id=? AND capability=? AND status='accepted'",(job['job_id'],OPERATION)).fetchall()
    result={}
    for row in rows:
        value=json.loads(row[0]);path=(raw_dir()/value['raw_relative']).resolve()
        if not path.is_relative_to(raw_dir().resolve()) or not path.is_file() or hash_file(path)!=row[1]:raise ValueError('已核验原始响应缺失或SHA变化，拒绝复用')
        result[value['instrument_id']]=value
    return result


def _publish(job,parameters,diagnosis,results):
    gaps={key:value for item in results.values() for key,value in item.get('gaps',{}).items()}
    report={'version':VERSION,'source_signature':parameters['source_signature'],'snapshot_id':parameters['market_snapshot_id'],
            'diagnosis_id':diagnosis['diagnosis_id'],'job_id':job['job_id'],'source':'fuyao','checked_at_ms':now_ms(),
            'planned_stocks':len(parameters['instrument_ids']),'checked_stocks':len(results),'failed_stocks':sum(row.get('status')=='failed' for row in results.values()),
            'gap_stock_count':diagnosis['stock_count'],'total_gap_sessions':diagnosis['total_missing_sessions'],
            'checked_sessions':sum(len(row.get('missing_dates',[]))+len(row.get('returned_dates',[])) for row in gaps.values()),
            'fillable_sessions':sum(row.get('fillable_sessions',0) for row in results.values()),
            'source_absent_sessions':sum(row.get('source_absent_sessions',0) for row in results.values()),
            'overlap_difference_sessions':sum(len(row.get('overlap_difference_dates',[])) for row in results.values()),
            'gaps':gaps,'stocks':results,'publication_changed':False,'confirmed_suspension_sessions':0}
    path=_root()/'reports'/(digest(report)+'.json');atomic_json(path,report)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE');_guard(connection,job,parameters)
        atomic_json(_root()/'latest.json',{'artifact':path.relative_to(_root()).as_posix(),'sha256':hash_file(path)})
    return report


def execute_check(job):
    parameters=json.loads(job['parameters_json']);market,context,_,diagnosis=_current()
    if (parameters.get('version')!=VERSION or parameters['source_signature']!=context['signature'] or parameters['diagnosis_id']!=diagnosis['diagnosis_id']):raise ValueError('来源核验计划已过期')
    stocks={row['instrument_id']:row for row in diagnosis['stocks']};identifiers=parameters['instrument_ids']
    if any(symbol not in stocks for symbol in identifiers):raise ValueError('来源核验范围不属于当前缺口清单')
    results=_accepted(job);adapter=source_service.client('fuyao')
    from app.analytics.market_storage import parquet_input
    daily=next(row['path'] for row in context['files'] if row['role']=='daily.parquet')
    con=duckdb.connect(':memory:')
    try:
        con.execute("SET memory_limit='256MB'");con.execute('SET threads=1')
        quote_cache={}
        for position,symbol in enumerate(identifiers):
            with connect() as connection:_guard(connection,job,parameters)
            if symbol in results:continue
            if symbol not in quote_cache:
                batch=[value for value in identifiers[position:position+32] if value not in results]
                quote_cache={value:{} for value in batch}
                for row in con.execute('SELECT instrument_id,date,open,high,low,close,volume,amount FROM read_parquet(?) WHERE instrument_id IN (SELECT unnest(?))',[parquet_input(daily),batch]).fetchall():
                    quote_cache[row[0]][row[1]]=dict(zip(('open','high','low','close','volume','amount'),row[2:]))
            stock=stocks[symbol];dates=sorted({day for episode in stock['episodes'] for day in episode['dates']})
            windows=request_windows(dates,context['reference']['calendar'])
            value={'instrument_id':symbol,'request_windows':windows,'checked_at_ms':now_ms()}
            try:
                responses=[];quotes={}
                for start,end in windows:
                    with connect() as connection:_guard(connection,job,parameters)
                    request={'thscode':symbol,'interval':'1d','adjust':'none','start':midnight_ms(start),'end':midnight_ms(end)+86400000-1}
                    payload=safe_payload(adapter.request('/api/a-share/prices/historical',request))
                    incoming=_quotes(payload,symbol,start,end)
                    if any(day in quotes and quotes[day]!=row for day,row in incoming.items()):raise ValueError('来源相邻窗口重叠报价不一致')
                    quotes.update(incoming);responses.append({'request':request,'response':payload,'response_sha256':digest(payload)})
                local_quotes={day:value for day,value in quote_cache[symbol].items() if any(start<=day<=end for start,end in windows)}
                value.update(compare(stock,quotes,local_quotes,value['checked_at_ms']),status='accepted')
                raw_path=raw_dir()/'quote-gap-source'/job['job_id']/digest(job['lease_owner'])/(symbol+'.json')
                atomic_json(raw_path,{'version':VERSION,'responses':responses});value['raw_relative']=raw_path.relative_to(raw_dir()).as_posix();sha=hash_file(raw_path)
                value['source_sha256']=sha
                with connect() as connection:
                    connection.execute('BEGIN IMMEDIATE');_guard(connection,job,parameters)
                    attempt=connection.execute('SELECT coalesce(max(attempt_no),0)+1 FROM sync_batch WHERE job_id=? AND capability=? AND batch_key=?',(job['job_id'],OPERATION,symbol)).fetchone()[0]
                    connection.execute("INSERT INTO sync_batch VALUES(?,?,?,?,?,?,?,?,?,?)",(job['job_id'],OPERATION,symbol,attempt,'accepted',value['checked_at_ms'],len(quotes),sha,None,dumps(value)))
            except (tasks.TaskCancelled,tasks.TaskPaused):raise
            except (ValueError,source_service.ProviderError,OverflowError,OSError) as error:
                value.update(status='failed',reason=str(error)[:300],gaps={episode['gap_id']:{'status':'failed','source':'fuyao','checked_at_ms':value['checked_at_ms'],'reason':str(error)[:300],'confirmed_suspension':None} for episode in stock['episodes']})
            results[symbol]=value
            _publish(job,parameters,diagnosis,results)
            tasks.progress(job['job_id'],'向来源核验报价缺口',min(99,round(len(results)/len(identifiers)*99)),
                           symbol+' · '+('来源核验完成' if value['status']=='accepted' else '请求未确认，保留待核验'),
                           {'actual_completed_batches':len(results),'actual_total_batches':len(identifiers)},lease_owner=job['lease_owner'])
        report=_publish(job,parameters,diagnosis,results)
    finally:con.close()
    message='来源核验完成：'+str(report['source_absent_sessions'])+'日来源也无报价，'+str(report['fillable_sessions'])+'日发现可补实际报价；未改写行情'
    tasks.finish(job['job_id'],'partial' if report['failed_stocks'] else 'succeeded',message,lease_owner=job['lease_owner'])
    return report
