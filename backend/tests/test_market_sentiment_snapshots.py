"""Explicit manual capture uses temporary SQLite, never live prices or jobs."""
from contextlib import contextmanager
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import sqlite3

import pytest

from app import core
from app.routers import market_sentiment_snapshots as router_module
from app.services import market_sentiment_snapshots as service
from test_workspace import ws


@pytest.fixture
def snapshots(ws, monkeypatch):
    market, client = ws
    client.app.include_router(router_module.router)
    context = {'model': 'fixture-context-v1', 'date': market.entry, 'market': {'sample_count': 2},
               'provenance': {'mode': 'retained_retrospective', 'rule_version': 'fixture-rule-v1',
                              'input_fingerprint': 'a'*64, 'input_snapshot_ids': ['fixture-market'],
                              'computed_at': '2026-10-22T00:00:00+08:00', 'first_known_at': None,
                              'source_records': [{'trade_date': market.entry, 'fetched_at': '2026-10-21T21:00:00+08:00',
                                                  'first_known_at': None}]}}
    monkeypatch.setattr(service, 'compute_context', lambda day: deepcopy(context))
    ticks = iter(range(1800000000000, 1800001000000, 10))
    monkeypatch.setattr(service, 'now_ms', lambda: next(ticks))
    return market, client, context


def body(context, **extra):
    return {'date': context['date'], 'expected_input_fingerprint': context['provenance']['input_fingerprint'], **extra}


def post(client, context, **extra):
    response = client.post('/api/v1/market/sentiment-snapshots', json=body(context, **extra))
    assert response.status_code == 200, response.text
    return response.json()


def test_manual_capture_roundtrip_is_immutable_idempotent_and_not_backdated(snapshots, monkeypatch):
    market, client, context = snapshots
    assert client.get('/api/v1/market/sentiment-snapshots').json()['items'] == []
    first = post(client, context)
    assert first['created'] is True and first['reused'] is False
    assert first['record_mode'] == 'manual_retained_capture'
    assert first['payload'] == context
    assert first['payload']['provenance']['first_known_at'] is None
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    assert first['content_sha256'] == sha256(encoded.encode()).hexdigest()
    context['provenance']['computed_at'] = '2026-10-22T00:01:00+08:00'
    again = post(client, context)
    assert again['created'] is False and again['reused'] is True
    assert again['snapshot_id'] == first['snapshot_id']
    assert again['recorded_at_ms'] == first['recorded_at_ms']
    assert again['payload'] == first['payload']
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM market_sentiment_snapshot').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM job').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM strategy').fetchone()[0] == 0
    # GETs read retained SQLite only even when the market source is unavailable.
    monkeypatch.setattr(service, 'compute_context', lambda day: pytest.fail('GET must not compute/fetch'))
    detail = client.get('/api/v1/market/sentiment-snapshots/'+first['snapshot_id']).json()
    assert detail['payload'] == first['payload']
    listed = client.get('/api/v1/market/sentiment-snapshots', params={'date': market.entry}).json()
    assert listed['total'] == 1 and listed['items'][0]['snapshot_id'] == first['snapshot_id']
    assert 'payload' not in listed['items'][0]


def test_input_change_is_409_before_sqlite_transaction_or_append(snapshots, monkeypatch):
    _, client, context = snapshots
    request = body(context)
    context['provenance']['input_fingerprint'] = 'b'*64
    monkeypatch.setattr(service, 'connect', lambda: pytest.fail('stale input must fail before SQLite append'))
    response = client.post('/api/v1/market/sentiment-snapshots', json=request)
    assert response.status_code == 409
    assert response.json()['detail']['current_input_fingerprint'] == 'b'*64


def test_price_feedback_rule_v2_appends_without_rewriting_retained_v1(snapshots, monkeypatch):
    _, client, context = snapshots
    context['provenance']['rule_version'] = 'descriptive-context-v1'
    old = post(client, context)
    previous_payload = deepcopy(old['payload'])
    # Even the same retained-input fingerprint is versioned separately. The
    # real new computation also fingerprints its added per-member price field.
    context['provenance']['rule_version'] = 'descriptive-context-v2'
    context['short_term'] = {'groups': [{'id': 'ordinary_10', 'cohort_price_feedback': {
        'version': 'previous-group-price-feedback-v1', 'date': context['date'],
        'cohort_date': None, 'sample_count': 0, 'metrics': []}}]}
    new = post(client, context)
    assert new['created'] is True and new['snapshot_id'] != old['snapshot_id']
    assert post(client, context)['snapshot_id'] == new['snapshot_id']
    monkeypatch.setattr(service, 'compute_context', lambda _: pytest.fail('retained GET must not recompute'))
    retained = client.get('/api/v1/market/sentiment-snapshots/'+old['snapshot_id']).json()
    assert retained['payload'] == previous_payload
    assert retained['rule_version'] == 'descriptive-context-v1'
    assert 'short_term' not in retained['payload']


def test_server_context_is_computed_before_short_write_transaction(snapshots, monkeypatch):
    _, client, context = snapshots
    calls = []
    def compute(day):
        assert calls == []
        calls.append('computed')
        return deepcopy(context)
    @contextmanager
    def connection():
        assert calls == ['computed']
        calls.append('connected')
        with core.connect() as value: yield value
    monkeypatch.setattr(service, 'compute_context', compute)
    monkeypatch.setattr(service, 'connect', connection)
    assert post(client, context)['created'] is True
    assert calls == ['computed', 'connected']


def test_asof_filtering_uses_actual_record_time_and_pagination(snapshots):
    market, client, context = snapshots
    first = post(client, context)
    before = client.get('/api/v1/market/sentiment-snapshots', params={'date': market.entry, 'known_at_ms': first['recorded_at_ms']-1}).json()
    assert before['items'] == [] and before['total'] == 0
    context['provenance']['input_fingerprint'] = 'b'*64
    second = post(client, context)
    assert second['snapshot_id'] != first['snapshot_id']
    then = client.get('/api/v1/market/sentiment-snapshots', params={'date': market.entry, 'known_at_ms': first['recorded_at_ms']}).json()
    assert [row['snapshot_id'] for row in then['items']] == [first['snapshot_id']]
    page = client.get('/api/v1/market/sentiment-snapshots', params={'limit': 1, 'offset': 1}).json()
    assert page['total'] == 2 and [row['snapshot_id'] for row in page['items']] == [first['snapshot_id']]


@pytest.mark.parametrize('changes', [{'recorded_at_ms': 1}, {'payload': {}}, {'first_known_at': '2020-01-01'},
    {'expected_input_fingerprint': None}, {'expected_input_fingerprint': 'A'*64}, {'expected_input_fingerprint': []},
    {'date': '2026-02-30'}, {'date': True}])
def test_client_payload_or_time_cannot_enter_snapshot(snapshots, changes, monkeypatch):
    _, client, context = snapshots
    monkeypatch.setattr(service, 'compute_context', lambda day: pytest.fail('invalid request must not read market'))
    response = client.post('/api/v1/market/sentiment-snapshots', json=body(context, **changes))
    assert response.status_code == 422
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM market_sentiment_snapshot').fetchone()[0] == 0


def test_database_rejects_update_delete_and_replace_even_without_recursive_delete_triggers(snapshots):
    _, client, context = snapshots
    first = post(client, context)
    with core.connect() as db:
        row = dict(db.execute('SELECT * FROM market_sentiment_snapshot').fetchone())
        db.execute('PRAGMA recursive_triggers=OFF')
        for statement, args in [
            ('UPDATE market_sentiment_snapshot SET recorded_at_ms=1', ()),
            ('DELETE FROM market_sentiment_snapshot', ()),
            ('INSERT OR REPLACE INTO market_sentiment_snapshot ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', tuple(row.values())),
        ]:
            with pytest.raises(sqlite3.IntegrityError, match='retain manual sentiment snapshot'):
                db.execute(statement, args)
        assert db.execute('SELECT count(*) FROM market_sentiment_snapshot').fetchone()[0] == 1
    assert client.get('/api/v1/market/sentiment-snapshots/'+first['snapshot_id']).json()['recorded_at_ms'] == first['recorded_at_ms']


def test_clock_rollback_between_computation_and_append_is_rejected(snapshots, monkeypatch):
    _, client, context = snapshots
    ticks = iter([1800000000010, 1800000000000])
    monkeypatch.setattr(service, 'now_ms', lambda: next(ticks))
    response = client.post('/api/v1/market/sentiment-snapshots', json=body(context))
    assert response.status_code == 409 and '时钟回退' in response.text
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM market_sentiment_snapshot').fetchone()[0] == 0


def test_clock_rollback_across_dates_cannot_inject_into_old_known_at_result(snapshots, monkeypatch):
    market, client, context = snapshots
    first = post(client, context)
    # Different date/key still must obey the global retention clock.
    context['date'] = market.days[69]
    context['provenance']['input_fingerprint'] = 'b'*64
    monkeypatch.setattr(service, 'now_ms', lambda: first['recorded_at_ms']-1)
    response = client.post('/api/v1/market/sentiment-snapshots', json=body(context))
    assert response.status_code == 409
    before = client.get('/api/v1/market/sentiment-snapshots', params={'known_at_ms': first['recorded_at_ms']-1}).json()
    assert before['items'] == []


def test_concurrent_repeated_capture_keeps_one_original_record(snapshots):
    _, client, context = snapshots
    def save(_): return post(client, context)
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(save, range(8)))
    assert len({row['snapshot_id'] for row in result}) == 1
    assert sum(row['created'] for row in result) == 1
    assert len({row['recorded_at_ms'] for row in result}) == 1


@pytest.mark.parametrize('params', [{'known_at_ms': 0}, {'known_at_ms': -1}, {'limit': 0}, {'limit': 101}, {'offset': -1}, {'date': '2026-02-30'}])
def test_bounded_read_validation(snapshots, params):
    _, client, _ = snapshots
    assert client.get('/api/v1/market/sentiment-snapshots', params=params).status_code == 422


def test_detail_missing_or_damaged_content_never_falls_back_to_current_context(snapshots):
    _, client, context = snapshots
    assert client.get('/api/v1/market/sentiment-snapshots/not-an-id').status_code == 404
    first = post(client, context)
    assert client.get('/api/v1/market/sentiment-snapshots/sentiment_'+'0'*32).status_code == 404
    # Simulate storage corruption outside normal API writes in this disposable DB.
    with core.connect() as db:
        db.execute('DROP TRIGGER retain_sentiment_snapshot_update')
        db.execute("UPDATE market_sentiment_snapshot SET content_sha256=?", ('0'*64,))
    response = client.get('/api/v1/market/sentiment-snapshots/'+first['snapshot_id'])
    assert response.status_code == 409 and 'SHA' in response.text


def test_literal_snapshot_route_precedes_market_catch_all(snapshots):
    from app.main import app
    from fastapi.testclient import TestClient
    response = TestClient(app).get('/api/v1/market/sentiment-snapshots')
    assert response.status_code == 200, response.text
    assert response.json()['items'] == []
