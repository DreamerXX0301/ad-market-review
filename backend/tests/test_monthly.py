"""Monthly charts use calendar completeness, never invented monthly history."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.analytics.monthly import aggregate_monthly, enrich_monthly_bars
from app.services.chart_limits import enrich_chart_limits
from app.services.research_trends import aggregate_monthly as research_aggregate_monthly
from app.services.research_trends import build_trends


def weekdays(start, end):
    result = []
    day = date.fromisoformat(start)
    while day.isoformat() <= end:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def daily(days):
    return [{'date': day, 'open': 10 + i / 100, 'high': 11 + i / 100,
             'low': 9 + i / 100, 'close': 10.5 + i / 100, 'volume': 100 + i,
             'amount': 1000 + i, 'corporate_action': False, 'price_basis': 'none'}
            for i, day in enumerate(days)]


def test_aggregate_is_shared_ordered_and_preserves_real_ohlc_units_and_cutoff():
    calendar = weekdays('2025-12-31', '2026-03-03')
    rows = daily([day for day in calendar if day >= '2026-01-01'])
    before = deepcopy(rows)
    result = aggregate_monthly(list(reversed(rows)), calendar, '2026-03-02')
    january = [row for row in rows if row['date'][:7] == '2026-01']
    assert research_aggregate_monthly is aggregate_monthly
    assert result[0]['date'] == january[-1]['date']
    assert result[0]['open'] == january[0]['open']
    assert result[0]['close'] == january[-1]['close']
    assert result[0]['high'] == max(row['high'] for row in january)
    assert result[0]['low'] == min(row['low'] for row in january)
    assert result[0]['volume'] == sum(row['volume'] for row in january)
    assert result[0]['amount'] == sum(row['amount'] for row in january)
    assert result[0]['complete'] and result[0]['monthly_quote_coverage_complete'] is True
    assert result[-1]['date'] == '2026-03-02' and not result[-1]['complete']
    assert rows == before


def test_retained_calendar_starting_midmonth_cannot_prove_a_complete_first_month():
    calendar = weekdays('2026-01-12', '2026-03-02')
    result = aggregate_monthly(daily(calendar), calendar, '2026-03-02')
    assert result[0]['month_closed'] and not result[0]['complete']
    assert result[0]['window_start_partial']
    assert result[0]['monthly_quote_coverage_complete'] is None
    assert '日历边界' in result[0]['monthly_continuity_reset_reason']
    assert result[1]['complete']
    assert enrich_monthly_bars(result)[1]['indicator_warmup_bars'] == 1


def test_missing_start_day_known_from_prior_calendar_is_missing_not_complete():
    calendar = weekdays('2025-12-31', '2026-03-02')
    rows = daily([day for day in calendar if day >= '2026-01-02'])
    result = aggregate_monthly(rows, calendar, '2026-03-02')
    assert result[0]['monthly_quote_coverage_complete'] is False
    assert not result[0]['complete'] and result[0]['window_start_partial']
    assert '2026-01-01' in result[0]['monthly_missing_market_dates']


def test_no_calendar_or_empty_input_never_invents_complete_months():
    assert aggregate_monthly([], [], '2026-03-02') == []
    result = aggregate_monthly(daily(['2026-01-02', '2026-02-02']), as_of='2026-03-02')
    assert all(row['monthly_quote_coverage_complete'] is None and not row['complete'] for row in result)
    assert all(row['macd_hist'] is None for row in enrich_monthly_bars(result))


def test_forming_month_only_closes_when_calendar_proves_month_end():
    calendar = weekdays('2025-12-31', '2026-02-02')
    rows = daily([day for day in calendar if day >= '2026-01-01'])
    preview = aggregate_monthly(rows, calendar, '2026-01-15')[0]
    assert not preview['month_closed'] and not preview['complete']
    # January ends on Saturday; the retained next-month session proves no later
    # January market day. There are still no future prices in the candle.
    closed = aggregate_monthly(rows, calendar, '2026-01-30')[0]
    assert closed['month_closed'] and closed['complete'] and closed['date'] == '2026-01-30'
    unknown_end = aggregate_monthly(rows, [day for day in calendar if day <= '2026-01-30'], '2026-01-30')[0]
    assert not unknown_end['month_closed']


@pytest.mark.parametrize('remove', ['one_day', 'whole_month'])
def test_missing_quotes_or_an_entire_month_breaks_indicator_continuity(remove):
    calendar = weekdays('2025-12-31', '2026-05-01')
    rows = daily([day for day in calendar if day >= '2026-01-01'])
    rows = [row for row in rows if row['date'] != '2026-02-10'] if remove == 'one_day' else [row for row in rows if row['date'][:7] != '2026-02']
    result = aggregate_monthly(rows, calendar, '2026-05-01')
    enriched = enrich_monthly_bars(result)
    march = next(row for row in enriched if row['date'][:7] == '2026-03')
    assert march['complete'] and march['indicator_warmup_bars'] == 1
    assert march['macd_hist'] is None
    if remove == 'whole_month':
        assert all(row['date'][:7] != '2026-02' for row in result)
        assert march['monthly_missing_market_sessions'] == len([day for day in calendar if day[:7] == '2026-02'])
    else:
        february = next(row for row in enriched if row['date'][:7] == '2026-02')
        assert february['monthly_quote_coverage_complete'] is False
        assert not february['monthly_indicator_eligible'] and february['ma5'] is None


def test_duplicate_daily_rows_do_not_double_volume_or_qualify_as_complete():
    calendar = weekdays('2025-12-31', '2026-02-02')
    rows = daily([day for day in calendar if day >= '2026-01-01'])
    duplicated = {**rows[2], 'close': 99, 'volume': 9999}
    result = aggregate_monthly(rows + [duplicated], calendar, '2026-02-02')
    january = result[0]
    assert january['monthly_duplicate_market_dates'] == [rows[2]['date']]
    assert january['monthly_quote_coverage_complete'] is False
    assert january['volume'] == sum(row['volume'] for row in rows if row['date'][:7] == '2026-01')
    assert enrich_monthly_bars(result)[0]['macd_hist'] is None


def test_three_year_window_requires_35_comparable_complete_months_not_35_candles():
    calendar = weekdays('2023-10-20', '2026-10-20')
    result = enrich_monthly_bars(aggregate_monthly(daily(calendar), calendar, '2026-10-20'))
    assert len(result) == 37
    assert result[0]['window_start_partial'] and result[0]['indicator_warmup_bars'] == 0
    assert result[-3]['indicator_warmup_bars'] == 34 and result[-3]['macd_hist'] is None
    assert result[-2]['indicator_warmup_bars'] == 35
    assert result[-2]['macd_hist'] is not None and result[-2]['kdj_k'] is not None
    assert result[-1]['indicator_warmup_bars'] == 0
    assert result[-1]['macd_hist'] is None and result[-1]['kdj_j'] is None
    assert all(row['ma60'] is None and row['ma250'] is None for row in result)


def test_raw_action_month_is_not_comparable_but_forward_input_keeps_its_basis():
    calendar = weekdays('2023-10-20', '2026-10-20')
    rows = daily(calendar)
    action = next(row for row in rows if row['date'] == '2025-12-15')
    action['corporate_action'] = True
    raw = enrich_monthly_bars(aggregate_monthly(rows, calendar, '2026-10-20'))
    december = next(row for row in raw if row['date'][:7] == '2025-12')
    assert december['complete'] and not december['monthly_price_comparable']
    assert december['macd_hist'] is None and december['indicator_warmup_bars'] == 0
    assert next(row for row in raw if row['date'][:7] == '2026-01')['indicator_warmup_bars'] == 1
    adjusted = [{**row, 'corporate_action': False, 'corporate_action_original': row['corporate_action'],
                 'price_basis': 'forward_event_affine', 'close': row['close'] / 2,
                 'open': row['open'] / 2, 'high': row['high'] / 2, 'low': row['low'] / 2} for row in rows]
    forward = enrich_monthly_bars(aggregate_monthly(adjusted, calendar, '2026-10-20'))
    assert forward[-2]['price_basis'] == 'forward_event_affine'
    assert forward[-2]['macd_hist'] is not None and forward[-2]['indicator_warmup_bars'] == 35
    assert forward[-2]['close'] == raw[-2]['close'] / 2


class MonthlyMarket:
    def __init__(self):
        self.days = weekdays('2025-12-31', '2026-03-03')
        self.rows = daily([day for day in self.days if day >= '2026-01-01'])
        self.ref = {}
    def calendar(self): return self.days
    def bars(self, symbol, as_of=None): return [dict(row) for row in self.rows if not as_of or row['date'] <= as_of]
    def reference(self): return self.ref
    def actions(self, symbol, end=None): return []
    def chart(self, cutoff='2026-03-02'):
        return {'instrument': {'instrument_id': '600001.SH', 'kind': 'stock'}, 'as_of': cutoff,
                'period': 'M', 'bars': enrich_monthly_bars(aggregate_monthly(self.rows, self.days, cutoff))}


def pool(market, day, kind):
    close = next(row['close'] for row in market.rows if row['date'] == day)
    return {'date': day, 'pool_kind': kind, 'items': [{'instrument_id': '600001.SH', 'date': day, 'last_price': close}]}


def test_monthly_limits_aggregate_exact_daily_dates_without_whole_month_claims_or_future():
    market = MonthlyMarket()
    supplement = {'annual_pools': {'2026-01-05': pool(market, '2026-01-05', 'limitup'),
                                  '2026-03-03': pool(market, '2026-03-03', 'limitup')},
                  'annual_down_pools': {'2026-01-20': pool(market, '2026-01-20', 'limitdown')}}
    result = enrich_chart_limits(market, market.chart(), supplement)
    january = result['bars'][0]
    assert january['limit_up_state'] is None and january['limit_down_state'] is None
    assert january['limit_event_status'] == {'up': 'monthly_daily_summary', 'down': 'monthly_daily_summary'}
    assert january['limit_event_summary']['scope'] == 'monthly_daily_events'
    assert january['limit_event_summary']['up_count'] == january['limit_event_summary']['down_count'] == 1
    assert [event['date'] for event in january['limit_events']] == ['2026-01-05', '2026-01-20']
    assert all(event['bar_date'] == january['date'] and event['chart_price'] is None for event in january['limit_events'])
    assert len(result['limit_events']) == 2 and result['limit_event_coverage']['until'] == '2026-03-02'


def test_monthly_research_uses_shared_complete_months_and_reports_actual_sample():
    market = MonthlyMarket()
    trend = next(row for row in build_trends(market, '600001.SH', '2026-03-02') if row['period'] == 'M')
    assert trend['sample_bars'] == 3 and trend['complete_bars'] == 2
    assert trend['latest_complete_date'] == '2026-02-27'
    assert trend['indicator_warmup_bars'] == 2 and trend['observations']['macd_hist'] is None
    assert any('共3根' in warning for warning in trend['warnings'])
