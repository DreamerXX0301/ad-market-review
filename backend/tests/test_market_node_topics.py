import pytest
from fastapi import HTTPException

from app.services import market_node_topics as topics, task_service
from test_platform import isolated


DAY, LATER, SYMBOL = '2026-10-16', '2026-10-19', '600036.SH'


def test_topic_adjustment_compare_and_swap_and_exact_date_receipt(isolated):
    assert topics.read_overrides(DAY) == {}
    topic = topics.manual_topic('  机器人  ')
    assert topics.manual_topic('机器人') == topic
    assert topics.manual_topic('机器人', 'industry')['topic_id'] != topic['topic_id']

    def receipt(day):
        return task_service.task_activity(observation_date=day)['read_revisions']['dates'][day]['node_topics']

    assert receipt(DAY) is None
    saved = topics.save_override(DAY, SYMBOL, topic, expected_revision=None)['override']
    token = saved['revision']
    assert saved == {**topic, 'revision': token}
    assert topics.read_overrides(DAY) == {SYMBOL: saved}
    assert topics.read_overrides(LATER) == {}
    date_proof = receipt(DAY)
    assert date_proof and receipt(LATER) is None
    assert topics.save_override(DAY, SYMBOL, topic, expected_revision=token)['override'] == saved
    assert receipt(DAY) == date_proof

    alternative = {'topic_id': 'reason:east', 'name': '东方系', 'kind': 'concept'}
    with pytest.raises(HTTPException) as stale:
        topics.save_override(DAY, SYMBOL, alternative, expected_revision=None)
    assert stale.value.status_code == 409 and topics.read_overrides(DAY) == {SYMBOL: saved}
    changed = topics.save_override(DAY, SYMBOL, alternative, expected_revision=token)['override']
    assert changed['topic_id'] == alternative['topic_id'] and changed['revision'] != token
    assert receipt(DAY) != date_proof
    with pytest.raises(HTTPException) as stale:
        topics.save_override(DAY, SYMBOL, None, expected_revision=token)
    assert stale.value.status_code == 409
    assert topics.save_override(DAY, SYMBOL, None, expected_revision=changed['revision'])['override'] is None
    assert topics.read_overrides(DAY) == {} and receipt(DAY) is None


@pytest.mark.parametrize('date,symbol,topic,revision', [
    ('2026-02-30', SYMBOL, None, None),
    (DAY, '600036', None, None),
    (DAY, SYMBOL, {'topic_id': 'x', 'name': 'a\nb', 'kind': 'concept'}, None),
    (DAY, SYMBOL, {'topic_id': 'x', 'name': '股票', 'kind': 'unsupported'}, None),
    (DAY, SYMBOL, None, 'bad-version'),
])
def test_topic_adjustment_rejects_invalid_storage_inputs(isolated, date, symbol, topic, revision):
    with pytest.raises(HTTPException) as invalid:
        topics.save_override(date, symbol, topic, expected_revision=revision)
    assert invalid.value.status_code == 422


def test_anonymous_instance_disables_both_editor_routes(isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv('SHIXU_TOPIC_ADJUSTMENTS_ENABLED', '0')
    client = TestClient(app)
    path = '/api/v1/market/ladder/topic-adjustments'
    assert client.get(path, params={'date': DAY}).status_code == 403
    assert client.post(path, json={'date': DAY}).status_code == 403
    assert topics.read_overrides(DAY) == {}
