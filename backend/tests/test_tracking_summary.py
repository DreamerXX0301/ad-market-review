"""List projection retains browsing semantics while details retain all evidence."""
import copy
import pytest

from test_workspace import ws, make_strategy, add_one, advance
from test_tracking_event_quotes import seed_alert
from app import core
from app.services import tracking, entry_high


def test_summary_preserves_all_rows_prices_checks_policy_and_current_scope(ws):
    market, client = ws
    first_strategy = make_strategy(client)
    _, first = add_one(client, first_strategy)
    _, second = add_one(client, make_strategy(client))
    advance(market, 71)
    seed_alert(first, market.days[71], 1)
    seed_alert(second, market.days[71], 2)
    client.post('/api/v1/tracking-rounds/'+second['round_id']+'/remove', json={})
    full = client.get('/api/v1/tracking-rounds?status=all').json()
    summary = client.get('/api/v1/tracking-rounds?status=all&view=summary').json()
    expected = copy.deepcopy(full)
    expected['view'] = 'summary'
    expected['counts']['alerts'] = 2
    for row in expected['items']:
        for key in ('topics', 'topics_snapshot', 'entry_high_basis', 'entry_low_basis'):
            row.pop(key, None)
        for key in ('source_lineage', 'entry_topics', 'entry_high', 'entry_low'):
            row['entry_basis'].pop(key, None)
        for event in [row['latest_event'], *row['alerts']]:
            if event is not None:event.pop('evidence', None)
    assert summary == expected
    assert summary['total'] == 2
    assert all('entry_checks' in row['entry_basis'] for row in summary['items'])
    filtered = client.get('/api/v1/tracking-rounds', params={
        'view': 'summary', 'triggered_only': True, 'strategy_id': first_strategy['strategy_id'],
    }).json()
    assert filtered['counts'] == {'active': 1, 'history': 0, 'alerts': 1}
    assert [row['round_id'] for row in filtered['items']] == [first['round_id']]
    assert 'evidence' in client.get('/api/v1/tracking-rounds/'+first['round_id']).json()['events'][0]
    assert 'evidence' in client.get('/api/v1/tracking-events?event_type=alert').json()['items'][0]
    assert client.get('/api/v1/tracking-rounds?view=invalid').status_code == 422


def test_summary_rereads_policy_events_and_topic_edits(ws):
    market, client = ws
    _, round_ = add_one(client, make_strategy(client))
    advance(market, 71)
    path = '/api/v1/tracking-rounds?view=summary'
    before = client.get(path).json()
    changed = client.patch('/api/v1/tracking-rounds/'+round_['round_id']+'/policy', json={
        'policy': {'duration_sessions': 5},
    })
    assert changed.status_code == 200
    topic = client.post('/api/v1/topics', json={
        'name': '新的当前归属', 'instrument_ids': [round_['instrument_id']],
    }).json()
    seed_alert(round_, market.days[71], 3)
    after = client.get(path).json()
    row = after['items'][0]
    assert row['policy'] == changed.json()['policy']
    assert row['remaining_sessions'] == 4
    assert row['policy_review_pending'] is True
    assert after['counts']['alerts'] == 1 and row['alerts']
    assert topic['topic_id'] in {value['topic_id'] for value in row['current_topics']}
    assert after['hot_filter_scope'] != before['hot_filter_scope']
    assert row['entry_basis']['entry_checks'] == before['items'][0]['entry_basis']['entry_checks']


def test_request_decodes_each_entry_basis_once_for_high_low_and_row(ws, monkeypatch):
    market, client = ws
    _, round_ = add_one(client, make_strategy(client))
    with core.connect() as connection:
        encoded = connection.execute('SELECT entry_basis_json FROM tracking_round WHERE round_id=?', (round_['round_id'],)).fetchone()[0]
    calls = []
    original = tracking.obj
    def counted(value, default=None):
        if value == encoded:calls.append(value)
        return original(value, default)
    monkeypatch.setattr(tracking, 'obj', counted)
    monkeypatch.setattr(entry_high, '_basis', lambda *_: pytest.fail('list must share the fresh parsed basis'))
    response = client.get('/api/v1/tracking-rounds?view=summary')
    assert response.status_code == 200 and len(calls) == 1


def test_json_ready_get_routes_do_not_deep_encode_again(ws, monkeypatch):
    _, client = ws
    run, round_ = add_one(client, make_strategy(client))
    import fastapi.routing
    monkeypatch.setattr(fastapi.routing, 'jsonable_encoder', lambda *_a, **_kw: pytest.fail('JSON-ready response must be encoded once'))
    for path in ('/tracking-rounds', '/tracking-rounds?view=summary', '/tracking-rounds/'+round_['round_id'],
                 '/strategy-runs', '/strategy-runs/'+run['run_id']+'?decision=matched'):
        response = client.get('/api/v1'+path)
        assert response.status_code == 200 and response.headers['content-type'] == 'application/json'
