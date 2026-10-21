"""One-time board acquisition and frozen daily followups, using isolated data.

Only the local adapter fixture produces responses. These tests neither access
credentials nor create tasks in the user's workspace.
"""
from copy import deepcopy

import pytest

from app import core, worker
from app.services import sector_chart_batch as batch, task_service as tasks
from test_platform import isolated
from test_sector_chart_chain import local, http, finish_claimed
from test_sector_charts import DAY

INCREMENT_CHAIN = 'sector-charts-increment-v1'


@pytest.fixture(autouse=True)
def no_local_throttle(monkeypatch):
    monkeypatch.setattr(batch, 'REQUEST_INTERVAL', 0)


def inventory():
    with core.connect() as connection:
        return tuple(connection.execute('SELECT count(*) FROM '+table).fetchone()[0]
                     for table in ('job', 'job_event'))


def acquired(local, *, partial=True, key='initial-board-acquisition'):
    if partial:
        def leading_gap(payload):
            payload['data']['item'] = payload['data']['item'][1:]
            return payload
        local.adapter.transform = leading_gap
    task = local.jobs.create_batch_job(local.market, DAY, request_key=key)
    job = tasks.claim_next('isolated-board-acquisition')
    assert job['job_id'] == task['job_id']
    worker.dispatch(job)
    saved = tasks.task_view(task['job_id'])
    report = batch.read_frozen_sector_chart_batch_report(saved['parameters'])
    assert report['coverage']['collection_complete'] is True
    assert report['coverage']['available_count'] == 2
    return saved, report


def completed_increment(local, status='partial', *, new=True):
    parameters = {'source_id': 'fuyao'}
    if new:
        parameters['data_chain_version'] = INCREMENT_CHAIN
    parent = tasks.create_task('increment', parameters, target_date=DAY)
    return finish_claimed(parent, status)


def supplements():
    return [job for job in tasks.list_tasks()['items']
            if job['parameters'].get('operation') == 'market_supplement']


def test_all_boards_acquired_locks_initialization_even_with_historical_gaps(local):
    task, report = acquired(local)
    assert task['status'] == 'partial'
    assert report['complete'] is False
    assert report['coverage']['partial_count'] == 2
    state = local.jobs.initialization_state(local.market)
    assert state['initialized'] is True and state['can_initialize'] is False
    assert state['job_id'] == task['job_id']
    assert state['initialized_date'] == DAY
    assert state['available_count'] == state['catalog_count'] == 2
    assert state['reason']


@pytest.mark.parametrize('key', ['initial-board-acquisition', 'different-initial-request'])
def test_completed_initial_http_including_same_key_rejects_without_side_effects(http, local, key):
    acquired(local)
    before = inventory(), len(local.client_calls), len(local.adapter.calls)
    response = http.post('/api/v1/sync/sector-charts', json={'request_key': key})
    assert response.status_code == 409
    assert '增量' in response.json()['detail']
    assert (inventory(), len(local.client_calls), len(local.adapter.calls)) == before


def test_initialization_proof_survives_snapshot_and_catalog_change(local):
    task, _ = acquired(local)
    later = deepcopy(local.market)
    later.state['snapshot_id'] = 'market-next-generation'
    later.state['data_end'] = '2026-10-21'
    later.days.append('2026-10-21')
    later.ref['concept_catalog'].append({'thscode': '886055.TI', 'name': '新目录样本'})
    local.current = later
    before = inventory(), len(local.adapter.calls)
    state = local.jobs.initialization_state(later)
    assert state['initialized'] is True and state['job_id'] == task['job_id']
    assert state['available_count'] == state['catalog_count'] == 2
    with pytest.raises(Exception) as caught:
        local.jobs.create_batch_job(later, '2026-10-21')
    assert getattr(caught.value, 'status_code', None) == 409
    assert (inventory(), len(local.adapter.calls)) == before


@pytest.mark.parametrize('action', ['retry', 'resume'])
def test_old_initial_retry_resume_cannot_bypass_completed_acquisition(http, local, action):
    old = local.jobs.create_batch_job(local.market, '2026-10-19', request_key='old-initial')
    if action == 'resume':
        tasks.change_task(old['job_id'], 'pause')
    else:
        finish_claimed(old, 'failed')
    acquired(local)
    before = inventory(), deepcopy(tasks.task_view(old['job_id'])['logs']), len(local.client_calls)
    response = http.post('/api/v1/tasks/'+old['job_id']+'/'+action)
    assert response.status_code == 409
    assert inventory() == before[0]
    assert tasks.task_view(old['job_id'])['logs'] == before[1]
    assert len(local.client_calls) == before[2]


def test_stale_running_initial_rejected_before_provider_construction(local):
    stale = local.jobs.create_batch_job(local.market, '2026-10-19', request_key='stale-initial')
    tasks.change_task(stale['job_id'], 'pause')
    acquired(local)
    with core.connect() as connection:
        connection.execute("UPDATE job SET status='running' WHERE job_id=?", (stale['job_id'],))
    before = inventory(), len(local.client_calls), len(local.adapter.calls)
    with pytest.raises(ValueError, match='已初始化'):
        local.jobs.execute_batch_job(stale['job_id'])
    assert (inventory(), len(local.client_calls), len(local.adapter.calls)) == before


def test_incomplete_catalog_acquisition_can_retry_missing_boards(local, monkeypatch):
    original = local.adapter.request
    def missing_industry(path, params):
        if params['thscode'] == '881101.TI':
            raise ValueError('isolated missing board')
        return original(path, params)
    monkeypatch.setattr(local.adapter, 'request', missing_industry)
    task = local.jobs.create_batch_job(local.market, DAY)
    job = tasks.claim_next('isolated-incomplete'); worker.dispatch(job)
    report = batch.read_frozen_sector_chart_batch_report(task['parameters'])
    assert report['coverage']['collection_complete'] is False
    assert report['coverage']['available_count'] == 1
    state = local.jobs.initialization_state(local.market)
    assert state['initialized'] is False and state['can_initialize'] is True
    retried = tasks.change_task(task['job_id'], 'retry')
    assert retried['retry_of_job_id'] == task['job_id'] and retried['status'] == 'queued'


@pytest.mark.parametrize('damage', ['manifest', 'raw', 'failed-status'])
def test_invalid_receipt_or_failed_job_does_not_claim_initialized(local, damage):
    task, report = acquired(local)
    if damage == 'failed-status':
        with core.connect() as connection:
            connection.execute("UPDATE job SET status='failed' WHERE job_id=?", (task['job_id'],))
    elif damage == 'manifest':
        (batch._root()/report['artifact']).write_text('{}')
    else:
        (batch._root()/report['raw_artifact']).write_text('{}')
    assert local.jobs.initialization_state(local.market)['initialized'] is False


def test_excluding_current_task_does_not_self_lock_after_accepted_receipt(local):
    task, _ = acquired(local)
    # Execution guards exclude their own just-accepted receipt; another initial
    # job still observes this successful receipt and must remain blocked.
    assert local.jobs.initialization_state(local.market, exclude_job_id=task['job_id'])['initialized'] is False
    assert local.jobs.initialization_state(local.market)['initialized'] is True


def test_same_day_increment_uses_separate_manifest_without_erasing_initial_receipt(local):
    initial, _ = acquired(local)
    initial_pointer = batch._root()/'accepted/fuyao/sector-chart-batch'/(DAY+'.json')
    before = initial_pointer.read_bytes(), len(local.adapter.calls)
    increment = local.jobs.create_batch_job(local.market, DAY, mode='increment')
    assert increment['parameters']['mode'] == 'increment'
    assert increment['parameters']['request_signature'] != initial['parameters']['request_signature']
    job = tasks.claim_next('isolated-same-day-increment'); worker.dispatch(job)
    assert initial_pointer.read_bytes() == before[0]
    assert len(local.adapter.calls) == before[1]
    pointer = batch._root()/'accepted/fuyao/sector-chart-increment-batch'/(DAY+'.json')
    assert pointer.is_file()
    report = batch.read_frozen_sector_chart_batch_report(increment['parameters'], mode='increment')
    assert report['mode'] == 'increment'
    assert report['coverage']['cache_hit_count'] == 2 and report['coverage']['request_count'] == 0
    assert local.jobs.initialization_state(local.market)['job_id'] == initial['job_id']


def test_new_increment_http_freezes_chain_version_without_provider_request(http, local, monkeypatch):
    from app.services import sync_clock
    monkeypatch.setattr(sync_clock, 'require_new_increment', lambda: None)
    response = http.post('/api/v1/sync/tasks', json={'mode': 'increment', 'request_key': 'daily-chain'})
    assert response.status_code == 200
    assert response.json()['parameters']['data_chain_version'] == INCREMENT_CHAIN
    assert local.adapter.calls == []


@pytest.mark.parametrize('status', ['partial', 'succeeded', 'no_change'])
def test_new_increment_then_tail_batch_then_light_supplement_once(local, monkeypatch, status):
    parent = completed_increment(local, status)
    # Recovery must retain the completed parent's date even when the visible
    # market has already advanced, rather than taking a newer global date.
    local.market.state['data_end'] = '2026-10-21'
    local.market.days.append('2026-10-21')
    children = worker.recover_followups()
    assert len(children) == 1 and supplements() == []
    child = children[0]
    assert child['parameters']['operation'] == 'sector_charts'
    assert child['parameters']['mode'] == 'increment'
    assert child['parameters']['increment_chain'] is True
    assert child['parameters']['data_chain_version'] == INCREMENT_CHAIN
    assert child['parameters']['parent_sync_job_id'] == parent['job_id']
    assert child['target_date'] == child['parameters']['as_of'] == DAY
    assert child['request_key'] == 'sector-charts-after-sync:'+parent['job_id']
    assert worker.recover_followups() == []
    monkeypatch.setattr(local.jobs, 'execute_batch_job', lambda *a, **k: {'quality': 'partial'})
    running = tasks.claim_next('isolated-increment-chain'); worker.dispatch(running)
    assert running['job_id'] == child['job_id']
    following = supplements(); assert len(following) == 1
    item = following[0]
    assert item['target_date'] == DAY
    assert item['parameters']['year_history'] is False
    assert item['parameters']['sector_history'] is True
    assert item['parameters']['recent_days'] == 1
    assert item['parameters']['parent_sync_job_id'] == parent['job_id']
    assert item['request_key'] == 'supplement-after-sync:'+parent['job_id']
    worker.dispatch(running)
    assert worker.recover_followups() == [] and len(supplements()) == 1
    assert local.adapter.calls == []


def test_legacy_increment_stays_light_without_new_batch_scope(local):
    parent = completed_increment(local, new=False)
    children = worker.recover_followups()
    assert len(children) == 1 and children[0]['parameters']['operation'] == 'market_supplement'
    assert children[0]['request_key'] == 'supplement-after-sync:'+parent['job_id']
    assert not any(job['parameters'].get('operation') == 'sector_charts'
                   for job in tasks.list_tasks()['items'])


@pytest.mark.parametrize('state', ['paused', 'cancelled', 'failed'])
def test_stopped_increment_parent_does_not_create_tail_batch(local, state):
    parent = completed_increment(local)
    with core.connect() as connection:
        connection.execute('UPDATE job SET status=? WHERE job_id=?', (state, parent['job_id']))
    assert worker.recover_followups() == []
    assert tasks.list_tasks()['total'] == 1 and local.adapter.calls == []


@pytest.mark.parametrize('state', ['paused', 'cancelled', 'failed'])
def test_stopped_tail_batch_not_resumed_or_followed(local, state):
    completed_increment(local)
    child = worker.recover_followups()[0]
    if state == 'failed':
        finish_claimed(child, 'failed')
    else:
        tasks.change_task(child['job_id'], 'pause' if state == 'paused' else 'cancel')
    before = inventory(), deepcopy(tasks.task_view(child['job_id'])['logs'])
    assert worker.recover_followups() == [] and supplements() == []
    assert inventory() == before[0]
    assert tasks.task_view(child['job_id'])['logs'] == before[1]


@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_recovery_reuses_existing_stopped_tail_child_without_resuming(local, action):
    parent = completed_increment(local)
    child = local.jobs.create_batch_job(local.market, DAY, mode='increment',
        request_key='sector-charts-after-sync:'+parent['job_id'],
        chain_parameters={'increment_chain': True, 'parent_sync_job_id': parent['job_id'],
                          'data_chain_version': INCREMENT_CHAIN})
    tasks.change_task(child['job_id'], action)
    children = worker.recover_followups()
    assert len(children) == 1 and children[0]['job_id'] == child['job_id']
    assert children[0]['status'] == ('paused' if action == 'pause' else 'cancelled')
    assert worker.recover_followups() == [] and supplements() == []


def test_increment_retry_is_not_blocked_by_completed_initialization(local):
    acquired(local)
    task = local.jobs.create_batch_job(local.market, DAY, mode='increment')
    finish_claimed(task, 'failed')
    retried = tasks.change_task(task['job_id'], 'retry')
    assert retried['parameters']['mode'] == 'increment' and retried['status'] == 'queued'
    assert local.jobs.initialization_state(local.market)['initialized'] is True
