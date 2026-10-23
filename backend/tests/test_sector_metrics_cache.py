"""Disposable board metrics must never outrank immutable price evidence."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from app.services import sector_charts as charts, sector_strategies as strategies
from test_platform import isolated
from test_sector_strategies import workspace, cached
from test_sector_charts import CODE, DAY


def clear_memory():
    with strategies._SUMMARY_LOCK: strategies._SUMMARIES.clear()


def paths(workspace):
    plan = charts.plan_sector_chart(workspace.market, CODE, DAY)
    scope = strategies._summary_scope(plan, workspace.market.calendar())
    return scope, strategies._summary_paths(scope)


def test_persistent_summary_survives_memory_clear_and_returns_independent_objects(workspace, monkeypatch):
    cached(workspace)
    first = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    scope, (_, content, receipt) = paths(workspace)
    saved = content.read_bytes(); proof = json.loads(receipt.read_text())
    assert sha256(saved).hexdigest() == proof['sha256'] and proof['bytes'] == len(saved)
    assert proof['scope_sha256'] == strategies.digest(scope)
    assert content.stat().st_size <= 256*1024
    clear_memory()
    monkeypatch.setattr(strategies, '_indicators', lambda *a: pytest.fail('persisted summary must not reenrich'))
    second = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    assert second == first
    second['metrics_by_period']['D']['close'] = -1
    assert strategies.metrics_for_sector(workspace.market, CODE, DAY) == first


@pytest.mark.parametrize('part', ['content', 'receipt', 'contract'])
def test_corrupt_derived_content_or_receipt_recomputes_real_inputs(workspace, monkeypatch, part):
    cached(workspace); expected = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    _, (_, content, receipt) = paths(workspace)
    if part == 'content': content.write_bytes(b'{"invented":0}')
    elif part == 'receipt': receipt.write_bytes(b'not-json')
    else:
        proof = json.loads(receipt.read_text()); proof['contract'] = 'old-contract'
        receipt.write_text(json.dumps(proof))
    clear_memory(); original = strategies._indicators; calls = []
    def recompute(*args): calls.append(1); return original(*args)
    monkeypatch.setattr(strategies, '_indicators', recompute)
    assert strategies.metrics_for_sector(workspace.market, CODE, DAY) == expected
    assert len(calls) == 1


def test_source_corruption_cannot_be_hidden_by_persistent_summary(workspace, monkeypatch):
    cached(workspace); data = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    raw = next(r for r in data['cache_refs']['files'] if r['artifact'].startswith('raw/'))
    path = charts._root()/raw['artifact']; old = path.read_bytes()
    path.write_bytes(b' '*len(old)); clear_memory()
    monkeypatch.setattr(strategies, '_indicators', lambda *a: pytest.fail('bad source must not reach indicators'))
    actual = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    assert actual['coverage']['available'] is False
    assert actual['metrics_by_period']['D']['close'] is None


@pytest.mark.parametrize('change', ['algorithm', 'normalization', 'identity', 'generation', 'calendar'])
def test_persistent_scope_changes_are_cache_misses(workspace, monkeypatch, change):
    cached(workspace); first = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    old_scope, (_, old_content, _) = paths(workspace)
    if change == 'algorithm': monkeypatch.setattr(strategies, 'INDICATOR_VERSION', 'changed-indicator')
    elif change == 'normalization': monkeypatch.setattr(charts, 'NORMALIZATION_VERSION', 'changed-normalization')
    elif change == 'identity': workspace.market.ref['concept_catalog'][0]['name'] = '新的明确目录名称'
    elif change == 'generation': workspace.market.state['snapshot_id'] = 'market-new-generation'
    else: workspace.market.days = workspace.market.days[1:]
    new_scope, (_, new_content, _) = paths(workspace)
    assert new_scope != old_scope and new_content != old_content and old_content.is_file()
    plan = charts.plan_sector_chart(workspace.market, CODE, DAY)
    assert strategies._load_summary(new_scope, plan) is None


def test_volume_comparison_contract_cannot_reuse_old_bool_and_new_scope_survives_memory_clear(workspace, monkeypatch):
    # Both finite positive source values pass the real normalizer. Their
    # quotient underflows to zero: old boolean code called it contraction,
    # while the corrected predicate keeps an unverifiable ratio unknown.
    def boundary(payload):
        payload['data']['item'][-2]['volume'] = 1e308
        payload['data']['item'][-1]['volume'] = 1e-308
        return payload
    cached(workspace, transform=boundary)
    adapter = strategies.metrics_from_enriched
    def legacy_adapter(row, previous=None, topics=None):
        value = adapter(row, previous, topics)
        value['volume_expand'] = row['volume_ratio'] > 1 if row.get('volume_ratio') is not None else None
        value['volume_shrink'] = row['volume_ratio'] < 1 if row.get('volume_ratio') is not None else None
        return value
    with monkeypatch.context() as legacy:
        legacy.setattr(strategies, 'SUMMARY_CONTRACT', 'sector-strategy-metrics-complete-first-week-v2')
        legacy.setattr(strategies, 'metrics_from_enriched', legacy_adapter)
        old = strategies.metrics_for_sector(workspace.market, CODE, DAY)
        old_scope, (_, old_content, old_receipt) = paths(workspace)
        old_bytes = (old_content.read_bytes(), old_receipt.read_bytes())
        assert old['metrics_by_period']['D']['volume_ratio'] == 0
        assert old['metrics_by_period']['D']['volume_shrink'] is True
    # Leave the old in-memory entry as well as both old disk files in place.
    # The current contract must miss both, then read the same verified source.
    scope, (_, content, receipt) = paths(workspace)
    assert scope['contract'] == 'sector-strategy-metrics-volume-comparison-v3'
    assert scope != old_scope and content != old_content and not content.exists()
    assert scope['evaluator'] == old_scope['evaluator']
    assert scope['indicators'] == old_scope['indicators']
    assert scope['normalization'] == old_scope['normalization']
    calls = []; indicators = strategies._indicators
    def calculate(*args, **kwargs):
        calls.append(True)
        return indicators(*args, **kwargs)
    monkeypatch.setattr(strategies, '_indicators', calculate)
    corrected = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    assert calls == [True]
    assert corrected['metrics_by_period']['D']['volume_ratio'] == 0
    assert corrected['metrics_by_period']['D']['volume_shrink'] is None
    assert corrected['cache_refs'] == old['cache_refs']
    assert corrected['close_price'] == old['close_price']
    assert (old_content.read_bytes(), old_receipt.read_bytes()) == old_bytes
    proof = json.loads(receipt.read_text())
    assert proof['contract'] == scope['contract']
    assert proof['scope_sha256'] == strategies.digest(scope)
    assert proof['sha256'] == sha256(content.read_bytes()).hexdigest()
    clear_memory()
    monkeypatch.setattr(strategies, '_indicators', lambda *a, **k: pytest.fail('new persisted scope must be reused'))
    assert strategies.metrics_for_sector(workspace.market, CODE, DAY) == corrected
    assert (old_content.read_bytes(), old_receipt.read_bytes()) == old_bytes


def test_work_directory_namespaces_do_not_share_derived_files(workspace, monkeypatch, tmp_path):
    cached(workspace); first = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    _, (_, old_content, _) = paths(workspace)
    monkeypatch.setattr(strategies, 'work_dir', lambda: tmp_path/'different-work')
    _, (_, new_content, _) = paths(workspace)
    assert old_content != new_content and not new_content.exists()
    clear_memory(); calls = []; original = strategies._indicators
    def calculate(*args): calls.append(1); return original(*args)
    monkeypatch.setattr(strategies, '_indicators', calculate)
    assert strategies.metrics_for_sector(workspace.market, CODE, DAY) == first
    assert calls == [1] and new_content.is_file() and old_content.is_file()


def test_derived_cache_path_symlink_does_not_read_or_write_outside(workspace, monkeypatch, tmp_path):
    cached(workspace)
    outside = tmp_path/'outside'; outside.mkdir()
    marker = outside/'keep.json'; marker.write_text('{"keep":true}')
    local_work = tmp_path/'work'; local_work.mkdir()
    (local_work/'sector-strategy-metrics-v1').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(strategies, 'work_dir', lambda: local_work)
    clear_memory()
    data = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    assert data['coverage']['available'] is True
    assert list(outside.iterdir()) == [marker] and marker.read_text() == '{"keep":true}'


def test_oversized_summary_is_not_persisted_but_verified_prices_remain_available(workspace, monkeypatch):
    cached(workspace); monkeypatch.setattr(strategies, 'MAX_PERSISTED_SUMMARY_BYTES', 100)
    value = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    _, (_, content, receipt) = paths(workspace)
    assert value['coverage']['available'] is True
    assert not content.exists() and not receipt.exists()


def test_total_budget_evicts_derived_pairs_without_touching_source(workspace, monkeypatch):
    cached(workspace); cached(workspace, '881101.TI')
    first = strategies.metrics_for_sector(workspace.market, CODE, DAY)
    _, (root, content, receipt) = paths(workspace)
    budget = content.stat().st_size+receipt.stat().st_size+200
    monkeypatch.setattr(strategies, 'MAX_PERSISTED_CACHE_BYTES', budget)
    second = strategies.metrics_for_sector(workspace.market, '881101.TI', DAY)
    files = [p for p in root.rglob('*.json') if p.is_file()]
    assert sum(p.stat().st_size for p in files) <= budget
    assert len(list(root.rglob('*.receipt.json'))) <= 1
    assert all((charts._root()/r['artifact']).is_file() for r in first['cache_refs']['files']+second['cache_refs']['files'])


def test_first_source_week_without_monday_is_not_a_comparable_week(workspace):
    # Exact known market Monday exists, but the provider starts on Tuesday.
    days = ['2026-09-07','2026-09-08','2026-09-09','2026-09-10','2026-09-11',
            '2026-09-14','2026-09-15','2026-09-16','2026-09-17','2026-09-18']
    bars = [{'date': d, 'open': 10., 'high': 12., 'low': 9., 'close': 11.,
             'volume': 10., 'amount': 100.} for d in days[1:]]
    result, _ = strategies._indicators({'bars': bars}, days, days[-1])
    assert result['W']['observed_date'] == days[-1]
    assert result['W']['indicator_warmup_bars'] == 1
    assert result['W']['ma5'] is None
    full, _ = strategies._indicators({'bars': [{**bars[0], 'date': days[0]}, *bars]}, days, days[-1])
    assert full['W']['indicator_warmup_bars'] == 2


def test_prewarm_only_current_codes_reports_unknown_and_honors_guards(workspace):
    cached(workspace); events = []; guards = []
    result = strategies.prewarm_sector_metrics(workspace.market,
        ['881101.TI', CODE], DAY, guard=lambda: guards.append(True),
        progress=lambda done,total,code: events.append((done,total,code)))
    assert result['available_count'] == 1 and result['failed_count'] == 1
    assert result['persistent_expected_count'] == result['persistent_available_count'] == 1
    assert result['persistent_failed_count'] == 0
    assert result['complete'] is False and result['failures'][0]['topic_code'] == '881101.TI'
    assert events == [(1,2,'881101.TI'), (2,2,CODE)] and len(guards) >= 6
    with pytest.raises(ValueError): strategies.prewarm_sector_metrics(workspace.market, ['fake.TI'], DAY)
    with pytest.raises(ValueError): strategies.prewarm_sector_metrics(workspace.market, [CODE], '2026-10-21')


def test_prewarm_stop_guard_propagates_without_new_business(workspace):
    cached(workspace); count = []
    def guard():
        count.append(1)
        if len(count) == 2: raise ValueError('paused')
    with pytest.raises(ValueError, match='paused'):
        strategies.prewarm_sector_metrics(workspace.market, [CODE], DAY, guard=guard)


def test_prewarm_reports_unwritable_persistent_cache_without_losing_verified_prices(workspace, monkeypatch):
    cached(workspace)
    def denied(*args, **kwargs): raise OSError('isolated derived write denied')
    monkeypatch.setattr(strategies, 'atomic_json', denied)
    result = strategies.prewarm_sector_metrics(workspace.market, [CODE], DAY)
    assert result['complete'] is True and result['available_count'] == 1
    assert result['failed_count'] == 0
    assert result['persistent_expected_count'] == result['persistent_failed_count'] == 1
    assert result['persistent_available_count'] == 0
    assert result['persistent_failures'][0]['topic_code'] == CODE
    assert strategies.metrics_for_sector(workspace.market, CODE, DAY)['close_price'] > 0
