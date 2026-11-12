"""Worker, sync-lock ancestry and public reminders use the new opt-in path."""
import json
from contextlib import contextmanager

import pytest

from app import core, worker
from app.routers.automatic_price_alerts import router as automatic_router
from app.routers.watchlist import router as watchlist_router
from app.services import automatic_price_alerts as alerts, collection_controls
from app.services import market_alerts, sync_progress, task_service as tasks
from test_automatic_price_alerts import automatic, configure, drain, publish_next
from test_workspace import ws


def root_after_data(market):
    root = tasks.create_task('increment', {'increment_session_version': sync_progress.LOCK_VERSION,
        'automatic_price_alert_check_version': alerts.VERSION}, market.entry, request_key='auto-alert-sync-parent')
    tasks.finish(root['job_id'])
    with core.connect() as connection:
        # This narrow fixture has no provider/data chain. Explicitly finish its
        # data handoff before testing only the new local reminder continuation.
        tasks.append_event(connection, root['job_id'], 'followup_queued', '测试中数据步骤已结束', details={'skipped': True})
        root = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (root['job_id'],)).fetchone())
    return root


def test_sync_lock_waits_for_auto_child_dispatch_and_ancestry_is_preserved(automatic):
    market = automatic
    configure()
    drain()
    publish_next(market)
    root = root_after_data(market)
    waiting = sync_progress.session(task_id=root['job_id'])
    assert waiting['status'] == 'awaiting_followup' and not waiting['can_acknowledge']
    child_id = alerts.after_sync(root)
    assert child_id is not None
    state = sync_progress.session(task_id=root['job_id'])
    assert state['status'] == 'running' and not state['can_acknowledge']
    assert any(task['job_id'] == child_id for task in state['tasks'])
    with core.connect() as connection:
        child = connection.execute('SELECT * FROM job WHERE job_id=?', (child_id,)).fetchone()
        assert collection_controls.protected(connection, child)
        assert sync_progress.root_for_task(connection, child_id)['job_id'] == root['job_id']
    job = tasks.claim_next('automatic-sync-child')
    assert job['job_id'] == child_id
    worker.dispatch(job)
    assert alerts.list_alerts()['total'] == 1
    state = sync_progress.session(task_id=root['job_id'])
    assert state['can_acknowledge'] and state['status'] == 'succeeded'
    result = sync_progress.acknowledge(request_key=root['request_key'], task_id=root['job_id'], epoch=state['session_epoch'])
    assert result['acknowledged']
    with core.connect() as connection:
        assert sync_progress.lock_record(connection) is None


def test_recovered_worker_attempt_rejects_old_lease_and_emits_one_event(automatic):
    market = automatic
    configure()
    drain()
    publish_next(market)
    task = alerts.create_check({})
    old = tasks.claim_next('same-worker-name')
    with core.connect() as connection:
        connection.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?', (core.now_ms() - 1, task['job_id']))
    recovered = tasks.claim_next('same-worker-name')
    assert old['lease_owner'] != recovered['lease_owner']
    with pytest.raises(tasks.TaskCancelled):
        worker.dispatch(old)
    worker.dispatch(recovered)
    assert tasks.task_view(task['job_id'])['status'] == 'succeeded'
    assert alerts.list_alerts()['total'] == 1


@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_committed_root_stop_marker_fences_write_even_before_child_status_changes(automatic, action):
    market = automatic
    configure()
    drain()
    publish_next(market)
    root = root_after_data(market)
    child = alerts.after_sync(root)
    job = tasks.claim_next('stopped-after-claim')
    assert job['job_id'] == child
    with core.connect() as connection:
        tasks.append_event(connection, root['job_id'], 'increment_chain_stop_requested', '测试停止竞态',
                           details={'action': action, 'scope_task_id': root['job_id']})
        assert connection.execute('SELECT status FROM job WHERE job_id=?', (child,)).fetchone()[0] == 'running'
    # The direct worker call covers the transaction guard, not only dispatch's
    # early cancellation check; no event may enter the marker/status race gap.
    with pytest.raises(tasks.TaskPaused if action == 'pause' else tasks.TaskCancelled):
        alerts.execute_check(job)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_event').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_evaluation WHERE job_id=?', (child,)).fetchone()[0] == 0


def test_public_api_topfive_combines_sources_without_rewriting_manual_records(automatic, ws):
    market, client = ws
    assert market is automatic
    client.app.include_router(automatic_router)
    client.app.include_router(watchlist_router)
    path = '/api/v1/instruments/600001.SH/automatic-price-alerts'
    assert client.get(path).json()['rule']['enabled'] is False
    response = client.put(path, json={'enabled': True, 'modes': ['close_breakout'],
                                    'require_volume': True, 'expected_revision': 0})
    assert response.status_code == 200 and response.json()['task']['status'] == 'queued'
    drain()
    publish_next(market)
    requested = client.post('/api/v1/watchlist/automatic-price-alerts/evaluate', json={})
    assert requested.status_code == 200
    worker.dispatch(tasks.claim_next('public-api-auto-alert'))
    original = {}
    with core.connect() as connection:
        for index in range(6):
            identifier = 'manual-evidence-' + str(index)
            event = {'event_id': identifier, 'instrument_id': '600001.SH', 'code': '600001', 'name': '测试个股',
                     'annotation_id': 'old-drawn-line', 'annotation_revision': 1, 'line_type': 'support',
                     'rule': 'support_reclaim', 'trade_date': market.entry, 'state': 'triggered',
                     'created_at_ms': index + 1, 'title': '旧手动提醒'}
            original[identifier] = core.dumps(event)
            connection.execute('INSERT INTO line_alert_event VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (identifier, 'old-drawn-line-' + str(index), 1, '600001.SH', 'support', 'support_reclaim',
                 market.entry, 'triggered', 'fixture-job', original[identifier], index + 1))
    summary = client.get('/api/v1/watchlist/line-alerts/summary').json()
    assert summary['total'] == 7 and len(summary['items']) == 5
    assert summary['items'][0]['source_kind'] == alerts.SOURCE
    assert summary['items'][1]['event_id'] == 'manual-evidence-5'
    listing = client.get('/api/v1/watchlist/line-alerts?scope=today&limit=2').json()
    assert listing['total'] == 7 and len(listing['items']) == 2
    assert set(listing['latest_checks']) == {'manual_line', 'automatic_price_volume'}
    with core.connect() as connection:
        assert {row['event_id']: row['event_json'] for row in connection.execute('SELECT event_id,event_json FROM line_alert_event')} == original
        assert market_alerts.latest_by_stock(connection, ['600001.SH'], market.entry)['600001.SH']['source_kind'] == alerts.SOURCE


def test_union_remains_valid_before_first_publication_without_business_clock(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    result = market_alerts.list_alerts(scope='today')
    assert result['business_date'] is None
    assert result['items'] == [] and result['total'] == 0
    assert result['latest_checks'] == {'manual_line': None, 'automatic_price_volume': None}
    assert market_alerts.alert_summary() == {'business_date': None, 'total': 0, 'limit': 5, 'items': []}


def test_config_read_uses_one_snapshot_during_concurrent_revision_commit(automatic, monkeypatch):
    configure()
    drain()
    old = alerts.get_config('600001.SH')
    fired = []

    class RevisionCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            if not fired:
                fired.append(True)
                with core.connect() as writer:
                    writer.execute('UPDATE automatic_price_alert_rule SET revision=revision+1,enabled=0')
                    writer.execute('DELETE FROM automatic_price_alert_baseline')
            return row

    @contextmanager
    def concurrent_connect():
        with core.connect() as connection:
            class ReadConnection:
                def execute(self, statement, parameters=()):
                    cursor = connection.execute(statement, parameters)
                    if statement.startswith('SELECT * FROM automatic_price_alert_rule WHERE instrument_id='):
                        return RevisionCursor(cursor)
                    return cursor

                def __getattr__(self, name):
                    return getattr(connection, name)
            yield ReadConnection()

    monkeypatch.setattr(alerts, 'connect', concurrent_connect)
    during = alerts.get_config('600001.SH')
    assert during['rule']['revision'] == old['rule']['revision']
    assert during['latest_baseline'] == old['latest_baseline']
    with core.connect() as connection:
        assert connection.execute('SELECT revision FROM automatic_price_alert_rule').fetchone()[0] == old['rule']['revision'] + 1
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_baseline').fetchone()[0] == 0


@pytest.mark.parametrize('view', ['summary', 'list'])
def test_reminder_union_does_not_mix_a_commit_between_two_source_reads(automatic, monkeypatch, view):
    market = automatic
    configure()
    drain()
    publish_next(market)
    alerts.create_check({})
    drain()
    fired = []

    def simultaneous_commit():
        if fired:
            return
        fired.append(True)
        with core.connect() as writer:
            original = writer.execute('SELECT * FROM automatic_price_alert_event LIMIT 1').fetchone()
            body = json.loads(original['event_json'])
            body.update(event_id='concurrent-automatic-event', rule='support_breakdown', state='invalidated')
            writer.execute('INSERT INTO automatic_price_alert_event VALUES(?,?,?,?,?,?,?,?,?,?)',
                (body['event_id'], original['rule_id'], original['rule_revision'], original['instrument_id'],
                 body['rule'], original['trade_date'], body['state'], original['job_id'], core.dumps(body), original['created_at_ms'] + 1))
            body.update(event_id='concurrent-manual-event', annotation_id='concurrent-line')
            writer.execute('INSERT INTO line_alert_event VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (body['event_id'], 'concurrent-line', 1, body['instrument_id'], 'support', 'support_breakdown',
                 body['trade_date'], 'invalidated', original['job_id'], core.dumps(body), original['created_at_ms'] + 1))

    original = market_alerts.line_alerts.alert_summary if view == 'summary' else market_alerts.line_alerts.list_alerts

    def first_source(*args, **kwargs):
        result = original(*args, **kwargs)
        simultaneous_commit()
        return result

    monkeypatch.setattr(market_alerts.line_alerts, 'alert_summary' if view == 'summary' else 'list_alerts', first_source)
    result = market_alerts.alert_summary() if view == 'summary' else market_alerts.list_alerts(scope='today')
    assert result['total'] == 1 and len(result['items']) == 1
    assert result['items'][0]['event_id'].startswith('auto-price-event-')
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_event').fetchone()[0] == 2
        assert connection.execute('SELECT count(*) FROM line_alert_event').fetchone()[0] == 1
