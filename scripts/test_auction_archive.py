"""竞价内嵌原件随标准备份独立恢复；仅使用临时合成业务与来源。"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

import pytest

from test_deployment import source
from backup import connect_sqlite, perform_backup, sha
from restore import perform_restore, verify_backup


ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-10-19'
OLD_DAY = '2025-01-02'
CODE = '600001.SH'
ACTIVE_JOB = 'job_' + '3' * 32
QUEUED_JOB = 'job_' + '4' * 32


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def payload_fixture(day, *, benchmark_received=True):
    """完整原件与元数据分别构造，避免导入应用配置或使用真实行情。"""
    start = datetime.fromisoformat(day + 'T09:25:10+08:00')
    end = start + timedelta(seconds=3)
    quote = {'thscode': CODE, 'ticker': CODE[:6], 'name': '竞价备份合成样本',
             'auction_price': 10.25, 'pre_close_price': 10, 'auction_pct': 2.5,
             'auction_volume': 100, 'auction_amount': 102500,
             'auction_turnover_pct': .1, 'auction_yesterday_ratio_pct': 5,
             'auction_volume_ratio': 1.5}
    definitions = [
        ('calendar', '/api/a-share/calendar/trading-days', {},
         {'item': [{'date': day.replace('-', '')}]}),
        ('auction', '/api/a-share/auction/snapshot',
         {'stage': 'final', 'thscodes': CODE},
         {'auction_phase': 'final', 'data_status': 'ready', 'total': 1,
          'item': [quote]}),
        ('benchmark', '/api/a-share/auction/short-term-benchmark', {'date': day},
         {'date': day, 'date_ms': int(start.replace(hour=0, minute=0, second=0).timestamp() * 1000),
          'item': [{'thscode': CODE, 'ticker': CODE[:6], 'name': quote['name'],
                    'auction_pct': 2.5, 'tags': ['合成来源标签']}]}),
    ]
    sources, evidence = [], []
    for index, (name, endpoint, params, data) in enumerate(definitions):
        requested = start + timedelta(seconds=index)
        received = requested + timedelta(milliseconds=100)
        record = {'name': name, 'source': 'fuyao', 'endpoint': endpoint,
                  'params': params, 'requested_at': requested.isoformat(),
                  'received_at': received.isoformat()}
        if name == 'benchmark' and not benchmark_received:
            sources.append({**record, 'state': 'failed', 'http_status': 503})
            continue
        body = {'code': 0, 'data': {**data, 'timestamp': int(received.timestamp() * 1000)}}
        original = encode(body)
        record.update(state='received', bytes=len(original), sha256=sha256(original).hexdigest())
        sources.append(deepcopy(record))
        evidence.append({**deepcopy(record), 'body': body})
    return {'version': 'auction-snapshot-v1', 'date': day,
            'collected_start_at': start.isoformat(), 'collected_end_at': end.isoformat(),
            'universe': {'mode': 'explicit', 'items': [{'instrument_id': CODE,
                         'name': quote['name'], 'reasons': ['合成测试明确指定']}]},
            'quotes': {'state': 'observed', 'items': [{
                'instrument_id': CODE, 'name': quote['name'], 'state': 'observed',
                **{key: value for key, value in quote.items() if key.startswith('auction_')},
                'pre_close_price': 10, 'quote_at': None}]},
            'benchmark': {'state': 'observed' if benchmark_received else 'unknown',
                          'date': day, 'items': []},
            'gaps': [] if benchmark_received else [{'domain': 'benchmark', 'reason': '合成来源失败'}],
            'sources': sources, 'evidence': evidence}


def insert_capture(connection, day, job_id, status, payload=None):
    stamp = int(datetime.fromisoformat(day + 'T09:25:10+08:00').timestamp() * 1000)
    parameters = {'operation': 'auction_snapshot', 'version': 'auction-snapshot-v1',
                  'source_id': 'fuyao', 'date': day, 'stage': 'final',
                  'instrument_ids': [CODE], 'max_symbols': 100,
                  'window_end_at': day + 'T09:30:00+08:00'}
    encoded_parameters = encode(parameters)
    connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,
        parameters_json,parameters_hash,status,lease_owner,lease_expires_at_ms,created_at_ms)
        VALUES(?,?,?,'source_check',?,?,?,?,?,?,?)''',
        (job_id, job_id, job_id, day, encoded_parameters.decode(),
         sha256(encoded_parameters).hexdigest(), status,
         'old-auction-worker' if status == 'running' else None,
         stamp + 60000 if status == 'running' else None, stamp))
    connection.execute('INSERT INTO market_auction_capture VALUES(?,?,?,?)',
                       (day, job_id, job_id, stamp))
    if payload is not None:
        encoded = encode(payload)
        quality = 'partial' if payload['gaps'] else 'complete'
        connection.execute('INSERT INTO market_auction_snapshot VALUES(?,?,?,?,?,?,?,?,?)',
            ('auction_' + day.replace('-', ''), day, job_id,
             payload['collected_start_at'], payload['collected_end_at'], stamp + 3000,
             quality, sha256(encoded).hexdigest(), encoded.decode()))


@pytest.fixture
def auction_source(source, monkeypatch):
    monkeypatch.delenv('SHIXU_RAW_DIR', raising=False)
    with connect_sqlite(source / 'business/app.sqlite3') as connection:
        connection.executescript((ROOT / 'backend/app/migrations/030_auction_snapshots.sql').read_text())
        # 基础部署fixture已有旧运行任务；竞价用例仍遵守全库单一运行者约束。
        connection.execute("UPDATE job SET status='paused',lease_owner=NULL,lease_expires_at_ms=NULL WHERE status='running'")
        insert_capture(connection, DAY, 'job_' + '1' * 32, 'succeeded', payload_fixture(DAY))
        insert_capture(connection, OLD_DAY, 'job_' + '2' * 32, 'partial',
                       payload_fixture(OLD_DAY, benchmark_received=False))
        insert_capture(connection, '2026-10-20', ACTIVE_JOB, 'running')
        insert_capture(connection, '2026-10-21', QUEUED_JOB, 'queued')
    return source


def stored_snapshots(database):
    with connect_sqlite(database, 'ro') as connection:
        return [tuple(row) for row in connection.execute('''SELECT snapshot_id,trade_date,
            quality,content_sha256,payload_json FROM market_auction_snapshot ORDER BY trade_date''')]


def mutate_payload(database, damage):
    """模拟离线存储损坏；保留原DDL，并故意重算外层正文SHA。"""
    with connect_sqlite(database) as connection:
        row = connection.execute('SELECT payload_json FROM market_auction_snapshot WHERE trade_date=?', (DAY,)).fetchone()
        payload = json.loads(row['payload_json'])
        if damage == 'inner_body':
            payload['evidence'][1]['body']['data']['item'][0]['auction_price'] = 11
        elif damage == 'inner_sha':
            payload['evidence'][1]['sha256'] = '0' * 64
        elif damage == 'inner_bytes':
            payload['evidence'][1]['bytes'] += 1
        elif damage == 'evidence_missing':
            payload['evidence'] = [item for item in payload['evidence'] if item['name'] != 'auction']
        elif damage == 'metadata_missing':
            payload['sources'] = [item for item in payload['sources'] if item['name'] != 'auction']
        elif damage == 'metadata_extra':
            # 基准原件被移除，但其received元数据仍在；不是合法的来源失败。
            payload['evidence'] = [item for item in payload['evidence'] if item['name'] != 'benchmark']
        elif damage == 'metadata_duplicate':
            payload['sources'][2] = deepcopy(payload['sources'][0])
        elif damage == 'request_scope':
            for entries in (payload['sources'], payload['evidence']):
                next(item for item in entries if item['name'] == 'auction')['params']['stage'] = 'live'
        else:
            raise AssertionError('unhandled damage ' + damage)
        encoded = encode(payload)
        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='retain_auction_snapshot_update'").fetchone()[0]
        connection.execute('DROP TRIGGER retain_auction_snapshot_update')
        connection.execute('UPDATE market_auction_snapshot SET payload_json=?,content_sha256=? WHERE trade_date=?',
                           (encoded.decode(), sha256(encoded).hexdigest(), DAY))
        connection.execute(trigger)


def rehash_database_manifest(bundle):
    """外层备份清单也重算，确保拒绝原因来自竞价证据校验。"""
    path = bundle / 'backup-manifest.json'
    manifest = json.loads(path.read_text())
    database = bundle / 'business/app.sqlite3'
    item = next(item for item in manifest['files'] if item['path'] == 'business/app.sqlite3')
    item.update(bytes=database.stat().st_size, sha256=sha(database))
    path.write_text(json.dumps(manifest, ensure_ascii=False))


def test_standard_roundtrip_keeps_old_partial_and_complete_embedded_originals(auction_source, tmp_path):
    data = auction_source
    before = stored_snapshots(data / 'business/app.sqlite3')
    bundle = tmp_path / 'bundle'
    result = perform_backup(data, bundle)
    manifest = verify_backup(bundle)
    assert result['master_key_included'] is False
    assert not any(item['path'].startswith('raw/') for item in manifest['files'])
    assert not (bundle / 'work').exists()
    restored = tmp_path / 'restored'
    perform_restore(bundle, restored)
    after = stored_snapshots(restored / 'business/app.sqlite3')
    assert after == before
    assert [(row[1], row[2]) for row in after] == [(OLD_DAY, 'partial'), (DAY, 'complete')]
    for _, _, _, expected_sha, encoded in after:
        assert sha256(encoded.encode()).hexdigest() == expected_sha
        payload = json.loads(encoded)
        for item in payload['evidence']:
            original = encode(item['body'])
            assert sha256(original).hexdigest() == item['sha256'] and len(original) == item['bytes']
    assert [len(json.loads(row[4])['evidence']) for row in after] == [2, 3]
    with connect_sqlite(restored / 'business/app.sqlite3', 'ro') as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 4


def test_restore_pauses_active_auction_jobs_without_creating_or_collecting(auction_source, tmp_path):
    data = auction_source
    with connect_sqlite(data / 'business/app.sqlite3', 'ro') as connection:
        before_jobs = {row['job_id'] for row in connection.execute('SELECT job_id FROM job')}
    before_snapshots = stored_snapshots(data / 'business/app.sqlite3')
    bundle, restored = tmp_path / 'bundle', tmp_path / 'restored'
    perform_backup(data, bundle)
    report = perform_restore(bundle, restored)
    assert {ACTIVE_JOB, QUEUED_JOB} <= set(report['paused_jobs'])
    assert stored_snapshots(restored / 'business/app.sqlite3') == before_snapshots
    with connect_sqlite(restored / 'business/app.sqlite3', 'ro') as connection:
        assert {row['job_id'] for row in connection.execute('SELECT job_id FROM job')} == before_jobs
        assert connection.execute("SELECT count(*) FROM job WHERE status IN ('queued','running')").fetchone()[0] == 0
        for job_id in (ACTIVE_JOB, QUEUED_JOB):
            row = connection.execute('SELECT status,phase,lease_owner,lease_expires_at_ms FROM job WHERE job_id=?', (job_id,)).fetchone()
            assert tuple(row) == ('paused', 'restored_waiting_for_manual_resume', None, None)
            assert connection.execute('SELECT count(*) FROM market_auction_snapshot WHERE job_id=?', (job_id,)).fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM job_event WHERE job_id=? AND stage='restored'", (job_id,)).fetchone()[0] == 1
    with connect_sqlite(data / 'business/app.sqlite3', 'ro') as connection:
        assert connection.execute('SELECT status FROM job WHERE job_id=?', (ACTIVE_JOB,)).fetchone()[0] == 'running'
        assert connection.execute('SELECT status FROM job WHERE job_id=?', (QUEUED_JOB,)).fetchone()[0] == 'queued'
    assert list((restored / 'raw').iterdir()) == []


DAMAGES = ['inner_body', 'inner_sha', 'inner_bytes', 'evidence_missing',
           'metadata_missing', 'metadata_extra', 'metadata_duplicate', 'request_scope']


@pytest.mark.parametrize('damage', DAMAGES)
def test_backup_rejects_inner_damage_even_with_recomputed_payload_sha(auction_source, tmp_path, damage):
    mutate_payload(auction_source / 'business/app.sqlite3', damage)
    bundle = tmp_path / 'must-not-exist'
    with pytest.raises(ValueError, match='Auction'):
        perform_backup(auction_source, bundle)
    assert not bundle.exists()
    with connect_sqlite(auction_source / 'business/app.sqlite3', 'ro') as connection:
        assert connection.execute("SELECT count(*) FROM retention_pin WHERE owner_kind='backup'").fetchone()[0] == 0


@pytest.mark.parametrize('damage', DAMAGES)
def test_restore_rejects_inner_damage_even_with_recomputed_outer_manifests(auction_source, tmp_path, damage):
    bundle = tmp_path / 'bundle'
    perform_backup(auction_source, bundle)
    mutate_payload(bundle / 'business/app.sqlite3', damage)
    rehash_database_manifest(bundle)
    with pytest.raises(ValueError, match='Auction'):
        verify_backup(bundle)
    restored = tmp_path / 'must-not-exist'
    with pytest.raises(ValueError, match='Auction'):
        perform_restore(bundle, restored)
    assert not restored.exists()


def test_legacy_backup_without_auction_tables_still_restores(source, tmp_path):
    bundle, restored = tmp_path / 'legacy', tmp_path / 'restored'
    perform_backup(source, bundle)
    verify_backup(bundle)
    perform_restore(bundle, restored)
    with connect_sqlite(restored / 'business/app.sqlite3', 'ro') as connection:
        assert connection.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'market_auction_%'").fetchone()[0] == 0
