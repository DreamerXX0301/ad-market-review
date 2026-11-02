"""SSE replay uses synthetic rows only; it never calls a model or market source."""
import asyncio
from contextlib import contextmanager
import json
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.routers.ai_events import router
from app.services import ai_event_read as reader


@pytest.fixture
def events(tmp_path, monkeypatch):
    path = tmp_path / 'events.sqlite3'
    writer = sqlite3.connect(path)
    writer.executescript("""
        CREATE TABLE ai_task(task_id TEXT PRIMARY KEY,session_id TEXT,state TEXT,result_json TEXT);
        CREATE TABLE ai_task_event(task_id TEXT,seq INTEGER,kind TEXT,data_json TEXT,at_ms INTEGER,
                                  PRIMARY KEY(task_id,seq));
        INSERT INTO ai_task VALUES('task','session','running','{}');
    """)
    writer.commit()

    @contextmanager
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        for statement in ('PRAGMA foreign_keys=ON', 'PRAGMA recursive_triggers=ON',
                          'PRAGMA busy_timeout=10000', 'PRAGMA query_only=ON'):
            connection.execute(statement)
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(reader, 'connect', connect)
    app = FastAPI()
    app.include_router(router)
    yield writer, TestClient(app)
    writer.close()


def add(writer, event_seq, kind='delta', **data):
    writer.execute('INSERT INTO ai_task_event VALUES(?,?,?,?,?)',
                   ('task', event_seq, kind, json.dumps(data), 100 + event_seq))
    writer.commit()


def test_resume_header_and_query_never_rewind():
    assert reader.resume_cursor(5, '3') == 5
    assert reader.resume_cursor(1, '8') == 8
    for value in ('nope', '-1', '1000001'):
        with pytest.raises(HTTPException):
            reader.resume_cursor(0, value)


def test_cursor_bounds_and_event_identity(events):
    writer, _ = events
    add(writer, 1, text='first')
    add(writer, 2, text='second', task_id='forged', session_id='forged', seq=99, type='done')
    snapshot, batch = reader.read_batch('task', 1)
    assert snapshot['state'] == 'running'
    assert len(batch) == 1
    seq, kind, data = batch[0]
    assert (seq, kind) == (2, 'delta')
    assert (data['task_id'], data['session_id'], data['seq'], data['type']) == ('task', 'session', 2, 'delta')


def test_frame_is_single_json_line_not_injectable():
    value = reader.frame('delta', {'text': '第一行\n\nevent: done\n第二行'}, 4)
    assert value.count('\ndata: ') == 1
    assert value.count('\nevent: done') == 0
    assert json.loads(value.split('data: ', 1)[1])['text'].startswith('第一行')


def test_stream_yields_before_completion_and_releases_db(events):
    writer, _ = events

    class Connected:
        async def is_disconnected(self):
            return False

    async def exercise():
        stream = reader.stream_events(Connected(), 'task', interval=.001)
        assert await anext(stream) == ': connected\n\n'
        add(writer, 1, text='第一段')
        first = await anext(stream)
        assert '第一段' in first and 'event: delta' in first
        assert writer.execute("SELECT state FROM ai_task").fetchone()[0] == 'running'
        # A separate writer can commit while the response generator is paused.
        writer.execute("UPDATE ai_task SET state='completed',result_json=?", (
            json.dumps({'stream_last_seq': 2, 'message_id': 'message'}),))
        writer.execute('DELETE FROM ai_task_event')
        writer.commit()
        final = await anext(stream)
        assert 'event: done' in final and '"message_id":"message"' in final
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(exercise())


def test_terminal_replay_is_get_only_and_does_not_mutate(events):
    writer, client = events
    writer.execute("UPDATE ai_task SET state='completed',result_json=?", (
        json.dumps({'stream_last_seq': 7, 'message_id': 'message'}),))
    writer.commit()
    before = list(writer.iterdump())
    with client.stream('GET', '/api/v1/ai/tasks/task/events?after=5',
                       headers={'Last-Event-ID': '6'}) as response:
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/event-stream')
        body = ''.join(response.iter_text())
    assert 'event: done' in body and 'id: 7' in body
    assert '"last_seq":7' in body
    assert list(writer.iterdump()) == before


def test_missing_and_invalid_requests_do_not_open_stream(events):
    _, client = events
    assert client.get('/api/v1/ai/tasks/missing/events').status_code == 404
    assert client.get('/api/v1/ai/tasks/task/events?after=-1').status_code == 422
    assert client.get('/api/v1/ai/tasks/task/events', headers={'Last-Event-ID': 'bad'}).status_code == 400
