"""Shared dated detail statistics reuse bounded local member calculations."""
from copy import deepcopy

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.services import stock_summary as summary
from test_topic_member_metrics import MetricMarket, complete_year, source_pool, A, B, C, D, E
from app.domain.conditions import metrics_for


@pytest.fixture
def local(monkeypatch):
    market = MetricMarket()
    monkeypatch.setattr(market, 'status', lambda: {'ready': True, 'snapshot_id': 'metric-fixture',
        'data_begin': market.days[0], 'data_end': market.day})
    monkeypatch.setattr(market, 'panel', lambda *a: pytest.fail('summary must not load the full panel'))
    monkeypatch.setattr(summary, 'get_market', lambda: market)
    document = complete_year(market, {market.days[-6]: [A, B]})
    document['annual_pools'][market.days[-1]] = source_pool(market, market.days[-1], [A, B])
    monkeypatch.setattr(summary, 'load_supplement', lambda *a: deepcopy(document))
    from app.adapters.providers import ProviderClient
    monkeypatch.setattr(ProviderClient, 'request', lambda *a: pytest.fail('summary never requests a source'))
    with summary._LOCK: summary._CACHE.clear()
    return market, document


def test_common_summary_preserves_exact_count_latest_and_asof_without_future(local):
    market, _ = local
    result = summary.get_summary(A, market.day)
    assert result['as_of'] == result['data_date'] == result['quote_date'] == market.day
    assert result['board'] == 'main' and result['board_label'] == '主板'
    assert result['limit_up_ytd'] == result['annual_limit_up_count'] == 1
    assert result['limit_up_ytd_observed'] == result['annual_limit_up_count_observed'] == 1
    assert result['last_limit_up_date'] == result['latest_limit_up_date'] == market.days[-6]
    assert result['last_limit_up_complete'] is True
    assert result['sessions_since_limit_up'] == market.days.index(market.day)-market.days.index(market.days[-6])
    assert result['return_ytd_pct'] is not None
    assert result['return_30d_pct'] == result['change_30d_pct']
    assert result['return_30d_coverage']['start'] == market.days[market.days.index(market.day)-30]
    assert result['change_pct'] == metrics_for(market.data[A], market.day, market.days)['change_pct']
    assert result['change_pct_reason'] is None
    assert result['change_pct_date'] == market.day
    assert result['change_pct_previous_date'] == market.days[market.days.index(market.day)-1]
    assert market.calls['panel'] == 0 and market.calls['actions'] == 0
    assert market.calls['history_for_instruments'] == 2
    assert all(request['symbols'] == {A} and request['end'] == market.day for request in market.history_requests)
    assert market.history_requests[0]['start'] == result['change_pct_previous_date']


@pytest.mark.parametrize('case', ['previous_missing', 'previous_duplicate', 'current_duplicate',
    'current_invalid', 'previous_invalid', 'current_action', 'missing_actions', 'malformed_actions'])
def test_daily_change_never_falls_back_to_older_or_incomparable_quotes(local, monkeypatch, case):
    from test_topic_member_metrics import action
    market, _ = local
    previous = market.days[market.days.index(market.day)-1]
    current = next(row for row in market.data[A] if row['date'] == market.day)
    old = next(row for row in market.data[A] if row['date'] == previous)
    if case == 'previous_missing': market.data[A].remove(old)
    if case == 'previous_duplicate': market.data[A].append(deepcopy(old))
    if case == 'current_duplicate': market.data[A].append(deepcopy(current))
    if case == 'current_invalid': current['close'] = float('nan')
    if case == 'previous_invalid': old['close'] = 0
    if case == 'current_action': market.events[A] = [action(market.day)]
    if case == 'missing_actions': monkeypatch.setattr(market, 'actions_for_instruments', lambda symbols: None)
    if case == 'malformed_actions': market.events[A] = [{'date': None}]
    result = summary.get_summary(A, market.day)
    assert result['change_pct'] is None and result['change_pct_reason']
    assert result['change_pct_date'] == market.day and result['change_pct_previous_date'] == previous
    assert market.calls['history_for_instruments'] == 2


def test_daily_change_is_dated_and_does_not_use_future_quotes_or_returns(local):
    market, _ = local
    day = market.days[-7]
    expected = metrics_for(market.data[A], day, market.days)['change_pct']
    next(row for row in market.data[A] if row['date'] > day)['close'] = 999
    result = summary.get_summary(A, day)
    assert result['change_pct'] == expected
    assert result['change_pct_date'] == result['quote_date'] == day
    assert result['change_pct_previous_date'] == market.days[market.days.index(day)-1]
    assert all(request['end'] == day for request in market.history_requests)


def test_daily_change_first_retained_market_day_has_no_previous_reference(local):
    market, _ = local
    result = summary.get_summary(A, market.days[0])
    assert result['change_pct'] is None and result['change_pct_reason']
    assert result['change_pct_previous_date'] is None


def test_historical_summary_excludes_later_positive_even_when_source_retains_it(local):
    market, _ = local
    day = market.days[-7]
    result = summary.get_summary(A, day)
    assert result['annual_limit_up_count'] == 0
    assert result['latest_limit_up_date'] is None
    assert result['quote_date'] == day
    assert result['return_30d_coverage']['end'] == day


def test_missing_pool_is_unknown_not_zero_and_observed_is_only_lower_bound(local):
    market, document = local
    del document['annual_pools'][market.day]
    result = summary.get_summary(A, market.day)
    assert result['limit_up_ytd'] is None and result['annual_limit_up_count'] is None
    assert result['annual_limit_up_count_observed'] == 1
    assert result['latest_limit_up_complete'] is False
    assert result['latest_limit_up_date'] == market.days[-6]
    assert result['annual_limit_up_count_coverage']['complete'] is False
    assert result['latest_limit_up_reason']


def test_missing_quote_does_not_reuse_previous_quote_or_return(local):
    market, _ = local
    result = summary.get_summary(E, market.day)
    assert result['board'] == 'beijing' and result['board_label'] == '北交所'
    assert result['quote_date'] is None and result['quote_available'] is False
    assert result['close'] is None and result['return_ytd_pct'] is None and result['return_30d_pct'] is None


@pytest.mark.parametrize('symbol,board,label', [(A, 'main', '主板'), (C, 'chinext', '创业板'),
    (D, 'star', '科创板'), (E, 'beijing', '北交所')])
def test_classification_uses_real_identity_once_without_recomputing_members(local, symbol, board, label):
    market, _ = local
    before = market.calls.copy()
    original = {'instrument_id': symbol, 'limit_up_ytd': None, 'limit_up_ytd_observed': 0,
                'return_ytd_pct': 12, 'change_30d_pct': 3}
    result = summary.enrich_members([original], market.master, market.day)[0]
    assert result['board'] == board and result['board_label'] == label
    assert result['annual_limit_up_count'] is None and result['annual_limit_up_count_observed'] == 0
    assert result['return_30d_pct'] == 3 and result['annual_return_pct'] == 12
    assert market.calls == before and 'board' not in original


@pytest.mark.parametrize('day', ['2026-10-30', '2025-01-01', '2026-02-30'])
def test_bad_or_unpublished_observation_date_rejects_before_history(local, day):
    market, _ = local
    with pytest.raises(HTTPException) as caught: summary.get_summary(A, day)
    assert caught.value.status_code == 422 and market.calls['history_for_instruments'] == 0


def test_retained_future_calendar_is_not_permission_to_read_unpublished_day(local):
    market, _ = local
    assert market.days[-1] in market.calendar() and market.days[-1] > market.day
    with pytest.raises(HTTPException): summary.get_summary(A, market.days[-1])
    assert market.calls['history_for_instruments'] == 0


def test_index_unknown_identity_and_ambiguous_short_codes_are_rejected(local):
    market, _ = local
    for symbol in ('000001.SH', '999999.SH', '885728.TI'):
        with pytest.raises(HTTPException) as caught: summary.get_summary(symbol, market.day)
        assert caught.value.status_code == 404
    assert summary.get_summary('920001', market.day)['instrument_id'] == E
    assert market.calls['history_for_instruments'] == 2


def test_summary_generation_switch_does_not_return_mixed_result(local, monkeypatch):
    market, _ = local
    original = summary.summary_for
    def changed(*args):
        result = original(*args)
        monkeypatch.setattr(market, 'status', lambda: {'ready': True, 'snapshot_id': 'replacement', 'data_begin': market.days[0], 'data_end': market.day})
        return result
    monkeypatch.setattr(summary, 'summary_for', changed)
    with pytest.raises(HTTPException) as caught: summary.get_summary(A, market.day)
    assert caught.value.status_code == 409


def test_api_summary_returns_dated_stats_and_has_no_provider_dependency(local):
    market, _ = local
    from app.routers.platform import router
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        response = client.get('/api/v1/instruments/'+A+'/summary', params={'as_of': market.day})
    assert response.status_code == 200
    body = response.json()
    assert body['data_date'] == market.day and body['annual_limit_up_count'] == 1
    assert body['return_30d_pct'] == body['change_30d_pct']


def test_unreliable_market_never_keeps_summary_in_lru(local):
    market, _ = local
    summary.get_summary(A, market.day)
    assert len(summary._CACHE) == 0


def test_fixed_generation_summary_cache_is_copied_and_invalidated_by_source(local, tmp_path, monkeypatch):
    from app.analytics.market import MarketData
    fake, document = local
    root = tmp_path/'fixed-root'; folder = root/'published'/'fixed-summary'
    folder.mkdir(parents=True); (folder/'manifest.json').write_text('{}')
    market = MarketData(root, snapshot_id='fixed-summary')
    monkeypatch.setattr(market, 'status', lambda: {'ready': True, 'snapshot_id': 'fixed-summary',
        'path': str(folder), 'data_begin': fake.days[0], 'data_end': fake.day})
    for method in ('calendar', 'instruments', 'history_for_instruments', 'actions_for_instruments', 'reference'):
        monkeypatch.setattr(market, method, getattr(fake, method))
    monkeypatch.setattr(summary, 'get_market', lambda: market)
    first = summary.get_summary(A, fake.day)
    original = deepcopy(first)
    first['return_ytd_coverage']['end'] = 'caller-mutated'
    function = summary.topic_member_metrics.member_metrics
    monkeypatch.setattr(summary.topic_member_metrics, 'member_metrics', lambda *a: pytest.fail('warm summary must reuse computed statistics'))
    assert summary.get_summary(A, fake.day) == original
    monkeypatch.setattr(summary.topic_member_metrics, 'member_metrics', function)
    document['annual_pools'][fake.day] = source_pool(fake, fake.day, [A, B])
    after = summary.get_summary(A, fake.day)
    assert after['annual_limit_up_count'] == 2
    assert after['latest_limit_up_date'] == fake.day and after['sessions_since_limit_up'] == 0
    assert original['annual_limit_up_count'] == 1
    assert len(summary._CACHE) == 2


def test_summary_cache_has_small_lru_bound(local, tmp_path, monkeypatch):
    from app.analytics.market import MarketData
    fake, _ = local
    root = tmp_path/'bounded-root'; folder = root/'published'/'bounded-summary'
    folder.mkdir(parents=True); (folder/'manifest.json').write_text('{}')
    market = MarketData(root, snapshot_id='bounded-summary')
    monkeypatch.setattr(market, 'status', lambda: {'ready': True, 'snapshot_id': 'bounded-summary',
        'path': str(folder), 'data_begin': fake.days[0], 'data_end': fake.day})
    for method in ('calendar', 'instruments', 'history_for_instruments', 'actions_for_instruments', 'reference'):
        monkeypatch.setattr(market, method, getattr(fake, method))
    monkeypatch.setattr(summary, 'MAX_SUMMARIES', 2)
    for symbol in (A, B, C): summary.summary_for(market, symbol, fake.day)
    assert len(summary._CACHE) == 2
