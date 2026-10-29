"""Same-day admission binds real local supplement bytes, not only SQL heads.

All input pools and nested CAS objects are synthetic fixtures under pytest tmp.
No source requests, live database, worker process or credentials are involved.
"""
from copy import deepcopy
import json

import pytest

from app import core
from app.services import market_supplement, stock_daily_once, strategies, supplement_storage
from app.services import strategy_batches as batches
from test_workspace import ws, make_strategy
from test_stock_strategy_daily_once import run, state, control_counts
from test_strategy_batches import start, drain
from test_strategy_limits import pool, event


@pytest.fixture
def pools(ws, monkeypatch):
    market, client = ws
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'raw'}, raising=False)
    monkeypatch.setattr(market, 'reference', lambda: {}, raising=False)
    monkeypatch.setattr(market, 'history_for_instruments', lambda symbols, start=None, end=None: {
        symbol: [row for row in market.data[symbol] if start <= row['date'] <= end]
        for symbol in symbols}, raising=False)
    root = core.published_dir() / 'market-supplement'
    cache = market_supplement.SupplementCache(root)
    document = {'version': market_supplement.VERSION, 'snapshot_id': 'raw',
                'job_id': 'fixture-pool-proof', 'target_date': market.entry,
                'quality': 'partial', 'failures': [], 'public_pools': {},
                'annual_pools': {}, 'annual_down_pools': {}}

    def publish(*, symbols=('600001.SH',), nested=False, field='annual_pools'):
        rows = [event(market.entry, symbol, market.data[symbol][70]['close']) for symbol in symbols]
        if nested:
            for row in rows:
                row['fixture_only_padding'] = ['verified-CAS'] * 800
        document[field][market.entry] = pool(market.entry, rows, complete=False, pagination_complete=False)
        cache.publish(document)
        return deepcopy(document)

    return market, client, root, cache, document, publish


def limit_strategy(client):
    return make_strategy(client, {'field': 'limit_up', 'op': '==', 'value': True})


def candidate_states(saved):
    return {item['instrument_id']: item['decision'] for item in saved['items']}


def test_missing_then_published_pool_unlocks_without_sql_head_change_and_keeps_old_replay(pools):
    market, client, _, _, _, publish = pools
    strategy = limit_strategy(client)
    first = run(client, strategy, 'before-pools').json()
    assert first['unknown_count'] == 2
    assert first['universe_definition']['selection_guard']['supplement_input']['state'] == 'absent'
    before = control_counts()
    assert run(client, strategy).json()['run_id'] == first['run_id']
    assert control_counts() == before
    with core.connect() as connection:
        frozen = [tuple(row) for row in connection.execute('SELECT * FROM published_head ORDER BY module')]
    publish()
    assert state(client, strategy)['can_run'] is True
    second = run(client, strategy, 'after-pools').json()
    assert second['run_id'] != first['run_id'] and second['input_sha256'] != first['input_sha256']
    assert candidate_states(second)['600001.SH'] == 'matched'
    assert client.get('/api/v1/strategy-runs/' + first['run_id']).json() == first
    assert run(client, strategy, 'before-pools').json()['run_id'] == first['run_id']
    assert run(client, strategy).json()['run_id'] == second['run_id']
    with core.connect() as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM published_head ORDER BY module')] == frozen


def test_stable_verified_partial_pool_reuses_without_expanding_historical_cas(pools, monkeypatch):
    _, client, _, _, _, publish = pools
    publish(nested=True)
    strategy = limit_strategy(client)
    saved = run(client, strategy).json()
    assert saved['unknown_count'] > 0  # Partial coverage remains unknown, never zero.
    def forbidden(*_args, **_kwargs):
        raise AssertionError('warm proof must not decode CAS payloads or recompute candidates')
    with monkeypatch.context() as guard:
        guard.setattr(supplement_storage, '_object_value', forbidden)
        guard.setattr(strategies, 'evaluate', forbidden)
        guard.setattr(strategies, 'get_market', forbidden)
        same = run(client, strategy)
    assert same.status_code == 200, same.text
    assert same.json()['run_id'] == saved['run_id'] and same.json()['result_reused'] is True


@pytest.mark.parametrize('corruption', ['nested', 'index', 'head'])
def test_forged_head_or_damaged_actual_cas_never_reuses_completed_result(pools, corruption):
    _, client, root, _, _, publish = pools
    publish(nested=True)
    strategy = limit_strategy(client)
    saved = run(client, strategy, 'verified-before-corruption').json()
    head = json.loads((root / 'head.json').read_text())
    index_path = root / head['artifact']
    index = json.loads(index_path.read_text())
    if corruption == 'nested':
        parent = json.loads((root / index['annual_pools'][saved['business_date']]['record_ref']).read_text())
        child = next(iter(parent['__supplement_fields_v1'].values()))
        path = root / child['record_ref']
        content = path.read_bytes()
        path.write_bytes(content.replace(b'verified-CAS', b'corrupt!-CAS', 1))
    elif corruption == 'index':
        index_path.write_text('{}')
    else:
        head['sha256'] = 'a' * 64
        core.atomic_json(root / 'head.json', head)
    assert state(client, strategy)['can_run'] is True
    result = run(client, strategy).json()
    assert result['run_id'] != saved['run_id'] and result['unknown_count'] == 2
    assert result['universe_definition']['selection_guard']['supplement_input'] is None
    assert state(client, strategy)['can_run'] is True
    assert run(client, strategy, 'verified-before-corruption').json()['run_id'] == saved['run_id']


def test_unrelated_fund_or_sector_objects_do_not_invalidate_stock_pool_proof(pools):
    market, client, _, cache, document, publish = pools
    publish()
    strategy = limit_strategy(client)
    saved = run(client, strategy).json()
    document['fund_flows'] = {market.entry: {'value': ['not-a-stock-input'] * 500}}
    document['sector_index_history'] = {market.entry: {'value': ['not-a-stock-input'] * 500}}
    cache.publish(document)
    assert state(client, strategy)['completed_run_id'] == saved['run_id']
    assert run(client, strategy).json()['run_id'] == saved['run_id']


@pytest.mark.parametrize('field', ['annual_pools', 'annual_down_pools', 'public_pools'])
def test_each_consumed_pool_family_changes_admission(pools, field):
    _, client, _, _, _, publish = pools
    publish()
    strategy = make_strategy(client, {'field': 'limit_up_count_1y', 'op': '>', 'value': 1})
    saved = run(client, strategy).json()
    assert state(client, strategy)['completed_run_id'] == saved['run_id']
    publish(symbols=('000001.SZ',), field=field)
    assert state(client, strategy)['can_run'] is True


def test_supplement_change_during_compute_rejects_mixed_result(pools, monkeypatch):
    _, client, _, _, _, publish = pools
    publish()
    strategy = limit_strategy(client)
    saved = run(client, strategy).json()
    client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={
        'definition': {'field': 'prev_limit_up', 'op': '==', 'value': True}})
    original = strategies.evaluate
    changed = False
    def evaluate(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            publish(symbols=('000001.SZ',))
        return result
    monkeypatch.setattr(strategies, 'evaluate', evaluate)
    response = run(client, strategy)
    assert response.status_code == 409 and '补充数据已变化' in response.json()['detail']
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 1
    assert client.get('/api/v1/strategy-runs/' + saved['run_id']).json() == saved


def test_batch_rechecks_new_pool_head_and_retains_original_request_intent(pools):
    _, client, _, _, _, publish = pools
    publish()
    strategy = limit_strategy(client)
    old = run(client, strategy).json()
    assert start(client, [strategy], key='original-read')['receipt_only'] is True
    publish(symbols=('000001.SZ',))
    queued = start(client, [strategy], key='new-evidence')
    assert queued['skipped_completed_count'] == 0 and queued['total_count'] == 1
    drain(3)
    completed = batches.get_batch(queued['batch_id'])
    assert completed['status'] == 'succeeded'
    fresh = client.get('/api/v1/strategy-runs/' + completed['items'][0]['run_id']).json()
    assert candidate_states(fresh)['000001.SZ'] == 'matched'
    assert fresh['input_sha256'] != old['input_sha256']
    publish()
    assert start(client, [strategy], key='new-evidence')['batch_id'] == queued['batch_id']
    assert client.get('/api/v1/strategy-runs/' + old['run_id']).json() == old


def test_new_batch_binds_pool_proof_and_rejects_later_rebase(pools):
    _, client, _, _, _, publish = pools
    publish()
    strategy = limit_strategy(client)
    queued = start(client, [strategy], key='frozen-pools')
    with core.connect() as connection:
        parameters = json.loads(connection.execute('SELECT parameters_json FROM job WHERE job_id=?',
                                                   (queued['items'][0]['job_id'],)).fetchone()[0])
    assert parameters['selection_supplement_input']['state'] == 'verified'
    publish(symbols=('000001.SZ',))
    drain(3)
    completed = batches.get_batch(queued['batch_id'])
    assert completed['status'] == 'failed' and completed['items'][0]['selection_status'] == 'failed'
    assert '补充数据已变化' in completed['items'][0]['error']
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 0


@pytest.mark.parametrize('field', ['limit_up', 'limit_up_1y'])
def test_legacy_run_without_pool_proof_is_not_reused_for_dependent_rule(pools, monkeypatch, field):
    _, client, _, _, _, publish = pools
    publish()
    strategy = make_strategy(client, {'field': field, 'op': '==', 'value': True})
    original = stock_daily_once.evidence
    monkeypatch.setattr(stock_daily_once, 'evidence', lambda context: {
        'contract': 'stock-daily-once-v1', 'topic_revision_signature': context['topic_revision_signature']})
    old = run(client, strategy).json()
    monkeypatch.setattr(stock_daily_once, 'evidence', original)
    assert state(client, strategy)['can_run'] is True
    assert run(client, strategy).json()['run_id'] != old['run_id']


def test_hot_universe_uses_same_pool_dependency_without_changing_technical_only_rules(pools, monkeypatch):
    _, client, _, _, _, publish = pools
    publish()
    pure = make_strategy(client)
    pure_saved = run(client, pure).json()
    strategy = make_strategy(client)
    strategy = client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={
        'hot_universe': {'window_sessions': 7, 'top_n': 10, 'kind': 'all'}}).json()
    monkeypatch.setattr(strategies.hot_universe, 'freeze', lambda config, expected_day: (
        {'complete': False, 'reason': '合成缺口', 'member_ids': []}, {}))
    saved = run(client, strategy).json()
    assert state(client, strategy)['completed_run_id'] == saved['run_id']
    publish(symbols=('000001.SZ',))
    assert state(client, strategy)['can_run'] is True
    assert state(client, pure)['completed_run_id'] == pure_saved['run_id']


def test_one_verified_pool_proof_is_shared_by_batch_admission(pools, monkeypatch):
    _, client, _, _, _, publish = pools
    publish()
    strategies_ = [limit_strategy(client) for _ in range(3)]
    for strategy in strategies_:
        assert run(client, strategy).status_code == 200
    calls = []
    original = stock_daily_once._supplement_input
    def verify(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(stock_daily_once, '_supplement_input', verify)
    assert start(client, strategies_, key='shared-proof')['receipt_only'] is True
    assert calls == [1]


def test_cold_and_final_pool_verification_stay_outside_write_transactions(pools, monkeypatch):
    _, client, _, _, _, publish = pools
    publish(nested=True)
    strategy = limit_strategy(client)
    calls = []
    original = stock_daily_once._supplement_input
    def verify(connection, *args, **kwargs):
        assert connection.in_transaction is False
        calls.append(1)
        return original(connection, *args, **kwargs)
    monkeypatch.setattr(stock_daily_once, '_supplement_input', verify)
    saved = run(client, strategy)
    assert saved.status_code == 200, saved.text
    assert calls == [1, 1]  # Admission preflight and pre-commit verification.
    calls.clear()
    assert run(client, strategy).json()['run_id'] == saved.json()['run_id']
    assert calls == [1]


@pytest.mark.parametrize('stage', ['admission', 'commit'])
def test_file_identity_change_after_outside_proof_rejects_without_mixed_run(pools, monkeypatch, stage):
    _, client, _, _, _, publish = pools
    publish()
    strategy = limit_strategy(client)
    original = stock_daily_once.prepare_inputs
    calls = 0
    def prepare(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == (1 if stage == 'admission' else 2):
            publish(symbols=('000001.SZ',))
        return result
    monkeypatch.setattr(stock_daily_once, 'prepare_inputs', prepare)
    response = run(client, strategy)
    assert response.status_code == 409 and '数据已变化' in str(response.json()['detail'])
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM selection_run').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM selection_candidate').fetchone()[0] == 0


def test_technical_only_admission_never_reads_supplement(pools, monkeypatch):
    _, client, _, _, _, _ = pools
    strategy = make_strategy(client)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('technical-only rules do not consume pool or sector files')
    monkeypatch.setattr(stock_daily_once, '_supplement_input', forbidden)
    saved = run(client, strategy).json()
    assert run(client, strategy).json()['run_id'] == saved['run_id']
    assert state(client, strategy)['completed_run_id'] == saved['run_id']


def test_no_matching_saved_result_does_not_scan_source_in_list_response(pools, monkeypatch):
    _, client, _, _, _, _ = pools
    strategy = limit_strategy(client)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('no saved result needs no reuse-source scan')
    monkeypatch.setattr(stock_daily_once, '_supplement_input', forbidden)
    assert state(client, strategy)['can_run'] is True


def test_oversize_reuse_proof_falls_back_without_accepting_a_fake_proof(pools, monkeypatch):
    _, client, _, _, _, publish = pools
    publish(nested=True)
    strategy = limit_strategy(client)
    with stock_daily_once._proof_lock:
        stock_daily_once._proof_cache.clear()
    monkeypatch.setattr(stock_daily_once, '_MAX_PROOF_BYTES', 1024)
    prepared = stock_daily_once.prepare_inputs([strategy['strategy_id']])
    assert prepared['supplement_input'] is None
    saved = run(client, strategy).json()
    assert saved['matched_count'] == 1  # Evaluation still uses its actual source reader.
    assert state(client, strategy)['can_run'] is True


def test_index_replace_after_verified_read_cannot_bind_old_sha_to_new_identity(tmp_path, monkeypatch):
    path = tmp_path / 'index.json'
    path.write_text('{"fixture":"old"}')
    replacement = tmp_path / 'replacement.json'
    replacement.write_text('{"fixture":"new"}')
    files = stock_daily_once._ProofFiles()
    original = stock_daily_once._identity
    old_identity = original(path)
    calls = 0
    def identity(target):
        nonlocal calls
        result = original(target)
        calls += 1
        if calls == 2:
            replacement.replace(path)
        return result
    monkeypatch.setattr(stock_daily_once, '_identity', identity)
    value, _ = stock_daily_once._small_json(path, 1024, verified_files=files)
    assert calls == 2 and value == {'fixture': 'old'}
    assert files[str(path)] == old_identity
    assert stock_daily_once._files_match(files) is False


def test_noop_pool_strategy_save_retains_completed_state_without_transaction_hash(pools, monkeypatch):
    _, client, _, _, _, publish = pools
    publish()
    strategy = limit_strategy(client)
    saved = run(client, strategy).json()
    original = stock_daily_once._supplement_input
    def verify(connection, *args, **kwargs):
        assert connection.in_transaction is False
        return original(connection, *args, **kwargs)
    monkeypatch.setattr(stock_daily_once, '_supplement_input', verify)
    updated = client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={
        'name': '合成名称调整', 'definition': strategy['definition']})
    assert updated.status_code == 200, updated.text
    assert updated.json()['run_availability']['completed_run_id'] == saved['run_id']
