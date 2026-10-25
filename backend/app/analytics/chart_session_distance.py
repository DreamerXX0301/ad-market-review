"""Read-only chart distances over the chart's own retained market calendar."""
from datetime import date


def _date(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def attach_chart_session_distance(chart, calendar, *, same_snapshot=True):
    """Add display evidence without changing prices, bars, or signal periods.

    Count (selected quotation date, chart observation date] in the supplied
    market calendar. Never substitute quotation-row count or weekday arithmetic.
    Weekly/monthly ``date`` remains the aggregator's final actual quotation day.
    """
    anchor = chart.get('as_of')
    rows = chart.get('bars')
    valid_calendar = (isinstance(calendar, (list, tuple)) and bool(calendar)
                      and all(_date(day) for day in calendar)
                      and all(left < right for left, right in zip(calendar, calendar[1:])))
    positions = {day: i for i, day in enumerate(calendar)} if valid_calendar else {}
    reason = ('读取期间行情代际变化，交易日距离待核验，请刷新。' if not same_snapshot
              else '本图市场交易日日历缺失、重复或顺序无效，交易日距离待核验。' if not valid_calendar
              else '本图观察日不在已留存市场交易日日历内，交易日距离待核验。' if not _date(anchor) or anchor not in positions
              else '当前图表没有已留存K线，交易日距离待核验。' if not isinstance(rows, list) or not rows
              else None)
    evidence = {'anchor_date': anchor if _date(anchor) else None,
                'basis': 'market_calendar', 'state': 'unknown' if reason else 'available',
                'reason': reason or '按本图已留存市场交易日日历计算，所选报价日不计、观察日计；停牌或缺报价不减少间隔，不以自然日或K线根数代替。'}
    projected = []
    for row in rows if isinstance(rows, list) else []:
        day = row.get('date') if isinstance(row, dict) else None
        distance = (positions[anchor] - positions[day]
                    if not reason and _date(day) and day in positions and day <= anchor else None)
        projected.append({**row, 'trading_days_to_as_of': distance} if isinstance(row, dict) else row)
    return {**chart, 'bars': projected, 'trading_day_distance': evidence}
