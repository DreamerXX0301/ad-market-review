"""服务内置竞价计划：本地配置、唯一任务、有限尝试与真实停止。"""
from datetime import timedelta
import json

from fastapi import HTTPException
import pytest

from app import core, worker
from app.adapters.providers import ProviderError
from app.services import auction_automation as automation, auction_service as service, task_service as tasks
from test_auction_snapshots import DAY, NOW, isolated as auction_environment, quote


@pytest.fixture
def auto(auction_environment, monkeypatch):
    monkeypatch.setattr(automation, 'now_china', lambda: auction_environment.clock[0])
    return auction_environment


def enable(auto, mode='daily', **changes):
    value = automation.read_view()
    response = auto.client.post('/api/v1/market/auction/automation', json={
        'mode': mode, 'expected_revision': value['revision'], **changes})
    assert response.status_code == 200, response.text
    return response.json()


def row():
    with core.connect() as connection:
        saved = connection.execute('SELECT * FROM market_auction_automation_run WHERE trade_date=?', (DAY,)).fetchone()
    return dict(saved) if saved else None


def claimed(auto):
    automation.tick()
    value = tasks.claim_next('fixture-auto-worker')
    assert value and value['target_date'] == DAY
    return value


def fail_attempt(job, error_type=ValueError):
    with pytest.raises(error_type) as caught:
        worker.dispatch(job)
    tasks.finish(job['job_id'], 'failed', error=str(caught.value), lease_owner=job['lease_owner'])


def not_ready(auto):
    def result(path, _params):
        if path.endswith('/auction/snapshot'):
            return {'code': 0, 'data': {'timestamp': int(auto.clock[0].timestamp() * 1000),
                    'auction_phase': 'closed', 'data_status': 'not_ready', 'total': 1, 'item': [quote()]}}
    return result


def test_get_configuration_and_post_are_local_only_default_off(auto, monkeypatch):
    monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('settings cannot read market'))
    monkeypatch.setattr(auto.source, 'request', lambda *_a, **_k: pytest.fail('settings cannot fetch'))
    value = auto.client.get('/api/v1/market/auction/automation').json()
    assert value['version'] == 'auction-automation-v1' and value['revision'] == 0
    assert value['mode'] == 'off' and not value['enabled']
    assert value['worker'] == {'alive': False, 'last_heartbeat_at_ms': None}
    enabled = enable(auto)
    assert enabled['revision'] == 1 and enabled['mode'] == 'daily'
    assert enabled['instrument_ids'] == [] and enabled['last_run'] is None
    assert enabled['start_time'] == '09:25:05' and enabled['end_time'] == '09:30:00'


@pytest.mark.parametrize('payload', [{'mode': 'daily'}, {'mode': 'daily', 'expected_revision': True},
    {'mode': 'daily', 'expected_revision': -1}, {'mode': 'forever', 'expected_revision': 0},
    {'mode': 'once', 'expected_revision': 0, 'date': '2026-10-28'},
    {'mode': 'daily', 'expected_revision': 0, 'date': DAY},
    {'mode': 'daily', 'expected_revision': 0, 'instrument_ids': ['600001']},
    {'mode': 'daily', 'expected_revision': 0, 'instrument_ids': ['600001.SH'] * 101},
    {'mode': 'daily', 'expected_revision': 0, 'unexpected': True}])
def test_invalid_authorization_does_not_change_revision_or_create_job(auto, payload):
    assert auto.client.post('/api/v1/market/auction/automation', json=payload).status_code == 422
    assert automation.read_view()['revision'] == 0 and row() is None


def test_eligibility_blocks_enable_but_disabling_is_always_available(auto, monkeypatch):
    enable(auto)
    monkeypatch.setattr(service, '_configured', lambda _connection: False)
    assert not automation.read_view()['can_enable']
    # Saving the same enabled value is a no-op; source eligibility is checked on a real transition.
    assert enable(auto)['revision'] == 1
    disabled = enable(auto, 'off')
    assert disabled['mode'] == 'off'
    assert auto.client.post('/api/v1/market/auction/automation',
        json={'mode':'daily','expected_revision':disabled['revision']}).status_code == 409
    assert automation.read_view()['mode'] == 'off'


def test_idle_tick_registers_one_frozen_automatic_job_and_worker_collects_exact_explicit_cohort(auto):
    enable(auto)
    job = claimed(auto)
    assert auto.source.calls == []
    params = json.loads(job['parameters_json'])
    assert params['automatic'] is True and job['requested_by'] == 'auction_automation'
    assert params['automation']['source_version'] == 'auction-snapshot-v1'
    worker.dispatch(job)
    value = automation.read_view()['last_run']
    assert value['state'] == 'available' and value['attempts'] == 1
    assert auto.source.calls[1][1]['thscodes'] == '600001.SH,000001.SZ'
    assert service.read_view(DAY)['snapshot']['payload']['universe']['mode'] == 'watchlist_previous_events'
    for _ in range(3):
        automation.tick()
    assert tasks.claim_next('must-stay-idle') is None
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1
        event = connection.execute("SELECT details_json,message FROM job_event WHERE stage='queued'").fetchone()
        assert json.loads(event['details_json'])['authorization'] == 'auction_automation'
        assert '手动提交' not in event['message']


def test_not_ready_retries_same_job_after10_then20_seconds_at_most3_total(auto):
    enable(auto)
    auto.source.hook = not_ready(auto)
    first = claimed(auto); fail_attempt(first, automation.AuctionNotReady)
    assert row()['attempts'] == 1 and row()['state'] == 'waiting_retry'
    assert not service.read_view(DAY)['can_retry']
    assert service.read_view(DAY)['automation_state'] == 'waiting_retry'
    activity = tasks.task_activity()
    current = next(item for item in activity['items'] if item['task_id'] == first['job_id'])
    assert current['automatic'] and current['automation_state'] == 'waiting_retry'
    assert activity['active_count'] == 1
    assert sum(item['task_id'] == first['job_id'] for item in activity['items']) == 1
    auto.clock[0] += timedelta(seconds=9)
    automation.tick(); assert tasks.claim_next('too-soon') is None
    auto.clock[0] += timedelta(seconds=1)
    second = claimed(auto)
    assert second['job_id'] == first['job_id']; fail_attempt(second, automation.AuctionNotReady)
    assert row()['attempts'] == 2 and row()['retry_after_at_ms'] == int(auto.clock[0].timestamp() * 1000) + 20000
    auto.clock[0] += timedelta(seconds=19)
    automation.tick(); assert tasks.claim_next('still-too-soon') is None
    auto.clock[0] += timedelta(seconds=1)
    third = claimed(auto); fail_attempt(third, automation.AuctionNotReady)
    assert third['job_id'] == first['job_id'] and row()['attempts'] == 3
    assert row()['state'] == 'failed' and not row()['automatic_control']
    auto.clock[0] += timedelta(seconds=60)
    automation.tick(); assert tasks.claim_next('no-fourth-attempt') is None
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1


def test_subsequent_ready_success_ends_waiting_and_keeps_original_inputs(auto):
    enable(auto); auto.source.hook = not_ready(auto)
    first = claimed(auto); fail_attempt(first)
    original = tasks.task_view(first['job_id'])['parameters']['frozen_inputs']
    auto.previous['payload'] = {'original': '后来的材料不可混入'}
    auto.clock[0] += timedelta(seconds=10); auto.source.hook = None
    second = claimed(auto); worker.dispatch(second)
    assert automation.read_view()['last_run']['state'] == 'available'
    assert tasks.task_view(first['job_id'])['parameters']['frozen_inputs'] == original
    assert row()['attempts'] == 2


@pytest.mark.parametrize('mode', ['permission', 'ready_invalid_price', 'ready_unit_mismatch', 'wrong_timestamp'])
def test_only_this_attempt_explicit_not_ready_is_retryable(auto, mode):
    enable(auto); auto.source.hook = not_ready(auto)
    first = claimed(auto); fail_attempt(first)
    auto.clock[0] += timedelta(seconds=10)
    def invalid(path, params):
        if path.endswith('/auction/snapshot'):
            if mode == 'permission':
                raise ProviderError('合成无权限', 403)
            stamp = int((auto.clock[0] - timedelta(days=1) if mode == 'wrong_timestamp' else auto.clock[0]).timestamp() * 1000)
            record = quote(auction_pct=999) if mode == 'ready_invalid_price' else quote(auction_amount=1)
            return {'code': 0, 'data': {'timestamp': stamp, 'auction_phase': 'final', 'data_status': 'ready',
                                      'total': 1, 'item': [record]}}
    auto.source.hook = invalid
    second = claimed(auto)
    if mode == 'ready_unit_mismatch':
        # A valid price plus unknown amount is an accepted partial snapshot,
        # never a reason to refetch a successful day's evidence.
        worker.dispatch(second)
        assert row()['state'] == 'available'
    else:
        fail_attempt(second)
        assert row()['state'] == 'failed' and row()['outcome'] == 'failed'
    auto.clock[0] += timedelta(seconds=60)
    automation.tick(); assert tasks.claim_next('stale-marker-must-not-retry') is None
    assert row()['attempts'] == 2


@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_explicit_stop_during_failed_waiting_interval_prevents_auto_requeue(auto, action):
    enable(auto); auto.source.hook = not_ready(auto)
    job = claimed(auto); fail_attempt(job)
    stopped = auto.client.post('/api/v1/tasks/' + job['job_id'] + '/' + action)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()['status'] == ('paused' if action == 'pause' else 'cancelled')
    auto.clock[0] += timedelta(seconds=30)
    automation.tick(); assert tasks.claim_next('not-resumed') is None
    assert not row()['automatic_control']


def test_manual_retry_cannot_take_over_or_change_automatic_retry_authority(auto):
    enable(auto); auto.source.hook = not_ready(auto)
    job = claimed(auto); fail_attempt(job)
    before = row()
    with pytest.raises(HTTPException) as caught:
        tasks.change_task(job['job_id'], 'retry')
    assert caught.value.status_code == 403
    assert row() == before and row()['automatic_control']
    auto.clock[0] += timedelta(seconds=10)
    auto.source.hook = None
    retried = claimed(auto)
    assert retried['job_id'] == job['job_id']
    worker.dispatch(retried)
    assert row()['attempts'] == 2 and row()['state'] == 'available'
    assert not json.loads(retried['parameters_json'])['automation']['manual_takeover']

def test_off_pauses_unstarted_queued_capture_and_revision_change_blocks_lease_recovery(auto):
    enable(auto); automation.tick()
    identifier = row()['job_id']
    enable(auto, 'off')
    assert tasks.task_view(identifier)['status'] == 'paused' and not row()['automatic_control']
    assert tasks.claim_next('off-must-not-execute') is None


def test_off_during_running_attempt_does_not_authorize_retry_or_new_job(auto):
    enable(auto); auto.source.hook = not_ready(auto)
    def disable_before_auction(path, params):
        if path.endswith('/auction/snapshot'):
            enable(auto, 'off')
            return not_ready(auto)(path, params)
    auto.source.hook = disable_before_auction
    job = claimed(auto); fail_attempt(job)
    assert row()['state'] == 'failed' and not row()['automatic_control']
    auto.clock[0] += timedelta(seconds=30)
    automation.tick(); assert tasks.claim_next('disabled-no-retry') is None


def test_source_revision_change_before_execution_is_not_implicitly_authorized(auto):
    enable(auto); job = claimed(auto)
    core.set_setting('source-credential-revision:fuyao', 'new-key-revision-without-key')
    with pytest.raises(tasks.TaskPaused):
        worker.dispatch(job)
    assert auto.source.calls == [] and row()['state'] == 'paused'


def test_daily_start92505_boundary_does_not_register_before_window(auto):
    enable(auto)
    auto.clock[0] = NOW.replace(minute=25, second=4)
    automation.tick(); assert row() is None
    auto.clock[0] = NOW.replace(minute=25, second=5)
    automation.tick(); assert row()['state'] == 'queued'
    assert row()['attempts'] == 0


def test_legacy_once_configuration_is_readable_but_not_run_the_following_day(auto):
    with core.connect() as connection:
        connection.execute("UPDATE market_auction_automation SET mode='once',trade_date=?,revision=1 WHERE singleton=1", (DAY,))
    auto.clock[0] += timedelta(days=1)
    automation.tick()
    assert row() is None and tasks.claim_next('no-old-day') is None
    view = automation.read_view()
    assert view['mode'] == 'once' and view['legacy_configuration'] and not view['enabled']
    assert auto.source.calls == []

def test_worker_heartbeat_is_local_and_ages_out(auto):
    core.set_setting('worker_heartbeat', {'at_ms': int(auto.clock[0].timestamp() * 1000), 'owner': 'fixture-worker'})
    assert automation.read_view()['worker']['alive']
    auto.clock[0] += timedelta(seconds=46)
    assert not automation.read_view()['worker']['alive']


def test_service_idle_loop_collects_without_page_read_or_manual_job_submission(auto, monkeypatch):
    enable(auto)
    monkeypatch.setattr(worker.signal, 'signal', lambda *_args: None)
    worker.run(once=True)
    assert row()['state'] == 'available' and row()['attempts'] == 1
    assert len(auto.source.calls) == 3
    assert tasks.task_view(row()['job_id'])['status'] == 'succeeded'


def test_service_loop_failed_attempt_records_waiting_then_retries_without_new_job(auto, monkeypatch):
    enable(auto); auto.source.hook = not_ready(auto)
    monkeypatch.setattr(worker.signal, 'signal', lambda *_args: None)
    worker.run(once=True)
    original = row()['job_id']
    assert row()['state'] == 'waiting_retry' and tasks.task_view(original)['status'] == 'failed'
    auto.clock[0] += timedelta(seconds=10); auto.source.hook = None
    worker.run(once=True)
    assert row()['job_id'] == original and row()['attempts'] == 2 and row()['state'] == 'available'


def test_stale_execution_outcome_cannot_stop_recovered_attempt_before_its_begin(auto):
    enable(auto); original = claimed(auto)
    assert automation.begin_attempt(original)
    with core.connect() as connection:
        connection.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?', (tasks.now_ms() - 1, original['job_id']))
    recovered = tasks.claim_next('recovered-worker')
    assert recovered['lease_owner'] != original['lease_owner']
    automation.record_outcome(original, 'failed')
    assert row()['state'] == 'running' and row()['automatic_control']
    assert automation.begin_attempt(recovered)
    assert row()['attempts'] == 2


@pytest.mark.parametrize('at', [NOW.replace(hour=9,minute=30), NOW + timedelta(days=2)])
def test_once_cannot_authorize_an_expired_or_weekend_window(auto, at):
    auto.clock[0] = at
    response = auto.client.post('/api/v1/market/auction/automation', json={'mode':'once','expected_revision':0})
    assert response.status_code == 422
    assert automation.read_view()['mode'] == 'off' and row() is None


def test_daily_first_enabled_after_cutoff_starts_next_window_without_false_missed_receipt(auto):
    auto.clock[0] = NOW.replace(hour=18,minute=0)
    value = enable(auto)
    assert value['next_run_at'] == '2026-10-30T09:25:05+08:00'
    automation.tick()
    assert row() is None and auto.source.calls == []
    auto.clock[0] = (NOW + timedelta(days=1)).replace(hour=8,minute=0)
    automation.tick()
    assert automation.read_view()['last_run'] is None


def test_previously_enabled_daily_plan_preserves_latest_missed_weekday_receipt_before_next_window(auto):
    auto.clock[0] = NOW.replace(hour=8,minute=0)
    enable(auto)
    auto.clock[0] = (NOW + timedelta(days=1)).replace(hour=8,minute=0)
    automation.tick()
    assert row()['state'] == 'expired' and row()['job_id'] is None
    assert auto.source.calls == []
