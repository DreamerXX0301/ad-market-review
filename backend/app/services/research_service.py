"""Explicit single-stock research with factual materials and an isolated TTL."""
import json
import re
from fastapi import HTTPException
from app.core import business_date,connect,dumps,get_market,json_clean,new_id,now_ms
from app.services import task_service as tasks
from app.services.source_service import client
from app.services.research_cache import TTL_MS,list_reports,get_report,cleanup_expired
from app.services.research_materials import collect_materials
from app.services.research_trends import build_trends
from app.services.research_evidence import freeze_plan,build_evidence_report


def create(payload):
    symbol=payload.get('instrument_id')
    if not isinstance(symbol,str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)',symbol.upper()):raise HTTPException(422,'请先选择完整个股标的')
    market=get_market();symbol=symbol.upper();instrument=market.instrument(symbol)
    if instrument.get('kind')!='stock' or not market.bars(symbol):raise HTTPException(422,'该个股尚无已发布行情')
    status=market.status()
    day=business_date()
    if not day:raise HTTPException(422,'请先完成行情初始化并建立业务日期')
    parameters={'instrument_id':symbol,'market_snapshot_id':status.get('snapshot_id'),
                'research_plan':freeze_plan(symbol,status.get('snapshot_id'),day)}
    return tasks.create_task('research',parameters,day,payload.get('request_key'))


def execute_research(job_id,progress=None):
    with connect() as connection:
        old=connection.execute('SELECT report_id,expires_at_ms FROM research_report WHERE job_id=?',(job_id,)).fetchone()
        job=connection.execute('SELECT * FROM job WHERE job_id=?',(job_id,)).fetchone()
        refs={row['module']:row['snapshot_id'] for row in connection.execute("SELECT module,snapshot_id FROM published_head WHERE module IN ('calendar','daily_raw','analysis_bars','corporate_actions','topics','security_identity')")}
    if old and old['expires_at_ms']>now_ms():return get_report(old['report_id'],touch=False)
    if not job or job['kind']!='research':raise ValueError('指定研究任务不存在')
    if old:raise ValueError('研究记录暂不可用，请手动创建新研究任务')
    if job['status']=='cancelled':raise tasks.TaskCancelled()
    if job['status']=='paused':raise tasks.TaskPaused()
    parameters=json.loads(job['parameters_json']);symbol=parameters['instrument_id'];market=get_market();status=market.status()
    snapshot=status.get('snapshot_id');requested=parameters.get('market_snapshot_id')
    if requested and requested!=snapshot:raise ValueError('研究任务的行情代际已变化，请手动重新运行')
    day=business_date();cutoff=job['target_date'] or day
    if cutoff!=day:raise ValueError('研究任务业务日已变化，请手动重新运行')
    plan=parameters.get('research_plan')
    enhanced=plan is not None
    if enhanced and plan not in (freeze_plan(symbol, requested, cutoff),
                                 freeze_plan(symbol, requested, cutoff, market_cap_fallback=False)):
        raise ValueError('研究计划版本、来源或预算未通过冻结校验，请手动创建新研究任务')
    def checkpoint():
        with connect() as connection:
            row=connection.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()
        if not row or row['status']=='cancelled':raise tasks.TaskCancelled()
        if row['status']=='paused':raise tasks.TaskPaused()
    if progress:progress('行情与趋势',10,'从固定本地OHLC生成日、周、月观察')
    instrument=market.instrument(symbol);trends=build_trends(market,symbol,cutoff)
    if not trends:raise ValueError('研究标的缺少已发布行情')
    adapter=None;adapter_reason=None
    try:
        configured=client('fuyao')
        if configured.key:adapter=configured
    except ValueError as error:adapter_reason=str(error)
    source_data=collect_materials(symbol,adapter,progress=progress,enhanced=enhanced,plan=plan,checkpoint=checkpoint)
    if adapter_reason:source_data['gaps'].append({'capability':'fuyao','source':'fuyao','title':'扶摇研究材料','state':'unavailable','reason':adapter_reason})
    topics=[topic['name'] for topic in market.topics_data() if symbol in topic.get('instrument_ids',[])]
    extension={}
    if enhanced:
        if progress:progress('证据与趋势评估',85,'关联实际材料与可解释观察；不生成交易建议或调用模型')
        checkpoint()
        extension=build_evidence_report(symbol,instrument,source_data,plan)
        from app.services.research_technical import build_research_technical
        extension['technical_assessment']=build_research_technical(market,symbol,cutoff,price_basis=plan['price_basis'],benchmark=plan['benchmark'])
        for item in extension['source_coverage']:
            if item['source_id'] in ('institution_views','media_news'):
                source_data['gaps'].append({'capability':item['source_id'],'title':item['title'],'source':item['source_id'],'state':item['state'],'reason':item['reason']})
    created=now_ms();report_id=new_id('research')
    warnings=[gap['title']+'：'+gap['reason'] for gap in source_data['gaps']]
    warnings.extend(source_data['source_notes'])
    result=json_clean({'report_id':report_id,'job_id':job_id,'status':'partial' if source_data['gaps'] else 'completed','instrument':instrument,'data_cutoff':trends[0]['data_end'],'research_as_of_ms':source_data['material_window']['research_as_of_ms'],'created_at_ms':created,'trends':trends,'topics':topics,'sections':[{'title':'题材归属','content':'、'.join(topics) if topics else '尚无已留存成员归属，可在题材库补充人工研究。'}],**source_data,**extension,'warnings':warnings,'snapshot_refs':refs,'cache':{'ttl_days':7,'last_accessed_at_ms':created,'expires_at_ms':created+TTL_MS}})
    if progress:progress('留存研究',95,'保存材料、单位、来源与缺口；不会执行交易')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        state=connection.execute('SELECT status FROM job WHERE job_id=?',(job_id,)).fetchone()
        if not state or state[0]=='cancelled':raise tasks.TaskCancelled()
        if state[0]=='paused':raise tasks.TaskPaused()
        if business_date()!=day or get_market().status().get('snapshot_id')!=snapshot:raise ValueError('行情业务日或代际变化，未发布混合研究材料，请重新运行')
        connection.execute('INSERT INTO research_report(report_id,job_id,instrument_id,data_cutoff,created_at_ms,report_json,snapshot_refs_json,last_accessed_at_ms,expires_at_ms) VALUES(?,?,?,?,?,?,?,?,?)',(report_id,job_id,symbol,result['data_cutoff'],created,dumps(result),dumps(refs),created,created+TTL_MS))
        for reference in set(refs.values()):
            connection.execute('INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms,expires_at_ms) VALUES(?,?,?,?,?,?) ON CONFLICT(snapshot_id,owner_kind,owner_id) DO NOTHING',(new_id('pin'),reference,'research',report_id,created,created+TTL_MS))
        job_status='partial' if source_data['gaps'] else 'succeeded'
        connection.execute("UPDATE job SET status=?,phase='completed',accepted_batches=100,finished_at_ms=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?",(job_status,created,job_id))
        tasks.append_event(connection,job_id,'completed','指定个股研究已留存；未取得材料保留缺口',
                           details={'gap_titles':list(dict.fromkeys(gap['title'] for gap in source_data['gaps']))[:6]})
    return result
