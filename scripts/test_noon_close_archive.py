"""Standard backup/restore includes close-review evidence without new raw files."""
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from test_deployment import source
from test_noon_archive import noon_source, DAY, JOB
from backup import perform_backup, sha
from restore import perform_restore, verify_backup

ROOT = Path(__file__).resolve().parents[1]


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return sha256(value.encode()).hexdigest()


@pytest.fixture
def close_source(noon_source):
    data, *_ = noon_source
    c = sqlite3.connect(data / 'business/app.sqlite3'); c.row_factory = sqlite3.Row
    for option in ('foreign_keys=ON', 'recursive_triggers=ON', 'busy_timeout=10000'):
        c.execute('PRAGMA ' + option)
    c.executescript((ROOT / 'backend/app/migrations/029_noon_close_reviews.sql').read_text())
    parent = c.execute("SELECT * FROM market_noon_snapshot WHERE snapshot_id='noon_fixture'").fetchone()
    item = {'instrument_id': '600001.SH', 'name': '合成回封股', 'board': 'main', 'transition': 'resealed',
            'final_board_count': 3, 'afternoon_change_pct': 4.0, 'close_change_pct': 10.0,
            'noon_change_pct': 5.5, 'close_amount': 10.0, 'sections': ['events', 'tomorrow'],
            'classification_keys': []}
    item_json = encode(item)
    inputs = encode({'date': DAY, 'stocks': [{'instrument_id': item['instrument_id'], 'close': 11.0}]})
    provenance = encode({'kind': 'derived_day_evidence', 'source_snapshot_id': 'fixture-closed-generation'})
    result = encode({'version': 'noon-close-v1', 'date': DAY, 'summary': {}, 'items_count': 1,
                     'items_sha256': digest('[' + item_json + ']'), 'provenance_sha256': digest(provenance)})
    c.execute('INSERT INTO market_noon_close_review VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              ('close_fixture', parent['snapshot_id'], DAY, 1, 'noon-close-v1', parent['recorded_at_ms'] + 1,
               JOB, 'fixture-closed-generation', parent['content_sha256'], digest(inputs), digest(result),
               'available', 0, inputs, provenance, result))
    c.execute('INSERT INTO market_noon_close_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              ('close_fixture', parent['snapshot_id'], item['instrument_id'], item['name'], 'main', 'resealed',
               3, 4.0, 10.0, 5.5, 10.0, encode(item['sections']), '[]', item_json, digest(item_json)))
    c.commit(); c.close()
    return data, item_json, inputs


def test_standard_restore_retains_self_contained_close_result_and_no_extra_raw(close_source, tmp_path):
    data, item_json, inputs = close_source
    bundle = tmp_path / 'bundle'; perform_backup(data, bundle)
    manifest = verify_backup(bundle)
    raw = [row['path'] for row in manifest['files'] if row['path'].startswith('raw/')]
    assert len(raw) == 2  # Only the original noon response and its original manifest.
    assert not any('close' in path for path in raw)
    target = tmp_path / 'restored'; perform_restore(bundle, target)
    c = sqlite3.connect(target / 'business/app.sqlite3')
    assert c.execute('SELECT input_json FROM market_noon_close_review').fetchone()[0] == inputs
    assert c.execute('SELECT item_json FROM market_noon_close_item').fetchone()[0] == item_json
    assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    c.close()


def test_rehashed_outer_backup_manifest_cannot_hide_corrupt_close_provenance(close_source, tmp_path):
    data, *_ = close_source
    bundle = tmp_path / 'bundle'; perform_backup(data, bundle)
    database = bundle / 'business/app.sqlite3'
    c = sqlite3.connect(database)
    c.execute('DROP TRIGGER retain_noon_close_update')
    c.execute("UPDATE market_noon_close_review SET provenance_json='{\"tampered\":true}'")
    c.commit(); c.close()
    path = bundle / 'backup-manifest.json'; manifest = json.loads(path.read_text())
    for row in manifest['files']:
        if row['path'] == 'business/app.sqlite3':
            row.update(bytes=database.stat().st_size, sha256=sha(database))
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Noon close'):
        verify_backup(bundle)
