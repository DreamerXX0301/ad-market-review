"""Selected-day stock badges reuse the chart proof without scanning event history."""
from app.services.chart_limits import enrich_chart_limits
from app.services.chart_patterns import KINDS


def stock_pattern_fields(market, chart, day):
    applicable = chart.get('instrument', {}).get('kind') != 'index'
    row = next((bar for bar in reversed(chart.get('bars', [])) if bar.get('date') == day), None)
    fields = {'pattern_as_of': day, 'pattern_date': day if row is not None else None}
    if row is None or not applicable:
        reason = '指数不适用个股涨跌停形态' if not applicable else '观察日没有原始日K，不沿用上一报价日形态'
        return {**fields, 'pattern_events': [],
                'pattern_status': {kind: {'state': None, 'status': 'unknown' if applicable else 'not_applicable', 'reason': reason} for kind in KINDS},
                'pattern_coverage': {'applicable': applicable, 'scope': 'observation_day', 'until': day, 'reason': reason}}
    focused = enrich_chart_limits(market, {**chart, 'period': 'D', 'as_of': day, 'bars': [row]}, focus_day=day)
    return {**fields, 'pattern_events': focused['bars'][0]['pattern_events'],
            'pattern_status': focused['bars'][0]['pattern_status'], 'pattern_coverage': focused['pattern_coverage']}
