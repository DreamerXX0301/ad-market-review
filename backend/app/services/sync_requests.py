"""Shared manual/explicitly enabled scheduled sync creation; never runs collection."""
from fastapi import HTTPException
from app.services import task_service,source_service
from app.adapters.public_fund_budget import FUND_REQUEST_BUDGET_VERSION


def create_sync_request(payload: dict):
    mode=payload.get('mode')
    if mode not in ('initialise','increment'):raise HTTPException(422,'请选择首次初始化或盘后增量')
    from app.core import connect
    from app.services.sync_progress import lock_record,require_unlocked,LOCK_VERSION
    with connect() as connection:
        locked=lock_record(connection)
        if locked:
            if mode=='increment' and payload.get('request_key')==locked['request_key']:
                return task_service.task_view(locked['root_task_id'])
            require_unlocked(connection)
    if mode=='increment':
        from app.services.sync_clock import require_new_increment
        try:require_new_increment()
        except ValueError as error:raise HTTPException(409,str(error)) from error
    adapter=source_service.client()
    if adapter.source_id!='fuyao':raise HTTPException(422,'当前全市场同步已适配扶摇；TickFlow可用于指数和个股核验，请切换扶摇后初始化')
    if not adapter.key:raise HTTPException(422,'请先在数据源配置扶摇免费Key并验证连接')
    from app.services.history_window import resolve_initial_window
    window=resolve_initial_window(payload,for_new_task=True)['window'] if mode=='initialise' else None
    from app.services.retention_policy import VERSION as retention_version
    from app.adapters.providers import SECTOR_TAIL_PACING_VERSION
    from app.services.supplement_policy import FUND_FLOW_RETIRED
    parameters={'source_id':adapter.source_id,'line_alert_check_version':'explicit-local-line-alert-v1','listing_status_check_version':'listing-evidence-v1','fund_request_budget_version':FUND_REQUEST_BUDGET_VERSION,'retention_policy_version':retention_version,**({'window':window,'data_chain_version':'sector-charts-v1'} if window else {'data_chain_version':'sector-charts-increment-v1','increment_session_version':LOCK_VERSION,'sector_tail_request_policy_version':SECTOR_TAIL_PACING_VERSION})}
    parameters['fund_flow_policy']=FUND_FLOW_RETIRED
    from app.adapters.dragon_tiger import VERSION as dragon_tiger_version
    parameters.update(dragon_tiger_policy=dragon_tiger_version,dragon_tiger_backfill=mode=='initialise')
    if mode=='increment':
        from app.services.sync_workspace_followup import MARKER, VERSION as workspace_followup_version
        parameters[MARKER]=workspace_followup_version
        from app.services.sync_clock import safe_completion_date
        parameters['sync_completion_date']=safe_completion_date()
        from app.services.line_alert_retention import VERSION as line_retention_version
        parameters['line_alert_retention_version']=line_retention_version
        parameters['noon_retention_policy']='noon-retention-28d-v1'
        parameters['auction_retention_policy']='auction-retention-28d-v1'
        from app.services.auction_retention import DISCOVERY_POLICY
        parameters['auction_discovery_retention_policy']=DISCOVERY_POLICY
        parameters['noon_close_policy']='noon-close-v1'
    return task_service.create_task(mode,parameters,request_key=payload.get('request_key'))
