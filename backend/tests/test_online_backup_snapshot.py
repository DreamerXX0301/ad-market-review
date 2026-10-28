"""An online WAL copy keeps its original read view across concurrent writes."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('online_snapshot_backup', ROOT / 'scripts/backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@pytest.fixture
def databases(tmp_path):
    source_path = tmp_path / 'source.sqlite3'
    source = backup.connect_sqlite(source_path, 'rwc')
    source.execute('PRAGMA journal_mode=WAL')
    source.execute('CREATE TABLE snapshot_probe(id INTEGER PRIMARY KEY,value TEXT,padding BLOB)')
    source.executemany('INSERT INTO snapshot_probe VALUES(?,?,zeroblob(8192))',
                       [(index, 'before') for index in range(1, 33)])
    source.commit()
    writer = backup.connect_sqlite(source_path)
    destination = backup.connect_sqlite(tmp_path / 'backup.sqlite3', 'rwc')
    try:
        yield source, writer, destination
    finally:
        destination.close()
        writer.close()
        source.close()


def test_backup_uses_before_snapshot_when_a_wal_writer_commits_during_first_step(databases):
    source, writer, destination = databases
    steps = []

    def write_during_first_step(status, remaining, total):
        steps.append((status, remaining, total))
        if len(steps) == 1:
            assert remaining > 0  # The concurrent commit occurs mid-copy.
            assert source.in_transaction
            writer.execute("UPDATE snapshot_probe SET value='after' WHERE id=1")
            writer.commit()  # Proves the copy holds no write transaction.

    backup.backup_sqlite_snapshot(source, destination, pages=1, sleep=0, progress=write_during_first_step)

    assert len(steps) > 1 and steps[-1][0] == sqlite3.SQLITE_DONE
    assert not source.in_transaction
    assert writer.execute('SELECT value FROM snapshot_probe WHERE id=1').fetchone()[0] == 'after'
    assert destination.execute('SELECT value FROM snapshot_probe WHERE id=1').fetchone()[0] == 'before'
    assert destination.execute('SELECT count(*) FROM snapshot_probe').fetchone()[0] == 32
    assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    # The same connection must be free for the normal backup-pin cleanup.
    source.execute('BEGIN IMMEDIATE')
    source.execute("UPDATE snapshot_probe SET value='cleanup-write' WHERE id=2")
    source.commit()


def test_failed_backup_releases_its_read_transaction_for_final_pin_cleanup(databases):
    source, writer, destination = databases

    def interrupt(status, remaining, total):
        assert source.in_transaction
        writer.execute("UPDATE snapshot_probe SET value='writer-committed' WHERE id=1")
        writer.commit()
        raise RuntimeError('controlled backup interruption')

    with pytest.raises(RuntimeError, match='controlled backup interruption'):
        backup.backup_sqlite_snapshot(source, destination, pages=1, sleep=0, progress=interrupt)
    assert not source.in_transaction
    source.execute('BEGIN IMMEDIATE')
    source.execute("UPDATE snapshot_probe SET value='cleanup-write' WHERE id=2")
    source.commit()
    assert writer.execute('SELECT value FROM snapshot_probe WHERE id=2').fetchone()[0] == 'cleanup-write'


def test_backup_rejects_caller_owned_transaction_without_rolling_it_back(databases):
    source, _writer, destination = databases
    source.execute('BEGIN IMMEDIATE')
    source.execute("UPDATE snapshot_probe SET value='caller-owned' WHERE id=1")
    with pytest.raises(ValueError, match='no active transaction'):
        backup.backup_sqlite_snapshot(source, destination)
    assert source.in_transaction
    assert source.execute('SELECT value FROM snapshot_probe WHERE id=1').fetchone()[0] == 'caller-owned'
    source.rollback()
