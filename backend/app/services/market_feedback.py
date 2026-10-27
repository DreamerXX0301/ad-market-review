"""One dated feedback payload: multi-board detail, first-board summary only."""
import math


def feedback_view(market, status, reference, day, indexed):
    from app.services.market_service import prior_ladder_feedback, dated_events
    from app.services.stock_board_presentation import decorate_board_collections
    reader = getattr(market, 'calendar', None)
    calendar = (reader() or []) if callable(reader) else []
    position = calendar.index(day) if day in calendar else 0
    detail = prior_ladder_feedback(market, reference, day, indexed) if day in calendar else []
    previous = calendar[position - 1] if position else None
    events = dated_events(reference, previous) if previous else {}
    candidates = {}
    source_unknown = set()
    counts = {}
    for event in events.get('limitup', []):
        symbol = event.get('thscode') or event.get('instrument_id')
        if not isinstance(symbol, str):
            continue
        values = [event[key] for key in ('continue_day_cnt', 'board_count', 'board_num') if event.get(key) is not None]
        if (not values or any(isinstance(value, bool) or not isinstance(value, (int, float))
                             or not math.isfinite(value) or value < 1 or value != int(value) for value in values)):
            source_unknown.add(symbol)
            continue
        counts.setdefault(symbol, set()).update(values)
    for symbol, values in counts.items():
        if values == {1} and symbol not in source_unknown:
            row = indexed.get(symbol, {})
            candidates[symbol] = {**row, 'instrument_id': symbol, 'date': day, 'snapshot_id': status['snapshot_id'],
                                  'previous_board_count': 1, 'previous_board_date': previous}
        elif 1 in values:
            source_unknown.add(symbol)
    decorated = decorate_board_collections(market, status, day, reference, {'first': list(candidates.values())})['first'] if candidates else []
    first = []
    for row in decorated:
        presentation = row['board_presentation']
        height = presentation.get('previous_height', {})
        if height.get('state') != 'confirmed' or height.get('value') != 1:
            source_unknown.add(row['instrument_id'])
            continue
        change = row.get('change_pct')
        first.append({'instrument_id': row['instrument_id'], 'date': day, 'snapshot_id': status['snapshot_id'],
                      'previous_board_count': 1, 'previous_board_date': previous,
                      'change_pct': change if isinstance(change, (int, float)) and not isinstance(change, bool) and math.isfinite(change) else None,
                      'board_presentation': {key: presentation[key] for key in ('version', 'date', 'snapshot_id', 'instrument_id', 'previous_height')}})
    available = 'limitup' in events
    coverage = {'previous_date': previous, 'source_available': available, 'confirmed_count': len(first),
                'unverified_count': len(source_unknown), 'state': 'ready' if available and not source_unknown else 'partial' if first else 'unknown',
                'scope': '昨日涨停池中明确标记且通过收盘证据核验的首板；仅加入概览，不加入连板明细。来源缺失或高度未核验不按零补齐。'}
    return {'feedback': detail, 'first_board_feedback': first, 'first_board_feedback_coverage': coverage}
