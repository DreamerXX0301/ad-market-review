"""Disk-graph capacity and safety at the verified object-loader boundary."""
import tracemalloc
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app import core
from app.services import supplement_retention as retention
from app.services import supplement_retention_graph as graph
from test_supplement_retention import calendar, tree
from app.services.task_service import TaskPaused


def _plan(root, **kwargs):
    days = calendar()
    return graph.build_plan(root, retention._window(days, days[-1]),
                            max_files=kwargs.pop('max_files', 4096),
                            max_bytes=kwargs.pop('max_bytes', 256 * 1024 * 1024),
                            node_loader=kwargs.pop('node_loader', retention._node), **kwargs)


def test_disk_graph_preserves_real_reference_sha_parent_order_and_lazy_mapping(tmp_path):
    root = tmp_path / 'supplement'
    days, old_key, old, raw, recent_key, recent, _ = tree(root)
    plan = _plan(root)
    try:
        selected = [row['object_key'] for row in plan['scope']['files']]
        assert selected.index(old_key) < selected.index(old['artifact']) < selected.index(raw['artifact'])
        assert recent_key in plan['protected_keys']
        assert recent['artifact'] in plan['protected_keys']
        assert plan['nodes'][old_key]['references'] == ((old['artifact'], old['sha256']),)
        assert tuple(plan['nodes']) == tuple(sorted(plan['nodes']))
        assert len(plan['nodes']) == plan['scope']['inventory_files']
        assert core.digest(plan['scope']) == plan['scope_sha256']
        assert plan['scope']['graph_storage'] == 'disposable-sqlite-v1'
        assert plan.graph.connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert plan.graph.connection.execute('PRAGMA recursive_triggers').fetchone()[0] == 1
        assert plan.graph.connection.execute('PRAGMA busy_timeout').fetchone()[0] > 0
        assert plan.graph.connection.execute('PRAGMA cache_size').fetchone()[0] == -8192
        assert plan.graph.connection.execute('PRAGMA temp_store').fetchone()[0] == 1
        path = plan.graph.path
    finally:
        plan.close()
    assert not path.exists()


@pytest.mark.parametrize('damage', ['missing', 'sha', 'symlink', 'unknown_file', 'unknown_dir'])
def test_disk_graph_refuses_damaged_inventory_without_mutating_files(tmp_path, damage):
    root = tmp_path / 'supplement'
    _, _, _, raw, *_ = tree(root)
    if damage == 'missing':
        (root / raw['artifact']).unlink()
    elif damage == 'sha':
        (root / raw['artifact']).write_text('{}')
    elif damage == 'symlink':
        (root / raw['artifact']).unlink()
        (root / raw['artifact']).symlink_to(tmp_path / 'outside')
    elif damage == 'unknown_file':
        (root / 'unknown.json').write_text('{}')
    else:
        (root / 'unknown').mkdir()
    before = {path.relative_to(root).as_posix() for path in root.rglob('*')}
    with pytest.raises((ValueError, OSError)):
        _plan(root)
    assert before == {path.relative_to(root).as_posix() for path in root.rglob('*')}


def test_large_parent_budget_block_propagates_to_descendants(tmp_path):
    root = tmp_path / 'supplement'
    days, old_key, old, raw, *_ = tree(root)
    # Accepted parent must fit before an expired normalized record can leave;
    # a 1-byte budget cannot delete the tiny raw descendant either.
    plan = _plan(root, max_bytes=1)
    try:
        assert plan['scope']['files'] == []
        assert plan['scope']['backlog_files'] > 0
    finally:
        plan.close()


def test_more_than_100000_verified_nodes_use_disk_without_python_inventory(tmp_path, monkeypatch, record_property):
    count = 100_257
    def keys(_root):
        yield 'head.json'
        yield 'indexes/' + 'f' * 64 + '.json'
        for value in range(count):
            yield 'raw/' + f'{value:064x}' + '.json'
    def node_loader(_root, key):
        result = {'sha256': key[-69:-5] if key != 'head.json' else 'a' * 64,
                  'byte_count': 100, 'identity': (1, 1, 100, 1, 1), 'references': (),
                  'target_date': None, 'snapshot_id': None, 'created_at_ms': None}
        if key == 'head.json':
            result['references'] = (('indexes/' + 'f' * 64 + '.json', 'f' * 64),)
        elif key.startswith('indexes/'):
            result.update(target_date=calendar()[-1], snapshot_id='synthetic-generation', created_at_ms=1)
        else:
            number = int(key[4:-5], 16)
            if number % 2 == 0 and number + 1 < count:
                child = f'{number + 1:064x}'
                result['references'] = (('raw/' + child + '.json', child),)
        return result
    monkeypatch.setattr(graph, '_files', keys)
    tracemalloc.start()
    plan = None
    try:
        plan = _plan(tmp_path, node_loader=node_loader)
        _, peak = tracemalloc.get_traced_memory()
        record_property('python_graph_peak_bytes', peak)
        record_property('planning_ms_with_tracemalloc', plan['elapsed_ms'])
        record_property('derived_graph_bytes', plan['scope']['graph_bytes'])
        record_property('verified_nodes', plan['scope']['inventory_files'])
        record_property('selected_files', len(plan['scope']['files']))
        assert plan['scope']['inventory_files'] == count + 2
        assert plan['scope']['eligible_files'] == count
        assert len(plan['scope']['files']) == 4096
        assert plan['scope']['backlog_files'] == count - 4096
        seen = set()
        for row in plan['scope']['files']:
            number = int(row['object_key'][4:-5], 16)
            if number % 2:
                assert number - 1 in seen, 'selected child must follow its selected parent'
            seen.add(number)
        assert not isinstance(plan['nodes'], dict)
        assert not isinstance(plan['protected_keys'], set)
        assert peak < 24 * 1024 * 1024, f'Python graph peak {peak:,} bytes must not scale with all nodes'
        assert plan['scope']['graph_bytes'] < 160 * 1024 * 1024
    finally:
        tracemalloc.stop()
        if plan is not None:
            plan.close()


def test_disposable_graph_disk_budget_failure_preserves_every_source(tmp_path, monkeypatch):
    root = tmp_path / 'supplement'
    tree(root)
    before = {path.relative_to(root).as_posix() for path in root.rglob('*')}
    before_temps = set(Path(tempfile.gettempdir()).glob('supplement-retention-graph-*'))
    monkeypatch.setattr(graph, '_MAX_GRAPH_PAGES', 3)
    with pytest.raises(sqlite3.OperationalError, match='full'):
        _plan(root)
    assert before == {path.relative_to(root).as_posix() for path in root.rglob('*')}
    assert set(Path(tempfile.gettempdir()).glob('supplement-retention-graph-*')) == before_temps


def test_graph_uses_private_persistent_work_directory_and_cleans_on_guard(tmp_path):
    root = tmp_path/'published/market-supplement'; days, *_ = tree(root)
    private = tmp_path/'work/.supplement-retention'
    with retention.plan_retention(root, days, days[-1], graph_directory=private) as plan:
        graph_path = plan.graph.path
        assert graph_path.is_relative_to(private)
        assert private.stat().st_mode & 0o777 == 0o700
        assert graph_path.parent.stat().st_mode & 0o777 == 0o700
        assert plan.graph.connection.execute("SELECT name FROM sqlite_master WHERE name='ready_batch'").fetchone()[0] == 'ready_batch'
        assert plan.graph.connection.execute("SELECT name FROM sqlite_temp_master WHERE name='ready_batch'").fetchone() is None
    assert not graph_path.exists() and not list(private.iterdir())
    def stop(): raise TaskPaused()
    with pytest.raises(TaskPaused): retention.plan_retention(root, days, days[-1], graph_directory=private, guard=stop)
    assert not list(private.iterdir())


@pytest.mark.parametrize('damage', ['inside_source', 'symlink', 'ancestor_symlink', 'mode'])
def test_graph_rejects_unsafe_private_directory_without_source_changes(tmp_path, damage):
    root = tmp_path/'published/market-supplement'; days, *_ = tree(root)
    private = tmp_path/'work/.supplement-retention'
    if damage == 'inside_source': private = root/'derived'
    elif damage == 'symlink':
        private.parent.mkdir(parents=True); (tmp_path/'other').mkdir(); private.symlink_to(tmp_path/'other', target_is_directory=True)
    elif damage == 'ancestor_symlink':
        (tmp_path/'other').mkdir(); private.parent.symlink_to(tmp_path/'other', target_is_directory=True)
    elif damage == 'mode':
        private.mkdir(parents=True); private.chmod(0o755)
    before = {path.relative_to(root).as_posix() for path in root.rglob('*')}
    with pytest.raises(ValueError): retention.plan_retention(root, days, days[-1], graph_directory=private)
    assert before == {path.relative_to(root).as_posix() for path in root.rglob('*')}


def test_scan_guard_is_checked_at_128_node_boundary_and_closes_disk_graph(tmp_path, monkeypatch):
    count = [0]
    private = tmp_path/'work/.supplement-retention'
    def keys(_root):
        for value in range(1000): yield 'raw/'+f'{value:064x}'+'.json'
    def node(_root, key):
        count[0] += 1
        return {'sha256':'a'*64, 'byte_count':10, 'identity':(1,1,10,1,1), 'references':()}
    def guard():
        if count[0] >= 128: raise TaskPaused()
    monkeypatch.setattr(graph, '_files', keys)
    with pytest.raises(TaskPaused):
        _plan(tmp_path/'source', node_loader=node, guard=guard, graph_directory=private)
    assert count[0] == 128 and not list(private.iterdir())
