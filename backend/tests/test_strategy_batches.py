"""Manual multi-strategy batches persist order/results without browser execution."""
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import core, worker
from app.routers.workspace import router
from app.services import strategy_batches as batches, task_service as tasks, sector_strategies, sector_tracking, tracking
from test_workspace import ws, make_strategy, advance
from test_platform import isolated
from test_sector_strategies import workspace, cached, create


def start(client, strategies=(), target='stock', scope='selected', key='batch-fixture'):
    payload = {'target_type': target, 'scope': scope, 'request_key': key}
    if scope == 'selected': payload['strategy_ids'] = [s['strategy_id'] for s in strategies]
    response = client.post('/api/v1/strategy-batches', json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def step():
    job = tasks.claim_next('test-worker')
    assert job
    try: worker.dispatch(job)
    except Exception as error:
        tasks.finish(job['job_id'], 'failed', str(error), str(error), lease_owner=job['lease_owner'])
    return job


def drain(count):
    return [step() for _ in range(count)]


def test_order_results_read_recovery_and_request_idempotency(ws):
    market, client = ws
    one = make_strategy(client); two = make_strategy(client, {'field': 'close', 'op': '>', 'value': 0})
    batch = start(client, [two, one])
    assert batch['status'] == 'queued' and batch['progress'] == 0
    assert [x['strategy_id'] for x in batch['items']] == [two['strategy_id'], one['strategy_id']]
    assert [x['sequence'] for x in batch['items']] == [1, 2]
    replay = start(client, [two, one])
    assert replay['batch_id'] == batch['batch_id'] and replay['request_key'] == 'batch-fixture'
    # Even a parent that looks older than its children can never summarize first.
    with core.connect() as c: c.execute('UPDATE job SET created_at_ms=1 WHERE job_id=?', (batch['batch_id'],))
    claimed = tasks.claim_next('fixture')
    assert claimed['job_id'] == batch['items'][0]['job_id']
    assert tasks.claim_next('second-worker') is None
    activity = tasks.task_activity()
    assert len([x for x in activity['items'] if x['kind'] == 'selection' and x['status'] == 'queued']) == 0
    parent = next(x for x in activity['items'] if x['job_id'] == batch['batch_id'])
    assert parent['strategy_name'] == two['name'] and parent['actual_total_batches'] == 4
    worker.dispatch(claimed)
    recovered = client.get('/api/v1/strategy-batches/'+batch['batch_id']).json()
    assert recovered['completed_count'] == 0 and recovered['progress'] == 25
    assert recovered['completed_steps'] == 1 and recovered['total_steps'] == 4
    assert recovered['auto_add'] is True and recovered['items'][0]['tracking_status'] == 'queued'
    assert step()['job_id'] == batch['items'][0]['tracking_job_id']
    recovered = batches.get_batch(batch['batch_id'])
    assert recovered['completed_count'] == 1 and recovered['progress'] == 50
    assert recovered['items'][0]['run_id']
    # Reopening a browser only GETs persisted receipts, never creates more jobs.
    another = TestClient(client.app)
    assert another.get('/api/v1/strategy-batches?target_type=stock').json()['items'][0]['batch_id'] == batch['batch_id']
    rest = drain(3)
    assert [j['job_id'] for j in rest] == [batch['items'][1]['job_id'], batch['items'][1]['tracking_job_id'], batch['batch_id']]
    result = batches.get_batch(batch['batch_id'])
    assert result['status'] == 'succeeded' and result['completed_count'] == 2 and result['progress'] == 100
    assert len({x['run_id'] for x in result['items']}) == 2
    assert client.get('/api/v1/tracking-rounds').json()['total'] == 3
    for item in result['items']:
        detail = client.get('/api/v1/strategy-runs/'+item['run_id']).json()
        assert detail['strategy_version'] == item['version'] and detail['business_date'] == market.entry
    assert start(client, [two, one])['items'] == result['items']
    with core.connect() as c:
        assert c.execute("SELECT count(*) FROM job WHERE json_extract(parameters_json,'$.operation') IN ('strategy_batch','strategy_batch_item','strategy_batch_tracking')").fetchone()[0] == 5
    assert tasks.claim_next('done') is None


def test_all_is_current_target_only_and_frozen(ws):
    _, client = ws
    one = make_strategy(client); two = make_strategy(client)
    sector = sector_strategies.create_strategy({'name': '隔离板块', 'definition': {'field': 'close', 'op': '>', 'value': 0}})
    batch = start(client, target='stock', scope='all')
    assert {x['strategy_id'] for x in batch['items']} == {one['strategy_id'], two['strategy_id']}
    later = make_strategy(client)
    assert len(start(client, target='stock', scope='all')['items']) == 2
    assert sector['strategy_id'] not in {x['strategy_id'] for x in batch['items']}
    assert later['strategy_id'] not in {x['strategy_id'] for x in batch['items']}


def test_conflicts_and_invalid_requests(ws):
    _, client = ws; strategy = make_strategy(client)
    bad = [
        {}, {'target_type': 'fund', 'scope': 'all'},
        {'target_type': 'stock', 'scope': 'all', 'strategy_ids': ['x']},
        {'strategy_ids': [strategy['strategy_id']]*2}, {'strategy_ids': ['missing']},
        {'strategy_ids': [strategy['strategy_id']], 'run_automatically': True},
    ]
    for payload in bad:
        assert client.post('/api/v1/strategy-batches', json=payload).status_code in (404, 422)
    batch = start(client, [strategy])
    mismatch = client.post('/api/v1/strategy-batches', json={'target_type': 'stock', 'scope': 'all', 'request_key': 'batch-fixture'})
    assert mismatch.status_code == 409
    other = client.post('/api/v1/strategy-batches', json={'scope': 'all', 'request_key': 'other'})
    assert other.status_code == 409 and other.json()['detail']['batch_id'] == batch['batch_id']
    active = tasks.claim_next('busy-worker')
    busy = client.post('/api/v1/strategy-batches', json={'scope': 'all', 'request_key': 'third'})
    assert busy.status_code == 409 and busy.json()['detail']['active_task']['job_id'] == active['job_id']


def test_one_failure_keeps_other_results_and_old_success(ws):
    _, client = ws; one = make_strategy(client); two = make_strategy(client)
    previous = client.post('/api/v1/strategies/'+one['strategy_id']+'/runs', json={}).json()
    batch = start(client, [one, two])
    # Editing after submission does not silently run a different rule version.
    client.patch('/api/v1/strategies/'+one['strategy_id'], json={'definition': {'field': 'close', 'op': '>', 'value': 0}})
    drain(5)
    result = batches.get_batch(batch['batch_id'])
    assert result['status'] == 'partial' and result['failed_count'] == 1 and result['succeeded_count'] == 1
    assert result['items'][0]['run_id'] is None and '版本' in result['items'][0]['error']
    old = client.get('/api/v1/strategy-runs/'+previous['run_id']).json()
    assert old['run_id'] == previous['run_id']
    assert result['items'][1]['run_id']


@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_stop_mid_batch_never_revives_or_repeats_success(ws, action):
    _, client = ws; one = make_strategy(client); two = make_strategy(client)
    batch = start(client, [one, two]); first = step()
    first_run = batches.get_batch(batch['batch_id'])['items'][0]['run_id']
    tasks.change_task(batch['batch_id'], action)
    assert tasks.claim_next('after-stop') is None
    assert batches.get_batch(batch['batch_id'])['items'][0]['run_id'] == first_run
    assert batches.get_batch(batch['batch_id'])['items'][1]['status'] == ('paused' if action == 'pause' else 'cancelled')
    if action == 'pause':
        tasks.change_task(batch['batch_id'], 'resume')
        rest = drain(4)
        assert rest[0]['job_id'] == batch['items'][0]['tracking_job_id']
        assert rest[1]['job_id'] == batch['items'][1]['job_id']
        assert batches.get_batch(batch['batch_id'])['status'] == 'succeeded'
        assert batches.get_batch(batch['batch_id'])['items'][0]['run_id'] == first_run
    else:
        tasks.change_task(batch['batch_id'], 'resume')
        assert tasks.claim_next('after-cancel') is None
    with pytest.raises(HTTPException): tasks.change_task(batch['batch_id'], 'retry')


def test_old_attempt_cannot_progress_finish_or_commit_after_resume(ws):
    _, client = ws; strategy = make_strategy(client)
    batch = start(client, [strategy]); old = tasks.claim_next('same-worker')
    tasks.change_task(batch['batch_id'], 'pause'); tasks.change_task(batch['batch_id'], 'resume')
    assert tasks.task_view(old['job_id'])['status'] == 'queued'
    with pytest.raises(tasks.TaskCancelled): tasks.progress(old['job_id'], 'old', 88, lease_owner=old['lease_owner'])
    tasks.finish(old['job_id'], 'failed', lease_owner=old['lease_owner'])
    assert tasks.task_view(old['job_id'])['status'] == 'queued'
    new = tasks.claim_next('same-worker')
    assert new['lease_owner'] != old['lease_owner']
    with pytest.raises(tasks.TaskCancelled): worker.dispatch(old)
    assert tasks.task_view(new['job_id'])['status'] == 'running'
    assert tasks.task_view(new['job_id'])['progress'] == 0
    worker.dispatch(new); drain(2)
    assert batches.get_batch(batch['batch_id'])['status'] == 'succeeded'
    with core.connect() as c: assert c.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 1


def test_expired_worker_recovers_only_incomplete_item(ws):
    _, client = ws; one = make_strategy(client); two = make_strategy(client)
    batch = start(client, [one, two]); drain(2); old = tasks.claim_next('same-worker')
    first_run = batches.get_batch(batch['batch_id'])['items'][0]['run_id']
    with core.connect() as c: c.execute('UPDATE job SET lease_expires_at_ms=?,accepted_batches=60 WHERE job_id=?', (core.now_ms()-1,old['job_id']))
    recovered = tasks.claim_next('same-worker')
    assert recovered['job_id'] == old['job_id'] and recovered['lease_owner'] != old['lease_owner']
    assert recovered['recovery_count'] == 1 and recovered['accepted_batches'] == 0
    with pytest.raises(tasks.TaskCancelled): worker.dispatch(old)
    worker.dispatch(recovered); drain(2)
    result = batches.get_batch(batch['batch_id'])
    assert result['status'] == 'succeeded' and result['items'][0]['run_id'] == first_run
    assert tasks.claim_next('end') is None


def test_day_change_fails_frozen_items_and_blocks_paused_resume(ws):
    market, client = ws; strategy = make_strategy(client)
    batch = start(client, [strategy]); tasks.change_task(batch['batch_id'], 'pause')
    advance(market, 71)
    with pytest.raises(HTTPException): tasks.change_task(batch['batch_id'], 'resume')
    assert tasks.claim_next('no-auto') is None
    tasks.change_task(batch['batch_id'], 'cancel')
    fresh = start(client, [strategy], key='fresh')
    advance(market, 72); drain(3)
    result = batches.get_batch(fresh['batch_id'])
    assert result['status'] == 'failed' and '输入快照' in result['items'][0]['error']


def test_sector_sequential_results_are_separate(workspace, monkeypatch):
    monkeypatch.setattr(sector_tracking, 'get_market', lambda: workspace.market)
    cached(workspace)
    one = create(name='板块一'); two = create(name='板块二', definition={'field': 'close', 'op': '>', 'value': 999999})
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    batch = start(client, [two, one], target='sector')
    assert client.get('/api/v1/strategy-batches?target_type=stock').json()['total'] == 0
    drain(5)
    result = batches.get_batch(batch['batch_id'])
    assert result['status'] == 'succeeded' and result['succeeded_count'] == 2
    assert [x['strategy_id'] for x in result['items']] == [two['strategy_id'], one['strategy_id']]
    assert sector_strategies.get_run(result['items'][0]['run_id'])['matched_count'] == 0
    assert sector_strategies.get_run(result['items'][1]['run_id'])['matched_count'] > 0
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM sector_tracking_round').fetchone()[0] > 0


def test_running_pause_resume_does_not_fail_resumed_queue(ws, monkeypatch):
    market, client = ws; strategy = make_strategy(client)
    batch = start(client, [strategy]); old = tasks.claim_next('fixture')
    original_calendar = market.calendar; changed = False
    def stop_during_computation():
        nonlocal changed
        if not changed:
            changed = True
            tasks.change_task(batch['batch_id'], 'pause')
            tasks.change_task(batch['batch_id'], 'resume')
        return original_calendar()
    with monkeypatch.context() as scope:
        scope.setattr(market, 'calendar', stop_during_computation)
        with pytest.raises(tasks.TaskCancelled): worker.dispatch(old)
    assert tasks.task_view(old['job_id'])['status'] == 'queued'
    with core.connect() as c: assert c.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 0
    drain(3)
    assert batches.get_batch(batch['batch_id'])['status'] == 'succeeded'


def test_siblings_cannot_overtake_persisted_sequence(ws):
    _, client = ws; one = make_strategy(client); two = make_strategy(client)
    batch = start(client, [one, two]); first, second = batch['items']
    with core.connect() as c:
        c.execute('UPDATE job SET created_at_ms=1 WHERE job_id=?', (second['job_id'],))
    assert step()['job_id'] == first['job_id']
    assert step()['job_id'] == first['tracking_job_id']
    assert step()['job_id'] == second['job_id']
    assert step()['job_id'] == second['tracking_job_id']
    assert step()['job_id'] == batch['batch_id']


def test_snapshot_change_keeps_success_and_fails_remaining_input(ws):
    _, client = ws; one = make_strategy(client); two = make_strategy(client)
    batch = start(client, [one, two]); drain(2)
    first_run = batches.get_batch(batch['batch_id'])['items'][0]['run_id']
    with core.connect() as c: c.execute("DELETE FROM published_head WHERE module='analysis_bars'")
    drain(3)
    result = batches.get_batch(batch['batch_id'])
    assert result['status'] == 'partial' and result['items'][0]['run_id'] == first_run
    assert '输入快照' in result['items'][1]['error'] and result['items'][1]['run_id'] is None


def test_local_member_edit_is_an_input_change(ws):
    _, client = ws; strategy = make_strategy(client)
    topic = client.post('/api/v1/topics', json={'name': '本地题材', 'instrument_ids': ['600001.SH']}).json()
    batch = start(client, [strategy]); tasks.change_task(batch['batch_id'], 'pause')
    response = client.post('/api/v1/topics/'+topic['topic_id']+'/members', json={'instrument_id': '000001.SZ', 'action': 'include'})
    assert response.status_code == 200
    with pytest.raises(HTTPException): tasks.change_task(batch['batch_id'], 'resume')
    tasks.change_task(batch['batch_id'], 'cancel')
    next_batch = start(client, [strategy], key='new-members')
    client.patch('/api/v1/topics/'+topic['topic_id'], json={'name': '已改名'})
    drain(3)
    result = batches.get_batch(next_batch['batch_id'])
    assert result['status'] == 'failed' and '本地题材' in result['items'][0]['error']


def test_sector_all_never_includes_stock_strategy(workspace, monkeypatch):
    monkeypatch.setattr(sector_tracking, 'get_market', lambda: workspace.market)
    cached(workspace); one = create(); two = create()
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    stock = make_strategy(client)
    batch = start(client, target='sector', scope='all')
    assert {x['strategy_id'] for x in batch['items']} == {one['strategy_id'], two['strategy_id']}
    assert stock['strategy_id'] not in {x['strategy_id'] for x in batch['items']}
    drain(5)
    assert batches.get_batch(batch['batch_id'])['succeeded_count'] == 2


class SimulatedCrash(BaseException):
    pass


def expire(identifier):
    with core.connect() as connection:
        connection.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?', (core.now_ms()-1, identifier))


def test_auto_tracking_skips_existing_and_manual_ignores_without_reset(ws):
    _, client = ws; strategy = make_strategy(client, {'field': 'close', 'op': '>', 'value': 0})
    old_run = client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs', json={}).json()
    original = client.post('/api/v1/tracking-rounds', json={'run_id': old_run['run_id'], 'instrument_ids': ['600001.SH','000001.SZ']}).json()
    removed = original['items'][0]
    client.post('/api/v1/tracking-rounds/'+removed['round_id']+'/remove', json={})
    with core.connect() as c:
        before = [dict(row) for row in c.execute('SELECT * FROM tracking_round ORDER BY round_id')]
    batch = start(client, [strategy]); drain(3)
    result = batches.get_batch(batch['batch_id']); item = result['items'][0]
    assert result['auto_add'] is True and result['plan_version'] == batches.PLAN_VERSION
    assert item['selection_status'] == item['tracking_status'] == 'succeeded'
    assert item['matched_count'] == 2 and item['added_count'] == 0 and item['skipped_count'] == 2 and item['ignored_count'] == 1
    with core.connect() as c:
        assert before == [dict(row) for row in c.execute('SELECT * FROM tracking_round ORDER BY round_id')]


def test_crash_after_selection_keeps_durable_tracking_step(ws):
    _, client = ws; strategy = make_strategy(client)
    batch = start(client, [strategy]); step()
    stored = batches.get_batch(batch['batch_id'])['items'][0]
    assert stored['run_id'] and stored['selection_status'] == 'succeeded' and stored['tracking_status'] == 'queued'
    assert batches.get_batch(batch['batch_id'])['completed_count'] == 0
    # A fresh process/browser follows persisted jobs rather than resubmitting selection.
    assert client.get('/api/v1/strategy-batches/'+batch['batch_id']).json()['items'][0]['run_id'] == stored['run_id']
    drain(2)
    item = batches.get_batch(batch['batch_id'])['items'][0]
    assert item['run_id'] == stored['run_id'] and item['added_count'] == 1
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 1


def test_crash_after_tracking_commit_does_not_readd_ended_round(ws, monkeypatch):
    _, client = ws; strategy = make_strategy(client)
    batch = start(client, [strategy]); step(); tracking_job = tasks.claim_next('worker')
    real_finish = tasks.finish
    def crash_before_terminal(identifier, *args, **kwargs):
        if identifier == tracking_job['job_id']: raise SimulatedCrash()
        return real_finish(identifier, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(tasks, 'finish', crash_before_terminal)
        with pytest.raises(SimulatedCrash): worker.dispatch(tracking_job)
    with core.connect() as c:
        round_ = dict(c.execute('SELECT * FROM tracking_round').fetchone())
        end_fixture_round(c, round_)
    expire(tracking_job['job_id']); recovered = tasks.claim_next('worker')
    assert recovered['job_id'] == tracking_job['job_id'] and recovered['lease_owner'] != tracking_job['lease_owner']
    with pytest.raises(tasks.TaskCancelled): worker.dispatch(tracking_job)
    worker.dispatch(recovered); step()
    item = batches.get_batch(batch['batch_id'])['items'][0]
    assert item['added_count'] == 1 and item['skipped_count'] == 0
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 1
        assert c.execute('SELECT status FROM tracking_round').fetchone()[0] == 'manual_removed'
        assert c.execute("SELECT count(*) FROM job_event WHERE job_id=? AND stage='auto_tracking_chunk'", (tracking_job['job_id'],)).fetchone()[0] == 1


def test_tracking_and_receipt_rollback_together_on_mid_commit_crash(ws, monkeypatch):
    _, client = ws; strategy = make_strategy(client)
    batch = start(client, [strategy]); step(); job = tasks.claim_next('fixture')
    def crash(*args): raise SimulatedCrash()
    with monkeypatch.context() as scoped:
        scoped.setattr(batches, '_tracking_receipt', crash)
        with pytest.raises(SimulatedCrash): worker.dispatch(job)
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM job_event WHERE job_id=? AND stage='auto_tracking_chunk'", (job['job_id'],)).fetchone()[0] == 0
    expire(job['job_id']); recovered = tasks.claim_next('fixture'); worker.dispatch(recovered); step()
    assert batches.get_batch(batch['batch_id'])['added_count'] == 1


def test_multi_chunk_resume_keeps_accounted_chunks_and_frozen_baselines(ws, monkeypatch):
    _, client = ws; strategy = make_strategy(client, {'field': 'close', 'op': '>', 'value': 0})
    monkeypatch.setattr(batches, 'TRACKING_CHUNK_SIZE', {'stock':1,'sector':800})
    batch = start(client, [strategy]); step(); job = tasks.claim_next('fixture')
    original = tracking.add_tracking; called = 0
    def crash_after_first_commit(*args, **kwargs):
        nonlocal called
        result = original(*args, **kwargs); called += 1
        if called == 1: raise SimulatedCrash()
        return result
    with monkeypatch.context() as scoped:
        scoped.setattr(tracking, 'add_tracking', crash_after_first_commit)
        with pytest.raises(SimulatedCrash): worker.dispatch(job)
    partial = batches.get_batch(batch['batch_id'])['items'][0]
    assert partial['added_count'] == 1 and partial['tracking_status'] == 'running'
    with core.connect() as c:
        first = dict(c.execute('SELECT * FROM tracking_round').fetchone())
        end_fixture_round(c, first)
    expire(job['job_id']); worker.dispatch(tasks.claim_next('fixture')); step()
    final = batches.get_batch(batch['batch_id'])['items'][0]
    assert final['added_count'] == 2 and final['skipped_count'] == 0 and final['matched_count'] == 2
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 2
        preserved = dict(c.execute('SELECT * FROM tracking_round WHERE round_id=?', (first['round_id'],)).fetchone())
        assert preserved['status'] == 'manual_removed'
        assert preserved['entry_price_e8'] == first['entry_price_e8'] and preserved['entry_basis_json'] == first['entry_basis_json']
        assert c.execute("SELECT count(*) FROM job_event WHERE job_id=? AND stage='auto_tracking_chunk'", (job['job_id'],)).fetchone()[0] == 2


def test_auto_tracking_failure_preserves_runs_and_continues_other_strategies(ws, monkeypatch):
    _, client = ws; first = make_strategy(client); second = make_strategy(client)
    original = tracking.add_tracking
    def reject_first(payload, **kwargs):
        with core.connect() as c: strategy = c.execute('SELECT strategy_id FROM selection_run WHERE run_id=?', (payload['run_id'],)).fetchone()[0]
        if strategy == first['strategy_id']: raise HTTPException(409, 'fixture入池失败')
        return original(payload, **kwargs)
    monkeypatch.setattr(tracking, 'add_tracking', reject_first)
    batch = start(client, [first, second]); drain(5)
    result = batches.get_batch(batch['batch_id'])
    assert result['status'] == 'partial' and result['failed_count'] == 1 and result['succeeded_count'] == 1
    assert result['items'][0]['run_id'] and result['items'][0]['selection_status'] == 'succeeded'
    assert result['items'][0]['tracking_status'] == 'failed' and 'fixture入池失败' in result['items'][0]['error']
    assert result['items'][1]['added_count'] == 1


def legacy_fixture_batch(strategy):
    from app.services.workspace_common import current, freeze_inputs
    with core.connect() as c:
        c.execute('BEGIN IMMEDIATE'); day=current(c); deps,primary=freeze_inputs(c)
        parent=core.new_id('legacy-batch'); child=core.new_id('legacy-selection'); stamp=core.now_ms()
        item={'job_id':child,'sequence':1,'strategy_id':strategy['strategy_id'],'name':strategy['name'],'version':strategy['version']}
        parameters={'operation':batches.ITEM_OPERATION,'batch_job_id':parent,'target_type':'stock','sequence':1,
            'strategy_id':strategy['strategy_id'],'version':strategy['version'],'business_date':day,'input_snapshots':deps,'primary_snapshot_id':primary}
        parent_params={'operation':batches.OPERATION,'target_type':'stock','scope':'selected',
            'request_signature':core.digest({'target_type':'stock','scope':'selected','strategy_ids':[strategy['strategy_id']]}),
            'business_date':day,'input_snapshots':deps,'primary_snapshot_id':primary,'items':[item]}
        for identifier,key,kind,params in [(child,'legacy-child','selection',parameters),(parent,'batch-fixture','source_check',parent_params)]:
            c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,created_at_ms) VALUES(?,?,?,?,?,?,?,'queued',?)",
                (identifier,key,key,kind,day,core.dumps(params),core.digest(params),stamp))
            tasks.append_event(c,identifier,'queued','旧版仅筛选批次')
    return batches.get_batch(parent)


def end_fixture_round(connection, row):
    connection.execute("INSERT INTO tracking_event(event_id,event_key,round_id,event_type,business_date,data_snapshot_id,data_state,outcome,trading_age,evidence_json,recorded_at_ms) VALUES(?,?,?,'manual_remove',?,?,'not_applicable','not_applicable',0,?,?)",
        (core.new_id('event'),row['round_id']+':fixture-end',row['round_id'],row['entry_business_date'],row['entry_snapshot_id'],core.dumps({'ignore_reentry':False}),core.now_ms()))
    connection.execute("UPDATE tracking_round SET status='manual_removed',end_business_date=entry_business_date,end_reason='fixture' WHERE round_id=?",(row['round_id'],))


@pytest.mark.parametrize('completed', [False, True])
def test_old_batch_no_auto_add_marker_is_never_expanded_or_backfilled(ws, completed):
    _, client = ws; strategy = make_strategy(client)
    batch = legacy_fixture_batch(strategy)
    if completed: drain(2)
    replay = start(client, [strategy])
    assert replay['batch_id'] == batch['batch_id'] and replay['auto_add'] is False
    assert 'tracking_job_id' not in replay['items'][0] and replay['items'][0]['tracking_status'] is None
    if not completed: drain(2)
    assert tasks.claim_next('no-auto-backfill') is None
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM job WHERE json_extract(parameters_json,'$.operation')='strategy_batch_tracking'").fetchone()[0] == 0


def test_sector_manual_removed_is_ignored_by_automatic_batch(workspace, monkeypatch):
    monkeypatch.setattr(sector_tracking, 'get_market', lambda: workspace.market)
    cached(workspace); strategy = create()
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    first = start(client, [strategy], target='sector'); drain(3)
    with core.connect() as c: original = dict(c.execute('SELECT * FROM sector_tracking_round').fetchone())
    sector_tracking.remove_tracking(original['round_id'])
    second = start(client, [strategy], target='sector', key='sector-second'); drain(3)
    item = batches.get_batch(second['batch_id'])['items'][0]
    assert item['added_count'] == 0 and item['ignored_count'] == item['skipped_count'] == 1
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM sector_tracking_round').fetchone()[0] == 1
        retained = dict(c.execute('SELECT * FROM sector_tracking_round').fetchone())
        assert retained['status'] == 'manual_removed' and retained['entry_price_e8'] == original['entry_price_e8']


def test_tracking_pause_resume_fences_old_preparation_before_any_insert(ws, monkeypatch):
    _, client = ws; strategy = make_strategy(client)
    batch = start(client, [strategy]); step(); old = tasks.claim_next('same-worker')
    original = tracking.load_entry_highs; stopped = False
    def pause_during_preparation(*args, **kwargs):
        nonlocal stopped
        if not stopped:
            stopped = True
            tasks.change_task(batch['batch_id'], 'pause')
            tasks.change_task(batch['batch_id'], 'resume')
        return original(*args, **kwargs)
    with monkeypatch.context() as scope:
        scope.setattr(tracking, 'load_entry_highs', pause_during_preparation)
        with pytest.raises(tasks.TaskCancelled): worker.dispatch(old)
    assert tasks.task_view(old['job_id'])['status'] == 'queued'
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM tracking_round').fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM job_event WHERE job_id=? AND stage='auto_tracking_chunk'", (old['job_id'],)).fetchone()[0] == 0
    new = tasks.claim_next('same-worker')
    assert old['lease_owner'] != new['lease_owner']
    worker.dispatch(new); step()
    assert batches.get_batch(batch['batch_id'])['added_count'] == 1


def test_internal_steps_cannot_be_independently_retried_or_copied(ws):
    _, client = ws; strategy = make_strategy(client)
    batch = start(client, [strategy]); item = batch['items'][0]
    for identifier in (item['job_id'],item['tracking_job_id']):
        for action in ('pause','resume','cancel','retry'):
            with pytest.raises(HTTPException) as error: tasks.change_task(identifier,action)
            assert error.value.status_code == 422
    with core.connect() as c:
        parent = json.loads(c.execute('SELECT parameters_json FROM job WHERE job_id=?', (batch['batch_id'],)).fetchone()[0])
        assert parent['auto_add'] is True and parent['plan_version'] == batches.PLAN_VERSION
        assert parent['request_signature'] == core.digest({'target_type':'stock','scope':'selected','strategy_ids':[strategy['strategy_id']],'auto_add':True,'plan_version':batches.PLAN_VERSION})
    drain(3)
    assert batches.get_batch(batch['batch_id'])['added_count'] == 1
