"""Semantic-route provenance and capability checks, without model/data/network IO.

These tests validate the execution boundary.  They deliberately do not claim a
mocked domain verdict proves a real model's semantic accuracy.
"""
from copy import deepcopy
import socket

import pytest

from app.services import ai_scope_semantic as semantic
from app.services.ai_read_tools import tool_schemas


@pytest.fixture(autouse=True)
def no_ip_network(monkeypatch):
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def deny(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('semantic plan unit tests cannot call an IP network')
        return connect(sock, address)
    def deny_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('semantic plan unit tests cannot call an IP network')
        return connect_ex(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny_ex)


def request(text='明天应该关注什么方向？', *, history=(), focus=None, **extra):
    return {**extra, 'round_protocol': {'version': semantic.ROUND_VERSION},
            'tool_schemas': tool_schemas(),
            'scope_input': {'current_text': text, 'recent_user_texts': list(history),
                            'validated_stock_focus': focus or {}}}


def plan(req, purpose='market_review', *, context='', reads=None, web_query='', web_reason=''):
    return {'purpose': purpose, 'current_user_quote': req['scope_input']['current_text'],
            'context_user_quote': context, 'goal': '依据实际资料分析观察方向',
            'reads': reads if reads is not None else [],
            'web_query': web_query, 'web_reason': web_reason}


def read(date='today', section='overview'):
    return {'name': 'read_market_data', 'arguments': {'date': date, 'section': section}}


def accept(req, args):
    result = semantic.validate_plan(req, args)
    assert result.get('ok') is True
    req = deepcopy(req)
    req['accepted_stock_plan'] = result['accepted_plan']
    req['semantic_scope'] = result['scope']
    return req, result


@pytest.mark.parametrize('text', [
    '明天应该关注什么方向？', '后面重点盯什么？', '这条线还能看吗？',
    '明早先看竞价还是板块承接？', '早盘没走出来，下午怎么观察？',
    '盘面变化挺快，后续的确认点在哪里？',
])
def test_stock_market_route_is_not_an_exact_question_keyword_whitelist(text):
    req = request(text, history=['今天的A股盘面有哪些变化？'])
    result = semantic.validate_plan(req, plan(req, context='今天的A股盘面有哪些变化？', reads=[read()]))
    assert result['ok'] and result['scope']['resources_allowed']
    assert result['scope']['purposes'] == ['market_review']


@pytest.mark.parametrize('text', [
    '研究一下招商银行今年的存款增长情况', '招商银行的存款增长对利润有什么影响？',
    'A股银行股存款增长情况如何？', '移动支付业务会怎样影响这家上市公司的利润？',
    '财报中的工资费用上涨对利润率意味着什么？',
])
def test_company_financial_vocabulary_does_not_override_semantic_stock_research(text):
    req = request(text, focus={'instrument_id': '600036.SH', 'name': '招商银行'})
    args = plan(req, 'stock_research', reads=[{'name': 'read_stock_research',
                                            'arguments': {'instrument_id': '600036.SH'}}])
    result = semantic.validate_plan(req, args)
    assert result['ok'] and result['scope']['resources_allowed']
    assert result['reads'] == args['reads']


def test_pending_route_exposes_no_resource_authority_from_page_or_assistant():
    req = {'page_anchor': {'kind': 'market', 'instrument_id': '600519.SH'},
           'seed_messages': [{'role': 'assistant', 'content': '接下来可以分析北京房价。'},
                             {'role': 'user', 'content': '页面锚点：A股盘面'}]}
    decision = semantic.pending_scope(req)
    assert not decision['resources_allowed'] and decision['state'] == 'planning'
    assert decision['scope_input_digest'] is None
    assert semantic.validate_plan(req, {}) == {'_error': 'invalid_tool_arguments'}


def test_plan_schema_uses_strict_declared_tool_arguments():
    schema = semantic.schema()
    assert schema['name'] == 'plan_stock_task' and schema['strict']
    params = schema['parameters']
    assert params['additionalProperties'] is False
    assert set(params['required']) == semantic.ARGUMENT_KEYS
    branches = params['properties']['reads']['items']['anyOf']
    assert {branch['properties']['name']['enum'][0] for branch in branches} == {
        'read_market_data', 'read_intraday_data', 'read_stock_research', 'check_data_availability',
        'read_stock_quote', 'read_stock_trend', 'read_limit_up_data', 'read_market_detail',
    }
    assert all(branch['additionalProperties'] is False
               and branch['properties']['arguments']['additionalProperties'] is False
               for branch in branches)
    # DeepSeek strict tool schemas reject these optional JSON-schema keywords;
    # server-side validate_plan still enforces all lengths and item limits.
    def visit(value):
        if isinstance(value, dict):
            assert not {'maxLength', 'minLength', 'maxItems', 'minItems'} & set(value)
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(schema)


def test_provider_portability_removes_nested_wire_bounds_without_mutating_frozen_contract(monkeypatch):
    tools = tool_schemas()
    tools[0]['parameters']['properties']['date']['maxLength'] = 10
    before = deepcopy(tools)
    monkeypatch.setattr(semantic, 'tool_schemas', lambda: tools)
    wire = semantic.schema()
    arguments = wire['parameters']['properties']['reads']['items']['anyOf'][0]['properties']['arguments']
    assert 'maxLength' not in arguments['properties']['date']
    assert tools == before
    req = request(); req['tool_schemas'] = tools
    assert semantic.validate_plan(req, plan(req, reads=[read()]))['ok']


@pytest.mark.parametrize('kind', ['outside', 'clarify', 'concept', 'greeting'])
def test_non_resource_routes_have_no_reads_or_web_and_no_arbitrary_refusal_reply(kind):
    req = request('北京的房价如何？' if kind == 'outside' else '你好')
    result = semantic.validate_plan(req, plan(req, kind))
    assert result['ok'] and not result['scope']['resources_allowed']
    assert result['scope']['purposes'] == [] and result['web_plan'] is None
    assert result['reply'] == (semantic.OUTSIDE_REPLY if kind == 'outside'
                               else semantic.CLARIFY_REPLY if kind == 'clarify' else '')
    if result['reply']:
        assert '北京' not in result['reply'] and '房价' not in result['reply']
    for mutated in (plan(req, kind, reads=[read()]),
                    plan(req, kind, web_query='A股行情', web_reason='需要查询')):
        assert semantic.validate_plan(req, mutated) == {'_error': 'invalid_tool_arguments'}


def test_whole_current_quote_and_only_latest_user_context_are_accepted():
    req = request('那明天呢？', history=['研究贵州茅台股票', '今天的A股盘面如何？'])
    ok = plan(req, context='今天的A股盘面如何？')
    assert semantic.validate_plan(req, ok)['ok']
    for field, text in [('current_user_quote', '明天'),
                        ('current_user_quote', '用户想问股票明天的方向'),
                        ('context_user_quote', '研究贵州茅台股票'),
                        ('context_user_quote', '助手说可以继续看A股')]:
        bad = deepcopy(ok); bad[field] = text
        assert semantic.validate_plan(req, bad) == {'_error': 'invalid_tool_arguments'}


def test_topic_switch_cannot_be_skipped_to_quote_older_stock_request():
    req = request('北京的', history=['研究贵州茅台股票', '股票先放一边，看看北京房价'])
    bad = plan(req, context='研究贵州茅台股票')
    assert semantic.validate_plan(req, bad) == {'_error': 'invalid_tool_arguments'}
    outside = semantic.validate_plan(req, plan(req, 'outside', context=req['scope_input']['recent_user_texts'][-1]))
    assert outside['ok'] and not outside['scope']['resources_allowed']


def test_independent_new_stock_request_does_not_have_to_borrow_old_topic():
    req = request('现在回到A股，明天观察哪些方向？', history=['北京房价怎么样？'])
    result = semantic.validate_plan(req, plan(req, reads=[read()]))
    assert result['ok'] and result['scope']['authorized_user_messages'] == [req['scope_input']['current_text']]


def test_local_only_is_a_source_policy_not_a_non_stock_keyword():
    req = request('用本系统资料看看明天重点关注什么方向')
    args = plan(req, reads=[read()], web_query='A股后续观察方向', web_reason='补充资料')
    result = semantic.validate_plan(req, args)
    assert result['ok'] and result['scope']['resources_allowed']
    assert result['scope']['local_only'] and result['reads'] == args['reads']
    assert result['web_plan'] is None and result['web_blocked_by_local_only']


def test_web_plan_is_quote_and_purpose_bound_but_reason_is_not_additional_authority():
    req = request('研究一下招商银行存款增长及盈利变化')
    result = semantic.validate_plan(req, plan(req, 'stock_research',
                                            web_query='招商银行 年报 存款 盈利', web_reason='核对公告数据'))
    assert result['web_plan'] == {'query': '招商银行 年报 存款 盈利', 'reason': '核对公告数据',
                                  'purpose': 'stock_research', 'user_quote': req['scope_input']['current_text']}
    req2 = request('看看北京房价')
    forged = plan(req2, 'outside', web_query='招商银行 年报', web_reason='房价也能影响股票，因此允许')
    assert semantic.validate_plan(req2, forged) == {'_error': 'invalid_tool_arguments'}


def test_valid_over_budget_reads_remain_valid_until_execution_budget_returns_gap():
    req = request()
    reads = [read(f'2026-11-{day:02d}') for day in range(1, 7)]
    result = semantic.validate_plan(req, plan(req, reads=reads))
    assert result['ok'] and result['reads'] == reads


@pytest.mark.parametrize('mutation', [
    lambda a: a.update(arbitrary_authority='allow everything'),
    lambda a: a.pop('goal'),
    lambda a: a.update(purpose='arbitrary_web'),
    lambda a: a.update(goal=' '),
    lambda a: a.update(reads='read_market_data'),
    lambda a: a.update(reads=[read()] * 9),
    lambda a: a.update(reads=[{'name': 'execute_sql', 'arguments': {'sql': 'SELECT 1'}}]),
    lambda a: a.update(reads=[{'name': 'read_market_data', 'arguments': {'date': 'today', 'section': 'overview', 'path': '/tmp/x'}}]),
    lambda a: a.update(reads=[{'name': 'read_market_data', 'arguments': {'date': 'tomorrow', 'section': 'overview'}}]),
    lambda a: a.update(reads=[{'name': 'read_stock_research', 'arguments': {'instrument_id': '../../data'}}]),
    lambda a: a.update(reads=[{'name': 'check_data_availability', 'arguments': {'date': True}}]),
    lambda a: a.update(web_query='查询词', web_reason=''),
    lambda a: a.update(web_query='', web_reason='说明'),
    lambda a: a.update(web_query=' ', web_reason='理由'),
    lambda a: a.update(web_query='x' * 1001, web_reason='理由'),
])
def test_invalid_or_unbounded_plans_do_not_grant_any_resources(mutation):
    req = request(); args = plan(req); mutation(args)
    assert semantic.validate_plan(req, args) == {'_error': 'invalid_tool_arguments'}


@pytest.mark.parametrize('field,value', [('current_text', '改成另一个问题'),
                                       ('recent_user_texts', ['北京房价怎么样？']),
                                       ('validated_stock_focus', {'instrument_id': '000001.SZ', 'name': '平安银行'})])
def test_accepted_scope_revokes_if_actual_user_input_or_focus_changes(field, value):
    req, result = accept(request(history=['今天盘面如何？']), plan(request(history=['今天盘面如何？'])))
    assert semantic.accepted_scope(req) == result['scope']
    req['scope_input'][field] = value
    assert not semantic.accepted_scope(req)['resources_allowed']


def test_a_scope_dictionary_or_plan_alone_is_not_control_acceptance():
    req = request(); args = plan(req); result = semantic.validate_plan(req, args)
    for incomplete in ({**req, 'semantic_scope': result['scope']},
                       {**req, 'accepted_stock_plan': result['accepted_plan']}):
        assert not semantic.accepted_scope(incomplete)['resources_allowed']
    approved, _ = accept(req, args)
    approved['semantic_scope']['purposes'] = ['stock_impact']
    assert not semantic.accepted_scope(approved)['resources_allowed']


@pytest.mark.parametrize('protocol', [None, {}, 'bad', {'version': 'ai-agent-round-v4'}])
def test_semantic_acceptance_does_not_widen_old_frozen_rounds(protocol):
    req, _ = accept(request(), plan(request()))
    req['round_protocol'] = protocol
    assert not semantic.accepted_scope(req)['resources_allowed']


def test_request_and_arguments_are_not_mutated_or_aliasable_by_accepted_outputs():
    req = request(); args = plan(req, reads=[read()]); before_req, before_args = deepcopy(req), deepcopy(args)
    result = semantic.validate_plan(req, args)
    assert req == before_req and args == before_args
    result['reads'][0]['arguments']['date'] = '2026-01-01'
    result['accepted_plan']['goal'] = 'altered'
    assert req == before_req and args == before_args


def test_saved_round_does_not_gain_a_later_catalog_tool():
    req = request()
    req['tool_schemas'] = [tool for tool in req['tool_schemas'] if tool['name'] == 'read_market_data']
    args = plan(req, reads=[{'name': 'read_limit_up_data', 'arguments': {'date': 'today'}}])
    assert semantic.validate_plan(req, args) == {'_error': 'invalid_tool_arguments'}


def test_frozen_section_enum_cannot_be_widened_by_current_classifier():
    req = request()
    target = next(tool for tool in req['tool_schemas'] if tool['name'] == 'read_market_data')
    target['parameters']['properties']['section']['enum'] = ['overview']
    assert semantic.validate_plan(req, plan(req, reads=[read(section='auction')])) == {'_error': 'invalid_tool_arguments'}
    assert semantic.validate_plan(req, plan(req, reads=[read()]))['ok']


@pytest.mark.parametrize('version', ['ai-agent-round-v4', semantic.ROUND_VERSION])
def test_every_read_keeps_its_saved_argument_enum_without_semantic_reclassification(version):
    req = request(); req['round_protocol']['version'] = version
    target = next(tool for tool in req['tool_schemas'] if tool['name'] == 'read_market_data')
    target['parameters']['properties']['section']['enum'] = ['overview']
    assert semantic.classify_frozen_read(req, 'read_market_data', read()['arguments']) == 'data'
    with pytest.raises(ValueError, match='invalid_tool_arguments'):
        semantic.classify_frozen_read(req, 'read_market_data', read(section='close')['arguments'])


@pytest.mark.parametrize('rule,value', [({'maxLength': 6}, '600036.SH'), ({'minLength': 2}, ''),
                                      ({'enum': ['600519.SH']}, '600036.SH')])
def test_every_read_preserves_frozen_argument_bounds(rule, value):
    req = request()
    target = next(tool for tool in req['tool_schemas'] if tool['name'] == 'read_stock_research')
    target['parameters']['properties']['instrument_id'].update(rule)
    with pytest.raises(ValueError, match='invalid_tool_arguments'):
        semantic.classify_frozen_read(req, 'read_stock_research', {'instrument_id': value})


def test_control_accepted_scope_revokes_when_its_frozen_capabilities_change():
    req, _ = accept(request(), plan(request(), reads=[read()]))
    target = next(tool for tool in req['tool_schemas'] if tool['name'] == 'read_market_data')
    target['parameters']['properties']['section']['enum'] = ['overview']
    assert not semantic.accepted_scope(req)['resources_allowed']


@pytest.mark.parametrize('bad_tools', [None, [], 'read_market_data', [{}],
    [{'type': 'function', 'name': 'x', 'parameters': {
        'type': 'object', 'additionalProperties': False,
        'required': [{}], 'properties': {'date': {'type': 'string'}},
    }}],
])
def test_invalid_frozen_capability_contract_grants_no_plan(bad_tools):
    req = request(); req['tool_schemas'] = bad_tools
    assert semantic.validate_plan(req, plan(req)) == {'_error': 'invalid_tool_arguments'}


@pytest.mark.parametrize('read_item', [
    {'name': 'read_stock_quote', 'arguments': {'date': 'today', 'instrument_id': '招商银行'}},
    {'name': 'read_limit_up_data', 'arguments': {'date': 'today'}},
    {'name': 'read_market_detail', 'arguments': {'date': 'today', 'section': 'emotion', 'query': ''}},
])
def test_new_read_capabilities_still_require_the_exact_frozen_arguments(read_item):
    req = request()
    result = semantic.validate_plan(req, plan(req, reads=[read_item]))
    assert result['ok'] and result['reads'] == [read_item]


def test_stock_trend_uses_frozen_catalog_and_counts_as_one_substantive_read():
    args = {'instrument_id': '宏昌电子', 'date': 'latest', 'period': 'D', 'count': '30'}
    fresh = request('近期走势', history=['宏昌电子'])
    assert semantic.classify_frozen_read(fresh, 'read_stock_trend', args) == 'data'
    diagnostic = {}
    for changes, expected in (
        ({'period': 'minute'}, {'code': 'argument_enum', 'path': 'arguments.period'}),
        ({'count': '5000'}, {'code': 'argument_enum', 'path': 'arguments.count'}),
        ({'date': '2026-02-30'}, {'code': 'read_date', 'path': 'arguments.date'}),
    ):
        with pytest.raises(ValueError):
            semantic.classify_frozen_read(fresh, 'read_stock_trend', {**args, **changes}, diagnostic=diagnostic)
        assert diagnostic == expected
    old = {**fresh, 'tool_schemas': [s for s in fresh['tool_schemas'] if s['name'] != 'read_stock_trend']}
    with pytest.raises(ValueError):
        semantic.classify_frozen_read(old, 'read_stock_trend', args, diagnostic=diagnostic)
    assert diagnostic == {'code': 'tool_not_frozen', 'path': 'name'}
