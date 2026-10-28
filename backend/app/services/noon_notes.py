"""User observations are append-only notes, separate from immutable market facts."""
import re

from fastapi import HTTPException

from app.core import connect, new_id, now_ms
from app.services.increment_access import require_business_unlocked


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,120}', value):
        raise HTTPException(404, '午评快照不存在或已到期清理')
    return value


def _latest(connection, snapshot_id):
    if not connection.execute('SELECT 1 FROM market_noon_snapshot WHERE snapshot_id=?', (snapshot_id,)).fetchone():
        raise HTTPException(404, '午评快照不存在或已到期清理，不能补写观察')
    row = connection.execute('SELECT revision,body,recorded_at_ms FROM market_noon_note WHERE snapshot_id=? ORDER BY revision DESC LIMIT 1', (snapshot_id,)).fetchone()
    return {'snapshot_id': snapshot_id, 'revision': row['revision'] if row else 0,
            'body': row['body'] if row else '', 'recorded_at_ms': row['recorded_at_ms'] if row else None}


def read_notes(snapshot_id):
    snapshot_id = _identifier(snapshot_id)
    with connect() as connection:
        connection.execute('BEGIN')
        return _latest(connection, snapshot_id)


def save_note(snapshot_id, payload):
    snapshot_id = _identifier(snapshot_id)
    if not isinstance(payload, dict) or set(payload) != {'body', 'expected_revision'}:
        raise HTTPException(422, '观察仅接受正文和当前修订号，记录时间由服务器保存')
    body, expected = payload['body'], payload['expected_revision']
    if (not isinstance(body, str) or len(body) > 4000
            or any(ord(char) < 32 and char not in '\n\t\r' for char in body)):
        raise HTTPException(422, '观察正文最多4000字，不能含非法控制字符')
    if isinstance(expected, bool) or not isinstance(expected, int) or not 0 <= expected < 2147483647:
        raise HTTPException(422, 'expected_revision需要当前非负修订号')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        require_business_unlocked(connection)
        current = _latest(connection, snapshot_id)
        if current['revision'] != expected:
            raise HTTPException(409, '观察已在其他页面修改，请重新读取后再保存')
        if body == current['body']:
            return current
        stamp = now_ms()
        if stamp <= 0 or current['recorded_at_ms'] is not None and stamp < current['recorded_at_ms']:
            raise HTTPException(409, '系统时钟回退，未保存新的观察修订')
        connection.execute('INSERT INTO market_noon_note VALUES(?,?,?,?,?)',
                           (new_id('noon-note'), snapshot_id, expected + 1, body, stamp))
    return {'snapshot_id': snapshot_id, 'revision': expected + 1, 'body': body, 'recorded_at_ms': stamp}
