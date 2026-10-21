"""Chart display recurrence is independent of strict signal-time gap handling."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.analytics.chart_indicators import indicator_gaps
from app.analytics.indicators import enrich_bars
from app.analytics.market import MarketData


def weekdays(start, end):
    first = date.fromisoformat(start); last = date.fromisoformat(end)
    return [(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)
            if (first + timedelta(days=i)).weekday() < 5]


class QuoteGapMarket(MarketData):
    def __init__(self):
        self.calendar_days = [day for day in weekdays('2025-10-20', '2026-10-20')
                              if day != '2026-09-25' and not '2026-10-01' <= day <= '2026-10-07']
        missing = set(weekdays('2026-09-07', '2026-09-18') + weekdays('2026-10-13', '2026-10-15'))
        prices = {'2026-09-04': 5.31, '2026-09-21': 5.84, '2026-09-22': 6.42,
                  '2026-09-23': 7.06, '2026-09-24': 7.77, '2026-09-28': 8.55,
                  '2026-09-29': 9.41, '2026-09-30': 10.35, '2026-10-08': 11.39,
                  '2026-10-09': 12.53, '2026-10-12': 13.78, '2026-10-16': 15.16,
                  '2026-10-19': 15.58, '2026-10-20': 15.19}
        self.rows = []
        for day in self.calendar_days:
            if day in missing: continue
            price = prices.get(day, 5.0)
            self.rows.append({'date': day, 'open': price, 'close': price, 'high': price+.1,
                              'low': price-.1, 'volume': 100.0, 'amount': 1000.0,
                              'corporate_action': day == '2026-05-28', 'corporate_action_coverage_known': True})
    def calendar(self): return self.calendar_days
    def status(self): return {'data_end': self.rows[-1]['date'], 'snapshot_id': 'fixture', 'warnings': [], 'quality': 'partial'}
    def instrument(self, symbol): return {'instrument_id': symbol, 'kind': 'stock'}
    def bars(self, symbol, as_of=None): return [dict(row) for row in self.rows if as_of is None or row['date'] <= as_of]
    def actions(self, *args, **kwargs):
        return [{'date': '2026-05-28', 'dividend_per_share': .013, 'per_share_bonus': 0,
                 'allotment_ratio': 0, 'allotment_price': 0}]


def test_quote_display_is_continuous_but_retains_thirteen_missing_days_and_strict_evidence():
    market = QuoteGapMarket(); original = deepcopy(market.rows)
    strict = market.chart('600825.SH')
    quoted = market.chart('600825.SH', indicator_continuity='quotes')
    assert strict['indicator_continuity'] == 'market_sessions'
    assert quoted['indicator_continuity'] == 'quotes'
    assert market.rows == original
    assert strict['bars'][-1]['ma5'] is None and strict['bars'][-1]['macd_dif'] is None
    assert quoted['bars'][-1]['ma5'] == pytest.approx((12.53+13.78+15.16+15.58+15.19)/5)
    assert quoted['bars'][-1]['macd_dif'] is not None and quoted['bars'][-1]['kdj_k'] is not None
    expected = enrich_bars(market.rows)
    for raw, display, scalar in zip(strict['bars'], quoted['bars'], expected):
        for field in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'change_pct',
                      'price_continuity', 'macd_cross', 'kd_cross', 'indicator_warmup_bars',
                      'indicator_reset_reason', 'missing_market_sessions_before'):
            assert display[field] == raw[field], field
        for field in ('ma5', 'ma60', 'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j'):
            assert display[field] == scalar[field], field
    assert [gap['missing_market_sessions'] for gap in quoted['quote_gaps']] == [10, 3]
    assert sum(len(gap['missing_market_dates']) for gap in quoted['quote_gaps']) == 13
    assert all(gap['classification'] == 'unclassified' for gap in quoted['quote_gaps'])
    assert len(quoted['bars']) == len(strict['bars']) == len(market.rows)
    assert quoted['bars'][-1]['indicator_warmup_bars'] == 3
    assert quoted['bars'][-1]['display_indicator_warmup_bars'] > 35


def test_quotes_still_restarts_raw_action_and_explicit_forward_changes_only_display_basis():
    market = QuoteGapMarket()
    raw = market.chart('600825.SH', as_of='2026-05-29', indicator_continuity='quotes')
    forward = market.chart('600825.SH', as_of='2026-05-29', price_basis='forward', indicator_continuity='quotes')
    assert raw['bars'][-1]['macd_dif'] is None
    assert raw['bars'][-1]['display_indicator_warmup_bars'] == 2
    assert '公司行动' in raw['bars'][-1]['display_indicator_warmup_reason']
    assert forward['bars'][-1]['macd_dif'] is not None
    assert forward['bars'][-1]['raw_close'] == raw['bars'][-1]['close']
    assert forward['price_basis'] == 'forward_event_affine'
    assert forward['bars'][-1]['close'] == raw['bars'][-1]['close']


@pytest.mark.parametrize('period', ['W', 'M'])
def test_week_and_month_never_skip_coverage_boundaries_when_quote_mode_requested(period):
    market = QuoteGapMarket()
    strict = market.chart('600825.SH', period=period)
    quoted = market.chart('600825.SH', period=period, indicator_continuity='quotes')
    assert quoted['requested_indicator_continuity'] == 'quotes'
    assert quoted['indicator_continuity'] == 'market_sessions'
    assert quoted['bars'] == strict['bars']
    assert quoted['bars'][-1]['complete'] is False
    assert quoted['bars'][-1]['macd_dif'] is None


def test_quote_display_asof_does_not_use_future_rows_and_rejects_invalid_mode():
    market = QuoteGapMarket(); cutoff = '2026-09-24'
    before = market.chart('600825.SH', as_of=cutoff, indicator_continuity='quotes')
    for row in market.rows:
        if row['date'] > cutoff: row.update(close=10000, high=10001)
    after = market.chart('600825.SH', as_of=cutoff, indicator_continuity='quotes')
    assert before['bars'] == after['bars']
    assert all(gap['resume_quote_date'] <= cutoff for gap in after['quote_gaps'])
    with pytest.raises(ValueError): market.chart('600825.SH', indicator_continuity='fill_missing')


def test_separated_action_warmups_do_not_merge_across_valid_indicator_intervals():
    days = weekdays('2026-01-01', '2026-12-31')[:120]
    rows = [{'date': day, 'open': 5, 'high': 6, 'low': 4, 'close': 5,
             'volume': 100, 'amount': 500, 'corporate_action': index in (40, 90)}
            for index, day in enumerate(days)]
    gaps = indicator_gaps(enrich_bars(rows, days))
    action_gaps = [gap for gap in gaps if '公司行动' in gap['reason']]
    assert len(action_gaps) == 2
    assert action_gaps[0]['start_date'] == days[40] and action_gaps[0]['end_date'] == days[73]
    assert action_gaps[1]['start_date'] == days[90] and action_gaps[1]['end_date'] == days[-1]
    assert action_gaps[0]['end_warmup_bars'] == 34
