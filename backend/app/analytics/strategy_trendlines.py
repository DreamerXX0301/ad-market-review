"""Causal trendline features for stock and sector selection."""
from collections import defaultdict
from math import isfinite

from .chart_trendlines import LOOKBACK, _active_line, _observation_zones
from .strict_period_layout import period_layout
from .support_resistance import _consecutive, _number, _valid_bar, confirmed_pivots

VERSION = 'strategy-confirmed-trendlines-v1'
HORIZONTAL_VERSION = 'strategy-confirmed-horizontal-zones-v1'
MIN_HISTORY = 12
ZONE_FIELDS = {'horizontal_support_close_near': 'support',
               'horizontal_resistance_breakout': 'resistance'}
LABELS = {
    'trendline_pullback': '回踩上升支撑（容差%）',
    'trendline_breakout': '收盘突破压力（缓冲%）',
    'horizontal_support_close_near': '收盘靠近水平支撑区（容差%）',
    'horizontal_resistance_breakout': '收盘突破水平压力区（缓冲%）',
}
FIELDS = {field: {'type': 'number', 'operators': ['matches'], 'constant_only': True,
                  'selection_only': True, 'min': 0, 'max': 10, 'step': 0.1, 'default': 0.5}
          for field in LABELS}


def version_for(fields):
    return HORIZONTAL_VERSION if ZONE_FIELDS.keys() & set(fields) else VERSION


def validate_threshold(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or not 0 <= value <= 10):
        raise ValueError('价位容差或突破缓冲需要为0至10的百分数')
    return value


def requested_fields(tree, default):
    if tree.get('field') in FIELDS:
        return {tree.get('period', default): {tree['field']}}
    result = defaultdict(set)
    for child in tree.get('all', tree.get('any', [])):
        for period, fields in requested_fields(child, default).items():
            result[period].update(fields)
    return dict(result)


def windows_for(calendar, day, requested):
    return {period: period_layout(calendar, day, period).groups[-(LOOKBACK+1):]
            for period in requested}


def summaries(rows, windows, requested, *, basis='none'):
    by_day = defaultdict(list)
    for row in rows:
        by_day[row['date']].append(row)
    return {period: _summary(by_day, groups, period, requested[period], basis)
            for period, groups in windows.items()}


def _summary(by_day, groups, period, fields, basis):
    bars = []
    blocker = None
    for _, days, boundary_known in groups:
        quotes = [by_day[day][0] for day in days if len(by_day[day]) == 1]
        if (len(quotes) != len(days) or not boundary_known or any(
                not _valid_bar(row, 'D') or not _number(row.get('volume'))
                or row.get('corporate_action_coverage_known') is not True
                or row.get('corporate_action') is not False for row in quotes)):
            bars.clear()
            blocker = days[-1]
            continue
        if bars and period != 'D' and not _consecutive(bars[-1], {'date':days[-1]}, period, {}):
            blocker = bars[-1]['date']
            bars.clear()
        bars.append({'date': days[-1], 'open': quotes[0]['open'], 'close': quotes[-1]['close'],
                     'low': min(row['low'] for row in quotes), 'high': max(row['high'] for row in quotes),
                     'complete': True})

    result = {'version': version_for(fields), 'period': period, 'price_basis': basis,
              'observed_date': groups[-1][1][-1] if groups else None,
              'covered_periods': len(bars), 'required_periods': MIN_HISTORY+1,
              'last_blocked_period': blocker}
    if len(bars) <= MIN_HISTORY:
        return {**result, 'reason': '最近连续可比的完整K线不足，缺报价、周期边界或公司行动可能中断样本'}
    history, signal = bars[:-1], bars[-1]
    positions = {row['date']: i for i, row in enumerate(history)}
    pivots = list(confirmed_pivots(history, period, positions))
    lines = {}
    for field in fields - ZONE_FIELDS.keys():
        kind = 'support' if field == 'trendline_pullback' else 'resistance'
        anchors = [p for p in pivots if p['line_type'] == kind]
        line = _active_line(kind, anchors, history, positions)
        if line:
            projected = line['end_price'] + line['slope_per_bar']
            if isfinite(projected) and projected > 0:
                lines[field] = {**line, 'projected_price': projected}
    result = {**result, 'previous_close': history[-1]['close'], 'signal_low': signal['low'],
              'signal_close': signal['close'], 'lines': lines}
    if fields & ZONE_FIELDS.keys():
        result['zones'] = {zone['line_type']: zone for zone in _observation_zones(history, pivots, positions)}
    return result


def evaluate(node, metrics):
    state = metrics.get('trendline_state') or {}
    reason = state.get('reason') or metrics.get('reason')
    result = None
    field = node['field']
    zone_kind = ZONE_FIELDS.get(field)
    line = state.get('lines', {}).get(field)
    zone = state.get('zones', {}).get(zone_kind)
    tolerance = node['value']/100
    if zone_kind and 'zones' in state:
        result = False
        if zone:
            lower, upper = zone['lower_price'], zone['upper_price']
            close = state['signal_close']
            result = (lower <= close <= upper*(1+tolerance) if zone_kind == 'support'
                      else state['previous_close'] <= upper and close > upper*(1+tolerance))
    elif not zone_kind and 'lines' in state:
        result = False
        if line:
            level = line['projected_price']
            before, close = state['previous_close'], state['signal_close']
            if field == 'trendline_pullback':
                result = (line['slope_per_bar'] > 0 and before >= line['end_price']
                          and level*(1-tolerance) <= state['signal_low'] <= level*(1+tolerance)
                          and close >= level)
            else:
                result = before <= line['end_price'] and close > level*(1+tolerance)
    return {**node, 'result': result, 'period': state.get('period'),
            'observed_date': state.get('observed_date'),
            'reason': (reason or ('水平区价格输入未核验' if zone_kind else '趋势线价格输入未核验')) if result is None else None,
            'evidence': {**{key: value for key, value in state.items() if key not in ('lines', 'zones')},
                         **({'zone': zone} if zone_kind else {'line': line})}}
