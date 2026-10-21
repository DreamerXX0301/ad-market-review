"""Bounded local history preparation. No HTTP, tasks or business mutations."""
from datetime import date

from app.domain.history_events import HistoryContext, read_start, retain_start, nodes, warmup

BATCH_SIZE = 256


def batch_size(trees):
    return 32 if any(warmup(event['where']) is None for tree in trees for event in nodes(tree)) else BATCH_SIZE


def prepare_shared(market, symbols, trees, calendar, day):
    """One vectorized preparation per selection run, never once per batch."""
    from app.domain.conditions import names
    method = getattr(market, 'history_condition_metrics', None)
    if not callable(method) or not symbols: return None
    fields = set().union(*(names(event['where']) for tree in trees for event in nodes(tree)))
    return method(symbols, start=retain_start(trees, calendar, day), end=day, fields=fields)


def contexts_for_batch(market, symbols, trees, calendar, day, histories=None, shared=None):
    if len(symbols) > batch_size(trees): raise ValueError('历史事件准备每批超出有界证券数量')
    if not symbols: return {}
    if shared is not None:
        return {symbol: HistoryContext.from_prepared(shared, symbol) for symbol in symbols}
    start = read_start(trees, calendar, day)
    method = getattr(market, 'history_for_instruments', None)
    records = ({symbol: [row for row in histories.get(symbol, []) if start <= str(row.get('date', '')) <= day] for symbol in symbols}
               if histories is not None else method(symbols, start=start, end=day) if callable(method) else {})
    action_method = getattr(market, 'actions_for_instruments', None)
    missing_flags = any('corporate_action_coverage_known' not in row or 'corporate_action' not in row for values in records.values() for row in values)
    actions = action_method(symbols) if missing_flags and callable(action_method) else None
    status_method = getattr(market, 'status', None)
    snapshot = status_method().get('snapshot_id') if callable(status_method) else None
    result = {}
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
        result[symbol] = HistoryContext(values, calendar, day, snapshot, retain_start=retain_start(trees, calendar, day))
    return result
