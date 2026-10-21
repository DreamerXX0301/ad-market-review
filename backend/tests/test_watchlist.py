"""Personal watchlists freeze origin evidence without changing strategy pools."""
from copy import deepcopy

import pytest

from test_workspace import ws, make_strategy, add_one, advance
from app import core
from app.routers.watchlist import router
from app.services import watchlist
from app.services.tracking_quotes import clear_tracking_quote_cache


@pytest.fixture
def personal(ws, monkeypatch):
    market, client = ws
    client.app.include_router(router)
    monkeypatch.setattr(watchlist, 'get_market', lambda: market)
    monkeypatch.setattr(market,'history_for_instruments',lambda symbols,start=None,end=None:
                        {symbol:[deepcopy(row) for row in market.data.get(symbol,[]) if (start is None or row['date']>=start) and (end is None or row['date']<=end)] for symbol in symbols},raising=False)
    monkeypatch.setattr(market,'actions_for_instruments',lambda symbols:{symbol:[] for symbol in symbols},raising=False)
    return market, client


def add(client, symbol='600001.SH', **source):
    return client.post('/api/v1/watchlist', json={'instrument_id': symbol, **source})


def test_tracking_origin_is_frozen_and_ignored_client_prices_never_replace_it(personal):
    market, client = personal
    strategy = make_strategy(client)
    _, original = add_one(client, strategy)
    result = add(client, tracking_round_id=original['round_id'], event_close=9999,
                 tracking_entry_date='2020-01-01', source_name='伪造客户端来源')
    assert result.status_code == 200, result.text
    saved = result.json()['item']
    assert saved['tracking_entry_date'] == original['entry_business_date']
    assert saved['event_close'] == original['entry_price']
    assert saved['source_name'] == strategy['name']
    assert saved['source_kind'] == 'tracking'
    assert saved['event_price_basis'] == 'none'
    advance(market, 71)
    again = add(client, tracking_round_id=original['round_id'], observed_date=market.days[71])
    assert again.json()['already_exists'] is True
    assert again.json()['item']['added_business_date'] == saved['added_business_date']
    assert again.json()['item']['observed_date'] == saved['observed_date']
    assert again.json()['item']['created_at_ms'] == saved['created_at_ms']


def test_manual_sources_have_no_event_baseline_and_duplicate_does_not_upgrade_them(personal):
    _, client = personal
    manual = add(client, source_kind='sector', source_name='测试行业', source_id='fixture-sector')
    assert manual.status_code == 200
    row = manual.json()['item']
    assert row['event_close'] is None and row['tracking_entry_date'] is None
    assert row['event_baseline_reason']
    strategy = make_strategy(client)
    _, original = add_one(client, strategy)
    duplicate = add(client, tracking_round_id=original['round_id'])
    assert duplicate.json()['already_exists'] is True
    assert duplicate.json()['item']['source_kind'] == 'sector'
    assert duplicate.json()['item']['event_close'] is None
    assert client.get('/api/v1/watchlist/ids').json()['instrument_ids'] == ['600001.SH']


def test_remove_is_idempotent_and_never_modifies_tracking(personal):
    _, client = personal
    strategy = make_strategy(client)
    _, original = add_one(client, strategy)
    add(client, tracking_round_id=original['round_id'])
    removed = client.delete('/api/v1/watchlist/600001.SH')
    assert removed.json()['removed'] is True
    assert client.delete('/api/v1/watchlist/600001.SH').json()['removed'] is False
    kept = client.get('/api/v1/tracking-rounds/'+original['round_id']).json()
    assert kept['status'] == 'tracking'
    assert kept['entry_business_date'] == original['entry_business_date']
    assert kept['entry_price'] == original['entry_price']
    assert client.get('/api/v1/watchlist').json()['items'] == []


def test_list_batches_quotes_and_reuses_generation_cache_without_full_history(personal, monkeypatch, tmp_path):
    market, client = personal
    for symbol in market.data:
        assert add(client, symbol).status_code == 200
    reads = []
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'watchlist-fixture'}, raising=False)
    monkeypatch.setattr(market, 'data_dir', tmp_path, raising=False)
    def batch(symbols, start=None, end=None):
        reads.append((symbols, start, end))
        return {symbol: [deepcopy(bar) for bar in market.data[symbol] if start <= bar['date'] <= end] for symbol in symbols}
    monkeypatch.setattr(market, 'history_for_instruments', batch, raising=False)
    monkeypatch.setattr(market, 'actions_for_instruments', lambda symbols: {symbol: [] for symbol in symbols}, raising=False)
    monkeypatch.setattr(market, 'bars', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('watchlist cannot load full history')))
    clear_tracking_quote_cache()
    first = client.get('/api/v1/watchlist').json()
    assert first['total'] == 2 and len(reads) == 1
    assert set(reads[0][0]) == set(market.data)
    assert reads[0][1] == market.days[69] and reads[0][2] == market.entry
    assert all(row['today_change_pct'] is not None for row in first['items'])
    assert client.get('/api/v1/watchlist').json() == first and len(reads) == 1
    filtered = client.get('/api/v1/watchlist', params={'query': '600001'}).json()
    assert filtered['total'] == 2 and filtered['matched_count'] == 1
    assert filtered['items'][0]['instrument_id'] == '600001.SH'


def test_invalid_stock_origin_date_and_mismatched_round_are_rejected(personal):
    market, client = personal
    assert add(client, 'bad').status_code == 422
    assert add(client, source_kind=[]).status_code == 422
    assert add(client, source_kind='tracking').status_code == 422
    assert add(client, observed_date='2026-02-30').status_code == 422
    assert add(client, observed_date='2030-01-01').status_code == 422
    assert add(client, '999999.SH').status_code == 422
    strategy = make_strategy(client)
    _, original = add_one(client, strategy)
    assert add(client, '000001.SZ', tracking_round_id=original['round_id']).status_code == 422
    assert add(client, tracking_round_id='missing-round').status_code == 404
    assert client.get('/api/v1/watchlist/ids').json()['total'] == 0


def test_watchlist_starts_empty_and_does_not_enqueue_tasks(personal):
    _, client = personal
    with core.connect() as connection:
        jobs = connection.execute('SELECT count(*) FROM job').fetchone()[0]
    assert client.get('/api/v1/watchlist').json()['items'] == []
    assert add(client).status_code == 200
    client.get('/api/v1/watchlist')
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == jobs
        assert connection.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 0


def test_current_industry_and_concept_memberships_are_batched_searchable_and_not_frozen_origins(personal, monkeypatch):
    market, client = personal
    strategy = make_strategy(client)
    _, original = add_one(client, strategy)
    assert add(client, tracking_round_id=original['round_id']).status_code == 200
    assert add(client, '000001.SZ', source_kind='sector', source_name='原收藏板块').status_code == 200
    catalog = [
        {'topic_id': 'concept-a', 'name': '合成概念甲', 'kind': 'concept', 'observed_date': market.days[69], 'members_complete': True, 'instrument_ids': ['600001.SH']},
        {'topic_id': 'concept-b', 'name': '合成概念乙', 'kind': 'concept', 'observed_date': market.entry, 'members_complete': True, 'instrument_ids': ['600001.SH']},
        {'topic_id': 'concept-c', 'name': '折叠后仍可搜索概念', 'kind': 'concept', 'observed_date': market.entry, 'members_complete': False, 'instrument_ids': ['600001.SH']},
        {'topic_id': 'industry-a', 'name': '合成行业', 'kind': 'industry', 'observed_date': market.days[68], 'members_complete': True, 'instrument_ids': ['600001.SH', '000001.SZ']},
    ]
    monkeypatch.setattr(market, 'topics', lambda: catalog)
    assert client.post('/api/v1/topics/initialize', json={}).status_code == 200
    calls = []
    original_reader = watchlist.topic_members._read_state
    def batch(connection, day, provider_catalog):
        statements = []
        connection.set_trace_callback(statements.append)
        try:
            result = original_reader(connection, day, provider_catalog)
        finally:
            connection.set_trace_callback(None)
        calls.append(statements)
        return result
    monkeypatch.setattr(watchlist.topic_members, '_read_state', batch)
    listed = client.get('/api/v1/watchlist').json()
    assert len(calls) == 1
    assert len([sql for sql in calls[0] if sql.lstrip().upper().startswith('SELECT')]) == 2, 'one definitions query + one override query; not one query per stock/topic'
    row = next(item for item in listed['items'] if item['instrument_id'] == '600001.SH')
    assert len(row['current_topics']) == 4
    assert [tag['name'] for tag in row['current_industries']] == ['合成行业']
    assert row['current_topics_observed_date'] == market.entry
    assert next(tag for tag in row['current_topics'] if tag['name'] == '合成行业')['source_observed_date'] == market.days[68]
    assert row['source_name'] == strategy['name'] and row['event_close'] == original['entry_price']
    for search, expected in [('折叠后仍可搜索概念', 1), ('合成行业', 2), ('原收藏板块', 1)]:
        assert client.get('/api/v1/watchlist', params={'query': search}).json()['matched_count'] == expected
    definitions = client.get('/api/v1/topics').json()['items']
    concept = next(item for item in definitions if item['name'] == '折叠后仍可搜索概念')
    assert client.post('/api/v1/topics/'+concept['topic_id']+'/members', json={'instrument_id': '600001.SH', 'action': 'exclude'}).status_code == 200
    assert client.get('/api/v1/watchlist', params={'query': '折叠后仍可搜索概念'}).json()['matched_count'] == 0
    after = next(item for item in client.get('/api/v1/watchlist').json()['items'] if item['instrument_id'] == '600001.SH')
    assert after['tracking_entry_date'] == original['entry_business_date'] and after['event_close'] == original['entry_price']
    industry = next(item for item in definitions if item['name'] == '合成行业')
    assert client.patch('/api/v1/topics/'+industry['topic_id'], json={'archived': True}).status_code == 200
    assert client.get('/api/v1/watchlist', params={'query': '合成行业'}).json()['matched_count'] == 0
