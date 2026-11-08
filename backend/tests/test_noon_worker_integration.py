"""真实dispatch/HTTP入口的隔离集成；只替换来源和既有后续链，不访问网络。"""
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import core, worker
from app.adapters import providers
from app.routers import noon as noon_router, platform
from app.services import noon_retention, noon_service, source_service, sync_clock, sync_service
from app.services import task_service as tasks, workspace_automation
from app.services.noon_collection import CHINA

DAY = '2026-10-28'
NOON = datetime(2026, 10, 28, 11, 45, tzinfo=CHINA)


@pytest.fixture
def isolated_worker(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    monkeypatch.setattr(providers.ProviderClient, 'request', lambda *_args, **_kwargs: pytest.fail('integration must not fetch'))
    calls = []
    monkeypatch.setattr(worker, '_queue_local_sync_checks', lambda *_args, **_kwargs: calls.append('local_checks'))
    monkeypatch.setattr(worker, '_queue_followup', lambda *_args, **_kwargs: calls.append('followup'))
    monkeypatch.setattr(workspace_automation, 'run_after_sync', lambda *_args, **_kwargs: calls.append('workspace'))
    return calls


def claimed_increment(*, policy=True, kind='increment'):
    params = {'source_id': 'fuyao'}
    if policy:
        params['noon_retention_policy'] = noon_retention.VERSION if policy is True else policy
    created = tasks.create_task(kind, params, DAY, request_key='fixture-increment')
    claimed = tasks.claim_next('fixture-worker')
    assert claimed['job_id'] == created['task_id']
    return claimed


@pytest.mark.parametrize('result,expected_status', [
    ({'quality': 'complete', 'data_status': {'data_end': DAY, 'snapshot_id': 'fixture-generation'}}, 'succeeded'),
    ({'quality': 'partial', 'data_status': {'data_end': DAY, 'snapshot_id': 'fixture-generation'}}, 'partial'),
    ({'status': 'no_change', 'quality': 'partial', 'data_status': {'data_end': DAY, 'snapshot_id': 'fixture-generation'}}, 'no_change'),
])
@pytest.mark.parametrize('policy', [noon_retention.LEGACY_VERSION, noon_retention.VERSION])
def test_dispatch_calls_cleanup_after_persisted_sync_success_including_no_change(isolated_worker, monkeypatch, result, expected_status, policy):
    calls = isolated_worker; claimed = claimed_increment(policy=policy)
    def execute(identifier, callback):
        assert tasks.task_view(identifier)['status'] == 'running'
        core.set_setting('fixture-publication', {'accepted': True, 'day': DAY})
        calls.append('published_or_verified')
        return result
    def cleanup(job, completed):
        assert job['job_id'] == claimed['job_id'] and completed is result
        assert tasks.task_view(job['job_id'])['status'] == expected_status
        assert core.setting('fixture-publication')['accepted'] is True
        calls.append('noon_retention')
        return {'status': 'completed'}
    monkeypatch.setattr(sync_service, 'execute_sync', execute)
    monkeypatch.setattr(noon_retention, 'after_increment', cleanup)
    worker.dispatch(claimed)
    assert tasks.task_view(claimed['job_id'])['status'] == expected_status
    assert calls == ['published_or_verified', 'noon_retention', 'local_checks', 'workspace', 'followup']


@pytest.mark.parametrize('status', ['succeeded', 'partial', 'no_change'])
def test_completed_dispatch_reentry_retries_cleanup_without_refetch_or_workspace(isolated_worker, monkeypatch, status):
    claimed = claimed_increment(); tasks.finish(claimed['job_id'], status)
    monkeypatch.setattr(sync_service, 'execute_sync', lambda *_: pytest.fail('completed increment must not fetch again'))
    def cleanup(job, result):
        assert job['job_id'] == claimed['job_id'] and result is None
        assert tasks.task_view(job['job_id'])['status'] == status
        isolated_worker.append('noon_retention')
    monkeypatch.setattr(noon_retention, 'after_increment', cleanup)
    worker.dispatch(claimed)
    assert isolated_worker == ['noon_retention', 'local_checks', 'followup']


@pytest.mark.parametrize('kind,policy', [('increment', False), ('initialise', True)])
def test_legacy_increment_or_initialisation_does_not_expand_cleanup_scope(isolated_worker, monkeypatch, kind, policy):
    claimed = claimed_increment(policy=policy, kind=kind)
    monkeypatch.setattr(sync_service, 'execute_sync', lambda *_: {'quality': 'complete'})
    monkeypatch.setattr(noon_retention, 'after_increment', lambda *_: pytest.fail('unfrozen task cannot clean noon sources'))
    worker.dispatch(claimed)
    assert tasks.task_view(claimed['job_id'])['status'] == 'succeeded'
    assert isolated_worker == ['local_checks', 'workspace', 'followup']


@pytest.mark.parametrize('result,expected_status', [({'quality': 'complete'}, 'succeeded'), ({'status': 'no_change'}, 'no_change')])
def test_unexpected_cleanup_exception_cannot_turn_accepted_sync_into_failure(isolated_worker, monkeypatch, result, expected_status):
    claimed = claimed_increment()
    def execute(*_):
        core.set_setting('fixture-publication', {'accepted': True})
        return result
    monkeypatch.setattr(sync_service, 'execute_sync', execute)
    monkeypatch.setattr(noon_retention, 'after_increment', lambda *_: (_ for _ in ()).throw(RuntimeError('fixture failure')))
    worker.dispatch(claimed)
    retained = tasks.task_view(claimed['job_id'])
    assert retained['status'] == expected_status and retained['error_summary'] is None
    assert core.setting('fixture-publication') == {'accepted': True}
    assert isolated_worker == ['local_checks', 'workspace', 'followup']
    warnings = [item for item in retained['logs'] if item['stage'] == 'noon_retention_warning']
    assert len(warnings) == 1 and warnings[0]['level'] == 'warning'


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'paused'])
def test_stopped_worker_task_never_enters_cleanup(isolated_worker, monkeypatch, status):
    claimed = claimed_increment()
    if status == 'paused':
        tasks.change_task(claimed['job_id'], 'pause')
    else:
        tasks.finish(claimed['job_id'], status)
    monkeypatch.setattr(noon_retention, 'after_increment', lambda *_: pytest.fail('stopped task cannot clean'))
    monkeypatch.setattr(sync_service, 'execute_sync', lambda *_: pytest.fail('stopped task cannot run sync'))
    if status == 'paused':
        with pytest.raises(tasks.TaskPaused):
            worker.dispatch(claimed)
    else:
        worker.dispatch(claimed)
    assert isolated_worker == []


def test_new_platform_increment_freezes_policy_without_touching_sources(isolated_worker, monkeypatch):
    monkeypatch.setattr(sync_clock, 'now_china', lambda: NOON.replace(hour=18, minute=30))
    monkeypatch.setattr(source_service, 'client', lambda *_: SimpleNamespace(source_id='fuyao', key='fixture-key'))
    app = FastAPI(); app.include_router(platform.router)
    response = TestClient(app).post('/api/v1/sync/tasks', json={'mode': 'increment', 'request_key': 'new-increment'})
    assert response.status_code == 200, response.text
    assert response.json()['parameters']['noon_retention_policy'] == noon_retention.VERSION


@pytest.fixture
def manual_noon(isolated_worker, monkeypatch):
    clock = [NOON]
    monkeypatch.setattr(noon_service, 'now_china', lambda: clock[0])
    monkeypatch.setattr(sync_clock, 'local_now', lambda *_: clock[0])
    monkeypatch.setattr(noon_service, '_configured', lambda *_: True)
    monkeypatch.setattr(tasks, 'initialization_state', lambda *_: {'main_published': True, 'can_initialize': False})
    app = FastAPI(); app.include_router(noon_router.router); app.include_router(platform.router)
    client = TestClient(app)
    response = client.post('/api/v1/market/noon/jobs', json={'date': DAY, 'request_key': 'fixture-noon-once'})
    assert response.status_code == 200, response.text
    return client, response.json()['task_id'], clock


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_failed_or_cancelled_day_cannot_bypass_daily_slot_through_generic_retry_or_new_key(manual_noon, status):
    client, identifier, _ = manual_noon
    claimed = tasks.claim_next('fixture-noon-worker'); assert claimed['job_id'] == identifier
    tasks.finish(identifier, status)
    before = tasks.list_tasks(summary=True)['total']
    retry = client.post('/api/v1/tasks/' + identifier + '/retry')
    assert retry.status_code == 409 and '一次' in retry.json()['detail']
    second = client.post('/api/v1/market/noon/jobs', json={'date': DAY, 'request_key': 'changed-request'})
    assert second.status_code == 409
    replay = client.post('/api/v1/market/noon/jobs', json={'date': DAY, 'request_key': 'fixture-noon-once'})
    assert replay.status_code == 200 and replay.json()['task_id'] == identifier
    assert tasks.list_tasks(summary=True)['total'] == before
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1


def test_generic_resume_reuses_same_daily_task_only_inside_original_window(manual_noon):
    client, identifier, clock = manual_noon
    assert client.post('/api/v1/tasks/' + identifier + '/pause').status_code == 409
    tasks.change_task(identifier, 'pause')  # A pre-existing stopped task, not a UI action.
    clock[0] = NOON.replace(hour=11, minute=30, second=59)
    early = client.post('/api/v1/tasks/' + identifier + '/resume')
    assert early.status_code == 409 and '11:31' in early.json()['detail']
    assert tasks.task_view(identifier)['status'] == 'paused'
    clock[0] = NOON.replace(hour=11, minute=31, second=0)
    within = client.post('/api/v1/tasks/' + identifier + '/resume')
    assert within.status_code == 200 and within.json()['task_id'] == identifier and within.json()['status'] == 'queued'
    tasks.change_task(identifier, 'pause')
    clock[0] = NOON.replace(hour=13, minute=0)
    expired = client.post('/api/v1/tasks/' + identifier + '/resume')
    assert expired.status_code == 409 and '窗口' in expired.json()['detail']
    clock[0] = NOON.replace(day=29)
    cross_day = client.post('/api/v1/tasks/' + identifier + '/resume')
    assert cross_day.status_code == 409
    assert tasks.task_view(identifier)['status'] == 'paused'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1
