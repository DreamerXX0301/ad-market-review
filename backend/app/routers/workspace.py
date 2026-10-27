"""HTTP adapters for independent research. Domain rules never depend on market view date."""
from fastapi import APIRouter,Body,Query
from fastapi.responses import JSONResponse
from app.services import topics,strategies,tracking,topic_members as membership_service
from app.services import sector_strategies,sector_tracking,strategy_batches
from app.domain.conditions import specs
from app.services import workspace_deletion, workspace_purge

router=APIRouter(prefix='/api/v1',tags=['workspace'])

@router.get('/topic-members')
def topic_members_query(topic_ids:list[str]=Query(...,min_length=1,max_length=20),match:str='any',query:str='',sort_by:str='change_pct',sort_order:str='desc',boards:list[str]|None=Query(None,max_length=4)):
    return membership_service.list_members(topic_ids,match,query,sort_by,sort_order,boards=boards)

@router.post('/topic-members/associate')
def topic_members_associate(payload:dict=Body(...)):
    return membership_service.associate(payload)

@router.get('/strategy-fields')
def strategy_fields():
    fields=specs()
    return {'items':fields,'total':len(fields),'unknown_policy':'数据缺失/预热不足/复权不可核对为unknown，不触发提醒和结束；数值条件支持right_field'}

@router.get('/hot-universe')
def hot_browse(as_of:str,window_sessions:int=Query(7,ge=1,le=60),top_n:int=Query(10,ge=1,le=50),kind:str=Query('all',pattern='^(all|concept|industry)$'),snapshot_id:str|None=Query(None,max_length=200),topic_revision_signature:str|None=Query(None,max_length=64)):
    from app.services.hot_browse import read_hot_range
    return read_hot_range(as_of,window_sessions=window_sessions,top_n=top_n,kind=kind,snapshot_id=snapshot_id,topic_revision_signature=topic_revision_signature)

@router.get('/topics')
def topics_list(q:str='',include_archived:bool=False,kind:str|None=None):return topics.list_topics(q,include_archived,kind)
@router.get('/stock-topic-memberships/{instrument_id}')
def stock_topic_memberships(instrument_id:str):
    from app.core import get_market
    from app.services.stock_memberships import stock_membership_fields
    from app.services.workspace_common import fail,identities,current
    from app.core import connect
    symbol=instrument_id.upper()
    with connect() as connection:day=current(connection)
    if symbol not in identities(day):fail('股票不在当前已导入证券范围内',404)
    return {'instrument_id':symbol,**stock_membership_fields(get_market(),symbol)}
@router.post('/topics')
def topics_create(payload:dict=Body(...)):return topics.create_topic(payload)
@router.post('/topics/initialize')
def topics_initialize(payload:dict=Body(default={})):
    return {**topics.seed_topics(),**topics.list_topics()}
@router.get('/topics/{topic_id}')
def topic_get(topic_id:str):return topics.get_topic(topic_id)
@router.patch('/topics/{topic_id}')
def topic_update(topic_id:str,payload:dict=Body(...)):return topics.update_topic(topic_id,payload)
@router.post('/topics/{topic_id}/members')
def topic_members(topic_id:str,payload:dict=Body(...)):return topics.add_member(topic_id,payload)
@router.get('/topics/{topic_id}/history')
def topic_history(topic_id:str):return topics.topic_history(topic_id)

@router.get('/strategies')
def strategy_list(deleted:bool=False,limit:int|None=Query(None,ge=1,le=100),offset:int=Query(0,ge=0)):return strategies.list_strategies(deleted,limit,offset)
@router.post('/strategy-batches')
def strategy_batch_create(payload:dict=Body(...)):return strategy_batches.create_batch(payload)
@router.get('/strategy-batches')
def strategy_batch_list(target_type:str='stock',limit:int=Query(10,ge=1,le=20)):return strategy_batches.list_batches(target_type,limit)
@router.get('/strategy-batches/{batch_id}')
def strategy_batch_get(batch_id:str):return strategy_batches.get_batch(batch_id)
@router.post('/strategies')
def strategy_create(payload:dict=Body(...)):return strategies.create_strategy(payload)
@router.post('/strategies/bootstrap')
def strategy_bootstrap(payload:dict=Body(default={})):
    return strategies.seed_workspace(payload.get('price_basis','none'))
@router.get('/strategies/{strategy_id}')
def strategy_get(strategy_id:str):return strategies.get_strategy(strategy_id)
@router.patch('/strategies/{strategy_id}')
def strategy_update(strategy_id:str,payload:dict=Body(...)):return strategies.update_strategy(strategy_id,payload)
@router.delete('/strategies/{strategy_id}')
def strategy_delete(strategy_id:str):return workspace_deletion.delete_strategy(strategy_id)
@router.post('/strategies/{strategy_id}/restore')
def strategy_restore(strategy_id:str):return workspace_deletion.restore_strategy(strategy_id)
@router.post('/strategies/{strategy_id}/runs')
def strategy_run(strategy_id:str,payload:dict=Body(default={}),response_decision:str|None=None):return strategies.run_strategy(strategy_id,payload,response_decision=response_decision)
@router.get('/strategy-runs')
def strategy_runs(strategy_id:str|None=None,limit:int=Query(50,ge=1,le=200)):return JSONResponse(strategies.list_runs(strategy_id,limit))
@router.get('/strategy-runs/{run_id}')
def strategy_run_get(run_id:str,decision:str|None=None):return JSONResponse(strategies.get_run(run_id,decision))

@router.get('/tracking-rounds')
def tracking_list(strategy_id:str|None=None,status:str='tracking',triggered_only:bool=False,view:str=Query('full',pattern='^(full|summary)$')):return JSONResponse(tracking.list_tracking(strategy_id,status,triggered_only,view))
@router.post('/tracking-rounds')
def tracking_add(payload:dict=Body(...)):return tracking.add_tracking(payload)
@router.post('/tracking-rounds/review')
def tracking_review(payload:dict=Body(default={})):return tracking.review_tracking(payload)
@router.get('/tracking-rounds/{round_id}')
def tracking_get(round_id:str):return JSONResponse(tracking.get_tracking(round_id))
@router.patch('/tracking-rounds/{round_id}/policy')
def tracking_policy(round_id:str,payload:dict=Body(...)):return tracking.change_tracking_policy(round_id,payload)
@router.post('/tracking-rounds/{round_id}/remove')
def tracking_remove(round_id:str,payload:dict=Body(default={})):return tracking.remove_tracking(round_id,payload)
@router.delete('/strategies/{strategy_id}/ignores/{instrument_id}')
def tracking_ignore_release(strategy_id:str,instrument_id:str):return tracking.release_ignore(strategy_id,instrument_id)
@router.get('/tracking-events')
def tracking_events(strategy_id:str|None=None,event_type:str|None=None,business_date:str|None=None,limit:int=Query(100,ge=1,le=1000)):return tracking.list_events(strategy_id,event_type,business_date,limit)

@router.get('/sector-strategy-fields')
def sector_strategy_fields():
    fields=sector_strategies.specs()
    return {'items':fields,'total':len(fields),'target_type':'sector',
            'unknown_policy':'仅使用已核验本地板块指数；未收完周期、缺数和预热不足保持待核验'}
@router.get('/sector-strategies')
def sector_strategy_list():return sector_strategies.list_strategies()
@router.post('/sector-strategies')
def sector_strategy_create(payload:dict=Body(...)):return sector_strategies.create_strategy(payload)
@router.get('/sector-strategies/{strategy_id}')
def sector_strategy_get(strategy_id:str):return sector_strategies.get_strategy(strategy_id)
@router.patch('/sector-strategies/{strategy_id}')
def sector_strategy_update(strategy_id:str,payload:dict=Body(...)):return sector_strategies.update_strategy(strategy_id,payload)
@router.post('/sector-strategies/{strategy_id}/runs')
def sector_strategy_run(strategy_id:str,payload:dict=Body(default={}),response_decision:str|None=None):return sector_strategies.run_strategy(strategy_id,payload,response_decision=response_decision)
@router.get('/sector-strategy-runs')
def sector_strategy_runs(strategy_id:str|None=None,limit:int=Query(50,ge=1,le=200)):return sector_strategies.list_runs(strategy_id,limit)
@router.get('/sector-strategy-runs/{run_id}')
def sector_strategy_run_get(run_id:str,decision:str|None=None):return sector_strategies.get_run(run_id,decision)
@router.get('/sector-tracking-rounds')
def sector_tracking_list(strategy_id:str|None=None,status:str='tracking',triggered_only:bool=False):return sector_tracking.list_tracking(strategy_id,status,triggered_only)
@router.post('/sector-tracking-rounds')
def sector_tracking_add(payload:dict=Body(...)):return sector_tracking.add_tracking(payload)
@router.post('/sector-tracking-rounds/review')
def sector_tracking_review(payload:dict=Body(default={})):return sector_tracking.review_tracking(payload)
@router.get('/sector-tracking-rounds/{round_id}')
def sector_tracking_get(round_id:str):return sector_tracking.get_tracking(round_id)
@router.patch('/sector-tracking-rounds/{round_id}/policy')
def sector_tracking_policy(round_id:str,payload:dict=Body(...)):return sector_tracking.change_tracking_policy(round_id,payload)
@router.post('/sector-tracking-rounds/{round_id}/remove')
def sector_tracking_remove(round_id:str,payload:dict=Body(default={})):return sector_tracking.remove_tracking(round_id,payload)
@router.get('/sector-tracking-rounds/{round_id}/members')
def sector_tracking_members(round_id:str,as_of:str|None=None,membership_scope:str='latest'):return sector_tracking.members(round_id,as_of,membership_scope)
@router.get('/sectors/{topic_code}/members')
def sector_members(topic_code:str,as_of:str|None=None,membership_scope:str='latest'):return sector_tracking.sector_members(topic_code,as_of,membership_scope)
@router.get('/sector-tracking-events')
def sector_tracking_events(strategy_id:str|None=None,event_type:str|None=None,business_date:str|None=None,limit:int=Query(100,ge=1,le=1000)):return sector_tracking.list_events(strategy_id,event_type,business_date,limit)


@router.get('/strategies/{strategy_id}/purge-preview')
def strategy_purge_preview(strategy_id:str):return workspace_purge.preview('strategy',strategy_id)
@router.post('/strategies/{strategy_id}/purge')
def strategy_purge(strategy_id:str,payload:dict=Body(...)):return workspace_purge.submit('strategy',strategy_id,payload)
@router.get('/workspace-purges')
def purge_list(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0),status:str|None=None,target_kind:str|None=None):return workspace_purge.list_purges(limit,offset,status,target_kind)
@router.get('/workspace-purges/{purge_id}')
def purge_get(purge_id:str):return workspace_purge.get_purge(purge_id)
@router.post('/workspace-purges/{purge_id}/retry')
def purge_retry(purge_id:str):return workspace_purge.retry(purge_id)
