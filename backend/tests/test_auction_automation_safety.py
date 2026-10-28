"""自动竞价的独立停止/隔离恢复验收；仅临时业务库与合成来源。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest

from app import core, worker
from app.services import auction_automation as automation
from app.services import auction_service as service, sync_progress, task_service as tasks
from test_auction_snapshots import DAY, NOW, PREVIOUS, isolated as auction_environment


@pytest.fixture
def safety(auction_environment, monkeypatch):
    monkeypatch.setattr(automation, 'now_china', lambda: auction_environment.clock[0])
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE', str(core.data_dir() / 'unused-synthetic-master'))
    return auction_environment


def configuration():
    with core.connect() as connection:
        return dict(connection.execute('SELECT * FROM market_auction_automation WHERE singleton=1').fetchone())


def configure(mode='daily'):
    automation.update_config({'mode': mode, 'expected_revision': configuration()['revision'],
                              'instrument_ids': ['600001.SH']})


def current_job():
    with core.connect() as connection:
        row = connection.execute('''SELECT j.* FROM market_auction_capture c JOIN job j ON j.job_id=c.job_id
            WHERE c.trade_date=?''', (DAY,)).fetchone()
        return dict(row) if row else None


def run_record():
    with core.connect() as connection:
        row = connection.execute('SELECT * FROM market_auction_automation_run WHERE trade_date=?', (DAY,)).fetchone()
        return dict(row) if row else None


def assert_no_collection(environment):
    assert environment.source.calls == []
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 0


def test_default_off_and_enable_read_and_ticks_before_window_do_not_fetch(safety):
    assert configuration()['mode'] == 'off'
    assert isinstance(automation.read_view(), dict)
    automation.tick()
    assert current_job() is None
    configure()
    safety.clock[0] = NOW.replace(hour=9, minute=24, second=59)
    for _ in range(3):
        automation.read_view()
        automation.tick()
    assert current_job() is None
    assert_no_collection(safety)


def test_configuration_cas_rejects_stale_writes_without_scheduling(safety):
    initial = configuration()['revision']
    configure()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        automation.update_config({'mode': 'off', 'expected_revision': initial})
    assert error.value.status_code == 409
    assert configuration()['mode'] == 'daily' and configuration()['revision'] == initial + 1
    assert current_job() is None
    assert_no_collection(safety)


def test_tick_binds_existing_manual_job_without_taking_retry_control(safety):
    manual = service.create_capture({'date': DAY, 'request_key': 'manual-before-auto',
                                     'instrument_ids': ['600001.SH']})
    configure()
    automation.tick()
    assert current_job()['job_id'] == manual['task_id']
    assert run_record()['job_id'] == manual['task_id']
    assert not run_record()['automatic_control']
    claimed = tasks.claim_next('manual-safety-worker')
    tasks.finish(claimed['job_id'], 'failed', error='合成手动任务失败', lease_owner=claimed['lease_owner'])
    safety.clock[0] += timedelta(seconds=60)
    for _ in range(3):
        automation.tick()
    assert current_job()['status'] == 'failed'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1
    assert_no_collection(safety)


def test_concurrent_scheduler_ticks_create_one_daily_job_without_fetching(safety):
    configure()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: automation.tick(), range(8)))
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM market_auction_automation_run').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1
    assert_no_collection(safety)


@pytest.mark.parametrize('action,expected', [('pause', 'paused'), ('cancel', 'cancelled')])
def test_explicit_stop_survives_ticks_and_off_then_daily_configuration(safety, action, expected):
    configure()
    automation.tick()
    original = current_job()['job_id']
    tasks.change_task(original, action)
    automation.tick()
    configure('off')
    configure('daily')
    safety.clock[0] += timedelta(seconds=60)
    automation.tick()
    assert current_job()['job_id'] == original and current_job()['status'] == expected
    assert not run_record()['automatic_control']
    assert tasks.claim_next('must-not-resume') is None
    assert_no_collection(safety)


def test_disabling_during_running_attempt_prevents_later_automatic_retry(safety):
    configure()
    automation.tick()
    job = tasks.claim_next('disable-race-safety-worker')
    def disable_during_request(path, _params):
        if path.endswith('/calendar/trading-days'):
            configure('off')
        if path.endswith('/auction/snapshot'):
            return {'code': 0, 'data': {'timestamp': int(safety.clock[0].timestamp() * 1000),
                    'auction_phase': 'final', 'data_status': 'not_ready', 'item': [], 'total': 0}}
    safety.source.hook = disable_during_request
    with pytest.raises(automation.AuctionNotReady):
        worker.dispatch(job)
    tasks.finish(job['job_id'], 'failed', error='合成终态尚未就绪', lease_owner=job['lease_owner'])
    safety.clock[0] += timedelta(seconds=60)
    automation.tick()
    assert [path for path, _ in safety.source.calls] == [
        '/api/a-share/calendar/trading-days', '/api/a-share/auction/snapshot']
    assert current_job()['status'] == 'failed'
    assert not run_record()['automatic_control'] and run_record()['retry_after_at_ms'] is None
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 0


def test_manual_resume_after_pause_is_detached_from_disabled_automation(safety):
    configure()
    automation.tick()
    original = current_job()['job_id']
    tasks.change_task(original, 'pause')
    configure('off')
    tasks.change_task(original, 'resume')
    assert not run_record()['automatic_control']
    job = tasks.claim_next('explicit-manual-resume-worker')
    assert job['job_id'] == original
    worker.dispatch(job)
    assert current_job()['status'] == 'succeeded'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 1


@pytest.mark.parametrize('action,expected', [('pause', 'paused'), ('cancel', 'cancelled')])
def test_manual_stop_while_waiting_for_not_ready_retry_remains_terminal_for_scheduler(safety, action, expected):
    def not_ready(path, _params):
        if path.endswith('/auction/snapshot'):
            return {'code': 0, 'data': {'timestamp': int(safety.clock[0].timestamp() * 1000),
                    'auction_phase': 'final', 'data_status': 'not_ready', 'item': [], 'total': 0}}
    safety.source.hook = not_ready
    configure()
    automation.tick()
    job = tasks.claim_next('waiting-stop-safety-worker')
    with pytest.raises(automation.AuctionNotReady):
        worker.dispatch(job)
    tasks.finish(job['job_id'], 'failed', error='合成终态未就绪', lease_owner=job['lease_owner'])
    assert run_record()['state'] == 'waiting_retry' and run_record()['retry_after_at_ms'] is not None
    calls = list(safety.source.calls)
    tasks.change_task(job['job_id'], action)
    safety.clock[0] += timedelta(seconds=60)
    automation.tick()
    assert current_job()['status'] == expected and current_job()['job_id'] == job['job_id']
    assert not run_record()['automatic_control'] and run_record()['retry_after_at_ms'] is None
    assert tasks.claim_next('waiting-stop-must-not-resume') is None
    assert safety.source.calls == calls


def test_once_authorization_cannot_run_again_after_restart_or_on_next_day(safety):
    configure('once')
    automation.tick()
    job = tasks.claim_next('once-safety-worker')
    worker.dispatch(job)
    calls = list(safety.source.calls)
    for _ in range(3):
        automation.tick()
    safety.clock[0] += timedelta(days=1)
    automation.tick()
    assert automation.read_view()['enabled'] is False
    assert safety.source.calls == calls
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 1


def test_increment_awaiting_confirmation_blocks_worker_side_schedule(safety):
    configure()
    root = tasks.create_task('increment', {'increment_session_version': sync_progress.LOCK_VERSION},
                             request_key='safety-failed-increment')
    tasks.finish(root['task_id'], 'failed', error='合成增量失败，等待确认')
    with core.connect() as connection:
        assert sync_progress.lock_record(connection) is not None
        assert connection.execute("SELECT count(*) FROM job WHERE status IN ('queued','running')").fetchone()[0] == 0
    automation.tick()
    assert current_job() is None
    assert_no_collection(safety)
    sync_progress.acknowledge(request_key=root['request_key'])
    automation.tick()
    assert current_job() is not None
    assert_no_collection(safety)


def test_scheduler_stops_at_930_and_does_not_backfill_missed_day(safety):
    configure()
    safety.clock[0] = NOW.replace(hour=9, minute=30, second=0)
    automation.tick()
    assert current_job() is None
    safety.clock[0] = NOW + timedelta(days=1)
    automation.tick()
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_capture WHERE trade_date=?', (DAY,)).fetchone()[0] == 0
    assert_no_collection(safety)


def test_weekday_holiday_verifies_calendar_without_auction_or_benchmark_requests(safety):
    def holiday(path, _params):
        if path.endswith('/calendar/trading-days'):
            return {'code': 0, 'data': {'timestamp': int(safety.clock[0].timestamp() * 1000),
                    'item': [{'date': PREVIOUS.replace('-', '')}, {'date': '20261030'}]}}
    safety.source.hook = holiday
    configure()
    automation.tick()
    job = tasks.claim_next('holiday-safety-worker')
    worker.dispatch(job)
    safety.clock[0] += timedelta(seconds=60)
    automation.tick()
    assert [path for path, _ in safety.source.calls] == ['/api/a-share/calendar/trading-days']
    assert current_job()['status'] == 'no_change'
    assert run_record()['state'] == 'skipped' and run_record()['retry_after_at_ms'] is None
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 0


@pytest.mark.parametrize('days', [[], [PREVIOUS]])
def test_empty_or_previous_only_calendar_is_unknown_not_confirmed_holiday(safety, days):
    def incomplete_calendar(path, _params):
        if path.endswith('/calendar/trading-days'):
            return {'code': 0, 'data': {'timestamp': int(safety.clock[0].timestamp() * 1000),
                    'item': [{'date': day.replace('-', '')} for day in days]}}
    safety.source.hook = incomplete_calendar
    configure()
    automation.tick()
    job = tasks.claim_next('calendar-gap-safety-worker')
    with pytest.raises(ValueError, match='日历'):
        worker.dispatch(job)
    tasks.finish(job['job_id'], 'failed', error='合成日历覆盖未知', lease_owner=job['lease_owner'])
    safety.clock[0] += timedelta(seconds=60)
    automation.tick()
    assert current_job()['status'] == 'failed'
    saved = run_record()
    assert saved['state'] == 'failed' and saved['outcome'] == 'failed'
    assert not saved['automatic_control'] and saved['retry_after_at_ms'] is None
    assert [path for path, _ in safety.source.calls] == ['/api/a-share/calendar/trading-days']
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 0


def test_restore_disables_daily_authorization_and_old_run_before_any_tick(safety, tmp_path, monkeypatch):
    configure()
    automation.tick()
    original = current_job()['job_id']
    revision = configuration()['revision']
    monkeypatch.syspath_prepend(str(core.PROJECT_ROOT / 'scripts'))
    from backup import perform_backup
    from restore import perform_restore
    bundle = tmp_path.parent / (tmp_path.name + '-auto-safety-bundle')
    restored = tmp_path.parent / (tmp_path.name + '-auto-safety-restored')
    perform_backup(core.data_dir(), bundle)
    perform_restore(bundle, restored)
    with sqlite3.connect(restored / 'business/app.sqlite3') as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA recursive_triggers=ON')
        connection.execute('PRAGMA busy_timeout=10000')
        saved = connection.execute('SELECT mode,revision FROM market_auction_automation WHERE singleton=1').fetchone()
        assert saved == ('off', revision + 1)
        assert connection.execute('SELECT status FROM job WHERE job_id=?', (original,)).fetchone()[0] == 'paused'
        assert connection.execute('SELECT automatic_control FROM market_auction_automation_run WHERE job_id=?', (original,)).fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 0
    monkeypatch.setenv('SHIXU_DATA_DIR', str(restored))
    for _ in range(3):
        automation.tick()
    assert configuration()['mode'] == 'off'
    assert tasks.claim_next('restored-must-stay-idle') is None
    assert_no_collection(safety)
    # 对副本的禁用不反向修改原业务库中的持续授权。
    with sqlite3.connect(tmp_path / 'business/app.sqlite3') as connection:
        assert connection.execute('SELECT mode,revision FROM market_auction_automation WHERE singleton=1').fetchone() == ('daily', revision)
