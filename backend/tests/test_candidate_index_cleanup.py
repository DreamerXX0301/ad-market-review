"""Check the unique keys retained by the schema 047 index cleanup."""

import shutil
import sqlite3

import pytest

from app.infrastructure import database


MIGRATION = '047_selection_candidate_packed_evidence.sql'
DROPPED_INDEXES = {
    'ai_request_receipt_task': ('ai_request_receipt', ('task_id', 'ordinal')),
    'intraday_monitor_batch_day': ('intraday_monitor_batch', ('trade_date', 'slot_key')),
    'auction_automation_run_latest': ('market_auction_automation_run', ('trade_date',)),
    'auction_snapshot_by_date': ('market_auction_snapshot', ('trade_date',)),
    'noon_snapshot_by_date': ('market_noon_snapshot', ('trade_date',)),
}
ORDERED_QUERIES = {
    'ai_request_receipt': (
        ('SELECT * FROM ai_request_receipt WHERE task_id=? ORDER BY ordinal', ('fixture-task',)),
    ),
    'intraday_monitor_batch': (
        ('''SELECT batch_id,status,completed_at_ms FROM intraday_monitor_batch
            WHERE trade_date=? ORDER BY slot_key DESC LIMIT 1''', ('2026-11-12',)),
        ('''SELECT * FROM intraday_monitor_batch WHERE trade_date<?
            ORDER BY trade_date,slot_key LIMIT ?''', ('2026-11-13', 20)),
    ),
    'market_auction_automation_run': (
        ('SELECT * FROM market_auction_automation_run ORDER BY trade_date DESC LIMIT 1', ()),
    ),
    'market_auction_snapshot': (
        ('''SELECT trade_date,quality,content_sha256,payload_json
            FROM market_auction_snapshot ORDER BY trade_date''', ()),
    ),
    'market_noon_snapshot': (
        ('''SELECT snapshot_id,trade_date,job_id,content_sha256,payload_json,sources_json
            FROM market_noon_snapshot ORDER BY trade_date''', ()),
    ),
}


def _indexes(connection):
    return dict(connection.execute("SELECT name,sql FROM sqlite_schema WHERE type='index'"))


def _check_unique_keys_and_query_plans(connection):
    for removed_index, (table, columns) in DROPPED_INDEXES.items():
        indexes = connection.execute('SELECT * FROM pragma_index_list(?)', (table,)).fetchall()
        assert removed_index not in {row['name'] for row in indexes}
        covering = set()
        for row in indexes:
            if not row['unique'] or row['partial']:
                continue
            keys = connection.execute(
                'SELECT name,coll FROM pragma_index_xinfo(?) WHERE key=1 ORDER BY seqno',
                (row['name'],),
            ).fetchall()
            if [tuple(key) for key in keys] == [(column, 'BINARY') for column in columns]:
                covering.add(row['name'])
        assert covering, f'{table} no longer enforces uniqueness on {columns}'

        for query, parameters in ORDERED_QUERIES[table]:
            plan = [row['detail'] for row in connection.execute('EXPLAIN QUERY PLAN ' + query, parameters)]
            assert any(
                f'INDEX {index}' in detail for index in covering for detail in plan
            ), plan
            assert all('TEMP B-TREE' not in detail for detail in plan), plan


@pytest.fixture
def isolated_migrations(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    return database.MIGRATIONS_DIR


def test_fresh_install_has_unique_ordered_paths_without_duplicate_indexes(isolated_migrations):
    with database.connect() as connection:
        assert connection.execute(
            'SELECT count(*) FROM _schema_migration WHERE name=?', (MIGRATION,),
        ).fetchone()[0] == 1
        _check_unique_keys_and_query_plans(connection)


def test_upgrade_047_drops_only_five_indexes_and_preserves_rows(
    isolated_migrations, tmp_path, monkeypatch,
):
    legacy = tmp_path / 'migrations'
    legacy.mkdir()
    for path in isolated_migrations.glob('[0-9]*.sql'):
        if int(path.name.split('_', 1)[0]) < 47:
            shutil.copyfile(path, legacy / path.name)
    monkeypatch.setattr(database, 'MIGRATIONS_DIR', legacy)

    receipt_sql = '''INSERT INTO market_auction_automation_run
        (trade_date,schedule_revision,state,created_at_ms,updated_at_ms)
        VALUES(?,1,'skipped',1,1)'''
    latest_sql = 'SELECT * FROM market_auction_automation_run ORDER BY trade_date DESC LIMIT 1'
    with database.connect() as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 46
        connection.executemany(receipt_sql, [('2026-11-12',), ('2026-11-11',)])
        old_latest = tuple(connection.execute(latest_sql).fetchone())
        old_indexes = _indexes(connection)
        assert DROPPED_INDEXES.keys() <= old_indexes.keys()

    shutil.copyfile(isolated_migrations / MIGRATION, legacy / MIGRATION)
    with database.connect() as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 47
        assert _indexes(connection) == {
            name: ddl for name, ddl in old_indexes.items() if name not in DROPPED_INDEXES
        }
        assert tuple(connection.execute(latest_sql).fetchone()) == old_latest
        assert old_latest[0] == '2026-11-12'
        assert connection.execute('SELECT count(*) FROM market_auction_automation_run').fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE constraint failed'):
            connection.execute(receipt_sql, ('2026-11-12',))
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        _check_unique_keys_and_query_plans(connection)
