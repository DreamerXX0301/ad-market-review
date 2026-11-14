"""Candidate evidence storage and retention use only isolated SQLite fixtures."""

from contextlib import closing
import json
import sqlite3
import zlib

import pytest

from app.services import auction_retention, market_storage_gc, noon_retention, supplement_retention
from app.services import selection_candidate_storage as storage


def test_candidate_codec_keeps_original_json_text_and_sqlite_row_support():
    evidence = (
        '\n {"reason" : "\\u4e0a", "exact" : 3.0e+2} \t',
        json.dumps({'说明': '日线\t证据' * 200, 'known': None}, ensure_ascii=False),
        '\n ["题材", "\\u4e2d", "\\\\"] ',
    )
    blob = storage.pack_evidence(*evidence)
    assert blob is not None
    assert storage.unpack_evidence(blob) == evidence
    assert len(blob) + len('{}{}[]') < sum(len(field.encode()) for field in evidence)
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.row_factory = sqlite3.Row
        legacy = connection.execute(
            'SELECT ? AS checks_json, ? AS metrics_json, ? AS topic_snapshot_json', evidence).fetchone()
        packed = connection.execute(
            "SELECT '{}' AS checks_json, '{}' AS metrics_json, '[]' AS topic_snapshot_json, ? AS packed_evidence", (blob,)).fetchone()
        assert storage.candidate_evidence_json(legacy) == storage.candidate_evidence_json(packed) == evidence
        assert storage.candidate_evidence_json(dict(packed)) == evidence
        assert storage.candidate_evidence(legacy) == storage.candidate_evidence(packed) == tuple(map(json.loads, evidence))


def test_small_and_oversized_evidence_keeps_legacy_storage(monkeypatch):
    monkeypatch.setattr(storage, '_MAX_FRAME_BYTES', 256)
    assert storage.pack_evidence('{}', '{}', '[]') is None
    # Byte limit, then framing limit. Both stay in the original text columns.
    assert storage.pack_evidence('{}', '"' + '研' * 100 + '"', '[]') is None
    metrics = json.dumps({'reason': '"' * 100})
    assert len(metrics) < 256 and storage.pack_evidence('{}', metrics, '[]') is None
    assert storage.pack_evidence('{}', '"' + 'x' * 300 + '"', '[]') is None


def test_corrupt_unsupported_and_oversized_frames_fail_closed(monkeypatch):
    blob = storage.pack_evidence('{}', json.dumps({'line': 'a' * 1000}), '[]')
    assert blob is not None
    bad_frame = blob[:4] + zlib.compress(b'["{}",true,"[]"]')
    checksum = blob[:-1] + bytes([blob[-1] ^ 1])
    for invalid in (b'', b'SCE\xff' + blob[4:], blob[:-3], checksum, blob + blob[4:], bad_frame):
        with pytest.raises(ValueError, match='压缩证据无效'):
            storage.candidate_evidence_json({'packed_evidence': invalid, 'checks_json': '{}',
                                             'metrics_json': '{}', 'topic_snapshot_json': '[]'})
    monkeypatch.setattr(storage, '_MAX_FRAME_BYTES', 128)
    with pytest.raises(ValueError, match='压缩证据无效'):
        storage.unpack_evidence(blob)  # The compressed body fits, its decoded frame does not.
    bad_json = blob[:4] + zlib.compress(b'["{}","bad","[]"]')
    with pytest.raises(ValueError, match='JSON 无效'):
        storage.candidate_evidence({'packed_evidence': bad_json})


@pytest.mark.parametrize('layout', ['legacy', 'packed', 'corrupt'])
def test_retention_scans_frozen_candidate_evidence_in_all_layouts(layout, monkeypatch):
    generation = 'market-' + 'a' * 24
    snapshots = [{'snapshot_id': 'old-snapshot', 'lineage_json': json.dumps({'market_snapshot_id': generation})}]
    auction_id, noon_id = 'auction_' + 'a' * 32, 'noon_' + 'b' * 32
    auction = [{'snapshot_id': auction_id, 'job_id': 'capture-auction'}]
    noon = [{'snapshot_id': noon_id, 'job_id': 'capture-noon', 'sources_json': '[]'}]
    evidence = (json.dumps({'generation': generation}),
                json.dumps({'auction': auction_id, 'details': '冻结依据' * 200}, ensure_ascii=False),
                json.dumps([{'noon': noon_id}]))
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript('''
            CREATE TABLE selection_candidate(checks_json TEXT, metrics_json TEXT, topic_snapshot_json TEXT);
            CREATE TABLE published_head(snapshot_id TEXT);
            CREATE TABLE retention_pin(snapshot_id TEXT, expires_at_ms INTEGER, owner_kind TEXT);
            CREATE TABLE job(job_id TEXT, kind TEXT, status TEXT, target_date TEXT, parameters_json TEXT,
                parameters_hash TEXT, lease_owner TEXT, lease_expires_at_ms INTEGER);
            CREATE TABLE dataset_snapshot(snapshot_id TEXT, lineage_json TEXT, state TEXT);
            CREATE TABLE supplement_retention_run(job_id TEXT, version TEXT);
            CREATE TABLE business_clock(singleton INTEGER, business_date TEXT);
            INSERT INTO business_clock VALUES (1,'2026-11-27');
        ''')
        connection.execute('INSERT INTO dataset_snapshot VALUES (?, ?, ?)',
                           (snapshots[0]['snapshot_id'], snapshots[0]['lineage_json'], 'published'))
        connection.execute("INSERT INTO job VALUES ('increment','increment','succeeded','2026-11-27',?,'hash',NULL,NULL)",
                           (json.dumps({'retention_policy_version': supplement_retention.VERSION}),))
        if layout == 'legacy':
            connection.execute('INSERT INTO selection_candidate VALUES (?, ?, ?)', evidence)
        else:
            connection.execute('ALTER TABLE selection_candidate ADD COLUMN packed_evidence BLOB')
            blob = storage.pack_evidence(*evidence)
            assert blob is not None
            if layout == 'corrupt':
                blob = blob[:-1]
            connection.execute("INSERT INTO selection_candidate VALUES ('{}', '{}', '[]', ?)", (blob,))
        connection.commit()
        monkeypatch.setattr(supplement_retention, 'connect', lambda: connection)

        probes = (lambda: market_storage_gc._roots(connection, snapshots, 1),
                  lambda: auction_retention._protected(connection, auction),
                  lambda: noon_retention._protected(connection, noon),
                  lambda: supplement_retention._database_scope('increment'))
        if layout == 'corrupt':
            for probe in probes:
                with pytest.raises(ValueError, match='压缩证据无效'):
                    probe()
        else:
            assert probes[0]() == {generation}
            assert probes[1]() == ({auction_id}, None)
            assert probes[2]() == ({noon_id}, None)
            state, reason = probes[3]()
            assert reason is None and state['protected_generations'] == [generation]
            # Only the original decoded strings count against the auction scan
            # budget, and representing them as a blob must not add a row.
            monkeypatch.setattr(auction_retention, '_INTERNAL', auction_retention._INTERNAL | {'business_clock', 'dataset_snapshot'})
            monkeypatch.setattr(auction_retention, 'MAX_REFERENCE_ROWS', 1)
            monkeypatch.setattr(auction_retention, 'MAX_REFERENCE_BYTES', sum(len(text.encode()) for text in evidence))
            assert probes[1]() == ({auction_id}, None)
            monkeypatch.setattr(auction_retention, 'MAX_REFERENCE_BYTES', sum(len(text.encode()) for text in evidence) - 1)
            with pytest.raises(ValueError, match='有界预算'):
                probes[1]()
