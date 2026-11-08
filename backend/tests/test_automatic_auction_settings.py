"""Unified settings and automatic-only auction boundaries; isolated synthetic DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import core, worker
from app.routers import automatic_tasks as settings_router
from app.services import automatic_tasks as settings, auction_automation as auction
from app.services import auction_service as capture, source_service, sync_clock, sync_requests, task_service as tasks
from test_auction_snapshots import DAY, NOW, isolated as auction_environment


@pytest.fixture
def configured(auction_environment, monkeypatch):
    environment = auction_environment
    monkeypatch.setattr(auction, 'now_china', lambda: environment.clock[0])
    monkeypatch.setattr(sync_clock, 'now_china', lambda: environment.clock[0])
    environment.client.app.include_router(settings_router.router)
    return environment


def payload(*, enabled=True, expected=0, auction_revision=0, close='17:00'):
    return {'noon': {'enabled': enabled, 'time': '12:05'},
            'after_close': {'enabled': enabled, 'time': close}, 'expected_revision': expected,
            'auction': {'enabled': enabled}, 'expected_auction_revision': auction_revision}


def facts():
    with core.connect() as connection:
        return {name: [tuple(row) for row in connection.execute('SELECT * FROM '+name+' ORDER BY rowid')]
                for name in ('platform_setting', 'market_auction_automation', 'market_auction_automation_run',
                             'job', 'job_event', 'market_auction_capture', 'market_auction_snapshot')}


def test_get_is_local_and_preserves_three_default_off_settings(configured, monkeypatch):
    before = facts()
    monkeypatch.setattr(capture, 'get_market', lambda: pytest.fail('settings cannot load行情'))
    monkeypatch.setattr(configured.source, 'request', lambda *_a, **_k: pytest.fail('settings cannot fetch'))
    response = configured.client.get('/api/v1/settings/automatic-tasks')
    assert response.status_code == 200
    view = response.json()
    assert view['revision'] == 0 and view['policy']['after_close_not_before'] == '17:00'
    assert view['after_close'] == {'enabled': False, 'time': '17:00'}
    assert view['auction_discovery'] == {'enabled': False, 'revision': 0}
    assert view['auction'] == {'enabled': False, 'revision': 0, 'mode': 'off',
        'fixed_time': '09:25:30', 'window_end': '09:30:00', 'can_enable': True, 'reason': None,
        'next_run_at': None, 'last_run': None, 'worker': {'alive': False, 'last_heartbeat_at_ms': None},
        'legacy_configuration': False, 'legacy_instrument_count': 0}
    assert facts() == before


def test_discovery_uses_independent_opt_in_and_revokes_frozen_authority(configured):
    request = {**payload(), 'auction_discovery': {'enabled': True, 'expected_revision': 0}}
    assert settings.configure(request)['auction_discovery'] == {'enabled': True, 'revision': 1}
    auction.tick()
    with core.connect() as connection:
        params = json.loads(connection.execute('SELECT parameters_json FROM job').fetchone()[0])
        proof = params['automation']['discovery']
        assert auction.discovery_authorized(connection, proof)
        assert not auction.discovery_authorized(connection, None)
    request = {**payload(expected=1, auction_revision=1),
               'auction_discovery': {'enabled': False, 'expected_revision': 1}}
    assert settings.configure(request)['auction_discovery'] == {'enabled': False, 'revision': 2}
    with core.connect() as connection:
        assert not auction.discovery_authorized(connection, proof)
        assert connection.execute('SELECT status FROM job').fetchone()[0] == 'queued'


def test_combined_put_is_atomic_and_repeated_save_is_idempotent(configured):
    result = configured.client.put('/api/v1/settings/automatic-tasks', json=payload())
    assert result.status_code == 200, result.text
    view = result.json()
    assert view['revision'] == view['auction']['revision'] == 1
    assert view['auction']['enabled'] and view['auction']['mode'] == 'daily'
    before = facts()
    repeated = configured.client.put('/api/v1/settings/automatic-tasks', json=payload(expected=1, auction_revision=1))
    assert repeated.status_code == 200 and repeated.json()['revision'] == repeated.json()['auction']['revision'] == 1
    assert facts() == before and configured.source.calls == []
    assert settings.enabled()


def test_old_client_does_not_read_modify_or_clear_auction_authority(configured):
    auction.update_config({'mode': 'daily', 'expected_revision': 0})
    old = payload(enabled=False, close='17:30'); old.pop('auction'); old.pop('expected_auction_revision')
    before = facts()['market_auction_automation']
    result = configured.client.put('/api/v1/settings/automatic-tasks', json=old)
    assert result.status_code == 200 and result.json()['revision'] == 1
    assert result.json()['auction']['revision'] == 1 and result.json()['auction']['enabled']
    assert facts()['market_auction_automation'] == before


@pytest.mark.parametrize('close_time', ['18:00', '18:30'])
def test_saved_close_time_is_not_replaced_by_the_new1700_default(configured, close_time):
    saved = {'revision': 7, 'noon': {'enabled': False, 'time': '12:05'},
             'after_close': {'enabled': True, 'time': close_time}}
    with core.connect() as connection:
        settings._save(connection, settings.CONFIG_KEY, saved)
    before = facts()
    for _ in range(2):
        view = settings.read_settings()
        assert view['revision'] == 7 and view['after_close'] == saved['after_close']
    assert facts() == before
    result = settings.configure({'expected_revision': 7,
        'noon': {'enabled': True, 'time': '12:05'}, 'after_close': dict(saved['after_close'])})
    assert result['revision'] == 8 and result['after_close'] == saved['after_close']
    assert result['auction']['mode'] == 'off' and result['auction']['revision'] == 0


@pytest.mark.parametrize('stale', ['review', 'auction'])
def test_either_stale_revision_rolls_back_both_systems(configured, stale):
    settings.configure(payload())
    before = facts()
    change = payload(enabled=False, expected=1, auction_revision=1)
    change['expected_revision' if stale == 'review' else 'expected_auction_revision'] = 0
    response = configured.client.put('/api/v1/settings/automatic-tasks', json=change)
    assert response.status_code == 409 and facts() == before


def test_late_failure_rolls_back_auction_update_and_review_update(configured, monkeypatch):
    before = facts()
    monkeypatch.setattr(settings, '_save', lambda *_a: (_ for _ in ()).throw(RuntimeError('synthetic late save failure')))
    with pytest.raises(RuntimeError, match='synthetic late'):
        settings.configure(payload())
    assert facts() == before


def test_actual_enable_without_source_authority_rolls_back_review_too(configured, monkeypatch):
    before = facts()
    monkeypatch.setattr(capture, '_configured', lambda _connection: False)
    response = configured.client.put('/api/v1/settings/automatic-tasks', json=payload())
    assert response.status_code == 409 and facts() == before


def test_existing_enabled_noop_does_not_recheck_temporarily_missing_source(configured, monkeypatch):
    settings.configure(payload())
    before = facts()['market_auction_automation']
    monkeypatch.setattr(capture, '_configured', lambda _connection: False)
    updated = payload(expected=1, auction_revision=1, close='17:30')
    response = configured.client.put('/api/v1/settings/automatic-tasks', json=updated)
    assert response.status_code == 200
    assert response.json()['revision'] == 2 and response.json()['auction']['revision'] == 1
    assert response.json()['auction']['enabled'] and not response.json()['auction']['can_enable']
    assert facts()['market_auction_automation'] == before


def test_worker_creation_scope_is_removed_even_when_submission_raises(configured):
    authorization = {'revision': 0}
    with pytest.raises(HTTPException) as error:
        with auction.worker_creation_scope(authorization):
            capture.create_capture({'instrument_ids': []}, automation=authorization)
    assert error.value.status_code == 422
    assert auction._worker_creation.get() is None
    with pytest.raises(HTTPException) as error:
        capture.create_capture({'date': DAY}, automation=authorization)
    assert error.value.status_code == 403 and facts()['job'] == []


def test_concurrent_revision_winner_commits_three_settings_once(configured):
    def save(clock):
        try:
            return settings.configure(payload(close=clock))['revision']
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['17:00', '17:01']))
    assert sorted(results) == [1, 409]
    result = settings.read_settings()
    assert result['revision'] == result['auction']['revision'] == 1
    assert result['noon']['enabled'] and result['after_close']['enabled'] and result['auction']['enabled']
    assert configured.source.calls == []


@pytest.mark.parametrize('auction_value, revision', [
    ({'enabled': True, 'time': '09:26'}, 0), ({'enabled': True, 'fixed_time': '09:25:30'}, 0),
    ({'enabled': True, 'mode': 'once'}, 0), ({'enabled': True, 'instrument_ids': []}, 0),
    ({'enabled': 1}, 0), ({'enabled': True}, None), ({'enabled': True}, True),
])
def test_unified_auction_rejects_extra_fields_and_invalid_revision(configured, auction_value, revision):
    value = payload(); value['auction'] = auction_value
    if revision is None: value.pop('expected_auction_revision')
    else: value['expected_auction_revision'] = revision
    before = facts()
    response = configured.client.put('/api/v1/settings/automatic-tasks', json=value)
    assert response.status_code == 422 and facts() == before


@pytest.mark.parametrize('extra', [{'mode': 'once'}, {'instrument_ids': []}, {'date': DAY}, {'time': '09:25:30'}])
def test_old_configuration_endpoint_cannot_restore_once_or_explicit_list(configured, extra):
    before = facts()
    response = configured.client.post('/api/v1/market/auction/automation', json={'mode': 'daily', 'expected_revision': 0, **extra})
    assert response.status_code == 422 and facts() == before


@pytest.mark.parametrize('mode, instruments', [('once', []), ('daily', ['600001.SH']), ('off', ['600001.SH'])])
def test_legacy_configuration_is_explained_not_consumed_or_silently_rewritten(configured, mode, instruments):
    with core.connect() as connection:
        connection.execute('UPDATE market_auction_automation SET mode=?,trade_date=?,instrument_ids_json=?,revision=revision+1,updated_at_ms=1',
                           (mode, DAY if mode == 'once' else None, json.dumps(instruments)))
    before = facts()
    view = settings.read_settings()['auction']
    assert not view['enabled'] and view['legacy_configuration'] and view['legacy_instrument_count'] == len(instruments)
    assert view['mode'] == mode and '旧' in view['reason']
    for _ in range(3): assert auction.tick() is None
    assert facts() == before and configured.source.calls == []
    result = settings.configure(payload(auction_revision=1))
    assert result['auction']['revision'] == 2 and result['auction']['enabled'] and not result['auction']['legacy_configuration']
    with core.connect() as connection:
        assert connection.execute('SELECT mode,trade_date,instrument_ids_json FROM market_auction_automation').fetchone()[:] == ('daily', None, '[]')


def test_all_new_manual_creation_layers_reject_without_job_or_source(configured):
    before = facts()
    response = configured.client.post('/api/v1/market/auction/jobs', json={'date': DAY})
    assert response.status_code == 403
    for authority in (None, {'revision': 0}):
        with pytest.raises(HTTPException) as error: capture.create_capture({'date': DAY}, automation=authority)
        assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        tasks.create_task('source_check', {'operation': 'auction_snapshot'}, target_date=DAY)
    assert error.value.status_code == 403
    assert facts() == before and configured.source.calls == []


def test_fixed_second_default_universe_and_restart_do_not_duplicate(configured):
    auction.update_config({'mode': 'daily', 'expected_revision': 0})
    configured.clock[0] = NOW.replace(minute=25, second=29)
    assert auction.tick() is None
    assert facts()['job'] == []
    configured.clock[0] += timedelta(seconds=1)
    created = auction.tick()
    assert created and configured.source.calls == []
    with core.connect() as connection:
        original = dict(connection.execute('SELECT * FROM job').fetchone())
        params = json.loads(original['parameters_json'])
        assert params['automatic'] is True and params['instrument_ids'] == []
        assert params['automation']['mode'] == 'daily'
    for _ in range(3): auction.tick()
    assert len(facts()['job']) == 1
    claimed = tasks.claim_next('synthetic-fixed-worker')
    worker.dispatch(claimed)
    saved = capture.read_view(DAY)
    assert saved['state'] == 'available' and saved['snapshot']['payload']['universe']['mode'] != 'explicit'
    assert not any(saved[key] for key in ('can_capture', 'can_retry', 'can_resume'))
    assert facts()['job'][0][0] == original['job_id']


@pytest.mark.parametrize('action', ['retry', 'resume'])
def test_generic_and_internal_manual_resume_never_revive_original_request(configured, action):
    auction.update_config({'mode': 'daily', 'expected_revision': 0}); original = auction.tick()
    status = 'failed' if action == 'retry' else 'paused'
    with core.connect() as connection:
        connection.execute('UPDATE job SET status=?,phase=? WHERE job_id=?', (status, status, original['task_id']))
    before = facts()
    for fn in (lambda: tasks.change_task(original['task_id'], action), lambda: capture.requeue_capture(original['task_id'], action)):
        with pytest.raises(HTTPException) as error: fn()
        assert error.value.status_code == 403
    assert facts() == before and configured.source.calls == []
    auction.tick()
    assert tasks.task_view(original['task_id'])['status'] == status


def test_attempt_cannot_request_source_before_fixed_second_or_after_window(configured):
    auction.update_config({'mode': 'daily', 'expected_revision': 0}); original = auction.tick()
    job = tasks.claim_next('synthetic-fixed-window')
    configured.clock[0] = NOW.replace(minute=25, second=29, microsecond=999999)
    with pytest.raises(tasks.TaskPaused): worker.dispatch(job)
    assert configured.source.calls == [] and tasks.task_view(original['task_id'])['status'] == 'paused'


def test_1700_is_real_sync_factory_and_execution_clock_boundary(configured, monkeypatch):
    source = SimpleNamespace(source_id='fuyao', key='synthetic-key')
    monkeypatch.setattr(source_service, 'client', lambda *_a: source)
    configured.clock[0] = datetime.fromisoformat(DAY+'T16:59:59+08:00')
    with pytest.raises(HTTPException) as error: sync_requests.create_sync_request({'mode': 'increment'})
    assert error.value.status_code == 409 and '17:00' in error.value.detail
    with pytest.raises(ValueError, match='17:00'): sync_clock.validate_frozen_target(DAY)
    assert facts()['job'] == []
    configured.clock[0] += timedelta(seconds=1)
    value = sync_requests.create_sync_request({'mode': 'increment', 'request_key': 'synthetic-at17'})
    assert value['parameters']['sync_completion_date'] == DAY
    assert value['parameters']['noon_retention_policy'] == 'noon-retention-30d-v1'
    assert value['parameters']['auction_retention_policy'] == 'auction-retention-30d-v2'
    assert sync_clock.sync_window()['increment_allowed'] and sync_clock.sync_window()['cutoff'] == '17:00'
    sync_clock.validate_frozen_target(DAY)
