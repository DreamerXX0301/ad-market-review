"""Sorted merge writes stay bounded; no requests or business database access."""
import hashlib

import duckdb
import pandas as pd
import pytest

from app.services import sync_service as sync


DAYS = ['2026-10-21', '2026-10-22', '2026-10-23']
COPY_SQL = ('COPY (SELECT * FROM merged ORDER BY thscode,date_ms) TO ? '
            '(FORMAT PARQUET,COMPRESSION ZSTD)')


class TrackedConnection:
    def __init__(self, connection, copy_error=None, restore_error=None):
        self.connection = connection
        self.copy_error = copy_error
        self.restore_error = restore_error
        self.queries = []
        self.copy_settings = []
        self.copy_attempted = False
        self.restore_attempted = False
        self.closed = False
        self.threads_at_close = None

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, parameters=None):
        self.queries.append(query)
        if query == COPY_SQL:
            self.copy_attempted = True
            self.copy_settings.append(self.connection.execute(
                "SELECT current_setting('threads'),current_setting('preserve_insertion_order')"
            ).fetchone())
            if self.copy_error is not None:
                raise self.copy_error
        if query == 'SET threads=2' and self.copy_attempted:
            self.restore_attempted = True
            if self.restore_error is not None:
                raise self.restore_error
        return self.connection.execute(query, parameters)

    def close(self):
        self.threads_at_close = self.connection.execute(
            "SELECT current_setting('threads')").fetchone()[0]
        self.connection.close()
        self.closed = True


def track_connections(monkeypatch, *, copy_error=None, restore_error=None):
    original = duckdb.connect
    connections = []

    def connect(*args, **kwargs):
        tracked = TrackedConnection(original(*args, **kwargs), copy_error, restore_error)
        connections.append(tracked)
        return tracked

    monkeypatch.setattr(sync.duckdb, 'connect', connect)
    return connections


def quote(day, close):
    return {'thscode': '600000.SH', 'currency': 'CNY', 'interval': '1d',
            'adjusted': 'none', 'date_ms': sync.midnight_ms(day),
            'open_price': 10., 'high_price': 13., 'low_price': 9.,
            'close_price': close, 'volume': 100., 'turnover': 1000.}


def inputs(tmp_path, *, unchanged=False):
    old_rows = [quote(DAYS[0], 10.), quote(DAYS[1], 10.)]
    if unchanged:
        old_rows.append(quote(DAYS[2], 12.))
        new_rows = [quote(DAYS[2], 12.)]
    else:
        new_rows = [quote(DAYS[1], 11.), quote(DAYS[2], 12.)]
    previous, incoming = tmp_path/'old.parquet', tmp_path/'incoming.parquet'
    pd.DataFrame(old_rows).to_parquet(previous, index=False)
    pd.DataFrame(new_rows).to_parquet(incoming, index=False)
    return incoming, previous, [{'thscode': '600000.SH', 'list_date': '2000-01-01'}]


def merge(paths, output, **kwargs):
    incoming, previous, master = paths
    return sync.merge_daily(incoming, previous, output, DAYS[0], DAYS[-1], DAYS, master, **kwargs)


@pytest.mark.parametrize('existing_empty_output', [False, True])
def test_only_copy_is_serial_and_restores_original_threads(tmp_path, monkeypatch, existing_empty_output):
    paths = inputs(tmp_path)
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths[:2]]
    output = tmp_path/'year.parquet'
    if existing_empty_output:
        output.touch()  # A failed writer's derived output is not an accepted batch.
    connections = track_connections(monkeypatch)
    result = merge(paths, output)
    tracked, = connections
    assert tracked.copy_settings == [(1, False)]
    assert tracked.queries.count('SET threads=1') == 1
    assert tracked.queries.count('SET threads=2') == 2
    assert tracked.queries.count("SET memory_limit='512MB'") == 1
    assert tracked.restore_attempted and tracked.closed and tracked.threads_at_close == 2
    assert result['rows'] == 3 and result['overlap_rows'] == result['revised_overlap_rows'] == 1
    assert result['unexplained_missing'] == 0 and result['derived_file_written'] is True
    rows = pd.read_parquet(output)
    assert rows.date_ms.tolist() == [sync.midnight_ms(day) for day in DAYS]
    assert rows.close_price.tolist() == [10., 11., 12.]
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths[:2]] == before


@pytest.mark.parametrize('restore_fails', [False, True])
def test_copy_error_survives_restore_failure_and_connection_closes(tmp_path, monkeypatch, restore_fails):
    paths = inputs(tmp_path)
    copy_error = duckdb.OutOfMemoryException('controlled sorted writer budget failure')
    restore_error = RuntimeError('controlled thread restore failure') if restore_fails else None
    connections = track_connections(monkeypatch, copy_error=copy_error, restore_error=restore_error)
    with pytest.raises(duckdb.OutOfMemoryException) as raised:
        merge(paths, tmp_path/'year.parquet')
    tracked, = connections
    assert raised.value is copy_error
    assert tracked.copy_settings == [(1, False)] and tracked.restore_attempted
    assert tracked.closed and tracked.threads_at_close == (1 if restore_fails else 2)


def test_restore_error_after_success_is_not_reported_as_success(tmp_path, monkeypatch):
    paths = inputs(tmp_path)
    restore_error = RuntimeError('controlled thread restore failure')
    connections = track_connections(monkeypatch, restore_error=restore_error)
    with pytest.raises(RuntimeError) as raised:
        merge(paths, tmp_path/'year.parquet')
    tracked, = connections
    assert raised.value is restore_error and tracked.restore_attempted and tracked.closed


def test_no_change_does_not_enter_writer_or_touch_output(tmp_path, monkeypatch):
    paths = inputs(tmp_path, unchanged=True)
    output = tmp_path/'unchanged.parquet'
    connections = track_connections(monkeypatch)
    result = merge(paths, output, unchanged_rows=3)
    tracked, = connections
    assert result['derived_file_written'] is False and not output.exists()
    assert not tracked.copy_attempted and not tracked.restore_attempted
    assert 'SET threads=1' not in tracked.queries
    assert tracked.closed and tracked.threads_at_close == 2
