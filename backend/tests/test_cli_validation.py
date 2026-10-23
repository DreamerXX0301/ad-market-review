"""CLI checks real fixture files and exact purge evidence in temporary roots."""
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import cli, core
from app.analytics import backtest_store as store
from app.analytics.backtest_engine import VERSION
from app.routers.backtests import router as backtests_router
from app.routers.workspace import router as workspace_router
from app.services import workspace_purge as purge, task_service as tasks
from test_backtests import CalendarOnly
from test_workspace import make_strategy
from test_workspace_purge import confirm, execute_one


@pytest.fixture
def publication(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith('SHIXU_'):
            monkeypatch.delenv(key)
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    core.ensure_ready()
    calendar = CalendarOnly().calendar()
    with core.connect() as c:
        c.execute("""INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms)
            VALUES('fixture-source','fixture-source','fixture-source','initialise','{}',?,'succeeded',?)""", (core.digest({}), core.now_ms()))
    inputs = {}
    for module in ('calendar', 'analysis_bars', 'daily_raw'):
        file = core.published_dir() / 'synthetic-source' / (module + '.json')
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({'synthetic_fixture_only': module}))
        inputs[module] = core.publish_snapshot(module=module, job_id='fixture-source', start=calendar[0], end=calendar[-1],
            artifacts=[{'object_key': str(file.relative_to(core.published_dir())), 'sha256': core.hash_file(file),
                        'byte_count': file.stat().st_size, 'row_count': 1}], quality='ready', price_basis='none')
    app = FastAPI(); app.include_router(workspace_router); app.include_router(backtests_router)
    client = TestClient(app); strategy = make_strategy(client)
    monkeypatch.setattr(store, 'context', lambda _: (CalendarOnly(), strategy, inputs, 'synthetic-generation'))
    monkeypatch.setattr(store, 'MarketData', lambda *a, **k: object())
    monkeypatch.setattr(store, 'run_backtest', lambda *a, **k: {'engine_version': VERSION,
        'stats': {'total_return': 1.2, 'trade_count': 0}, 'data_quality': {},
        **{name: [] for name in ('signals', 'orders', 'fills', 'positions', 'trades', 'equity')}})
    report = store.create_backtest({'strategy_id': strategy['strategy_id'], 'start_date': calendar[-20],
                                   'end_date': calendar[-1], 'request_key': 'synthetic-report'})
    store.execute_backtest(report['job_id'])
    assert cli.validate()['ok'] is True
    with core.connect() as c:
        snapshot = dict(c.execute('SELECT * FROM dataset_snapshot WHERE module=\'backtest_result\'').fetchone())
    files = {str(p.relative_to(core.published_dir())): p.read_bytes() for p in core.published_dir().rglob('*') if p.is_file()}

    def clean(*, absent=False, unfinished_job=False):
        path = '/api/v1/backtests/' + report['backtest_id']
        if absent:
            (core.published_dir() / 'backtests' / report['backtest_id'] / 'equity.parquet').unlink()
        assert client.delete(path).status_code == 200
        accepted, _ = confirm(client, path)
        if unfinished_job:
            job = tasks.claim_next('fixture-worker')
            monkeypatch.setattr(tasks, 'finish', lambda *a, **k: None)
            purge.execute(job)
        else:
            execute_one()
        return accepted['purge_id']
    return client, report, snapshot, files, clean


def corrupt_fixture(table, callback):
    """Simulate inconsistent backup metadata; guards are restored in this temp DB."""
    with core.connect() as c:
        triggers = list(c.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND tbl_name=?", (table,)))
        for trigger in triggers:
            c.execute('DROP TRIGGER "' + trigger['name'] + '"')
        callback(c)
        for trigger in triggers:
            c.execute(trigger['sql'])


@pytest.mark.parametrize('absent,unfinished_job', [(False, False), (True, False), (False, True)])
def test_completed_exact_tombstones_validate_even_before_job_finish(publication, absent, unfinished_job):
    _, _, snapshot, _, clean = publication
    pid = clean(absent=absent, unfinished_job=unfinished_job)
    before = {}
    with core.connect() as c:
        for table in ('workspace_purge', 'workspace_purge_file', 'workspace_purge_audit', 'job', 'snapshot_gc'):
            before[table] = [tuple(r) for r in c.execute('SELECT * FROM ' + table)]
    result = cli.validate()
    assert result['ok'] is True and not result['errors']
    assert len(result['authorized_removed']) == 8
    assert {r['purge_id'] for r in result['authorized_removed']} == {pid}
    assert {r['snapshot_id'] for r in result['authorized_removed']} == {snapshot['snapshot_id']}
    if absent:
        assert sum(r['status'] == 'absent' for r in result['authorized_removed']) == 1
    with core.connect() as c:
        assert before == {table: [tuple(r) for r in c.execute('SELECT * FROM ' + table)] for table in before}


@pytest.mark.parametrize('problem', ['gc_pending', 'purge_failed', 'pending_file', 'removed_bytes', 'scope_hash',
                                     'file_sha', 'file_size', 'audit', 'cleanup_job', 'head', 'live_pin'])
def test_incomplete_inconsistent_or_still_referenced_tombstones_fail(publication, problem):
    _, _, snapshot, _, clean = publication
    pid = clean(); sid = snapshot['snapshot_id']
    if problem == 'gc_pending':
        corrupt_fixture('snapshot_gc', lambda c:c.execute("UPDATE snapshot_gc SET state='gc_pending'"))
    elif problem == 'purge_failed':
        with core.connect() as c:c.execute("UPDATE workspace_purge SET status='failed' WHERE purge_id=?", (pid,))
    elif problem == 'pending_file':
        with core.connect() as c:c.execute("UPDATE workspace_purge_file SET status='pending' WHERE purge_id=?", (pid,))
    elif problem == 'removed_bytes':
        with core.connect() as c:c.execute('UPDATE workspace_purge_file SET removed_bytes=removed_bytes+1 WHERE purge_id=?', (pid,))
    elif problem == 'scope_hash':
        corrupt_fixture('workspace_purge', lambda c:c.execute('UPDATE workspace_purge SET scope_sha256=? WHERE purge_id=?', ('0' * 64, pid)))
    elif problem in ('file_sha', 'file_size'):
        sql = 'UPDATE workspace_purge_file SET ' + ('sha256=?' if problem == 'file_sha' else 'byte_count=byte_count+1') + ' WHERE purge_id=?'
        values = ('0' * 64, pid) if problem == 'file_sha' else (pid,)
        corrupt_fixture('workspace_purge_file', lambda c:c.execute(sql, values))
    elif problem == 'audit':
        corrupt_fixture('workspace_purge_audit', lambda c:c.execute("DELETE FROM workspace_purge_audit WHERE purge_id=? AND stage='completed'", (pid,)))
    elif problem == 'cleanup_job':
        corrupt_fixture('job', lambda c:c.execute("UPDATE job SET kind='backtest' WHERE job_id=(SELECT job_id FROM workspace_purge WHERE purge_id=?)", (pid,)))
    elif problem == 'head':
        corrupt_fixture('published_head', lambda c:c.execute("INSERT INTO published_head VALUES('backtest_result',?,?,?)", (snapshot['scope_key'], sid, core.now_ms())))
    else:
        corrupt_fixture('retention_pin', lambda c:c.execute("INSERT INTO retention_pin VALUES('synthetic-live',?,'manual','synthetic-owner',?,NULL)", (sid, core.now_ms())))
    result = cli.validate()
    assert result['ok'] is False and result['authorized_removed'] == [] and result['errors']


@pytest.mark.parametrize('problem', ['bad_hash', 'bad_size', 'directory', 'dangling_symlink'])
def test_tombstones_never_hide_present_bad_paths(publication, problem):
    _, report, _, files, clean = publication
    clean()
    key = 'backtests/' + report['backtest_id'] + '/equity.parquet'
    file = core.published_dir() / key
    if problem == 'directory':file.mkdir()
    elif problem == 'dangling_symlink':file.symlink_to(file.parent / 'synthetic-nonexistent')
    elif problem == 'bad_size':file.write_bytes(files[key] + b'extra')
    else:file.write_bytes(b'X' + files[key][1:])
    result = cli.validate()
    assert not result['ok'] and 'artifact: ' + key in result['errors']


def test_unknown_missing_market_file_is_not_excused_by_other_purge(publication):
    *_, clean = publication; clean()
    (core.published_dir() / 'synthetic-source/analysis_bars.json').unlink()
    result = cli.validate()
    assert not result['ok'] and len(result['authorized_removed']) == 8
    assert 'artifact: synthetic-source/analysis_bars.json' in result['errors']


def test_alias_cannot_reuse_a_safe_files_cached_validation(publication):
    source = core.published_dir() / 'synthetic-source/analysis_bars.json'
    alias = source.parent / 'z-alias.json'; alias.symlink_to(source)
    core.publish_snapshot(module='indicators', job_id='fixture-source', start='2025-01-01', end='2025-01-02',
        artifacts=[{'object_key': str(alias.relative_to(core.published_dir())), 'sha256': core.hash_file(source),
                    'byte_count': source.stat().st_size, 'row_count': 1}], quality='ready')
    result = cli.validate()
    assert not result['ok'] and 'artifact: synthetic-source/z-alias.json' in result['errors']


def test_shared_path_missing_is_not_hidden_by_the_original_reports_tombstone(publication):
    _, report, _, files, clean = publication
    clean()
    key = 'backtests/' + report['backtest_id'] + '/equity.parquet'
    file = core.published_dir() / key; file.write_bytes(files[key])
    core.publish_snapshot(module='indicators', job_id='fixture-source', start='2025-01-01', end='2025-01-02',
        artifacts=[{'object_key': key, 'sha256': core.hash_file(file), 'byte_count': file.stat().st_size, 'row_count': 1}], quality='ready')
    file.unlink()
    result = cli.validate()
    assert not result['ok'] and result['authorized_removed'] == []
    assert 'artifact: ' + key in result['errors']


def test_only_expired_pin_and_exact_cleanup_self_reference_can_be_ignored(publication):
    _, _, snapshot, _, clean = publication
    pid = clean()
    corrupt_fixture('retention_pin', lambda c:c.execute("INSERT INTO retention_pin VALUES('synthetic-expired',?,'manual','synthetic-owner',?,?)",
                                                     (snapshot['snapshot_id'], core.now_ms() - 200, core.now_ms() - 100)))
    with core.connect() as c:
        row = c.execute('SELECT * FROM workspace_purge WHERE purge_id=?', (pid,)).fetchone()
        params = {'operation': 'workspace_purge', 'purge_id': pid, 'scope': json.loads(row['scope_json'])}
        job_id = row['job_id']
    corrupt_fixture('job', lambda c:c.execute('UPDATE job SET parameters_json=?,parameters_hash=? WHERE job_id=?',
                                            (core.dumps(params), core.digest(params), job_id)))
    result = cli.validate()
    assert result['ok'] and len(result['authorized_removed']) == 8
