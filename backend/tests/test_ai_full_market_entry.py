"""The overview shortcut always grounds its opening review in the selected day."""
import json

import httpx

from app.services import ai_full_market_review, ai_read_tools
from app.adapters import ai_agent
from app.core import connect
from app.services.ai_runtime import AIRuntime
from test_ai_agent_round import complete, create, final, frame, function, mock_network
from test_ai_agent_round import modern_agent_client as client
from test_ai_service import drive, enable, fake


DAY = '2026-11-09'
QUESTION = '请用本地资料综合复盘 2026-11-09 的 A 股盘面。'
execute = ai_read_tools.execute_read_tool


def test_overview_read_is_anchored_and_inserted_even_if_the_plan_omits_it(client, monkeypatch):
    runtime = AIRuntime(fake)
    reads = []
    requests = []

    def fill(result, page):
        assert page['as_of'] == DAY and page['market_scope'] == 'full_review'
        reads.append(page['as_of'])
        result.update(date=DAY, state='partial')
        result['summary']['coverage_test_marker'] = '核对过所选日期'

    monkeypatch.setattr(ai_full_market_review, 'fill_full_review', fill)
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', execute)

    async def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            assert reads == []
            plan = {'purpose': 'market_review', 'current_user_quote': QUESTION, 'context_user_quote': '',
                    'goal': '复盘所选日', 'reads': [], 'web_query': '', 'web_reason': ''}
            return httpx.Response(200, content=complete([function('plan_stock_task', plan, 'plan')]))
        output = next(json.loads(item['output']) for item in payload['input']
                      if item.get('type') == 'function_call_output' and item['call_id'] == 'plan')
        [read] = output['tool_results']
        assert read['name'] == ai_full_market_review.TOOL_NAME
        assert read['args'] == ai_full_market_review.TOOL_ARGS
        assert read['result']['date'] == DAY
        assert read['result']['summary']['coverage_test_marker'] == '核对过所选日期'
        answer = '根据所选日的留存盘面复盘。'
        return httpx.Response(200, content=complete([final(answer)], answer))

    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai')
        assert create(client, 'full-no-date', text=QUESTION, as_of=None, market_scope='full_review').status_code == 422
        assert create(client, 'full-with-news', text=QUESTION, as_of=DAY, market_scope='full_review',
                      news_context={'kind': 'day'}).status_code == 422
        accepted = create(client, 'full-review', text=QUESTION, as_of=DAY, market_scope='full_review')
        assert accepted.status_code == 202, accepted.text
        saved = accepted.json()
        result = drive(runtime, saved['task_id'])
        assert result['state'] == 'completed', (result.get('reason_code'), reads, len(requests))
        detail = client.get('/api/v1/ai/sessions/' + saved['session_id']).json()
        assert detail['session']['context']['market_scope'] == 'full_review'
        [evidence] = detail['messages'][-1]['source_context']['tool_results']
        assert evidence['date'] == DAY and reads == [DAY] and len(requests) == 2
    finally:
        runtime.executor.shutdown(wait=True)


def test_plain_chats_do_not_declare_the_full_review_tool():
    tool = ai_full_market_review.TOOL_NAME
    assert tool not in {schema['name'] for schema in ai_read_tools.tool_schemas()}
    assert tool in {schema['name'] for schema in ai_read_tools.tool_schemas(include_full_review=True)}


def test_full_review_rereads_on_explicit_followup_without_duplicate_views(client, monkeypatch):
    followup = '请重新详细做一次这个交易日的完整复盘。'
    calls, deadlines = [], []
    original_timeout = ai_agent.asyncio.timeout

    def timeout(seconds):
        deadlines.append(seconds)
        return original_timeout(seconds)

    monkeypatch.setattr(ai_agent.asyncio, 'timeout', timeout)
    tool = ai_full_market_review.TOOL_NAME
    tool_args = ai_full_market_review.TOOL_ARGS

    def read(name, args, **kwargs):
        calls.append((name, args))
        return {'ok': True, 'state': 'partial', 'tool_name': name, 'date': DAY,
                'summary': {'modules': [{'module': kind, 'state': 'partial'} for kind in ('overview', 'intraday')]},
                'evidence': [{'kind': kind, 'data': {'marker': 1}} for kind in ('overview', 'intraday')]}

    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', read)
    n_requests = 0

    async def handler(request):
        nonlocal n_requests
        payload = json.loads(request.content)
        n_requests += 1
        if n_requests % 2:
            assert payload['max_output_tokens'] == 6144
            branches = payload['tools'][0]['parameters']['properties']['reads']['items']['anyOf']
            assert any(b['properties']['name']['enum'] == [tool] for b in branches)
            reads = [{'name': 'read_market_data', 'arguments': {'date': DAY, 'section': 'overview'}},
                     {'name': 'read_intraday_data', 'arguments': {'date': DAY, 'slot': 'all', 'query': ''}}]
            if n_requests == 3:
                reads += [{'name': tool, 'arguments': tool_args},
                          {'name': 'read_intraday_data', 'arguments': {'date': DAY, 'slot': '0945', 'query': '机器人'}}]
            plan = {'purpose': 'market_review', 'current_user_quote': QUESTION if n_requests == 1 else followup,
                    'context_user_quote': '', 'goal': '完整复盘', 'reads': reads, 'web_query': '', 'web_reason': ''}
            return httpx.Response(200, content=complete([function('plan_stock_task', plan, 'plan')]))
        assert payload['max_output_tokens'] == 12288
        output = next(json.loads(item['output']) for item in payload['input'] if item.get('type') == 'function_call_output')
        assert [item['name'] for item in output['tool_results']] == (
            [tool] if n_requests == 2 else [tool, 'read_intraday_data'])
        answer = '这是当日的复盘。'
        return httpx.Response(200, content=complete([final(answer)], answer))

    mock_network(monkeypatch, handler)
    runtime = AIRuntime(fake)
    try:
        enable(client, runtime, 'openai')
        accepted = create(client, 'full-review-detailed', text=QUESTION, as_of=DAY, market_scope='full_review')
        assert accepted.status_code == 202, accepted.text
        opened = accepted.json()
        assert drive(runtime, opened['task_id'])['state'] == 'completed'
        accepted = client.post('/api/v1/ai/sessions/' + opened['session_id'] + '/messages',
                               json={'consent': True, 'request_key': 'full-review-retry', 'text': followup})
        assert accepted.status_code == 202, accepted.text
        assert drive(runtime, accepted.json()['task_id'])['state'] == 'completed'
        assert deadlines.count(240) == 2
        assert calls == [(tool, tool_args), (tool, tool_args),
                         ('read_intraday_data', {'date': DAY, 'slot': '0945', 'query': '机器人'})]
        with connect() as db:
            reserves = [row[0] for row in db.execute(
                'SELECT reservation_nusd FROM ai_request_receipt WHERE task_id=? ORDER BY ordinal',
                (opened['task_id'],))]
        assert reserves[1] >= 122_880_000  # 12,288 tokens at $10 per million, plus the input reserve.
    finally:
        runtime.executor.shutdown(wait=True)


def test_output_limit_keeps_partial_text_and_a_specific_reason(client, monkeypatch):
    partial = '本日盘面复盘，以下是已写到的部分。'

    async def handler(request):
        payload = json.loads(request.content)
        if payload.get('tool_choice') == 'required':
            plan = {'purpose': 'market_review', 'current_user_quote': QUESTION, 'context_user_quote': '',
                    'goal': '复盘', 'reads': [], 'web_query': '', 'web_reason': ''}
            return httpx.Response(200, content=complete([function('plan_stock_task', plan, 'plan')]))
        assert payload['max_output_tokens'] == 6144
        return httpx.Response(200, content=(
            frame({'type': 'response.output_text.delta', 'delta': partial})
            + frame({'type': 'response.incomplete', 'response': {
                'status': 'incomplete', 'output': [final(partial)],
                'incomplete_details': {'reason': 'max_output_tokens'},
                'usage': {'input_tokens': 100, 'output_tokens': 6144}}})))

    mock_network(monkeypatch, handler)
    runtime = AIRuntime(fake)
    try:
        enable(client, runtime, 'openai')
        saved = create(client, 'answer-length-cap', text=QUESTION, as_of=DAY).json()
        result = drive(runtime, saved['task_id'])
        assert result['state'] == 'failed' and result['reason_code'] == 'output_truncated'
        item = client.get('/api/v1/ai/sessions/' + saved['session_id']).json()['messages'][-1]
        assert item['state'] == 'failed' and item['text'] == partial
        with connect() as db:
            receipt = db.execute('SELECT state, usage_json FROM ai_request_receipt WHERE task_id=? AND ordinal=2',
                                 (saved['task_id'],)).fetchone()
        assert receipt['state'] == 'failed' and json.loads(receipt['usage_json'])['output_tokens'] == 6144
    finally:
        runtime.executor.shutdown(wait=True)
