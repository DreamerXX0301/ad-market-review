"""Optional chart-only recurrence; market-day signal evidence stays untouched."""
from collections import defaultdict
from datetime import date
from math import isfinite

from .indicators import WINDOWS, enrich_bars
from .support_resistance import _day, _valid_bar

DISPLAY_FIELDS = tuple('ma' + str(window) for window in WINDOWS) + (
    'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j',
)
OSCILLATOR_FIELDS = DISPLAY_FIELDS[len(WINDOWS):]
FORMING_PERIOD_VERSION = 'forming-period-display-v1'


def display_indicators(strict_bars, source_rows, continuity, period):
    """Copy only plotted values, retaining strict returns/crosses/gap evidence.

    A missing market day is never filled with a synthetic candle.  Quote mode
    follows actual reported daily bars; unresolved actions still restart it.
    Week/month coverage and forming-period rules always retain the strict path.
    """
    actual = 'quotes' if period == 'D' and continuity == 'quotes' else 'market_sessions'
    display = enrich_bars(source_rows) if actual == 'quotes' else strict_bars
    result = []
    for strict, plotted in zip(strict_bars, display):
        row = dict(strict)
        for field in DISPLAY_FIELDS:
            row[field] = plotted.get(field)
        for field in ('indicator_warmup_bars', 'indicator_warmup_reason', 'indicator_reset_reason'):
            row['display_' + field] = plotted.get(field)
        result.append(row)
    return result, actual


def forming_period_indicators(periods, daily_rows, calendar, as_of, period, *, stock=False):
    """Continue the last complete run with one provisional aggregate for plotting.

    Verify all daily prices against the known sessions in each period. The
    recurrence can use the forming tail once, without emitting event flags or
    promoting it to a closed period in the chart rows or strategy inputs.
    """
    fields = {
        'W': ('week_closed', 'weekly_quote_coverage_complete', 'weekly_continuity_reset_reason'),
        'M': ('month_closed', 'monthly_quote_coverage_complete', 'monthly_continuity_reset_reason'),
    }.get(period)
    if not fields or len(periods) < 2 or not _day(as_of):
        return None
    closed, coverage, reset = fields
    last = periods[-1]
    if (last['date'] != as_of or last.get('complete') is not False or last.get(closed) is not False
            or last.get(coverage) is not True or last.get(reset)):
        return None
    if not calendar or calendar != sorted(set(calendar)) or as_of not in calendar:
        return None

    def period_start(day):
        parsed = date.fromisoformat(day)
        if period == 'W':
            return date.fromordinal(parsed.toordinal() - parsed.weekday()).isoformat()
        return parsed.replace(day=1).isoformat()

    def slot(day):
        parsed = date.fromisoformat(day)
        if period == 'W':
            return (parsed.toordinal() - 1) // 7
        return parsed.year * 12 + parsed.month - 1

    expected = defaultdict(list)
    for day in calendar:
        if day > as_of:
            break
        expected[slot(day)].append(day)
    observed = defaultdict(list)
    for row in daily_rows:
        if row['date'] <= as_of:
            observed[slot(row['date'])].append(row)

    def comparable(bar, key):
        rows = observed[key]
        return (bar.get(coverage) is True
                and not bar.get('corporate_action')
                and period_start(bar['date']) >= calendar[0]
                and bool(rows)
                and sorted(row['date'] for row in rows) == expected[key]
                and all(_valid_bar(row, 'D') and not row.get('corporate_action')
                        and (not stock or row.get('corporate_action_coverage_known') is True)
                        for row in rows))

    last_key = slot(as_of)
    if not comparable(last, last_key):
        return None
    seed = []
    key = last_key - 1
    for bar in reversed(periods[:-1]):
        if slot(bar['date']) != key or bar.get('complete') is not True or not comparable(bar, key):
            break
        seed.append(bar)
        if bar.get(reset):
            break
        key -= 1
    # At least one full period must precede the display preview. All older
    # invalid or incomparable inputs stay outside the recurrence.
    if not seed:
        return None
    preview = enrich_bars([*reversed(seed), last], tail=1)[0]
    values = {}
    for field in DISPLAY_FIELDS:
        value = preview[field]
        values[field] = value if value is not None and isfinite(value) else None
    return {'date': as_of, 'values': values,
            'warmup_bars': preview['indicator_warmup_bars']}


def quote_gaps(rows, calendar):
    """Report all omitted market sessions without labeling them source failures."""
    calendar = sorted(set(calendar or []))
    positions = {day: index for index, day in enumerate(calendar)}
    gaps = []
    for previous, current in zip(rows, rows[1:]):
        left = positions.get(previous['date']); right = positions.get(current['date'])
        if left is None or right is None or right - left <= 1:
            continue
        missing = calendar[left + 1:right]
        gaps.append({'previous_quote_date': previous['date'], 'resume_quote_date': current['date'],
                     'start_date': missing[0], 'end_date': missing[-1],
                     'missing_market_sessions': len(missing), 'missing_market_dates': missing,
                     'classification': 'unclassified',
                     'reason': '这些市场交易日没有本股报价；可能停牌或缺数，不能仅据缺行定性'})
    return gaps


def indicator_gaps(bars):
    """Only adjacent missing-value bars belong to one diagnostic interval."""
    gaps = []; last_index = -2
    for index, bar in enumerate(bars):
        affected = [field for field in OSCILLATOR_FIELDS if bar.get(field) is None]
        if not affected:
            continue
        reason = bar.get('display_indicator_warmup_reason', bar.get('indicator_warmup_reason')) or '价格不可比或预热不足'
        count = bar.get('display_indicator_warmup_bars', bar.get('indicator_warmup_bars'))
        reset = bar.get('display_indicator_reset_reason', bar.get('indicator_reset_reason'))
        if gaps and last_index == index - 1 and not reset and gaps[-1]['reason'] == reason and gaps[-1]['affected_fields'] == affected:
            gaps[-1]['end_date'] = bar['date']; gaps[-1]['bars'] += 1; gaps[-1]['end_warmup_bars'] = count
        else:
            gaps.append({'start_date': bar['date'], 'end_date': bar['date'], 'bars': 1,
                         'reason': reason, 'affected_fields': affected,
                         'start_warmup_bars': count, 'end_warmup_bars': count, 'required_bars': 35})
        last_index = index
    return gaps
