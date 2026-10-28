import json
import sqlite3

import pytest
from fastapi import HTTPException

from app import core
from app.services import noon_notes as notes
from test_workspace import ws


@pytest.fixture
def snapshot(ws):
    market, _ = ws
    payload = json.dumps({'date': market.entry, 'version': 'noon-snapshot-v1'})
    with core.connect() as db:
        db.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (market.entry, 'fixture-job', 'fixture-noon-request', 1000))
        db.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)',
                   ('fixture-noon', market.entry, 'fixture-job', market.entry+'T11:31:00+08:00',
                    market.entry+'T11:32:00+08:00', 2000, 'partial', 'a'*64, payload, '[]'))
    return 'fixture-noon'


def test_user_notes_append_without_changing_quote_content(snapshot):
    assert notes.read_notes(snapshot)['revision'] == 0
    first = notes.save_note(snapshot, {'body': '下午观察上涨范围是否保持', 'expected_revision': 0})
    assert first['revision'] == 1
    assert notes.save_note(snapshot, {'body': first['body'], 'expected_revision': 1}) == first
    second = notes.save_note(snapshot, {'body': '', 'expected_revision': 1})
    assert second['revision'] == 2 and notes.read_notes(snapshot)['body'] == ''
    with core.connect() as db:
        assert db.execute('SELECT content_sha256 FROM market_noon_snapshot').fetchone()[0] == 'a'*64
        assert db.execute('SELECT count(*) FROM market_noon_note').fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM market_noon_note')


def test_stale_revision_or_backdated_client_time_is_rejected(snapshot):
    notes.save_note(snapshot, {'body': '保存的原文', 'expected_revision': 0})
    with pytest.raises(HTTPException) as error:
        notes.save_note(snapshot, {'body': '覆盖旧内容', 'expected_revision': 0})
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        notes.save_note(snapshot, {'body': '新内容', 'expected_revision': 1, 'recorded_at_ms': 1})
    assert error.value.status_code == 422


def test_worker_expiry_cascades_notes_but_never_erases_daily_slot(snapshot):
    notes.save_note(snapshot, {'body': '跟随午评保留的观察', 'expected_revision': 0})
    with core.connect() as db:
        db.create_function('noon_purge_allowed', 1, lambda identifier: int(identifier == snapshot))
        db.execute('DELETE FROM market_noon_snapshot WHERE snapshot_id=?', (snapshot,))
        assert db.execute('SELECT count(*) FROM market_noon_note').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1
    with pytest.raises(HTTPException) as error:
        notes.save_note(snapshot, {'body': '清理后不能补写', 'expected_revision': 0})
    assert error.value.status_code == 404
