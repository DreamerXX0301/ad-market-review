"""Bounded stock input reader for optional trendline conditions."""
from app.analytics import strategy_trendlines as trend
from app.domain.strategy_prices import prepare_condition_prices


def stock_batch(market, symbols, calendar, day, windows, requested, *,
                price_basis, source_token, generation, price_contexts):
    groups = [groups for groups in windows.values() if groups]
    if not groups:
        return {}
    start = min(items[0][1][0] for items in groups)
    ready = {symbol: context['price_rows'] for symbol in symbols
             if (context := price_contexts.get(symbol, {})).get('price_basis') == 'forward_event_affine'
             and context.get('price_rows') and price_basis == 'forward'}
    pending = [symbol for symbol in symbols if symbol not in ready]
    method = getattr(market, 'condition_histories', None)
    raw = (method(pending, start=start, end=day) if callable(method)
           else {symbol: market.bars(symbol, as_of=day) for symbol in pending}) if pending else {}
    actions = {}
    if pending and price_basis == 'forward':
        action_method = getattr(market, 'actions_for_instruments', None)
        if callable(action_method):
            actions = action_method(pending) or {}
    output = {}
    for symbol in symbols:
        if symbol in ready:
            rows = ready[symbol]
        else:
            rows = raw.get(symbol, [])
            if price_basis == 'forward':
                context = prepare_condition_prices(rows, actions.get(symbol), calendar, day, price_basis,
                    instrument_id=symbol, actions_coverage={
                        'known': source_token is not None and isinstance(actions.get(symbol), list),
                        'source_token': source_token, 'snapshot_id': generation})
                rows = context['price_rows']
        output[symbol] = trend.summaries(
            [row for row in rows if isinstance(row.get('date'), str) and start <= row['date'] <= day],
            windows, requested, basis='forward_event_affine' if price_basis == 'forward' else 'none')
    return output
