"""Exercise the offline compactor against real migrations and frozen rows."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from app.infrastructure import database
from app.infrastructure.storage import hash_file
from app.services import selection_candidate_compaction as compaction
from app.services.selection_candidate_storage import candidate_evidence_json


_STAMP = 1770000000000
_DAY = '2026-02-02'
_HASH = 'a' * 64
_CHECKS = '  [ true, null, { "文本" : "\\u4e2d" } ]\n'
_METRICS = json.dumps({'facts': ['重复技术依据' * 40] * 8}, ensure_ascii=False)
_TOPICS = ' [ "题材", "第二项" ] '
_EVIDENCE = (_CHECKS, _METRICS, _TOPICS)
_UNCHANGED = ('{}', '{}', '[]')


@pytest.fixture
def old_copy(tmp_path, monkeypatch):
    migration_dir = tmp_path / 'migrations'
    migration_dir.mkdir()
    for migration in database.MIGRATIONS_DIR.glob('*.sql'):
        if migration.name < '047_':
            (migration_dir / migration.name).symlink_to(migration)
    source = tmp_path / 'restored' / 'app.sqlite3'
    output = tmp_path / 'compacted' / 'app.sqlite3'
    output.parent.mkdir()
    with monkeypatch.context() as restore:
        restore.setattr(database, 'MIGRATIONS_DIR', migration_dir)
        restore.setenv('SHIXU_DATA_DIR', str(tmp_path / 'restored'))
        restore.setenv('SHIXU_SQLITE_PATH', str(source))
        database.ensure_ready()
    with closing(database._raw_connection(source)) as connection, connection:
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.execute(
            '''
            INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms)
            VALUES('job','request','logical','initialise','{}',?,'succeeded',?)
            ''',
            (_HASH, _STAMP),
        )
        connection.execute(
            '''
            INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,knowledge_cutoff_ms,
              price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms)
            VALUES('bars','analysis_bars','all','job',?,?,?,'none','fixture','{}','bars/manifest.json',?,'staged','ready',?)
            ''',
            (_DAY, _DAY, _STAMP, _HASH, _STAMP),
        )
        connection.execute("INSERT INTO snapshot_artifact VALUES('bars','bars/data.parquet','analysis_bars',?,100,100)", (_HASH,))
        connection.execute(
            """
            INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,
                continuous_begin,continuous_end)
            VALUES('bars','analysis_bars','{}','ready',100,?,?)
            """,
            (_DAY, _DAY),
        )
        connection.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id='bars'")
        connection.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id='bars'", (_STAMP,))
        connection.execute("INSERT INTO strategy(strategy_id,name,created_at_ms) VALUES('strategy','策略',?)", (_STAMP,))
        connection.execute(
            '''
            INSERT INTO strategy_version(strategy_id,version,period,definition_json,definition_sha256,
                evaluator_version,recorded_at_ms)
            VALUES('strategy',1,'D','{}',?,'fixture',?)
            ''',
            (_HASH, _STAMP),
        )
        connection.execute(
            '''
            INSERT INTO selection_run(run_id,job_id,strategy_id,strategy_version,business_date,primary_snapshot_id,
                input_snapshots_json,input_sha256,status,universe_definition_json,created_at_ms)
            VALUES('run','job','strategy',1,?,'bars','{}',?,'running','{}',?)
            ''',
            (_DAY, _HASH, _STAMP),
        )
        connection.executemany(
            '''
            INSERT INTO selection_candidate(rowid,run_id,instrument_id,decision,close_price_e8,
                checks_json,metrics_json,topic_snapshot_json)
            VALUES(?,'run',?,?,?,?,?,?)
            ''',
            [
                (-2 ** 63, '000001.SZ', 'matched', 500_000_000, *_EVIDENCE),
                *((i, f'600{i:03d}.SH', 'unmatched', 800_000_000, *_EVIDENCE) for i in range(65)),
                (2 ** 63 - 1, '920001.BJ', 'unknown', None, *_UNCHANGED),
            ],
        )
        connection.execute("UPDATE selection_run SET status='completed',completed_at_ms=? WHERE run_id='run'", (_STAMP,))
        connection.execute('CREATE TABLE offline_fixture_unindexed(value TEXT NOT NULL) STRICT')
        connection.executemany('INSERT INTO offline_fixture_unindexed(rowid,value) VALUES(?,?)', [(1, 'first'), (7, 'second')])
    source.chmod(0o640)
    return source, output


def test_compacts_schema46_and_preserves_frozen_candidates(old_copy, tmp_path):
    source, output = old_copy
    source_hash = hash_file(source)
    receipt = compaction.compact_selection_candidates(source, output)
    assert receipt['input_schema_version'] == 46
    assert receipt['output_schema_version'] == 47
    assert receipt['verified_candidates'] == 67
    assert receipt['newly_packed_candidates'] == 66
    assert receipt['verified_rowid_tables'] > 0
    assert receipt['input_bytes'] - receipt['output_bytes'] == receipt['size_difference_bytes'] > 0
    assert receipt['available_bytes_before'] >= receipt['estimated_scratch_bytes'] == receipt['input_bytes'] * 3
    assert hash_file(source) == source_hash
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    assert tuple(output.parent.iterdir()) == (output,)

    with closing(database._raw_connection(output)) as connection:
        assert [tuple(row) for row in connection.execute(
            'SELECT rowid,value FROM offline_fixture_unindexed ORDER BY rowid'
        )] == [(1, 'first'), (7, 'second')]
        rows = {
            row['instrument_id']: row
            for row in connection.execute('SELECT rowid,* FROM selection_candidate')
        }
        example = rows['000001.SZ']
        assert (example['decision'], example['close_price_e8']) == ('matched', 500_000_000)
        assert tuple(example[key] for key in ('checks_json', 'metrics_json', 'topic_snapshot_json')) == _UNCHANGED
        assert candidate_evidence_json(example) == _EVIDENCE
        assert example['rowid'] == -2 ** 63
        assert rows['920001.BJ']['rowid'] == 2 ** 63 - 1
        assert rows['920001.BJ']['packed_evidence'] is None
        with pytest.raises(sqlite3.IntegrityError, match='candidates are immutable'):
            connection.execute("UPDATE selection_candidate SET close_price_e8=6 WHERE instrument_id='000001.SZ'")

    repeated = tmp_path / 'second-compaction' / 'app.sqlite3'
    repeated.parent.mkdir()
    second = compaction.compact_selection_candidates(output, repeated)
    assert second['input_schema_version'] == 47
    assert second['candidate_sha256'] == receipt['candidate_sha256']
    assert second['newly_packed_candidates'] == 0


def test_rejects_alias_active_database_and_log_sidecars(old_copy, monkeypatch):
    source, output = old_copy
    with pytest.raises(ValueError, match='相互独立的目录'):
        compaction.compact_selection_candidates(source, source.with_name('other.sqlite3'))
    output.hardlink_to(source)
    with pytest.raises(FileExistsError):
        compaction.compact_selection_candidates(source, output)
    output.unlink()
    with monkeypatch.context() as active:
        active.setenv('SHIXU_SQLITE_PATH', str(source))
        with pytest.raises(ValueError, match='配置中的业务库'):
            compaction.compact_selection_candidates(source, output)
    Path(str(source) + '-wal').touch()
    with pytest.raises(ValueError, match='SQLite 日志文件'):
        compaction.compact_selection_candidates(source, output)
    assert not output.exists()


def test_never_publishes_a_copy_when_evidence_round_trip_fails(old_copy, monkeypatch):
    source, output = old_copy
    source_hash = hash_file(source)
    monkeypatch.setattr(compaction, 'pack_evidence', lambda *_: b'bad packed evidence')
    with pytest.raises(ValueError, match='压缩证据无效'):
        compaction.compact_selection_candidates(source, output)
    assert not tuple(output.parent.iterdir())
    assert hash_file(source) == source_hash


def test_insufficient_scratch_space_is_rejected_before_creating_a_copy(old_copy, monkeypatch):
    source, output = old_copy
    monkeypatch.setattr(compaction.shutil, 'disk_usage', lambda _: SimpleNamespace(free=source.stat().st_size))
    with pytest.raises(OSError, match='文件系统空间不足'):
        compaction.compact_selection_candidates(source, output)
    assert not tuple(output.parent.iterdir())
