"""Small task receipts expose measured work without leaking task parameters."""
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import core
from app.routers.platform import router
from app.services import task_service as tasks
from test_platform import isolated
from test_workspace import ws, make_strategy
from test_sector_strategies import workspace, create


def test_activity_is_small_and_queued_zero_is_explicit(isolated):
    task = tasks.create_task('research', {'api_key': 'private-fixture', 'large_payload': ['secret'] * 100})
    app = FastAPI(); app.include_router(router)
    response = TestClient(app).get('/api/v1/tasks/activity')
    assert response.status_code == 200
    body = response.json()
    assert body['active_count'] == 1
    item = body['items'][0]
    assert item['job_id'] == task['job_id'] and item['status'] == 'queued'
    assert item['progress'] == 0
    assert 'logs' not in item and 'parameters' not in item
    assert 'private-fixture' not in response.text and len(response.content) < 2000


def test_running_requires_measured_progress_and_retains_reported_zero(isolated):
    task = tasks.create_task('research', {})
    tasks.claim_next('isolated-worker')
    assert tasks.task_activity()['items'][0]['progress'] is None
    tasks.progress(task['job_id'], '核验材料', 0, '当前准备中')
    view = tasks.task_activity()['items'][0]
    assert view['progress'] == 0 and view['stage'] == '核验材料'
    tasks.progress(task['job_id'], '核验材料', 35, '已核验3项', {'actual_completed_batches': 3, 'actual_total_batches': 10})
    view = tasks.task_activity()['items'][0]
    assert view['progress'] == 35
    assert view['actual_completed_batches'] == 3 and view['actual_total_batches'] == 10


@pytest.mark.parametrize('status,expected', [('succeeded', 100), ('partial', 100), ('no_change', 100), ('failed', 35), ('cancelled', 35)])
def test_terminals_are_clear_and_failure_is_not_completed(isolated, status, expected):
    task = tasks.create_task('research', {})
    tasks.claim_next('isolated-worker')
    tasks.progress(task['job_id'], '核验材料', 35)
    tasks.finish(task['job_id'], status, '任务结束', error='实际来源超时' if status == 'failed' else None)
    result = tasks.task_activity()
    assert result['active_count'] == 0
    view = result['items'][0]
    assert view['status'] == status and view['progress'] == expected
    assert view['finished_at_ms'] is not None
    if status == 'failed': assert view['message'] == '实际来源超时'


def test_active_task_not_hidden_by_newer_terminal_history(isolated):
    task = tasks.create_task('research', {})
    tasks.claim_next('isolated-worker')
    for index in range(15):
        other = tasks.create_task('research', {'index': index})
        tasks.finish(other['job_id'])
    result = tasks.task_activity()
    assert result['items'][0]['job_id'] == task['job_id']
    assert result['active_count'] == 1 and len(result['items']) == 6


def test_stock_conflict_names_actual_running_strategy_and_progress(ws):
    _, client = ws
    strategy = make_strategy(client)
    active = tasks.create_task('selection', {'strategy_id': strategy['strategy_id']})
    tasks.claim_next('isolated-worker')
    tasks.progress(active['job_id'], '核验条件', 23)
    response = client.post(f"/api/v1/strategies/{strategy['strategy_id']}/runs", json={})
    assert response.status_code == 409
    detail = response.json()['detail']
    assert strategy['name'] in detail['message']
    assert detail['active_task']['job_id'] == active['job_id']
    assert detail['active_task']['progress'] == 23
    assert detail['active_task']['target_type'] == 'stock'


def test_stock_execution_reports_real_counts(ws, monkeypatch):
    _, client = ws
    strategy = make_strategy(client)
    recorded = []
    original = tasks.progress
    def record(*args, **kwargs):
        recorded.append((args, kwargs)); return original(*args, **kwargs)
    monkeypatch.setattr(tasks, 'progress', record)
    response = client.post(f"/api/v1/strategies/{strategy['strategy_id']}/runs", json={})
    assert response.status_code == 200, response.text
    assert recorded[0][0][2] == 0
    assert any(args[1] == '核验个股条件' and args[4]['actual_completed_batches'] > 0 for args, _ in recorded if len(args) > 4)
    view = tasks.task_activity()['items'][0]
    assert view['status'] == 'succeeded' and view['progress'] == 100
    assert view['strategy_name'] == strategy['name']


def test_sector_conflict_retains_separate_target(workspace):
    from app.services import sector_strategies
    strategy = create()
    active = tasks.create_task('selection', {'target_type': 'sector', 'strategy_id': strategy['strategy_id']})
    tasks.claim_next('isolated-worker')
    with pytest.raises(HTTPException) as caught:
        sector_strategies.run_strategy(strategy['strategy_id'])
    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert strategy['name'] in detail['message']
    assert detail['active_task']['target_type'] == 'sector'
    assert detail['active_task']['title'] == '板块策略筛选'


def test_stock_saved_results_survive_reload_version_edit_and_failed_refresh(ws, monkeypatch):
    from app.services import strategies
    _, client = ws
    strategy = make_strategy(client)
    run = client.post(f"/api/v1/strategies/{strategy['strategy_id']}/runs", json={}).json()
    count = tasks.list_tasks()['total']
    for _ in range(2):
        listed = client.get('/api/v1/strategies').json()['items'][0]
        assert listed['latest_run']['run_id'] == run['run_id']
        saved = client.get('/api/v1/strategy-runs/' + listed['latest_run']['run_id']).json()
        assert saved['items'] == run['items'] and saved['strategy_version'] == run['strategy_version']
    assert tasks.list_tasks()['total'] == count, 'Restoring results must never start selection'
    changed = client.patch(f"/api/v1/strategies/{strategy['strategy_id']}", json={'definition': {'field': 'close', 'op': '>', 'value': 1}}).json()
    assert changed['version'] > run['strategy_version']
    assert changed['latest_run']['run_id'] == run['run_id']
    def unavailable(*args, **kwargs): raise RuntimeError('isolated refresh failure')
    monkeypatch.setattr(strategies, 'market_panel', unavailable)
    with pytest.raises(RuntimeError, match='isolated refresh failure'):
        client.post(f"/api/v1/strategies/{strategy['strategy_id']}/runs", json={})
    assert client.get(f"/api/v1/strategies/{strategy['strategy_id']}").json()['latest_run']['run_id'] == run['run_id']
    assert client.get('/api/v1/strategy-runs/' + run['run_id']).json()['items'] == run['items']


def test_sector_saved_result_remains_frozen_after_edits_and_failed_refresh(workspace, monkeypatch):
    from app.services import sector_strategies
    strategy = create()
    run = sector_strategies.run_strategy(strategy['strategy_id'])
    count = tasks.list_tasks()['total']
    listed = sector_strategies.list_strategies()['items'][0]
    saved = sector_strategies.get_run(listed['latest_run']['run_id'])
    assert saved['items'] == run['items'] and tasks.list_tasks()['total'] == count
    changed = sector_strategies.update_strategy(strategy['strategy_id'], {'definition': {'field': 'close', 'op': '>', 'value': 2}})
    assert changed['version'] > saved['strategy_version']
    def unavailable(*args, **kwargs): raise RuntimeError('isolated board refresh failure')
    monkeypatch.setattr(sector_strategies, 'metrics_for_sector', unavailable)
    with pytest.raises(RuntimeError, match='isolated board refresh failure'):
        sector_strategies.run_strategy(strategy['strategy_id'])
    assert sector_strategies.get_strategy(strategy['strategy_id'])['latest_run']['run_id'] == saved['run_id']
    assert sector_strategies.get_run(saved['run_id'])['items'] == saved['items']
