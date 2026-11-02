"""Small read models for application bootstrap and published data coverage."""
import json
import os
from app.core import APP_NAME,APP_VERSION, business_date, connect, get_market, published_dir

MODULES = {
    'calendar': ('交易日历','实际完成的市场交易日'),
    'security_identity': ('证券名录','最新名称与上市日期；历史退市身份尚未完整'),
    'daily_raw': ('个股日K','真实未复权OHLCV；重复键与价格范围校验'),
    'corporate_actions': ('公司行动','现金与送转事件；不冒充已完成复权验收'),
    'analysis_bars': ('分析行情','基于固定原始快照按需聚合日、周、月K'),
    'indicators': ('技术指标','统一MA、MACD、KDJ、量能计算；预热不足为空'),
    'topics': ('题材成员','采集时点的真实成员，支持人工研究覆盖'),
    'market_review': ('盘面事件','已留存涨跌停、炸板、梯队与监控标签'),
}


def summary(market=None):
    from .published_storage import published_storage
    status=(market or get_market()).status()
    accounting=published_storage(published_dir(),status.get('snapshot_id'))
    return {'stock_count':status.get('symbols',0),'row_count':status.get('rows',0),'first_date':status.get('data_begin'),'last_date':status.get('data_end'),'storage_bytes':accounting['logical_bytes'],'storage_accounting':accounting,'storage_layout':status.get('storage_layout','legacy-single-file'),'retention':status.get('active_window'),'last_storage_write':status.get('storage_write')}


def bootstrap():
    from .source_service import list_sources
    market=get_market(); status=market.status()
    quotes=(market.index_quotes() if hasattr(market,'index_quotes') else market.indices()) if status.get('ready') else []
    return {'app_name':APP_NAME,'version':APP_VERSION,'instance_label':os.environ.get('SHIXU_INSTANCE_LABEL','')[:120],'data_date':business_date(),'market_snapshot_id':status.get('snapshot_id'),'market_dates':market.calendar(),'data_summary':summary(market),'indices':quotes,'sources':list_sources(include_supplement=False)['items']}


def data_status():
    from .sector_chart_batch_jobs import batch_status
    from .task_service import initialization_state, task_view
    from .sync_clock import sync_window
    from .sync_completion import read_view as sync_completion_view
    from .history_window import initialization_window_status
    market=get_market(); status=market.status();datasets=[]
    from .data_coverage import dataset_progress, snapshot_quality_view
    reference=market.reference() if callable(getattr(market,'reference',None)) else {}
    # A head switch during the read must not combine different generations.
    if reference and market.status().get('snapshot_id')!=status.get('snapshot_id'):reference={}
    with connect() as c:
        initialization=initialization_state(c)
        rows=c.execute("SELECT s.*,v.observed_rows,v.unexplained_missing,v.expected_rows FROM published_head h JOIN dataset_snapshot s ON h.snapshot_id=s.snapshot_id LEFT JOIN snapshot_coverage v ON v.snapshot_id=s.snapshot_id AND v.feature=s.module ORDER BY s.module").fetchall()
        latest=c.execute("SELECT job_id FROM job WHERE kind IN ('initialise','increment','topic_init','topic_refresh','source_check') ORDER BY created_at_ms DESC LIMIT 1").fetchone()
    raw=next((json.loads(r['lineage_json']).get('coverage',{}) for r in rows if r['module']=='daily_raw'),{})
    from .local_coverage_check import read_view
    audit=read_view(market)
    local_report=audit.get('report') if audit.get('snapshot_id')==status.get('snapshot_id') else None
    for r in rows:
        if r['module'] not in MODULES: continue
        name,description=MODULES[r['module']]
        coverage=json.loads(r['lineage_json']).get('coverage',{});identity_unknown=coverage.get('identity_unknown_count',0)
        reason=(f"{identity_unknown}只证券上市日期未知，总缺口待核验；已确认缺口至少{r['unexplained_missing']}条" if identity_unknown and r['module']=='daily_raw' else f"{identity_unknown}只已收录证券的上市日期未知，历史身份完整度待核验" if identity_unknown and r['module']=='security_identity' else f"{r['unexplained_missing']}条预期记录待解释" if r['unexplained_missing'] else None)
        progress=dataset_progress(dict(r),reference,status.get('snapshot_id'),raw,local_report)
        datasets.append({'module':r['module'],'name':name,'description':description,'snapshot_id':r['snapshot_id'],'status':r['quality'],'row_count':r['observed_rows'],'first_date':r['data_begin'],'last_date':r['data_end'],'missing':None if identity_unknown else r['unexplained_missing'],'missing_confirmed_lower_bound':r['unexplained_missing'],'expected_rows':r['expected_rows'],'reason':reason,'coverage_progress':progress,**snapshot_quality_view(dict(r),progress)})
    from .source_service import list_sources
    sources=list_sources();current=next((s for s in sources['items'] if s['active']),None)
    sync_ready=bool(current and current['sync_supported'] and current['configured'] and current['enabled'])
    reason=None if sync_ready else '请先选择扶摇免费来源，配置Key并启用，再手动同步'
    window=initialization_window_status(market.reference() if status.get('ready') else None)
    from .automatic_tasks import enabled as automatic_enabled
    return {'business_date':business_date(),'initialization':initialization,'initialization_window':window,'sync_window':sync_window(),'sync_operations':sync_completion_view(status.get('snapshot_id'),status.get('data_end')),'summary':summary(market),'datasets':datasets,'coverage_check':audit,'latest_task':task_view(latest[0]) if latest else None,'public_supplement':sources['public_supplement'],'sector_charts':batch_status(market),'warnings':status.get('warnings',[]),'automatic_schedule':automatic_enabled(),'sync_configuration':{'ready':sync_ready,'source_id':current['source_id'] if current else None,'name':current['name'] if current else None,'tier':current['tier'] if current else None,'requires_key':current['requires_key'] if current else False,'configured':current['configured'] if current else False,'reason':reason}}
