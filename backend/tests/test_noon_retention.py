"""午评到期清理使用隔离SQLite和合成原件，不访问真实行情或运行库。"""
from hashlib import sha256
from datetime import date, datetime, timedelta
import gzip
import json
from pathlib import Path
import sqlite3

import pytest

from app import core
from app.services import noon_retention as retention


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for name in ('SHIXU_SQLITE_PATH', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR', 'SHIXU_PUBLISHED_DIR'):
        monkeypatch.delenv(name, raising=False)
    core.ensure_ready()
    return tmp_path


def job(identifier, *, kind='increment', status='succeeded', target='2026-11-27', policy=retention.VERSION):
    params = {'noon_retention_policy': policy} if policy else {}
    with core.connect() as connection:
        connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,
            parameters_json,parameters_hash,status,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?)''',
            (identifier, identifier, identifier, kind, target, core.dumps(params), core.digest(params), status, core.now_ms()))
    return {'job_id': identifier, 'kind': kind, 'parameters_json': core.dumps(params)}


def publication(owner, day='2026-11-27'):
    with core.connect() as connection:
        now = core.now_ms(); identifier = 'raw-fixture-' + owner
        connection.execute('''INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,
            data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,
            manifest_path,manifest_sha256,state,quality,created_at_ms)
            VALUES(?,'daily_raw','CN_A',?,?,?,?,'none','fixture',?,?,?,'staged','ready',?)''',
            (identifier, owner, day, day, now, core.dumps({'market_snapshot_id': 'market-fixture'}),
             identifier + '/manifest.json', 'a' * 64, now))
        connection.execute('INSERT INTO snapshot_artifact VALUES(?,?,?,?,?,?)',
                           (identifier, identifier + '/data.parquet', 'fixture', 'a' * 64, 100, 1))
        connection.execute("INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) VALUES(?,'daily_raw','{}','ready',1,?,?)", (identifier, day, day))
        connection.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?", (identifier,))
        connection.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id=?", (now, identifier))
        connection.execute('INSERT OR REPLACE INTO published_head VALUES(?,?,?,?)', ('daily_raw', 'CN_A', identifier, now))
    return identifier


def capture(day='2026-10-28', *, suffix='1', sources=None, token=None, payload=None, content_sha=None):
    token = token or suffix * 32
    capture_job = 'job_' + token
    job(capture_job, kind='source_check', target=day)
    original = core.dumps({'fixture': '真实响应仅限测试替身', 'date': day}).encode()
    compressed = gzip.compress(original, mtime=0)
    source = {'path': f'noon/{capture_job}/stocks.json.gz', 'bytes': len(compressed),
              'sha256': sha256(original).hexdigest(), 'stored_sha256': sha256(compressed).hexdigest()}
    path = core.raw_dir() / source['path']; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(compressed)
    values = [source] if sources is None else sources
    payload = core.dumps(payload if payload is not None else {'date': day, 'version': 'noon-snapshot-v1', 'fixture': True})
    identifier = 'noon_' + token
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (day, capture_job, capture_job, core.now_ms()))
        connection.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (identifier, day, capture_job, day + 'T11:31:00+08:00', day + 'T11:32:00+08:00',
                            core.now_ms(), 'partial', content_sha or sha256(payload.encode()).hexdigest(), payload, core.dumps(values)))
    return identifier, source, path


@pytest.mark.parametrize(('policy', 'window', 'expired_day', 'retained_day'), [
    (retention.VERSION, 30, '2026-10-28', '2026-10-29'),
    (retention.LEGACY_VERSION, 28, '2026-10-30', '2026-10-31'),
])
def test_calendar_boundary_purges_body_and_raw_but_retains_daily_slot(local, policy, window, expired_day, retained_day):
    increment = job('increment-new', policy=policy); publication(increment['job_id'])
    expired, _, path = capture(expired_day)
    retained, _, retained_path = capture(retained_day, suffix='2')
    result = retention.after_increment(increment)
    assert result['status'] == 'completed' and result['cutoff_date'] == retained_day
    assert result['version'] == policy and result['calendar_days'] == window
    assert result['purged_snapshots'] == 1 and not path.exists() and retained_path.exists()
    with core.connect() as connection:
        assert connection.execute('SELECT snapshot_id FROM market_noon_snapshot').fetchone()[0] == retained
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 2
        assert connection.execute('SELECT status FROM market_noon_purge WHERE snapshot_id=?', (expired,)).fetchone()[0] == 'purged'
        assert connection.execute("SELECT status FROM job WHERE job_id='increment-new'").fetchone()[0] == 'succeeded'
        assert connection.execute('SELECT snapshot_id FROM published_head').fetchone()[0] == 'raw-fixture-increment-new'
    again = retention.after_increment(increment)
    assert again['purged_snapshots'] == 0 and again['removed_bytes'] == 0


def test_no_change_requires_result_or_frozen_date_matching_current_head(local):
    original = job('original', kind='initialise'); publication(original['job_id'])
    increment = job('unchanged', status='no_change', target=None)
    _, _, path = capture()
    assert retention.after_increment(increment)['status'] == 'skipped' and path.exists()
    result = retention.after_increment(increment, {'status': 'no_change', 'data_status': {
        'data_end': '2026-11-27', 'snapshot_id': 'market-fixture'}})
    assert result['purged_snapshots'] == 1 and not path.exists()


@pytest.mark.parametrize(('policy', 'included'), [(retention.VERSION, True), (retention.LEGACY_VERSION, False)])
def test_close_review_candidate_range_matches_frozen_retention(local, monkeypatch, policy, included):
    from app.services import noon_close_inputs, noon_close_service

    capture('2026-10-29')
    increment = job('fixture-close', policy=policy)
    params = json.loads(increment['parameters_json'])
    params['noon_close_policy'] = noon_close_service.VERSION
    increment['parameters_json'] = core.dumps(params)
    monkeypatch.setattr(noon_close_service, '_anchor', lambda *_, **_kwargs: {
        'date': '2026-11-27', 'generation': 'fixture',
    })
    attempted = []

    def missing_close(_generation, day, _payload):
        attempted.append(day)
        raise ValueError('缺少同日留存的收盘数据')

    monkeypatch.setattr(noon_close_inputs, 'load_inputs', missing_close)
    result = noon_close_service.after_increment(increment)
    assert attempted == (['2026-10-29'] if included else [])
    assert bool(result['warnings']) is included


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'paused', 'running'])
def test_stopped_or_not_yet_published_increment_never_deletes(local, status):
    increment = job('stopped', status=status); publication(increment['job_id'])
    _, _, path = capture()
    assert retention.after_increment(increment)['status'] == 'skipped' and path.exists()


def test_legacy_policy_is_noop_without_opening_database(monkeypatch):
    monkeypatch.setattr(retention, 'connect', lambda: pytest.fail('legacy task must not read noon data'))
    assert retention.after_increment({'kind': 'increment', 'parameters_json': '{}'})['status'] == 'skipped'
    assert retention.after_increment({'kind': 'initialise', 'parameters': {'noon_retention_policy': retention.VERSION}})['status'] == 'skipped'


def test_business_reference_and_backup_pin_protect_expired_body(local):
    increment = job('protected'); raw = publication(increment['job_id'])
    identifier, _, path = capture()
    with core.connect() as connection:
        connection.execute('CREATE TABLE fixture_business (snapshot_json TEXT)')
        connection.execute('INSERT INTO fixture_business VALUES(?)', (json.dumps({'snapshot': identifier}),))
    assert retention.after_increment(increment)['protected_snapshots'] == 1 and path.exists()
    with core.connect() as connection:
        connection.execute('DELETE FROM fixture_business')
        connection.execute("INSERT INTO retention_pin VALUES('backup-fixture',?,'backup','fixture',?,NULL)", (raw, core.now_ms()))
    assert retention.after_increment(increment)['protected_snapshots'] == 1 and path.exists()


def test_shared_source_dependency_protects_owner_without_directory_scan(local):
    increment = job('shared'); publication(increment['job_id'])
    identifier, source, path = capture()
    capture('2026-11-01', suffix='2', sources=[source])
    result = retention.after_increment(increment)
    assert result['protected_snapshots'] == 1 and path.exists()
    with core.connect() as connection:
        assert connection.execute('SELECT 1 FROM market_noon_snapshot WHERE snapshot_id=?', (identifier,)).fetchone()


@pytest.mark.parametrize('damage', ['hash', 'symlink', 'missing'])
def test_corrupt_or_unsafe_source_keeps_body_and_warns_without_failing_increment(local, damage):
    increment = job('damaged'); publication(increment['job_id'])
    identifier, _, path = capture()
    if damage == 'hash':
        content = bytearray(path.read_bytes()); content[-1] ^= 1; path.write_bytes(content)
    elif damage == 'symlink':
        outside = local / 'outside'; outside.write_text('sentinel'); path.unlink(); path.symlink_to(outside)
    else:
        path.unlink()
    result = retention.after_increment(increment)
    assert result['status'] == 'partial' and result['warnings']
    with core.connect() as connection:
        assert connection.execute('SELECT 1 FROM market_noon_snapshot WHERE snapshot_id=?', (identifier,)).fetchone()
        assert connection.execute("SELECT status FROM job WHERE job_id='damaged'").fetchone()[0] == 'succeeded'
    if damage == 'symlink':
        assert outside.read_text() == 'sentinel'


def test_mid_unlink_failure_retains_manifest_and_resumes_exact_remaining_sources(local, monkeypatch):
    increment = job('resume'); publication(increment['job_id'])
    identifier, source, path = capture()
    second = {**source, 'path': source['path'].replace('stocks.json.gz', 'indices.json.gz')}
    second_path = core.raw_dir() / second['path']; second_path.write_bytes(path.read_bytes())
    # The fixture replaces its unpublished synthetic row using the exact worker
    # deletion guard, never changing an immutable retained row in place.
    with core.connect() as connection:
        row = list(connection.execute('SELECT * FROM market_noon_snapshot').fetchone())
        connection.create_function('noon_purge_allowed', 1, lambda value: int(value == identifier))
        connection.execute('DELETE FROM market_noon_snapshot WHERE snapshot_id=?', (identifier,))
        row[-1] = core.dumps([source, second])
        connection.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)', row)
    original = retention.source_bytes
    def fail_second(root, value, *, remove=False):
        if remove and value['path'] == second['path']:
            raise OSError('fixture interruption')
        return original(root, value, remove=remove)
    monkeypatch.setattr(retention, 'source_bytes', fail_second)
    assert retention.after_increment(increment)['status'] == 'partial'
    assert not path.exists() and second_path.exists()
    monkeypatch.setattr(retention, 'source_bytes', original)
    assert retention.after_increment(increment)['purged_snapshots'] == 1
    assert not second_path.exists()


def test_unexpected_cleanup_error_becomes_warning_only(local, monkeypatch):
    increment = job('failure'); publication(increment['job_id'])
    monkeypatch.setattr(retention, '_collect', lambda *_: (_ for _ in ()).throw(RuntimeError('fixture')))
    assert retention.after_increment(increment)['status'] == 'warning'
    with core.connect() as connection:
        assert connection.execute("SELECT status FROM job WHERE job_id='failure'").fetchone()[0] == 'succeeded'


def test_own_notes_are_purged_with_body_but_cross_snapshot_reference_is_protected(local):
    increment = job('note-cascade'); publication(increment['job_id'])
    expired, _, path = capture()
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_note VALUES(?,?,?,?,?)',
                           ('fixture-note', expired, 1, '午后继续观察本午评', core.now_ms()))
    assert retention.after_increment(increment)['purged_snapshots'] == 1
    assert not path.exists()
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_note').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1


def test_unexpired_noon_notes_referencing_older_snapshot_protect_reference(local):
    increment = job('note-reference'); publication(increment['job_id'])
    expired, _, path = capture()
    newer, _, _ = capture('2026-11-24', suffix='2')
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_note VALUES(?,?,?,?,?)',
                           ('fixture-reference', newer, 1, '对照旧午评 ' + expired, core.now_ms()))
    assert retention.after_increment(increment)['protected_snapshots'] == 1
    assert path.exists()


@pytest.mark.parametrize('reference', ['frozen_only', 'manual_payload', 'manual_note', 'incomplete_fragment', 'bad_payload_sha'])
def test_real_frozen_next_noon_fragment_does_not_create_permanent_retention_chain(local, monkeypatch, reference):
    from app.services import noon_close_context, noon_close_service, noon_close_inputs
    from test_noon_close_service import facts, job as close_job
    day, next_day = '2026-10-28', '2026-10-29'
    noon, closing = facts()
    expired, _, path = capture(day, payload=noon)
    owner = close_job('close-before-next-noon'); publication(owner['job_id'], day)
    started = next_day + 'T11:31:00+08:00'
    known_at = int(datetime.fromisoformat(started).timestamp()*1000)
    # The fixture review must exist before the fixed next-noon capture, even
    # when this regression is run after that historical time in the real world.
    monkeypatch.setattr(noon_close_service, 'now_ms', lambda: known_at - 3600000)
    monkeypatch.setattr(noon_close_inputs, 'load_inputs', lambda *_: (closing, {'kind': 'derived-same-day-projection'}))
    assert noon_close_service.after_increment(owner)['created'] == 1
    frozen = noon_close_context.freeze_previous_close_summary(day, known_at)
    assert frozen['state'] in ('retained', 'partial') and frozen['items']
    identifier = frozen['review_id']
    payload = {'date': next_day, 'version': 'noon-snapshot-v1', 'collected_start_at': started,
               'calendar': {'verified': True, 'previous_day': day},
               'previous_review': {'date': day, 'known_at_ms': known_at, 'close_review_summary': frozen}}
    if reference == 'manual_payload': payload['manual_observation'] = '人工明确引用 ' + identifier
    if reference == 'incomplete_fragment': frozen['coverage']['retained_items'] += 1
    newer, _, newer_path = capture(next_day, suffix='2', payload=payload,
                                   content_sha='0'*64 if reference == 'bad_payload_sha' else None)
    if reference == 'manual_note':
        with core.connect() as connection:
            connection.execute('INSERT INTO market_noon_note VALUES(?,?,?,?,?)',
                ('manual-close-reference', newer, 1, '人工对照 ' + identifier, core.now_ms()))
    increment = job('expire-prior-noon'); publication(increment['job_id'])
    result = retention.after_increment(increment)
    if reference == 'frozen_only':
        assert result['purged_snapshots'] == 1 and not path.exists()
        with core.connect() as connection:
            assert connection.execute('SELECT count(*) FROM market_noon_close_review WHERE review_id=?', (identifier,)).fetchone()[0] == 0
            retained = json.loads(connection.execute('SELECT payload_json FROM market_noon_snapshot WHERE snapshot_id=?', (newer,)).fetchone()[0])
            assert retained['previous_review']['close_review_summary'] == frozen
    else:
        assert result['purged_snapshots'] == 0 and result['protected_snapshots'] == 1 and path.exists()
    assert newer_path.exists()


def test_daily_slot_and_body_are_not_deletable_on_ordinary_sql_connection(local):
    identifier, _, _ = capture()
    with pytest.raises(sqlite3.IntegrityError, match='retain noon daily'):
        with core.connect() as connection:
            connection.execute('DELETE FROM market_noon_capture')
    with pytest.raises(sqlite3.IntegrityError, match='authorization'):
        with core.connect() as connection:
            connection.execute('DELETE FROM market_noon_snapshot WHERE snapshot_id=?', (identifier,))


def failed_capture(day='2026-10-28', *, status='failed', token='3' * 32):
    capture_job = 'job_' + token
    job(capture_job, kind='source_check', target=day, status=status)
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (day, capture_job, capture_job, core.now_ms()))
    original = core.dumps({'code': 0, 'data': {'item': []}, 'fixture': True}).encode()
    packed = gzip.compress(original, mtime=0)
    path = core.raw_dir() / 'noon' / capture_job / 'calendar.json.gz'
    path.parent.mkdir(parents=True); path.write_bytes(packed)
    source = {'path': path.relative_to(core.raw_dir()).as_posix(), 'bytes': len(packed),
              'stored_sha256': sha256(packed).hexdigest(), 'sha256': sha256(original).hexdigest()}
    manifest = path.parent / 'manifest.json'
    manifest.write_text(core.dumps({'version': 'noon-snapshot-v1', 'job_id': capture_job,
                                   'date': day, 'sources': [source], 'gaps': []}))
    return capture_job, path, manifest


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_terminal_capture_without_snapshot_cleans_only_exact_manifest_sources(local, status):
    increment = job('orphan-expiry'); publication(increment['job_id'])
    capture_job, path, manifest = failed_capture(status=status)
    unknown = path.parent / 'unlisted-writer-file.json.gz'; unknown.write_bytes(b'unknown fixture')
    result = retention.after_increment(increment)
    assert result['purged_failed_captures'] == 1 and result['purged_snapshots'] == 0
    assert not path.exists() and not manifest.exists() and unknown.exists()
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM market_noon_snapshot').fetchone()[0] == 0
        receipt = connection.execute('SELECT * FROM market_noon_purge').fetchone()
        assert receipt['snapshot_id'] == 'noon_orphan_' + capture_job.removeprefix('job_')
        assert receipt['status'] == 'purged'
        assert connection.execute('SELECT status FROM job WHERE job_id=?', (capture_job,)).fetchone()[0] == status
    assert retention.after_increment(increment)['purged_failed_captures'] == 0


def test_paused_capture_and_unverifiable_failed_manifest_are_not_scanned_or_removed(local):
    increment = job('no-orphan-guessing'); publication(increment['job_id'])
    capture_job, path, manifest = failed_capture(status='paused')
    assert retention.after_increment(increment)['purged_failed_captures'] == 0
    assert path.exists() and manifest.exists()
    with core.connect() as connection:
        connection.execute("UPDATE job SET status='failed' WHERE job_id=?", (capture_job,))
    manifest.unlink()
    result = retention.after_increment(increment)
    assert result['purged_failed_captures'] == 0 and result['status'] == 'partial'
    assert path.exists() and result['warnings']


@pytest.mark.parametrize('blocked_by', ['business_reference', 'source_sha', 'source_identity'])
def test_retained_oldest_batch_does_not_starve_later_expiry_on_no_change_increment(local, monkeypatch, blocked_by):
    original = job('round-robin-head', kind='initialise'); head = publication(original['job_id'])
    captures = [capture((date(2026, 8, 1) + timedelta(days=index)).isoformat(),
                        token=f'{index + 1:032x}',
                        sources=[{'path': 'invalid-fixture-path'}]
                        if blocked_by == 'source_identity' and index < retention.MAX_SNAPSHOTS else None)
                for index in range(retention.MAX_SNAPSHOTS + 1)]
    blocked = captures[:-1]; later, _, later_path = captures[-1]
    if blocked_by == 'business_reference':
        with core.connect() as connection:
            connection.execute('CREATE TABLE fixture_business (snapshot_json TEXT)')
            connection.executemany('INSERT INTO fixture_business VALUES(?)',
                                   [(json.dumps({'snapshot': identifier}),) for identifier, _, _ in blocked])
    elif blocked_by == 'source_sha':
        for _, _, path in blocked:
            content = bytearray(path.read_bytes()); content[-1] ^= 1; path.write_bytes(content)
    # Every receipt may share a timestamp, including after a clock rollback.
    monkeypatch.setattr(retention, 'now_ms', lambda: 1000)
    first = retention.after_increment(job('round-robin-first', status='no_change'))
    assert first['purged_snapshots'] == 0 and later_path.exists()
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_purge').fetchone()[0] == retention.MAX_SNAPSHOTS
        assert all(json.loads(row[0])['check_sequence'] == 1
                   for row in connection.execute('SELECT manifest_json FROM market_noon_purge'))
    second = retention.after_increment(job('round-robin-second', status='no_change'))
    assert second['purged_snapshots'] == 1 and not later_path.exists()
    assert all(path.exists() for _, _, path in blocked)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_snapshot').fetchone()[0] == retention.MAX_SNAPSHOTS
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == retention.MAX_SNAPSHOTS + 1
        receipt = connection.execute('SELECT manifest_json FROM market_noon_purge WHERE snapshot_id=?', (later,)).fetchone()
        assert json.loads(receipt[0])['check_sequence'] == 2
        assert connection.execute("SELECT snapshot_id FROM published_head WHERE module='daily_raw'").fetchone()[0] == head
        assert connection.execute("SELECT count(*) FROM job WHERE kind='increment' AND status='no_change'").fetchone()[0] == 2
    if blocked_by == 'business_reference':
        identifier, _, path = blocked[0]
        with core.connect() as connection:
            connection.execute('DELETE FROM fixture_business WHERE snapshot_json=?',
                               (json.dumps({'snapshot': identifier}),))
        third = retention.after_increment(job('round-robin-third', status='no_change'))
        assert third['purged_snapshots'] == 1 and not path.exists()
        assert all(path.exists() for _, _, path in blocked[1:])


def test_invalid_source_inventory_keeps_its_original_reference_to_healthy_owner(local):
    increment = job('invalid-inventory-reference'); publication(increment['job_id'])
    owner, source, path = capture('2026-08-01')
    invalid, _, invalid_path = capture('2026-08-02', suffix='2',
                                       sources=[{**source, 'sha256': 'invalid'}])
    result = retention.after_increment(increment)
    assert result['status'] == 'partial' and result['purged_snapshots'] == 0
    assert result['protected_snapshots'] == 1 and result['warnings']
    assert path.exists() and invalid_path.exists()
    with core.connect() as connection:
        receipts = {row['snapshot_id']: dict(row) for row in connection.execute('SELECT * FROM market_noon_purge')}
        assert receipts[owner]['status'] == 'protected'
        assert receipts[invalid]['status'] == 'warning'
        assert all(json.loads(row['manifest_json'])['check_sequence'] == 1 for row in receipts.values())


def test_unverifiable_orphan_batch_rotates_and_repaired_manifest_can_resume(local, monkeypatch):
    original = job('orphan-round-robin-head', kind='initialise'); publication(original['job_id'])
    captures = [failed_capture((date(2026, 8, 1) + timedelta(days=index)).isoformat(),
                               token=f'{index + 1:032x}') for index in range(retention.MAX_SNAPSHOTS + 1)]
    manifests = [manifest.read_bytes() for _, _, manifest in captures[:-1]]
    for _, _, manifest in captures[:-1]:
        manifest.unlink()
    monkeypatch.setattr(retention, 'now_ms', lambda: 1000)
    first = retention.after_increment(job('orphan-round-robin-first', status='no_change'))
    assert first['purged_failed_captures'] == 0 and len(first['warnings']) == retention.MAX_SNAPSHOTS
    with core.connect() as connection:
        receipts = connection.execute('SELECT status,manifest_json FROM market_noon_purge').fetchall()
        assert len(receipts) == retention.MAX_SNAPSHOTS
        assert all(row['status'] == 'warning' and not json.loads(row['manifest_json'])['inventory_verified']
                   and json.loads(row['manifest_json'])['check_sequence'] == 1 for row in receipts)
    second = retention.after_increment(job('orphan-round-robin-second', status='no_change'))
    assert second['purged_failed_captures'] == 1
    assert not captures[-1][1].exists() and not captures[-1][2].exists()
    assert all(path.exists() for _, path, _ in captures[:-1])
    captures[0][2].write_bytes(manifests[0])
    third = retention.after_increment(job('orphan-round-robin-third', status='no_change'))
    assert third['purged_failed_captures'] == 1
    assert not captures[0][1].exists() and not captures[0][2].exists()
    assert all(path.exists() for _, path, _ in captures[1:-1])
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == retention.MAX_SNAPSHOTS + 1
        assert connection.execute("SELECT count(*) FROM market_noon_purge WHERE status='purged'").fetchone()[0] == 2
