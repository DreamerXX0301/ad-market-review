"""Pure plan binding and private diagnostics; never retain provider values."""
from copy import deepcopy
import json
import socket

import pytest

from app.services import ai_agent_protocol, ai_scope_semantic as semantic
from app.services.ai_read_tools import tool_schemas


@pytest.fixture(autouse=True)
def no_ip_network(monkeypatch):
    actual, actual_ex = socket.socket.connect, socket.socket.connect_ex
    def deny(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('plan diagnostics tests must not reach an IP network')
        return actual(sock, address)
    def deny_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('plan diagnostics tests must not reach an IP network')
        return actual_ex(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny_ex)


def request(text='明天应该关注什么方向？', *, history=()):
    return {'round_protocol': ai_agent_protocol.protocol(), 'tool_schemas': tool_schemas(),
            'scope_input': {'current_text': text, 'recent_user_texts': list(history),
                            'validated_stock_focus': {}}}


def plan(req):
    return {'purpose': 'market_review', 'current_user_quote': req['scope_input']['current_text'],
            'context_user_quote': '', 'goal': '依据已保存盘面分析观察条件',
            'reads': [], 'web_query': '', 'web_reason': ''}


@pytest.mark.parametrize('history', [(), ('今天A股盘面如何？',),
                                   ('研究一下招商银行。', '股票先放一边，看北京房价。')])
def test_new_plan_schema_binds_exact_current_and_only_latest_actual_user_quote(history):
    current = '明天应该关注什么方向？\n  结合实际盘面。'
    req = request(current, history=history)
    props = semantic.schema(req)['parameters']['properties']
    assert props['current_user_quote']['enum'] == [current]
    assert props['context_user_quote']['enum'] == ['', *([history[-1]] if history else [])]
    if len(history) > 1:
        assert history[0] not in props['context_user_quote']['enum']


def test_generic_schema_remains_unbound_for_historical_callers():
    props = semantic.schema()['parameters']['properties']
    assert 'enum' not in props['current_user_quote']
    assert 'enum' not in props['context_user_quote']


def test_building_a_later_bound_schema_never_rewrites_an_already_frozen_schema():
    earlier = request('今天盘面如何？', history=['昨日下午有哪些强势板块？'])
    earlier['intent_plan_schema'] = semantic.schema(earlier)
    frozen = deepcopy(earlier)
    later = request('六连板是谁？', history=['今天最高板有几板？'])
    current_schema = semantic.schema(later)
    assert earlier == frozen
    assert current_schema['parameters']['properties']['current_user_quote']['enum'] == ['六连板是谁？']
    assert earlier['intent_plan_schema']['parameters']['properties']['current_user_quote']['enum'] == ['今天盘面如何？']


@pytest.mark.parametrize('fault,code,path', [
    ('current_quote', 'current_quote', 'args.current_user_quote'),
    ('older_context', 'context_quote', 'args.context_user_quote'),
    ('tomorrow_date', 'read_date', 'args.reads[0].arguments.date'),
    ('query_without_reason', 'web_pair', 'args.web_reason'),
    ('reason_without_query', 'web_pair', 'args.web_query'),
    ('section_enum', 'argument_enum', 'args.reads[0].arguments.section'),
    ('goal_too_long', 'argument_length', 'args.goal'),
])
def test_plan_rejection_keeps_original_result_and_records_only_known_failure_path(fault, code, path):
    req = request(history=['研究一下招商银行。', '今天A股盘面如何？'])
    args = plan(req)
    marker = 'UNTRUSTED_PRIVATE_VALUE'
    if fault == 'current_quote':
        args['current_user_quote'] = marker
    elif fault == 'older_context':
        args['context_user_quote'] = req['scope_input']['recent_user_texts'][0]
    elif fault == 'tomorrow_date':
        args['reads'] = [{'name': 'read_market_data', 'arguments': {'date': 'tomorrow', 'section': 'overview'}}]
    elif fault == 'query_without_reason':
        args['web_query'] = marker
    elif fault == 'reason_without_query':
        args['web_reason'] = '无需联网'
    elif fault == 'section_enum':
        args['reads'] = [{'name': 'read_market_data', 'arguments': {'date': 'today', 'section': marker}}]
    elif fault == 'goal_too_long':
        args['goal'] = marker * 60
    diagnostic = {'old_private_value': marker}
    assert semantic.validate_plan(req, args, diagnostic=diagnostic) == {'_error': 'invalid_tool_arguments'}
    assert diagnostic == {'stage': 'plan_validation', 'code': code, 'path': path}
    encoded = json.dumps(diagnostic, ensure_ascii=False)
    assert marker not in encoded and '无需联网' not in encoded
    assert req['scope_input']['current_text'] not in encoded
    assert semantic.safe_diagnostic(diagnostic) == {'version': 'ai-stock-plan-diagnostic-v1', **diagnostic}


def test_valid_plan_clears_a_reused_diagnostic_collector_without_exposing_it_in_result():
    req = request(); diagnostic = {'stage': 'provider_plan', 'private': 'OLD_PRIVATE_VALUE'}
    result = semantic.validate_plan(req, plan(req), diagnostic=diagnostic)
    assert result['ok'] is True and diagnostic == {}
    assert 'diagnostic' not in result and 'OLD_PRIVATE_VALUE' not in json.dumps(result)


@pytest.mark.parametrize('record', [
    {'stage': 'provider_plan', 'code': 'first_call_count', 'path': 'response.output'},
    {'stage': 'runtime_plan', 'code': 'receipt_not_completed', 'path': 'request.receipt'},
    {'stage': 'ipc', 'code': 'ipc_rejected', 'path': 'ipc.plan_stock'},
    {'stage': 'plan_validation', 'code': 'argument_enum', 'path': 'args.reads[7].arguments.section'},
])
def test_private_diagnostic_sanitizer_accepts_only_program_defined_fields_and_copies_them(record):
    original = deepcopy(record)
    safe = semantic.safe_diagnostic(record)
    assert safe == {'version': 'ai-stock-plan-diagnostic-v1', **record}
    record['path'] = 'arbitrary_provider_text'
    assert safe['path'] == original['path']


@pytest.mark.parametrize('change', [
    {'stage': 'UNTRUSTED_PRIVATE_VALUE'},
    {'code': 'UNTRUSTED_PRIVATE_VALUE'},
    {'path': 'args.reads[0].arguments.UNTRUSTED_PRIVATE_VALUE'},
    {'path': 'args.reads[0].arguments.date=tomorrow'},
    {'path': 'args.reads[8].arguments.date'},
    {'path': 'args.reads[-1].arguments.date'},
    {'path': 'args.reads[0].arguments.date\nUNTRUSTED_PRIVATE_VALUE'},
    {'path': {'raw': 'UNTRUSTED_PRIVATE_VALUE'}},
    {'provider_text': 'UNTRUSTED_PRIVATE_VALUE'},
    {'api_key': 'UNTRUSTED_PRIVATE_VALUE'},
])
def test_private_diagnostic_sanitizer_rejects_unknown_keys_values_and_dynamic_provider_paths(change):
    record = {'stage': 'provider_plan', 'code': 'first_call_count', 'path': 'response.output', **change}
    assert semantic.safe_diagnostic(record) is None


@pytest.mark.parametrize('value', [None, '', [], {}, {'stage': 'provider_plan'}])
def test_private_diagnostic_sanitizer_rejects_incomplete_or_non_object_values(value):
    assert semantic.safe_diagnostic(value) is None
