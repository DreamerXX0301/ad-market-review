"""Dated index windows, regime identities and honest cohort bounds."""
from copy import deepcopy
from datetime import date, timedelta
import math
from statistics import stdev

import pytest

from app.services.sentiment_context import index_environment, dated_regime, dated_st_state, cohort_group
from app.services.market_sentiment import describe_sentiment
from app.services import strategy_limits
from test_market_sentiment import rows
from test_market_ladder import Market, TODAY, DAYS, B, OTHER


CALENDAR = [(date(2026, 1, 1)+timedelta(days=i)).isoformat() for i in range(25)]
DAY = CALENDAR[-1]


def index_reference():
    return {'indices': {'000001.SH': [{'date': day, 'close': 100+i, 'source': 'fixture'}
                                    for i, day in enumerate(CALENDAR)]}}


def first(reference=None, calendar=None):
    return index_environment(reference or index_reference(), calendar or CALENDAR, DAY)['indices'][0]


def values(row): return {metric['key']: metric['value'] for metric in row['metrics']}


def test_index_uses_exact_21_session_prices_and_nonannualized_sample_volatility():
    result = first(); actual = values(result)
    assert result['state'] == 'strong'
    assert result['sample_count'] == 21 and result['unknown_count'] == 0
    assert actual['index_20d_return_pct'] == pytest.approx((124/104-1)*100)
    assert actual['index_ma20_distance_pct'] == pytest.approx((124/(sum(range(105, 125))/20)-1)*100)
    assert actual['index_20d_volatility_pct'] == pytest.approx(stdev([math.log(i+1)-math.log(i) for i in range(104, 124)])*100)
    future = index_reference(); future['indices']['000001.SH'].append({'date': '2099-01-01', 'close': 1e6})
    assert first(future) == result


@pytest.mark.parametrize('mode', ['missing_middle', 'missing_today', 'duplicate', 'zero', 'nan', 'source_conflict', 'calendar_duplicate'])
def test_index_window_never_falls_back_or_bridges_bad_sessions(mode):
    reference = index_reference(); calendar = list(CALENDAR)
    bars = reference['indices']['000001.SH']
    if mode == 'missing_middle': bars.pop(-5)
    elif mode == 'missing_today': bars.pop()
    elif mode == 'duplicate': bars.append(dict(bars[-3]))
    elif mode == 'zero': bars[-4]['close'] = 0
    elif mode == 'nan': bars[-4]['close'] = float('nan')
    elif mode == 'source_conflict': bars[-4]['source'] = 'other'
    else: calendar.append(calendar[-1])
    result = first(reference, calendar)
    assert result['state'] == 'insufficient'
    assert values(result)['index_20d_return_pct'] is None
    assert values(result)['index_20d_volatility_pct'] is None
    if mode == 'missing_today': assert values(result)['index_daily_return_pct'] is None


def test_regime_never_uses_current_name_or_daily_percentage_as_historical_st():
    identity = {'kind': 'stock', 'list_date': '2020-01-01', 'name': '普通公司', 'is_st': False}
    assert dated_regime(B, TODAY, identity, DAYS, None)[0] == 'unknown'
    assert dated_regime(B, TODAY, identity, DAYS, False)[0] == 'ordinary_10'
    assert dated_regime(B, TODAY, identity, DAYS, True)[0] == 'st'
    assert dated_regime('300001.SZ', TODAY, identity, DAYS, False)[0] == 'twenty_20'
    assert dated_regime(B, TODAY, {**identity, 'list_date': DAYS[-3]}, DAYS, False)[0] == 'listing'
    assert dated_regime(B, TODAY, {**identity, 'list_date': None}, DAYS, False)[0] == 'unknown'


def test_known_full_cohort_with_unknown_outcomes_has_bounds_not_midpoint():
    members = [{'promotion': True, 'return_pct': 1, 'current_identity_known': True}]*3
    members += [{'promotion': False, 'return_pct': -1, 'current_identity_known': True}]*5
    members += [{'promotion': None, 'return_pct': None, 'current_identity_known': False}]*2
    result = cohort_group('ordinary_10', members, source_complete=True, identity_complete=True)
    assert result['promotion_bounds_pct'] == {'lower': 30, 'upper': 50}
    assert values(result)['promotion_rate_pct'] is None
    assert result['sample_count'] == 8 and result['unknown_count'] == 2
    assert result['positive_ratio_bounds_pct'] == {'lower': 30, 'upper': 50}
    assert cohort_group('ordinary_10', members, source_complete=False, identity_complete=True)['promotion_bounds_pct'] is None
    assert cohort_group('ordinary_10', members, source_complete=True, identity_complete=False)['promotion_bounds_pct'] is None


def test_context_exposes_real_fetch_time_without_forging_first_known(monkeypatch):
    market = Market(); monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})
    for day, record in market.ref['_supplement']['annual_pools'].items():
        record['fetched_at'] = '2026-10-21T21:00:00+08:00'
        for event in record['items']: event['is_st'] = False
    # B is not in today's up-pool, so do not pretend its T-day identity is known.
    result = describe_sentiment(market, market.ref, rows(market), TODAY)
    proof = result['provenance']
    assert proof['mode'] == 'retained_retrospective'
    assert proof['first_known_at'] is None and proof['known_time_complete'] is False
    assert {record['fetched_at'] for record in proof['source_records']} == {'2026-10-21T21:00:00+08:00'}
    short = result['short_term']; group = next(row for row in short['groups'] if row['id'] == 'ordinary_10')
    assert group['observed_count'] == 2 and group['sample_count'] == 1
    assert group['current_identity_unknown_count'] == 1
    assert group['promotion_bounds_pct'] == {'lower': 50, 'upper': 100}
    assert values(group)['promotion_rate_pct'] is None
    assert len(proof['input_fingerprint']) == 64
    before = deepcopy(market.ref)
    describe_sentiment(market, market.ref, rows(market), TODAY)
    assert market.ref == before


def test_missing_or_conflicting_p_identity_blocks_group_denominator(monkeypatch):
    market = Market(); monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})
    # Only OTHER has explicit P identity: B could also be ordinary, so OTHER's
    # apparent 100% rate cannot be called the full ordinary-group rate.
    for record in market.ref['_supplement']['annual_pools'].values():
        for event in record['items']:
            if event.get('instrument_id', event.get('thscode')) == OTHER: event['is_st'] = False
    result = describe_sentiment(market, market.ref, rows(market), TODAY)
    group = next(row for row in result['short_term']['groups'] if row['id'] == 'ordinary_10')
    assert group['observed_count'] == 1 and group['identity_complete'] is False
    assert group['promotion_bounds_pct'] is None
    assert result['short_term']['identity_complete'] is False


def test_same_six_digit_index_does_not_make_a_stock_pool_identity_ambiguous(monkeypatch):
    market = Market(); monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})
    # 000001.SZ is a stock; the same six digits of the SSE index are not a
    # second possible security in an A-share closing-limit pool.
    market.master.append({'instrument_id': '000001.SH', 'kind': 'index', 'name': '合成测试指数'})
    result = describe_sentiment(market, market.ref, rows(market), TODAY)['short_term']
    assert result['cohort_candidate_count'] == result['observed_count'] == 2
    assert result['previous_unverified_count'] == 0
    assert result['cohort_source_complete'] is True
    assert values(result)['promotion_rate_pct'] == 50
    assert all('000001.SH' not in symbols for symbols, *_ in market.history_calls)


def test_genuine_two_stock_six_digit_collision_remains_unknown(monkeypatch):
    market = Market(); monkeypatch.setattr(strategy_limits, 'load_supplement', lambda _: {})
    market.master.append({'instrument_id': '000001.SH', 'kind': 'stock', 'name': '合成第二股票', 'list_date': '2020-01-01'})
    result = describe_sentiment(market, market.ref, rows(market), TODAY)['short_term']
    # The formal 000001.SZ event is still verified as itself, while the raw
    # six-digit source witness remains unresolvable rather than picking one.
    assert result['observed_count'] == 2
    assert result['cohort_candidate_count'] == 3 and result['previous_unverified_count'] == 1
    assert result['cohort_source_complete'] is False
    assert values(result)['promotion_rate_pct'] is None
    assert all('000001.SH' not in symbols for symbols, *_ in market.history_calls)


def test_dated_st_lookup_does_not_borrow_another_full_identity_with_same_code():
    class Proof:
        def _records(self, day, kind):
            if kind == 'down': return []
            stock = {'instrument_id': '000688.SZ', 'date': day, 'is_st': False, 'last_price': 10}
            index = {'instrument_id': '000688.SH', 'date': day, 'is_st': True, 'last_price': 10}
            return [({'date': day}, {'000688': [stock, index]}, 'fixture-source', 'fixture-path', True, True)]
    assert dated_st_state(Proof(), '000688.SZ', TODAY, {'close': 10}, {}) is False
