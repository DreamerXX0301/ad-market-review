"""Pure bounded forward-selection preparation, suitable for a spawned CPU child.

The caller supplies already verified quotations, action evidence and calendar.
This module never reads sources, writes a cache, evaluates a rule, or opens a
business database. Returned histories retain only the requested technical tail.
"""
import time


def prepare_job(request):
    if request.get('kind')=='strict-structures':
        from app.services.strict_structure_cache import calculate,decode_record
        started=time.perf_counter()
        values={}
        for period,value in request['periods'].items():
            rows=decode_record(value['record']) if 'record' in value else value['rows']
            values[period]=(calculate(rows,period,include_higher_lows=value['include_higher_lows'],
                boll_configs=value['boll_configs']) if rows is not None else None)
        return {'identity':request['identity'],'values':values,'stages':{'structures':time.perf_counter()-started}}
    from app.domain.conditions import prepare_metric_bars, metrics_for, normalized_metric_rows
    from app.domain.history_events import HistoryContext
    from app.domain.strategy_prices import prepare_condition_prices
    from app.services import condition_basis_cache as basis
    started=time.perf_counter();stages={}
    symbol=request['symbol'];day=request['day'];calendar=request['calendar']
    rows=request['rows'];key=request['basis_key']
    context=prepare_condition_prices(rows,request['actions'],calendar,day,'forward',
        instrument_id=symbol,actions_coverage=request['coverage'])
    basis.mark_verified_unavailable(context,key)
    for lane in ('raw_rows','price_rows'):
        context[lane]=prepare_metric_bars(context[lane])
    stages['price_preparation']=time.perf_counter()-started
    history=None;tick=time.perf_counter()
    if request['history_first'] is not None:
        history=HistoryContext(rows,calendar,day,request['snapshot'],
            retain_start=request['history_first'],price_context=context)
    stages['history']=time.perf_counter()-tick;tick=time.perf_counter()
    from app.services import strict_structure_cache as strict
    normalized=normalized_metric_rows(context['price_rows'] if context['price_basis']=='forward_event_affine' else [],day)
    from app.services.strategy_periods import _summary_key
    metrics={};strict_records={}
    for period,options in request['requests'].items():
        prepared=strict.build(normalized,calendar,day,period)
        summary_key=_summary_key(day,period,symbol,request.get('generation',(request['snapshot'],key[4])),context)
        strict_records[period]=(strict.disk.encode_record(summary_key,'strict-input',{'rows':prepared},strict.VERSION)
            if prepared is not None else None)
        value=metrics_for(rows,day,calendar,period,price_context=context,
            include_higher_lows=options['include_higher_lows'] if prepared is None else False,
            boll_configs=options['boll_configs'] if prepared is None else ())
        if prepared is not None:
            higher,bands=strict.calculate(prepared,period,**options)
            if options['include_higher_lows']:value['higher_lows_state']=higher
            if options['boll_configs']:value['boll_states']=bands
        metrics[period]=value
    stages['metrics']=time.perf_counter()-tick;tick=time.perf_counter()
    record=None
    try:record=basis._encode(key,context)
    except (ValueError,TypeError,KeyError,OverflowError,AttributeError):pass
    stages['encoding']=time.perf_counter()-tick
    thin={field:context.get(field) for field in basis._CONTEXT_FIELDS}
    if context.get('_verified_preparation_source'):
        thin['_verified_preparation_source']=context['_verified_preparation_source']
    if history is not None:history.price_context=thin
    return {'identity':(symbol,day,key),'price_context':thin,'metrics':metrics,
            'history':history,'basis_record':record,'strict_records':strict_records,'stages':stages}


def prepare_cold(market,symbols,histories,actions,day,calendar,plan,periods,generation,source_token,keys,
                 *,snapshot,history_trees=None):
    """Return pure jobs only inside an explicit execution scope, never on read."""
    from app.services.selection_compute_pool import current_session, PoolUnavailable
    session=current_session()
    if session is None or getattr(session,'max_workers',4)==0 or len(symbols)<8 or source_token is None or not isinstance(actions,dict):return None
    expected=generation[0] if isinstance(generation,tuple) else generation
    if snapshot!=expected:return None
    from datetime import date
    try:
        if any(keys.get(symbol) is None or not isinstance(actions.get(symbol),list)
            or len(histories.get(symbol,[]))>16384
            or any(not isinstance(row.get('date'),str) or date.fromisoformat(row['date']).isoformat()!=row['date'] or row['date']>day
                   or 'corporate_action_coverage_known' not in row or not isinstance(row.get('corporate_action'),bool)
                   for row in histories.get(symbol,[])) for symbol in symbols):return None
    except (TypeError,ValueError):return None
    first=None
    if history_trees:
        from app.services.forward_history_cache import window_scope,shared_window_scope
        scope=window_scope(history_trees,calendar,day)
        first=(shared_window_scope(calendar,day,scope) or scope)['first']
    requests=[{'symbol':symbol,'day':day,'calendar':calendar,'rows':histories.get(symbol,[]),
        'actions':actions[symbol],'basis_key':keys[symbol],'snapshot':snapshot,'generation':generation,'history_first':first,
        'coverage':{'known':True,'source_token':source_token,'snapshot_id':snapshot,'reason':None},
        'requests':{period:{'include_higher_lows':plan.requests[period].include_higher_lows,
                            'boll_configs':plan.requests[period].boll_configs} for period in periods}}
        for symbol in symbols]
    try:prepared=session.map(prepare_job,requests)
    except PoolUnavailable as error:
        import logging
        logging.getLogger(__name__).warning('纯计算进程不可用，使用原有有界准备路径：%s',error)
        session.stats['fallbacks']=session.stats.get('fallbacks',0)+1
        session.stats['fallback_reason']=str(error)
        return None
    if len(prepared)!=len(symbols) or any(value.get('identity')!=(symbol,day,keys[symbol])
                                        for symbol,value in zip(symbols,prepared)):
        raise ValueError('并行技术输入身份不一致，拒绝混用来源')
    phases=session.stats.setdefault('preparation_stages',{})
    for value in prepared:
        for phase,elapsed in value['stages'].items():phases[phase]=phases.get(phase,0.0)+elapsed
    return dict(zip(symbols,prepared))
