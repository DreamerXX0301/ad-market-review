"""The overview shortcut always grounds its opening review in the selected day."""
import json

import httpx

from app.services import ai_full_market_review, ai_read_tools
from app.services.ai_runtime import AIRuntime
from test_ai_agent_round import complete, create, final, function, mock_network
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
