"""Read-only, resumable delivery of worker-produced AI events."""
import asyncio
import json
import time

from fastapi import HTTPException
from app.core import connect

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted_manual_retry'}
EVENT_KINDS = {'status', 'tool', 'delta', 'done', 'error'}
MAX_CURSOR = 1_000_000


def resume_cursor(after=0, last_event_id=None):
    cursor = after
    if last_event_id:
        try:
            value = int(last_event_id)
        except (TypeError, ValueError):
            raise HTTPException(400, '无法读取回答位置，请重新打开对话') from None
        if not 0 <= value <= MAX_CURSOR:
            raise HTTPException(400, '回答位置超出范围')
        cursor = max(cursor, value)
    return cursor


def read_batch(task_id, after, limit=100):
    """Release SQLite before every network yield or wait; never renew history."""
    with connect() as connection:
        task = connection.execute(
            'SELECT task_id,session_id,state,result_json FROM ai_task WHERE task_id=?',
            (task_id,),
        ).fetchone()
        if task is None:
            raise HTTPException(404, '本次回答不存在')
        rows = connection.execute(
            'SELECT seq,kind,data_json,at_ms FROM ai_task_event '
            'WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?',
            (task_id, after, min(max(limit, 1), 100)),
        ).fetchall()
    try:
        metadata = json.loads(task['result_json'] or '{}')
    except (TypeError, ValueError):
        metadata = {}
    saved_seq = metadata.get('stream_last_seq', 0)
    if type(saved_seq) is not int or not 0 <= saved_seq <= MAX_CURSOR:
        saved_seq = 0
    snapshot = {
        'task_id': task_id, 'session_id': task['session_id'],
        'state': task['state'], 'last_seq': saved_seq,
        'message_id': metadata.get('message_id'),
    }
    events = []
    for row in rows:
        if row['kind'] not in EVENT_KINDS:
            continue
        try:
            data = json.loads(row['data_json'])
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        # Never let a provider string impersonate another task or event type.
        data.update(task_id=task_id, session_id=task['session_id'],
                    seq=row['seq'], type=row['kind'], at_ms=row['at_ms'])
        events.append((row['seq'], row['kind'], data))
    return snapshot, events


def frame(kind, data, seq=None):
    lines = [f'event: {kind}']
    if seq is not None:
        lines.append(f'id: {seq}')
    lines.append('data: ' + json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    return '\n'.join(lines) + '\n\n'


async def stream_events(request, task_id, after=0, *, interval=.15, heartbeat=10):
    cursor, ping = after, time.monotonic()
    yield ': connected\n\n'
    while not await request.is_disconnected():
        try:
            snapshot, events = read_batch(task_id, cursor)
        except HTTPException:
            yield frame('error', {'task_id': task_id, 'message': '本次回答已不可读取'})
            return
        for seq, kind, data in events:
            cursor = seq
            yield frame(kind, data, seq)
            if kind == 'done':
                return
        if snapshot['state'] in TERMINAL and len(events) < 100:
            # The worker may already have cleaned deltas. Fetching the final
            # message restores the complete answer without sending it again.
            last = max(snapshot['last_seq'], cursor)
            yield frame('done', {
                **snapshot, 'seq': last, 'type': 'done',
                'last_seq': last, 'final': True,
            }, max(last, 1))
            return
        now = time.monotonic()
        if now - ping >= heartbeat:
            ping = now
            yield ': ping\n\n'
        await asyncio.sleep(interval)
