"""Daily attribution uses isolated business SQLite and controlled identities only."""
import sqlite3
import pytest

from app import core
from app.routers import market_mainlines, platform
from app.services import market_mainlines as service
from test_workspace import ws


@pytest.fixture
def mainlines(ws, monkeypatch):
    market, client = ws
    client.app.include_router(market_mainlines.router)
    client.app.include_router(platform.router)
    monkeypatch.setattr(service, 'get_market', lambda: market)
    ticks = iter(range(1800000000000, 1800000100000, 10))
    monkeypatch.setattr(service, 'now_ms', lambda: next(ticks))
    return market, client


def assertion(name='测试主线', **changes):
    return {'mainline_name': name, 'is_display_primary': False, 'role': 'unknown',
            'evidence_status': 'source_claim', 'source_name': '测试用人工转录来源',
            'source_reason': '来源原文，不能转换为已验证因果',
            'evidence_ref': 'fixture:source-image#row-1', 'source_published_at': None,
            'evidence_sha256': None, 'catalyst_note': '待独立核验', 'user_note': '', **changes}


def body(market, revision=0, **changes):
    return {'date': market.entry, 'instrument_id': '600001.SH', 'expected_revision': revision,
            'action': 'replace', 'change_reason': '根据已留存测试证据录入',
            'attributions': [assertion()], **changes}


def save(client, payload):
    response = client.post('/api/v1/market/mainlines/revisions', json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def get(client, market, **params):
    response = client.get('/api/v1/market/mainlines', params={'date': market.entry, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_append_correct_revoke_preserves_audit_and_first_known(mainlines):
    market, client = mainlines
    assert get(client, market)['items'] == []  # No screenshot or provider seeds.
    first = save(client, body(market))
    assert first['display_state'] == 'attributed'
    assert first['display_mainline'] == '测试主线'
    assert first['source_kind'] == 'manual_user_entry'
    assert first['attributions'][0]['first_known_at_ms'] == first['recorded_at_ms']
    # Only display placement changes: retain the assertion identity, audit the choice.
    second = save(client, body(market, 1, attributions=[assertion(is_display_primary=True)]))
    assert second['attributions'][0]['assertion_id'] == first['attributions'][0]['assertion_id']
    assert second['attributions'][0]['first_known_at_ms'] == first['recorded_at_ms']
    assert second['supersedes_revision_id'] == first['revision_id']
    changed = save(client, body(market, 2, attributions=[assertion(source_reason='更正后的来源原文')]))
    assert changed['attributions'][0]['first_known_at_ms'] == changed['recorded_at_ms']
    revoked = save(client, body(market, 3, action='revoke', attributions=[], change_reason='撤销误录，保留旧证据'))
    assert revoked['action'] == 'revoke' and revoked['display_state'] == 'unattributed'
    assert get(client, market)['items'][0]['revision'] == 4
    history = client.get('/api/v1/market/mainlines/history', params={
        'date': market.entry, 'instrument_id': '600001.SH', 'limit': 2, 'offset': 1}).json()
    assert history['total'] == 4 and [row['revision'] for row in history['items']] == [3, 2]
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM market_mainline_revision').fetchone()[0] == 4
        for operation in ('UPDATE market_mainline_revision SET change_reason=\'rewrite\'', 'DELETE FROM market_mainline_revision'):
            with pytest.raises(sqlite3.IntegrityError, match='retain daily mainline audit'):
                db.execute(operation)


def test_asof_uses_real_recording_time_not_old_publication(mainlines):
    market, client = mainlines
    first = save(client, body(market, attributions=[assertion(source_published_at='2020-01-01T18:30:00+08:00')]))
    assert get(client, market, known_at_ms=first['recorded_at_ms'] - 1)['items'] == []
    revoked = save(client, body(market, 1, action='revoke', attributions=[]))
    then = get(client, market, known_at_ms=first['recorded_at_ms'])['items'][0]
    assert then['revision_id'] == first['revision_id'] and then['display_mainline'] == '测试主线'
    assert get(client, market, known_at_ms=revoked['recorded_at_ms'])['items'][0]['display_mainline'] is None
    assert then['attributions'][0]['source_published_at'] == '2020-01-01T10:30:00Z'


def test_multi_affiliations_pending_conflict_and_unknown_are_normal(mainlines):
    market, client = mainlines
    a, b = assertion('甲'), assertion('乙', evidence_ref='fixture:other-source', evidence_status='user_judgment')
    pending = save(client, body(market, attributions=[a, b]))
    assert pending['display_state'] == 'primary_pending' and pending['display_mainline'] is None
    assert pending['secondary_mainlines'] == ['甲', '乙']
    selected = save(client, body(market, 1, attributions=[a, {**b, 'is_display_primary': True}]))
    assert selected['display_mainline'] == '乙' and selected['secondary_mainlines'] == ['甲']
    conflict = save(client, body(market, 2, attributions=[a, {**b, 'evidence_status': 'conflict'}]))
    assert conflict['display_state'] == 'conflict' and conflict['display_mainline'] is None
    unknown = save(client, body(market, 3, attributions=[assertion('未证假设', evidence_status='unverified')]))
    assert unknown['display_state'] == 'unattributed' and unknown['display_mainline'] is None
    # Same named group from two sources is one display group, both claims retained.
    combined = save(client, body(market, 4, attributions=[a, {**a, 'evidence_ref': 'fixture:second-source'}]))
    assert combined['display_mainline'] == '甲' and len(combined['attributions']) == 2


def test_optimistic_lock_no_lost_update_and_read_paths_do_not_open_market(mainlines, monkeypatch):
    market, client = mainlines
    first = save(client, body(market))
    stale = client.post('/api/v1/market/mainlines/revisions', json=body(market))
    assert stale.status_code == 409 and stale.json()['detail']['current_revision'] == 1
    monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('GET must be SQLite-only'))
    assert get(client, market)['items'][0]['revision_id'] == first['revision_id']
    assert client.get('/api/v1/market/mainlines/history', params={'date': market.entry, 'instrument_id': '600001.SH'}).json()['total'] == 1
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM job').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM topic').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM strategy').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [
    {'date': '2026-02-30'}, {'date': '20260105'}, {'instrument_id': '600001'},
    {'expected_revision': True}, {'expected_revision': -1}, {'action': 'delete'},
    {'change_reason': '  '}, {'attributions': []}, {'action': 'revoke'},
    {'attributions': [assertion()] * 13}, {'attributions': [assertion(), assertion()]},
    {'attributions': [assertion(is_display_primary=True), assertion('乙', is_display_primary=True)]},
    {'attributions': [assertion(evidence_ref='')]}, {'attributions': [assertion(source_name='')]},
    {'attributions': [assertion(evidence_sha256='fake')]}, {'attributions': [assertion(evidence_sha256=[])]},
    {'attributions': [assertion(role='龙头')]},
    {'attributions': [assertion(evidence_status='official_verified')]},
    {'attributions': [assertion(evidence_status='unverified', is_display_primary=True)]},
    {'attributions': [assertion(is_display_primary=1)]},
    {'attributions': [assertion(source_published_at='2026-01-05T15:00:00')]},
    {'attributions': [assertion(source_published_at='2099-01-05T15:00:00Z')]},
    {'attributions': [assertion(first_known_at_ms=1)]}, {'recorded_at_ms': 1},
])
def test_invalid_writes_are_atomic(mainlines, changes):
    market, client = mainlines
    response = client.post('/api/v1/market/mainlines/revisions', json=body(market, **changes))
    assert response.status_code == 422, response.text
    assert get(client, market)['items'] == []


def test_identity_calendar_and_published_day_validation_happens_before_append(mainlines):
    market, client = mainlines
    assert client.post('/api/v1/market/mainlines/revisions', json=body(market, instrument_id='999999.SH')).status_code == 404
    assert client.post('/api/v1/market/mainlines/revisions', json=body(market, date='2026-01-04')).status_code == 422
    assert client.post('/api/v1/market/mainlines/revisions', json=body(market, date=market.days[71])).status_code == 409
    # An empty history remains readable even if no current catalogue is available.
    assert client.get('/api/v1/market/mainlines', params={'date': '2000-01-03'}).json()['items'] == []


def test_backward_clock_rejected_not_rewritten_into_fake_time(mainlines, monkeypatch):
    market, client = mainlines
    first = save(client, body(market))
    monkeypatch.setattr(service, 'now_ms', lambda: first['recorded_at_ms'] - 1)
    response = client.post('/api/v1/market/mainlines/revisions', json=body(market, 1))
    assert response.status_code == 409
    assert get(client, market)['items'][0]['revision'] == 1


@pytest.mark.parametrize('other_symbol,earlier_day', [('000001.SZ', False), ('600001.SH', True)])
def test_module_clock_rollback_cannot_add_late_records_to_prior_asof(mainlines, monkeypatch, other_symbol, earlier_day):
    market, client = mainlines
    monkeypatch.setattr(service, 'now_ms', lambda: 2000)
    first = save(client, body(market))
    other_day = market.days[0] if earlier_day else market.entry
    frozen = client.get('/api/v1/market/mainlines', params={'date': other_day, 'known_at_ms': 1500}).json()
    assert frozen['items'] == []
    monkeypatch.setattr(service, 'now_ms', lambda: 1000)
    response = client.post('/api/v1/market/mainlines/revisions', json=body(market, date=other_day, instrument_id=other_symbol))
    assert response.status_code == 409, response.text
    assert client.get('/api/v1/market/mainlines', params={'date': other_day, 'known_at_ms': 1500}).json() == frozen
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM market_mainline_revision').fetchone()[0] == 1
    # Recovery may use the actual equal timestamp; never synthesize max+1.
    monkeypatch.setattr(service, 'now_ms', lambda: 2000)
    recovered = save(client, body(market, date=other_day, instrument_id=other_symbol))
    assert recovered['recorded_at_ms'] == first['recorded_at_ms'] == 2000


def test_clock_rollback_during_first_save_rejects_without_fabricating_time(mainlines, monkeypatch):
    market, client = mainlines
    ticks = iter([2000, 1000])  # input checked first, actual transaction stamp second
    monkeypatch.setattr(service, 'now_ms', lambda: next(ticks))
    response = client.post('/api/v1/market/mainlines/revisions', json=body(market))
    assert response.status_code == 409, response.text
    assert get(client, market)['items'] == []


def test_source_publication_utc_underflow_is_validation_error(mainlines):
    market, client = mainlines
    response = client.post('/api/v1/market/mainlines/revisions', json=body(market,
        attributions=[assertion(source_published_at='0001-01-01T00:00:00+14:00')]))
    assert response.status_code == 422, response.text
    assert get(client, market)['items'] == []


def test_retained_audit_is_revisable_after_catalogue_rolls_forward(mainlines, monkeypatch):
    market, client = mainlines
    first = save(client, body(market))
    monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('retained daily identity does not require current membership'))
    revoked = save(client, body(market, 1, action='revoke', attributions=[]))
    assert revoked['instrument_name'] == first['instrument_name']
    assert revoked['supersedes_revision_id'] == first['revision_id']


def test_sqlite_chain_and_concurrent_editors_cannot_overwrite_audit(mainlines):
    from concurrent.futures import ThreadPoolExecutor
    market, client = mainlines
    first = save(client, body(market))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda name: client.post('/api/v1/market/mainlines/revisions',
            json=body(market, 1, attributions=[assertion(name)])), ['并发甲', '并发乙']))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with core.connect() as db:
        row = dict(db.execute('SELECT * FROM market_mainline_revision WHERE revision_id=?', (first['revision_id'],)).fetchone())
        row.update(revision_id='fixture-invalid-chain', revision=3, supersedes_revision_id=first['revision_id'], recorded_at_ms=1800000200000)
        from app.infrastructure.mainline_repository import append
        with pytest.raises(sqlite3.IntegrityError, match='predecessor mismatch'):
            append(db, row)
        assert db.execute('SELECT count(*) FROM market_mainline_revision').fetchone()[0] == 2


def test_schema_17_upgrade_keeps_prior_data_and_zero_seeds():
    from app.infrastructure.database import MIGRATIONS_DIR
    connection = sqlite3.connect(':memory:')
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA recursive_triggers=ON')
    for path in sorted(MIGRATIONS_DIR.glob('[0-9]*.sql')):
        if path.name.startswith('017_'):
            break
        connection.executescript(path.read_text())
    connection.execute("INSERT INTO platform_setting VALUES('test-preserved','{\"value\":7}',100)")
    connection.commit()
    connection.executescript((MIGRATIONS_DIR / '017_market_mainlines.sql').read_text())
    assert connection.execute('SELECT count(*) FROM market_mainline_revision').fetchone()[0] == 0
    assert connection.execute("SELECT value_json FROM platform_setting WHERE key='test-preserved'").fetchone()[0] == '{"value":7}'
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    connection.close()


def test_main_router_precedes_generic_market_route(mainlines, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(service, 'list_mainlines', lambda day, known=None: {'mainline_route': day})
    response = TestClient(app).get('/api/v1/market/mainlines?date=2026-01-05')
    assert response.json() == {'mainline_route': '2026-01-05'}
