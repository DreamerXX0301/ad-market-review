"""One manual increment locks the UI until its authorized chain has stopped."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import connect, now_ms
from app.routers.platform import router
from app.services import sync_progress, task_service as tasks
from test_platform import isolated


def event(job, stage, **details):
    with connect() as connection:
        tasks.append_event(connection, job['job_id'], stage, stage, details=details)


def test_main_completion_waits_for_entire_chain_and_local_checks(isolated):
    root = tasks.create_task('increment', {'data_chain_version': 'sector-charts-increment-v1'})
    event(root, 'chain_authorized')
    tasks.finish(root['job_id'])
    view = sync_progress.session(task_id=root['job_id'])
    assert not view['can_acknowledge'] and view['status'] == 'awaiting_followup'
    sector = tasks.create_task('source_check', {'operation': 'sector_charts'})
    local = tasks.create_task('source_check', {'operation': 'line_alert_check'})
    event(root, 'followup_queued', next_task_id=sector['job_id'])
    event(root, 'line_alert_check_queued', next_task_id=local['job_id'])
    event(sector, 'chain_authorized')
    tasks.finish(sector['job_id'])
    supplement = tasks.create_task('source_check', {'operation': 'market_supplement'})
    event(sector, 'followup_queued', next_task_id=supplement['job_id'])
    tasks.finish(supplement['job_id'], 'partial')
    assert not sync_progress.session(task_id=root['job_id'])['can_acknowledge']
    tasks.finish(local['job_id'])
    view = sync_progress.session(task_id=root['job_id'])
    assert view['can_acknowledge'] and view['status'] == 'partial'
    assert len(view['tasks']) == 4 and view['finished_at_ms'] is not None


@pytest.mark.parametrize('status', ['failed', 'paused', 'cancelled'])
def test_failure_pause_cancel_can_be_acknowledged_without_restarting(isolated, status):
    root = tasks.create_task('increment', {})
    if status == 'paused':
        tasks.change_task(root['job_id'], 'pause')
    else:
        tasks.finish(root['job_id'], status)
    before = tasks.list_tasks()['total']
    view = sync_progress.session(request_key=root['request_key'])
    assert view['can_acknowledge'] and view['status'] == status
    assert tasks.list_tasks()['total'] == before


def test_blocked_followup_and_missing_link_do_not_deadlock(isolated):
    root = tasks.create_task('increment', {'line_alert_check_version':'explicit-local-line-alert-v1','listing_status_check_version':'listing-evidence-v1'})
    event(root, 'chain_authorized')
    tasks.finish(root['job_id'])
    event(root, 'chain_blocked')
    view = sync_progress.session(task_id=root['job_id'])
    assert view['can_acknowledge'] and view['status'] == 'blocked' and view['issues']


def test_blocked_chain_still_waits_for_an_already_queued_sibling(isolated):
    root=tasks.create_task('increment',{'line_alert_check_version':'explicit-local-line-alert-v1'})
    event(root,'chain_authorized');tasks.finish(root['job_id']);event(root,'chain_blocked')
    child=tasks.create_task('source_check',{'operation':'line_alert_check'})
    event(root,'line_alert_check_queued',next_task_id=child['job_id'])
    assert sync_progress.session(task_id=root['job_id'])['can_acknowledge'] is False
    tasks.finish(child['job_id'])
    view=sync_progress.session(task_id=root['job_id'])
    assert view['can_acknowledge'] and view['status']=='blocked'


def test_queue_zero_never_becomes_fabricated_whole_chain_percent(isolated):
    root = tasks.create_task('increment', {'source_id': 'fuyao', 'api_key': 'private-fixture'})
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    response = client.get('/api/v1/sync/session', params={'request_key': root['request_key']})
    assert response.status_code == 200
    view = response.json()
    assert view['tasks'][0]['progress'] == 0 and not view['can_acknowledge']
    assert view['estimate']['remaining_max_ms'] is None
    assert 'private-fixture' not in response.text and 'parameters' not in response.text
    assert client.get('/api/v1/sync/session').status_code == 422
    assert client.get('/api/v1/sync/session?request_key=missing').status_code == 404
    other = tasks.create_task('research', {})
    assert client.get('/api/v1/sync/session', params={'task_id': other['job_id']}).status_code == 422


def test_eta_requires_three_same_chain_complete_samples_and_includes_children(isolated):
    sync_progress._history_cache.clear()
    stamp = now_ms()
    for index in range(3):
        prior = tasks.create_task('increment', {'data_chain_version': 'test-chain'}, request_key=f'history-{index}')
        tasks.finish(prior['job_id'])
        child = tasks.create_task('source_check', {'operation': 'market_supplement', 'n': index})
        tasks.finish(child['job_id'])
        event(prior, 'chain_authorized')
        event(prior, 'followup_queued', next_task_id=child['job_id'])
        with connect() as connection:
            connection.execute('UPDATE job SET created_at_ms=?,finished_at_ms=? WHERE job_id=?',
                               (stamp-600000, stamp-550000, prior['job_id']))
            connection.execute('UPDATE job SET created_at_ms=?,finished_at_ms=? WHERE job_id=?',
                               (stamp-550000, stamp-300000+index*10000, child['job_id']))
    root = tasks.create_task('increment', {'data_chain_version': 'test-chain'}, request_key='new-root')
    estimate = sync_progress.session(task_id=root['job_id'])['estimate']
    assert estimate['sample_count'] == 3
    assert estimate['remaining_min_ms'] > 290000 and estimate['remaining_max_ms'] > 310000
    assert estimate['expected_finish_at_ms'] is not None
    other = tasks.create_task('increment', {'data_chain_version': 'other-chain'}, request_key='other-root')
    assert sync_progress.session(task_id=other['job_id'])['estimate']['remaining_max_ms'] is None


def test_paused_elapsed_uses_recorded_stop_time_instead_of_zero_or_live_clock(isolated,monkeypatch):
    root=tasks.create_task('increment',{})
    stamp=now_ms()
    with connect() as connection:
        connection.execute('UPDATE job SET created_at_ms=? WHERE job_id=?',(stamp-120000,root['job_id']))
    monkeypatch.setattr(tasks,'now_ms',lambda:stamp-30000)
    tasks.change_task(root['job_id'],'pause')
    monkeypatch.setattr(sync_progress,'now_ms',lambda:stamp+300000)
    view=sync_progress.session(task_id=root['job_id'])
    assert view['status']=='paused' and view['can_acknowledge']
    assert view['elapsed_ms']==90000 and view['finished_at_ms']==stamp-30000


def test_estimate_excludes_tasks_created_after_this_session(isolated):
    sync_progress._history_cache.clear()
    root=tasks.create_task('increment',{'data_chain_version':'same'})
    stamp=now_ms()
    for index in range(3):
        later=tasks.create_task('increment',{'data_chain_version':'same','fixture':index})
        tasks.finish(later['job_id'])
        with connect() as connection:
            connection.execute('UPDATE job SET created_at_ms=?,finished_at_ms=? WHERE job_id=?',(stamp+1000,stamp+11000,later['job_id']))
    estimate=sync_progress.session(task_id=root['job_id'])['estimate']
    assert estimate['sample_count']==0 and estimate['remaining_max_ms'] is None
