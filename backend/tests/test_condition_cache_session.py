"""Task-local cache I/O batching never changes business/source semantics."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from hashlib import sha256
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from app.services import condition_basis_cache as cache


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('SHIXU_WORK_DIR', str(tmp_path / 'work'))
    cache.clear_cache()
    yield tmp_path / 'work' / cache._DIRECTORY / cache._FILENAME
    cache.clear_cache()


def record(value=b'encoded', size=None):
    return value, sha256(value).hexdigest(), len(value) if size is None else size


def database_rows(path):
    with sqlite3.connect(path) as connection:
        values = dict(connection.execute('SELECT key_sha256,payload FROM seed').fetchall())
        quota = connection.execute('SELECT bytes FROM quota').fetchone()[0]
        actual = connection.execute('SELECT coalesce(sum(compressed_bytes),0) FROM seed').fetchone()[0]
        assert quota == actual
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        return values


def trace_connections(monkeypatch):
    original = cache._disk_connection
    opened, statements = [], []

    def traced(create, identity_out=None):
        connection = original(create, identity_out)
        if connection is not None:
            opened.append(connection)
            connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(cache, '_disk_connection', traced)
    return opened, statements


def test_single_connection_short_transactions_pending_reads_and_64_record_limit(store, monkeypatch):
    opened, statements = trace_connections(monkeypatch)
    with cache.disk_session() as session:
        for index in range(65):
            value = record(str(index).encode())
            cache._put_disk(str(index), value)
            assert cache._get_disk(str(index)) == value
            assert not session.connection.in_transaction
            assert len(session.pending) < 64
            assert session.pending_bytes <= 16 * 1024 * 1024
        assert len(opened) == 1
        assert len(session.pending) == 1
        assert len(database_rows(store)) == 64
        assert session.connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert session.connection.execute('PRAGMA recursive_triggers').fetchone()[0] == 1
        assert session.connection.execute('PRAGMA busy_timeout').fetchone()[0] == 100
    assert len(database_rows(store)) == 65
    assert sum(value == 'BEGIN IMMEDIATE' for value in statements) == 2
    assert sum(value == 'COMMIT' for value in statements) == 2
    assert session.connection is None and session.disabled
    assert cache.SESSION_MAX_RECORDS == 64 and cache.SESSION_MAX_BYTES == 16 * 1024 * 1024


def test_byte_limit_flushes_before_exceeding_bound_and_oversized_record_is_not_buffered(store, monkeypatch):
    monkeypatch.setattr(cache, 'SESSION_MAX_BYTES', 100)
    opened, statements = trace_connections(monkeypatch)
    with cache.disk_session() as session:
        cache._put_disk('first', record(b'a' * 60))
        cache._put_disk('second', record(b'b' * 60))
        assert session.pending_bytes == 60
        cache._put_disk('oversized', record(b'c' * 101))
        assert session.pending_bytes == 0 and not session.pending
        assert not session.connection.in_transaction
    assert len(opened) == 1 and len(database_rows(store)) == 3
    assert sum(value == 'COMMIT' for value in statements) == 3


def test_negative_lookup_is_bounded_scoped_and_store_removes_it(store, monkeypatch):
    monkeypatch.setattr(cache, 'SESSION_MAX_MISSES', 2)
    _, statements = trace_connections(monkeypatch)
    with cache.disk_session() as session:
        assert cache._get_disk('absent') is None
        assert cache._get_disk('absent') is None
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(cache._put_disk, 'absent', record(b'external')).result()
        # Concurrent immutable admission may be reused on the next task; a
        # negative result never escapes this task or becomes business evidence.
        assert cache._get_disk('absent') is None
        cache._put_disk('absent', record(b'own-new'))
        assert cache._get_disk('absent') == record(b'own-new')
        for identity in ('one', 'two', 'three'):
            assert cache._get_disk(identity) is None
        assert list(session.misses) == ['two', 'three']
    probes = [value for value in statements if value.startswith('SELECT payload') and "'absent'" in value]
    assert len(probes) == 1
    with cache.disk_session():
        assert cache._get_disk('absent') == record(b'own-new')


def test_disk_hit_touches_are_batched_until_scope_exit(store):
    cache._put_disk('one', record()); cache._put_disk('two', record())
    with sqlite3.connect(store) as connection:
        connection.execute('UPDATE seed SET touched_ns=1')
    with cache.disk_session() as session:
        assert cache._get_disk('one') == record()
        assert cache._get_disk('two') == record()
        with sqlite3.connect(store) as connection:
            assert connection.execute('SELECT distinct touched_ns FROM seed').fetchall() == [(1,)]
        assert len(session.touches) == 2 and not session.connection.in_transaction
    with sqlite3.connect(store) as connection:
        assert all(value[0] > 1 for value in connection.execute('SELECT touched_ns FROM seed'))


def test_nested_decorator_reuses_scope_and_plain_helpers_persist_immediately(store, monkeypatch):
    opened, _ = trace_connections(monkeypatch)

    @cache.disk_session()
    def inner():
        cache._put_disk('inner', record())
        return 42

    with cache.disk_session() as outer:
        assert inner() == 42
        assert cache._current_disk_session() is outer
        assert database_rows(store) == {}
    assert len(opened) == 1 and 'inner' in database_rows(store)
    cache._put_disk('plain', record())
    assert len(opened) == 2 and 'plain' in database_rows(store)


def test_copied_context_cannot_share_sqlite_connection_with_another_thread(store):
    with cache.disk_session() as outer:
        cache._put_disk('outer', record())

        def child():
            assert cache._current_disk_session() is None
            assert cache._get_disk('outer') is None
            with cache.disk_session() as local:
                assert local is not outer
                cache._put_disk('child', record())

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(copy_context().run, child).result()
        assert cache._get_disk('outer') == record()
    assert set(database_rows(store)) == {'outer', 'child'}


@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt])
def test_exception_or_cancellation_drops_unflushed_cache_and_closes_handle(store, failure):
    with pytest.raises(failure):
        with cache.disk_session() as session:
            cache._put_disk('discarded', record())
            raise failure('business error/cancellation must be preserved')
    assert session.connection is None and session.disabled
    assert database_rows(store) == {}
    assert cache._current_disk_session() is None


def test_disk_clear_invalidates_pending_buffers_without_republication(store):
    with cache.disk_session() as session:
        cache._put_disk('before-clear', record())
        cache.clear_cache(disk=True)
        assert cache._get_disk('before-clear') is None
        assert session.disabled
    assert database_rows(store) == {}
    with cache.disk_session():
        cache._put_disk('after-clear', record())
    assert set(database_rows(store)) == {'after-clear'}


@pytest.mark.parametrize('replacement', ['database', 'directory', 'work-root'])
def test_file_or_namespace_replacement_disables_scope_without_writing_either_inode(store, replacement):
    cache._put_disk('keeper', record())
    with cache.disk_session() as session:
        cache._put_disk('pending', record())
        if replacement == 'database':
            retained = store.with_name('retained.sqlite3')
            store.rename(retained)
            shutil.copy2(retained, store)
        elif replacement == 'directory':
            retained_dir = store.parent.with_name('retained-directory')
            store.parent.rename(retained_dir)
            store.parent.mkdir(mode=0o700)
            retained = retained_dir / store.name
            shutil.copy2(retained, store)
        else:
            original_work = store.parent.parent
            retained_root = original_work.with_name('retained-work')
            original_work.rename(retained_root)
            store.parent.mkdir(parents=True, mode=0o700)
            retained = retained_root / store.parent.name / store.name
            shutil.copy2(retained, store)
        assert cache._get_disk('pending') is None
        assert session.disabled
        cache._put_disk('must-not-write', record())
    assert set(database_rows(store)) == {'keeper'}
    assert set(database_rows(retained)) == {'keeper'}


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
def test_sidecar_replacement_is_checked_before_each_operation(store, tmp_path, suffix):
    sentinel = tmp_path / 'outside'; sentinel.write_bytes(b'unchanged')
    with cache.disk_session() as session:
        cache._put_disk('pending', record())
        sidecar = Path(str(store) + suffix)
        sidecar.symlink_to(sentinel)
        cache._put_disk('blocked', record())
        assert session.disabled
    assert sentinel.read_bytes() == b'unchanged'
    sidecar.unlink()
    assert database_rows(store) == {}


def test_batch_insert_failure_rolls_back_all_records_and_quota(store):
    cache._put_disk('keeper', record())
    with sqlite3.connect(store) as connection:
        connection.execute("CREATE TRIGGER reject_bad BEFORE INSERT ON seed WHEN NEW.key_sha256='bad' BEGIN SELECT RAISE(ABORT,'fixture rejection'); END")
    with cache.disk_session():
        cache._put_disk('good', record(b'good'))
        cache._put_disk('bad', record(b'bad'))
    assert set(database_rows(store)) == {'keeper'}
    assert cache.cache_stats()['disk_errors'] == 1


@pytest.mark.parametrize('location', ['previous', 'oldest', 'quota'])
@pytest.mark.parametrize('corrupted', ['broken', -1, 1.5, b'broken'])
def test_non_strict_sqlite_size_corruption_cannot_replace_business_success(store, monkeypatch, location, corrupted):
    if location == 'oldest':
        monkeypatch.setattr(cache, 'MAX_DISK_BYTES', 256 * 1024)
        for index in range(4):
            cache._put_disk(str(index), record(bytes([index]) * 40000))
        target, replacement = 'new', record(b'n' * 100000)
        statement = "UPDATE seed SET compressed_bytes=? WHERE key_sha256='0'"
    else:
        cache._put_disk('keeper', record())
        target, replacement = 'keeper', record(b'replacement')
        statement = ('UPDATE quota SET bytes=?' if location == 'quota' else
                     "UPDATE seed SET compressed_bytes=? WHERE key_sha256='keeper'")
    with sqlite3.connect(store) as connection:
        connection.execute(statement, (corrupted,))
        before = (connection.execute('SELECT * FROM seed ORDER BY key_sha256').fetchall(),
                  connection.execute('SELECT * FROM quota').fetchall())

    @cache.disk_session()
    def business_result():
        cache._put_disk(target, replacement)
        return {'run': 'successful', 'result': 42}

    assert business_result() == {'run': 'successful', 'result': 42}
    with sqlite3.connect(store) as connection:
        assert (connection.execute('SELECT * FROM seed ORDER BY key_sha256').fetchall(),
                connection.execute('SELECT * FROM quota').fetchall()) == before
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert cache.cache_stats()['disk_errors'] == 1


@pytest.mark.parametrize('bad_record', [
    None, (), (b'encoded',), (None, 'checksum', 1), (b'encoded', None, 1),
    (b'encoded', 'checksum', True), (b'encoded', 'checksum', -1),
    (b'encoded', 'checksum', 1.5), (b'encoded', 'checksum', 'broken'),
])
def test_malformed_record_is_optional_failure_inside_session(store, bad_record):
    with cache.disk_session() as session:
        cache._put_disk('malformed', bad_record)
        assert session.disabled
    assert cache.cache_stats()['disk_errors'] == 1
    assert not store.exists()


def test_trigger_corrupted_metadata_is_optional_failure_on_next_batch(store):
    cache._put_disk('keeper', record())
    with sqlite3.connect(store) as connection:
        connection.execute("CREATE TRIGGER damage_count AFTER INSERT ON seed WHEN NEW.key_sha256='damaged' BEGIN UPDATE seed SET compressed_bytes='broken' WHERE key_sha256=NEW.key_sha256; END")
    with cache.disk_session():
        cache._put_disk('damaged', record())
    with cache.disk_session():
        cache._put_disk('damaged', record(b'replacement'))
    with sqlite3.connect(store) as connection:
        assert connection.execute("SELECT payload,compressed_bytes FROM seed WHERE key_sha256='damaged'").fetchone() == (record()[0], 'broken')
        assert connection.execute("SELECT payload FROM seed WHERE key_sha256='keeper'").fetchone()[0] == record()[0]
    assert cache.cache_stats()['disk_errors'] == 1


@pytest.mark.parametrize('location', ['previous', 'oldest'])
def test_inconsistent_sizes_cannot_underflow_quota_or_replace_success(store, monkeypatch, location):
    monkeypatch.setattr(cache, 'MAX_DISK_BYTES', 256 * 1024)
    cache._put_disk('keeper', record(b'k' * 100))
    with sqlite3.connect(store) as connection:
        connection.execute('UPDATE quota SET bytes=1')
    with cache.disk_session():
        cache._put_disk('keeper' if location == 'previous' else 'new',
                        record(b'n' * (cache.MAX_DISK_BYTES * 7 // 8)))
    with sqlite3.connect(store) as connection:
        assert connection.execute('SELECT key_sha256,payload FROM seed').fetchall() == [('keeper', b'k' * 100)]
        assert connection.execute('SELECT bytes FROM quota').fetchone()[0] == 1
    assert cache.cache_stats()['disk_errors'] == 1


def test_busy_database_is_optional_failure_and_never_holds_business_transaction(store):
    cache._put_disk('keeper', record())
    blocker = sqlite3.connect(store)
    try:
        with cache.disk_session() as session:
            assert cache._get_disk('keeper') == record()
            blocker.execute('BEGIN IMMEDIATE')
            cache._put_disk('busy', record())
            assert not session.connection.in_transaction
    finally:
        blocker.rollback(); blocker.close()
    assert set(database_rows(store)) == {'keeper'}
    assert cache.cache_stats()['disk_errors'] == 1


def test_readonly_flush_is_optional_and_preserves_existing_rows(store, monkeypatch):
    cache._put_disk('keeper', record())
    original = cache._disk_connection

    def readonly(create, identity_out=None):
        connection = original(create, identity_out)
        connection.execute('PRAGMA query_only=ON')
        return connection

    monkeypatch.setattr(cache, '_disk_connection', readonly)
    with cache.disk_session():
        assert cache._get_disk('keeper') == record()
        cache._put_disk('readonly', record())
    assert set(database_rows(store)) == {'keeper'}
    assert cache.cache_stats()['disk_errors'] == 1


def test_corrupt_database_is_disabled_once_and_untouched(store):
    store.parent.mkdir(parents=True, mode=0o700)
    store.write_bytes(b'not a SQLite derived cache'); store.chmod(0o600)
    before = store.read_bytes()
    with cache.disk_session() as session:
        assert cache._get_disk('missing') is None
        assert cache._get_disk('missing-again') is None
        cache._put_disk('ignored', record())
        assert session.disabled
    assert store.read_bytes() == before and cache.cache_stats()['disk_errors'] == 1


def test_batched_eviction_retains_payload_and_physical_quotas(store, monkeypatch):
    monkeypatch.setattr(cache, 'MAX_DISK_BYTES', 256 * 1024)
    monkeypatch.setattr(cache, 'SESSION_MAX_RECORDS', 2)
    with cache.disk_session():
        for index in range(8):
            cache._put_disk(str(index), record(bytes([index]) * 40000))
    values = database_rows(store)
    assert cache.cache_stats()['disk_errors'] == 0
    assert '0' not in values and '7' in values
    assert sum(map(len, values.values())) <= cache.MAX_DISK_BYTES * 7 // 8
    assert store.stat().st_size <= cache.MAX_DISK_BYTES


def test_independent_process_sessions_keep_atomic_quota_and_exact_records(store):
    cache._put_disk('keeper', record())
    script = '''import sys
from hashlib import sha256
from app.services import condition_basis_cache as cache
cache.SESSION_MAX_RECORDS=8
with cache.disk_session():
 for index in range(32):
  value=(sys.argv[1]+str(index)).encode()
  cache._put_disk(sys.argv[1]+str(index),(value,sha256(value).hexdigest(),len(value)))
assert cache.cache_stats()['disk_errors']==0,cache.cache_stats()
print('ok')
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    processes = [subprocess.Popen([sys.executable, '-B', '-c', script, prefix],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for prefix in ('one', 'two')]
    for process in processes:
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0, error
        assert output.strip() == 'ok'
    values = database_rows(store)
    assert len(values) == 65
    for prefix in ('one', 'two'):
        for index in range(32):
            assert values[prefix + str(index)] == (prefix + str(index)).encode()


def test_real_basis_history_and_dwm_proofs_restore_exactly_from_pending_then_disk(store, monkeypatch):
    from app.domain.conditions import evaluate, metrics_for, prepare_metric_bars
    from app.domain.history_events import HistoryContext
    from app.domain.strategy_prices import compact_source_token, prepare_condition_prices
    from app.services import forward_history_cache as history, strategy_periods as periods
    from test_boll_signal_integration import config
    from test_forward_shared_window_cache import event
    from test_forward_tracking_backtest import ForwardMarket

    market = ForwardMarket(320, 280, 200)
    symbol, snapshot = '600000.SH', 'forward-fixture'
    rows, calendar = market.data[symbol], market.days
    day = calendar[-1]
    source = compact_source_token(market.verify_condition_sources())
    generation = (snapshot, source['verified_closure_sha256'])
    prices = prepare_condition_prices(rows, market.events[symbol], calendar, day, 'forward',
        instrument_id=symbol, actions_coverage={'known': True, 'source_token': source, 'snapshot_id': snapshot})
    for lane in ('raw_rows', 'price_rows'):
        prices[lane] = prepare_metric_bars(prices[lane])
    basis_key = cache.key_for(symbol, day, generation, source)
    tree = event()
    scope = history.window_scope([tree], calendar, day)
    wider = history.shared_window_scope(calendar, day, scope)
    history_key = history.key_for(symbol, day, snapshot, prices['cache_key'], wider)
    original = HistoryContext(rows, calendar, day, snapshot, retain_start=wider['first'], price_context=prices)
    expected_event = evaluate(tree, metrics_for(rows, day, calendar, price_context=prices), original.at(day))
    options = {'instrument_id': symbol, 'generation': generation, 'price_context': prices,
               'include_higher_lows': True, 'boll_configs': [config()]}
    periods.clear_metric_cache()
    try:
        with cache.disk_session() as session:
            assert cache.remember(basis_key, prices)
            assert history.remember_shared(history_key, original, wider)
            expected = {period: periods.cached_metrics(rows, day, calendar, period, **options)
                        for period in ('D', 'W', 'M')}
            cache.clear_cache(); periods.clear_metric_cache()
            assert database_rows(store) == {}, 'every codec is still inside the bounded pending batch'
            restored_basis = cache.restore(basis_key)
            assert restored_basis['raw_rows'] == prices['raw_rows']
            assert restored_basis['price_rows'].normalized() == prices['price_rows'].normalized()
            projected = history.restore_shared(history_key, scope, calendar,
                                                {value: index for index, value in enumerate(calendar)})
            assert evaluate(tree, metrics_for(rows, day, calendar, price_context=prices), projected.at(day)) == expected_event

            def cannot_recompute(*args, **kwargs):
                raise AssertionError('pending D/W/M proof must restore without recomputing')

            with monkeypatch.context() as patch:
                patch.setattr(periods, 'metrics_for', cannot_recompute)
                actual = {period: periods.cached_metrics([], day, calendar, period, **options)
                          for period in ('D', 'W', 'M')}
            assert actual == expected
            assert not session.connection.in_transaction
        cache.clear_cache(); periods.clear_metric_cache()
        assert cache.restore(basis_key)['price_rows'].normalized() == prices['price_rows'].normalized()
        assert {period: periods.cached_metrics([], day, calendar, period, **options)
                for period in ('D', 'W', 'M')} == expected
    finally:
        periods.clear_metric_cache(); history.clear_cache()
