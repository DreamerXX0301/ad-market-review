"""Synthetic, database-free evidence for frozen technical report calculations."""
from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from app.services.research_technical import build_research_technical
from app.services.research_trends import build_trends

STOCK = '600001.SH'
INDEX = '000001.SH'


def weekdays(start='2019-01-01', end='2026-02-02'):
    values = []
    day = date.fromisoformat(start)
    while day.isoformat() <= end:
        if day.weekday() < 5:
            values.append(day.isoformat())
        day += timedelta(days=1)
    return values


class SyntheticMarket:
    def __init__(self, closes=None, calendar=None):
        self.days = calendar or weekdays()
        values = closes if closes is not None else [100 + i / 10 for i in range(len(self.days))]
        self.data = {
            STOCK: self.rows(values),
            INDEX: self.rows([200 + i / 20 for i in range(len(self.days))]),
        }
        self.events = {}
        self.ignore_cutoff = False

    def rows(self, closes):
        return [{'date': day, 'open': close, 'high': close + .3, 'low': close - .3,
                 'close': close, 'volume': 100, 'amount': close * 100,
                 'corporate_action': False, 'corporate_action_coverage_known': True,
                 'price_basis': 'none', 'raw': {'must_not_export': True}}
                for day, close in zip(self.days, closes)]

    def calendar(self):
        return list(self.days)

    def bars(self, symbol, as_of=None):
        return [deepcopy(row) for row in self.data[symbol]
                if self.ignore_cutoff or as_of is None or row['date'] <= as_of]

    def actions(self, symbol, end=None):
        # Deliberately return future events too; the research boundary must own
        # the cutoff rather than relying on an optimistic adapter or fixture.
        return deepcopy(self.events.get(symbol, []))

    def instrument(self, symbol):
        return {'instrument_id': symbol, 'name': '合成指数' if symbol == INDEX else '合成公司',
                'kind': 'index' if symbol == INDEX else 'stock'}

    def status(self):
        return {'snapshot_id': 'synthetic-market-generation'}


def period(report, name):
    return next(row for row in report['periods'] if row['period'] == name)


def test_arithmetic_states_relative_endpoints_and_volume_have_independent_oracles():
    market = SyntheticMarket()
    as_of = '2026-01-28'
    daily_rows = [row for row in market.data[STOCK] if row['date'] <= as_of]
    daily_rows[-1]['volume'] = 200
    next(row for row in market.data[STOCK] if row['date'] == as_of)['volume'] = 200
    before = deepcopy(market.data)
    report = build_research_technical(market, STOCK, as_of)
    assert market.data == before
    assert report['price_basis'] == 'none' and report['indicator_continuity'] == 'market_sessions'
    assert report['analysis_contract']['snapshot_id'] == 'synthetic-market-generation'
    assert {row['state'] for row in report['periods']} == {'strong'}
    daily = period(report, 'D')
    assert daily['observed_date'] == as_of
    assert daily['observations']['ma_fast'] == pytest.approx(sum(row['close'] for row in daily_rows[-20:]) / 20)
    assert daily['observations']['ma_slow'] == pytest.approx(sum(row['close'] for row in daily_rows[-60:]) / 60)
    assert daily['observations']['ma_fast_previous'] == pytest.approx(sum(row['close'] for row in daily_rows[-25:-5]) / 20)
    assert all(rule['met'] is True for rule in daily['rules'])
    assert daily['volume']['ratio_vs_previous20'] == 2
    assert daily['volume']['previous20_average_volume'] == 100
    for comparison in report['relative_strength']['windows']:
        length = comparison['market_sessions']
        stock_start = daily_rows[-length - 1]
        benchmark = [row for row in market.data[INDEX] if row['date'] <= as_of]
        stock_return = (daily_rows[-1]['close'] / stock_start['close'] - 1) * 100
        index_return = (benchmark[-1]['close'] / benchmark[-length - 1]['close'] - 1) * 100
        assert comparison['state'] == 'available'
        assert comparison['start_date'] == stock_start['date'] and comparison['end_date'] == as_of
        assert comparison['stock_return_pct'] == stock_return
        assert comparison['benchmark_return_pct'] == index_return
        assert comparison['excess_return_pp'] == stock_return - index_return
    assert period(report, 'W')['observed_date'] == '2026-01-23'
    assert period(report, 'M')['observed_date'] == '2025-12-31'
    assert period(report, 'W')['current_period']['forming']
    assert period(report, 'M')['current_period']['forming']
    assert report['space_assessment']['institutional_target']['state'] == 'unavailable'
    assert report['space_assessment']['valuation_scenarios']['state'] == 'not_calculated'
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('direction,expected', [(-1, 'weak'), (0, 'mixed')])
def test_weak_and_equal_values_are_states_not_unknown_or_probabilities(direction, expected):
    days = weekdays()
    market = SyntheticMarket([1000 + direction * i / 10 for i in range(len(days))], days)
    report = build_research_technical(market, STOCK, '2026-01-28')
    assert {row['state'] for row in report['periods']} == {expected}
    assert all('probability' not in row for row in report['periods'])
    if direction == 0:
        assert period(report, 'D')['observations']['macd_hist'] == 0
        assert period(report, 'D')['observations']['slope_pct'] == 0


def test_daily_and_monthly_conflict_remains_visible_without_majority_vote():
    market = SyntheticMarket()
    january = [row for row in market.data[STOCK] if row['date'] >= '2026-01-01']
    base = january[0]['close']
    for index, row in enumerate(january):
        value = base - 2.5 * index
        row.update(open=value, close=value, high=value + .3, low=value - .3)
    report = build_research_technical(market, STOCK, '2026-01-28')
    assert period(report, 'D')['state'] == 'weak'
    assert period(report, 'M')['state'] == 'strong'
    assert report['summary']['conflict'] is True
    assert '不投票' in report['summary']['scope']
    assert '2025-12-31' in report['summary']['text']


@pytest.mark.parametrize('missing', ['day', 'week', 'month'])
def test_absent_expected_latest_period_never_falls_back_to_an_older_complete_one(missing):
    market = SyntheticMarket()
    as_of = '2026-01-28'
    if missing == 'day':
        remove = lambda day: day == as_of
        name = 'D'
    elif missing == 'week':
        remove = lambda day: '2026-01-19' <= day <= '2026-01-23'
        name = 'W'
    else:
        remove = lambda day: day.startswith('2025-12')
        name = 'M'
    market.data[STOCK] = [row for row in market.data[STOCK] if not remove(row['date'])]
    report = build_research_technical(market, STOCK, as_of)
    item = period(report, name)
    assert item['state'] == 'unknown' and item['observed_date'] is None
    assert item['observations']['close'] is None
    assert '较早周期' in item['reason']
    assert item['levels']['support'] is item['levels']['resistance'] is None
    legacy = next(row for row in build_trends(market, STOCK, as_of) if row['period'] == name)
    assert legacy['latest_complete_date'] is None and legacy['observations']['close'] is None
    chart = report['frozen_charts_by_period'][name]
    assert any('不能把旧价' in warning for warning in chart['warnings'])


@pytest.mark.parametrize('issue', ['gap', 'action', 'unverified', 'zero_volume', 'duplicate'])
def test_recent_incomparability_is_unknown_and_not_silently_filled(issue):
    market = SyntheticMarket()
    as_of = '2026-01-28'
    days = [day for day in market.days if day <= as_of]
    bad = days[-10]
    row = next(row for row in market.data[STOCK] if row['date'] == bad)
    if issue == 'gap':
        market.data[STOCK].remove(row)
    elif issue == 'action':
        market.events[STOCK] = [{'date': bad}]
    elif issue == 'unverified':
        row['corporate_action_coverage_known'] = False
    elif issue == 'zero_volume':
        row['volume'] = 0
    else:
        market.data[STOCK].append(deepcopy(row))
    report = build_research_technical(market, STOCK, as_of)
    assert period(report, 'D')['state'] == 'unknown'
    assert period(report, 'D')['volume']['ratio_vs_previous20'] is None
    assert report['relative_strength']['windows'][0]['state'] == 'unknown'
    if issue in ('gap', 'zero_volume', 'duplicate'):
        assert bad not in {row['date'] for row in report['frozen_charts_by_period']['D']['bars']}
    if issue != 'unverified':
        assert period(report, 'D')['warmup_bars'] < 60


def test_relative_strength_keeps_same_endpoints_and_missing_benchmark_is_partial():
    market = SyntheticMarket()
    as_of = '2026-01-28'
    market.data[INDEX] = [row for row in market.data[INDEX] if row['date'] != as_of]
    result = build_research_technical(market, STOCK, as_of)
    assert period(result, 'D')['state'] == 'strong'
    for window in result['relative_strength']['windows']:
        assert window['end_date'] == as_of
        assert window['stock_return_pct'] is not None
        assert window['benchmark_return_pct'] is window['excess_return_pp'] is None
        assert window['state'] == 'unknown'
    del market.data[INDEX]
    result = build_research_technical(market, STOCK, as_of)
    assert all(window['state'] == 'unknown' for window in result['relative_strength']['windows'])


def test_complete_cycles_and_frozen_charts_are_bounded_after_full_history_calculation():
    market = SyntheticMarket()
    report = build_research_technical(market, STOCK, '2026-01-28')
    for name, limit in [('D', 320), ('W', 260), ('M', 72)]:
        chart = report['frozen_charts_by_period'][name]
        assert len(chart['bars']) <= limit
        assert chart['as_of'] == '2026-01-28'
        assert chart['price_basis'] == 'none' and chart['indicator_continuity'] == 'market_sessions'
        assert chart['calculated_before_projection'] is True
        assert chart['trading_day_distance']['anchor_date'] == chart['as_of']
        assert chart['trading_day_distance']['state'] == 'available'
        for row in chart['bars']:
            assert row['trading_days_to_as_of'] == market.days.index(chart['as_of']) - market.days.index(row['date'])
        assert all('raw' not in row for row in chart['bars'])
        assert all(row['date'] <= chart['as_of'] for row in chart['bars'])
        if name != 'D':
            assert all(row.get('complete') is True and not row.get('corporate_action') for row in chart['bars'])
        # A bounded chart still carries its full-history prewarm, never a reset
        # to 1 simply because the browser only receives the final 320 bars.
        assert chart['bars'][0]['indicator_warmup_bars'] > 1
        if name != 'M':
            assert chart['bars'][0]['ma20'] is not None
    assert len(report['frozen_charts_by_period']['D']['bars']) == 320


def test_future_prices_actions_and_source_fields_cannot_rewrite_historical_assessment():
    market = SyntheticMarket()
    as_of = '2025-09-17'
    before = build_research_technical(market, STOCK, as_of)
    market.ignore_cutoff = True
    for row in market.data[STOCK]:
        if row['date'] > as_of:
            row.update(open=999999, close=999999, high=999999.3, low=999998.7, volume=1e12)
    market.events[STOCK] = [{'date': '2025-09-18'}]
    assert build_research_technical(market, STOCK, as_of) == before


def test_confirmed_turns_are_delayed_same_run_and_do_not_change_role_after_break():
    # The last trough (8.7) and peak (12.3) each need two following real bars.
    closes = [10, 10.5, 12, 11, 10, 9, 10, 11, 12, 11, 10, 11, 10.5]
    days = weekdays('2026-01-05', '2026-02-06')
    market = SyntheticMarket(closes, days)
    cutoff = days[9]
    early = build_research_technical(market, STOCK, days[8])
    early_pressure = period(early, 'D')['levels']['resistance']
    assert early_pressure is None or early_pressure['formed_at'] != days[8]
    early = build_research_technical(market, STOCK, cutoff)
    assert period(early, 'D')['levels']['resistance']['formed_at'] != days[8]
    report = build_research_technical(market, STOCK, days[10])
    levels = period(report, 'D')['levels']
    assert levels['support']['price'] == 8.7
    assert levels['support']['formed_at'] == days[5]
    assert levels['support']['confirmed_at'] == days[7]
    assert levels['resistance']['price'] == 12.3
    assert levels['resistance']['formed_at'] == days[8]
    assert levels['resistance']['confirmed_at'] == days[10]
    assert levels['support']['distance_pct'] == pytest.approx((8.7 / 10 - 1) * 100)
    assert levels['resistance']['distance_pct'] == pytest.approx((12.3 / 10 - 1) * 100)
    # Preserve the prefix when later bars replace a same-kind candidate.
    market.ignore_cutoff = True
    assert period(build_research_technical(market, STOCK, days[10]), 'D')['levels'] == levels
    market.data[STOCK][11].update(open=13, high=13.3, low=12.7, close=13)
    broken = period(build_research_technical(market, STOCK, days[11]), 'D')['levels']
    assert broken['resistance'] is None
    assert broken['support']['kind'] == 'low'
    assert broken['support']['price'] != 12.3
    market.data[STOCK].pop(10)
    after_gap = period(build_research_technical(market, STOCK, days[12]), 'D')['levels']
    assert after_gap['support'] is after_gap['resistance'] is None


def test_current_week_action_cannot_compare_old_weekly_reference_with_new_raw_price():
    market = SyntheticMarket()
    market.events[STOCK] = [{'date': '2026-01-27'}]
    report = build_research_technical(market, STOCK, '2026-01-28')
    levels = period(report, 'W')['levels']
    assert levels['support'] is levels['resistance'] is None
    assert '公司行动' in levels['reason']
    assert not any(row.get('corporate_action') for row in report['frozen_charts_by_period']['W']['bars'])


def test_touching_support_at_the_close_is_not_a_strict_close_break():
    closes = [10, 10.5, 12, 11, 10, 9, 10, 11, 12, 11, 10, 11, 10.5]
    days = weekdays('2026-01-05', '2026-02-06')
    market = SyntheticMarket(closes, days)
    market.data[STOCK][11].update(open=8.7, close=8.7, high=9, low=8.4)
    level = period(build_research_technical(market, STOCK, days[11]), 'D')['levels']['support']
    assert level['price'] == 8.7 and level['distance_pct'] == 0
    market.data[STOCK][11].update(open=8.69, close=8.69, high=9, low=8.4)
    level = period(build_research_technical(market, STOCK, days[11]), 'D')['levels']['support']
    assert level is None


def test_insufficient_or_uncovered_calendar_and_unsupported_basis_stay_explicit():
    market = SyntheticMarket()
    report = build_research_technical(market, STOCK, '2030-01-02')
    assert all(row['state'] == 'unknown' and row['observed_date'] is None for row in report['periods'])
    assert all(row['state'] == 'unknown' for row in report['relative_strength']['windows'])
    assert any('市场日历未覆盖' in warning for warning in report['warnings'])
    with pytest.raises(ValueError, match='YYYY-MM-DD'):
        build_research_technical(market, STOCK, '2026-02-30')
    with pytest.raises(ValueError, match='未复权'):
        build_research_technical(market, STOCK, '2026-01-28', price_basis='forward')


def test_legacy_fixture_without_optional_instrument_or_index_does_not_fail_report():
    class Minimal:
        def calendar(self): return ['2026-01-05', '2026-01-06']
        def bars(self, symbol, as_of=None):
            if symbol == INDEX: raise KeyError(symbol)
            return [{'date': day, 'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 100, 'amount': None}
                    for day in self.calendar() if day <= as_of]
    result = build_research_technical(Minimal(), STOCK, '2026-01-06')
    assert period(result, 'D')['state'] == 'unknown'
    assert all(row['state'] == 'unknown' for row in result['relative_strength']['windows'])
    assert result['frozen_charts_by_period']['D']['bars'][0]['amount'] is None
    json.dumps(result, allow_nan=False)
