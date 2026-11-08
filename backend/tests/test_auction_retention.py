"""竞价自然日清理的隔离验收，使用独立的业务库及合成原件。"""
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import shutil
import sqlite3

import pytest

from app import core
from app.services import auction_retention as retention
from app.services import source_service
from app.services.auction_integrity import validate_snapshot


ANCHOR = '2026-11-25'
EXPIRED = '2026-10-26'
BOUNDARY = '2026-10-27'
LEGACY_EXPIRED = '2026-10-28'
LEGACY_BOUNDARY = '2026-10-29'
LEGACY_POLICY = 'auction-retention-28d-v1'
CODE = '600001.SH'
MARKET = 'market-' + 'a' * 24


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE', str(tmp_path / 'unused-synthetic-master'))
    for name in ('SHIXU_SQLITE_PATH', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR',
                 'SHIXU_PUBLISHED_DIR', 'SHIXU_CREDENTIAL_DIR', 'SHIXU_MASTER_KEY_FILE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(source_service, 'client', lambda *_args, **_kwargs: pytest.fail('retention must not request data'))
    core.ensure_ready()
    yield tmp_path
    core.clear_market_cache()


def job(identifier, *, kind='increment', status='succeeded', target=ANCHOR, policy=True,
        extra=None):
    params = {'auction_retention_policy': retention.VERSION} if policy else {}
    params.update(extra or {})
    with core.connect() as connection:
        connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,
            parameters_json,parameters_hash,status,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?)''',
            (identifier, identifier, identifier, kind, target, core.dumps(params),
             core.digest(params), status, core.now_ms()))
        return dict(connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone())


def publication(owner, day=ANCHOR):
    """真正写入可独立备份的小型合成不可变代际；不伪造磁盘SHA。"""
    identifier = 'raw-fixture-' + owner
    root = core.published_dir()
    generation = root / MARKET
    generation.mkdir(parents=True, exist_ok=True)
    (generation / 'manifest.json').write_bytes(encode({'snapshot_id': MARKET, 'summary': {'files': []}}))
    artifact = generation / 'reference.json'
    artifact.write_bytes(encode({'synthetic': True, 'date': day}))
    manifest = root / 'catalog' / identifier / 'manifest.json'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(encode({'snapshot_id': identifier, 'module': 'daily_raw'}))
    with core.connect() as connection:
        now = core.now_ms()
        connection.execute('''INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,
            data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,
            manifest_path,manifest_sha256,state,quality,created_at_ms)
            VALUES(?,'daily_raw','CN_A',?,?,?,?,'none','fixture',?,?,?,'staged','ready',?)''',
            (identifier, owner, day, day, now, core.dumps({'market_snapshot_id': MARKET}),
             str(manifest.relative_to(root)), sha256(manifest.read_bytes()).hexdigest(), now))
        connection.execute('INSERT INTO snapshot_artifact VALUES(?,?,?,?,?,?)',
            (identifier, str(artifact.relative_to(root)), 'daily_raw',
             sha256(artifact.read_bytes()).hexdigest(), artifact.stat().st_size, 1))
        connection.execute('''INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,
            coverage_state,observed_rows,continuous_begin,continuous_end)
            VALUES(?,'daily_raw','{}','ready',1,?,?)''', (identifier, day, day))
        connection.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?", (identifier,))
        connection.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id=?", (now, identifier))
        connection.execute('INSERT OR REPLACE INTO published_head VALUES(?,?,?,?)', ('daily_raw', 'CN_A', identifier, now))
    return identifier


def payload(day):
    start = datetime.fromisoformat(day + 'T09:26:00+08:00')
    quote = {'thscode': CODE, 'ticker': CODE[:6], 'name': '竞价清理合成样本',
             'auction_price': 10.5, 'pre_close_price': 10, 'auction_pct': 5,
             'auction_volume': 100, 'auction_amount': 105000,
             'auction_turnover_pct': .1, 'auction_volume_ratio': 1.5}
    definitions = [
        ('calendar', '/api/a-share/calendar/trading-days', {},
         {'item': [{'date': day.replace('-', '')}]}),
        ('auction', '/api/a-share/auction/snapshot', {'stage': 'final', 'thscodes': CODE},
         {'auction_phase': 'matched', 'data_status': 'final', 'total': 1, 'item': [quote]}),
        ('benchmark', '/api/a-share/auction/short-term-benchmark', {'date': day},
         {'date': day, 'item': [{'thscode': CODE, 'tags': ['合成概念']}]}),
    ]
    sources, evidence = [], []
    for index, (name, endpoint, params, data) in enumerate(definitions):
        requested = start + timedelta(seconds=index)
        received = requested + timedelta(milliseconds=100)
        body = {'code': 0, 'data': {**data, 'timestamp': int(received.timestamp() * 1000)}}
        original = encode(body)
        record = {'name': name, 'source': 'fuyao', 'endpoint': endpoint, 'params': params,
                  'requested_at': requested.isoformat(), 'received_at': received.isoformat(),
                  'state': 'received', 'bytes': len(original), 'sha256': sha256(original).hexdigest()}
        sources.append(deepcopy(record))
        evidence.append({**deepcopy(record), 'body': body})
    previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    return {'version': 'auction-snapshot-v1', 'date': day, 'previous_date': previous,
            'collected_start_at': start.isoformat(),
            'collected_end_at': (start + timedelta(seconds=3)).isoformat(),
            'universe': {'items': [{'instrument_id': CODE, 'name': quote['name']}]},
            'quotes': {'state': 'observed', 'items': [{'instrument_id': CODE,
                'state': 'observed', 'name': quote['name'], 'auction_price': 10.5,
                'auction_pct': 5, 'auction_amount': 105000}]},
            'previous_review': {'date': previous, 'payload': {
                'date': previous, 'mode': 'previous_session_price_facts',
                'source_snapshot_id': MARKET}},
            'gaps': [], 'sources': sources, 'evidence': evidence}


def capture(day=EXPIRED, *, tag='', automatic=False):
    token = sha256((day + tag).encode()).hexdigest()[:32]
    source_job = 'job_' + token
    parameters = {'operation': 'auction_snapshot', 'version': 'auction-snapshot-v1',
                  'date': day, 'source_id': 'fuyao', 'stage': 'final',
                  'instrument_ids': [CODE], 'frozen_inputs': {'source_snapshot_id': MARKET}}
    job(source_job, kind='source_check', target=day, policy=False, extra=parameters)
    value = payload(day)
    encoded = encode(value)
    identifier = 'auction_' + token
    now = core.now_ms()
    with core.connect() as connection:
        connection.execute('INSERT INTO market_auction_capture VALUES(?,?,?,?)',
                           (day, source_job, source_job, now))
        connection.execute('INSERT INTO market_auction_snapshot VALUES(?,?,?,?,?,?,?,?,?)',
            (identifier, day, source_job, value['collected_start_at'], value['collected_end_at'],
             now, 'complete', sha256(encoded).hexdigest(), encoded.decode()))
        if automatic:
            connection.execute('''INSERT INTO market_auction_automation_run(trade_date,job_id,
                schedule_revision,state,attempts,automatic_control,outcome,created_at_ms,updated_at_ms)
                VALUES(?,?,1,'available',1,0,'available',?,?)''', (day, source_job, now, now))
        stored = dict(connection.execute('SELECT * FROM market_auction_snapshot WHERE snapshot_id=?', (identifier,)).fetchone())
    validate_snapshot(stored)
    return identifier, stored


def exists(identifier):
    with core.connect() as connection:
        return bool(connection.execute('SELECT 1 FROM market_auction_snapshot WHERE snapshot_id=?', (identifier,)).fetchone())


def receipt(identifier):
    with core.connect() as connection:
        row = connection.execute('SELECT * FROM market_auction_purge WHERE snapshot_id=?', (identifier,)).fetchone()
        return dict(row) if row else None


def test_calendar_cutoff_retains_boundary_and_preserves_capture_automatic_and_job_audit(local):
    increment = job('successful-increment'); publication(increment['job_id'])
    expired, original = capture(automatic=True)
    boundary, _ = capture(BOUNDARY)
    within_new_window, _ = capture(LEGACY_EXPIRED)
    with core.connect() as connection:
        before_job = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (original['job_id'],)).fetchone())
        automatic = dict(connection.execute('SELECT * FROM market_auction_automation_run').fetchone())
    shared = core.raw_dir() / 'shared-evidence.json'
    shared.write_text('unrelated synthetic source')
    result = retention.after_increment(increment)
    assert retention.cutoff_date(ANCHOR) == BOUNDARY
    assert result['calendar_days'] == 30
    assert result['cutoff_date'] == BOUNDARY and result['purged_snapshots'] == 1
    assert not exists(expired) and exists(boundary) and exists(within_new_window)
    tombstone = receipt(expired)
    assert tombstone['status'] == 'purged' and tombstone['content_sha256'] == original['content_sha256']
    assert tombstone['source_job_id'] == original['job_id'] and tombstone['cleanup_job_id'] == increment['job_id']
    assert tombstone['payload_bytes'] == len(original['payload_json'].encode()) and tombstone['evidence_count'] == 3
    assert MARKET not in core.dumps(tombstone)
    assert shared.read_text() == 'unrelated synthetic source'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 3
        assert dict(connection.execute('SELECT * FROM market_auction_automation_run').fetchone()) == automatic
        assert dict(connection.execute('SELECT * FROM job WHERE job_id=?', (original['job_id'],)).fetchone()) == before_job
    again = retention.after_increment(increment)
    assert again['purged_snapshots'] == 0 and receipt(expired) == tombstone
    with pytest.raises(sqlite3.DatabaseError, match='purged|retain'):
        with core.connect() as connection:
            connection.execute('INSERT INTO market_auction_snapshot VALUES(?,?,?,?,?,?,?,?,?)', tuple(original.values()))


def test_migration_preserves_legacy_receipt_and_both_frozen_cutoffs(local, monkeypatch):
    from app.infrastructure import database
    from app.services.auction_integrity import validate_purges

    old_migrations = local / 'schema39-migrations'
    old_migrations.mkdir()
    for path in database.MIGRATIONS_DIR.glob('[0-9]*.sql'):
        if int(path.name.split('_', 1)[0]) <= 39:
            shutil.copyfile(path, old_migrations / path.name)
    monkeypatch.setenv('SHIXU_DATA_DIR', str(local / 'upgrade-data'))
    with monkeypatch.context() as old:
        old.setattr(database, 'MIGRATIONS_DIR', old_migrations)
        core.ensure_ready()
        increment = job('legacy-cutoff', extra={'auction_retention_policy': LEGACY_POLICY})
        publication(increment['job_id'])
        expired, original = capture(LEGACY_EXPIRED)
        retained, _ = capture(LEGACY_BOUNDARY)
        result = retention.after_increment(increment)
        assert result['calendar_days'] == 28 and result['cutoff_date'] == LEGACY_BOUNDARY
        assert not exists(expired) and exists(retained)
        old_receipt = receipt(expired)

    core.ensure_ready()
    assert receipt(expired) == old_receipt
    with core.connect() as connection:
        validate_purges(connection)
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    with pytest.raises(sqlite3.IntegrityError, match='purged'):
        with core.connect() as connection:
            connection.execute('INSERT INTO market_auction_snapshot VALUES(?,?,?,?,?,?,?,?,?)', tuple(original.values()))

    increment = job('current-cutoff')
    publication(increment['job_id'])
    new_expired, row = capture()
    boundary, _ = capture(BOUNDARY)
    # The 28-day cutoff would satisfy the table's compatible CHECK, but is
    # rejected when the newly frozen owner authorizes only the 30-day policy.
    with pytest.raises(sqlite3.IntegrityError, match='frozen retention'):
        with core.connect() as connection:
            retention._receipt(connection, row, increment['job_id'], ANCHOR, LEGACY_BOUNDARY,
                               'protected', 'fixture', 2, 3)
    assert retention.after_increment(increment)['purged_snapshots'] == 1
    assert not exists(new_expired) and exists(boundary) and exists(retained)
    assert receipt(expired) == old_receipt
    with core.connect() as connection:
        validate_purges(connection)


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'paused', 'running', 'queued'])
def test_unsuccessful_increment_cannot_purge_even_with_an_existing_publication(local, status):
    increment = job('not-successful', status=status); publication(increment['job_id'])
    identifier, _ = capture()
    assert retention.after_increment(increment)['status'] == 'skipped'
    assert exists(identifier) and receipt(identifier) is None


def test_legacy_or_non_increment_call_never_opens_retention_database(monkeypatch):
    monkeypatch.setattr(retention, 'connect', lambda: pytest.fail('unfrozen retention must be a no-op'))
    assert retention.after_increment({'kind': 'increment', 'parameters_json': '{}'})['status'] == 'skipped'
    assert retention.after_increment({'kind': 'initialise', 'parameters': {
        'auction_retention_policy': retention.VERSION}})['status'] == 'skipped'


@pytest.mark.parametrize('mismatch', ['policy', 'target', 'publication'])
def test_persisted_authorization_and_own_publication_are_required(local, mismatch):
    increment = job('frozen-evidence', policy=mismatch != 'policy',
                    target='2026-11-24' if mismatch == 'target' else ANCHOR)
    if mismatch != 'publication':
        publication(increment['job_id'])
    identifier, _ = capture()
    forged = {**increment, 'parameters_json': core.dumps({'auction_retention_policy': retention.VERSION}),
              'target_date': ANCHOR}
    assert retention.after_increment(forged)['status'] == 'skipped'
    assert exists(identifier)


def test_no_change_requires_frozen_date_or_matching_result_not_wall_clock(local):
    original = job('published-owner', kind='initialise'); publication(original['job_id'])
    increment = job('no-change-owner', status='no_change', target=None)
    identifier, _ = capture()
    assert retention.after_increment(increment)['status'] == 'skipped' and exists(identifier)
    assert retention.after_increment(increment, {'data_status': {
        'data_end': ANCHOR, 'snapshot_id': 'another-market'}})['status'] == 'skipped'
    result = retention.after_increment(increment, {'status': 'no_change', 'data_status': {
        'data_end': ANCHOR, 'snapshot_id': MARKET}})
    assert result['purged_snapshots'] == 1 and not exists(identifier)


@pytest.mark.parametrize('protection', ['business', 'backup'])
def test_explicit_business_reference_and_backup_pin_protect_then_release(local, protection):
    increment = job('protected'); raw = publication(increment['job_id'])
    identifier, _ = capture()
    with core.connect() as connection:
        if protection == 'business':
            connection.execute('CREATE TABLE fixture_business(snapshot_json TEXT)')
            connection.execute('INSERT INTO fixture_business VALUES(?)', (core.dumps({'snapshot': identifier}),))
        else:
            connection.execute("INSERT INTO retention_pin VALUES('fixture-backup',?,'backup','fixture',?,NULL)", (raw, core.now_ms()))
    result = retention.after_increment(increment)
    assert result['protected_snapshots'] == 1 and exists(identifier)
    assert receipt(identifier)['status'] == 'protected'
    with core.connect() as connection:
        connection.execute('DELETE FROM fixture_business' if protection == 'business' else "DELETE FROM retention_pin WHERE pin_id='fixture-backup'")
    assert retention.after_increment(increment)['purged_snapshots'] == 1
    assert not exists(identifier)


def test_paused_capture_and_active_frozen_reference_protect_without_rewriting_jobs(local):
    increment = job('activity-protection'); publication(increment['job_id'])
    paused, original = capture()
    referenced, _ = capture('2026-10-25')
    active = job('manual-active-reference', kind='research', status='paused', policy=False,
                 extra={'retained_auction': referenced})
    with core.connect() as connection:
        connection.execute("UPDATE job SET status='paused' WHERE job_id=?", (original['job_id'],))
    assert retention.after_increment(increment)['protected_snapshots'] == 2
    assert exists(paused) and exists(referenced)
    with core.connect() as connection:
        assert connection.execute('SELECT parameters_json FROM job WHERE job_id=?', (active['job_id'],)).fetchone()[0] == active['parameters_json']


def test_bounded_rounds_do_not_let_protected_oldest_rows_starve_later_expiry(local, monkeypatch):
    increment = job('bounded-cleanup'); publication(increment['job_id'])
    identifiers = [capture(f'2026-10-{day:02d}')[0] for day in range(1, 5)]
    with core.connect() as connection:
        connection.execute('CREATE TABLE fixture_business(snapshot_json TEXT)')
        connection.execute('INSERT INTO fixture_business VALUES(?)', (core.dumps({'keep': identifiers[:2]}),))
    monkeypatch.setattr(retention, 'MAX_SNAPSHOTS', 2)
    first = retention.after_increment(increment)
    assert first['protected_snapshots'] == 2 and first['purged_snapshots'] == 0
    second = retention.after_increment(increment)
    assert second['protected_snapshots'] == 0 and second['purged_snapshots'] == 2
    assert all(exists(identifier) for identifier in identifiers[:2])
    assert not any(exists(identifier) for identifier in identifiers[2:])


def test_byte_budget_is_bounded_and_next_round_can_finish_exact_remaining_snapshot(local, monkeypatch):
    increment = job('byte-budget'); publication(increment['job_id'])
    first, original = capture('2026-10-01')
    second, _ = capture('2026-10-02')
    monkeypatch.setattr(retention, 'MAX_BYTES', len(original['payload_json'].encode()) + 1)
    result = retention.after_increment(increment)
    assert result['purged_snapshots'] == 1 and result['warnings']
    assert not exists(first) and exists(second)
    assert retention.after_increment(increment)['purged_snapshots'] == 1
    assert not exists(second)


@pytest.mark.parametrize('damage', ['outer_sha', 'inner_body', 'inner_bytes', 'missing_evidence', 'extra_metadata'])
def test_invalid_original_closure_is_retained_even_if_outer_sha_is_recomputed(local, damage):
    increment = job('corrupt-original'); publication(increment['job_id'])
    identifier, original = capture()
    value = json.loads(original['payload_json'])
    if damage == 'inner_body':
        value['evidence'][1]['body']['data']['item'][0]['auction_price'] = 99
    elif damage == 'inner_bytes':
        value['evidence'][1]['bytes'] += 1
    elif damage == 'missing_evidence':
        value['evidence'] = [item for item in value['evidence'] if item['name'] != 'auction']
    elif damage == 'extra_metadata':
        value['evidence'] = [item for item in value['evidence'] if item['name'] != 'benchmark']
    encoded = encode(value)
    with core.connect() as connection:
        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='retain_auction_snapshot_update'").fetchone()[0]
        connection.execute('DROP TRIGGER retain_auction_snapshot_update')
        connection.execute('UPDATE market_auction_snapshot SET payload_json=?,content_sha256=? WHERE snapshot_id=?',
            (encoded.decode(), '0' * 64 if damage == 'outer_sha' else sha256(encoded).hexdigest(), identifier))
        connection.execute(trigger)
    result = retention.after_increment(increment)
    assert result['warnings'] and result['purged_snapshots'] == 0 and exists(identifier)
    assert receipt(identifier)['status'] == 'warning'
    with core.connect() as connection:
        assert connection.execute('SELECT status FROM job WHERE job_id=?', (increment['job_id'],)).fetchone()[0] == 'succeeded'


def test_ordinary_connections_cannot_delete_replace_or_update_immutable_snapshot(local):
    identifier, original = capture()
    for statement in ('DELETE FROM market_auction_snapshot WHERE snapshot_id=?',
                      'UPDATE market_auction_snapshot SET quality=quality WHERE snapshot_id=?'):
        with pytest.raises(sqlite3.DatabaseError):
            with core.connect() as connection:
                connection.execute(statement, (identifier,))
    with pytest.raises(sqlite3.DatabaseError):
        with core.connect() as connection:
            connection.execute('INSERT OR REPLACE INTO market_auction_snapshot VALUES(?,?,?,?,?,?,?,?,?)', tuple(original.values()))
    assert exists(identifier)


def test_purged_state_is_explicit_in_all_local_readers_and_standard_restore(local, monkeypatch):
    from app.services import auction_service, auction_topic_summary, auction_feedback, auction_resonance, auction_close_review
    increment = job('backup-tombstone'); publication(increment['job_id'])
    expired, _ = capture()
    retained, original = capture(BOUNDARY)
    assert retention.after_increment(increment)['purged_snapshots'] == 1
    monkeypatch.setattr(auction_service, '_configured', lambda _connection: False)
    view = auction_service.read_view(EXPIRED)
    assert view['state'] == 'purged' and view['snapshot'] is None
    assert view['retention']['notice'] and not view['can_capture'] and not view['can_retry']
    assert next(item for item in auction_service.list_dates()['items'] if item['date'] == EXPIRED)['state'] == 'purged'
    for reader in (auction_topic_summary.read_view, auction_feedback.read_view, auction_resonance.read_view):
        assert reader(EXPIRED)['state'] == 'purged'
    assert auction_close_review.read_close_review(EXPIRED)['state'] == 'purged'
    before = receipt(expired)
    monkeypatch.syspath_prepend(str(core.PROJECT_ROOT / 'scripts'))
    from backup import perform_backup
    from restore import perform_restore, verify_backup
    bundle, restored = local / 'bundle', local / 'restored'
    perform_backup(core.data_dir(), bundle)
    verify_backup(bundle)
    perform_restore(bundle, restored)
    monkeypatch.setenv('SHIXU_DATA_DIR', str(restored))
    assert not exists(expired) and exists(retained) and receipt(expired) == before
    assert auction_service.read_view(EXPIRED)['state'] == 'purged'
    with core.connect() as connection:
        after = dict(connection.execute('SELECT * FROM market_auction_snapshot WHERE snapshot_id=?', (retained,)).fetchone())
        assert after == original
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 2
    validate_snapshot(after)


def test_automatic_projection_keeps_purged_slot_terminal_and_once_disabled(local, monkeypatch):
    from app.services import auction_automation as automation
    from app.services.auction_collection import CHINA
    increment = job('automatic-tombstone'); publication(increment['job_id'])
    identifier, _ = capture(automatic=True)
    assert retention.after_increment(increment)['purged_snapshots'] == 1
    monkeypatch.setattr(automation, 'now_china', lambda: datetime.fromisoformat(EXPIRED + 'T09:26:00').replace(tzinfo=CHINA))
    monkeypatch.setattr(automation, '_eligibility', lambda _connection: (True, None))
    # Preserve an old immutable slot/config without using a now-rejected writer.
    with core.connect() as connection:
        connection.execute("UPDATE market_auction_automation SET mode='once',trade_date=?,instrument_ids_json=?,revision=1 WHERE singleton=1",
                           (EXPIRED, json.dumps([CODE])))
    result = automation.read_view()
    assert result['last_run']['state'] == 'purged' and result['last_run']['retry_after_at_ms'] is None
    assert result['enabled'] is False and result['next_run_at'] is None
    automation.tick()
    assert not exists(identifier)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM market_auction_automation_run').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 2


def test_standard_backup_and_restore_reject_invalid_tombstone_even_after_manifest_rehash(local, monkeypatch):
    increment = job('tombstone-integrity'); publication(increment['job_id'])
    identifier, _ = capture()
    assert retention.after_increment(increment)['purged_snapshots'] == 1
    monkeypatch.syspath_prepend(str(core.PROJECT_ROOT / 'scripts'))
    from backup import perform_backup
    from restore import perform_restore, verify_backup
    bundle = local / 'invalid-tombstone-bundle'
    perform_backup(core.data_dir(), bundle)
    database = bundle / 'business/app.sqlite3'
    # 模拟离线损坏备份，不借助授权函数更改业务回执。
    with sqlite3.connect(database) as connection:
        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='guard_auction_purge_update'").fetchone()[0]
        connection.execute('DROP TRIGGER guard_auction_purge_update')
        connection.execute('UPDATE market_auction_purge SET evidence_count=1 WHERE snapshot_id=?', (identifier,))
        connection.execute(trigger)
    path = bundle / 'backup-manifest.json'
    manifest = json.loads(path.read_text())
    item = next(row for row in manifest['files'] if row['path'] == 'business/app.sqlite3')
    item.update(bytes=database.stat().st_size, sha256=sha256(database.read_bytes()).hexdigest())
    path.write_bytes(encode(manifest))
    with pytest.raises(ValueError, match='Auction'):
        verify_backup(bundle)
    restored = local / 'must-not-restore'
    with pytest.raises(ValueError, match='Auction'):
        perform_restore(bundle, restored)
    assert not restored.exists()


@pytest.mark.parametrize('policy', [LEGACY_POLICY, retention.VERSION])
def test_worker_dispatch_cleans_only_after_committed_success_and_replay_is_idempotent(local, monkeypatch, policy):
    from app import worker
    from app.services import sync_service, task_service as tasks, workspace_automation
    identifier, _ = capture()
    queued = job('real-worker-increment', status='queued', extra={'auction_retention_policy': policy})
    for name in ('_queue_followup', '_queue_local_sync_checks', '_noon_close_after_increment'):
        monkeypatch.setattr(worker, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workspace_automation, 'run_after_sync', lambda *_args: None)
    def execute_sync(owner, _callback):
        assert exists(identifier)
        with core.connect() as connection:
            assert connection.execute('SELECT status FROM job WHERE job_id=?', (owner,)).fetchone()[0] == 'running'
        publication(owner)
        assert exists(identifier)
        return {'quality': 'ready', 'message': 'synthetic published market'}
    monkeypatch.setattr(sync_service, 'execute_sync', execute_sync)
    claimed = tasks.claim_next('auction-retention-fixture-worker')
    assert claimed['job_id'] == queued['job_id']
    worker.dispatch(claimed)
    assert tasks.task_view(queued['job_id'])['status'] == 'succeeded'
    assert not exists(identifier)
    before = receipt(identifier)
    assert before['cutoff_date'] == (LEGACY_BOUNDARY if policy == LEGACY_POLICY else BOUNDARY)
    worker.dispatch(claimed)
    assert receipt(identifier) == before


def test_platform_freezes_new_increment_policy_without_requesting_data(local, monkeypatch):
    from app.routers import platform
    from app.services import sync_clock
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(sync_clock, 'require_new_increment', lambda: None)
    monkeypatch.setattr(sync_clock, 'safe_completion_date', lambda: ANCHOR)
    def adapter(*_args, **_kwargs):
        calls.append('select-configured-adapter-only')
        return SimpleNamespace(source_id='fuyao', key='synthetic-non-production-value')
    monkeypatch.setattr(source_service, 'client', adapter)
    created = platform.sync({'mode': 'increment', 'request_key': 'new-manual-retention-plan'})
    assert created['parameters']['auction_retention_policy'] == retention.VERSION
    assert created['status'] == 'queued' and calls == ['select-configured-adapter-only']
    with core.connect() as connection:
        frozen = json.loads(connection.execute('SELECT parameters_json FROM job WHERE job_id=?', (created['job_id'],)).fetchone()[0])
        assert frozen['auction_retention_policy'] == retention.VERSION
