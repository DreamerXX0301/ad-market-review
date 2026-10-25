"""Browse-only hot filtering shares selection evidence without rewriting history."""
from copy import deepcopy
import pytest
from app import core
from app.services import strategy_hot_universe, hot_browse
from test_workspace import ws, make_strategy, add_one
from test_watchlist import personal, add


CONFIG = {'window_sessions': 7, 'top_n': 10, 'kind': 'all'}


def evidence(day, snapshot='generation-a', complete=True, config=None):
    return {'version': strategy_hot_universe.VERSION, 'business_date': day,
            'snapshot_id': snapshot, 'config': config or CONFIG,
            'window': {'start': day, 'complete': complete},
            'coverage': {'complete': complete}, 'complete': complete,
            'member_ids': ['600001.SH'], 'membership_sha256': 'a'*64,
            'items': [{'topic_id': 'topic-a', 'name': '已留存题材', 'rank': 1, 'event_count': 2}],
            'scope': 'current_members_proxy_not_historical', 'reason': None if complete else '覆盖不足'}


def counts():
    with core.connect() as connection:
        return {row[0]: connection.execute('SELECT count(*) FROM "'+row[0]+'"').fetchone()[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_hot_browse_reuses_strategy_policy_is_current_scoped_and_does_not_persist(ws, monkeypatch):
    market, client = ws
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'generation-a'}, raising=False)
    calls = []
    original = evidence(market.entry)
    def freeze(config, *, expected_day):
        calls.append((deepcopy(config), expected_day))
        return deepcopy(original), {'600001.SH': ['topic-a']}
    monkeypatch.setattr(strategy_hot_universe, 'freeze', freeze)
    before = counts()
    response = client.get('/api/v1/hot-universe', params={'as_of': market.entry})
    assert response.status_code == 200, response.text
    result = response.json()
    assert calls == [(CONFIG, market.entry)]
    assert result['config'] == CONFIG and result['member_ids'] == original['member_ids']
    assert result['scope'] == 'current_members_proxy_not_historical'
    assert result['browse_scope']['business_date'] == market.entry
    assert result['browse_scope']['snapshot_id'] == 'generation-a'
    assert len(result['browse_scope']['topic_revision_signature']) == 64
    assert result['persisted'] is False and '不' in result['browse_notice']
    assert counts() == before
    assert original == evidence(market.entry), 'browse adapters never mutate a saved selection receipt'


@pytest.mark.parametrize('params', [
    {'as_of': '2026-02-30'}, {'as_of': '2026-1-01'},
    {'window_sessions': 0}, {'window_sessions': 61}, {'top_n': 0}, {'top_n': 51}, {'kind': 'future'},
])
def test_invalid_browse_configuration_never_runs_ranking(ws, monkeypatch, params):
    market, client = ws
    monkeypatch.setattr(strategy_hot_universe, 'freeze', lambda *args, **kwargs: pytest.fail('invalid request must not rank'))
    response = client.get('/api/v1/hot-universe', params={'as_of': market.entry, **params})
    assert response.status_code == 422


@pytest.mark.parametrize('params', [
    {'as_of': '2020-01-01'}, {'snapshot_id': 'previous-generation'},
    {'topic_revision_signature': 'b'*64},
])
def test_historical_or_stale_list_scope_is_rejected_before_ranking(ws, monkeypatch, params):
    market, client = ws
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'generation-a'}, raising=False)
    monkeypatch.setattr(strategy_hot_universe, 'freeze', lambda *args, **kwargs: pytest.fail('stale request must not rank'))
    response = client.get('/api/v1/hot-universe', params={'as_of': market.entry, **params})
    assert response.status_code == 409


@pytest.mark.parametrize('change', ['topics', 'day', 'generation', 'receipt_generation'])
def test_changes_during_ranking_reject_mixed_receipt(ws, monkeypatch, change):
    market, client = ws
    generation = ['generation-a']
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': generation[0]}, raising=False)
    def freeze(config, *, expected_day):
        if change == 'topics':
            assert client.post('/api/v1/topics', json={'name': '并发新题材', 'instrument_ids': ['600001.SH']}).status_code == 200
        elif change == 'day':
            with core.connect() as connection:
                connection.execute('UPDATE business_clock SET business_date=? WHERE singleton=1', (market.days[71],))
        elif change == 'generation':
            generation[0] = 'generation-b'
        return evidence(expected_day, snapshot='generation-b' if change == 'receipt_generation' else 'generation-a'), {}
    monkeypatch.setattr(strategy_hot_universe, 'freeze', freeze)
    response = client.get('/api/v1/hot-universe', params={'as_of': market.entry})
    assert response.status_code == 409


def test_unknown_ranking_is_returned_as_unknown_without_fabricating_members(ws, monkeypatch):
    market, client = ws
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'generation-a'}, raising=False)
    partial = evidence(market.entry, complete=False)
    monkeypatch.setattr(strategy_hot_universe, 'freeze', lambda config, expected_day: (partial, {}))
    result = client.get('/api/v1/hot-universe', params={'as_of': market.entry}).json()
    assert result['complete'] is False and result['reason'] == '覆盖不足'
    assert result['member_ids'] == ['600001.SH'], 'observed positives remain evidence, not certified rank matches'


def test_tracking_and_watchlist_current_scope_is_shared_and_local_member_edits_invalidate_it(personal):
    market, client = personal
    strategy = make_strategy(client)
    _, round_ = add_one(client, strategy)
    assert add(client, tracking_round_id=round_['round_id']).status_code == 200
    tracking = client.get('/api/v1/tracking-rounds').json()
    watched = client.get('/api/v1/watchlist').json()
    assert tracking['hot_filter_scope'] == watched['hot_filter_scope']
    before = tracking['hot_filter_scope']
    assert before['business_date'] == market.entry
    frozen = deepcopy(tracking['items'][0]['entry_basis'])
    assert client.post('/api/v1/topics', json={'name': '后来新增分类', 'instrument_ids': ['600001.SH']}).status_code == 200
    changed = client.get('/api/v1/tracking-rounds').json()
    assert changed['hot_filter_scope']['topic_revision_signature'] != before['topic_revision_signature']
    assert changed['items'][0]['entry_basis'] == frozen
    assert changed['hot_filter_scope'] == client.get('/api/v1/watchlist').json()['hot_filter_scope']
