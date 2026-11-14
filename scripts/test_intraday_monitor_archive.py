"""Temporary synthetic monitoring archives; no live backup, keys or requests."""
import gzip
import json
from pathlib import Path

import pytest

import backup as backup_module
import monitor_archive
from backup import connect_sqlite, perform_backup, sha
from restore import perform_restore, verify_backup
from test_deployment import source

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-11-13'
BATCH = 'monitor_' + '1' * 32


@pytest.fixture
def monitoring(source, monkeypatch):
    monkeypatch.delenv('SHIXU_RAW_DIR', raising=False)
    quote = source / 'published' / 'intraday-monitor' / DAY / (BATCH + '.parquet')
    raw = source / 'raw' / 'intraday-monitor' / DAY / (BATCH + '.json.gz')
    quote.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    # Deliberately synthetic opaque bytes; archive code validates file identity,
    # not the application's Parquet schema or provider business semantics.
    quote.write_bytes(b'PAR1-synthetic-monitor-observations-PAR1')
    raw.write_bytes(gzip.compress(b'{"fixture":"monitor provider response"}', mtime=0))
    metadata = lambda kind, path: dict(root=kind, path=path.relative_to(source / kind).as_posix(),
                                       sha256=sha(path), bytes=path.stat().st_size)
    manifest = dict(version='intraday-monitor-v1', quote_file=metadata('published', quote),
                    raw_file=metadata('raw', raw), sample_count=1)
    with connect_sqlite(source / 'business/app.sqlite3') as connection:
        connection.executescript((ROOT / 'backend/app/migrations/003_platform.sql').read_text())
        connection.executescript((ROOT / 'backend/app/migrations/045_intraday_monitor.sql').read_text())
        connection.execute('''INSERT INTO intraday_monitor_batch
            (batch_id,job_id,trade_date,slot_key,config_revision,status,manifest_json)
            VALUES(?,'import',?,'0940',3,'succeeded',?)''', (BATCH, DAY, json.dumps(manifest)))
        connection.execute('''INSERT INTO platform_setting VALUES('intraday-monitor-config-v1',?,1)''',
                           (json.dumps(dict(enabled=True, revision=3, interval_minutes=10, scopes=['tracking'])),))
        connection.execute('INSERT INTO intraday_monitor_baseline VALUES(?,?,?,?)',
                           (DAY, '600001.SH', 'bars', '{"fixture":"baseline"}'))
        connection.execute('INSERT INTO intraday_monitor_state VALUES(?,?,?,?,?,?,?,?)',
                           ('600001.SH', DAY, '0940', 3, '{"fixture":"state"}', '{}', '[]', 1))
        connection.execute('''INSERT INTO intraday_monitor_event
            (event_id,instrument_id,name,trade_date,type,phase,lower_price,upper_price,
             first_seen_at_ms,last_seen_at_ms,latest_price)
            VALUES('monitor_event','600001.SH','fixture',?,'support_reclaim','observed',10,10,1,1,10.2)''', (DAY,))
        connection.execute('''INSERT INTO intraday_monitor_evidence
            (receipt_id,event_id,batch_id,rule_key,episode,type,phase,observed_at_ms,evidence_json)
            VALUES('monitor_receipt','monitor_event',?,'fixture_rule','fixture_episode',
                   'support_reclaim','observed',1,'{"fixture":"evidence"}')''', (BATCH,))
        connection.execute('''INSERT INTO retention_pin
            (pin_id,snapshot_id,owner_kind,owner_id,created_at_ms)
            VALUES('monitor-evidence-pin','bars','manual',?,1)''', ('intraday-monitor:' + BATCH,))
    return source, manifest, quote, raw


def update_manifest(data, manifest):
    with connect_sqlite(data / 'business/app.sqlite3') as connection:
        connection.execute('UPDATE intraday_monitor_batch SET manifest_json=? WHERE batch_id=?',
                           (json.dumps(manifest), BATCH))


def test_standard_backup_restore_exact_pair_and_disable_monitoring(monitoring, tmp_path):
    data, manifest, quote, raw = monitoring
    (quote.parent / 'orphan.parquet').write_bytes(b'unreferenced test quote')
    (raw.parent / 'orphan.json.gz').write_bytes(b'unreferenced test raw')
    with connect_sqlite(data / 'business/app.sqlite3', 'ro') as connection:
        events = [tuple(row) for row in connection.execute('SELECT * FROM intraday_monitor_event')]
        evidence = [tuple(row) for row in connection.execute('SELECT * FROM intraday_monitor_evidence')]
    bundle, restored = tmp_path / 'bundle', tmp_path / 'restored'
    perform_backup(data, bundle)
    frozen = verify_backup(bundle)
    assert {row['path'] for row in frozen['files'] if 'intraday-monitor/' in row['path']} == {
        'published/' + manifest['quote_file']['path'], 'raw/' + manifest['raw_file']['path']}
    before_bundle = {row['path']: sha(bundle / row['path']) for row in frozen['files']}
    report = perform_restore(bundle, restored)
    assert report['intraday_monitor_disabled'] is True
    assert report['intraday_monitor_reset'] == dict(configuration_disabled=True, removed_states=1, removed_baselines=1, removed_baseline_pins=0)
    with connect_sqlite(restored / 'business/app.sqlite3', 'ro') as connection:
        config = json.loads(connection.execute("SELECT value_json FROM platform_setting WHERE key='intraday-monitor-config-v1'").fetchone()[0])
        assert config == dict(enabled=False, revision=4, interval_minutes=10, scopes=['tracking'])
        assert connection.execute('SELECT count(*) FROM intraday_monitor_state').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM intraday_monitor_baseline').fetchone()[0] == 0
        assert [tuple(row) for row in connection.execute('SELECT * FROM intraday_monitor_event')] == events
        assert [tuple(row) for row in connection.execute('SELECT * FROM intraday_monitor_evidence')] == evidence
        assert connection.execute('SELECT manifest_json FROM intraday_monitor_batch').fetchone()[0] == json.dumps(manifest)
        assert connection.execute("SELECT count(*) FROM retention_pin WHERE owner_id=?", ('intraday-monitor:' + BATCH,)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM job WHERE status IN ('queued','running')").fetchone()[0] == 0
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    assert (restored / 'published' / manifest['quote_file']['path']).read_bytes() == quote.read_bytes()
    assert (restored / 'raw' / manifest['raw_file']['path']).read_bytes() == raw.read_bytes()
    assert {row['path']: sha(bundle / row['path']) for row in frozen['files']} == before_bundle
    with connect_sqlite(data / 'business/app.sqlite3', 'ro') as connection:
        assert json.loads(connection.execute("SELECT value_json FROM platform_setting WHERE key='intraday-monitor-config-v1'").fetchone()[0])['enabled']
        assert connection.execute('SELECT count(*) FROM intraday_monitor_state').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['quote_file', 'raw_file'])
@pytest.mark.parametrize('damage', ['missing', 'checksum', 'symlink', 'parent_symlink'])
def test_missing_changed_or_symlink_file_aborts_backup(monitoring, tmp_path, kind, damage):
    data, manifest, quote, raw = monitoring
    path = quote if kind == 'quote_file' else raw
    if damage == 'missing':
        path.unlink()
    elif damage == 'checksum':
        path.write_bytes(bytes([path.read_bytes()[0] ^ 1]) + path.read_bytes()[1:])
    elif damage == 'symlink':
        outside = tmp_path / 'outside-file'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    else:
        original = path.parent
        outside = tmp_path / 'outside-directory'
        original.rename(outside)
        original.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='Intraday|intraday'):
        perform_backup(data, tmp_path / 'must-not-exist')
    assert not (tmp_path / 'must-not-exist').exists()


@pytest.mark.parametrize('change', ['escape', 'absolute', 'backslash', 'wrong_day', 'wrong_batch',
                                    'wrong_root', 'missing_pair', 'bad_sha', 'bad_size', 'wrong_version'])
def test_manifest_path_identity_and_pair_must_match_captured_sqlite(monitoring, tmp_path, change):
    data, manifest, *_ = monitoring
    meta = manifest['quote_file']
    if change == 'escape': meta['path'] = 'intraday-monitor/' + DAY + '/../../outside.parquet'
    elif change == 'absolute': meta['path'] = '/outside.parquet'
    elif change == 'backslash': meta['path'] = meta['path'].replace('/', '\\')
    elif change == 'wrong_day': meta['path'] = meta['path'].replace(DAY, '2026-11-12')
    elif change == 'wrong_batch': meta['path'] = meta['path'].replace(BATCH, 'other_batch')
    elif change == 'wrong_root': meta['root'] = 'raw'
    elif change == 'missing_pair': manifest.pop('raw_file')
    elif change == 'bad_sha': meta['sha256'] = 'z' * 64
    elif change == 'bad_size': meta['bytes'] = True
    elif change == 'wrong_version': manifest['version'] = 'unknown'
    update_manifest(data, manifest)
    with pytest.raises(ValueError, match='Intraday|intraday'):
        perform_backup(data, tmp_path / 'must-not-exist')
    assert not (tmp_path / 'must-not-exist').exists()


def test_sql_inventory_does_not_scan_monitor_history(monitoring, monkeypatch):
    data, manifest, *_ = monitoring
    def reject(*_):
        pytest.fail('monitor inventory must follow exact SQL references, not scan files')
    monkeypatch.setattr(Path, 'rglob', reject)
    monkeypatch.setattr(Path, 'glob', reject)
    monkeypatch.setattr(Path, 'iterdir', reject)
    with connect_sqlite(data / 'business/app.sqlite3', 'ro') as connection:
        required = monitor_archive.inventory(connection, data / 'published', data / 'raw')
    assert required['published'][manifest['quote_file']['path']] == manifest['quote_file']
    assert required['raw'][manifest['raw_file']['path']] == manifest['raw_file']


def test_purged_rows_and_fileless_failed_receipts_need_no_missing_files(monitoring, tmp_path):
    data, manifest, quote, raw = monitoring
    with connect_sqlite(data / 'business/app.sqlite3') as connection:
        connection.execute('UPDATE intraday_monitor_batch SET purged_at_ms=1')
        connection.execute('''INSERT INTO intraday_monitor_batch
            (batch_id,job_id,trade_date,slot_key,config_revision,status)
            VALUES('failed_fixture','active',?,'0950',3,'failed')''', (DAY,))
    quote.unlink()
    raw.unlink()
    bundle = tmp_path / 'purged-bundle'
    perform_backup(data, bundle)
    assert not any('intraday-monitor/' in row['path'] for row in verify_backup(bundle)['files'])
    restored = tmp_path / 'restored'
    perform_restore(bundle, restored)
    with connect_sqlite(restored / 'business/app.sqlite3', 'ro') as connection:
        assert connection.execute('SELECT count(*) FROM intraday_monitor_batch').fetchone()[0] == 2


@pytest.mark.parametrize('kind', ['quote_file', 'raw_file'])
def test_restore_refuses_omitted_pair_member_even_with_outer_manifest_rehashed(monitoring, tmp_path, kind):
    data, manifest, *_ = monitoring
    bundle = tmp_path / 'bundle'
    perform_backup(data, bundle)
    relative = manifest[kind]['root'] + '/' + manifest[kind]['path']
    (bundle / relative).unlink()
    path = bundle / 'backup-manifest.json'
    outer = json.loads(path.read_text())
    outer['files'] = [row for row in outer['files'] if row['path'] != relative]
    path.write_text(json.dumps(outer))
    with pytest.raises(ValueError, match='Intraday|intraday'):
        perform_restore(bundle, tmp_path / 'must-not-exist')
    assert not (tmp_path / 'must-not-exist').exists()


@pytest.mark.parametrize('kind', ['published', 'raw'])
def test_restore_refuses_unreferenced_monitor_file_with_valid_outer_checksum(monitoring, tmp_path, kind):
    data, *_ = monitoring
    bundle = tmp_path / 'bundle'
    perform_backup(data, bundle)
    relative = kind + '/intraday-monitor/' + DAY + '/orphan' + ('.parquet' if kind == 'published' else '.json.gz')
    path = bundle / relative
    path.write_bytes(b'orphan synthetic content')
    outer_path = bundle / 'backup-manifest.json'
    outer = json.loads(outer_path.read_text())
    outer['files'].append(dict(path=relative, sha256=sha(path), bytes=path.stat().st_size))
    outer_path.write_text(json.dumps(outer))
    with pytest.raises(ValueError, match='Intraday monitor.*closure'):
        verify_backup(bundle)


def test_quote_changed_during_generic_copy_aborts_and_releases_backup_pins(monitoring, tmp_path, monkeypatch):
    data, _, quote, _ = monitoring
    original = backup_module.shutil.copyfile
    def copy(src, dst, *args, **kwargs):
        result = original(src, dst, *args, **kwargs)
        if Path(src) == quote:
            quote.write_bytes(quote.read_bytes() + b'changed')
        return result
    monkeypatch.setattr(backup_module.shutil, 'copyfile', copy)
    with pytest.raises(ValueError, match='changed during backup'):
        perform_backup(data, tmp_path / 'must-not-exist')
    with connect_sqlite(data / 'business/app.sqlite3', 'ro') as connection:
        assert connection.execute("SELECT count(*) FROM retention_pin WHERE owner_kind='backup'").fetchone()[0] == 0


def test_raw_changed_after_inventory_is_rechecked_before_copy(monitoring, tmp_path, monkeypatch):
    data, _, _, raw = monitoring
    original = monitor_archive.read_file
    mutated = False
    def read(root, metadata):
        nonlocal mutated
        content = original(root, metadata)
        if Path(root) == data / 'raw' and not mutated:
            raw.write_bytes(bytes([content[0] ^ 1]) + content[1:])
            mutated = True
        return content
    monkeypatch.setattr(monitor_archive, 'read_file', read)
    with pytest.raises(ValueError, match='Intraday monitor file checksum'):
        perform_backup(data, tmp_path / 'must-not-exist')
    assert mutated and not (tmp_path / 'must-not-exist').exists()


def test_old_database_and_backup_remain_compatible_without_creating_tables(source, tmp_path, monkeypatch):
    monkeypatch.delenv('SHIXU_RAW_DIR', raising=False)
    bundle, restored = tmp_path / 'legacy', tmp_path / 'restored'
    perform_backup(source, bundle)
    assert not any('intraday-monitor/' in row['path'] for row in verify_backup(bundle)['files'])
    report = perform_restore(bundle, restored)
    assert 'intraday_monitor_disabled' not in report
    with connect_sqlite(restored / 'business/app.sqlite3', 'ro') as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'intraday_monitor_%'").fetchall()
