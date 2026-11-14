"""Offline, fixed-generation caches over synthetic dated metric fixtures."""
from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from app import core
from app.services import intraday_monitor_metadata as service
from test_topic_member_metrics import MetricMarket, complete_year, source_pool, close_at, A, B, C, D, E


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    market = MetricMarket()
    monkeypatch.setattr(market, 'status', lambda: dict(snapshot_id='metric-fixture', data_end=market.day))
    monkeypatch.setattr(market, 'verify_condition_sources',
                        lambda: ('fixture-price-proof', core.digest([market.data, market.events, market.reference_data])), raising=False)
    monkeypatch.setattr(market, 'panel', lambda *_: pytest.fail('metadata must not build a decorated market panel'))
    from app.adapters.providers import ProviderClient
    monkeypatch.setattr(ProviderClient, 'request', lambda *_args, **_kw: pytest.fail('metadata preparation is local only'))
    document = complete_year(market, {market.days[-6]: [A, B]})
    monkeypatch.setattr(service, 'load_supplement_with_fingerprint',
                        lambda *_args, **_kw: (deepcopy(document), core.digest(document)))
    head = dict(business_date=market.day, market_snapshot_id='metric-fixture', primary_snapshot_id='primary-fixture')
    return market, head, document


def test_cold_get_returns_pending_or_unknown_without_market_reads(prepared, monkeypatch):
    market, head, _ = prepared
    monkeypatch.setattr(market, 'history_for_instruments', lambda *_a, **_kw: pytest.fail('GET must never prepare history'))
    with core.connect() as connection:
        pool = service.read_gainers_pool(connection, head)
        assert pool['state'] == 'pending' and pool['reason'] and pool['items'] == []
        assert service.read_metadata(connection, head) == {}
    assert not market.calls['panel']


def test_top_pool_reuses_price_rank_without_topics_or_annual_decoration(prepared, monkeypatch):
    market, head, _ = prepared
    calls = []
    original = service.read_rank_block
    def rank_block(name, day, proof, selected, build):
        calls.append(name)
        return original(name, day, proof, selected, build)
    monkeypatch.setattr(service, 'read_rank_block', rank_block)
    monkeypatch.setattr(service.metrics, 'limit_metrics', lambda *_a, **_kw: pytest.fail('ranking must not decorate annual events'))
    result = service.ensure_gainers_pool(market, head)
    assert result['state'] == 'ready' and result['basis_date'] == market.day
    assert len(result['items']) <= 1000 and calls == ['gainers-price-rank-v1']
    assert [row['rank'] for row in result['items']] == list(range(1, len(result['items']) + 1))
    baseline = market.days[market.days.index(market.day) - 30]
    for row in result['items']:
        expected = (close_at(market, row['instrument_id'], market.day) / close_at(market, row['instrument_id'], baseline) - 1) * 100
        assert row['change_30d_pct'] == pytest.approx(expected)
        assert set(row) == {'instrument_id', 'rank', 'change_30d_pct'}
    assert C not in {row['instrument_id'] for row in result['items']}
    assert E not in {row['instrument_id'] for row in result['items']}
    history_calls = market.calls['history_for_instruments']
    assert service.ensure_gainers_pool(market, head) == result
    assert market.calls['history_for_instruments'] == history_calls
    with core.connect() as connection:
        assert service.read_gainers_pool(connection, head) == result
        assert service.read_gainers_pool(connection, {**head, 'market_snapshot_id': 'different'})['state'] == 'pending'


def test_empty_or_short_price_coverage_is_unknown_not_successful_zero(prepared):
    market, head, _ = prepared
    market.day = market.days[4]
    head['business_date'] = market.day
    result = service.ensure_gainers_pool(market, head)
    assert result['state'] == 'unknown' and result['items'] == [] and result['reason']
    with core.connect() as connection:
        assert service.read_gainers_pool(connection, head)['state'] == 'unknown'


def test_top_pool_keeps_exact_first_1000_real_ranks(prepared):
    market, head, _ = prepared
    baseline = market.days[market.days.index(market.day) - 30]
    market.master, market.data = {}, {}
    for index in range(1100):
        symbol = f'{300000 + index:06}.SZ'
        market.master[symbol] = dict(instrument_id=symbol, code=symbol[:6], kind='stock', list_date='2020-01-01')
        start, end = 10 + index / 100, 20 + index / 100
        market.data[symbol] = [dict(instrument_id=symbol, date=day, close=price, open=price,
                                   high=price, low=price, volume=100, amount=price * 100)
                               for day, price in ((baseline, start), (market.day, end))]
    result = service.ensure_gainers_pool(market, head)
    assert result['state'] == 'ready' and len(result['items']) == 1000
    assert result['known_count'] == 1100
    assert result['items'][0]['instrument_id'] == '300000.SZ'
    assert result['items'][-1]['instrument_id'] == '300999.SZ'
    assert [item['rank'] for item in result['items']] == list(range(1, 1001))


def test_complete_rolling_year_and_thirty_session_counts_are_distinct_from_ytd(prepared):
    market, head, document = prepared
    market.days = []
    cursor = date(2025, 1, 1)
    while cursor <= date(2026, 11, 13):
        if cursor.weekday() < 5:
            market.days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    market.day = market.days[-1]
    head['business_date'] = market.day
    market.master = {symbol: dict(instrument_id=symbol, code=symbol[:6], name='fixture', kind='stock', list_date='2020-01-01') for symbol in (A, B)}
    market.data = {symbol: [dict(instrument_id=symbol, date=day, close=10 + index / 100,
                                open=10 + index / 100, high=11 + index / 100, low=9 + index / 100,
                                volume=1000, amount=10000, corporate_action=False)
                           for index, day in enumerate(market.days)] for symbol in (A, B)}
    old, recent = '2025-12-15', market.days[-5]
    start = service.rolling_start(market.day)
    document.clear()
    document['annual_pools'] = {day: source_pool(market, day, [A, B] if day in (old, recent) else [B])
                                for day in market.days if start <= day <= market.day}
    result = service.ensure_metadata(market, head, [A])[A]
    assert result['limit_up_1y'] == result['limit_up_1y_observed'] == 2
    assert result['limit_up_1y_complete'] and result['limit_up_1y_reason'] is None
    assert result['limit_up_30d'] == result['limit_up_30d_observed'] == 1
    assert result['limit_up_ytd'] == result['limit_up_ytd_observed'] == 1
    assert result['last_limit_up_date'] == recent and result['last_limit_up_complete']


def test_metadata_reconciles_existing_returns_boards_and_confirmed_counts(prepared):
    market, head, _ = prepared
    result = service.ensure_metadata(market, head, [A, B, C, D, E])
    assert result[A]['board'] == 'main' and result[A]['board_label'] == '主板'
    assert result[C]['board'] == 'chinext' and result[C]['board_label'] == '创业板'
    assert result[D]['board'] == 'star' and result[D]['board_label'] == '科创板'
    assert result[E]['board'] == 'beijing' and result[E]['board_label'] == '北交所'
    previous = market.days[market.days.index(market.day) - 1]
    thirty = market.days[market.days.index(market.day) - 30]
    assert result[A]['change_pct'] == pytest.approx((close_at(market, A, market.day) / close_at(market, A, previous) - 1) * 100)
    assert result[A]['return_ytd_pct'] == pytest.approx((close_at(market, A, market.day) / close_at(market, A, '2025-12-31') - 1) * 100)
    assert result[A]['change_30d_pct'] == result[A]['return_30d_pct'] == pytest.approx((close_at(market, A, market.day) / close_at(market, A, thirty) - 1) * 100)
    assert result[A]['limit_up_ytd'] == result[A]['annual_limit_up_count'] == 1
    assert result[A]['limit_up_30d'] == result[A]['limit_up_30d_observed'] == 1
    assert result[A]['last_limit_up_date'] == market.days[-6]
    assert result[A]['sessions_since_limit_up'] == market.days.index(market.day) - market.days.index(market.days[-6])
    assert result[A]['limit_up_1y'] is None and result[A]['limit_up_1y_complete'] is False
    assert result[A]['limit_up_1y_reason'] and result[A]['limit_up_1y_observed'] == 1
    assert result[C]['return_ytd_pct'] is None and result[C]['change_30d_pct'] is None
    assert result[E]['change_pct'] is None and result[E]['change_pct_reason']
    assert all(not {'topics', 'industries', 'current_topics'}.intersection(row) for row in result.values())
    with core.connect() as connection:
        assert service.read_metadata(connection, head) == result
    before = market.calls.copy()
    assert service.ensure_metadata(market, head, [E, D, C, B, A]) == result
    assert market.calls['history_for_instruments'] == before['history_for_instruments']


def test_missing_pool_remains_unknown_and_source_fingerprint_invalidates_cache(prepared):
    market, head, document = prepared
    first = service.ensure_metadata(market, head, [A])
    assert first[A]['limit_up_30d'] == 1
    del document['annual_pools'][market.day]
    result = service.ensure_metadata(market, head, [A])
    assert result[A]['limit_up_30d'] is None and result[A]['limit_up_ytd'] is None
    assert result[A]['limit_up_30d_observed'] == result[A]['limit_up_ytd_observed'] == 1
    assert result[A]['limit_up_30d_reason'] and result[A]['limit_up_ytd_reason']
    assert result[A]['input_sha256'] != first[A]['input_sha256']


def test_future_events_and_quotes_do_not_enter_fixed_observation(prepared):
    market, head, document = prepared
    first = service.ensure_metadata(market, head, [A])
    future = market.days[-1]
    next(row for row in market.data[A] if row['date'] == future)['close'] = 9999
    document['annual_pools'][future] = source_pool(market, future, [A])
    second = service.ensure_metadata(market, head, [A])
    for field in ('change_pct', 'change_30d_pct', 'return_ytd_pct', 'limit_up_30d', 'limit_up_ytd', 'last_limit_up_date'):
        assert first[A][field] == second[A][field]
    assert all(request['end'] <= market.day for request in market.history_requests)


def test_corrupt_cache_head_change_and_sql_publication_receipt_return_unknown(prepared):
    market, head, _ = prepared
    service.ensure_metadata(market, head, [A])
    service.ensure_gainers_pool(market, head)
    with core.connect() as connection:
        assert service.read_metadata(connection, {**head, 'business_date': market.days[-4]}) == {}
        connection.execute('''INSERT INTO platform_setting VALUES(?,?,1)''',
                           ('sync-supplement-completion:metric-fixture:' + market.day, '{"new_source":true}'))
        assert service.read_metadata(connection, head) == {}
        assert service.read_gainers_pool(connection, head)['state'] == 'pending'
    service.ensure_metadata(market, head, [A])
    with core.connect() as connection:
        value = json.loads(connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (service.METADATA_KEY,)).fetchone()[0])
        value['items'][A]['change_pct'] = 999
        connection.execute('UPDATE platform_setting SET value_json=? WHERE key=?', (json.dumps(value), service.METADATA_KEY))
        assert service.read_metadata(connection, head) == {}


def test_guard_or_missing_verifiable_source_never_publishes_ready_cache(prepared, monkeypatch):
    market, head, _ = prepared
    class Stop(Exception):
        pass
    def guard():
        raise Stop()
    with pytest.raises(Stop):
        service.ensure_gainers_pool(market, head, guard)
    with pytest.raises(Stop):
        service.ensure_metadata(market, head, [A], guard)
    monkeypatch.setattr(market, 'verify_condition_sources', lambda: None)
    with pytest.raises(ValueError, match='SHA'):
        service.ensure_gainers_pool(market, head)
    with core.connect() as connection:
        assert service.read_gainers_pool(connection, head)['state'] == 'pending'
        assert service.read_metadata(connection, head) == {}
