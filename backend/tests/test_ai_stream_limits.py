import asyncio
import json
import threading

import httpx
import pytest

from app.adapters import ai_agent
from app.adapters.ai_responses import ProviderError
from app.routers.ai_events import router as event_router
from app.services.ai_catalog import MAX_RESPONSE_BYTES
from app.services.ai_runtime import AIRuntime
from test_ai_agent_round import complete, create, function, mock_network, modern_agent_client
from test_ai_service import drive, enable, fake


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class Deltas:
    def __init__(self):
        self.parts = []

    def emit(self, kind, data):
        assert kind == 'delta'
        self.parts.append(data['text'])


def frame(kind, **payload):
    data = json.dumps({'type': kind, **payload}, ensure_ascii=False, separators=(',', ':'))
    return f'event: {kind}\ndata: {data}\n\n'.encode()


def delta(text, sequence_number=0):
    return frame(
        'response.output_text.delta', sequence_number=sequence_number,
        item_id='msg_' + 'a' * 64, output_index=0, content_index=0,
        delta=text, logprobs=[],
    )


def terminal(text):
    return {
        'status': 'completed',
        'output': [{
            'type': 'message', 'role': 'assistant', 'status': 'completed',
            'content': [{'type': 'output_text', 'text': text, 'annotations': []}],
        }],
        'usage': {'input_tokens': 100, 'output_tokens': len(text)},
    }


def stream(chunks, bridge, max_tokens=12288):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, stream=Chunks(chunks), headers={'Content-Type': 'text/event-stream'},
        ))
        async with httpx.AsyncClient(transport=transport) as client:
            return await ai_agent.stream_response(
                client, 'https://api.openai.com/v1/responses',
                {'max_output_tokens': max_tokens}, threading.Event(), bridge,
            )

    return asyncio.run(run())


def test_long_answer_has_more_protocol_bytes_than_the_legacy_wire_limit():
    text = '市场A股 +1.26%，finish.\n' * 240
    events = [delta(character, index) for index, character in enumerate(text)]
    body = terminal(text)
    events.append(frame('response.completed', response=body))
    assert sum(map(len, events)) > 4 * MAX_RESPONSE_BYTES
    assert len(text.encode()) < ai_agent.MAX_TEXT_BYTES
    split = events[0].index('市'.encode()) + 1
    chunks = [events[0][:split], events[0][split:], *events[1:]]

    bridge = Deltas()
    actual, returned = stream(chunks, bridge)

    assert actual == body
    assert returned == text == ''.join(bridge.parts)


def test_one_http_chunk_can_hold_many_individually_small_events():
    text = '涨跌 gain/loss\n' * 100
    body = terminal(text)
    events = [delta(character, index) for index, character in enumerate(text)]
    events.append(frame('response.completed', response=body))
    chunk = b''.join(events)
    assert len(chunk) > MAX_RESPONSE_BYTES > max(map(len, events))

    bridge = Deltas()
    actual, returned = stream([chunk], bridge)

    assert actual == body
    assert returned == text == ''.join(bridge.parts)


def test_one_multiline_event_over_the_data_limit_is_rejected():
    padding = b'x' * (100 * 1024)
    lines = [
        b'data: {"type":"response.output_item.done","output_index":0,"item":'
        b'{"id":"rs_1","type":"reasoning","encrypted_content":"' + padding + b'",\n',
        b'data: "summary":[{"type":"summary_text","text":"' + padding + b'"}]}}\n\n',
    ]
    assert max(map(len, lines)) < MAX_RESPONSE_BYTES < sum(map(len, lines))

    bridge = Deltas()
    with pytest.raises(ProviderError, match='response_too_large'):
        stream([delta('已接收'), *lines, frame('response.completed', response=terminal('已接收'))], bridge)

    assert ''.join(bridge.parts) == '已接收'


def test_wire_limit_still_rejects_excessive_non_text_events_for_small_requests():
    event = frame('response.in_progress', response={'metadata': {'trace': 'x' * 1024}})
    max_tokens = 16
    wire_limit = 4 * MAX_RESPONSE_BYTES + max_tokens * 1024
    chunks = [event] * (wire_limit // len(event) + 1)
    assert len(chunks) * len(event) > wire_limit

    bridge = Deltas()
    with pytest.raises(ProviderError, match='response_too_large'):
        stream(chunks, bridge, max_tokens=max_tokens)

    assert not bridge.parts


def test_terminal_body_limit_is_checked_after_restoring_encrypted_reasoning():
    reason = {'id': 'rs_1', 'type': 'reasoning', 'summary': []}
    encrypted = 'x' * (100 * 1024)
    previous = frame('response.output_item.done', output_index=0, item={
        **reason, 'encrypted_content': encrypted,
    })
    body = terminal('继续')
    body['instructions'] = 'y' * (100 * 1024)
    body['output'].insert(0, reason)
    complete = frame('response.completed', response=body)
    assert max(len(previous), len(complete)) < MAX_RESPONSE_BYTES

    with pytest.raises(ProviderError, match='response_too_large'):
        stream([previous, complete], Deltas())


def test_terminal_text_must_match_all_streamed_deltas():
    events = [delta('部分 answer'), frame('response.completed', response=terminal('完整 answer'))]
    with pytest.raises(ProviderError, match='invalid_response'):
        stream(events, Deltas())


def test_long_review_reaches_completed_task_history_usage_and_done(modern_agent_client, monkeypatch):
    client = modern_agent_client
    client.app.include_router(event_router)
    day = '2026-11-09'
    question = '请用本地资料完整复盘 2026-11-09 的 A 股盘面。'
    answer = '## 盘面复盘\n市场A股 +1.26%，finish.\n' * 220
    events = [delta(character, index) for index, character in enumerate(answer)]
    events.append(frame('response.completed', response=terminal(answer)))
    assert sum(map(len, events)) > 4 * MAX_RESPONSE_BYTES
    assert len(answer.encode()) < ai_agent.MAX_TEXT_BYTES
    model_requests = []

    async def handler(request):
        payload = json.loads(request.content)
        model_requests.append(payload)
        if len(model_requests) == 1:
            plan = {
                'purpose': 'market_review', 'current_user_quote': question,
                'context_user_quote': '', 'goal': '复盘所选日', 'reads': [],
                'web_query': '', 'web_reason': '',
            }
            return httpx.Response(200, content=complete([function('plan_stock_task', plan)]))
        assert payload['max_output_tokens'] == 12288
        return httpx.Response(200, stream=Chunks(events))

    mock_network(monkeypatch, handler)
    runtime = AIRuntime(fake)
    try:
        enable(client, runtime, 'openai')
        created = create(client, 'wire-limit-long-review', text=question, as_of=day, market_scope='full_review')
        assert created.status_code == 202, created.text
        ids = created.json()
        task = drive(runtime, ids['task_id'])
        assert task['state'] == 'completed', task.get('reason_code')
        assert task['usage']['input_tokens'] == 200
        assert task['usage']['output_tokens'] == 50 + len(answer)

        detail = client.get('/api/v1/ai/sessions/' + ids['session_id']).json()
        saved = detail['messages'][-1]
        assert saved['text'] == answer and saved['partial'] is False
        assert saved['usage'] == task['usage']

        replay = client.get('/api/v1/ai/tasks/' + ids['task_id'] + '/events')
        assert replay.headers['content-type'].startswith('text/event-stream')
        packets = [json.loads(line.removeprefix('data: ')) for line in replay.text.splitlines()
                   if line.startswith('data: ')]
        assert ''.join(packet['text'] for packet in packets if packet['type'] == 'delta') == answer
        assert not any(packet['type'] == 'error' for packet in packets)
        assert packets[-1]['type'] == 'done' and packets[-1]['state'] == 'completed'
        assert packets[-1]['message_id'] == saved['message_id']
        assert len(model_requests) == 2
    finally:
        runtime.shutdown()
