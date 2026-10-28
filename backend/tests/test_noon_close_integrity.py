from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from app.services.noon_close_integrity import validate_close_reviews
from app.services.market_storage_gc import _roots

DAY = '2026-10-28'
MIGRATIONS = Path(__file__).resolve().parents[1] / 'app/migrations'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def checksum(value):
    return sha256(value.encode()).hexdigest()


def insert_review(connection, snapshot='noon-fixture', job='increment'):
    item = {'instrument_id': '600001.SH', 'name': '合成股票', 'board': 'main',
            'transition': 'resealed', 'final_board_count': 3, 'afternoon_change_pct': 4.0,
            'close_change_pct': 10.0, 'noon_change_pct': 5.5, 'close_amount': 10.0,
            'sections': ['events', 'relay', 'tomorrow'], 'classification_keys': ['fixture-concept']}
    saved_item = encoded(item)
    inputs = encoded({'date': DAY, 'stocks': [{'instrument_id': item['instrument_id'], 'close': 11.0}]})
    provenance = encoded({'source_snapshot_id': 'market-fixture-old', 'kind': 'derived_day_evidence'})
    result = encoded({'version': 'noon-close-v1', 'date': DAY,
                      'items_count': 1, 'items_sha256': checksum('[' + saved_item + ']'),
                      'provenance_sha256': checksum(provenance), 'summary': {},
                      'tomorrow': {'groups': [{'key': 'resealed', 'label': '昨日回封',
                                              'count': 1, 'instrument_ids': [item['instrument_id']]}]}})
    parent = connection.execute('SELECT content_sha256 FROM market_noon_snapshot WHERE snapshot_id=?', (snapshot,)).fetchone()[0]
    connection.execute('INSERT INTO market_noon_close_review VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('close-fixture', snapshot, DAY, 1, 'noon-close-v1', 2000, job,
                        'market-fixture-old', parent, checksum(inputs), checksum(result), 'available', 0,
                        inputs, provenance, result))
    connection.execute('INSERT INTO market_noon_close_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('close-fixture', snapshot, item['instrument_id'], item['name'], 'main', 'resealed',
                        3, 4.0, 10.0, 5.5, 10.0, encoded(item['sections']),
                        encoded(item['classification_keys']), saved_item, checksum(saved_item)))
    connection.commit()
    return item


@pytest.fixture
def evidence():
    c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row
    for option in ('foreign_keys=ON', 'recursive_triggers=ON', 'busy_timeout=10000'):
        c.execute('PRAGMA ' + option)
    c.create_function('noon_purge_allowed', 1, lambda *_: 0)
    c.executescript('''CREATE TABLE job(job_id TEXT PRIMARY KEY,kind TEXT,status TEXT,parameters_json TEXT);
        CREATE TABLE published_head(snapshot_id TEXT);
        CREATE TABLE retention_pin(snapshot_id TEXT,expires_at_ms INTEGER);''')
    c.executemany('INSERT INTO job VALUES(?,?,?,?)', [('capture', 'source_check', 'succeeded', '{}'), ('increment', 'increment', 'succeeded', '{}')])
    c.executescript((MIGRATIONS / '027_noon_snapshots.sql').read_text())
    c.executescript((MIGRATIONS / '029_noon_close_reviews.sql').read_text())
    body = encoded({'version': 'noon-snapshot-v1', 'date': DAY})
    c.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (DAY, 'capture', 'capture', 1000))
    c.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)',
              ('noon-fixture', DAY, 'capture', DAY + 'T11:31:00+08:00', DAY + 'T11:32:00+08:00',
               1000, 'partial', checksum(body), body, '[]'))
    insert_review(c)
    yield c
    c.close()


def test_self_contained_evidence_has_no_generation_pin_but_real_business_reference_does(evidence):
    assert validate_close_reviews(evidence) == {'reviews': 1, 'items': 1}
    snapshots = [{'snapshot_id': 'daily-old', 'lineage_json': encoded({'market_snapshot_id': 'market-fixture-old'})}]
    assert 'market-fixture-old' not in _roots(evidence, snapshots, 9999)
    evidence.execute('CREATE TABLE user_fixture(payload_json TEXT)')
    evidence.execute('INSERT INTO user_fixture VALUES(?)', (encoded({'source': 'market-fixture-old'}),))
    assert 'market-fixture-old' in _roots(evidence, snapshots, 9999)


@pytest.mark.parametrize('kind', ['input', 'result', 'provenance', 'numeric', 'missing_item'])
def test_corrupt_derived_evidence_blocks_backup_or_gc_instead_of_ignoring_a_reference(evidence, kind):
    evidence.execute('DROP TRIGGER retain_noon_close_update')
    evidence.execute('DROP TRIGGER retain_noon_close_item_update')
    if kind == 'input':
        evidence.execute("UPDATE market_noon_close_review SET input_json='{}'")
    elif kind == 'result':
        evidence.execute("UPDATE market_noon_close_review SET result_json='{}'")
    elif kind == 'provenance':
        evidence.execute("UPDATE market_noon_close_review SET provenance_json='{}'")
    elif kind == 'numeric':
        evidence.execute('UPDATE market_noon_close_item SET amount=99')
    else:
        evidence.create_function('noon_purge_allowed', 1, lambda *_: 1)
        evidence.execute('DELETE FROM market_noon_close_item')
    with pytest.raises(ValueError, match='Noon close'):
        validate_close_reviews(evidence)
    with pytest.raises(ValueError, match='Noon close'):
        _roots(evidence, [], 9999)


def test_parent_purge_removes_owned_evidence_without_freeing_daily_ledger(evidence):
    with pytest.raises(sqlite3.IntegrityError):
        evidence.execute("DELETE FROM market_noon_snapshot WHERE snapshot_id='noon-fixture'")
    evidence.rollback()
    evidence.create_function('noon_purge_allowed', 1, lambda sid: int(sid == 'noon-fixture'))
    evidence.execute("DELETE FROM market_noon_snapshot WHERE snapshot_id='noon-fixture'")
    assert validate_close_reviews(evidence) == {'reviews': 0, 'items': 0}
    assert evidence.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1


def test_old_schema_is_still_accepted_without_optional_close_tables():
    c = sqlite3.connect(':memory:')
    assert validate_close_reviews(c) == {'reviews': 0, 'items': 0}
    c.execute('CREATE TABLE market_noon_close_review(review_id TEXT)')
    with pytest.raises(ValueError, match='schema is incomplete'):
        validate_close_reviews(c)
    c.close()
