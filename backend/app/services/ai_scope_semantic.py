"""Bind a model's stock-task route to actual user input and bounded tools.

The model supplies the semantic domain decision.  This module does not pretend
that a keyword list or JSON schema can prove that decision semantically correct.
It validates the provenance of that decision and its proposed read capabilities;
the control thread still enforces tool, source, cost and execution limits.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from app.services.ai_read_tools import tool_schemas
from app.services.ai_tool_budget import classify_tool

VERSION = 'ai-stock-scope-v4'
PLAN_VERSION = 'ai-stock-task-plan-v1'
ROUND_VERSION = 'ai-agent-round-v5'
RESOURCE_PURPOSES = ('stock_research', 'market_review', 'stock_impact')
PURPOSES = (*RESOURCE_PURPOSES, 'concept', 'greeting', 'outside', 'clarify')
MAX_PLANNED_READS = 8
ARGUMENT_KEYS = {
    'purpose', 'current_user_quote', 'context_user_quote', 'goal', 'reads',
    'web_query', 'web_reason',
}
OUTSIDE_REPLY = '我专注A股行情复盘和上市公司研究，可以分析股票、板块、经营财报及估值。'
CLARIFY_REPLY = '你想了解A股的板块方向，还是某只股票？补充一下分析对象，我再继续。'
_LOCAL_ONLY = re.compile(r'本系统|本地(?:资料|数据)|系统数据|内部数据')
_NON_PORTABLE_SCHEMA_KEYS = frozenset(('minLength', 'maxLength', 'minItems', 'maxItems'))


def portable_schema(value):
    """Shared strict-tool subset; all omitted bounds are enforced by control."""
    if isinstance(value, dict):
        return {key: portable_schema(item) for key, item in value.items()
                if key not in _NON_PORTABLE_SCHEMA_KEYS}
    if isinstance(value, list):
        return [portable_schema(item) for item in value]
    return deepcopy(value)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def _trusted_input(request):
    """Only API/control-owned scope_input participates; never seed/page prose."""
    value = request.get('scope_input') if isinstance(request, dict) else None
    if not isinstance(value, dict):
        return None
    current = value.get('current_text')
    history = value.get('recent_user_texts', [])
    if (not isinstance(current, str) or not current.strip() or len(current) > 2000
            or not isinstance(history, list) or len(history) > 8
            or not all(isinstance(text, str) and text.strip() and len(text) <= 2000
                       for text in history)):
        return None
    focus = value.get('validated_stock_focus') or {}
    if not isinstance(focus, dict):
        return None
    canonical_focus = {}
    if focus:
        identifier, name = focus.get('instrument_id'), focus.get('name')
        if (not isinstance(identifier, str)
                or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', identifier)
                or not isinstance(name, str) or not 1 <= len(name) <= 100):
            return None
        canonical_focus = {'instrument_id': identifier, 'name': name}
    return {'current_text': current, 'recent_user_texts': list(history),
            'validated_stock_focus': canonical_focus}


def scope_input_digest(request):
    trusted = _trusted_input(request)
    return hashlib.sha256(_encoded(trusted)).hexdigest() if trusted is not None else None


def pending_scope(request):
    trusted = _trusted_input(request)
    current = trusted['current_text'] if trusted else ''
    return {'version': VERSION, 'state': 'planning', 'resources_allowed': False,
            'purposes': [], 'authorized_user_messages': [],
            'validated_stock_focus': trusted['validated_stock_focus'] if trusted else {},
            'current_text': current, 'local_only': bool(_LOCAL_ONLY.search(current)),
            'scope_input_digest': scope_input_digest(request),
            'intent_plan_version': PLAN_VERSION}


def schema():
    """One semantic plan can propose the ordinary strict read-tool arguments."""
    read_branches = []
    for tool in tool_schemas():
        read_branches.append({
            'type': 'object', 'additionalProperties': False,
            'description': tool.get('description', ''),
            'properties': {'name': {'type': 'string', 'enum': [tool['name']]},
                           'arguments': portable_schema(tool['parameters'])},
            'required': ['name', 'arguments'],
        })
    return {
        'type': 'function', 'name': 'plan_stock_task', 'strict': True,
        'description': (
            '结合当前真实用户问题及最近真实用户上下文，判断是否为A股盘面、上市公司研究或对股票的影响，'
            '并在同一次计划中提出需要的只读资料。语义判断不要求用户重复股票关键词；'
            '正常次日观察方向、财务指标和承接追问都可属于股票任务。'
            '不能从页面锚点或助手话术借用用户意图，不能跳过最近用户新话题。'
            'current_user_quote逐字复制整条当前问题；context_user_quote只可逐字复制最近一条真实用户问题，'
            '不需要继承时为空串。明确跑题用outside，真正缺分析对象才用clarify；'
            'outside/clarify/concept/greeting不提出资料读取或联网。'
            'goal仅描述研究目标，不赋予额外权限；reads仅使用声明的只读工具。'
            '资料预算仍由程序执行层控制，不存在交易、写库、SQL或文件操作能力。'
        ),
        'parameters': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'purpose': {'type': 'string', 'enum': list(PURPOSES)},
                'current_user_quote': {'type': 'string', 'description': '整条当前真实用户问题，不超过2000字'},
                'context_user_quote': {'type': 'string', 'description': '空串或最近一条真实用户问题，不超过2000字'},
                'goal': {'type': 'string', 'description': '简短研究目标，不超过1000字；不是额外授权'},
                'reads': {'type': 'array', 'items': {'anyOf': read_branches},
                          'description': '最多8项计划。真正资料执行仍受3份实质/2次可用性/共5次约束'},
                'web_query': {'type': 'string', 'description': '不联网时为空串，否则不超过1000字'},
                'web_reason': {'type': 'string', 'description': '不联网时为空串，否则不超过1000字'},
            },
            'required': sorted(ARGUMENT_KEYS),
        },
    }


def _invalid():
    return {'_error': 'invalid_tool_arguments'}


def _frozen_tools(request):
    """Never widen a saved round merely because today's tool catalog grew."""
    tools = request.get('tool_schemas')
    if not isinstance(tools, list) or not 1 <= len(tools) <= 16:
        return None
    result = {}
    for tool in tools:
        if (not isinstance(tool, dict) or tool.get('type') != 'function'
                or not isinstance(tool.get('name'), str) or tool['name'] in result
                or not isinstance(tool.get('parameters'), dict)):
            return None
        parameters = tool['parameters']
        properties, required = parameters.get('properties'), parameters.get('required')
        if (parameters.get('type') != 'object' or parameters.get('additionalProperties') is not False
                or not isinstance(properties, dict) or not isinstance(required, list)
                or not all(isinstance(name, str) for name in required)
                or len(required) != len(set(required)) or set(required) != set(properties)):
            return None
        # Current read tools deliberately accept flat string-only arguments.
        # More expressive future schemas need an explicit validator revision.
        if not all(isinstance(item, dict) and item.get('type') == 'string'
                   for item in properties.values()):
            return None
        result[tool['name']] = parameters
    return result


def _matches_frozen_arguments(args, parameters):
    if set(args) != set(parameters['required']):
        return False
    for name, value in args.items():
        rule = parameters['properties'][name]
        if (not isinstance(value, str)
                or 'enum' in rule and value not in rule['enum']
                or 'minLength' in rule and len(value) < rule['minLength']
                or 'maxLength' in rule and len(value) > rule['maxLength']):
            return False
    return True


def classify_frozen_read(request, name, args):
    """Validate every execution against its saved capability, including v4.

    A later model request is not covered by the initial plan's argument check.
    Check both the frozen schema and today's concrete executor before IO;
    neither the catalog nor a changed enum can widen a saved round.
    """
    declared = _frozen_tools(request) if isinstance(request, dict) else None
    if (declared is None or not isinstance(name, str) or name not in declared
            or not isinstance(args, dict)
            or not _matches_frozen_arguments(args, declared[name])):
        raise ValueError('invalid_tool_arguments')
    return classify_tool(name, args)


def validate_plan(request, args):
    """Validate route provenance and proposed capabilities, without doing IO."""
    trusted = _trusted_input(request)
    protocol = request.get('round_protocol') if isinstance(request, dict) else None
    declared = _frozen_tools(request) if isinstance(request, dict) else None
    if (trusted is None or not isinstance(protocol, dict) or protocol.get('version') != ROUND_VERSION
            or declared is None or not isinstance(args, dict) or set(args) != ARGUMENT_KEYS):
        return _invalid()
    purpose = args['purpose']
    if not isinstance(purpose, str) or purpose not in PURPOSES:
        return _invalid()
    limits = {'current_user_quote': 2000, 'context_user_quote': 2000,
              'goal': 1000, 'web_query': 1000, 'web_reason': 1000}
    if any(not isinstance(args[key], str) or len(args[key]) > maximum
           for key, maximum in limits.items()):
        return _invalid()
    current, previous = args['current_user_quote'], args['context_user_quote']
    if current != trusted['current_text']:
        return _invalid()
    history = trusted['recent_user_texts']
    if previous and (not history or previous != history[-1]):
        return _invalid()
    reads = args['reads']
    if not isinstance(reads, list) or len(reads) > MAX_PLANNED_READS:
        return _invalid()
    checked_reads = []
    for read in reads:
        if (not isinstance(read, dict) or set(read) != {'name', 'arguments'}
                or not isinstance(read['name'], str) or read['name'] not in declared
                or not isinstance(read['arguments'], dict)):
            return _invalid()
        try:
            classify_frozen_read(request, read['name'], read['arguments'])
        except (ValueError, TypeError):
            return _invalid()
        checked_reads.append(deepcopy(read))
    query, reason = args['web_query'], args['web_reason']
    if bool(query.strip()) != bool(reason.strip()):
        return _invalid()
    if (query and not query.strip()) or (reason and not reason.strip()):
        return _invalid()
    is_resource_task = purpose in RESOURCE_PURPOSES
    if not is_resource_task and (reads or query or reason):
        return _invalid()
    if is_resource_task and not args['goal'].strip():
        return _invalid()
    basis = [current, *([previous] if previous else [])]
    local_only = any(_LOCAL_ONLY.search(text) for text in basis)
    scope = {
        'version': VERSION, 'state': 'allowed' if is_resource_task else purpose,
        'resources_allowed': is_resource_task,
        'purposes': [purpose] if is_resource_task else [],
        'authorized_user_messages': basis if is_resource_task else [],
        'validated_stock_focus': trusted['validated_stock_focus'],
        'current_text': current, 'local_only': local_only,
        'scope_input_digest': scope_input_digest(request),
        'intent_plan_version': PLAN_VERSION, 'semantic_purpose': purpose,
        'goal': args['goal'],
        'capability_schema_digest': hashlib.sha256(_encoded(request['tool_schemas'])).hexdigest(),
    }
    accepted = deepcopy(args)
    scope['intent_plan_digest'] = hashlib.sha256(_encoded(accepted)).hexdigest()
    web_plan = None
    if query and not local_only:
        web_plan = {'query': query, 'reason': reason, 'purpose': purpose,
                    'user_quote': current}
    return {'ok': True, 'scope': scope, 'reads': checked_reads,
            'web_plan': web_plan,
            'web_blocked_by_local_only': bool(query and local_only),
            'reply': OUTSIDE_REPLY if purpose == 'outside'
                     else CLARIFY_REPLY if purpose == 'clarify' else '',
            'accepted_plan': accepted}


def accepted_scope(request):
    """Only a control-accepted plan still bound to identical user input grants IO."""
    protocol = request.get('round_protocol') if isinstance(request, dict) else None
    if not isinstance(protocol, dict) or protocol.get('version') != ROUND_VERSION:
        return pending_scope(request)
    result = validate_plan(request, request.get('accepted_stock_plan'))
    stored = request.get('semantic_scope')
    if result.get('ok') is not True or stored != result['scope']:
        return pending_scope(request)
    return deepcopy(result['scope'])
