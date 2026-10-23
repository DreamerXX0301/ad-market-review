"""Recycle-bin visibility never edits frozen research evidence or schedules work."""
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import core
from app.analytics import backtest_store as store
from app.analytics.backtest_engine import VERSION
from app.routers.backtests import router as backtests_router
from app.routers.workspace import router as workspace_router
from app.services import strategies, tracking, strategy_batches, task_service
from app.services import workspace_deletion as deletion, workspace_automation
from test_backtests import CalendarOnly, creation_context
from test_workspace import ws, make_strategy, add_one


@pytest.fixture
def workspace(ws, monkeypatch):
    market, _ = ws
    app = FastAPI(); app.include_router(workspace_router); app.include_router(backtests_router)
    client = TestClient(app)
    calendar = CalendarOnly().calendar()

    def factory(strategy, key, state='completed'):
        with core.connect() as connection:
            inputs = {row['module']: row['snapshot_id'] for row in connection.execute('SELECT * FROM published_head') if row['module'] != 'backtest_result'}
        monkeypatch.setattr(store, 'context', lambda identifier: (CalendarOnly(), strategy, inputs, 'fixture-generation'))
        payload = {'strategy_id': strategy['strategy_id'], 'start_date': calendar[-20], 'end_date': calendar[-1], 'request_key': key}
        created = store.create_backtest(payload)
        if state == 'completed':
            report = {'engine_version': VERSION, 'stats': {'total_return': 1.2, 'trade_count': 0}, 'data_quality': {},
                      **{name: [] for name in ('signals', 'orders', 'fills', 'positions', 'trades', 'equity')}}
            monkeypatch.setattr(store, 'MarketData', lambda *args, **kwargs: object())
            monkeypatch.setattr(store, 'run_backtest', lambda *args, **kwargs: dict(report))
            store.execute_backtest(created['job_id'])
        elif state in ('failed', 'cancelled'):
            with core.connect() as connection:
                connection.execute('UPDATE backtest_run SET status=? WHERE backtest_id=?', (state, created['backtest_id']))
                connection.execute('UPDATE job SET status=? WHERE job_id=?', (state, created['job_id']))
        return created, payload

    return market, client, factory


def rows(table):
    with core.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM '+table+' ORDER BY rowid')]


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled'])
def test_independent_backtest_delete_restore_is_idempotent_and_preserves_evidence(workspace, state):
    _, client, factory = workspace
    strategy = make_strategy(client)
    created, _ = factory(strategy, 'delete-'+state, state)
    identifier = created['backtest_id']; path = '/api/v1/backtests/'+identifier
    original = client.get(path).json()
    evidence = {table: rows(table) for table in ('backtest_run', 'job', 'retention_pin', 'dataset_snapshot', 'snapshot_artifact', 'published_head', 'strategy_version')}
    files = {str(file): core.hash_file(file) for file in core.published_dir().rglob('*') if file.is_file()}
    first = client.delete(path); assert first.status_code == 200, first.text
    assert client.delete(path).json() == first.json()
    assert client.get(path).status_code == 404
    assert client.get('/api/v1/backtests').json()['total'] == 0
    trash = client.get('/api/v1/backtests?deleted=true').json()
    assert trash['total'] == 1 and trash['items'][0]['can_restore']
    assert trash['items'][0]['deleted_at_ms'] == first.json()['deleted_at_ms']
    assert client.post(path+'/restore').status_code == 200
    assert client.post(path+'/restore').json()['deleted'] is False
    assert client.get(path).json() == original
    assert {table: rows(table) for table in evidence} == evidence
    assert {str(file): core.hash_file(file) for file in core.published_dir().rglob('*') if file.is_file()} == files


@pytest.mark.parametrize('task_state,calculation,lease', [
    ('queued', 'queued', False), ('running', 'running', False), ('paused', 'running', False),
    ('paused', 'failed', False), ('succeeded', 'running', False), ('cancelled', 'running', True),
    ('failed', 'failed', True),
])
def test_active_or_unreleased_backtest_rejects_both_deletion_paths(workspace, task_state, calculation, lease):
    _, client, factory = workspace; strategy = make_strategy(client)
    created, _ = factory(strategy, 'active', 'queued')
    with core.connect() as connection:
        connection.execute('UPDATE backtest_run SET status=? WHERE backtest_id=?', (calculation, created['backtest_id']))
        connection.execute('UPDATE job SET status=?,lease_owner=?,lease_expires_at_ms=? WHERE job_id=?',
                           (task_state, 'fixture-worker' if lease else None, core.now_ms()+100000 if lease else None, created['job_id']))
    item = client.get('/api/v1/backtests').json()['items'][0]
    assert not item['can_delete'] and item['delete_blocked_reason']
    assert client.delete('/api/v1/backtests/'+created['backtest_id']).status_code == 409
    assert client.delete('/api/v1/strategies/'+strategy['strategy_id']).status_code == 409
    assert not rows('backtest_deletion') and not rows('strategy_deletion')


@pytest.mark.parametrize('calculation', ['queued', 'running'])
def test_real_cancelled_queue_can_be_deleted_without_rewriting_calculation(workspace, monkeypatch, calculation):
    _, client, factory = workspace; strategy = make_strategy(client)
    created, _ = factory(strategy, 'cancelled-queue', 'queued')
    with core.connect() as connection:
        connection.execute('UPDATE backtest_run SET status=? WHERE backtest_id=?', (calculation, created['backtest_id']))
    assert task_service.change_task(created['job_id'], 'cancel')['status'] == 'cancelled'
    assert client.get('/api/v1/backtests').json()['items'][0]['can_delete'] is True
    assert client.delete('/api/v1/strategies/'+strategy['strategy_id']).status_code == 200
    monkeypatch.setattr(store, 'run_backtest', lambda *args, **kwargs: pytest.fail('deleted task must not simulate'))
    with pytest.raises(ValueError, match='回收站'): store.execute_backtest(created['job_id'])
    assert rows('backtest_run')[0]['status'] == calculation


def test_strategy_cascade_restores_only_its_own_markers_and_preserves_rounds(workspace):
    _, client, factory = workspace; strategy = make_strategy(client)
    other = make_strategy(client)
    run, tracked = add_one(client, strategy)
    independent, _ = factory(strategy, 'previous-delete')
    cascade, _ = factory(strategy, 'cascade')
    unrelated, _ = factory(other, 'unrelated')
    client.delete('/api/v1/backtests/'+independent['backtest_id'])
    evidence = {table: rows(table) for table in ('backtest_run', 'job', 'retention_pin', 'dataset_snapshot', 'snapshot_artifact', 'published_head', 'tracking_round', 'tracking_event', 'tracking_policy_version', 'strategy_version', 'watchlist_item')}
    path = '/api/v1/strategies/'+strategy['strategy_id']
    before = client.get(path).json(); assert before['related_backtests_count'] == 1
    first = client.delete(path); assert first.status_code == 200, first.text
    assert first.json()['affected_backtests'] == 1
    assert client.delete(path).json()['deleted_at_ms'] == first.json()['deleted_at_ms']
    assert client.get(path).status_code == 404
    assert client.patch(path, json={'archived': False}).status_code == 409
    assert {item['strategy_id'] for item in client.get('/api/v1/strategies').json()['items']} == {other['strategy_id']}
    deleted = client.get('/api/v1/strategies?deleted=true').json()['items'][0]
    assert deleted['tracking_count'] == 1 and deleted['can_restore']
    trash = client.get('/api/v1/backtests?deleted=true').json()['items']
    assert len(trash) == 2 and all(not item['can_restore'] for item in trash)
    assert all('所属策略' in item['restore_blocked_reason'] for item in trash)
    assert client.post('/api/v1/backtests/'+cascade['backtest_id']+'/restore').status_code == 409
    assert client.get('/api/v1/backtests/'+unrelated['backtest_id']).status_code == 200
    assert client.get('/api/v1/tracking-rounds/'+tracked['round_id']).json()['status'] == 'tracking'
    assert client.get('/api/v1/strategy-runs/'+run['run_id']).status_code == 200
    restored = client.post(path+'/restore'); assert restored.status_code == 200
    assert restored.json()['affected_backtests'] == 1
    assert client.post(path+'/restore').json()['affected_backtests'] == 0
    assert client.get(path).json()['version'] == before['version']
    assert client.get('/api/v1/backtests/'+cascade['backtest_id']).status_code == 200
    assert client.get('/api/v1/backtests/'+independent['backtest_id']).status_code == 404
    assert {table: rows(table) for table in evidence} == evidence


def test_legacy_archive_state_survives_delete_restore(workspace):
    _, client, _ = workspace; strategy = make_strategy(client)
    path = '/api/v1/strategies/'+strategy['strategy_id']
    client.patch(path, json={'archived': True})
    assert client.get('/api/v1/strategies').json()['total'] == 1
    client.delete(path); client.post(path+'/restore')
    assert client.get(path).json()['archived'] == 1
    assert len(rows('strategy_version')) == 1


def test_deleted_request_key_is_conflict_even_without_market_context(workspace, monkeypatch):
    _, client, factory = workspace; strategy = make_strategy(client)
    created, payload = factory(strategy, 'same-request', 'cancelled')
    client.delete('/api/v1/backtests/'+created['backtest_id'])
    original_context = store.context
    monkeypatch.setattr(store, 'context', lambda *args: pytest.fail('deleted key must be rejected before market reads'))
    response = client.post('/api/v1/backtests', json=payload)
    assert response.status_code == 409 and '回收站' in response.text
    monkeypatch.setattr(store, 'context', original_context)
    new = client.post('/api/v1/backtests', json={**payload, 'request_key': 'new-request'})
    assert new.status_code == 200 and new.json()['backtest_id'] != created['backtest_id']


def test_deleted_strategy_write_boundaries_reject_old_runs_and_batches(workspace):
    _, client, factory = workspace; strategy = make_strategy(client)
    run, _ = add_one(client, strategy)
    _, payload = factory(strategy, 'old-backtest', 'cancelled')
    path = '/api/v1/strategies/'+strategy['strategy_id']
    client.delete(path)
    before = {table: rows(table) for table in ('job', 'strategy_version', 'tracking_round', 'retention_pin')}
    with core.connect() as connection:
        key = connection.execute('SELECT request_key FROM job WHERE job_id=?', (run['job_id'],)).fetchone()[0]
    assert client.post(path+'/runs', json={'request_key': key}).status_code == 409
    assert client.post('/api/v1/backtests', json={**payload, 'request_key': 'after-delete'}).status_code == 409
    assert client.post('/api/v1/strategy-batches', json={'strategy_ids': [strategy['strategy_id']]}).status_code == 409
    assert client.post('/api/v1/tracking-rounds', json={'run_id': run['run_id'], 'instrument_ids': ['600001.SH']}).status_code == 409
    with pytest.raises(HTTPException) as error:
        task_service.create_task('selection', {'strategy_id': strategy['strategy_id']})
    assert error.value.status_code == 409
    assert {table: rows(table) for table in before} == before


def test_backtest_creation_rechecks_deletion_after_context_read(workspace, monkeypatch):
    _, client, factory = workspace; strategy = make_strategy(client)
    _, payload = factory(strategy, 'original', 'cancelled')
    original = store.context
    def raced(identifier):
        prepared = original(identifier)
        deletion.delete_strategy(identifier)
        return prepared
    monkeypatch.setattr(store, 'context', raced)
    response = client.post('/api/v1/backtests', json={**payload, 'request_key': 'raced'})
    assert response.status_code == 409
    assert len(rows('backtest_run')) == 1


def test_tracking_add_rechecks_deletion_after_preparing_price_evidence(workspace, monkeypatch):
    _, client, _ = workspace; strategy = make_strategy(client)
    run = client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs', json={}).json()
    original = tracking.load_entry_highs
    def raced(prepared):
        evidence = original(prepared)
        deletion.delete_strategy(strategy['strategy_id'])
        return evidence
    monkeypatch.setattr(tracking, 'load_entry_highs', raced)
    response = client.post('/api/v1/tracking-rounds', json={'run_id': run['run_id'], 'instrument_ids': ['600001.SH']})
    assert response.status_code == 409 and not rows('tracking_round')


@pytest.mark.parametrize('kind,parameters', [
    ('selection', {'strategy_id': 'target'}),
    ('source_check', {'operation': 'strategy_batch', 'scope': 'all', 'items': [{'strategy_id': 'target'}]}),
    ('source_check', {'operation': 'strategy_batch_tracking', 'strategy_id': 'target'}),
    ('source_check', {'operation': 'strategy_batch', 'items': ['malformed']}),
    ('source_check', {'operation': 'strategy_batch', 'items': [{}]}),
    ('source_check', {'operation': 'strategy_batch', 'items': None}),
    ('source_check', {'operation': 'strategy_batch', 'strategy_ids': [None]}),
    ('tracking_review', {}),
])
def test_strategy_delete_blocks_all_active_reference_forms(workspace, kind, parameters):
    _, client, _ = workspace; strategy = make_strategy(client)
    if kind == 'tracking_review': add_one(client, strategy)
    parameters = json.loads(json.dumps(parameters).replace('target', strategy['strategy_id']))
    task = task_service.create_task(kind, parameters)
    response = client.delete('/api/v1/strategies/'+strategy['strategy_id'])
    assert response.status_code == 409, response.text
    with core.connect() as connection:
        connection.execute("UPDATE job SET status='cancelled',lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (task['job_id'],))
    assert client.delete('/api/v1/strategies/'+strategy['strategy_id']).status_code == 200


def test_bootstrap_does_not_recreate_or_upgrade_deleted_builtin(workspace):
    _, client, _ = workspace
    client.post('/api/v1/strategies/bootstrap')
    identifier = 'builtin-volume-3x'
    client.delete('/api/v1/strategies/'+identifier)
    before = rows('strategy_version')
    assert client.post('/api/v1/strategies/bootstrap').status_code == 200
    assert rows('strategy_version') == before
    assert client.get('/api/v1/strategies').json()['total'] == 2


def test_bootstrap_upgrade_and_concurrent_delete_are_serialized(workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    _, client, _ = workspace
    assert client.post('/api/v1/strategies/bootstrap').status_code == 200
    identifier = 'builtin-volume-3x'; attempted = Event(); futures = []
    original = strategies.ensure_evaluator_version
    evaluator = strategies.evaluator_for
    monkeypatch.setattr(strategies, 'evaluator_for', lambda *args: evaluator(*args)+'-fixture-upgrade')
    with ThreadPoolExecutor(max_workers=1) as pool:
        def during_upgrade(connection, strategy_id):
            if strategy_id == identifier and not futures:
                def delete():
                    attempted.set()
                    return deletion.delete_strategy(identifier)
                future = pool.submit(delete); futures.append(future)
                assert attempted.wait(2)
                # DELETE cannot pass its write boundary while bootstrap still
                # owns the marker check and optional version insertion.
                with pytest.raises(TimeoutError): future.result(timeout=.05)
            return original(connection, strategy_id)
        monkeypatch.setattr(strategies, 'ensure_evaluator_version', during_upgrade)
        assert client.post('/api/v1/strategies/bootstrap').status_code == 200
        assert futures[0].result(timeout=5)['deleted'] is True
    versions = [row for row in rows('strategy_version') if row['strategy_id'] == identifier]
    assert len(versions) == 2
    assert client.post('/api/v1/strategies/bootstrap').status_code == 200
    assert [row for row in rows('strategy_version') if row['strategy_id'] == identifier] == versions


def test_deleted_strategy_execution_settings_cannot_be_overwritten(workspace):
    _, client, _ = workspace; strategy = make_strategy(client); identifier = strategy['strategy_id']
    settings = workspace_automation.configure(identifier, {'refresh_after_sync': True, 'auto_track': True})
    client.delete('/api/v1/strategies/'+identifier)
    before = rows('platform_setting')
    with pytest.raises(HTTPException) as error: workspace_automation.configure(identifier, {'auto_track': False})
    assert error.value.status_code == 409 and rows('platform_setting') == before
    client.post('/api/v1/strategies/'+identifier+'/restore')
    assert workspace_automation.get_settings(identifier) == settings


def test_backtest_pagination_and_strategy_default_all_are_explicit(workspace):
    _, client, factory = workspace; strategy = make_strategy(client)
    for index in range(3): factory(strategy, 'page-'+str(index), 'cancelled')
    first = client.get('/api/v1/backtests?limit=2').json()
    second = client.get('/api/v1/backtests?limit=2&offset=2').json()
    assert first['total'] == 3 and first['has_more'] and len(first['items']) == 2
    assert second['total'] == 3 and not second['has_more'] and len(second['items']) == 1
    for index in range(101): strategies.create_strategy({'name': 'page-'+str(index), 'definition': {'field': 'close', 'op': '>', 'value': 0}})
    assert client.get('/api/v1/strategies').json()['total'] == len(client.get('/api/v1/strategies').json()['items']) == 102
    assert len(client.get('/api/v1/strategies?limit=25&offset=100').json()['items']) == 2
    for path in ('/api/v1/backtests?limit=101', '/api/v1/strategies?limit=101', '/api/v1/backtests?offset=-1'):
        assert client.get(path).status_code == 422


def test_completed_run_identity_input_and_result_stay_immutable(workspace):
    _, client, factory = workspace; strategy = make_strategy(client)
    created, _ = factory(strategy, 'immutable')
    for clause in ("status='failed'", "backtest_id='replacement'", "summary_json='{}'", "result_snapshot_id=NULL", "strategy_snapshot_json='{}'"):
        with pytest.raises(sqlite3.IntegrityError):
            with core.connect() as connection: connection.execute('UPDATE backtest_run SET '+clause+' WHERE backtest_id=?', (created['backtest_id'],))
    with pytest.raises(sqlite3.IntegrityError):
        with core.connect() as connection: connection.execute('DELETE FROM backtest_run WHERE backtest_id=?', (created['backtest_id'],))
    assert client.delete('/api/v1/backtests/'+created['backtest_id']).status_code == 200


def test_019_upgrades_without_touching_existing_triggers():
    directory = Path(__file__).parents[1]/'app'/'migrations'
    connection = sqlite3.connect(':memory:')
    connection.execute('PRAGMA foreign_keys=ON'); connection.execute('PRAGMA recursive_triggers=ON')
    for file in sorted(directory.glob('*.sql')):
        if file.name < '019': connection.executescript(file.read_text())
    before = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
    original_tables = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert len(original_tables) == 44
    connection.executescript((directory/'019_workspace_deletion.sql').read_text())
    assert connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall() == before
    assert connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT IN ('strategy_deletion','backtest_deletion') ORDER BY name").fetchall() == original_tables
    assert connection.execute('SELECT count(*) FROM strategy_deletion').fetchone()[0] == 0
    assert connection.execute('SELECT count(*) FROM backtest_deletion').fetchone()[0] == 0
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()


def test_standard_backup_restore_preserves_tombstones_and_old_evidence(creation_context, tmp_path, monkeypatch):
    # This factory has no invented published sources; backup copies the actual
    # SQLite file through the normal deployed backup/restore implementation.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2]/'scripts'))
    from backup import perform_backup
    from restore import perform_restore
    store_, payload = creation_context
    created = store_.create_backtest(payload)
    task_service.change_task(created['job_id'], 'cancel')
    deletion.delete_strategy(payload['strategy_id'])
    with core.connect() as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        evidence = {table: [tuple(row) for row in connection.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in tables}
        triggers = [tuple(row) for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")]
    bundle = tmp_path.parent/(tmp_path.name+'-backup')
    target = tmp_path.parent/(tmp_path.name+'-restored')
    perform_backup(tmp_path, bundle, credential_dir=tmp_path/'credentials')
    perform_restore(bundle, target)
    with sqlite3.connect(target/'business/app.sqlite3') as connection:
        assert {table: connection.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall() for table in tables} == evidence
        assert connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall() == triggers
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
