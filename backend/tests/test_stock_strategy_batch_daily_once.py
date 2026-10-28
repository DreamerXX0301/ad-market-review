"""Completed daily stock selections are omitted before a durable batch is queued."""
from app import core
from app.services import strategies, strategy_batches as batches, task_service as tasks
from test_workspace import ws, make_strategy, advance
from test_strategy_batches import start, drain


def job_count():
    with core.connect() as connection:
        return connection.execute('SELECT count(*) FROM job').fetchone()[0]


def selected_run(client, strategy):
    response = client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs', json={})
    assert response.status_code == 200, response.text
    return response.json()


def raw_start(client, ids, key):
    return client.post('/api/v1/strategy-batches', json={
        'target_type': 'stock', 'scope': 'selected', 'strategy_ids': ids,
        'request_key': key,
    })


def test_mixed_batch_queues_only_unrun_and_replay_keeps_original_skip_receipt(ws):
    market, client = ws
    done = make_strategy(client)
    pending = make_strategy(client, {'field': 'close', 'op': '>', 'value': 0})
    previous = selected_run(client, done)
    before = job_count()

    batch = start(client, [done, pending], key='mixed-daily-once')
    assert batch['requested_count'] == 2
    assert batch['total_count'] == 1
    assert batch['skipped_completed_count'] == 1
    assert batch['skipped_completed'] == [{
        'strategy_id': done['strategy_id'], 'name': done['name'],
        'run_id': previous['run_id'], 'business_date': market.entry,
        'version': previous['strategy_version'],
    }]
    assert [item['strategy_id'] for item in batch['items']] == [pending['strategy_id']]
    assert batch['items'][0]['sequence'] == 1
    assert job_count() == before + 3

    # The original request must retain its original frozen intent even if a
    # previously omitted strategy has become eligible since submission.
    changed = client.patch('/api/v1/strategies/'+done['strategy_id'], json={
        'definition': {'field': 'volume_ratio', 'op': '>=', 'value': 4},
    })
    assert changed.status_code == 200
    replay = start(client, [done, pending], key='mixed-daily-once')
    assert replay['batch_id'] == batch['batch_id']
    assert replay['skipped_completed'] == batch['skipped_completed']
    assert replay['items'] == batch['items']
    assert job_count() == before + 3

    drain(3)
    completed = batches.get_batch(batch['batch_id'])
    assert completed['status'] == 'succeeded'
    assert completed['completed_count'] == 1
    assert completed['skipped_completed'] == batch['skipped_completed']
    assert client.get('/api/v1/strategy-batches?target_type=stock').json()['items'][0]['skipped_completed_count'] == 1
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM selection_run WHERE strategy_id=?', (done['strategy_id'],)).fetchone()[0] == 1


def test_all_completed_read_only_receipt_without_creating_any_job_even_with_new_request_keys(ws, monkeypatch):
    market, client = ws
    strategy = make_strategy(client)
    run = selected_run(client, strategy)
    before = job_count()
    def should_not_compute():
        raise AssertionError('saved results must not open market or compute')
    monkeypatch.setattr(strategies, 'get_market', should_not_compute)
    receipts = []
    for key in ('done-first-click', 'done-repeated-click'):
        response = raw_start(client, [strategy['strategy_id']], key)
        assert response.status_code == 200, response.text
        detail = response.json()
        receipts.append(detail)
        assert detail['business_date'] == market.entry
        assert detail['skipped_completed_count'] == 1
        assert detail['skipped_completed'][0]['run_id'] == run['run_id']
        assert detail['receipt_only'] is True and detail['result_reused'] is True
        assert detail['job_id'] is None and detail['auto_add'] is False
        assert detail['completed_steps'] == detail['total_steps'] == 0
        assert detail['mode_label'] == '今日已保存结果，未自动入池'
        assert len(detail['items']) == 1
        item = detail['items'][0]
        assert item['run_id'] == run['run_id'] and item['result_reused'] is True
        assert item['selection_status'] == 'no_change' and item['tracking_status'] is None
        assert item['job_id'] is item['tracking_job_id'] is None
        assert job_count() == before
    assert receipts[0]['batch_id'] == receipts[1]['batch_id']
    assert receipts[0]['batch_id'].startswith('saved-results-')
    assert tasks.claim_next('no-new-work') is None
    assert client.get('/api/v1/strategy-batches?target_type=stock').json()['total'] == 0
    assert client.get('/api/v1/strategy-runs/'+run['run_id']).json()['run_id'] == run['run_id']
    assert client.get('/api/v1/tracking-rounds').json()['total'] == 0


def test_noop_rule_save_does_not_unlock_but_parameter_change_and_new_day_do(ws):
    market, client = ws
    strategy = make_strategy(client)
    selected_run(client, strategy)
    unchanged = client.patch('/api/v1/strategies/'+strategy['strategy_id'], json={
        'definition': strategy['definition'], 'name': '只修改名称',
    })
    assert unchanged.status_code == 200
    assert unchanged.json()['version'] > strategy['version']
    unchanged_receipt = raw_start(client, [strategy['strategy_id']], 'no-op-save')
    assert unchanged_receipt.status_code == 200 and unchanged_receipt.json()['receipt_only'] is True

    changed = client.patch('/api/v1/strategies/'+strategy['strategy_id'], json={
        'definition': {'field': 'volume_ratio', 'op': '>=', 'value': 4},
    })
    assert changed.status_code == 200
    batch = start(client, [changed.json()], key='changed-rule')
    assert batch['total_count'] == 1 and batch['skipped_completed_count'] == 0
    drain(3)
    assert batches.get_batch(batch['batch_id'])['status'] == 'succeeded'
    repeat = raw_start(client, [strategy['strategy_id']], 'changed-done')
    assert repeat.status_code == 200 and repeat.json()['receipt_only'] is True

    advance(market, 71)
    next_day = start(client, [changed.json()], key='next-business-day')
    assert next_day['total_count'] == 1
    assert next_day['business_date'] == market.days[71]


def test_cancelled_and_failed_without_saved_success_remain_retryable(ws, monkeypatch):
    market, client = ws
    strategy = make_strategy(client)
    cancelled = start(client, [strategy], key='cancel-before-selection')
    tasks.change_task(cancelled['batch_id'], 'cancel')
    retry = start(client, [strategy], key='after-cancel')
    assert retry['total_count'] == 1 and not retry['skipped_completed']

    def unavailable_market():
        raise RuntimeError('fixture computation failed')

    monkeypatch.setattr(strategies, 'get_market', unavailable_market)
    drain(3)
    assert batches.get_batch(retry['batch_id'])['status'] == 'failed'
    monkeypatch.setattr(strategies, 'get_market', lambda: market)
    final = start(client, [strategy], key='after-failure')
    assert final['total_count'] == 1 and final['skipped_completed_count'] == 0


def test_read_only_all_completed_receipt_does_not_conflict_with_another_running_selection(ws):
    _, client = ws
    done = make_strategy(client)
    pending = make_strategy(client)
    previous = selected_run(client, done)
    active = start(client, [pending], key='another-batch')
    claimed = tasks.claim_next('already-running')
    assert claimed['job_id'] == active['items'][0]['job_id']
    before = job_count()
    response = raw_start(client, [done['strategy_id']], 'read-while-another-runs')
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['receipt_only'] is True and receipt['items'][0]['run_id'] == previous['run_id']
    assert job_count() == before
    assert tasks.task_view(claimed['job_id'])['status'] == 'running'


def test_full_completed_all_scope_receipt_preserves_each_original_run_and_versions(ws):
    _, client = ws
    one = make_strategy(client)
    two = make_strategy(client)
    first = selected_run(client, one)
    second = selected_run(client, two)
    client.patch('/api/v1/strategies/'+one['strategy_id'], json={'definition': one['definition']})
    before = job_count()
    response = client.post('/api/v1/strategy-batches', json={
        'target_type': 'stock', 'scope': 'all', 'request_key': 'all-saved-results',
    })
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['requested_count'] == receipt['total_count'] == receipt['completed_count'] == 2
    assert receipt['receipt_only'] is True and receipt['auto_add'] is False
    originals = {first['run_id']: first['strategy_version'], second['run_id']: second['strategy_version']}
    assert {item['run_id']: item['version'] for item in receipt['items']} == originals
    assert {item['run_id']: item['version'] for item in receipt['skipped_completed']} == originals
    assert job_count() == before


def test_previously_frozen_selection_still_finishes_its_own_job_if_a_saved_run_appears(ws):
    _, client = ws
    strategy = make_strategy(client)
    batch = start(client, [strategy], key='frozen-before-result')
    # An already-frozen authorization must not turn into a dangling running
    # child merely because an independent saved result appeared meanwhile.
    previous = selected_run(client, strategy)
    drain(3)
    finished = batches.get_batch(batch['batch_id'])
    assert finished['status'] == 'succeeded'
    item = finished['items'][0]
    assert item['selection_status'] == item['tracking_status'] == 'succeeded'
    assert item['run_id'] != previous['run_id']
    assert tasks.task_view(item['job_id'])['status'] == 'succeeded'
    assert tasks.claim_next('no-hung-child') is None
