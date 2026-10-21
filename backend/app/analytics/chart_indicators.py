"""Optional chart-only recurrence; market-day signal evidence stays untouched."""
from .indicators import WINDOWS, enrich_bars

DISPLAY_FIELDS = tuple('ma' + str(window) for window in WINDOWS) + (
    'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j',
)
OSCILLATOR_FIELDS = DISPLAY_FIELDS[len(WINDOWS):]


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
