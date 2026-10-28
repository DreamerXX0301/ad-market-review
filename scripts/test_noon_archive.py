"""午评原件进入标准备份闭包；隔离合成资料，不读取主库或真实凭据。"""
from hashlib import sha256
import gzip
import json
from pathlib import Path
import sqlite3
import time

import pytest

from test_deployment import source
from backup import perform_backup, sha
from restore import perform_restore, verify_backup
from noon_archive import inventory, read_source

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-10-19'
JOB = 'job_' + '1' * 32


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def noon_source(source, monkeypatch):
    monkeypatch.delenv('SHIXU_RAW_DIR', raising=False)
    original = encode({'code': 0, 'data': {'item': [{'fixture': '午评测试样本'}]}})
    packed = gzip.compress(original, mtime=0)
    raw = source / 'raw'; path = raw / 'noon' / JOB / 'stocks.json.gz'
    path.parent.mkdir(parents=True); path.write_bytes(packed)
    record = {'path': path.relative_to(raw).as_posix(), 'sha256': sha256(original).hexdigest(),
              'stored_sha256': sha256(packed).hexdigest(), 'bytes': len(packed)}
    manifest = {'version': 'noon-snapshot-v1', 'date': DAY, 'job_id': JOB,
                'sources': [record, {'name': 'fixture-failed', 'state': 'failed'}], 'gaps': []}
    content = encode(manifest); manifest_file = path.parent / 'manifest.json'; manifest_file.write_bytes(content)
    manifest_record = {'path': manifest_file.relative_to(raw).as_posix(), 'sha256': sha256(content).hexdigest(),
                       'stored_sha256': sha256(content).hexdigest(), 'bytes': len(content), 'kind': 'manifest'}
    body = encode({'version': 'noon-snapshot-v1', 'date': DAY, 'sources': manifest['sources'] + [manifest_record]})
    db = sqlite3.connect(source / 'business/app.sqlite3')
    db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA recursive_triggers=ON'); db.execute('PRAGMA busy_timeout=10000')
    db.executescript((ROOT / 'backend/app/migrations/027_noon_snapshots.sql').read_text())
    now = time.time_ns() // 1000000
    db.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms)
        VALUES(?,?,?,'source_check','{}',?,'partial',?)''', (JOB, JOB, JOB, 'a' * 64, now))
    db.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (DAY, JOB, JOB, now))
    db.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)',
               ('noon_fixture', DAY, JOB, DAY + 'T11:31:00+08:00', DAY + 'T11:32:00+08:00', now,
                'partial', sha256(body).hexdigest(), body.decode(), encode(manifest['sources'] + [manifest_record]).decode()))
    db.commit(); db.close()
    return source, record, manifest_record, path


def test_standard_backup_restore_retains_noon_sql_and_exact_sources(noon_source, tmp_path):
    data, record, manifest_record, path = noon_source
    orphan = path.parent / 'unreferenced.json.gz'; orphan.write_bytes(b'unreferenced fixture')
    bundle = tmp_path / 'bundle'; perform_backup(data, bundle); manifest = verify_backup(bundle)
    names = {entry['path'] for entry in manifest['files']}
    assert 'raw/' + record['path'] in names and 'raw/' + manifest_record['path'] in names
    assert 'raw/' + orphan.relative_to(data / 'raw').as_posix() not in names
    target = tmp_path / 'restored'; perform_restore(bundle, target)
    assert (target / 'raw' / record['path']).read_bytes() == path.read_bytes()
    connection = sqlite3.connect(target / 'business/app.sqlite3'); connection.row_factory = sqlite3.Row
    assert inventory(connection, target / 'raw')[record['path']]['stored_sha256'] == record['stored_sha256']
    assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1
    connection.close()


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'symlink'])
def test_missing_corrupt_or_symlink_source_rejects_backup(noon_source, tmp_path, damage):
    data, _, _, path = noon_source
    if damage == 'missing':
        path.unlink()
    elif damage == 'corrupt':
        path.write_bytes(b'corrupt')
    else:
        outside = tmp_path / 'outside'; outside.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(outside)
    with pytest.raises(ValueError, match='Noon|noon'):
        perform_backup(data, tmp_path / 'must-not-exist')
    assert not (tmp_path / 'must-not-exist').exists()


def test_restore_refuses_omitted_dependency_even_when_outer_manifest_rehashed(noon_source, tmp_path):
    data, record, _, _ = noon_source
    bundle = tmp_path / 'bundle'; perform_backup(data, bundle)
    relative = 'raw/' + record['path']; (bundle / relative).unlink()
    path = bundle / 'backup-manifest.json'; manifest = json.loads(path.read_text())
    manifest['files'] = [entry for entry in manifest['files'] if entry['path'] != relative]; path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Noon|noon'):
        verify_backup(bundle)


def test_restore_refuses_extra_raw_object_with_valid_outer_checksum(noon_source, tmp_path):
    data, _, _, path = noon_source
    bundle = tmp_path / 'bundle'; perform_backup(data, bundle)
    relative = 'raw/noon/' + JOB + '/unreferenced.json.gz'; extra = bundle / relative; extra.write_bytes(path.read_bytes())
    manifest_path = bundle / 'backup-manifest.json'; manifest = json.loads(manifest_path.read_text())
    manifest['files'].append({'path': relative, 'bytes': extra.stat().st_size, 'sha256': sha(extra)})
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Noon.*closure'):
        verify_backup(bundle)


def test_inventory_does_not_enumerate_raw_history(noon_source, monkeypatch):
    data, record, _, _ = noon_source
    original = Path.rglob
    def reject_scan(path, pattern):
        if path == data / 'raw' or path.is_relative_to(data / 'raw'):
            pytest.fail('noon inventory must follow SQL exact dependency paths')
        return original(path, pattern)
    monkeypatch.setattr(Path, 'rglob', reject_scan)
    connection = sqlite3.connect(data / 'business/app.sqlite3'); connection.row_factory = sqlite3.Row
    assert record['path'] in inventory(connection, data / 'raw')
    connection.close()


def test_canonical_checksum_checked_even_if_gzip_stored_checksum_matches(noon_source):
    data, record, _, _ = noon_source
    wrong = {**record, 'sha256': '0' * 64}
    with pytest.raises(ValueError, match='canonical'):
        read_source(data / 'raw', wrong)


def test_old_backup_without_noon_table_still_restores(source, tmp_path):
    bundle = tmp_path / 'legacy'; perform_backup(source, bundle)
    assert not any(entry['path'].startswith('raw/') for entry in verify_backup(bundle)['files'])
    perform_restore(bundle, tmp_path / 'restored-legacy')
