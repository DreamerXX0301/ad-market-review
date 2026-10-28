"""Daily admission uses frozen stock rules/input facts, never candidate scans."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import core
from app.services import strategies, stock_daily_once
from test_workspace import ws, make_strategy, advance
from test_selection_generation_guard import publish_revision


def run(client, strategy, key=None):
    return client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs',
                       json={'request_key': key} if key else {})


def state(client, strategy):
    return client.get('/api/v1/strategies/' + strategy['strategy_id']).json()['run_availability']


def control_counts():
    with core.connect() as connection:
        return tuple(connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                     for table in ('job', 'selection_run', 'selection_candidate'))


def test_completed_rule_reuses_saved_result_and_keeps_idempotent_replay(ws, monkeypatch):
    market, client = ws
    strategy = make_strategy(client)
    assert state(client, strategy) == {
        'can_run': True, 'state': 'ready', 'business_date': market.entry,
        'completed_run_id': None, 'reason': None,
    }
    first = run(client, strategy, 'first-click')
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved['universe_definition']['selection_guard']['contract'] == stock_daily_once.CONTRACT
    expected = control_counts()
    def no_computation(*args, **kwargs):
        raise AssertionError('duplicate request must stop before selection computation')
    with monkeypatch.context() as guard:
        guard.setattr(strategies, 'evaluate', no_computation)
        guard.setattr(strategies, 'selection_metric_batch', no_computation)
        guard.setattr(strategies, 'market_panel', no_computation)
        guard.setattr(market, 'bars', no_computation)
        guard.setattr(market, 'history_panel', no_computation)
        duplicate = run(client, strategy, 'another-click')
    assert duplicate.status_code == 200, duplicate.text
    reused = duplicate.json()
    assert reused['run_id'] == saved['run_id'] and reused['result_reused'] is True
    assert {key: value for key, value in reused.items() if key not in ('result_reused', 'reuse_reason')} == saved
    assert state(client, strategy)['completed_run_id'] == saved['run_id']
    assert control_counts() == expected
    replay = run(client, strategy, 'first-click')
    assert replay.status_code == 200 and replay.json()['run_id'] == saved['run_id']
    assert control_counts() == expected


@pytest.mark.parametrize('definition,decision', [
    ({'field': 'close', 'op': '>', 'value': 9999}, 'unmatched'),
    ({'field': 'ma250', 'op': '>', 'value': 0}, 'unknown'),
])
def test_completed_zero_match_and_unknown_results_also_reuse(ws, definition, decision):
    _, client = ws
    strategy = make_strategy(client, definition)
    first = run(client, strategy)
    assert first.status_code == 200, first.text
    assert first.json()['matched_count'] == 0
    assert first.json()[decision + '_count'] == 2
    assert state(client, strategy)['state'] == 'completed'
    before = control_counts()
    duplicate = run(client, strategy)
    assert duplicate.status_code == 200 and duplicate.json()['run_id'] == first.json()['run_id']
    assert control_counts() == before


def test_noop_saves_names_and_tracking_policy_do_not_unlock_but_parameters_do(ws):
    _, client = ws
    strategy = make_strategy(client)
    saved = run(client, strategy).json()
    for payload in (
        {'name': '只改名称'},
        {'policy': {'duration_sessions': 60}},
        {'definition': {'field': 'volume_ratio', 'op': '>=', 'value': 3.0},
         'period': 'D', 'price_basis': 'none', 'hot_universe': None,
         'trade_rules': {'entry': None, 'exit': None}},
    ):
        changed = client.patch('/api/v1/strategies/' + strategy['strategy_id'], json=payload)
        assert changed.status_code == 200, changed.text
        assert changed.json()['run_availability']['state'] == 'completed'
        duplicate = run(client, strategy)
        assert duplicate.status_code == 200 and duplicate.json()['run_id'] == saved['run_id']
    changed = client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={
        'definition': {'field': 'volume_ratio', 'op': '>=', 'value': 2}}).json()
    assert changed['run_availability']['can_run'] is True
    next_run = run(client, strategy)
    assert next_run.status_code == 200 and next_run.json()['run_id'] != saved['run_id']
    assert state(client, strategy)['state'] == 'completed'
    # Reverting to an earlier successful definition does not create a loophole.
    reverted = client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={
        'definition': {'field': 'volume_ratio', 'op': '>=', 'value': 3}}).json()
    assert reverted['run_availability']['completed_run_id'] == saved['run_id']


@pytest.mark.parametrize('field,value', [
    ('period', 'W'), ('price_basis', 'forward'),
    ('hot_universe', {'window_sessions': 7, 'top_n': 10, 'kind': 'all'}),
    ('trade_rules', {'entry': {'field': 'close', 'op': '>', 'value': 0}, 'exit': None}),
])
def test_actual_execution_parameter_changes_unlock(ws, field, value):
    _, client = ws
    strategy = make_strategy(client)
    assert run(client, strategy).status_code == 200
    response = client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={field: value})
    assert response.status_code == 200, response.text
    assert response.json()['run_availability']['can_run'] is True


def test_new_business_day_and_published_input_revision_unlock(ws):
    market, client = ws
    strategy = make_strategy(client)
    assert run(client, strategy).status_code == 200
    publish_revision(market, 'analysis_bars', 'daily-once-revision')
    assert state(client, strategy)['can_run'] is True
    assert run(client, strategy).status_code == 200
    assert state(client, strategy)['state'] == 'completed'
    advance(market, 71)
    assert state(client, strategy)['business_date'] == market.days[71]
    assert state(client, strategy)['can_run'] is True


@pytest.mark.parametrize('legacy', [False, True])
def test_local_topic_revision_unlocks_new_and_legacy_runs(ws, monkeypatch, legacy):
    market, client = ws
    strategy = make_strategy(client)
    if legacy:
        monkeypatch.setattr(stock_daily_once, 'evidence', lambda context: {})
    saved = run(client, strategy).json()
    assert state(client, strategy)['state'] == 'completed'
    stamp = saved['created_at_ms'] + 1
    with core.connect() as connection:
        connection.execute("INSERT INTO topic(topic_id,origin,created_at_ms) VALUES('manual-change','manual',?)", (stamp,))
        connection.execute("""INSERT INTO topic_revision(topic_id,revision,name,description,aliases_json,
                              archived,effective_business_date,recorded_at_ms)
                              VALUES('manual-change',1,'测试人工题材','','[]',0,?,?)""", (market.entry, stamp))
    assert state(client, strategy)['can_run'] is True
    assert run(client, strategy).status_code == 200


def test_failed_attempt_can_retry_and_later_success_locks(ws, monkeypatch):
    _, client = ws
    strategy = make_strategy(client)
    original = strategies.evaluate
    def fail_once(*args, **kwargs):
        raise RuntimeError('fixture evaluation failure')
    monkeypatch.setattr(strategies, 'evaluate', fail_once)
    with pytest.raises(RuntimeError, match='fixture evaluation failure'):
        run(client, strategy)
    assert state(client, strategy)['can_run'] is True
    monkeypatch.setattr(strategies, 'evaluate', original)
    assert run(client, strategy).status_code == 200
    assert state(client, strategy)['state'] == 'completed'


def test_cancelled_attempt_without_successful_run_does_not_lock(ws, monkeypatch):
    _, client = ws
    strategy = make_strategy(client)
    original = strategies.evaluate
    def cancelled(*args, **kwargs):
        with core.connect() as connection:
            connection.execute("UPDATE job SET status='cancelled',finished_at_ms=? WHERE kind='selection' AND status='running'", (core.now_ms(),))
        raise RuntimeError('fixture cancelled')
    monkeypatch.setattr(strategies, 'evaluate', cancelled)
    with pytest.raises(RuntimeError, match='fixture cancelled'):
        run(client, strategy)
    assert state(client, strategy)['can_run'] is True
    monkeypatch.setattr(strategies, 'evaluate', original)
    assert run(client, strategy).status_code == 200


def test_changed_evaluator_can_run_without_editing_old_results(ws, monkeypatch):
    _, client = ws
    strategy = make_strategy(client)
    first = run(client, strategy).json()
    monkeypatch.setattr(strategies, 'ENGINE_VERSION', 'fixture-new-selection-engine')
    assert state(client, strategy)['can_run'] is True
    second = run(client, strategy)
    assert second.status_code == 200, second.text
    assert second.json()['strategy_version'] > first['strategy_version']
    assert state(client, strategy)['state'] == 'completed'
    assert client.get('/api/v1/strategy-runs/' + first['run_id']).json() == first


def test_list_admission_is_one_sqlite_context_without_market_access(ws, monkeypatch):
    _, client = ws
    one = make_strategy(client)
    make_strategy(client, {'field': 'close', 'op': '>', 'value': 0})
    assert run(client, one).status_code == 200
    calls = []
    original = stock_daily_once.read_context
    def read(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(stock_daily_once, 'read_context', read)
    monkeypatch.setattr(strategies, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('no market access')))
    response = client.get('/api/v1/strategies')
    assert response.status_code == 200, response.text
    assert len(calls) == 1 and len(calls[0]['strategy_ids']) == 2
    assert {row['run_availability']['state'] for row in response.json()['items']} == {'ready', 'completed'}


def test_parallel_clicks_admit_only_one_computation(ws, monkeypatch):
    _, client = ws
    strategy = make_strategy(client)
    entered = threading.Event()
    release = threading.Event()
    original = strategies.evaluate
    def evaluate(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)
    monkeypatch.setattr(strategies, 'evaluate', evaluate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, client, strategy, 'parallel-first')
        try:
            assert entered.wait(timeout=5)
            second = pool.submit(run, client, strategy, 'parallel-second').result(timeout=5)
            assert second.status_code == 409
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
    assert state(client, strategy)['state'] == 'completed'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM job WHERE kind='selection'").fetchone()[0] == 1


def test_topic_change_during_execution_cannot_seal_mixed_input(ws, monkeypatch):
    market, client = ws
    strategy = make_strategy(client)
    original = strategies.evaluate
    changed = False
    def evaluate(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            with core.connect() as connection:
                stamp = core.now_ms()
                connection.execute("INSERT INTO topic(topic_id,origin,created_at_ms) VALUES('mid-run-topic','manual',?)", (stamp,))
                connection.execute("""INSERT INTO topic_revision(topic_id,revision,name,archived,effective_business_date,
                                      recorded_at_ms) VALUES('mid-run-topic',1,'运行期间调整',0,?,?)""", (market.entry, stamp))
        return original(*args, **kwargs)
    monkeypatch.setattr(strategies, 'evaluate', evaluate)
    response = run(client, strategy)
    assert response.status_code == 409 and '本地题材或成员已变化' in response.json()['detail']
    assert state(client, strategy)['can_run'] is True
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 0
