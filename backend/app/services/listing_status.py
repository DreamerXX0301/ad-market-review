"""Source listing dates and explicit user statements remain separate evidence.

Read views use only the small master and matching SQLite receipts. Quote scans
are explicit writes/worker checks; no missing quote is itself proof of pending.
"""
from contextlib import nullcontext
from datetime import date
import json
import logging
import re

import duckdb
from fastapi import HTTPException

from app.core import connect, digest, dumps, get_market, new_id, now_ms, published_dir
from app.services import task_service as tasks
from app.services.workspace_common import current, pin

VERSION='listing-evidence-v1'
OPERATION='listing_status_check'
MAX_CONFIRM=128


def valid_date(value):
    try:return isinstance(value,str) and date.fromisoformat(value).isoformat()==value
    except ValueError:return False


def read_revision(connection=None):
    with nullcontext(connection) if connection is not None else connect() as db:
        row=db.execute('SELECT revision FROM listing_status_revision WHERE singleton=1').fetchone()
        return row[0] if row else 0


def confirmations(connection=None):
    with nullcontext(connection) if connection is not None else connect() as db:
        rows=db.execute('SELECT * FROM (SELECT *,row_number() OVER(PARTITION BY instrument_id ORDER BY created_at_ms DESC,rowid DESC) n FROM listing_status_confirmation) WHERE n=1 ORDER BY instrument_id')
        return {row['instrument_id']:{key:value for key,value in dict(row).items() if key!='n'} for row in rows}


def _reference_hash(reference):
    return digest([{'instrument_id':symbol,**{key:row.get(key) for key in ('kind','name','list_date','end_date')}} for symbol,row in sorted(reference.get('master',{}).items())])


def _head(connection):
    row=connection.execute("SELECT s.snapshot_id,s.lineage_json FROM published_head h JOIN dataset_snapshot s USING(snapshot_id) WHERE h.module='daily_raw' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    if not row:raise HTTPException(409,'请先发布行情再核验上市身份')
    return {'primary_snapshot_id':row[0],'market_snapshot_id':json.loads(row[1]).get('market_snapshot_id') or row[0], 'business_date':current(connection)}


def resolve(reference, as_of, quote_presence=None, *, confirmations=None, market_snapshot_id=None):
    """Pure classification. Only an explicit count=0 receipt proves no quotes.

    ``items`` is a dict for bounded scope construction; public summaries omit
    listed items and keep all pending/unknown rows with their evidence.
    """
    manual=confirmations or {};presence=quote_presence or {};items={}
    for symbol,source in sorted(reference.get('master',{}).items()):
        if source.get('kind')!='stock':continue
        listed=source.get('list_date');ended=source.get('end_date');statement=manual.get(symbol)
        proof=presence.get(symbol);count=proof.get('valid_quote_count') if isinstance(proof,dict) else None
        checked=isinstance(count,int) and not isinstance(count,bool) and count>=0
        no_quote=checked and count==0 and proof.get('observed_quote_rows',0)==0 and proof.get('ambiguous_quote_keys',0)==0 and proof.get('absence_verified',True) is True
        row={'instrument_id':symbol,'name':source.get('name') or symbol,'source_list_date':listed,
             'listing_status':'unknown','evidence_kind':'unverified','source':'来源证券名录',
             'confirmed_date':None,'confirmed_at_ms':None,'effective_business_date':None,'reason':'来源未提供有效上市日期，需核验身份',
             'reference':{'market_snapshot_id':market_snapshot_id,'source_list_date':listed},'can_edit':True}
        if valid_date(listed) and listed<=as_of and (ended is None or valid_date(ended)):
            row.update(listing_status='listed',evidence_kind='source_list_date',reason='来源已提供不晚于观察日的上市日期',can_edit=False,effective_business_date=listed)
        elif valid_date(listed) and listed>as_of:
            if no_quote:
                row.update(listing_status='pending',evidence_kind='source_list_date',reason='来源上市日期晚于观察日，本代未见有效报价',effective_business_date=as_of,can_edit=False)
            elif checked and count>0:
                row.update(evidence_kind='quote_presence',reason='来源上市日期晚于观察日，但本代已出现真实报价，身份存在冲突')
            else:row['reason']='来源上市日期晚于观察日，尚未核验本代报价是否冲突'
        elif checked and count>0:
            row.update(listing_status='quoted_unknown_date',evidence_kind='quote_presence',reason='本代已出现真实报价，来源上市日期仍待核验；不以首根报价推造上市日')
        elif statement and statement['status']=='pending' and statement['observed_business_date']<=as_of and no_quote:
            row.update(listing_status='pending',evidence_kind='manual_user_confirmation',source='用户确认（非官方来源）',
                       confirmed_date=statement['observed_business_date'],confirmed_at_ms=statement.get('created_at_ms'),effective_business_date=statement['observed_business_date'],reason=statement['reason'])
        elif statement:
            row['reason']='人工状态已更正为待核验' if statement['status']=='unknown' else '人工待上市确认尚缺本代无报价核验，不能复用旧代回执'
        if checked and count==0 and not no_quote and row['listing_status']!='listed':
            row.update(listing_status='unknown',evidence_kind='quote_presence',reason='本代存在原始、重复或无效报价记录，或原始文件未核验，不能当作无报价证据')
        if ended is not None and not valid_date(ended):
            row.update(listing_status='unknown',evidence_kind='unverified',reason='来源退市日期无效，上市区间待核验')
        if checked:row['reference']['quote_presence']=proof
        if statement:
            row['reference']['manual_confirmation']={key:statement.get(key) for key in ('confirmation_id','status','source_kind','observed_business_date','created_at_ms','reason','evidence','market_snapshot_id')}
        items[symbol]=row
    pending=[row for row in items.values() if row['listing_status']=='pending']
    unknown=[row for row in items.values() if row['listing_status'] not in ('listed','pending')]
    listed=sum(row['listing_status']=='listed' for row in items.values())
    return {'stock_count':len(items),'catalog_count':len(items),'listed_count':listed,'known_count':listed,
            'pending_listing_count':len(pending),'unknown_count':len(unknown),'pending_listing':pending,'unknown':unknown,
            'indices_excluded':sum(row.get('kind')=='index' for row in reference.get('master',{}).values()),'items':items}


def snapshot_summary(reference, as_of, quote_presence, market_snapshot_id, *, connection=None, expected_revision=None):
    with nullcontext(connection) if connection is not None else connect() as db:
        if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
        head=_head(db);revision=read_revision(db)
        if head['business_date']!=as_of or head['market_snapshot_id']!=market_snapshot_id or (expected_revision is not None and revision!=expected_revision):
            raise HTTPException(409,'行情或人工上市身份已变化，请重新核验')
        result=resolve(reference,as_of,quote_presence,confirmations=confirmations(db),market_snapshot_id=market_snapshot_id)
        summary={key:value for key,value in result.items() if key!='items'}
        summary.update(listing_revision=revision,market_snapshot_id=market_snapshot_id,primary_snapshot_id=head['primary_snapshot_id'],business_date=as_of)
        candidates={symbol:proof for symbol,proof in quote_presence.items() if symbol in result['items'] and result['items'][symbol]['listing_status']!='listed'}
        reference_hash=_reference_hash(reference);identifier=digest([head['primary_snapshot_id'],market_snapshot_id,as_of,revision,reference_hash])
        db.execute('INSERT INTO listing_status_summary VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(summary_id) DO UPDATE SET identities_json=excluded.identities_json,presence_json=excluded.presence_json,checked_at_ms=excluded.checked_at_ms',
                   (identifier,market_snapshot_id,head['primary_snapshot_id'],as_of,revision,reference_hash,dumps(summary),dumps(candidates),now_ms()))
        return summary


def read_summary(market_snapshot_id, as_of, reference=None):
    with connect() as db:
        db.execute('BEGIN')
        return _read_summary(db,market_snapshot_id,as_of,reference)


def _read_summary(db,market_snapshot_id,as_of,reference=None):
    try:head=_head(db)
    except HTTPException:return None
    if head['market_snapshot_id']!=market_snapshot_id or head['business_date']!=as_of:return None
    row=db.execute('SELECT * FROM listing_status_summary WHERE market_snapshot_id=? AND primary_snapshot_id=? AND business_date=? AND manual_revision=?'+(' AND reference_hash=?' if reference is not None else '')+' ORDER BY checked_at_ms DESC LIMIT 1',
                   [market_snapshot_id,head['primary_snapshot_id'],as_of,read_revision(db),*([_reference_hash(reference)] if reference is not None else [])]).fetchone()
    return json.loads(row['identities_json']) if row else None


def read_view():
    market=get_market();status=market.status()
    if not status.get('ready'):return {'business_date':None,'market_snapshot_id':None,'revision':read_revision(),'summary_is_current':False,'identities':None,'confirmations':[]}
    reference=market.reference()
    with connect() as db:
        db.execute('BEGIN')
        head=_head(db);revision=read_revision(db);manual=confirmations(db)
        if status.get('snapshot_id')!=head['market_snapshot_id']:raise HTTPException(409,'行情实例正在切换，请刷新后重试')
        summary=_read_summary(db,head['market_snapshot_id'],head['business_date'],reference)
    is_current=summary is not None
    if summary is None:summary={key:value for key,value in resolve(reference,head['business_date'],confirmations=manual,market_snapshot_id=head['market_snapshot_id']).items() if key!='items'}
    return {'business_date':head['business_date'],'market_snapshot_id':head['market_snapshot_id'],'revision':revision,
            'summary_is_current':is_current,'identities':summary,
            'confirmations':[{key:value for key,value in row.items() if key not in ('request_hash','quote_presence_json')} for row in manual.values()]}


def confirmation_history(instrument_id=None,limit=100):
    if instrument_id is not None and (not isinstance(instrument_id,str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)',instrument_id)):raise HTTPException(422,'审计股票须为完整A股代码')
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=1000:raise HTTPException(422,'审计条数须为1至1000')
    with connect() as db:
        condition=' WHERE instrument_id=?' if instrument_id else '';args=[instrument_id] if instrument_id else []
        total=db.execute('SELECT count(*) FROM listing_status_confirmation'+condition,args).fetchone()[0]
        rows=db.execute('SELECT * FROM listing_status_confirmation'+condition+' ORDER BY created_at_ms DESC,rowid DESC LIMIT ?',[*args,limit])
        items=[]
        for row in rows:
            item={key:value for key,value in dict(row).items() if key!='request_hash'}
            item['quote_presence']=json.loads(item.pop('quote_presence_json'));items.append(item)
    return {'items':items,'total':total}


def quote_presence(market, identifiers, as_of):
    """Worker/write only: bounded SQL aggregation, never materialize history rows."""
    status=market.status();root=published_dir().resolve();generation=(root/status['snapshot_id']).resolve()
    from app.analytics.market_storage import descriptor_path,parquet_input
    path=descriptor_path(generation,'daily.parquet');raw=descriptor_path(generation,'raw-daily.parquet')
    if not generation.is_relative_to(root) or not path.is_file():raise HTTPException(409,'当前代际分析报价文件不可用，不能确认无报价')
    result={};con=duckdb.connect(':memory:')
    try:
        con.execute('SET threads=1');con.execute("SET memory_limit='256MB'")
        valid='open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL AND volume IS NOT NULL AND amount IS NOT NULL AND isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close) AND isfinite(volume) AND isfinite(amount) AND least(open,high,low,close)>0 AND volume>=0 AND amount>=0 AND high>=greatest(open,low,close) AND low<=least(open,high,close)'
        calendar=market.calendar()
        for offset in range(0,len(identifiers),MAX_CONFIRM):
            batch=identifiers[offset:offset+MAX_CONFIRM]
            rows=con.execute('SELECT instrument_id,count(*) FILTER(WHERE is_valid AND copies=1),min(date) FILTER(WHERE is_valid AND copies=1),max(date) FILTER(WHERE is_valid AND copies=1),sum(copies),count(*) FILTER(WHERE is_valid IS NOT TRUE OR copies!=1) FROM (SELECT instrument_id,date,bool_and('+valid+' AND date IS NOT NULL AND date<=? AND date IN (SELECT unnest(?))) AS is_valid,count(*) copies FROM read_parquet(?) WHERE instrument_id IN (SELECT unnest(?)) GROUP BY instrument_id,date) GROUP BY instrument_id',[as_of,calendar,parquet_input(path),batch]).fetchall()
            found={symbol:{'valid_quote_count':count,'first_quote_date':first,'last_quote_date':last,'analysis_observed_rows':observed,'ambiguous_quote_keys':ambiguous} for symbol,count,first,last,observed,ambiguous in rows}
            raw_counts=dict(con.execute('SELECT thscode,count(*) FROM read_parquet(?) WHERE thscode IN (SELECT unnest(?)) GROUP BY thscode',[parquet_input(raw),batch]).fetchall()) if raw.is_file() else {}
            for symbol in batch:
                item=found.get(symbol,{'valid_quote_count':0,'first_quote_date':None,'last_quote_date':None,'analysis_observed_rows':0,'ambiguous_quote_keys':0})
                item.update(raw_observed_rows=raw_counts.get(symbol,0),absence_verified=raw.is_file())
                item['observed_quote_rows']=item['analysis_observed_rows']+item['raw_observed_rows'];result[symbol]=item
    finally:con.close()
    return result


def _context():
    market=get_market();status=market.status()
    if not status.get('ready'):raise HTTPException(409,'请先初始化行情，再确认上市身份')
    with connect() as db:head=_head(db);revision=read_revision(db)
    if status.get('snapshot_id')!=head['market_snapshot_id']:raise HTTPException(409,'行情实例正在切换，请刷新后重试')
    return market,market.reference(),head,revision


def confirm(payload):
    allowed={'instrument_ids','status','reason','evidence','observed_business_date','request_key'}
    if not isinstance(payload,dict) or set(payload)-allowed:raise HTTPException(422,'人工上市身份只接受股票、状态、说明、证据与请求标识；不接受上市日期')
    symbols=payload.get('instrument_ids');state=payload.get('status');reason=payload.get('reason');evidence=payload.get('evidence','');key=payload.get('request_key') if payload.get('request_key') is not None else new_id('listing-confirm')
    if not isinstance(symbols,list) or not 1<=len(symbols)<=MAX_CONFIRM or any(not isinstance(symbol,str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)',symbol) for symbol in symbols):raise HTTPException(422,'请选择1至128只完整代码的A股')
    if state not in ('pending','unknown') or not isinstance(reason,str) or not 1<=len(reason.strip())<=1000 or not isinstance(evidence,str) or len(evidence.strip())>4000:raise HTTPException(422,'状态须为pending/unknown，说明1至1000字符，证据最多4000字符')
    if not isinstance(key,str) or not key.strip() or len(key)>200:raise HTTPException(422,'请求标识须为1至200字符')
    observed=payload.get('observed_business_date')
    if observed is not None and not valid_date(observed):raise HTTPException(422,'确认观察日期须为规范业务日期')
    symbols=sorted(set(symbols));request_hash=digest([symbols,state,reason.strip(),evidence.strip(),observed])
    with connect() as db:
        prior=db.execute('SELECT confirmation_id,request_hash FROM listing_status_confirmation WHERE request_key=?',(key,)).fetchall()
    if prior:
        if any(row['request_hash']!=request_hash for row in prior):raise HTTPException(409,'该请求标识已用于不同的人工确认')
        return {**read_view(),'confirmation_ids':[row['confirmation_id'] for row in prior],'already_exists':True}
    market,reference,head,revision=_context();day=head['business_date']
    if observed is not None and observed!=day:raise HTTPException(409,'人工确认只能记录当前业务日，请刷新后确认')
    stocks=reference.get('master',{})
    if any(symbol not in stocks or stocks[symbol].get('kind')!='stock' for symbol in symbols):raise HTTPException(422,'只能确认当前证券名录中的A股')
    candidates=sorted(set(symbols)|{symbol for symbol,row in stocks.items() if row.get('kind')=='stock' and (not valid_date(row.get('list_date')) or row['list_date']>day)})
    presence=quote_presence(market,candidates,day)
    if state=='pending':
        for symbol in symbols:
            if valid_date(stocks[symbol].get('list_date')) and stocks[symbol]['list_date']<=day:raise HTTPException(409,symbol+' 来源已提供上市日期，不能标记为待上市')
            if symbol not in presence:raise HTTPException(409,symbol+' 本代无报价证据尚未核验')
            if presence[symbol]['valid_quote_count']>0 or presence[symbol].get('observed_quote_rows',0)>0:raise HTTPException(409,symbol+' 已存在报价记录，不能标记为待上市')
            if presence[symbol].get('absence_verified',True) is not True or presence[symbol].get('ambiguous_quote_keys',0)>0:raise HTTPException(409,symbol+' 无报价证据未完整核验，不能标记为待上市')
    identifiers=[];already_exists=False
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT confirmation_id,request_hash FROM listing_status_confirmation WHERE request_key=?',(key,)).fetchall()
        if prior:
            if any(row['request_hash']!=request_hash for row in prior):raise HTTPException(409,'该请求标识已用于不同的人工确认')
            identifiers=[row['confirmation_id'] for row in prior];already_exists=True
        else:
            if _head(db)!=head or read_revision(db)!=revision:raise HTTPException(409,'行情或人工身份已变化，请刷新后重新确认')
            for symbol in symbols:
                identifier=new_id('listing-confirmation');identifiers.append(identifier)
                db.execute('INSERT INTO listing_status_confirmation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (identifier,symbol,state,'manual_user_confirmation',day,reason.strip(),evidence.strip(),head['primary_snapshot_id'],head['market_snapshot_id'],dumps(presence.get(symbol)),key,request_hash,now_ms()))
            snapshot_summary(reference,day,presence,head['market_snapshot_id'],connection=db)
            pin(db,head['primary_snapshot_id'],'manual','listing-confirmation:'+identifiers[0])
    return {**read_view(),'confirmation_ids':identifiers,'already_exists':already_exists}


def enqueue(connection, request_key, origin='manual-sync'):
    head=_head(connection);revision=read_revision(connection)
    prior=connection.execute('SELECT job_id,parameters_json FROM job WHERE request_key=?',(request_key,)).fetchone()
    if prior:
        if json.loads(prior['parameters_json']).get('operation')!=OPERATION:raise HTTPException(409,'身份核验请求标识冲突')
        return prior['job_id']
    parameters={'operation':OPERATION,'version':VERSION,'origin':origin,**head,'manual_revision':revision}
    logical=digest(parameters)
    active=connection.execute("SELECT job_id FROM job WHERE logical_key=? AND status IN ('queued','running','paused')",(logical,)).fetchone()
    if active:return active[0]
    identifier=new_id('job')
    connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,planned_batches,created_at_ms) VALUES(?,?,?,'source_check',?,?,?,'queued',100,?)",
                       (identifier,request_key,logical,head['business_date'],dumps(parameters),digest(parameters),now_ms()))
    tasks.append_event(connection,identifier,'queued','上市身份核验已排队；仅聚合本地报价，不联网')
    return identifier


def execute_check(job):
    parameters=json.loads(job['parameters_json'])
    def guard(db):
        row=db.execute('SELECT status,lease_owner FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
        if not row or row['status']!='running' or row['lease_owner']!=job['lease_owner']:raise tasks.TaskCancelled()
        if _head(db)!={key:parameters[key] for key in ('primary_snapshot_id','market_snapshot_id','business_date')} or read_revision(db)!=parameters['manual_revision']:raise HTTPException(409,'行情或人工身份已变化，此旧核验不提交')
    try:
        with connect() as db:guard(db)
        market,reference,head,revision=_context();day=parameters['business_date']
        candidates=[symbol for symbol,row in reference.get('master',{}).items() if row.get('kind')=='stock' and (not valid_date(row.get('list_date')) or row['list_date']>day)]
        presence=quote_presence(market,candidates,day)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');guard(db)
            summary=snapshot_summary(reference,day,presence,head['market_snapshot_id'],connection=db,expected_revision=revision)
        tasks.finish(job['job_id'],'succeeded','上市身份核验完成：已上市 '+str(summary['listed_count'])+'，待上市 '+str(summary['pending_listing_count'])+'，待核验 '+str(summary['unknown_count']),lease_owner=job['lease_owner'])
    except HTTPException as error:
        tasks.finish(job['job_id'],'no_change' if error.status_code==409 else 'failed',str(error.detail),lease_owner=job['lease_owner'])


def after_sync(job):
    parameters=json.loads(job['parameters_json'])
    if job['kind'] not in ('initialise','increment') or parameters.get('listing_status_check_version')!=VERSION:return None
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        saved=db.execute('SELECT * FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
        if not saved or saved['status'] not in ('succeeded','partial','no_change') or db.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='listing_status_check_queued'",(job['job_id'],)).fetchone():return None
        from .sync_progress import chain_stop_action
        if chain_stop_action(db,saved):return None
        publication=db.execute("SELECT data_end FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' ORDER BY published_at_ms DESC LIMIT 1",(job['job_id'],)).fetchone()
        frozen=db.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='chain_target_frozen' ORDER BY seq LIMIT 1",(job['job_id'],)).fetchone()
        target=publication[0] if publication else saved['target_date'] or (json.loads(frozen[0]).get('target_date') if frozen else None)
        if target is None:return None
        identifier=enqueue(db,'listing-after-sync:'+job['job_id']) if _head(db)['business_date']==target else None
        tasks.append_event(db,job['job_id'],'listing_status_check_queued','已接续本地上市身份核验' if identifier else '旧同步业务日已被新发布取代，不追溯排队',details={'next_task_id':identifier,'target_date':target})
        return identifier


def recover_sync_followups():
    with connect() as db:
        rows=[dict(row) for row in db.execute("SELECT * FROM job j WHERE kind IN ('initialise','increment') AND status IN ('succeeded','partial','no_change') AND json_extract(parameters_json,'$.listing_status_check_version')=? AND NOT EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='listing_status_check_queued') ORDER BY created_at_ms LIMIT 20",(VERSION,))]
    for job in rows:
        try:after_sync(job)
        except Exception as error:
            logging.getLogger(__name__).warning('上市核验补漏未完成 %s %s；保留原任务供下次核对',job['job_id'],type(error).__name__)
