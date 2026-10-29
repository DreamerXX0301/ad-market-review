"""17:00 Beijing manual-sync rules, isolated SQLite/transport fixtures only."""
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app import core
from app.services import sync_clock as clock, task_service as tasks, sync_service as sync
from test_sync import transport, start_job


@pytest.mark.parametrize('stamp,allowed,safe', [
    ('2026-10-20T16:59:59+08:00', False, '2026-10-19'),
    ('2026-10-20T17:00:00+08:00', True, '2026-10-20'),
    ('2026-10-20T23:59:59+08:00', True, '2026-10-20'),
    ('2026-10-21T00:00:00+08:00', False, '2026-10-20'),
    ('2026-10-20T08:59:59+00:00', False, '2026-10-19'),
    ('2026-10-20T09:00:00+00:00', True, '2026-10-20'),
])
def test_cutoff_timezone_midnight_and_completion_date(stamp, allowed, safe):
    observed = datetime.fromisoformat(stamp); state = clock.sync_window(observed)
    assert state['increment_allowed'] is allowed
    assert state['completion_safe_date'] == clock.safe_completion_date(observed) == safe
    assert state['cutoff'] == '17:00' and state['timezone'] == 'Asia/Shanghai'
    assert (state['reason'] is None) is allowed
    assert (state['next_available_at'] is None) is allowed


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for name in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'):
        monkeypatch.delenv(name, raising=False)
    core.ensure_ready();core.clear_market_cache()
    monkeypatch.setattr(clock, 'now_china', lambda: datetime(2026,10,20,16,59,tzinfo=clock.CHINA))
    yield tmp_path
    core.clear_market_cache()


def freeze(job, day):
    path = core.raw_dir()/'sync'/job['job_id']/'checkpoint.json'
    path.parent.mkdir(parents=True)
    core.atomic_json(path, {'plan': {'kind': 'increment', 'target': day}})


def test_api_rejects_new_increment_before_credentials_or_network(isolated, monkeypatch):
    from app.routers import platform
    monkeypatch.setattr(platform.source_service, 'client', lambda *args: (_ for _ in ()).throw(AssertionError('no credentials/source before17')))
    app = FastAPI();app.include_router(platform.router)
    response = TestClient(app).post('/api/v1/sync/tasks', json={'mode': 'increment'})
    assert response.status_code == 409 and '17:00' in response.json()['detail']
    assert tasks.list_tasks()['total'] == 0


def test_api_initialization_is_still_available_before17_and_metadata_uses_server_clock(isolated, monkeypatch):
    from app.routers import platform
    monkeypatch.setattr(platform.source_service, 'client', lambda *args: SimpleNamespace(source_id='fuyao', key='fixture-not-a-secret'))
    app = FastAPI();app.include_router(platform.router); client = TestClient(app)
    state = client.get('/api/v1/data/status').json()['sync_window']
    assert state['increment_allowed'] is False and state['next_available_at'] == '2026-10-20T17:00:00+08:00'
    response = client.post('/api/v1/sync/tasks', json={'mode': 'initialise'})
    assert response.status_code == 200 and response.json()['kind'] == 'initialise'
    assert response.json()['parameters']['window'] == 'near-five-years'


def test_retry_is_a_new_run_after17_but_old_frozen_resume_is_allowed_before17(isolated, monkeypatch):
    job = tasks.create_task('increment', {})
    freeze(job, '2026-10-19')
    tasks.change_task(job['job_id'], 'pause')
    assert tasks.change_task(job['job_id'], 'resume')['status'] == 'queued'
    tasks.finish(job['job_id'], 'failed')
    with pytest.raises(HTTPException) as error: tasks.change_task(job['job_id'], 'retry')
    assert error.value.status_code == 409
    monkeypatch.setattr(clock, 'now_china', lambda: datetime(2026,10,20,17,tzinfo=clock.CHINA))
    retry = tasks.change_task(job['job_id'], 'retry')
    assert retry['target_date'] == '2026-10-19' and retry['retry_of_job_id'] == job['job_id']


@pytest.mark.parametrize('frozen_day', [None, '2026-10-20', '2026-10-21'])
def test_unplanned_current_or_future_paused_increment_cannot_resume_early(isolated, frozen_day):
    job = tasks.create_task('increment', {})
    if frozen_day: freeze(job, frozen_day)
    tasks.change_task(job['job_id'], 'pause')
    with pytest.raises(HTTPException): tasks.change_task(job['job_id'], 'resume')
    assert tasks.task_view(job['job_id'])['status'] == 'paused'


def test_new_increment_worker_guard_precedes_calendar_and_client(transport, monkeypatch):
    monkeypatch.setattr(sync.source_service, 'client', lambda *args: (_ for _ in ()).throw(AssertionError('no source call')))
    with pytest.raises(ValueError, match='17:00'):
        sync.execute_sync(start_job('increment'))
    assert transport.calls == [] and transport.downloads == []


@pytest.mark.parametrize('hour,minute,expected', [(16,59,'2026-10-19'), (17,0,'2026-10-20')])
def test_initial_target_uses_17_cutoff_without_expanding_original_three_year_window(transport, monkeypatch, hour, minute, expected):
    monkeypatch.setattr(sync, 'now_china', lambda: datetime(2026,10,20,hour,minute,tzinfo=clock.CHINA))
    result = sync.execute_sync(start_job())
    assert result['data_status']['data_end'] == expected
    assert result['coverage']['initial_window'] == 'near-three-years'


def test_worker_resumes_old_frozen_target_before17_without_advancing_to_today(transport, monkeypatch):
    sync.execute_sync(start_job())
    monkeypatch.setattr(sync, 'now_china', lambda: datetime(2026,10,20,17,tzinfo=clock.CHINA))
    job = start_job('increment', target='2026-10-19')
    def pause(stage, percent):
        if stage == '下载daily-k-10d': tasks.change_task(job, 'pause')
    with pytest.raises(tasks.TaskPaused): sync.execute_sync(job, pause)
    monkeypatch.setattr(sync, 'now_china', lambda: datetime(2026,10,20,12,tzinfo=clock.CHINA))
    monkeypatch.setattr(clock, 'now_china', lambda: datetime(2026,10,20,12,tzinfo=clock.CHINA))
    tasks.change_task(job, 'resume');tasks.claim_next('fixture-worker')
    result = sync.execute_sync(job)
    assert result['status'] == 'no_change'
    assert result['data_status']['data_end'] == '2026-10-19'


def test_existing_today_plan_cannot_bypass_guard_before_network(transport, monkeypatch):
    job = start_job('initialise')
    path = core.raw_dir()/'sync'/job/'checkpoint.json';path.parent.mkdir(parents=True,exist_ok=True)
    core.atomic_json(path, {'plan': {'kind': 'initialise', 'target': '2026-10-20'}})
    monkeypatch.setattr(sync.source_service, 'client', lambda *args: (_ for _ in ()).throw(AssertionError('no source call')))
    with pytest.raises(ValueError, match='17:00'): sync.execute_sync(job)
    assert not transport.calls
