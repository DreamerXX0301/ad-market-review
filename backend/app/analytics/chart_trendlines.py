"""Chart-only sloped price references from confirmed local pivots."""
from itertools import combinations
import math

from .support_resistance import WING, _consecutive, _day, _number, _valid_bar, confirmed_pivots


VERSION = 'confirmed-pivot-trendlines-v1'
LOOKBACK = 120
NEAR_LOOKBACK = 35
EVENT_LOOKBACK = 8
ZONE_SPREAD = 0.015
NEAR_DISTANCE = 0.15
PRICE_TOLERANCE = 1e-9


def _current_segment(bars, period, sessions, *, stock, basis):
    """Do not carry anchors through missing quotes or incomparable price bases."""
    closed_field, coverage_field = {
        'W': ('week_closed', 'weekly_quote_coverage_complete'),
        'M': ('month_closed', 'monthly_quote_coverage_complete'),
    }.get(period, (None, None))
    if bars and closed_field and bars[-1].get(closed_field) is False:
        bars = bars[:-1]
    raw_stock = basis == 'none' and stock
    segment = []
    for bar in bars:
        if (not _valid_bar(bar, period) or bar['date'] not in sessions
                or bar.get('price_basis', basis) != basis
                or (closed_field and bar.get(closed_field) is False)
                or (coverage_field and bar.get(coverage_field) is False)
                or (stock and not _number(bar.get('volume')))
                or (raw_stock and bar.get('corporate_action_coverage_known') is not True)):
            segment = []
            continue
        if raw_stock and bar.get('corporate_action'):
            segment = []
            if period != 'D':
                continue  # An aggregate action candle can mix two price bases.
        if segment and not _consecutive(segment[-1], bar, period, sessions):
            segment = []
        segment.append(bar)
    return segment


def _active_line(kind, anchors, bars, positions, *, tolerance=PRICE_TOLERANCE):
    last = len(bars) - 1
    field, direction = ('low', 1) if kind == 'support' else ('high', -1)
    candidates = []
    for left, right in combinations(anchors, 2):
        start, next_anchor = positions[left['date']], positions[right['date']]
        if math.isclose(left['price'], right['price'], rel_tol=PRICE_TOLERANCE):
            continue
        slope = (right['price'] - left['price']) / (next_anchor - start)
        end_price = left['price'] + slope * (last - start)
        if not math.isfinite(end_price) or end_price <= 0:
            continue
        if any(
            direction * (bar[field] - (left['price'] + slope * offset)) < -tolerance * bar[field]
            or direction * (bar['close'] - (left['price'] + slope * offset)) < -PRICE_TOLERANCE * bar['close']
            for offset, bar in enumerate(bars[start:])
        ):
            continue
        candidates.append((abs(end_price - bars[-1]['close']), -next_anchor, -start, {
            'line_type': kind, 'start_date': left['date'], 'start_price': left['price'],
            'end_date': bars[-1]['date'], 'end_price': end_price, 'slope_per_bar': slope,
            'anchors': [left, right], 'confirmed_on': right['confirmed_on'],
        }))
    return min(candidates, key=lambda item: item[:3])[3] if candidates else None


def _line_groups(bars, pivots, positions):
    main, near = [], []
    close = bars[-1]['close']
    cutoff = positions[bars[max(0, len(bars) - NEAR_LOOKBACK)]['date']]
    for kind in ('support', 'resistance'):
        anchors = [p for p in pivots if p['line_type'] == kind]
        broad = _active_line(kind, anchors, bars, positions)
        local = _active_line(
            kind, [p for p in anchors if positions[p['date']] >= cutoff],
            bars, positions, tolerance=0.0075,
        )
        if broad:
            main.append(broad)
        if (local and abs(local['end_price'] - close) <= close * NEAR_DISTANCE
                and not (broad
                         and abs(local['end_price'] - broad['end_price']) <= close * 0.002
                         and abs(local['slope_per_bar'] - broad['slope_per_bar']) <= close * 0.0005)):
            near.append(local)
    return main, near


def _observation_zones(bars, pivots, positions):
    close, end_date = bars[-1]['close'], bars[-1]['date']
    recent = sorted(
        (p for p in pivots if positions[p['date']] >= len(bars) - 60),
        key=lambda p: p['price'],
    )
    nearest = {}
    for index, pivot in enumerate(recent):
        low = pivot['price']
        group = [p for p in recent[index:] if p['price'] <= low * (1 + ZONE_SPREAD)]
        dates = sorted({p['date'] for p in group})
        if len(dates) < 2:
            continue
        high = group[-1]['price']
        if high == low:
            low, high = low * 0.999, high * 1.001
        midpoint = (low + high) / 2
        kind = 'support' if close >= midpoint else 'resistance'
        rank = (abs(close - midpoint), -len(dates), dates[0])
        if kind not in nearest or rank < nearest[kind][0]:
            nearest[kind] = (rank, {
                'line_type': kind, 'lower_price': low, 'upper_price': high,
                'start_date': dates[0], 'end_date': end_date, 'touches': len(dates),
            })
    return [entry[1] for entry in nearest.values()]


def _crossed(line, price, level):
    return (price < level * (1 - PRICE_TOLERANCE) if line['line_type'] == 'support'
            else price > level * (1 + PRICE_TOLERANCE))


def _recent_crossings(bars, pivots, positions, adjustment_date=None):
    # Each bar sees only the candidates already confirmed by the preceding bar.
    events = {}
    for index in range(max(5, len(bars) - EVENT_LOOKBACK), len(bars)):
        before, current = bars[index - 1], bars[index]
        if adjustment_date and before['date'] < adjustment_date:
            continue  # The current forward basis incorporates later action data.
        start = max(0, index - LOOKBACK)
        known = [p for p in pivots if p['confirmed_on'] <= before['date']
                 and positions[p['date']] >= start + WING]
        history = bars[start:index]
        history_positions = {bar['date']: offset for offset, bar in enumerate(history)}
        for scope, lines in zip(('main', 'near'), _line_groups(history, known, history_positions)):
            for line in lines:
                field = 'low' if line['line_type'] == 'support' else 'high'
                level = line['end_price'] + line['slope_per_bar']
                if level <= 0 or not _crossed(line, current[field], level):
                    continue
                state = 'close_cross' if _crossed(line, current['close'], level) else 'wick_cross'
                before_field = 'close' if state == 'close_cross' else field
                if _crossed(line, before[before_field], line['end_price']):
                    continue
                key = (line['line_type'], tuple(p['date'] for p in line['anchors']))
                if key in events and not (
                    events[key][1]['state'] == 'wick_cross' and state == 'close_cross'
                ):
                    continue
                events[key] = (index, {
                    'line_type': line['line_type'], 'scope': scope, 'state': state,
                    'date': current['date'], 'price': level,
                    'line': {**line, 'end_date': current['date'], 'end_price': level},
                })
    output = []
    for index, event in events.values():
        line = event['line']
        if event['state'] == 'close_cross' and index + 1 < len(bars):
            next_level = event['price'] + line['slope_per_bar']
            if next_level > 0 and _crossed(line, bars[index + 1]['close'], next_level):
                event.update(state='confirmed_cross', confirmed_on=bars[index + 1]['date'])
        output.append(event)
    return sorted(output, key=lambda event: event['date'], reverse=True)[:4]


def automatic_trendlines(chart, calendar, *, expected_snapshot=None):
    """Return bounded references and recent events through the latest usable bar."""
    period, as_of, basis = (chart.get(key) for key in ('period', 'as_of', 'price_basis'))
    instrument = chart.get('instrument', {})
    snapshot = chart.get('snapshot_id')
    result = {'algorithm': VERSION, 'pivot_wing': WING, 'lookback': LOOKBACK,
              'instrument_id': instrument.get('instrument_id'), 'snapshot_id': snapshot,
              'as_of': as_of, 'period': period, 'price_basis': basis,
              'items': [], 'near_items': [], 'zones': [], 'events': [], 'reason': None}
    if (instrument.get('kind') not in ('stock', 'index') or period not in ('D', 'W', 'M')
            or not _day(as_of) or not snapshot or (expected_snapshot and snapshot != expected_snapshot)):
        return {**result, 'reason': '证券、观察日、周期或已发布行情代际未能核验'}
    adjustment = chart.get('adjustment', {})
    if (basis not in ('none', 'forward_event_affine') or (basis == 'forward_event_affine'
            and (adjustment.get('usable') is not True or adjustment.get('anchor') != as_of))):
        return {**result, 'reason': '图表价格口径未能核验'}
    bars = (chart.get('bars') or [])[-(LOOKBACK + EVENT_LOOKBACK):]
    dates = [bar.get('date') for bar in bars]
    if (any(not _day(day) or day > as_of for day in dates) or dates != sorted(set(dates))
            or not calendar or list(calendar) != sorted(set(calendar)) or as_of not in calendar):
        return {**result, 'reason': '报价或交易日历存在缺失、重复、乱序或晚于观察日'}
    sessions = {day: index for index, day in enumerate(calendar)}
    stock = instrument['kind'] == 'stock'
    segment = _current_segment(bars, period, sessions, stock=stock, basis=basis)
    if len(segment) < WING * 2 + 2:
        return {**result, 'reason': '最新连续样本中完整可比K线不足'}
    positions = {bar['date']: index for index, bar in enumerate(segment)}
    pivots = list(confirmed_pivots(segment, period, sessions))
    current = segment[-LOOKBACK:]
    current_positions = {bar['date']: index for index, bar in enumerate(current)}
    current_pivots = [p for p in pivots if current_positions.get(p['date'], -1) >= WING]
    result['items'], result['near_items'] = _line_groups(current, current_pivots, current_positions)
    result['zones'] = _observation_zones(segment, pivots, positions)
    adjustment_date = max(
        (event['date'] for event in adjustment.get('events', []) if _day(event.get('date'))),
        default=None,
    ) if basis == 'forward_event_affine' else None
    result['events'] = _recent_crossings(segment, pivots, positions, adjustment_date)
    if not (result['items'] or result['near_items'] or result['zones']):
        result['reason'] = '最新连续样本内没有两点确认且未被后续报价穿越的斜线'
    return result
