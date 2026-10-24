"""Shared display indicators on raw prices; restart after known actions/gaps."""
from collections import deque
from bisect import bisect_left, bisect_right
from datetime import date
from math import isfinite
from .rolling_high import RollingHighState

WINDOWS = (5, 10, 20, 30, 60, 250)
INDICATOR_VERSION = 'raw-actions-reset-dead-cross-v2'
INTRADAY_HIGH_REASONS = {
    'period': '当日最高涨幅只使用观察日日线，不用周/月聚合高价替代',
    'previous': '缺少前一个市场交易日的连续有效日报价，不能用上一条可见报价替代',
    'coverage': '未留存可核验的公司行动覆盖，前收比较保持未知',
    'action': '当日有未复权公司行动，最高价与前收不可直接比较',
    'price': '当日最高价或当前/前一日收盘价无效',
    'volume': '当日或前一交易日缺少有效成交量，停牌/零量保持未知',
}
INTRADAY_HIGH_DECIMALS = 12


def intraday_high_change(row, previous, positions):
    """Completed daily high versus the exact previous market session close."""
    positive = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0
    if 'week_closed' in row or 'month_closed' in row:
        return None, INTRADAY_HIGH_REASONS['period']
    today = positions.get(row.get('date'))
    if not previous or today is None or today < 1 or positions.get(previous.get('date')) != today-1:
        return None, INTRADAY_HIGH_REASONS['previous']
    if any(item.get('corporate_action_coverage_known', True) is not True for item in (row, previous)):
        return None, INTRADAY_HIGH_REASONS['coverage']
    if row.get('corporate_action'):
        return None, INTRADAY_HIGH_REASONS['action']
    high = row.get('high'); close = row.get('close'); prior = previous.get('close')
    if not all(positive(value) for value in (high, close, prior)) or high < close:
        return None, INTRADAY_HIGH_REASONS['price']
    if not all(positive(item.get('volume')) for item in (row, previous)):
        return None, INTRADAY_HIGH_REASONS['volume']
    value = (high/prior-1)*100
    return (round(value, INTRADAY_HIGH_DECIMALS), None) if isfinite(value) else (None, INTRADAY_HIGH_REASONS['price'])


def crossed_down(previous_fast, previous_slow, fast, slow):
    """A real downward crossing, rather than the negation of a golden cross."""
    values = (previous_fast, previous_slow, fast, slow)
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) for value in values):
        return None
    return previous_fast >= previous_slow and fast < slow


def enrich_bars(rows, calendar=None, *, tail=None):
    """Keep the original recurrence; optionally materialize only its last rows.

    Selection needs the current and preceding indicators, while charts and
    historical events need every dated proof. A tail never truncates the seed:
    all prices, resets and arithmetic updates still run in their original order.
    """
    if tail is not None:
        if not isinstance(tail, int) or isinstance(tail, bool) or tail < 1:
            raise ValueError('指标末期数量必须为正整数')
        rows = list(rows)
    emit_from = max(0, len(rows)-tail) if tail is not None else 0
    positions = {d: i for i, d in enumerate(calendar or [])}
    out = []
    windows = {n: deque() for n in WINDOWS}
    totals = {n: 0.0 for n in WINDOWS}
    last9 = deque(maxlen=9)
    closes = deque(maxlen=31)
    e12 = e26 = dea = None
    k = d = 50.0
    count = 0
    prev = None
    warmup_reason = '初始预热：MACD/KDJ需要35根可比报价'
    high_state = RollingHighState(calendar)
    for position, source in enumerate(rows):
        emit = position >= emit_from
        row = dict(source)
        daily_gap = prev is not None and row['date'] in positions and prev['date'] in positions and positions[row['date']] - positions[prev['date']] != 1
        weekly_reason = row.get('weekly_continuity_reset_reason')
        gap = daily_gap or bool(weekly_reason)
        reset = row.get('corporate_action', False) or gap
        missing = positions[row['date']] - positions[prev['date']] - 1 if daily_gap else row.get('weekly_missing_market_sessions',0)
        reset_reason = '公司行动尚未复权，技术指标重新预热' if row.get('corporate_action',False) else weekly_reason or (f'缺少{missing}个市场交易日报价，尚未区分停牌与缺数' if daily_gap else None)
        if reset:
            windows = {n: deque() for n in WINDOWS}; totals = {n: 0.0 for n in WINDOWS}
            last9.clear(); closes.clear(); e12 = e26 = dea = None; k = d = 50.0; count = 0
            warmup_reason=reset_reason
        close = float(row['close']); count += 1
        for n in WINDOWS:
            windows[n].append(close); totals[n] += close
            if len(windows[n]) > n: totals[n] -= windows[n].popleft()
            if emit: row[f'ma{n}'] = totals[n] / n if len(windows[n]) == n else None
        if emit:
            row['change_pct'] = (close / prev['close'] - 1) * 100 if prev and prev['close'] and not reset else None
            row['pct_change'] = row['change_pct']
            row['intraday_high_change_pct'], row['intraday_high_change_pct_reason'] = intraday_high_change(row, prev, positions)
            row['volume_ratio'] = row['volume'] / prev['volume'] if prev and prev['volume'] and not reset else None
            row['volume_multiple_prev'] = row['volume_ratio']
        closes.append(close)
        if emit:
            row['return_30d'] = (close / closes[0] - 1) * 100 if len(closes) == 31 and closes[0] else None
            row['macd_cross'] = row['kd_cross'] = None
            row['macd_dead_cross'] = row['kd_dead_cross'] = None
        old_dif, old_dea = (e12 - e26, dea) if e12 is not None else (None, None)
        if e12 is None: e12 = e26 = close; dea = 0.0
        else: e12 = close * 2 / 13 + e12 * 11 / 13; e26 = close * 2 / 27 + e26 * 25 / 27
        dif = e12 - e26; dea = .2 * dif + .8 * dea
        if emit:
            row['macd_dif'] = dif if count >= 35 else None
            row['macd_dea'] = dea if count >= 35 else None
            row['macd_hist'] = 2 * (dif - dea) if count >= 35 else None
            if count >= 36: row['macd_cross'] = old_dif <= old_dea and dif > dea
            traded_pair = prev is not None and all(isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0 for value in (row.get('volume'), prev.get('volume')))
            if count >= 36 and traded_pair: row['macd_dead_cross'] = crossed_down(old_dif, old_dea, dif, dea)
        last9.append(row)
        old_k, old_d = k, d
        if len(last9) == 9:
            low = min(r['low'] for r in last9); high = max(r['high'] for r in last9)
            rsv = (close - low) / (high - low) * 100 if high > low else 50
            k = 2*k/3 + rsv/3; d = 2*d/3 + k/3
        breakout = high_state.observe(row,reset=bool(reset))
        if emit:
            row['kdj_k'] = k if count >= 35 else None
            row['kdj_d'] = d if count >= 35 else None
            row['kdj_j'] = 3*k-2*d if count >= 35 else None
            if count >= 36: row['kd_cross'] = old_k <= old_d and k > d
            if count >= 36 and traded_pair: row['kd_dead_cross'] = crossed_down(old_k, old_d, k, d)
            row['price_continuity'] = not reset
            row['indicator_warmup_bars'] = count
            row['indicator_reset_reason'] = reset_reason
            row['indicator_warmup_reason'] = warmup_reason if count<35 else None
            row['missing_market_sessions_before'] = missing
            row.update(breakout)
            row.setdefault('turnover_ratio_pct', None)
            out.append(row)
        prev = row
    return out


def aggregate_weekly(rows, calendar=None, as_of=None):
    groups = {}
    for row in rows:
        key = date.fromisoformat(row['date']).isocalendar()[:2]
        groups.setdefault(key, []).append(row)
    calendar = sorted(set(calendar or []))
    horizon = as_of or (max(row['date'] for row in rows) if rows else None)
    start = min((row['date'] for row in rows),default=None)
    expected_by_week = {}
    for day in calendar:
        if start and start<=day<=horizon:
            expected_by_week.setdefault(date.fromisoformat(day).isocalendar()[:2],[]).append(day)
    result = []
    for key, bs in sorted(groups.items()):
        bs=sorted(bs,key=lambda row:row['date'])
        last = bs[-1]['date']
        following_index = bisect_right(calendar, last)
        following = calendar[following_index] if following_index < len(calendar) else None
        observed={bar['date'] for bar in bs}
        missing_within=[day for day in expected_by_week.get(key,[]) if day not in observed]
        missing_between=(calendar[bisect_right(calendar,result[-1]['date']):bisect_left(calendar,bs[0]['date'])]
                         if result else [])
        missing_dates=sorted(set(missing_within+missing_between))
        coverage_complete=not missing_within if expected_by_week.get(key) else None
        previous_incomplete=bool(result and result[-1]['weekly_quote_coverage_complete'] is False)
        reason=f'周线区间缺少{len(missing_dates)}个市场交易日报价，尚未区分停牌与缺数' if missing_dates else '前一周报价不完整，周指标重新预热' if previous_incomplete else None
        week_closed=(following is not None and date.fromisoformat(following).isocalendar()[:2] != key) or date.fromisoformat(last).weekday() == 4
        complete=week_closed and coverage_complete is not False
        result.append({**bs[-1], 'date': last, 'open': bs[0]['open'], 'high': max(b['high'] for b in bs), 'low': min(b['low'] for b in bs), 'close': bs[-1]['close'], 'volume': sum(b['volume'] for b in bs), 'amount': sum(b['amount'] for b in bs), 'corporate_action': any(b.get('corporate_action') for b in bs), 'complete': complete,'week_closed':week_closed,'weekly_quote_coverage_complete':coverage_complete,'weekly_missing_market_sessions':len(missing_dates),'weekly_missing_market_dates':missing_dates,'weekly_continuity_reset_reason':reason})
    return result
