"""Current-topic browsing never rewrites frozen entry/review evidence."""
from test_workspace import ws, make_strategy, add_one
from app.services import tracking


def test_current_memberships_and_entry_snapshot_are_separate(ws):
    _, client = ws
    client.post('/api/v1/topics/initialize', json={})
    source = client.get('/api/v1/topics').json()['items'][0]
    strategy = make_strategy(client)
    _, round_ = add_one(client, strategy)
    path = '/api/v1/tracking-rounds/' + round_['round_id']
    before = client.get(path).json()
    assert source['topic_id'] in {topic['topic_id'] for topic in before['topics_snapshot']}
    client.post('/api/v1/topics/' + source['topic_id'] + '/members', json={
        'instrument_id': round_['instrument_id'], 'action': 'exclude',
    })
    created = client.post('/api/v1/topics', json={
        'name': '后加入的新归属', 'instrument_ids': [round_['instrument_id']],
    })
    assert created.status_code == 200, created.text
    after = client.get(path).json()
    assert after['topics_snapshot'] == before['topics_snapshot']
    assert after['topics_snapshot_date'] == after['entry_business_date']
    assert after['events'] == before['events']
    assert {topic['topic_id'] for topic in after['current_topics']} == {created.json()['topic_id']}
    listing = client.get('/api/v1/tracking-rounds').json()
    assert listing['items'][0]['current_topics'] == after['current_topics']
    assert listing['items'][0]['current_topics_observed_date'] == listing['business_date']
    assert '不是历史归属' in listing['current_topics_scope']


def test_current_topic_map_is_built_once_for_complete_tracking_list(ws, monkeypatch):
    _, client = ws
    client.post('/api/v1/topics/initialize', json={})
    strategy = make_strategy(client, {'field': 'close', 'op': '>', 'value': 0})
    run = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={}).json()
    response = client.post('/api/v1/tracking-rounds', json={
        'run_id': run['run_id'],
        'instrument_ids': [row['instrument_id'] for row in run['items'] if row['decision'] == 'matched'],
    })
    assert response.status_code == 200 and response.json()['added_count'] == 2
    original = tracking.topic_map
    calls = []
    def counted(c, day):
        calls.append(day)
        return original(c, day)
    monkeypatch.setattr(tracking, 'topic_map', counted)
    response = client.get('/api/v1/tracking-rounds')
    assert response.status_code == 200 and response.json()['total'] == 2
    assert len(calls) == 1
