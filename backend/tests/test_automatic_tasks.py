"""Daily automatic plans: isolated SQLite, fixed clocks and synthetic calendar only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from time import perf_counter, process_time
import tracemalloc

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import core
from app.routers import automatic_tasks as router
from app.services import automatic_tasks as service, noon_service, source_service, sync_clock
from app.services import task_service as tasks

DAY = '2026-10-29'
NOW = datetime(2026, 10, 29, 12, 5, tzinfo=sync_clock.CHINA)


class CalendarSource:
    source_id = 'fuyao'
    key = 'synthetic-calendar-key'

    def __init__(self):
        self.calls = []
        self.days = ['2026-10-27', '2026-10-28', DAY, '2026-10-30', '2026-11-02']
        self.hook = None

    def request(self, path, params=None, *, before_attempt=None):
        # A real network call would occur only here, outside every write txn.
        with core.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
        assert path == '/api/a-share/calendar/trading-days'
        self.calls.append(path)
        if before_attempt:
            before_attempt()
        if self.hook:
            changed = self.hook(before_attempt)
            if changed is not None:
                return changed
        return {'code': 0, 'data': {'item': [{'date': day.replace('-', '')} for day in self.days]}}


@pytest.fixture
def scheduled(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR', 'SHIXU_CREDENTIAL_DIR'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE', str(tmp_path / 'unused-master'))
    core.ensure_ready(); core.clear_market_cache()
    clock = [NOW]
    monkeypatch.setattr(sync_clock, 'now_china', lambda: clock[0])
    monkeypatch.setattr(noon_service, 'now_china', lambda: clock[0])
    monkeypatch.setattr(tasks, 'now_ms', lambda: int(clock[0].timestamp() * 1000))
    monkeypatch.setattr(noon_service, '_configured', lambda _connection: True)
    source = CalendarSource()
    monkeypatch.setattr(source_service, 'client', lambda *_args: source)
    monkeypatch.setattr(core, 'get_market', lambda: pytest.fail('scheduler must not open market/DuckDB'))
    # A tiny synthetic head makes the standard initialization gate real.
    job = tasks.create_task('initialise', {}, request_key='fixture-initialise')
    path = core.published_dir() / 'fixture.json'; core.atomic_json(path, {'synthetic': True})
    core.publish_snapshot(module='daily_raw', job_id=job['job_id'], start='2026-10-27', end='2026-10-28',
                          artifacts=[{'object_key': 'fixture.json', 'sha256': core.hash_file(path), 'byte_count': path.stat().st_size, 'row_count': 1}])
    tasks.finish(job['job_id'])
    return source, clock


def enable(*, noon=True, after_close=False, noon_time='12:05', close_time='18:30', expected=None):
    payload = {'noon': {'enabled': noon, 'time': noon_time}, 'after_close': {'enabled': after_close, 'time': close_time}}
    if expected is not None:
        payload['expected_revision'] = expected
    return service.configure(payload)


def actual_jobs():
    with core.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM job WHERE kind<>'initialise' ORDER BY created_at_ms,rowid")]


def test_default_get_and_put_are_local_read_config_only(scheduled):
    source, _ = scheduled
    app = FastAPI(); app.include_router(router.router); client = TestClient(app)
    first = client.get('/api/v1/settings/automatic-tasks')
    assert first.status_code == 200
    state = first.json()
    assert state['noon'] == {'enabled': False, 'time': '12:05'}
    assert state['after_close'] == {'enabled': False, 'time': '18:30'}
    assert state['revision'] == 0 and state['timezone'] == 'Asia/Shanghai'
    assert state['last_checks'] == {'noon': None, 'after_close': None}
    saved = client.put('/api/v1/settings/automatic-tasks', json={**{key: state[key] for key in service.KINDS}, 'expected_revision': 0})
    assert saved.status_code == 200 and saved.json()['revision'] == 0
    assert source.calls == [] and actual_jobs() == []


@pytest.mark.parametrize('change', [
    {'unknown': True}, {'noon': {}}, {'noon': {'enabled': 1, 'time': '12:05'}},
    {'noon': {'enabled': 'true', 'time': '12:05'}},
    {'noon': {'enabled': True, 'time': '11:30'}}, {'noon': {'enabled': True, 'time': '13:00'}},
    {'noon': {'enabled': True, 'time': '12:5'}}, {'noon': {'enabled': True, 'time': '24:00'}},
    {'after_close': {'enabled': True, 'time': '17:59'}},
    {'expected_revision': True}, {'expected_revision': -1}, {'expected_revision': None},
])
def test_config_validation_and_unknown_fields(scheduled, change):
    payload = {key: dict(value) for key, value in service.DEFAULTS.items()}; payload.update(change)
    with pytest.raises(HTTPException) as error:
        service.configure(payload)
    assert error.value.status_code == 422 and actual_jobs() == []


def test_configuration_revision_and_restart_persist_without_job(scheduled):
    assert enable(expected=0)['revision'] == 1
    with pytest.raises(HTTPException) as error:
        enable(after_close=True, expected=0)
    assert error.value.status_code == 409
    assert service.read_settings()['noon']['enabled'] is True
    assert enable(expected=1)['revision'] == 1
    assert actual_jobs() == []


@pytest.mark.parametrize('stamp,kind,enabled,clock,expected', [
    ('2026-10-29T11:30:59+08:00', 'noon', True, '11:31', 'waiting_time'),
    ('2026-10-29T11:31:00+08:00', 'noon', True, '11:31', 'due'),
    ('2026-10-29T12:59:59+08:00', 'noon', True, '12:05', 'due'),
    ('2026-10-29T13:00:00+08:00', 'noon', True, '12:05', 'missed'),
    ('2026-10-29T04:05:00+00:00', 'noon', True, '12:05', 'due'),
    ('2026-10-29T18:29:59+08:00', 'after_close', True, '18:30', 'waiting_time'),
    ('2026-10-29T18:30:00+08:00', 'after_close', True, '18:30', 'due'),
    ('2026-10-29T23:59:59+08:00', 'after_close', True, '18:30', 'due'),
    ('2026-10-30T00:00:00+08:00', 'after_close', True, '18:30', 'waiting_time'),
    ('2026-10-29T12:05:00+08:00', 'noon', False, '12:05', 'disabled'),
])
def test_pure_clock_beijing_cutoffs(stamp, kind, enabled, clock, expected):
    assert service.due_state(kind, {'enabled': enabled, 'time': clock}, datetime.fromisoformat(stamp)) == expected


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError):
        service.due_state('noon', {'enabled': True, 'time': '12:05'}, datetime(2026, 10, 29, 12, 5))


def test_noon_auto_uses_public_once_per_day_creator_and_durable_restart(scheduled):
    source, clock = scheduled; enable()
    first = service.AutomaticReviewScheduler().tick()
    assert len(first['created']) == 1
    task = tasks.task_view(first['created'][0])
    assert task['kind'] == 'source_check' and task['parameters']['operation'] == 'noon_snapshot'
    assert task['parameters']['daily_once'] is True and task['target_date'] == DAY
    authorization = next(item['details'] for item in task['logs'] if item['stage'] == 'automatic_schedule')
    assert authorization['calendar_source_id'] == 'fuyao' and len(authorization['calendar_source_sha256']) == 64
    assert authorization['configuration_revision'] == 1 and authorization['scheduled_time'] == '12:05' and authorization['timezone'] == 'Asia/Shanghai'
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert len(actual_jobs()) == 1 and len(source.calls) == 1
    clock[0] = clock[0].replace(hour=18)
    service.AutomaticReviewScheduler().tick()
    state = service.read_settings()['last_checks']['noon']
    assert state['state'] == 'existing' and state['task_id'] == task['job_id']


def test_concurrent_schedulers_create_only_one_task_and_calendar_request(scheduled):
    source, _ = scheduled; enable()
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(lambda _: service.AutomaticReviewScheduler().tick(), range(8)))
    assert len(actual_jobs()) == 1 and len(source.calls) == 1
    assert sum(len(value['created']) for value in result) == 1


@pytest.mark.parametrize('status', ['succeeded', 'partial', 'no_change', 'paused', 'cancelled', 'failed'])
@pytest.mark.parametrize('kind', ['noon', 'after_close'])
def test_existing_manual_or_auto_task_is_never_retried_or_resumed(scheduled, kind, status):
    source, clock = scheduled
    enable(noon=kind == 'noon', after_close=kind == 'after_close')
    if kind == 'after_close':
        clock[0] = clock[0].replace(hour=18, minute=30)
    created = service.AutomaticReviewScheduler().tick()['created'][0]
    with core.connect() as connection:
        connection.execute('UPDATE job SET status=? WHERE job_id=?', (status, created))
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert tasks.task_view(created)['status'] == status
    assert len(actual_jobs()) == 1 and len(source.calls) == 1


def test_after_close_reuses_complete_chain_and_transactional_workbench_lock(scheduled):
    from app.services import sync_progress
    source, clock = scheduled; enable(noon=False, after_close=True)
    clock[0] = clock[0].replace(hour=18, minute=30)
    created = service.AutomaticReviewScheduler().tick()['created'][0]
    job = tasks.task_view(created); params = job['parameters']
    assert job['kind'] == 'increment' and params['sync_completion_date'] == DAY
    assert params['data_chain_version'] == 'sector-charts-increment-v1'
    assert params['noon_close_policy'] == 'noon-close-v1'
    assert params['line_alert_check_version'] == 'explicit-local-line-alert-v1'
    assert params['noon_retention_policy'] == 'noon-retention-28d-v1'
    assert params['auction_retention_policy'] == 'auction-retention-28d-v1'
    with core.connect() as connection:
        assert sync_progress.lock_record(connection)['root_task_id'] == created
    assert not any(job['kind'] in ('selection', 'tracking_review') for job in actual_jobs())


def test_same_day_missed_restart_only_no_cross_midnight_replay(scheduled):
    source, clock = scheduled; enable(noon=False, after_close=True)
    clock[0] = clock[0].replace(hour=23, minute=59)
    assert len(service.AutomaticReviewScheduler().tick()['created']) == 1
    assert len(source.calls) == 1
    clock[0] += timedelta(minutes=2)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert len(actual_jobs()) == 1 and len(source.calls) == 1


def test_noon_window_missed_does_not_fetch_or_create(scheduled):
    source, clock = scheduled; enable(); clock[0] = clock[0].replace(hour=13, minute=0)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert source.calls == [] and actual_jobs() == []
    assert service.read_settings()['last_checks']['noon']['state'] == 'missed'


@pytest.mark.parametrize('day', ['2026-10-31', '2026-10-01'])
def test_official_calendar_not_weekday_guesses_controls_weekends_and_holidays(scheduled, day):
    source, clock = scheduled; enable()
    clock[0] = datetime.fromisoformat(day + 'T12:05:00+08:00')
    source.days = ['2026-09-30', '2026-10-09', '2026-10-30', '2026-11-02']
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert actual_jobs() == [] and len(source.calls) == 1
    assert service.read_settings()['last_checks']['noon']['state'] == 'not_trading'


def test_calendar_missing_today_is_unknown_not_a_closed_day(scheduled):
    source, _ = scheduled; enable(); source.days = ['2026-10-27', '2026-10-28']
    service.AutomaticReviewScheduler().tick()
    assert actual_jobs() == []
    assert service.read_settings()['last_checks']['noon']['state'] == 'calendar_unknown'


def test_calendar_response_row_budget_fails_closed(scheduled):
    source, _ = scheduled; enable()
    source.hook = lambda _before: {'data': {'item': [{'date': '20261029'}] * (service.MAX_CALENDAR_DAYS + 1)}}
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert actual_jobs() == [] and core.setting(service.CALENDAR_KEY)['state'] == 'failed'


def test_calendar_failure_and_transport_retry_budget_survive_restart(scheduled):
    source, clock = scheduled; enable(noon=True, after_close=True)
    def fail(before):
        before()  # A ProviderClient retry would enter this callback before HTTP.
        pytest.fail('second request must have been rejected')
    source.hook = fail
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    clock[0] = clock[0].replace(hour=18, minute=30)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert len(source.calls) == 1 and actual_jobs() == []
    assert core.setting(service.CALENDAR_KEY)['state'] == 'failed'


def test_both_daily_plans_share_calendar_receipt(scheduled):
    source, clock = scheduled; enable(noon=True, after_close=True)
    noon = service.AutomaticReviewScheduler().tick()['created'][0]; tasks.finish(noon)
    clock[0] = clock[0].replace(hour=18, minute=30)
    assert len(service.AutomaticReviewScheduler().tick()['created']) == 1
    assert len(source.calls) == 1 and len(actual_jobs()) == 2


def test_disable_during_calendar_fetch_prevents_submission(scheduled):
    source, _ = scheduled; enable()
    source.hook = lambda _before: (enable(noon=False), None)[1]
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert actual_jobs() == [] and service.read_settings()['noon']['enabled'] is False


def test_no_initialization_or_queued_work_is_started_by_scheduler(scheduled):
    source, _ = scheduled; enable()
    queued = tasks.create_task('research', {'fixture': 'only queue, never execute'})
    service.AutomaticReviewScheduler().tick()
    assert len(actual_jobs()) == 1 and tasks.task_view(queued['job_id'])['status'] == 'queued'
    assert source.calls == []
    with core.connect() as connection:
        connection.execute('DELETE FROM published_head')
    tasks.finish(queued['job_id'])
    service.AutomaticReviewScheduler().tick()
    assert len(actual_jobs()) == 1 and source.calls == []
    assert service.read_settings()['last_checks']['noon']['state'] == 'blocked'


def test_unacknowledged_previous_increment_blocks_next_plan(scheduled):
    source, clock = scheduled; enable(noon=False, after_close=True)
    clock[0] = clock[0].replace(hour=18, minute=30)
    created = service.AutomaticReviewScheduler().tick()['created'][0]; tasks.finish(created['job_id'] if isinstance(created, dict) else created)
    clock[0] += timedelta(days=1)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert len(actual_jobs()) == 1 and len(source.calls) == 1
    assert '确认' in service.read_settings()['last_checks']['after_close']['reason']


def test_claim_commit_to_task_crash_recovers_idempotently(scheduled, monkeypatch):
    _source, clock = scheduled; enable(); original = service._create
    def after_commit(*args):
        original(*args)
        raise SystemExit('synthetic process loss after durable job')
    monkeypatch.setattr(service, '_create', after_commit)
    with pytest.raises(SystemExit):
        service.AutomaticReviewScheduler().tick()
    monkeypatch.setattr(service, '_create', original)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert len(actual_jobs()) == 1


def test_claim_before_task_crash_waits_lease_without_creating_duplicate(scheduled, monkeypatch):
    _source, clock = scheduled; enable(); original = service._create
    monkeypatch.setattr(service, '_create', lambda *_: (_ for _ in ()).throw(SystemExit('synthetic before creation')))
    with pytest.raises(SystemExit):
        service.AutomaticReviewScheduler().tick()
    monkeypatch.setattr(service, '_create', original)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    clock[0] += timedelta(seconds=service.CLAIM_SECONDS + 1)
    assert len(service.AutomaticReviewScheduler().tick()['created']) == 1
    assert len(actual_jobs()) == 1


@pytest.mark.parametrize('kind', ['noon', 'after_close'])
@pytest.mark.parametrize('action', ['disable', 'time_change', 'window_end'])
def test_actual_creation_transaction_rechecks_switch_revision_and_current_clock(scheduled, monkeypatch, kind, action):
    _source, clock = scheduled
    enable(noon=kind == 'noon', after_close=kind == 'after_close')
    if kind == 'after_close':
        clock[0] = clock[0].replace(hour=18, minute=30)
    original = service._create
    def delayed(*args):
        if action == 'disable':
            enable(noon=False, after_close=False)
        elif action == 'time_change':
            enable(noon=kind == 'noon', after_close=kind == 'after_close', noon_time='12:30', close_time='19:00')
        else:
            clock[0] = clock[0].replace(hour=13, minute=0) if kind == 'noon' else clock[0] + timedelta(days=1)
        return original(*args)
    monkeypatch.setattr(service, '_create', delayed)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert actual_jobs() == []
    assert service.read_settings()['last_checks'][kind]['state'] == 'settings_changed'


@pytest.mark.parametrize('kind', ['noon', 'after_close'])
def test_manual_submission_racing_after_claim_wins_without_second_task(scheduled, monkeypatch, kind):
    _source, clock = scheduled
    enable(noon=kind == 'noon', after_close=kind == 'after_close')
    if kind == 'after_close':
        clock[0] = clock[0].replace(hour=18, minute=30)
    original = service._create
    def delayed(*args):
        if kind == 'noon':
            noon_service.create_capture({'date': DAY, 'request_key': 'manual-race'})
        else:
            from app.services.sync_requests import create_sync_request
            create_sync_request({'mode': 'increment', 'request_key': 'manual-race'})
        return original(*args)
    monkeypatch.setattr(service, '_create', delayed)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert len(actual_jobs()) == 1 and actual_jobs()[0]['request_key'] == 'manual-race'


@pytest.mark.parametrize('kind', ['noon', 'after_close'])
def test_other_manual_work_racing_after_claim_waits_and_preserves_today_opportunity(scheduled, monkeypatch, kind):
    _source, clock = scheduled
    enable(noon=kind == 'noon', after_close=kind == 'after_close')
    if kind == 'after_close':
        clock[0] = clock[0].replace(hour=18, minute=30)
    original = service._create
    def delayed(*args):
        tasks.create_task('research', {'fixture': 'race, no execution'}, request_key='manual-other-work')
        return original(*args)
    monkeypatch.setattr(service, '_create', delayed)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert len(actual_jobs()) == 1 and actual_jobs()[0]['kind'] == 'research'
    assert service.read_settings()['last_checks'][kind]['state'] == 'waiting'
    tasks.finish(actual_jobs()[0]['job_id'])
    monkeypatch.setattr(service, '_create', original)
    assert len(service.AutomaticReviewScheduler().tick()['created']) == 1
    assert len(actual_jobs()) == 2


@pytest.mark.parametrize('kind', ['noon', 'after_close'])
@pytest.mark.parametrize('action', ['disable_then_enable', 'later_time'])
def test_unsubmitted_configuration_change_keeps_same_day_new_plan_eligible(scheduled, monkeypatch, kind, action):
    _source, clock = scheduled
    enable(noon=kind == 'noon', after_close=kind == 'after_close')
    if kind == 'after_close':
        clock[0] = clock[0].replace(hour=18, minute=30)
    original = service._create
    def delayed(*args):
        enable(noon=kind == 'noon' and action != 'disable_then_enable',
               after_close=kind == 'after_close' and action != 'disable_then_enable',
               noon_time='12:30' if action == 'later_time' else '12:05',
               close_time='19:00' if action == 'later_time' else '18:30')
        return original(*args)
    monkeypatch.setattr(service, '_create', delayed)
    assert service.AutomaticReviewScheduler().tick()['created'] == [] and actual_jobs() == []
    assert service.read_settings()['last_checks'][kind]['state'] == 'settings_changed'
    monkeypatch.setattr(service, '_create', original)
    if action == 'disable_then_enable':
        enable(noon=kind == 'noon', after_close=kind == 'after_close')
    else:
        assert service.AutomaticReviewScheduler().tick()['created'] == []
        clock[0] = clock[0].replace(hour=12, minute=30) if kind == 'noon' else clock[0].replace(hour=19, minute=0)
    assert len(service.AutomaticReviewScheduler().tick()['created']) == 1
    assert len(actual_jobs()) == 1


def test_legacy_enabled_status_tracks_real_switches(scheduled):
    assert service.enabled() is False
    enable(after_close=True)
    assert service.enabled() is True
    enable(noon=False, after_close=False)
    assert service.enabled() is False


def test_worker_loop_checks_plan_under_its_existing_single_writer_lock(scheduled, monkeypatch):
    from app import worker
    from app.services import research_service
    enable(); seen = []
    monkeypatch.setattr(worker.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(research_service, 'cleanup_expired', lambda **_: None)
    def dispatch(job):
        assert json.loads(job['parameters_json'])['operation'] == 'noon_snapshot'
        seen.append(job['job_id'])
        tasks.finish(job['job_id'])
    monkeypatch.setattr(worker, 'dispatch', dispatch)
    worker.run(once=True)
    assert seen == [actual_jobs()[0]['job_id']] and len(actual_jobs()) == 1


def test_clean_start_after_midnight_never_catches_up_yesterday(scheduled):
    source, clock = scheduled; enable(noon=False, after_close=True)
    clock[0] = (clock[0] + timedelta(days=1)).replace(hour=0, minute=1)
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert actual_jobs() == [] and source.calls == []


def test_calendar_failure_budget_resets_only_on_next_beijing_day(scheduled):
    source, clock = scheduled; enable()
    source.hook = lambda _before: (_ for _ in ()).throw(ValueError('synthetic transport failure'))
    service.AutomaticReviewScheduler().tick()
    source.hook = None
    assert service.AutomaticReviewScheduler().tick()['created'] == [] and len(source.calls) == 1
    clock[0] += timedelta(days=1)
    assert len(service.AutomaticReviewScheduler().tick()['created']) == 1
    assert len(source.calls) == 2


def test_missing_key_and_uninitialized_work_never_use_remote_calendar(scheduled):
    source, _ = scheduled; enable(); source.key = ''
    assert service.AutomaticReviewScheduler().tick()['created'] == []
    assert actual_jobs() == [] and source.calls == []


def test_utc_worker_host_still_creates_beijing_day(scheduled):
    _source, _clock = scheduled; enable()
    result = service.AutomaticReviewScheduler().tick(at=NOW.astimezone(timezone.utc))
    assert len(result['created']) == 1 and actual_jobs()[0]['target_date'] == DAY


@pytest.mark.parametrize('enabled', [False, True])
def test_low_polling_thousand_ticks_no_network_market_or_filesystem(scheduled, monkeypatch, enabled):
    source, clock = scheduled
    if enabled:
        enable(); clock[0] = clock[0].replace(hour=9, minute=0)
    calls = []
    original = service.connect
    def count():
        calls.append(1)
        return original()
    monkeypatch.setattr(service, 'connect', count)
    scheduler = service.AutomaticReviewScheduler()
    tracemalloc.start(); started = perf_counter(); cpu = process_time()
    for index in range(1000):
        scheduler.tick(monotonic=index / 100)
    elapsed = perf_counter() - started; used_cpu = process_time() - cpu
    _current, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    assert len(calls) == 1 and actual_jobs() == [] and source.calls == []
    assert scheduler.tick(monotonic=60)['checked'] is True and len(calls) == 2
    print(json.dumps({'scope': 'synthetic scheduler clock throttle only; excludes network/daemon RSS',
                      'enabled_but_not_due': enabled, 'ticks': 1000, 'sqlite_connections': 1,
                      'wall_ms': round(elapsed * 1000, 3), 'cpu_ms': round(used_cpu * 1000, 3), 'python_tracemalloc_peak_bytes': peak}))
