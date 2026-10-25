"""Calendar-aware monthly OHLC and indicators shared by charts and research.

Only supplied daily prices are aggregated: choosing raw or verified forward
prices belongs to the caller. A partial/missing month never becomes an extra
comparable observation merely to make a long-period indicator appear.
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date

from .indicators import WINDOWS, enrich_bars


def _month_index(month):
    return int(month[:4]) * 12 + int(month[5:7])


def aggregate_monthly(rows, calendar=None, as_of=None):
    """Aggregate existing daily bars, exposing uncertain calendar boundaries.

    The calendar is an ordered set of *market* trading days. Its first retained
    day may be mid-month, so that boundary cannot prove a full first month.
    Later calendar dates may prove that the current month has closed; no future
    prices or future events are read. Entirely missing months are not fabricated.
    """
    rows = list(rows)
    if not rows:
        return []
    horizon = as_of or max(row['date'] for row in rows)
    date.fromisoformat(horizon)
    sessions = sorted(set(calendar or []))
    expected_by_month = defaultdict(list)
    for day in sessions:
        if day <= horizon:
            expected_by_month[day[:7]].append(day)
    groups = defaultdict(dict)
    duplicates = defaultdict(set)
    for source in rows:
        day = source['date']
        date.fromisoformat(day)
        if day > horizon:
            continue
        month = day[:7]
        if day in groups[month]:
            # Even identical duplicates are not evidence of two trading days.
            duplicates[month].add(day)
        else:
            groups[month][day] = source
    result = []
    for month, by_date in sorted(groups.items()):
        values = [by_date[day] for day in sorted(by_date)]
        first, last = values[0]['date'], values[-1]['date']
        observed = set(by_date)
        expected = expected_by_month.get(month, [])
        month_start = month + '-01'
        calendar_start_known = bool(sessions and sessions[0] <= month_start)
        partial_begin = not calendar_start_known or bool(expected and first > expected[0])
        missing_within = [day for day in expected if day not in observed]
        previous = result[-1] if result else None
        missing_between = [day for day in sessions if previous and previous['date'] < day < first and day <= horizon and day not in observed]
        missing_dates = sorted(set(missing_within + missing_between))
        unexpected = sorted(observed - set(expected)) if sessions else []
        quote_complete = False if missing_within or duplicates[month] or unexpected else True if expected and calendar_start_known else None
        year, number = map(int, month.split('-'))
        month_end = date(year, number, monthrange(year, number)[1]).isoformat()
        known_month_sessions = [day for day in sessions if day[:7] == month]
        calendar_spans_month_end = bool(sessions and sessions[-1] > month_end)
        month_closed = month < horizon[:7] or horizon >= month_end or bool(calendar_spans_month_end and known_month_sessions and known_month_sessions[-1] <= horizon)
        complete = bool(month_closed and quote_complete is True and not partial_begin)
        skipped_months = bool(previous and _month_index(month) - _month_index(previous['date'][:7]) != 1)
        if duplicates[month]:
            reason = '月内存在重复交易日报价，月指标重新预热'
        elif missing_dates:
            reason = f'月线区间缺少{len(missing_dates)}个市场交易日报价，尚未区分停牌与缺数'
        elif unexpected:
            reason = '月内报价日期不在已留存市场日历，完整性未知'
        elif not calendar_start_known:
            reason = '首月日历边界不完整，无法证明完整月报价'
        elif skipped_months:
            reason = '中间整月没有报价，月指标重新预热'
        elif previous and not previous['complete']:
            reason = '前一月报价或周期不完整，月指标重新预热'
        else:
            reason = None
        result.append({
            **values[-1], 'date': last, 'open': values[0]['open'],
            'high': max(row['high'] for row in values), 'low': min(row['low'] for row in values),
            'close': values[-1]['close'], 'volume': sum(row['volume'] for row in values),
            'amount': sum(row['amount'] for row in values),
            'corporate_action': any(row.get('corporate_action', False) for row in values),
            'complete': complete, 'month_closed': month_closed,
            'window_start_partial': partial_begin,
            'monthly_quote_coverage_complete': quote_complete,
            'monthly_missing_market_sessions': len(missing_dates),
            'monthly_missing_market_dates': missing_dates,
            'monthly_duplicate_market_dates': sorted(duplicates[month]),
            'monthly_unexpected_market_dates': unexpected,
            'monthly_continuity_reset_reason': reason,
            # The indicator engine's continuity bridge is shared with weeks.
            'weekly_continuity_reset_reason': reason,
            'weekly_missing_market_sessions': len(missing_dates),
        })
    return result


def enrich_monthly_bars(rows, *, include_chart_extremes=False):
    """Keep preview candles, but warm indicators only on comparable full months.

    An unadjusted action month can contain prices from both sides of the action,
    so it is excluded as a comparable monthly observation. Verified forward
    input has already cleared the action flag and remains eligible.
    """
    rows = list(rows)
    eligible = []
    pending_reason = None
    reasons = {}
    for source in rows:
        row = dict(source)
        if row.get('corporate_action'):
            reason = '月内存在尚未复权公司行动，该月不纳入可比月指标预热'
        elif row.get('complete') is not True:
            reason = row.get('monthly_continuity_reset_reason') or ('当前月尚未完成，仅展示聚合OHLC' if not row.get('month_closed') else '完整月报价尚未核验')
        else:
            reason = None
        reasons[row['date']] = reason
        if reason:
            pending_reason = reason
            continue
        if pending_reason:
            row['weekly_continuity_reset_reason'] = row.get('monthly_continuity_reset_reason') or pending_reason
        eligible.append(row)
        pending_reason = None
    computed = {row['date']: row for row in enrich_bars(eligible, include_chart_extremes=include_chart_extremes)}
    indicator_fields = [*(f'ma{window}' for window in WINDOWS), 'change_pct', 'pct_change',
                        'volume_ratio', 'volume_multiple_prev', 'return_30d', 'macd_cross', 'kd_cross',
                        'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j']
    if include_chart_extremes:
        indicator_fields += ['chart_high_change_pct', 'chart_low_change_pct']
    result = []
    for source in rows:
        reason = reasons[source['date']]
        if reason is None:
            row = computed[source['date']]
            row.update(monthly_indicator_eligible=True, monthly_price_comparable=True)
        else:
            row = {**source, **{field: None for field in indicator_fields},
                   'monthly_indicator_eligible': False, 'monthly_price_comparable': False,
                   'price_continuity': False, 'indicator_warmup_bars': 0,
                   'indicator_reset_reason': reason, 'indicator_warmup_reason': reason,
                   'missing_market_sessions_before': source.get('monthly_missing_market_sessions', 0)}
            if include_chart_extremes:
                row.update(chart_extreme_change_period='M', chart_extreme_change_reference_date=None,
                           chart_high_change_pct_reason=reason, chart_low_change_pct_reason=reason)
            row.setdefault('turnover_ratio_pct', None)
        result.append(row)
    return result
