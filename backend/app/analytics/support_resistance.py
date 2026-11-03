"""Bounded, retrospective price levels, never a prediction or trading signal.

A pivot needs two completed bars on *both* sides. Its confirmation date is the
last right-hand bar, so every input and confirmation is known at observation.
Incomplete bars and quote gaps break the local window instead of being skipped.
"""
from __future__ import annotations

from datetime import date, timedelta
import math

VERSION = 'confirmed-pivot-cluster-v1'
WING = 2
CLUSTER_PCT = 0.8
PER_SIDE = 3


def _number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_bar(bar, period):
    return (all(_number(bar.get(key)) for key in ('open', 'high', 'low', 'close'))
            and bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']
            and (period == 'D' or bar.get('complete') is True))


def _consecutive(left, right, period, sessions):
    if period == 'D':
        return left['date'] in sessions and sessions.get(right['date']) == sessions[left['date']] + 1
    before, after = date.fromisoformat(left['date']), date.fromisoformat(right['date'])
    if period == 'W':
        return (after - timedelta(days=after.weekday())) - (before - timedelta(days=before.weekday())) == timedelta(days=7)
    return (after.year * 12 + after.month) - (before.year * 12 + before.month) == 1


def confirmed_pivots(bars, period, sessions):
    """Emit strict extrema after both right-hand bars are complete."""
    for index in range(WING, len(bars) - WING):
        window = bars[index-WING:index+WING+1]
        if not all(_valid_bar(bar, period) for bar in window):
            continue
        if any(not _consecutive(left, right, period, sessions) for left, right in zip(window, window[1:])):
            continue
        center = bars[index]
        neighbors = window[:WING] + window[WING+1:]
        for kind, field, comparison in (('support', 'low', min), ('resistance', 'high', max)):
            value = center[field]
            if value != comparison(bar[field] for bar in window) or any(value == bar[field] for bar in neighbors):
                continue
            yield {'line_type': kind, 'price': value, 'date': center['date'], 'confirmed_on': window[-1]['date']}


def local_levels(chart, calendar, lookback=120):
    """Return at most six levels from this already-published chart only."""
    period, as_of = chart.get('period'), chart.get('as_of')
    if period not in ('D', 'W', 'M') or not _day(as_of):
        raise ValueError('支撑压力候选需要明确周期和观察日')
    supplied = list(chart.get('bars') or [])
    dates = [row.get('date') for row in supplied]
    if not all(_day(day) and day <= as_of for day in dates) or dates != sorted(set(dates)):
        raise ValueError('候选行情存在无效、重复或晚于观察日的报价')
    bars = supplied[-lookback:]
    sessions = {day: index for index, day in enumerate(calendar)}
    # Unadjusted prices before an action must not be clustered with prices after it.
    # A weekly/monthly action candle itself can mix two price bases.
    reset = 0
    if chart.get('price_basis') == 'none':
        for index, bar in enumerate(bars):
            if bar.get('corporate_action'):
                reset = index + (0 if period == 'D' else 1)
    bars = bars[reset:]
    completed = [bar for bar in bars if _valid_bar(bar, period)]
    latest = completed[-1] if completed else None
    reference = chart.get('reference_quote')
    if isinstance(reference, dict) and _day(reference.get('date')) and reference['date'] <= as_of and _number(reference.get('close')):
        # W/M structure is confirmed only by full periods. Its current-side
        # classification can still use the latest already-published daily close.
        latest = reference
    result = {'items': [], 'reference_price': latest['close'] if latest else None,
              'reference_date': latest['date'] if latest else None,
              'window_start': bars[0]['date'] if bars else None,
              'window_end': bars[-1]['date'] if bars else None,
              'observed_bars': len(bars), 'complete_bars': len(completed),
              'algorithm': VERSION, 'pivot_wing': WING, 'cluster_pct': CLUSTER_PCT,
              'reason': None}
    if chart.get('instrument_kind') == 'stock' and chart.get('price_basis') == 'none' and any(bar.get('corporate_action_coverage_known') is False for bar in bars):
        return {**result, 'reason': '公司行动来源覆盖未知，不能将未复权历史价位合并为可比候选'}
    if not latest or len(completed) < WING * 2 + 1:
        return {**result, 'reason': '完整可比报价不足，暂不能形成已确认支撑压力候选'}
    pivots = [pivot for pivot in confirmed_pivots(bars, period, sessions)
              if (pivot['price'] <= latest['close'] if pivot['line_type'] == 'support' else pivot['price'] >= latest['close'])]
    levels = []
    for kind in ('support', 'resistance'):
        clusters = []
        for pivot in sorted((p for p in pivots if p['line_type'] == kind), key=lambda p: p['price']):
            # Keep the entire cluster within tolerance, not an unbounded chain.
            cluster = next((group for group in clusters if (pivot['price'] / group[0]['price'] - 1) * 100 <= CLUSTER_PCT), None)
            if cluster is None:
                clusters.append([pivot])
            else:
                cluster.append(pivot)
        candidates = []
        for group in clusters:
            price = round(sum(p['price'] for p in group) / len(group), 4)
            if price <= 0:
                continue
            candidates.append({'line_type': kind, 'price': price,
                               'distance_pct': (price / latest['close'] - 1) * 100,
                               'touches': len(group), 'last_pivot_date': max(p['date'] for p in group),
                               'confirmed_on': max(p['confirmed_on'] for p in group),
                               'evidence': group})
        candidates.sort(key=lambda row: (abs(row['distance_pct']), -row['touches'], -date.fromisoformat(row['last_pivot_date']).toordinal()))
        levels.extend(candidates[:PER_SIDE])
    return {**result, 'items': levels,
            'reason': None if levels else '窗口内未形成位于当前完整收盘两侧的已确认分型；可增加回看范围或手动划线'}
