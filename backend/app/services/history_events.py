"""Bounded local history preparation. No HTTP, tasks or business mutations."""
from datetime import date

from app.domain.history_events import HistoryContext, read_start, retain_start, nodes, preparation_warmup, prepared_fields

BATCH_SIZE = 256


def cached_forward_contexts(symbols,trees,calendar,day,snapshot,source_token):
    """Probe only after the run has verified its immutable source closure."""
    if source_token is None:return {}
    from app.domain.strategy_prices import price_cache_key
    from app.services.forward_history_cache import window_scope,key_for,restore,shared_window_scope,restore_shared,remember
    scope=window_scope(trees,calendar,day);positions={value:index for index,value in enumerate(calendar)}
    wider=shared_window_scope(calendar,day,scope)
    result={}
    for symbol in symbols:
        key=key_for(symbol,day,snapshot,price_cache_key(symbol,day,'forward',source_token),scope)
        context=restore(key,calendar,positions)
        if context is None and wider is not None:
            shared_key=key_for(symbol,day,snapshot,price_cache_key(symbol,day,'forward',source_token),wider)
            context=restore_shared(shared_key,scope,calendar,positions)
            if context is not None:remember(key,context,scope)
        if context is not None:result[symbol]=context
    return result


def batch_size(trees):
    return 32 if any(preparation_warmup(event) is None for tree in trees for event in nodes(tree)) else BATCH_SIZE


def prepare_shared(market, symbols, trees, calendar, day,price_basis='none'):
    """One vectorized preparation per selection run, never once per batch."""
    if price_basis=='forward':return None  # Raw vector matrices cannot carry an adjusted price lane.
    method = getattr(market, 'history_condition_metrics', None)
    if not callable(method) or not symbols: return None
    fields = prepared_fields(trees)
    # Closing-pool events are verified by the local source adapter, never by an
    # unverified flag carried on a price panel. Keep the vectorized price path.
    prepared = method(symbols, start=retain_start(trees, calendar, day), end=day, fields=fields - {'limit_up'})
    if prepared is not None: prepared['fields'] |= fields
    return prepared


def attach_limit_events(context, symbol, trees, evidence):
    """Fill only retained exact dates; no source I/O or price history reads."""
    from app.domain.conditions import names
    if context is not None and evidence is not None and any('limit_up' in names(event['where']) for tree in trees for event in nodes(tree)):
        for day, metrics in context.daily.items():
            metrics.update(evidence.historical_metrics(symbol, day, context.raw_quotes.get(day)))
    return context


def contexts_for_batch(market, symbols, trees, calendar, day, histories=None, shared=None, limit_evidence=None,price_basis='none',price_contexts=None,prepared_contexts=None):
    if len(symbols) > batch_size(trees): raise ValueError('历史事件准备每批超出有界证券数量')
    if not symbols: return {}
    if 'limit_up' in prepared_fields(trees) and limit_evidence is None:
        from app.services.strategy_limits import LimitConditionEvidence
        limit_evidence = LimitConditionEvidence(market)
    if shared is not None and price_basis!='forward':
        return {symbol: attach_limit_events(HistoryContext.from_prepared(shared, symbol), symbol, trees, limit_evidence) for symbol in symbols}
    result={}
    if price_basis=='forward':
        for symbol in symbols:
            context=(prepared_contexts or {}).get(symbol)
            if context is not None:result[symbol]=attach_limit_events(context,symbol,trees,limit_evidence)
        symbols=[symbol for symbol in symbols if symbol not in result]
        if not symbols:return result
    start = read_start(trees, calendar, day)
    method = getattr(market, 'history_for_instruments', None)
    records = ({symbol: [row for row in histories.get(symbol, []) if (price_basis=='forward' or start <= str(row.get('date', ''))) and str(row.get('date',''))<=day] for symbol in symbols}
               if histories is not None else method(symbols, start=None if price_basis=='forward' else start, end=day) if callable(method) else {})
    action_method = getattr(market, 'actions_for_instruments', None)
    missing_flags = any('corporate_action_coverage_known' not in row or 'corporate_action' not in row for values in records.values() for row in values)
    actions = action_method(symbols) if missing_flags and callable(action_method) else None
    status_method = getattr(market, 'status', None)
    snapshot = status_method().get('snapshot_id') if callable(status_method) else None
    cache_scope=None;shared_scope=None
    if price_basis=='forward':
        from app.services.forward_history_cache import window_scope,shared_window_scope
        cache_scope=window_scope(trees,calendar,day);shared_scope=shared_window_scope(calendar,day,cache_scope)
    for symbol in symbols:
        values = []
        known = isinstance(actions, dict) and isinstance(actions.get(symbol), list)
        action_dates = set()
        for action in actions.get(symbol, []) if known else []:
            try:
                if action.get('thscode', symbol).upper() != symbol: raise ValueError('action identity')
                raw = action.get('ex_date', action.get('date'))
                if raw is not None: observed = date.fromisoformat(raw).isoformat()
                else:
                    from app.analytics.market import date_ms
                    stamp = action.get('ex_date_ms')
                    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)): raise ValueError('action date')
                    observed = date_ms(stamp)
                if observed <= day: action_dates.add(observed)
            except (ValueError, TypeError, AttributeError, OverflowError, OSError): known = False
        for source in records.get(symbol, []):
            row = dict(source)
            if 'corporate_action_coverage_known' not in row or 'corporate_action' not in row:
                row.update(corporate_action_coverage_known=known, corporate_action=row.get('date') in action_dates)
            values.append(row)
        price_context=(price_contexts or {}).get(symbol)
        if price_basis=='forward' and price_context is None:
            from app.domain.strategy_prices import condition_prices_for
            price_context=condition_prices_for(market,symbol,values,calendar,day,price_basis)
        context=HistoryContext(values, calendar, day, snapshot,
            retain_start=shared_scope['first'] if shared_scope else retain_start(trees, calendar, day),price_context=price_context)
        if price_basis=='forward':
            from app.services.forward_history_cache import key_for,remember,remember_shared,project_context
            if shared_scope is not None and context.price_basis=='forward_event_affine':
                shared_key=key_for(symbol,day,snapshot,(price_context or {}).get('cache_key'),shared_scope)
                remember_shared(shared_key,context,shared_scope)
                context=project_context(context,cache_scope,calendar,{value:index for index,value in enumerate(calendar)})
            key=key_for(symbol,day,snapshot,(price_context or {}).get('cache_key'),cache_scope)
            remember(key,context,cache_scope)
        result[symbol] = attach_limit_events(context,symbol,trees,limit_evidence)
    return result
