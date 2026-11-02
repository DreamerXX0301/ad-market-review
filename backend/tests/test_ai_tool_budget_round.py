"""Offline whole-round regressions for bounded reads and honest finalization.

All reports, credentials, provider events and market observations are synthetic.
The runtime bridge is real; HTTP uses MockTransport and IP sockets are denied.
"""
from copy import deepcopy
import json
import socket
import time

import httpx
import pytest

from app import core
from app.services import ai_service as service, ai_read_tools
from app.services.ai_runtime import AIRuntime
from test_ai_service import enable, fake
from test_ai_agent_round import agent_client, complete, create, final, frame, function, mock_network

QUESTION = '结合2026-10-30盘面及今天竞价和午评，分析板块情况；涨停数量不能从指数涨幅推算。'
SUMMARY = '已核对所选日期的盘面及今天竞价和午评。板块指数涨幅不能替代涨停家数，目前资料不足以判断全市场哪个板块涨停最多。'


@pytest.fixture(autouse=True)
def offline_ip_guard(monkeypatch):
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('This regression must never reach an IP network')
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('This regression must never reach an IP network')
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', connect_ex)
    monkeypatch.delenv('SHIXU_AI_SYSTEM_PROXY_ENABLED', raising=False)
    monkeypatch.delenv('SHIXU_AI_READ_SQLITE_PATH', raising=False)


def market(section, call_id, day='today'):
    return function('read_market_data', {'date': day, 'section': section}, call_id)


def availability(call_id, day='today'):
    return function('check_data_availability', {'date': day}, call_id)


def response(output, text=None, provider='deepseek'):
    # Reuse the existing UTF-8 SSE harness, changing only the synthetic model ID.
    content = complete(output, text)
    if provider == 'deepseek':
        content = content.replace(b'"model": "gpt-6.1-sol"', b'"model": "deepseek-flash"')
    return httpx.Response(200, content=content, headers={'content-type': 'text/event-stream'})


def install_reads(monkeypatch):
    reads = []

    def read(name, args, **kwargs):
        reads.append({'name': name, 'args': deepcopy(args)})
        day = args.get('date', 'today')
        day = '2026-11-02' if day == 'today' else day
        section = args.get('section', 'availability')
        metadata_only = name == 'check_data_availability' or section == 'availability'
        summary = {
            'now': '2026-11-02T12:10:00+08:00', 'timezone': 'Asia/Shanghai',
            'calendar_state': 'trading_day',
            'phases': {phase: {'phase_state': 'available', 'source_ids': {'snapshot_id': 'fixture-' + phase}}
                       for phase in ('auction', 'noon', 'close')},
            'fixture_material': '合成资料：有板块指数涨幅摘要，没有全市场板块涨停家数榜',
        }
        return {
            'ok': True, 'state': 'available', 'tool_name': name, 'date': day,
            'source': ['fixture'], 'summary': summary,
            'evidence': [] if metadata_only else [{'evidence_id': day + ':' + section, 'kind': 'fixture',
                                                  'data': {'label': '合成指数样本', 'change_pct': 1.2}}],
            'metrics': [], 'gaps': [], 'source_bindings': {'snapshot_id': 'fixture-' + day},
        }

    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', read)
    return reads


def drive_round(runtime, identifier):
    for _ in range(600):
        runtime.tick('fixture-budget-worker')
        task = service.get_task(identifier)
        if task['state'] not in ('queued', 'running'):
            return task
        time.sleep(.005)
    pytest.fail('offline budget round did not finish')


def detail(client, created):
    return client.get('/api/v1/ai/sessions/' + created['session_id']).json()['messages'][-1]


def outputs(payload):
    return {item['call_id']: json.loads(item['output']) for item in payload['input']
            if item.get('type') == 'function_call_output'}


def receipts(task_id):
    with core.connect() as db:
        return [dict(row) for row in db.execute(
            'SELECT * FROM ai_request_receipt WHERE task_id=? ORDER BY ordinal', (task_id,))]


def run_sequence(client, monkeypatch, sequence, *, provider='deepseek', question=QUESTION,
                 legacy=False, first_text=None, day_limit=None):
    calls = []
    reads = install_reads(monkeypatch)

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload['stream'] is True and payload['store'] is False
        assert str(request.url) == ('https://api.deepseek.com/responses' if provider == 'deepseek'
                                    else 'https://api.openai.com/v1/responses')
        if len(calls) == 1:
            return response(sequence, first_text, provider)
        return response([final(SUMMARY)], SUMMARY, provider)

    mock_network(monkeypatch, handler)
    runtime = AIRuntime(fake)
    try:
        enable(client, runtime, provider)
        if day_limit is not None:
            with core.connect() as db:
                db.execute('UPDATE ai_instance_limits SET daily_request_limit=? WHERE singleton=1', (day_limit,))
        created_response = create(client, key='fixture-budget-round', provider_id=provider, text=question)
        assert created_response.status_code == 202, created_response.text
        created = created_response.json()
        if legacy:
            with core.connect() as db:
                request = json.loads(db.execute('SELECT request_json FROM ai_task WHERE task_id=?',
                                               (created['task_id'],)).fetchone()[0])
                frozen = request['round_protocol']
                frozen['version'] = 'ai-agent-round-v3'
                frozen['max_local_tools'] = 3
                frozen.pop('local_tool_budget', None)
                frozen.pop('finalize_system_prompt', None)
                db.execute('UPDATE ai_task SET request_json=? WHERE task_id=?',
                           (core.dumps(request), created['task_id']))
        task = drive_round(runtime, created['task_id'])
        for _ in range(3):
            runtime.tick('fixture-budget-worker')
        return created, task, calls, reads, detail(client, created), receipts(created['task_id'])
    finally:
        runtime.shutdown()


def test_deepseek_four_calls_compare_old_overview_and_today_without_aborting(agent_client, monkeypatch):
    sequence = [market('overview', 'old_overview', '2026-10-30'), availability('today_available'),
                market('auction', 'today_auction'), market('noon', 'today_noon')]
    created, task, calls, reads, message, sent = run_sequence(agent_client, monkeypatch, sequence)
    assert task['state'] == 'completed', task
    assert len(calls) == 2 and len(reads) == 4 and len(sent) == 2
    assert set(outputs(calls[1])) == {item['call_id'] for item in sequence}
    assert sum(item.get('type') == 'function_call_output' for item in calls[1]['input']) == len(sequence)
    assert [result['date'] for result in outputs(calls[1]).values()] == [
        '2026-10-30', '2026-11-02', '2026-11-02', '2026-11-02']
    assert message['text'] == SUMMARY and message['response_type'] == 'agent_answer'
    assert message['source_context']['web']['used'] is False
    assert all(row['cost_state'] == 'estimated' for row in sent)
    assert service.providers()['usage']['requests'] == 3  # one probe plus two model attempts


@pytest.mark.parametrize('provider', ['deepseek', 'openai'])
def test_both_metadata_forms_and_three_data_reads_fit_five_calls(agent_client, monkeypatch, provider):
    sequence = [availability('check'), market('availability', 'metadata'), market('auction', 'auction'),
                market('noon', 'noon'), market('close', 'close')]
    _, task, calls, reads, message, sent = run_sequence(agent_client, monkeypatch, sequence, provider=provider)
    assert task['state'] == 'completed', task
    assert len(reads) == 5 and len(calls) == 2
    assert set(outputs(calls[1])) == {item['call_id'] for item in sequence}
    assert sum(item.get('type') == 'function_call_output' for item in calls[1]['input']) == len(sequence)
    assert all(result['ok'] is True for result in outputs(calls[1]).values())
    assert calls[1]['tools'] == []
    assert message['text'] == SUMMARY and len(sent) == 2


@pytest.mark.parametrize('sequence,executed,soft_ids', [
    ([availability('meta1'), market('availability', 'meta2'), availability('meta3')], 2, {'meta3'}),
    ([market('overview', 'data1'), market('auction', 'data2'), market('noon', 'data3'),
      market('close', 'data4')], 3, {'data4'}),
    ([availability('meta1'), market('availability', 'meta2'), market('overview', 'data1'),
      market('auction', 'data2'), market('noon', 'data3'), market('close', 'sixth')], 5, {'sixth'}),
])
def test_exhausted_read_is_soft_unread_and_next_round_summarizes_only(
        agent_client, monkeypatch, sequence, executed, soft_ids):
    _, task, calls, reads, message, sent = run_sequence(agent_client, monkeypatch, sequence)
    assert task['state'] == 'completed', task
    assert len(reads) == executed and len(calls) == 2
    results = outputs(calls[1])
    assert set(results) == {item['call_id'] for item in sequence}
    assert sum(item.get('type') == 'function_call_output' for item in calls[1]['input']) == len(sequence)
    for identifier in soft_ids:
        assert results[identifier]['ok'] is False
        assert results[identifier]['state'] == 'not_read'
        assert results[identifier]['reason_code'] == 'tool_budget_exhausted'
    assert calls[1]['tools'] == []  # neither local tools nor a new web plan
    assert message['text'] == SUMMARY and len(sent) == 2
    assert all(row['cost_state'] == 'estimated' for row in sent)


def test_soft_cap_cannot_start_a_new_web_plan_later_in_same_batch(agent_client, monkeypatch):
    question = '结合股市盘面和交易所最新公告分析市场情况'
    plan = function('request_web_search', {'query': '股票市场交易所公告', 'reason': '合成查询',
                    'purpose': 'market_review', 'user_quote': question}, 'late_web')
    sequence = [market('overview', 'data1'), market('auction', 'data2'), market('noon', 'data3'),
                market('close', 'data4'), plan]
    _, task, calls, reads, message, sent = run_sequence(
        agent_client, monkeypatch, sequence, provider='openai', question=question)
    assert task['state'] == 'completed', task
    assert len(reads) == 3 and len(calls) == 2 and calls[1]['tools'] == []
    assert outputs(calls[1])['late_web']['ok'] is False
    assert message['source_context']['web']['used'] is False
    assert all('web_policy' not in json.loads(row['price_json']) for row in sent)


def test_legacy_three_total_does_not_expand_but_can_finish_answer(agent_client, monkeypatch):
    sequence = [availability('meta1'), market('availability', 'meta2'), market('auction', 'data1'),
                market('noon', 'data2')]
    _, task, calls, reads, message, sent = run_sequence(agent_client, monkeypatch, sequence, legacy=True)
    assert task['state'] == 'completed', task
    assert len(reads) == 3 and len(calls) == 2
    assert outputs(calls[1])['data2']['state'] == 'not_read'
    assert outputs(calls[1])['data2']['budget']['limits']['max_reads'] == 3
    assert calls[1]['tools'] == [] and message['text'] == SUMMARY and len(sent) == 2


def test_daily_budget_does_not_add_a_model_call_after_valid_four_read_batch(agent_client, monkeypatch):
    sequence = [market('overview', 'old', '2026-10-30'), availability('available'),
                market('auction', 'auction'), market('noon', 'noon')]
    prefix = '已开始核对合成盘面材料。'
    created, task, calls, reads, message, sent = run_sequence(
        agent_client, monkeypatch, sequence, first_text=prefix, day_limit=2)
    assert task['state'] == 'failed' and task['reason_code'] == 'budget_exceeded'
    assert len(calls) == 1 and len(reads) == 4 and len(sent) == 1
    assert sent[0]['cost_state'] == 'estimated'
    assert message['partial'] is True and prefix in message['text']
    assert service.providers()['usage']['requests'] == 2
    before = deepcopy(sent)
    recovered = AIRuntime(fake)
    try:
        recovered.recover_interrupted()
        for _ in range(3):
            recovered.tick('fixture-budget-worker')
        assert len(calls) == 1 and receipts(created['task_id']) == before
    finally:
        recovered.shutdown()


@pytest.mark.parametrize('bad', [
    function('run_sql', {'sql': 'select 1'}, 'invalid'),
    function('read_market_data', {'date': 'today', 'section': 'auction', 'path': 'fixture'}, 'invalid'),
])
def test_invalid_tool_after_three_data_reads_is_not_soft_authorized(agent_client, monkeypatch, bad):
    sequence = [market('overview', 'data1'), market('auction', 'data2'), market('noon', 'data3'), bad]
    _, task, calls, reads, _, sent = run_sequence(agent_client, monkeypatch, sequence)
    assert task['state'] == 'failed' and task['reason_code'] == 'invalid_tool_arguments'
    assert len(reads) <= 3 and len(calls) == 1
    assert all(item['name'] == 'read_market_data' and 'path' not in item['args'] for item in reads)
    assert len(sent) == 1 and sent[0]['cost_state'] == 'estimated'


def test_outside_scope_still_cannot_read_even_with_metadata_allowance(agent_client, monkeypatch):
    reads = install_reads(monkeypatch)
    calls = []
    answer = '我仅提供A股行情复盘与个股研究。'
    sequence = [availability('meta'), market('auction', 'auction'), market('noon', 'noon'), market('close', 'close')]

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload['tools'] == []
        return response(sequence if len(calls) == 1 else [final(answer)],
                        answer if len(calls) > 1 else None)

    mock_network(monkeypatch, handler)
    runtime = AIRuntime(fake)
    try:
        enable(agent_client, runtime, 'deepseek')
        created = create(agent_client, key='fixture-budget-outside', provider_id='deepseek', text='北京的住宅房价最近怎么样').json()
        task = drive_round(runtime, created['task_id'])
        assert task['state'] == 'completed', task
        assert reads == [] and 1 <= len(calls) <= 2
        if len(calls) == 2:
            assert all(value['state'] == 'scope_denied' for value in outputs(calls[1]).values())
        assert detail(agent_client, created)['source_context']['web']['used'] is False
    finally:
        runtime.shutdown()


def test_final_summary_uses_third_model_request_without_automatic_fourth(agent_client, monkeypatch):
    reads = install_reads(monkeypatch)
    calls = []
    first = [availability('meta1')]
    second = [market('availability', 'meta2'), market('overview', 'old', '2026-10-30'),
              market('auction', 'auction'), market('noon', 'noon'), market('close', 'unread_close')]

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert len(calls) <= 3, 'No automatic fourth paid model request is allowed'
        if len(calls) == 1:
            return response(first)
        if len(calls) == 2:
            return response(second)
        assert payload['tools'] == []
        result = outputs(payload)
        assert set(result) == {item['call_id'] for item in first + second}
        assert result['unread_close']['state'] == 'not_read'
        return response([final(SUMMARY)], SUMMARY)

    mock_network(monkeypatch, handler)
    runtime = AIRuntime(fake)
    try:
        enable(agent_client, runtime, 'deepseek')
        created_response = create(agent_client, key='fixture-budget-three-models',
                                  provider_id='deepseek', text=QUESTION)
        assert created_response.status_code == 202, created_response.text
        created = created_response.json()
        task = drive_round(runtime, created['task_id'])
        assert task['state'] == 'completed', task
        for _ in range(3):
            runtime.tick('fixture-budget-worker')
        assert len(calls) == 3 and len(reads) == 5
        sent = receipts(created['task_id'])
        assert len(sent) == 3 and all(row['cost_state'] == 'estimated' for row in sent)
        assert detail(agent_client, created)['text'] == SUMMARY
        assert service.providers()['usage']['requests'] == 4
    finally:
        runtime.shutdown()


def test_third_model_ignoring_no_tools_keeps_partial_and_usage_without_a_fourth(agent_client, monkeypatch):
    reads = install_reads(monkeypatch)
    calls = []
    prefix = '本轮已核对的盘面、竞价与午评样本如下。'

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert len(calls) <= 3, 'A disobedient last response cannot authorize a fourth model call'
        if len(calls) == 1:
            return response([availability('meta')])
        if len(calls) == 2:
            return response([market('overview', 'overview'), market('auction', 'auction'),
                             market('noon', 'noon'), market('close', 'unread_close')])
        assert payload['tools'] == []
        # A supplier may ignore tools=[]; a partial assistant message is real
        # received output, but its new function call must never be executed.
        return response([final(prefix), market('fund_flow', 'forbidden_last_read')], prefix)

    mock_network(monkeypatch, handler)
    runtime = AIRuntime(fake)
    try:
        enable(agent_client, runtime, 'deepseek')
        created_response = create(agent_client, key='fixture-budget-disobedient-last',
                                  provider_id='deepseek', text=QUESTION)
        assert created_response.status_code == 202, created_response.text
        created = created_response.json()
        task = drive_round(runtime, created['task_id'])
        assert task['state'] == 'failed' and task['reason_code'] == 'agent_tool_limit'
        assert len(calls) == 3 and len(reads) == 4
        assert all(item['args'].get('section') != 'fund_flow' for item in reads)
        sent = receipts(created['task_id'])
        assert len(sent) == 3 and all(row['cost_state'] == 'estimated' for row in sent)
        message = detail(agent_client, created)
        assert message['partial'] is True and prefix in message['text']
        for _ in range(3):
            runtime.tick('fixture-budget-worker')
        assert len(calls) == 3 and receipts(created['task_id']) == sent
    finally:
        runtime.shutdown()


def test_summary_only_second_model_cannot_use_remaining_data_allowance(agent_client, monkeypatch):
    reads = install_reads(monkeypatch)
    calls = []
    prefix = '本轮已取得资料可用性信息，尚未读取具体盘面正文。'

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert len(calls) <= 2, 'A summary-only violation must not trigger another model request'
        if len(calls) == 1:
            return response([availability('meta1'), market('availability', 'meta2'), availability('unread_meta3')])
        assert payload['tools'] == []
        assert outputs(payload)['unread_meta3']['state'] == 'not_read'
        # Three substantive reads would remain in the raw budget, but the prior
        # soft limit has already moved this round into final-summary-only mode.
        return response([final(prefix), market('overview', 'forbidden_second_round_data')], prefix)

    mock_network(monkeypatch, handler)
    runtime = AIRuntime(fake)
    try:
        enable(agent_client, runtime, 'deepseek')
        created_response = create(agent_client, key='fixture-budget-summary-only-second',
                                  provider_id='deepseek', text=QUESTION)
        assert created_response.status_code == 202, created_response.text
        created = created_response.json()
        task = drive_round(runtime, created['task_id'])
        assert task['state'] == 'failed' and task['reason_code'] == 'agent_tool_limit'
        assert len(calls) == 2 and len(reads) == 2
        assert all(item['name'] == 'check_data_availability'
                   or item['args'].get('section') == 'availability' for item in reads)
        sent = receipts(created['task_id'])
        assert len(sent) == 2 and all(row['cost_state'] == 'estimated' for row in sent)
        message = detail(agent_client, created)
        assert message['partial'] is True and prefix in message['text']
        assert service.providers()['usage']['requests'] == 3
        for _ in range(3):
            runtime.tick('fixture-budget-worker')
        assert len(calls) == 2 and receipts(created['task_id']) == sent
    finally:
        runtime.shutdown()
