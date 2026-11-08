"""Platform HTTP endpoints; all data and job behavior lives in services."""
from fastapi import APIRouter,Body,HTTPException,Query
from fastapi.responses import JSONResponse
from app.core import APP_NAME,APP_ID,json_clean,connect
from app.services import platform_service,market_service,task_service,source_service,research_service,workspace_automation
from app.adapters.public_fund_budget import FUND_REQUEST_BUDGET_VERSION

router=APIRouter(prefix='/api/v1',tags=['platform'])

@router.get('/data/quote-gap-source')
def quote_gap_source_view():
    from app.services.quote_gap_source_check import read_view
    return json_clean(read_view())

@router.post('/data/quote-gap-source')
def quote_gap_source_check(payload:dict=Body(...)):
    from app.services.quote_gap_source_check import create_check
    return json_clean(create_check(payload))

@router.get('/health')
def health(): return {'status':'ok','application':APP_ID,'app_name':APP_NAME}

@router.get('/bootstrap')
def bootstrap(scope:str=Query('full',pattern='^(full|navigation)$')):
    return JSONResponse(json_clean(platform_service.bootstrap(scope=scope)))

@router.get('/overview')
def overview(date:str|None=None):return JSONResponse(market_service.overview(date))

@router.get('/market/topic-members')
def market_topic_members(date:str|None=None,topic_id:str|None=Query(None,max_length=200),source_code:str|None=Query(None,max_length=200),kind:str|None=Query(None,pattern='^(industry|concept)$'),source_name:str|None=Query(None,max_length=200)):
    return market_service.market_topic_members(date,topic_id=topic_id,source_code=source_code,kind=kind,source_name=source_name)

@router.get('/market/sector-chart')
def sector_chart(topic_code:str=Query(...,max_length=30),date:str|None=None,period:str=Query('D',pattern='^(D|W|M)$'),encoding:str=Query('rows',pattern='^(rows|columns-v1)$')):
    from app.analytics.chart_trendlines import automatic_trendlines
    from app.services.sector_chart_jobs import chart_view
    from app.services.chart_transport import chart_payload
    market,status,day=market_service.context(date)
    result=chart_view(market,topic_code,day,period)
    if result['available']:
        result['automatic_trendlines'] = automatic_trendlines(
            result, market.calendar(), expected_snapshot=status.get('snapshot_id'))
    return JSONResponse(chart_payload(result,encoding))

@router.post('/market/sector-chart/jobs')
def sector_chart_create(payload:dict=Body(...)):
    from app.services.sector_chart_jobs import create_chart_job
    if set(payload)-{'topic_code','date','request_key'}:raise HTTPException(422,'板块K线只接受板块代码、日期与请求标识')
    if not isinstance(payload.get('topic_code'),str):raise HTTPException(422,'请选择明确的板块代码')
    if payload.get('date') is not None and not isinstance(payload['date'],str):raise HTTPException(422,'板块K线日期须为YYYY-MM-DD')
    if payload.get('request_key') is not None and (not isinstance(payload['request_key'],str) or not 1<=len(payload['request_key'])<=200):raise HTTPException(422,'请求标识须为1至200字符')
    market,_,day=market_service.context(payload.get('date'))
    return json_clean(create_chart_job(market,payload.get('topic_code'),day,payload.get('request_key')))

@router.get('/market/indices')
def index_overview(date:str|None=None):
    return JSONResponse(market_service.index_overview(date))

@router.get('/market/{kind}')
def market_page(kind:str,date:str|None=None,limit:int=Query(100,ge=1,le=1000),query:str='',page:int=Query(1,ge=1),page_size:int|None=Query(None,ge=1,le=200),sort_by:str|None=None,sort_order:str=Query('desc',pattern='^(asc|desc)$'),top_n:int|None=Query(None,ge=100,le=1000),monitor_scope:str=Query('historical',pattern='^(historical|latest)$'),rank_state:str=Query('all',pattern='^(all|unranked)$'),summary_only:bool=False):
    if rank_state!='all' and kind!='gainers':raise HTTPException(422,'未排名范围仅适用于近30日涨幅榜')
    if monitor_scope!='historical' and kind!='abnormal':raise HTTPException(422,'最新监控名单范围仅适用于监管页')
    if summary_only and kind!='emotion':raise HTTPException(422,'首屏摘要范围仅适用于市场情绪')
    # Services already return JSON-native read models. Avoid FastAPI recursively
    # copying every bar/evidence field again before JSON encoding large charts.
    return JSONResponse(market_service.overview(date) if kind=='overview' else market_service.market_page(kind,date,limit,query=query,page=page,page_size=page_size,sort_by=sort_by,sort_order=sort_order,top_n=top_n,monitor_scope=monitor_scope,rank_state=rank_state,include_latest_classification_reference=True,**({'summary_only':True} if summary_only else {})))

@router.get('/instruments')
def instruments(query:str='',limit:int=Query(50,ge=1,le=10000),fuzzy:bool=False):
    if fuzzy:
        from app.services.unified_search import search
        result = search(query, limit, include_sectors=False)
        return {'items': result['items'], 'total': result['total']}
    from app.core import get_market
    items=get_market().instruments(query,limit)
    return {'items':items,'total':len(items)}

@router.get('/search')
def unified_search(query:str='',limit:int=Query(20,ge=1,le=100),stocks_only:bool=False):
    from app.services.unified_search import search
    return json_clean(search(query,limit,stocks_only=stocks_only))

@router.get('/topic-hotspots')
def topic_hotspots(limit:int=Query(8,ge=1,le=20)):
    from app.services.hot_topics import list_hot_topics
    return json_clean(list_hot_topics(limit=limit))

@router.get('/instruments/{instrument_id}/chart')
def chart(instrument_id:str,period:str='D',as_of:str|None=None,price_basis:str='none',indicator_continuity:str=Query('quotes',pattern='^(quotes|market_sessions)$'),encoding:str=Query('rows',pattern='^(rows|columns-v1)$')):
    from app.analytics.market import canonical
    from app.analytics.chart_trendlines import automatic_trendlines
    from app.services.chart_limits import enrich_chart_limits
    from app.services.chart_read_cache import instrument_projection
    from app.services.chart_transport import chart_payload
    market,status,day=market_service.context(as_of)

    def build():
        result=market.chart(instrument_id,period,as_of,price_basis,indicator_continuity=indicator_continuity)
        if not result['bars']:raise HTTPException(404,'该标的没有已留存K线')
        expected_snapshot = market.status().get('snapshot_id') if status.get('snapshot_id') else None
        result['automatic_trendlines'] = automatic_trendlines(result, market.calendar(), expected_snapshot=expected_snapshot)
        return result

    variant = ('chart', canonical(instrument_id), period.upper(), price_basis, indicator_continuity)
    result = instrument_projection(market, status, day, variant, build)
    return JSONResponse(chart_payload(enrich_chart_limits(market,result),encoding))

@router.get('/instruments/{instrument_id}/summary')
def instrument_summary(instrument_id:str,as_of:str|None=None):
    from app.services.stock_summary import get_summary
    return JSONResponse(json_clean(get_summary(instrument_id,as_of)))

@router.get('/instruments/{instrument_id}/tracking-baselines')
def instrument_tracking_baselines(instrument_id:str,as_of:str|None=None):
    from app.services.chart_tracking import tracking_baselines
    return json_clean(tracking_baselines(instrument_id,as_of))

@router.get('/instruments/{instrument_id}')
def instrument_detail(instrument_id:str,as_of:str|None=None):
    from app.services.chart_read_cache import instrument_projection
    from app.services.stock_memberships import stock_membership_fields
    from app.services.stock_chart_patterns import stock_pattern_fields
    market,status,day=market_service.context(as_of)
    symbol=market.instrument(instrument_id)['instrument_id']
    detail_chart=getattr(market,'detail_chart',None)
    def build():
        return detail_chart(symbol,as_of=day) if callable(detail_chart) else market.chart(symbol,as_of=day)
    chart=instrument_projection(market,status,day,('detail',symbol),build)
    row=chart['bars'][-1] if chart['bars'] else {}
    patterns=stock_pattern_fields(market,chart,day)
    current=day==status['data_end']
    row.update(instrument_id=symbol,topics=[{'topic_id':t['topic_id'],'name':t['name']} for t in market.topics_data() if current and symbol in t['instrument_ids']],turnover_ratio_pct=None)
    identity=market.instrument(symbol)
    memberships=stock_membership_fields(market,symbol) if identity.get('kind',identity.get('asset_type','stock')) in ('stock','equity','a_share','A') else {}
    return JSONResponse(json_clean({**identity,**market_service.stock(row,market.reference() if current else None),**memberships,**patterns,'instrument':identity,'data_date':day,'snapshot_id':status['snapshot_id'],'warnings':status.get('warnings',[])+([] if current else ['历史题材与监管标签未留存；当前归属单独列示，不使用当前分类回填历史'])}))

@router.get('/data/status')
def data_status():return JSONResponse(json_clean(platform_service.data_status()))

@router.get('/data/coverage/check')
def coverage_check():
    from app.services.local_coverage_check import read_view
    return json_clean(read_view(full=True))

@router.post('/data/coverage/check')
def coverage_check_create(payload:dict=Body(...)):
    from app.services.local_coverage_check import create_check
    return json_clean(create_check(payload))

@router.get('/data/listing-status')
def listing_status():
    from app.services.listing_status import read_view
    return json_clean(read_view())

@router.post('/data/listing-status/confirm')
def listing_status_confirm(payload:dict=Body(...)):
    from app.services.listing_status import confirm
    return json_clean(confirm(payload))

@router.get('/data/listing-status/confirmations')
def listing_status_confirmations(instrument_id:str|None=None,limit:int=Query(100,ge=1,le=1000)):
    from app.services.listing_status import confirmation_history
    return json_clean(confirmation_history(instrument_id,limit))

@router.get('/sources')
def sources():return source_service.list_sources()

@router.get('/strategies/{strategy_id}/automation')
def automation_get(strategy_id:str):return workspace_automation.get_settings(strategy_id)

@router.put('/strategies/{strategy_id}/automation')
def automation_set(strategy_id:str,payload:dict=Body(...)):return workspace_automation.configure(strategy_id,payload)

@router.post('/sources/{source_id}/configure')
def configure(source_id:str,payload:dict=Body(...)):return source_service.configure(source_id,payload)

@router.post('/sources/{source_id}/verify')
def verify(source_id:str):return source_service.verify(source_id)

@router.get('/tasks')
def tasks(limit:int=Query(50,ge=1,le=200),summary:bool=False):return task_service.list_tasks(limit,summary=summary)

@router.get('/tasks/activity')
def task_activity():return task_service.task_activity()

@router.get('/sync/session')
def sync_session(task_id:str|None=Query(None,max_length=200),request_key:str|None=Query(None,max_length=200)):
    from app.services.sync_progress import session
    return json_clean(session(task_id=task_id,request_key=request_key))

@router.post('/sync/session/acknowledge')
def sync_session_acknowledge(payload:dict=Body(...)):
    from app.services.sync_progress import acknowledge
    return json_clean(acknowledge(request_key=payload.get('request_key'),task_id=payload.get('task_id'),epoch=payload.get('session_epoch')))

@router.post('/sync/session/control')
def sync_session_control(payload:dict=Body(...)):
    raise HTTPException(409, '盘后增量不提供暂停或取消，请等待本次任务结束后确认结果')

@router.get('/tasks/{task_id}')
def task(task_id:str,after_seq:int=Query(0,ge=0)):
    result=task_service.task_view(task_id,after_seq)
    if result['kind']=='research' and result['status'] in ('succeeded','partial'):
        try:result['report']=research_service.get_report(task_id,touch=False)
        except HTTPException as error:
            if error.status_code!=404:raise
            result['report_available']=False
    return result

@router.post('/tasks/{task_id}/{action}')
def task_action(task_id:str,action:str):
    if action not in ('pause','resume','cancel','retry'):raise HTTPException(404,'任务操作不存在')
    from app.services.collection_controls import require_interactive_action
    with connect() as connection:
        row = connection.execute('SELECT * FROM job WHERE job_id=?', (task_id,)).fetchone()
        if row is not None:
            require_interactive_action(connection, row, action)
    return task_service.change_task(task_id,action)

@router.post('/sync/tasks')
def sync(payload:dict=Body(...)):
    from app.services.sync_requests import create_sync_request
    return create_sync_request(payload)

@router.post('/sync/sector-charts')
def sector_charts_sync(payload:dict=Body(default={})):
    from app.core import get_market
    from app.services.sector_chart_batch_jobs import create_batch_job
    if set(payload)-{'request_key'}:raise HTTPException(422,'全量板块K线只接受请求标识；日期与目录按当前已发布行情冻结')
    key=payload.get('request_key')
    if key is not None and (not isinstance(key,str) or not 1<=len(key)<=200):raise HTTPException(422,'请求标识须为1至200字符')
    market=get_market();status=market.status()
    if not status.get('ready'):raise HTTPException(409,'请先完成主行情初始化，再补齐板块历史K线')
    try:return json_clean(create_batch_job(market,status['data_end'],request_key=key))
    except ValueError as error:raise HTTPException(422,str(error)) from error

@router.get('/research/jobs')
def research_list():return research_service.list_reports()

@router.post('/sync/topics')
def topic_sync(payload:dict=Body(default={})):
    from app.core import get_market
    from app.services.topic_catalog_additions import SCOPE
    scope = payload.get('scope', 'current-full-catalog')
    if scope not in ('current-full-catalog', SCOPE):
        raise HTTPException(422, '题材同步范围不支持')
    adapter=source_service.client('fuyao')
    if not adapter.key:raise HTTPException(422,'请先配置扶摇免费Key')
    if scope == SCOPE and not get_market().status().get('ready'):
        raise HTTPException(409, '请先完成行情初始化，再同步新增题材')
    return task_service.create_task('topic_refresh',{'source_id':'fuyao','scope':scope},request_key=payload.get('request_key'))

@router.post('/sync/market-supplement')
def market_supplement(payload:dict=Body(default={})):
    from app.core import get_market
    status=get_market().status()
    if not status.get('ready'):raise HTTPException(422,'请先初始化行情，再补充盘面事件')
    revalidate_only=payload.get('revalidate_only',False)
    if not isinstance(revalidate_only,bool):raise HTTPException(422,'revalidate_only须为布尔')
    repair={}
    if payload.get('coverage_plan_id') is not None:
        from app.services.local_coverage_check import repair_parameters
        repair=repair_parameters(payload['coverage_plan_id'])
        if revalidate_only or any(payload.get(key)!=repair[key] for key in ('year_history','sector_history','recent_days')):
            raise HTTPException(422,'盘面修补范围与已审阅核验计划不一致')
    if revalidate_only:
        from app.services.sync_completion import replay_supplement_request
        replay=replay_supplement_request(payload.get('request_key'),{'revalidate_only':True})
        if replay:return replay
        return task_service.create_task('source_check',{'operation':'market_supplement','revalidate_only':True,'year_history':True,'sector_history':False,'recent_days':1,'source_id':'local-cache'},target_date=status['data_end'],request_key=payload.get('request_key'))
    year_history=payload.get('year_history',True)
    recent_days=payload.get('recent_days',1)
    sector_history=payload.get('sector_history',True)
    if not isinstance(year_history,bool) or not isinstance(sector_history,bool) or isinstance(recent_days,bool) or not isinstance(recent_days,int) or not 1<=recent_days<=30:raise HTTPException(422,'year_history和sector_history须为布尔，recent_days须为1至30整数')
    from app.services.sync_completion import replay_supplement_request
    replay=replay_supplement_request(payload.get('request_key'),
        {'year_history':year_history,'sector_history':sector_history,'recent_days':recent_days,'coverage_plan_id':payload.get('coverage_plan_id')})
    if replay:return replay
    if not repair:
        from app.services.sync_completion import require_published_supplement_available
        require_published_supplement_available(status.get('snapshot_id'), status['data_end'],
            {'year_history':year_history,'sector_history':sector_history,'recent_days':recent_days})
    if (year_history or sector_history) and not source_service.client('fuyao').key:raise HTTPException(422,'年内来源池及板块日行情补充需要已配置的扶摇免费Key')
    from app.services.retention_policy import VERSION as retention_version
    from app.services.supplement_policy import FUND_FLOW_RETIRED
    from app.adapters.dragon_tiger import VERSION as dragon_tiger_version
    return task_service.create_task('source_check',{'operation':'market_supplement','year_history':year_history,'sector_history':sector_history,'recent_days':recent_days,'source_id':'fuyao' if year_history or sector_history else 'akshare-public','fund_request_budget_version':FUND_REQUEST_BUDGET_VERSION,'fund_flow_policy':FUND_FLOW_RETIRED,'retention_policy_version':retention_version,'market_snapshot_id':status.get('snapshot_id'),'completion_target_date':status['data_end'],'dragon_tiger_policy':dragon_tiger_version,'dragon_tiger_backfill':False,**repair},target_date=status['data_end'],request_key=payload.get('request_key'))

@router.post('/research/jobs')
def research_create(payload:dict=Body(...)):return research_service.create(payload)

@router.get('/research/jobs/{identifier}')
def research_get(identifier:str):return research_service.get_report(identifier)
