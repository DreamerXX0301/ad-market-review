"""Real API/control/Responses-SSE path with mocked semantic route decisions.

The fixtures prove routing provenance, streaming and bounded IO.  They do not
claim that a mocked verdict measures any real provider model's classification.
"""
from copy import deepcopy
import json
import socket
import threading

import httpx
import pytest

from app import core
from app.services import ai_read_tools, ai_scope_semantic as semantic
from app.services.ai_runtime import AIRuntime
from test_ai_agent_round import modern_agent_client as agent_client, create, complete, function, final, mock_network
from test_ai_service import enable, fake, drive


@pytest.fixture(autouse=True)
def no_ip_network(monkeypatch):
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def deny(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('semantic integration must not reach an IP network')
        return connect(sock, address)
    def deny_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('semantic integration must not reach an IP network')
        return connect_ex(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny_ex)
    for key in ('SHIXU_AI_SYSTEM_PROXY_ENABLED', 'SHIXU_AI_READ_SQLITE_PATH'):
        monkeypatch.delenv(key, raising=False)


def market_read(section='overview', date='today'):
    return {'name': 'read_market_data', 'arguments': {'date': date, 'section': section}}


def planned(question, *, purpose='market_review', context='', reads=(), query='', reason=''):
    return {'purpose': purpose, 'current_user_quote': question, 'context_user_quote': context,
            'goal': '核对实际股票资料后分析，未取得的内容保持未知',
            'reads': list(reads), 'web_query': query, 'web_reason': reason}


def assert_initial_plan_only(payload):
    assert [tool.get('name') for tool in payload['tools']] == ['plan_stock_task']
    assert payload.get('tool_choice') in ('required', {'type': 'function', 'name': 'plan_stock_task'})
    assert payload['stream'] is True and payload['store'] is False


def fixture_result(name, args):
    # Synthetic test fixtures must never be confused with product data.
    result = {'ok': True, 'state': 'available', 'tool_name': name,
              'date': '2026-11-02' if args.get('date', 'today') == 'today' else args['date'],
              'source': ['synthetic-semantic-fixture'],
              'summary': {'fixture': '仅合成测试资料', 'sample_count': 86, 'amount_yuan': 123000000},
              'evidence': [{'evidence_id': 'fixture-e1', 'data': {'fixture': True}}],
              'metrics': [], 'gaps': [], 'source_bindings': {'snapshot_id': 'synthetic-fixture'}}
    if name == 'read_stock_research':
        result['summary']['instrument'] = {'instrument_id': '600036.SH', 'name': '招商银行'}
    return result


def deltas(task_id):
    with core.connect() as db:
        return ''.join(json.loads(row[0]).get('text', '') for row in db.execute(
            "SELECT data_json FROM ai_task_event WHERE task_id=? AND kind='delta' ORDER BY seq", (task_id,)))


def task_request(task_id):
    with core.connect() as db:
        return json.loads(db.execute('SELECT request_json FROM ai_task WHERE task_id=?', (task_id,)).fetchone()[0])


@pytest.mark.parametrize('question,purpose,read_item', [
    ('明天应该关注什么方向？', 'market_review', market_read()),
    ('后面重点盯什么？', 'market_review', market_read('close')),
    ('研究一下招商银行今年的存款增长情况', 'stock_research',
     {'name': 'read_stock_research', 'arguments': {'instrument_id': '600036.SH'}}),
    ('招商银行的存款增长对利润有什么影响？', 'stock_research',
     {'name': 'read_stock_research', 'arguments': {'instrument_id': '600036.SH'}}),
    ('长鑫科技今天成交额多少？', 'stock_research',
     {'name': 'read_stock_quote', 'arguments': {'date': 'today', 'instrument_id': '长鑫科技'}}),
])
def test_semantic_stock_plan_accepts_normal_questions_then_streams_real_tool_result(
        agent_client, monkeypatch, question, purpose, read_item):
    client = agent_client; runtime = AIRuntime(fake); calls = []; reads = []; task_ids = []
    control_thread = threading.get_ident()
    hidden = 'FIRST_PLAN_TEXT_MUST_NOT_LEAK：北京房价已经普涨。'
    answer = '已核对合成股票资料。明日应观察延续与分化；这是观察条件，不是确定上涨预测。'
    def local(name, args, **kwargs):
        assert threading.get_ident() == control_thread
        saved = task_request(task_ids[0])
        assert semantic.accepted_scope(saved)['resources_allowed'], 'control must persist acceptance before data IO'
        reads.append((name, deepcopy(args)))
        return fixture_result(name, args)
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', local)
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        if len(calls) == 1:
            assert_initial_plan_only(payload)
            return httpx.Response(200, content=complete([
                function('plan_stock_task', planned(question, purpose=purpose, reads=[read_item]), 'semantic-plan')
            ], hidden))
        assert len(calls) == 2
        plan_result = next(json.loads(item['output']) for item in payload['input']
                           if item.get('type') == 'function_call_output' and item['call_id'] == 'semantic-plan')
        assert plan_result['ok'] and plan_result['tool_results'][0]['result']['summary']['sample_count'] == 86
        assert not any(tool.get('name') == 'plan_stock_task' for tool in payload['tools'])
        return httpx.Response(200, content=complete([final(answer)], answer))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai')
        created = create(client, text=question).json(); task_ids.append(created['task_id'])
        task = drive(runtime, created['task_id'])
        assert task['state'] == 'completed', task
        assert reads == [(read_item['name'], read_item['arguments'])] and len(calls) == 2
        message = client.get('/api/v1/ai/sessions/' + created['session_id']).json()['messages'][-1]
        assert message['text'] == answer and deltas(created['task_id']) == answer
        assert hidden not in json.dumps(message, ensure_ascii=False)
        assert message['source_context']['scope']['version'] == semantic.VERSION
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM ai_request_receipt WHERE task_id=?',
                              (created['task_id'],)).fetchone()[0] == 2
    finally:
        runtime.shutdown()


def test_natural_limit_up_followup_uses_latest_actual_user_not_old_assistant_facts(agent_client, monkeypatch):
    client = agent_client; runtime = AIRuntime(fake); calls = []; reads = []
    first, question = '今天收盘行情数据有了吗？', '六连板是谁？'
    marker = 'ASSISTANT_PRIVATE_FACTS_MUST_NOT_AUTHORIZE：北京房价可以查。'
    answer = '已取得合成涨停梯队样本，六连板为测试标的；实际数量依来源覆盖核验。'
    def local(name, args, **kwargs):
        reads.append((name, deepcopy(args))); return fixture_result(name, args)
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', local)
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        if len(calls) == 1:
            assert_initial_plan_only(payload)
            return httpx.Response(200, content=complete([function('plan_stock_task', planned(first, reads=[market_read()]), 'first-plan')]))
        if len(calls) == 2:
            return httpx.Response(200, content=complete([final('已取得合成盘面资料。')], '已取得合成盘面资料。'))
        if len(calls) == 3:
            assert_initial_plan_only(payload)
            body = json.dumps(payload['input'], ensure_ascii=False)
            assert first in body and question in body and marker not in body
            return httpx.Response(200, content=complete([function('plan_stock_task', planned(
                question, context=first, reads=[{'name': 'read_limit_up_data', 'arguments': {'date': 'today'}}]), 'followup-plan')]))
        assert len(calls) == 4
        return httpx.Response(200, content=complete([final(answer)], answer))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai'); created = create(client, text=first).json()
        assert drive(runtime, created['task_id'])['state'] == 'completed'
        with core.connect() as db:
            db.execute("UPDATE ai_message SET text=? WHERE session_id=? AND role='assistant'", (marker, created['session_id']))
        follow = client.post('/api/v1/ai/sessions/' + created['session_id'] + '/messages',
                             json={'text': question, 'consent': True, 'request_key': 'semantic-limit-up-followup'})
        assert follow.status_code == 202, follow.text
        identifier = follow.json()['task_id']; assert drive(runtime, identifier)['state'] == 'completed'
        assert [name for name, _ in reads] == ['read_market_data', 'read_limit_up_data']
        assert deltas(identifier) == answer
        message = client.get('/api/v1/ai/sessions/' + created['session_id']).json()['messages'][-1]
        assert message['source_context']['scope']['authorized_user_messages'] == [question, first]
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('purpose,question', [
    ('outside', '北京最近房价怎么样？'), ('outside', '明天北京天气怎么样？'),
    ('outside', '股票放一边，教我怎么转账'), ('clarify', '北京的'),
])
def test_outside_or_unclear_route_on_stock_page_uses_one_request_and_never_leaks_provider_facts(
        agent_client, monkeypatch, purpose, question):
    client = agent_client; runtime = AIRuntime(fake); calls = []
    hidden = 'UNRELATED_PROVIDER_FACT：北京房价最近上涨20%。'
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', lambda *a, **k: pytest.fail('non-stock route cannot read data'))
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload); assert_initial_plan_only(payload)
        assert len(calls) == 1
        return httpx.Response(200, content=complete([
            function('plan_stock_task', planned(question, purpose=purpose), 'non-resource-plan')
        ], hidden))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai')
        created = create(client, text=question, kind='stock', instrument_id='600519.SH').json()
        task = drive(runtime, created['task_id']); assert task['state'] == 'completed', task
        expected = semantic.OUTSIDE_REPLY if purpose == 'outside' else semantic.CLARIFY_REPLY
        message = client.get('/api/v1/ai/sessions/' + created['session_id']).json()['messages'][-1]
        assert message['text'] == expected and deltas(created['task_id']) == expected
        assert hidden not in json.dumps(message, ensure_ascii=False)
        assert message['source_context']['tool_results'] == []
        assert message['source_context']['web']['used'] is False and len(calls) == 1
        with core.connect() as db:
            receipt = db.execute('SELECT price_json FROM ai_request_receipt WHERE task_id=?', (created['task_id'],)).fetchall()
            assert len(receipt) == 1 and all('web_policy' not in json.loads(row[0]) for row in receipt)
    finally:
        runtime.shutdown()


def test_clear_topic_switch_and_city_followup_do_not_reuse_stock_route(agent_client, monkeypatch):
    client = agent_client; runtime = AIRuntime(fake); calls = []; reads = []
    stock, other, city = '今天A股盘面如何？', '股票先放一边，最近房价跌了吗？', '北京的'
    def local(name, args, **kwargs):
        reads.append(name); return fixture_result(name, args)
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', local)
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        if len(calls) == 1:
            assert_initial_plan_only(payload)
            return httpx.Response(200, content=complete([function('plan_stock_task', planned(stock, reads=[market_read()]), 'stock')]))
        if len(calls) == 2:
            return httpx.Response(200, content=complete([final('已读取合成盘面。')], '已读取合成盘面。'))
        assert_initial_plan_only(payload)
        question, context = (other, stock) if len(calls) == 3 else (city, other)
        return httpx.Response(200, content=complete([function('plan_stock_task', planned(
            question, purpose='outside', context=context), 'outside-' + str(len(calls)))]))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai'); created = create(client, text=stock).json()
        assert drive(runtime, created['task_id'])['state'] == 'completed'
        for index, text in enumerate((other, city)):
            response = client.post('/api/v1/ai/sessions/' + created['session_id'] + '/messages',
                                   json={'text': text, 'consent': True, 'request_key': 'semantic-switch-' + str(index)})
            assert response.status_code == 202, response.text
            assert drive(runtime, response.json()['task_id'])['state'] == 'completed'
            assert deltas(response.json()['task_id']) == semantic.OUTSIDE_REPLY
        assert reads == ['read_market_data'] and len(calls) == 4
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('fault', ['resource_only', 'plan_plus_resource', 'multiple_plans',
                                  'wrong_quote', 'old_context', 'outside_with_read', 'late_invalid_read',
                                  'malformed_json', 'plain_answer'])
def test_invalid_initial_response_cannot_execute_even_the_valid_prefix_before_whole_plan_acceptance(
        agent_client, monkeypatch, fault):
    client = agent_client; runtime = AIRuntime(fake); calls = []
    question = '明天应该关注什么方向？'; hidden = 'BAD_FIRST_RESPONSE_FACT：北京房价上涨。'
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', lambda *a, **k: pytest.fail('reject complete first response before any local read'))
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload); assert_initial_plan_only(payload)
        assert len(calls) == 1
        args = planned(question, reads=[market_read()])
        output = [function('plan_stock_task', args, 'good-prefix')]
        if fault == 'resource_only': output = [function('read_market_data')]
        elif fault == 'plan_plus_resource': output.append(function('read_market_data'))
        elif fault == 'multiple_plans': output.append(function('plan_stock_task', args, 'second-plan'))
        elif fault == 'wrong_quote': args['current_user_quote'] = '分析A股'; output = [function('plan_stock_task', args)]
        elif fault == 'old_context': args['context_user_quote'] = '今天A股盘面如何？'; output = [function('plan_stock_task', args)]
        elif fault == 'outside_with_read': args['purpose'] = 'outside'; output = [function('plan_stock_task', args)]
        elif fault == 'late_invalid_read':
            args['reads'].append({'name': 'execute_sql', 'arguments': {'sql': 'SELECT 1'}})
            output = [function('plan_stock_task', args)]
        elif fault == 'malformed_json': output[0]['arguments'] = '{"purpose":'
        else: output = [final(hidden)]
        return httpx.Response(200, content=complete(output, hidden))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai'); created = create(client, text=question).json()
        if fault == 'old_context':
            with core.connect() as db:
                saved = json.loads(db.execute('SELECT request_json FROM ai_task WHERE task_id=?', (created['task_id'],)).fetchone()[0])
                saved['scope_input']['recent_user_texts'] = ['今天A股盘面如何？', '股票先放一边，看北京房价']
                db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?', (core.dumps(saved), created['task_id']))
        task = drive(runtime, created['task_id'])
        assert task['state'] == 'failed', task
        assert task['reason_code'] in ('invalid_response', 'invalid_tool_arguments', 'invalid_output', 'stock_scope_required')
        assert len(calls) == 1 and hidden not in deltas(created['task_id'])
        saved = task_request(created['task_id']); assert not semantic.accepted_scope(saved)['resources_allowed']
        with core.connect() as db:
            receipt = db.execute('SELECT price_json FROM ai_request_receipt WHERE task_id=?', (created['task_id'],)).fetchall()
            assert len(receipt) == 1 and all('web_policy' not in json.loads(row[0]) for row in receipt)
    finally:
        runtime.shutdown()


def test_planned_batch_retains_real_read_budget_and_returns_a_gap_before_summary(agent_client, monkeypatch):
    client = agent_client; runtime = AIRuntime(fake); calls = []; reads = []
    question = '对比今天各个时段的盘面资料，已有的先告诉我。'
    actions = [market_read(section) for section in ('overview', 'auction', 'noon', 'close')]
    actions += [{'name': 'check_data_availability', 'arguments': {'date': 'today'}} for _ in range(2)]
    answer = '已取得合成概览、竞价和午间资料；收盘这一项本轮尚未取得，不代表收盘资料不存在。'
    def local(name, args, **kwargs):
        reads.append((name, deepcopy(args))); return fixture_result(name, args)
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', local)
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        if len(calls) == 1:
            assert_initial_plan_only(payload)
            return httpx.Response(200, content=complete([function('plan_stock_task', planned(
                question, reads=actions), 'batch-plan')]))
        assert len(calls) == 2 and payload['tools'] == []
        result = next(json.loads(item['output']) for item in payload['input']
                      if item.get('type') == 'function_call_output' and item['call_id'] == 'batch-plan')
        results = result['tool_results']
        assert len(results) == 6 and sum(item['executed'] for item in results) == 5
        assert results[3]['result']['state'] == 'not_read' and results[3]['result']['reason_code'] == 'tool_budget_exhausted'
        assert results[3]['executed'] is False and result['force_summary']
        return httpx.Response(200, content=complete([final(answer)], answer))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai'); created = create(client, text=question).json()
        task = drive(runtime, created['task_id']); assert task['state'] == 'completed', task
        assert len(reads) == 5 and len(calls) == 2 and deltas(created['task_id']) == answer
        assert [name for name, _ in reads].count('read_market_data') == 3
        assert [name for name, _ in reads].count('check_data_availability') == 2
    finally:
        runtime.shutdown()


def test_semantic_bank_research_can_plan_one_bounded_native_search_without_a_new_scope_veto(agent_client, monkeypatch):
    client = agent_client; runtime = AIRuntime(fake); calls = []
    question = '核对招商银行最新年报的存款增长及其盈利影响。'
    answer = '合成公告样本显示，应分开核对存款结构与付息成本；结论依据引用公告。'
    url = 'https://www.sse.com.cn/disclosure/semantic-fixture.pdf'
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', lambda *a, **k: pytest.fail('this plan requests only approved search'))
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        if len(calls) == 1:
            assert_initial_plan_only(payload)
            return httpx.Response(200, content=complete([function('plan_stock_task', planned(
                question, purpose='stock_research', query='招商银行 年报 存款增长 付息成本', reason='核对股票经营资料'), 'bank-web-plan')]))
        assert len(calls) == 2 and payload['tools'][0]['type'] == 'web_search' and payload['max_tool_calls'] == 1
        message = final(answer)
        message['content'][0]['annotations'] = [{'type': 'url_citation', 'start_index': 0,
                                               'end_index': len(answer), 'url': url}]
        search = {'type': 'web_search_call', 'id': 'semantic-web', 'status': 'completed',
                  'action': {'type': 'search', 'sources': [{'url': url}]}}
        return httpx.Response(200, content=complete([search, message], answer))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai'); created = create(client, text=question).json()
        task = drive(runtime, created['task_id']); assert task['state'] == 'completed', task
        message = client.get('/api/v1/ai/sessions/' + created['session_id']).json()['messages'][-1]
        assert message['text'] == answer and message['source_context']['web']['used']
        assert len(calls) == 2
        with core.connect() as db:
            prices = [json.loads(row[0]) for row in db.execute('SELECT price_json FROM ai_request_receipt WHERE task_id=?', (created['task_id'],))]
            assert len(prices) == 2 and sum('web_policy' in item for item in prices) == 1
    finally:
        runtime.shutdown()


def test_initial_planner_message_is_not_reused_as_answer_context_after_data_authorization(agent_client, monkeypatch):
    client = agent_client; runtime = AIRuntime(fake); calls = []
    question = '明天应该关注什么方向？'
    hidden = 'UNVERIFIED_PLAN_MESSAGE：北京房价明天确定上涨。'
    answer = '应依据已读取的合成盘面观察延续和分化，不能把未来表现当成已发生事实。'
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', lambda name, args, **kw: fixture_result(name, args))
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        if len(calls) == 1:
            assert_initial_plan_only(payload)
            return httpx.Response(200, content=complete([
                final(hidden), function('plan_stock_task', planned(question, reads=[market_read()]), 'route')
            ], hidden))
        assert len(calls) == 2
        assert hidden not in json.dumps(payload['input'], ensure_ascii=False)
        return httpx.Response(200, content=complete([final(answer)], answer))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai'); created = create(client, text=question).json()
        task = drive(runtime, created['task_id']); assert task['state'] == 'completed', task
        assert deltas(created['task_id']) == answer and len(calls) == 2
    finally:
        runtime.shutdown()


def test_pre_v5_frozen_request_keeps_its_original_scope_gate(agent_client, monkeypatch):
    client = agent_client; runtime = AIRuntime(fake); calls = []; question = '明天应该关注什么方向？'
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', lambda *a, **k: pytest.fail('old unknown scope must not gain new authority'))
    async def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        assert payload['tools'] == [] and payload['max_output_tokens'] <= 256
        answer = '我只能协助A股行情复盘和股票研究。'
        return httpx.Response(200, content=complete([final(answer)], answer))
    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai'); created = create(client, text=question).json()
        with core.connect() as db:
            saved = json.loads(db.execute('SELECT request_json FROM ai_task WHERE task_id=?', (created['task_id'],)).fetchone()[0])
            saved['round_protocol']['version'] = 'ai-agent-round-v4'
            saved['round_protocol'].pop('scope_policy', None)
            saved.pop('intent_plan_schema', None)
            saved['tool_schemas'] = [tool for tool in saved['tool_schemas'] if tool['name'] in
                                      ('read_market_data', 'read_stock_research', 'check_data_availability')]
            db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?', (core.dumps(saved), created['task_id']))
        task = drive(runtime, created['task_id']); assert task['state'] == 'completed', task
        message = client.get('/api/v1/ai/sessions/' + created['session_id']).json()['messages'][-1]
        assert message['source_context']['scope']['version'] == 'ai-stock-scope-v3' and len(calls) == 1
        assert not message['source_context']['scope']['resources_allowed']
    finally:
        runtime.shutdown()
