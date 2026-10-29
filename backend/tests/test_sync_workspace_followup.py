"""New increments compute only after their original target-day data chain.

All data/publications here are isolated fixtures; no provider or live service.
"""
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import core, worker
from app.services import (task_service as tasks, sync_workspace_followup as phase,
    sync_progress, line_alerts, workspace_automation as automation, strategies)
from test_workspace import ws


def event(job, stage, **details):
    with core.connect() as connection:
        tasks.append_event(connection, job['job_id'], stage, stage, details=details)


def row(identifier):
    with core.connect() as connection:
        return dict(connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone())


@pytest.fixture
def chain(ws, monkeypatch):
    market, _ = ws
    day = market.entry
    monkeypatch.setattr(market, 'status', lambda: {'ready': True, 'snapshot_id': 'raw', 'data_end': day}, raising=False)
    root = tasks.create_task('increment', {phase.MARKER: phase.VERSION,
        'data_chain_version': 'sector-charts-increment-v1',
        'increment_session_version': sync_progress.LOCK_VERSION,
        'line_alert_check_version': line_alerts.VERSION}, day)
    claimed = tasks.claim_next('fixture-worker'); assert claimed['job_id'] == root['job_id']
    worker._authorize_chain(claimed)
    event(root, 'chain_target_frozen', target_date=day)
    tasks.finish(root['job_id'], 'partial')
    # Real production uses daily_raw-<generation> in SQLite and market-<generation>
    # in the physical market adapter. Their explicit lineage, not equal IDs, joins them.
    with core.connect() as connection:
        publication = dict(connection.execute("SELECT * FROM dataset_snapshot WHERE snapshot_id='raw'").fetchone())
        publication.update(snapshot_id='daily_raw-fixture-increment', job_id=root['job_id'],
            data_end=day, lineage_json=core.dumps({'market_snapshot_id': 'raw'}), state='staged', published_at_ms=None)
        columns=list(publication)
        connection.execute('INSERT INTO dataset_snapshot('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',
                           [publication[key] for key in columns])
        connection.execute("INSERT INTO snapshot_artifact SELECT 'daily_raw-fixture-increment',object_key,role,sha256,byte_count,row_count FROM snapshot_artifact WHERE snapshot_id='raw'")
        connection.execute("INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) SELECT 'daily_raw-fixture-increment',feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end FROM snapshot_coverage WHERE snapshot_id='raw'")
        connection.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id='daily_raw-fixture-increment'")
        connection.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id='daily_raw-fixture-increment'", (core.now_ms(),))
    sector = tasks.create_task('source_check', {'operation': 'sector_charts', 'mode': 'increment',
        'parent_sync_job_id': root['job_id'], 'increment_chain': True,
        'snapshot_id': 'raw', 'as_of': day}, day)
    event(root, 'followup_queued', next_task_id=sector['job_id'], target_date=day)
    doc = {'snapshot_id': 'raw', 'target_date': day, 'quality': 'partial',
           'annual_coverage': {'covered_days': 115, 'expected_days': 197, 'complete': False},
           'annual_down_coverage': {'covered_days': 86, 'expected_days': 197, 'complete': False},
           'annual_pools': {day: {'items': [], 'complete': True}},
           'failures': [{'date': '2026-01-05', 'reason': '合成历史缺口'}]}
    report = {'date': day, 'snapshot_id': 'raw', 'quality': 'partial',
              'coverage': {'collection_complete': True, 'available_count': 710, 'total_count': 710,
                           'failed_count': 0, 'unrequested_count': 0, 'partial_count': 710}}
    from app.services import market_supplement, sector_chart_batch
    reads = []
    def supplement_read(snapshot, *, dates, fields):
        reads.append((snapshot, dates, fields))
        return dict(doc), ('verified-supplement-index-v1', 'synthetic', snapshot, 'a'*64)
    monkeypatch.setattr(market_supplement, 'load_supplement_with_fingerprint', supplement_read)
    monkeypatch.setattr(sector_chart_batch, 'read_frozen_sector_chart_batch_report', lambda *_args, **_kwargs: report)
    return SimpleNamespace(root=root, sector=sector, doc=doc, report=report, reads=reads, day=day, market=market)


def finish_data(chain, status='partial'):
    tasks.finish(chain.sector['job_id'], status)
    supplement = tasks.create_task('source_check', {'operation': 'market_supplement',
        'year_history': False, 'parent_sync_job_id': chain.root['job_id']}, chain.day)
    event(chain.sector, 'followup_queued', next_task_id=supplement['job_id'], target_date=chain.day)
    tasks.finish(supplement['job_id'], status)
    chain.doc['job_id'] = supplement['job_id']
    return supplement


def logs(chain, stage):
    with core.connect() as connection:
        return [json.loads(item[0]) for item in connection.execute(
            'SELECT details_json FROM job_event WHERE job_id=? AND stage=?', (chain.root['job_id'], stage))]


def test_pending_chain_cannot_run_select_or_unlock_before_supplement(chain, monkeypatch):
    monkeypatch.setattr(automation, 'run_after_sync', lambda *_args: pytest.fail('data chain not complete'))
    assert phase.run(chain.root['job_id']) is None
    assert line_alerts.after_sync(row(chain.root['job_id'])) is None
    with pytest.raises(HTTPException):
        sync_progress.acknowledge(request_key=chain.root['request_key'], task_id=chain.root['job_id'])
    assert chain.reads == []
    tasks.finish(chain.sector['job_id'], 'succeeded')
    assert phase.recover() == [None]
    assert not logs(chain, phase.COMPLETE) and chain.reads == []


def test_historical_partial_runs_actual_selection_then_tracking_then_reminders_once(chain, monkeypatch):
    finish_data(chain)
    strategy = strategies.create_strategy({'name': '合成后置策略',
        'definition': {'field': 'close', 'op': '>', 'value': 0}}, strategy_id='after-data')
    automation.configure(strategy['strategy_id'], {'refresh_after_sync': True, 'auto_track': True})
    calls = []
    for name, module, attr in [('selection', strategies, 'run_strategy'),
                               ('track', automation.tracking, 'add_tracking'),
                               ('review', automation.tracking, 'review_tracking'),
                               ('reminders', line_alerts, 'after_sync')]:
        original = getattr(module, attr)
        def record(*args, _original=original, _name=name, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)
        monkeypatch.setattr(module, attr, record)
    assert phase.run(chain.root['job_id']) == {'state': 'completed'}
    assert calls == ['selection', 'track', 'review', 'reminders']
    assert len(logs(chain, phase.COMPLETE)) == 1 and not logs(chain, phase.BLOCKED)
    assert chain.reads[0][1] == [chain.day]
    proof = logs(chain, 'workspace_followup_started')[0]['publication_proof']
    assert proof['sector']['coverage']['collection_complete']
    assert proof['supplement']['annual_coverage']['covered_days'] == 115
    with core.connect() as connection:
        before = {table: connection.execute('SELECT count(*) FROM '+table).fetchone()[0]
                  for table in ('selection_run', 'tracking_round', 'job')}
    assert phase.recover() == []
    phase.run(chain.root['job_id'])
    with core.connect() as connection:
        assert before == {table: connection.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before}


@pytest.mark.parametrize('damage', ['main_other_generation', 'sector_failed', 'sector_unrequested', 'sector_sha', 'old_supplement',
                                   'missing_pool', 'supplement_sha', 'different_job', 'future_market'])
def test_real_missing_or_wrong_day_publications_block_without_old_input_fallback(chain, monkeypatch, damage):
    finish_data(chain)
    if damage == 'main_other_generation':
        # Keep the immutable published original; a different adapter generation
        # must not be mistaken for this task's SQLite module publication.
        monkeypatch.setattr(chain.market, 'status', lambda: {'snapshot_id': 'wrong-generation', 'data_end': chain.day})
    elif damage == 'sector_failed':
        with core.connect() as connection:
            connection.execute("UPDATE job SET status='failed' WHERE job_id=?", (chain.sector['job_id'],))
    elif damage == 'sector_unrequested':
        chain.report['coverage']['unrequested_count'] = 1
    elif damage == 'sector_sha':
        from app.services import sector_chart_batch
        monkeypatch.setattr(sector_chart_batch, 'read_frozen_sector_chart_batch_report',
                            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError('SHA不匹配')))
    elif damage == 'old_supplement': chain.doc['target_date'] = '2026-01-05'
    elif damage == 'missing_pool': chain.doc['annual_pools'] = {}
    elif damage == 'different_job': chain.doc['job_id'] = 'different-original'
    elif damage == 'future_market':
        monkeypatch.setattr(chain.market, 'status', lambda: {'snapshot_id': 'raw', 'data_end': '2026-12-31'})
    else:
        from app.services import market_supplement
        monkeypatch.setattr(market_supplement, 'load_supplement_with_fingerprint', lambda *_args, **_kwargs: ({}, None))
    monkeypatch.setattr(automation, 'run_after_sync', lambda *_args: pytest.fail('unverified dependency must block'))
    result = phase.run(chain.root['job_id'])
    assert result['state'] == 'blocked' and len(logs(chain, phase.BLOCKED)) == 1
    assert logs(chain, 'line_alert_check_queued')[0]['skipped'] is True
    assert phase.recover() == []
    phase.run(chain.root['job_id'])
    assert len(logs(chain, phase.BLOCKED)) == 1


@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_explicit_chain_stop_is_never_resumed_or_recomputed(chain, monkeypatch, action):
    finish_data(chain)
    event(chain.root, 'increment_chain_stop_requested', action=action, scope_task_id=chain.root['job_id'])
    monkeypatch.setattr(automation, 'run_after_sync', lambda *_args: pytest.fail('explicitly stopped'))
    assert phase.run(chain.root['job_id']) is None
    assert phase.recover() == []
    assert not logs(chain, phase.COMPLETE) and not logs(chain, phase.BLOCKED)


def test_restart_after_started_phase_recovers_without_duplicate_selection_or_tracking(chain, monkeypatch):
    finish_data(chain)
    strategies.create_strategy({'name': '合成恢复策略', 'definition': {'field': 'close', 'op': '>', 'value': 0}},
                               strategy_id='resume-after-data')
    automation.configure('resume-after-data', {'refresh_after_sync': True, 'auto_track': True})
    original = automation.run_after_sync
    def crash_after_calculation(identifier):
        original(identifier)
        raise SystemExit('synthetic worker stop before phase receipt')
    monkeypatch.setattr(automation, 'run_after_sync', crash_after_calculation)
    with pytest.raises(SystemExit, match='synthetic worker stop'):
        phase.run(chain.root['job_id'])
    with core.connect() as connection:
        before = {table: connection.execute('SELECT count(*) FROM '+table).fetchone()[0]
                  for table in ('selection_run', 'tracking_round', 'tracking_event')}
    monkeypatch.setattr(automation, 'run_after_sync', original)
    assert phase.recover() == [{'state': 'completed'}]
    with core.connect() as connection:
        assert before == {table: connection.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before}
    assert len(logs(chain, 'workspace_followup_started')) == 2
    assert len(logs(chain, phase.COMPLETE)) == 1 and len(logs(chain, phase.RELEASED)) == 2


def test_legacy_frozen_increment_is_never_added_to_new_phase(chain, monkeypatch):
    legacy = tasks.create_task('increment', {'data_chain_version': 'sector-charts-increment-v1'}, chain.day)
    tasks.finish(legacy['job_id'], 'succeeded')
    monkeypatch.setattr(automation, 'run_after_sync', lambda *_args: pytest.fail('legacy phase must not expand'))
    assert phase.run(legacy['job_id']) is None
    assert phase.recover() == [None]  # Only the still-pending new marked root.


def test_actual_worker_root_dispatch_queues_data_before_any_workspace_or_reminder(ws, monkeypatch):
    market, _ = ws
    root = tasks.create_task('increment', {phase.MARKER: phase.VERSION,
        'data_chain_version': 'sector-charts-increment-v1',
        'line_alert_check_version': line_alerts.VERSION}, market.entry)
    claimed = tasks.claim_next('fixture-worker')
    calls = []
    from app.services import sync_service
    monkeypatch.setattr(sync_service, 'execute_sync', lambda *_args: calls.append('publish_main') or
                        {'quality': 'partial', 'business_date': market.entry})
    monkeypatch.setattr(worker, '_queue_followup', lambda *_args: calls.append('queue_original_data_chain'))
    monkeypatch.setattr(automation, 'run_after_sync', lambda *_args: pytest.fail('root dispatch cannot select early'))
    monkeypatch.setattr(line_alerts, 'after_sync', lambda *_args: pytest.fail('root dispatch cannot alert early'))
    worker.dispatch(claimed)
    assert calls == ['publish_main', 'queue_original_data_chain']
    assert tasks.task_view(root['job_id'])['status'] == 'partial'


def test_actual_supplement_dispatch_finishes_publication_before_workspace(chain, monkeypatch):
    tasks.finish(chain.sector['job_id'], 'partial')
    supplement = tasks.create_task('source_check', {'operation': 'market_supplement',
        'year_history': False, 'parent_sync_job_id': chain.root['job_id']}, chain.day)
    event(chain.sector, 'followup_queued', next_task_id=supplement['job_id'], target_date=chain.day)
    chain.doc['job_id'] = supplement['job_id']
    calls = []
    from app.services import market_supplement
    monkeypatch.setattr(market_supplement, 'execute_supplement', lambda *_args: calls.append('publish_today') or chain.doc)
    def calculate(identifier):
        assert tasks.task_view(supplement['job_id'])['status'] == 'partial'
        assert identifier == chain.root['job_id']
        calls.append('selection_then_tracking')
        return []
    monkeypatch.setattr(automation, 'run_after_sync', calculate)
    worker.dispatch(tasks.claim_next('fixture-worker'))
    assert calls == ['publish_today', 'selection_then_tracking']
    assert len(logs(chain, phase.COMPLETE)) == 1
    worker.dispatch(row(supplement['job_id']))
    assert calls == ['publish_today', 'selection_then_tracking']


def test_direct_workspace_entry_cannot_bypass_pending_dependencies(chain):
    with pytest.raises(ValueError, match='执行|接续'):
        automation.run_after_sync(chain.root['job_id'])


def test_calculation_failure_is_visible_and_never_automatically_retried(chain, monkeypatch):
    finish_data(chain)
    monkeypatch.setattr(automation, 'run_after_sync', lambda *_args: (_ for _ in ()).throw(ValueError('synthetic failure')))
    assert phase.run(chain.root['job_id'])['state'] == 'blocked'
    assert logs(chain, phase.BLOCKED)[0]['error_type'] == 'ValueError'
    assert phase.recover() == [] and len(logs(chain, phase.RELEASED)) == 1


def test_stop_during_local_phase_cannot_ack_until_execution_has_released(chain, monkeypatch):
    finish_data(chain)
    def request_stop(identifier):
        assert sync_progress.control(task_id=identifier, action='pause')['stopping']
        with core.connect() as connection:
            state = sync_progress._chain_state(connection, row(identifier))
            assert state[3] == 'stopping' and state[4] is False
        with pytest.raises(HTTPException):
            sync_progress.acknowledge(request_key=chain.root['request_key'], task_id=identifier)
        return []
    monkeypatch.setattr(automation, 'run_after_sync', request_stop)
    with pytest.raises(tasks.TaskPaused):
        phase.run(chain.root['job_id'])
    with core.connect() as connection:
        assert not phase.active(connection, chain.root['job_id'])
    assert len(logs(chain, phase.RELEASED)) == 1 and not logs(chain, phase.COMPLETE)
    assert phase.recover() == []


def test_completion_receipt_does_not_unlock_before_the_execution_finally(chain):
    finish_data(chain)
    event(chain.root, 'workspace_followup_started')
    event(chain.root, phase.COMPLETE)
    event(chain.root, 'line_alert_check_queued', skipped=True)
    with pytest.raises(HTTPException):
        sync_progress.acknowledge(request_key=chain.root['request_key'], task_id=chain.root['job_id'])
    event(chain.root, phase.RELEASED)
    assert sync_progress.acknowledge(request_key=chain.root['request_key'], task_id=chain.root['job_id'])


def test_exclusive_worker_restart_releases_an_orphan_phase_without_resuming_a_stop(chain, monkeypatch):
    finish_data(chain)
    event(chain.root, 'workspace_followup_started')
    event(chain.root, 'increment_chain_stop_requested', action='cancel', scope_task_id=chain.root['job_id'])
    monkeypatch.setattr(automation, 'run_after_sync', lambda *_args: pytest.fail('cancelled phase must stay stopped'))
    with core.connect() as connection:
        assert phase.active(connection, chain.root['job_id'])
    sync_progress.worker_released(recovered=True)
    with core.connect() as connection:
        assert not phase.active(connection, chain.root['job_id'])
    assert logs(chain, phase.RELEASED)[0]['recovered_under_exclusive_worker_lock'] is True
    assert phase.recover() == [] and not logs(chain, phase.COMPLETE)


def test_stopped_and_acknowledged_roots_cannot_starve_a_later_ready_phase(chain, monkeypatch):
    finish_data(chain)
    monkeypatch.setattr(tasks, 'now_ms', lambda: 1)
    stopped=[]
    for offset in range(20):
        old = tasks.create_task('increment', {phase.MARKER: phase.VERSION}, chain.day,
                                request_key='old-stopped-'+str(offset))
        tasks.finish(old['job_id'], 'partial')
        event(old, 'chain_authorized')
        event(old, 'increment_chain_stop_requested', action='pause' if offset%2 else 'cancel',
              scope_task_id=old['job_id'])
        if offset%2:
            event(old, 'increment_session_acknowledged', attempt_task_id=old['job_id'])
        stopped.append(old)
    calls=[]
    monkeypatch.setattr(automation, 'run_after_sync', lambda identifier: calls.append(identifier) or [])
    assert phase.recover(limit=20) == [{'state': 'completed'}]
    assert calls == [chain.root['job_id']]
    with core.connect() as connection:
        assert not any(phase._event(connection, item['job_id'], phase.COMPLETE) for item in stopped)
