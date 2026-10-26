"""The browser cannot bypass one durable increment chain or unlock it early."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import connect
from app.routers.platform import router
from app.services import sync_progress as progress, task_service as tasks
from app.services.increment_access import IncrementWorkbenchMiddleware, business_http_request
from test_platform import isolated


def root(key='manual'):
    return tasks.create_task('increment', {'increment_session_version': progress.LOCK_VERSION,
        'data_chain_version': 'sector-charts-increment-v1'}, request_key=key)


def event(job, stage, **details):
    with connect() as connection:
        tasks.append_event(connection, job['job_id'], stage, stage, details=details)


def stop_root(job, status='failed'):
    tasks.finish(job['job_id'], status)


def complete_chain(job):
    tasks.finish(job['job_id'])
    sector = tasks.create_task('source_check', {'operation': 'sector_charts', 'mode': 'increment',
        'increment_chain': True, 'parent_sync_job_id': job['job_id']})
    event(job, 'followup_queued', next_task_id=sector['job_id'])
    tasks.finish(sector['job_id'])
    supplement = tasks.create_task('source_check', {'operation': 'market_supplement', 'parent_sync_job_id': job['job_id']})
    event(sector, 'followup_queued', next_task_id=supplement['job_id'])
    tasks.finish(supplement['job_id'], 'partial')
    return sector, supplement


def client_with_gate():
    app = FastAPI(); app.include_router(router); app.add_middleware(IncrementWorkbenchMiddleware)
    @app.get('/api/v1/business-fixture')
    def read_business(): return {'business': True}
    @app.post('/api/v1/business-fixture')
    def write_business(): return {'business': True}
    @app.get('/assets/fixture.css')
    def static(): return {'static': True}
    @app.get('/health')
    def health(): return {'status': 'ok'}
    return TestClient(app)


def test_server_discovery_survives_missing_browser_storage_and_ack_is_durable(isolated):
    job = root(); client = client_with_gate()
    view = client.get('/api/v1/sync/session').json()['active']
    assert view['root_task_id'] == job['job_id'] and view['locked']
    for method in ('get', 'post', 'put', 'patch', 'delete'):
        response = getattr(client, method)('/api/v1/business-fixture')
        assert response.status_code == 409
        assert response.json()['detail']['increment_session']['root_task_id'] == job['job_id']
    assert client.get('/api/v1/health').status_code == 200
    assert client.get('/health').status_code == 200
    assert client.get('/assets/fixture.css').status_code == 200
    assert client.get('/#strategy').status_code != 409
    assert client.get('/api/v1/tasks/' + job['job_id']).status_code == 200
    other = tasks.create_task('research', {})  # Worker-side creation is untouched.
    assert client.get('/api/v1/tasks/' + other['job_id']).status_code == 409
    assert client.post('/api/v1/tasks/' + other['job_id'] + '/cancel').status_code == 409
    assert client.post('/api/v1/sync/session/acknowledge', json={'request_key': job['request_key']}).status_code == 409
    complete_chain(job)
    view = client.get('/api/v1/sync/session').json()['active']
    assert view['can_acknowledge'] and view['status'] == 'partial' and view['locked']
    response = client.post('/api/v1/sync/session/acknowledge', json={'request_key': job['request_key'],
        'task_id': job['job_id'], 'session_epoch': view['session_epoch']})
    assert response.status_code == 200 and response.json()['acknowledged']
    assert client.get('/api/v1/sync/session').json() == {'active': None}
    assert client.get('/api/v1/sync/session', params={'task_id': job['job_id']}).json()['acknowledged']
    assert client.get('/api/v1/business-fixture').status_code == 200


def test_frozen_plan_waits_between_main_sector_and_supplement_even_before_authorization_marker(isolated):
    job = root(); tasks.finish(job['job_id'])
    assert progress.session(task_id=job['job_id'])['status'] == 'awaiting_followup'
    assert not progress.session(task_id=job['job_id'])['can_acknowledge']
    sector = tasks.create_task('source_check', {'operation': 'sector_charts', 'mode': 'increment',
        'increment_chain': True, 'parent_sync_job_id': job['job_id']})
    event(job, 'followup_queued', next_task_id=sector['job_id'])
    tasks.finish(sector['job_id'])
    view = progress.session(task_id=job['job_id'])
    assert view['waiting_followup'] and not view['can_acknowledge']
    assert len(view['tasks']) == 2


def test_same_accepted_request_is_replayed_without_source_or_clock_but_new_requests_are_blocked(isolated):
    job = root(); client = client_with_gate()
    response = client.post('/api/v1/sync/tasks', json={'mode': 'increment', 'request_key': job['request_key']})
    assert response.status_code == 200 and response.json()['job_id'] == job['job_id']
    assert client.post('/api/v1/sync/tasks', json={'mode': 'increment', 'request_key': 'another'}).status_code == 409
    assert client.post('/api/v1/sync/tasks', json={'mode': 'initialise', 'request_key': 'another'}).status_code == 409
    assert tasks.list_tasks()['total'] == 1


def test_job_queue_rechecks_lock_inside_its_write_transaction_after_http_gate_race(isolated):
    job = root()
    token = business_http_request.set(True)
    try:
        with pytest.raises(HTTPException) as error:
            tasks.create_task('selection', {'fixture': 'late HTTP request that passed an earlier gate'})
        assert error.value.status_code == 409
    finally:
        business_http_request.reset(token)
    assert tasks.list_tasks()['total'] == 1


def test_concurrent_new_roots_cannot_overwrite_or_leave_an_unlocked_second_job(isolated):
    def create(key):
        token = business_http_request.set(True)
        try:
            try: return root(key)['job_id']
            except HTTPException as error: return error.status_code
        finally: business_http_request.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(create, ('first', 'second')))
    assert sum(isinstance(item, str) for item in result) == 1
    assert 409 in result and tasks.list_tasks()['total'] == 1


def test_old_root_or_epoch_ack_cannot_clear_a_new_session(isolated):
    first = root(); stop_root(first)
    initial = progress.session(task_id=first['job_id'])
    with pytest.raises(HTTPException): progress.acknowledge(request_key=first['request_key'], epoch='stale')
    progress.acknowledge(request_key=first['request_key'], epoch=initial['session_epoch'])
    second = root('second')
    with pytest.raises(HTTPException): progress.acknowledge(request_key=first['request_key'], epoch=initial['session_epoch'])
    assert progress.session()['active']['root_task_id'] == second['job_id']


def test_absent_request_is_closed_atomically_and_a_late_post_cannot_start_collection(isolated):
    assert progress.acknowledge(request_key='never-arrived')['status'] == 'not_received'
    with pytest.raises(HTTPException): root('never-arrived')
    assert tasks.list_tasks()['total'] == 0
    with pytest.raises(HTTPException): progress.acknowledge(request_key='known-but-lost', task_id='previously-known-id')
    job = root()
    with connect() as connection:
        # A damaged reference remains unavailable, rather than being guessed
        # complete from an HTTP 404 or an elapsed timer.
        saved = progress.lock_record(connection)
        saved['root_task_id'] = 'missing-task'
        connection.execute('UPDATE platform_setting SET value_json=? WHERE key=?', (json.dumps(saved), progress.LOCK_SETTING))
    with pytest.raises(HTTPException): progress.acknowledge(request_key=job['request_key'], task_id=job['job_id'])


@pytest.mark.parametrize('action,status', [('pause', 'paused'), ('cancel', 'cancelled')])
def test_queued_stop_needs_no_worker_but_running_stop_waits_for_actual_release(isolated, action, status):
    job = root()
    progress.control(task_id=job['job_id'], action=action)
    view = progress.session(task_id=job['job_id'])
    assert view['status'] == status and view['can_acknowledge']
    progress.acknowledge(request_key=job['request_key'])
    if status == 'paused': tasks.change_task(job['job_id'], 'cancel')
    job = root('running'); tasks.claim_next('fixture-worker')
    progress.control(task_id=job['job_id'], action=action)
    view = progress.session(task_id=job['job_id'])
    assert view['status'] == 'stopping' and not view['can_acknowledge']
    with pytest.raises(HTTPException): progress.acknowledge(request_key=job['request_key'])
    progress.worker_released(job['job_id'])
    view = progress.session(task_id=job['job_id'])
    assert view['status'] == status and view['can_acknowledge']


def test_stop_between_root_completion_and_followup_waits_for_dispatch_and_blocks_late_children(isolated):
    job = root(); tasks.claim_next('fixture-worker'); event(job, 'chain_authorized'); tasks.finish(job['job_id'])
    progress.control(task_id=job['job_id'], action='pause')
    assert not progress.session(task_id=job['job_id'])['can_acknowledge']
    late = tasks.create_task('source_check', {'operation': 'sector_charts', 'increment_chain': True,
        'mode': 'increment', 'parent_sync_job_id': job['job_id']})
    assert late['status'] == 'paused'
    event(job, 'followup_queued', next_task_id=late['job_id'])
    progress.worker_released(job['job_id'])
    assert progress.session(task_id=job['job_id'])['can_acknowledge']


def test_worker_restart_release_proof_and_explicit_resume_relock_the_same_root(isolated):
    job = root(); tasks.claim_next('old-worker')
    progress.control(task_id=job['job_id'], action='pause')
    # Real worker invokes this only after acquiring its already existing EX lock.
    progress.worker_released(recovered=True)
    progress.acknowledge(request_key=job['request_key'])
    client = client_with_gate()
    response = client.post('/api/v1/tasks/' + job['job_id'] + '/resume')
    assert response.status_code == 200
    active = client.get('/api/v1/sync/session').json()['active']
    assert active['root_task_id'] == job['job_id'] and active['locked'] and not active['can_acknowledge']


def test_worker_honors_durable_stop_if_api_died_before_changing_individual_job(isolated, monkeypatch):
    from app import worker
    from app.services import sync_service
    job = root(); claimed = tasks.claim_next('fixture-worker')
    with connect() as connection:
        saved = progress.lock_record(connection); saved['stop_action'] = 'pause'
        connection.execute('UPDATE platform_setting SET value_json=? WHERE key=?', (json.dumps(saved), progress.LOCK_SETTING))
        tasks.append_event(connection, job['job_id'], 'increment_chain_stop_requested', 'fixture committed stop before per-job transition', details={'action': 'pause', 'scope_task_id': job['job_id']})
    monkeypatch.setattr(sync_service, 'execute_sync', lambda *a, **k: pytest.fail('explicit stop must not start collection'))
    worker.dispatch(claimed)
    assert tasks.task_view(job['job_id'])['status'] == 'paused'
    progress.worker_released(job['job_id'])
    assert progress.session(task_id=job['job_id'])['can_acknowledge']


def test_acknowledged_stop_does_not_allow_followup_recovery_to_restart_the_chain(isolated):
    from app import worker
    job = root(); tasks.claim_next('fixture-worker'); event(job, 'chain_authorized'); tasks.finish(job['job_id'])
    progress.control(task_id=job['job_id'], action='pause')
    progress.worker_released(job['job_id'])
    progress.acknowledge(request_key=job['request_key'])
    with connect() as connection:
        saved = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (job['job_id'],)).fetchone())
    assert worker._queue_followup_once(saved) is None
    assert tasks.list_tasks()['total'] == 1


def test_local_check_direct_sql_recovery_honors_persistent_stop_after_ack(isolated, monkeypatch):
    from app.services import line_alerts, listing_status
    job = tasks.create_task('increment', {'increment_session_version': progress.LOCK_VERSION,
        'data_chain_version': 'sector-charts-increment-v1', 'line_alert_check_version': line_alerts.VERSION,
        'listing_status_check_version': listing_status.VERSION})
    tasks.finish(job['job_id']); progress.control(task_id=job['job_id'], action='cancel')
    progress.acknowledge(request_key=job['request_key'])
    monkeypatch.setattr(line_alerts, 'enqueue', lambda *a, **k: pytest.fail('no automatic local check after explicit stop'))
    monkeypatch.setattr(listing_status, 'enqueue', lambda *a, **k: pytest.fail('no automatic local check after explicit stop'))
    line_alerts.recover_sync_followups(); listing_status.recover_sync_followups()
    assert tasks.list_tasks()['total'] == 1


def test_direct_sql_job_guard_is_http_only_and_leaves_worker_controls_unchanged(isolated):
    from app.services.increment_access import require_business_unlocked
    root()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        require_business_unlocked(connection)  # Sole worker has no HTTP scope.
        token = business_http_request.set(True)
        try:
            with pytest.raises(HTTPException) as error: require_business_unlocked(connection)
            assert error.value.status_code == 409
        finally: business_http_request.reset(token)


@pytest.mark.parametrize('operation', ['stock', 'sector', 'batch', 'batch-resume'])
def test_actual_selection_sql_creators_recheck_after_a_middleware_race(isolated, monkeypatch, operation):
    from app.services import strategies, sector_strategies, strategy_batches
    class EmptyMarket:
        def status(self): return {}
    monkeypatch.setattr(sector_strategies, 'get_market', lambda: EmptyMarket())
    root()
    token = business_http_request.set(True)
    try:
        with pytest.raises(HTTPException) as error:
            if operation == 'stock': strategies.run_strategy('missing')
            elif operation == 'sector': sector_strategies.run_strategy('missing')
            elif operation == 'batch': strategy_batches.create_batch({'strategy_ids': ['missing']})
            else: strategy_batches.change_batch('missing', 'resume')
        assert error.value.status_code == 409
    finally: business_http_request.reset(token)
    assert tasks.list_tasks()['total'] == 1


def test_missing_linked_child_is_unknown_and_cannot_be_acknowledged_as_complete(isolated):
    job = root(); tasks.finish(job['job_id'])
    event(job, 'followup_queued', next_task_id='missing-linked-child')
    view = progress.session(task_id=job['job_id'])
    assert view['status'] == 'unknown' and not view['can_acknowledge']
    with pytest.raises(HTTPException): progress.acknowledge(request_key=job['request_key'])


def test_parentless_legacy_local_check_resume_recovers_root_from_its_durable_link(isolated):
    job = root(); complete_chain(job)
    local = tasks.create_task('source_check', {'operation': 'listing_status_check'})
    event(job, 'listing_status_check_queued', next_task_id=local['job_id'])
    progress.control(task_id=job['job_id'], action='pause')
    progress.acknowledge(request_key=job['request_key'])
    client = client_with_gate()
    assert client.post('/api/v1/tasks/' + local['job_id'] + '/resume').status_code == 200
    assert progress.session()['active']['root_task_id'] == job['job_id']


def test_successful_child_retry_is_a_new_attempt_not_the_historical_failed_result(isolated):
    job = root(); complete_chain(job)
    failed = tasks.create_task('source_check', {'operation': 'line_alert_check', 'parent_sync_job_id': job['job_id']})
    event(job, 'line_alert_check_queued', next_task_id=failed['job_id']); tasks.finish(failed['job_id'], 'failed')
    progress.acknowledge(request_key=job['request_key'])
    client = client_with_gate()
    retried = client.post('/api/v1/tasks/' + failed['job_id'] + '/retry').json()
    assert retried['job_id'] != failed['job_id'] and retried['status'] == 'queued'
    view = progress.session()['active']
    assert view['attempt_task_id'] == retried['job_id'] and not view['can_acknowledge']
    tasks.finish(retried['job_id'])
    view = progress.session()['active']
    assert view['status'] == 'succeeded' and view['can_acknowledge']
    assert next(item for item in view['tasks'] if item['job_id'] == failed['job_id'])['historical_attempt']
    assert not next(item for item in view['tasks'] if item['job_id'] == retried['job_id'])['historical_attempt']
    progress.acknowledge(request_key=job['request_key'])
    saved = progress.session(task_id=job['job_id'])
    assert saved['attempt_task_id'] == retried['job_id'] and saved['status'] == 'succeeded' and saved['acknowledged']


def test_local_step_resume_does_not_revoke_original_root_stop_or_queue_missing_sector(isolated):
    from app import worker
    job = root(); tasks.finish(job['job_id'])
    local = tasks.create_task('source_check', {'operation': 'listing_status_check'})
    event(job, 'listing_status_check_queued', next_task_id=local['job_id'])
    progress.control(task_id=job['job_id'], action='pause'); progress.acknowledge(request_key=job['request_key'])
    client = client_with_gate()
    assert client.post('/api/v1/tasks/' + local['job_id'] + '/resume').status_code == 200
    with connect() as connection:
        original = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (job['job_id'],)).fetchone())
        attempt = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (local['job_id'],)).fetchone())
        assert progress.chain_stop_action(connection, original) == 'pause'
        assert progress.chain_stop_action(connection, attempt) is None
    assert worker._queue_followup_once(original) is None
    tasks.finish(local['job_id'])
    view = progress.session()['active']
    assert view['attempt_task_id'] == local['job_id'] and view['status'] == 'succeeded'
    assert tasks.list_tasks()['total'] == 2, 'local repair never silently starts the missing original sector stage'


def test_sector_step_resume_allows_only_its_own_authorized_followup_branch(isolated):
    job = root(); tasks.finish(job['job_id'])
    sector = tasks.create_task('source_check', {'operation': 'sector_charts', 'mode': 'increment',
        'increment_chain': True, 'parent_sync_job_id': job['job_id']})
    event(job, 'followup_queued', next_task_id=sector['job_id'])
    progress.control(task_id=job['job_id'], action='pause'); progress.acknowledge(request_key=job['request_key'])
    client = client_with_gate()
    assert client.post('/api/v1/tasks/' + sector['job_id'] + '/resume').status_code == 200
    tasks.finish(sector['job_id'])
    with connect() as connection:
        original = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (job['job_id'],)).fetchone())
        attempt = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (sector['job_id'],)).fetchone())
        assert progress.chain_stop_action(connection, original) == 'pause'
        assert progress.chain_stop_action(connection, attempt) is None
    leaf = tasks.create_task('source_check', {'operation': 'market_supplement', 'parent_sync_job_id': job['job_id']},
        chain_parent_id=sector['job_id'])
    assert leaf['status'] == 'queued'
    event(sector, 'followup_queued', next_task_id=leaf['job_id'])
    with connect() as connection:
        saved = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (leaf['job_id'],)).fetchone())
        assert progress.chain_stop_action(connection, saved) is None
    tasks.finish(leaf['job_id'])
    view = progress.session()['active']
    assert view['attempt_task_id'] == sector['job_id'] and view['status'] == 'succeeded' and view['can_acknowledge']


def test_restart_release_is_reached_only_while_the_existing_exclusive_worker_lock_is_held(isolated, monkeypatch):
    import fcntl
    from app import worker
    from app.core import work_dir
    job = root(); tasks.claim_next('old-worker'); progress.control(task_id=job['job_id'], action='pause')
    lock_path = work_dir() / 'worker.lock'
    real_release = progress.worker_released
    calls = []
    def release(*args, **kwargs):
        with lock_path.open('a+b') as competing:
            with pytest.raises(BlockingIOError): fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls.append(kwargs.get('recovered'))
        return real_release(*args, **kwargs)
    monkeypatch.setattr(progress, 'worker_released', release)
    monkeypatch.setattr(worker.signal, 'signal', lambda *args: None)
    with lock_path.open('a+b') as existing:
        fcntl.flock(existing, fcntl.LOCK_EX)
        with pytest.raises(SystemExit): worker.run(once=True)
    assert not calls
    worker.run(once=True)
    assert calls == [True]
    assert progress.session(task_id=job['job_id'])['can_acknowledge']
