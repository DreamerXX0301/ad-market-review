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


def test_activity_revisions_follow_saved_snapshots_for_exact_days(isolated):
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    today, historical = '2026-10-20', '2026-10-16'
    path = '/api/v1/tasks/activity?date=' + historical
    assert client.get('/api/v1/tasks/activity?date=2026-02-30').status_code == 422

    initial = client.get(path).json()['read_revisions']
    assert initial['today'] == today
    assert set(initial['dates']) == {today, historical}
    assert initial['dates'][historical] == dict(auction=None, intraday=None, noon=None, news=None)

    stamp = core.now_ms()
    for slot in ('0935', '0945'):
        task = tasks.create_task('source_check', {'operation': 'intraday_snapshot', 'slot': slot}, historical)
        job_id = task['job_id']
        with core.connect() as connection:
            connection.execute('INSERT INTO market_intraday_capture(trade_date,slot,job_id) VALUES(?,?,?)',
                               (historical, slot, job_id))
        assert client.get(path).json()['read_revisions']['dates'] == initial['dates']
        with core.connect() as connection:
            connection.execute('''UPDATE market_intraday_capture SET snapshot_id=?,payload_json=?,content_sha256=?
                WHERE job_id=?''', (f'archive-{slot}', '{}', core.digest(slot), job_id))
        stored = client.get(path).json()['read_revisions']
        assert stored['dates'][historical]['intraday'] != initial['dates'][historical]['intraday']
        assert stored['dates'][today] == initial['dates'][today]
        tasks.finish(job_id, 'partial')
        assert client.get(path).json()['read_revisions']['dates'] == stored['dates']
        initial = stored

    job_id = 'fixture-auction'
    with core.connect() as connection:
        params = {'operation': 'auction_snapshot'}
        connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,
            parameters_json,parameters_hash,status,created_at_ms) VALUES(?,?,?,'source_check',?,?,?,'queued',?)''',
            (job_id, job_id, job_id, historical, core.dumps(params), core.digest(params), stamp))
        connection.execute('INSERT INTO market_auction_capture VALUES(?,?,?,?)', (historical, job_id, job_id, stamp))
        stamp_text = historical + 'T09:25:30+08:00'
        payload = core.dumps({'date': historical, 'version': 'auction-snapshot-v1'})
        connection.execute('INSERT INTO market_auction_snapshot VALUES(?,?,?,?,?,?,?,?,?)',
                           ('auction-proof', historical, job_id, stamp_text, stamp_text, stamp,
                            'partial', core.digest(payload), payload))
    auction_only = client.get(path).json()['read_revisions']['dates'][historical]
    assert auction_only['auction'] is not None
    noon = tasks.create_task('source_check', {'operation': 'noon_snapshot'}, historical)
    with core.connect() as connection:
        payload = core.dumps({'date': historical, 'version': 'auction-discovery-v1', 'state': 'partial'})
        connection.execute('INSERT INTO market_auction_discovery VALUES(?,?,?,?,?,?)',
                           (historical, job_id, 'auction-proof', stamp, core.digest(payload), payload))
        connection.execute('INSERT INTO market_news_snapshot VALUES(?,?,?,?,?,?,?,?,?)',
                           (historical, 'auction', 'partial', stamp_text, None, stamp_text, None, '[]', core.digest([])))
        noon_id = noon['job_id']
        connection.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (historical, noon_id, noon_id, stamp))
        payload = core.dumps({'date': historical, 'version': 'noon-snapshot-v1'})
        stamp_text = historical + 'T12:05:00+08:00'
        connection.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)',
                           ('noon-proof', historical, noon_id, stamp_text, stamp_text, stamp,
                            'complete', core.digest(payload), payload, '[]'))
    final = client.get(path).json()['read_revisions']['dates'][historical]
    assert final['auction'] != auction_only['auction']
    assert final['news'] is not None
    assert final['noon'] is not None
    assert final['intraday'] == auction_only['intraday']


def test_activity_revision_survives_terminal_window_and_separates_publication_heads(isolated):
    from app.services.sync_progress import LOCK_VERSION

    initial = tasks.task_activity()['read_revisions']
    tracked = tasks.create_task('tracking_review', {})
    tasks.finish(tracked['job_id'])
    complete = tasks.task_activity()['read_revisions']
    assert complete['domains']['tracking'] != initial['domains']['tracking']
    for index in range(6):
        job = tasks.create_task('research', {'index': index})
        tasks.finish(job['job_id'])
    activity = tasks.task_activity()
    assert tracked['job_id'] not in {item['job_id'] for item in activity['items']}
    assert activity['read_revisions']['domains']['tracking'] == complete['domains']['tracking']

    for dirname, identifier, value in (('market-supplement', 'snapshot_id', 'market-test'),
                                       ('dragon-tiger', 'record_ref', 'artifacts/test.json')):
        head = core.published_dir() / dirname / 'head.json'
        head.parent.mkdir(parents=True)
        head.write_text(core.dumps({identifier: value, 'sha256': '1' * 64}))
    receipts = tasks.task_activity()['read_revisions']
    assert receipts['market'] == complete['market']
    assert receipts['domains']['supplement'] and receipts['domains']['dragon_tiger']
    assert receipts['domains']['suspensions'] is None
    archive = core.work_dir() / 'market-suspensions' / 'confirmed.json'
    core.atomic_json(archive, {'version': 'market-suspensions-v1', 'items': []})
    updated = tasks.task_activity()['read_revisions']
    assert updated['domains']['suspensions'] is not None
    assert updated['market'] == receipts['market']

    publishing = tasks.create_task('initialise', {})
    close_day = '2026-10-19'
    fixture = core.published_dir() / 'calendar-fixture.json'
    core.atomic_json(fixture, [close_day])
    artifact = {'object_key': fixture.name, 'sha256': core.hash_file(fixture),
                'byte_count': fixture.stat().st_size, 'row_count': 1}
    calendar = core.publish_snapshot(module='calendar', job_id=publishing['job_id'],
                                     start=close_day, end=close_day, artifacts=[artifact])
    with core.connect() as connection:
        connection.execute('INSERT INTO business_clock VALUES(1,?,?,?)', (close_day, calendar, core.now_ms()))
    published = tasks.task_activity()['read_revisions']
    assert published['market'] != receipts['market']
    assert close_day in published['dates']

    tasks.create_task('increment', {'increment_session_version': LOCK_VERSION})
    locked = tasks.task_activity()['read_revisions']
    assert locked == {'today': '2026-10-20', 'market': None, 'market_locked': True, 'dates': {}, 'domains': {}}


def test_historical_proof_tracks_archive_controls_and_reference_visibility(isolated):
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    day = '2026-10-19'
    path = f'/api/v1/tasks/activity?date={day}'
    archive_path = path + '&historical=true'
    job = tasks.create_task('initialise', {})
    generation = 'market-history-proof'
    reference = core.published_dir() / generation / 'reference.json'
    core.atomic_json(reference, {})
    snapshot = core.publish_snapshot(module='topics', job_id=job['job_id'],
        start=day, end=day, activate=False, lineage={'market_snapshot_id': generation}, artifacts=[{
            'role': 'reference.json', 'object_key': f'{generation}/reference.json',
            'sha256': core.hash_file(reference), 'byte_count': reference.stat().st_size, 'row_count': 1}])
    tasks.finish(job['job_id'])
    stamp = core.now_ms()
    with core.connect() as connection:
        connection.execute('INSERT INTO market_intraday_brief VALUES(?,?,?,?,?,?)',
                           (day, '09:35', 'a' * 64, '{}', stamp, 0))

    regular = client.get(path).json()['read_revisions']
    assert 'historical_reference' not in regular['domains']
    assert 'archive_controls' not in regular['domains']
    before = client.get(archive_path).json()['read_revisions']
    assert before['domains']['historical_reference']
    assert before['domains']['archive_controls']
    assert client.get(archive_path).json()['read_revisions'] == before
    with core.connect() as connection:
        connection.execute('UPDATE market_intraday_brief SET body_json=?,model_attempted=1 WHERE trade_date=?',
                           ('{"updated":true}', day))
        connection.execute('INSERT INTO platform_setting VALUES(?,?,?)',
                           ('intraday-automation-v1', '{"enabled":true}', stamp))
    updated = client.get(archive_path).json()['read_revisions']
    assert updated['dates'][day]['intraday'] != before['dates'][day]['intraday']
    assert updated['domains']['archive_controls'] != before['domains']['archive_controls']
    assert updated['domains']['historical_reference'] == before['domains']['historical_reference']

    with core.connect() as connection:
        connection.execute('INSERT INTO snapshot_gc VALUES(?,?,?,?)',
                           (snapshot, 'gc_pending', stamp, '测试归档撤回'))
    assert client.get(archive_path).json()['read_revisions']['domains']['historical_reference'] is None

    tasks.create_task('research', {})
    busy = client.get(archive_path).json()
    assert busy['active_count'] == 1
    assert 'archive_controls' not in busy['read_revisions']['domains']


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
