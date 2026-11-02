"""Pure per-round local-read limits shared by the IO and control threads.

Only actual executions belong in ``prior_executed_calls``. Cached results,
scope denials, quota gaps and hosted-search plans never consume another read.
Argument validation deliberately precedes quota checks: exhaustion cannot make
an undeclared tool or malformed arguments acceptable.
"""
from datetime import date
import re


BUDGET_VERSION = 'ai-local-tool-budget-v2'
LOCAL_TOOL_BUDGET = {
    'version': BUDGET_VERSION,
    'max_data_reads': 3,
    'max_availability_reads': 2,
    'max_reads': 5,
}
_SECTIONS = ('availability', 'auction', 'noon', 'close', 'overview', 'fund_flow')


def _day(value):
    if not isinstance(value, str):
        raise ValueError('invalid_tool_arguments')
    if value == 'today':
        return
    try:
        valid = date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('invalid_tool_arguments')


def classify_tool(name, args):
    """Validate the declared function arguments and return its read category."""
    if not isinstance(args, dict):
        raise ValueError('invalid_tool_arguments')
    if name == 'read_market_data':
        if set(args) != {'date', 'section'} or args['section'] not in _SECTIONS:
            raise ValueError('invalid_tool_arguments')
        _day(args['date'])
        return 'availability' if args['section'] == 'availability' else 'data'
    if name == 'check_data_availability':
        if set(args) != {'date'}:
            raise ValueError('invalid_tool_arguments')
        _day(args['date'])
        return 'availability'
    if name == 'read_stock_research':
        if (set(args) != {'instrument_id'} or not isinstance(args['instrument_id'], str)
                or (args['instrument_id'] != ''
                    and not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', args['instrument_id']))):
            raise ValueError('invalid_tool_arguments')
        return 'data'
    if name == 'read_stock_quote':
        if (set(args) != {'date', 'instrument_id'}
                or not isinstance(args['instrument_id'], str)
                or len(args['instrument_id']) > 80
                or any(ord(c)<32 for c in args['instrument_id'])):
            raise ValueError('invalid_tool_arguments')
        _day(args['date'])
        return 'data'
    if name == 'read_limit_up_data':
        if set(args) != {'date'}:
            raise ValueError('invalid_tool_arguments')
        _day(args['date'])
        return 'data'
    if name == 'read_market_detail':
        if (set(args) != {'date', 'section', 'query'}
                or args['section'] not in ('indices','sectors','emotion','gainers','abnormal','broken_boards')
                or not isinstance(args['query'], str) or len(args['query']) > 80
                or any(ord(c)<32 for c in args['query'])):
            raise ValueError('invalid_tool_arguments')
        _day(args['date'])
        return 'data'
    raise ValueError('invalid_tool_arguments')


def tool_budget(frozen):
    """Read a frozen budget without expanding pre-v4 three-read requests."""
    if not isinstance(frozen, dict):
        raise ValueError('invalid_tool_budget')
    if 'local_tool_budget' not in frozen:
        total = frozen.get('max_local_tools', 3)
        if type(total) is not int or not 0 <= total <= 3:
            raise ValueError('invalid_tool_budget')
        return {'version': 'legacy-local-tool-budget-v1', 'max_reads': total,
                'max_data_reads': None, 'max_availability_reads': None}
    if frozen.get('version') not in ('ai-agent-round-v4','ai-agent-round-v5'):
        raise ValueError('invalid_tool_budget')
    limits = frozen['local_tool_budget']
    if (not isinstance(limits, dict) or set(limits) != set(LOCAL_TOOL_BUDGET)
            or limits.get('version') != BUDGET_VERSION
            or any(type(limits.get(key)) is not int or limits[key] != LOCAL_TOOL_BUDGET[key]
                   for key in ('max_data_reads', 'max_availability_reads', 'max_reads'))
            or type(frozen.get('max_local_tools', 5)) is not int
            or frozen.get('max_local_tools', 5) != 5):
        raise ValueError('invalid_tool_budget')
    return dict(limits)


def assess_tool(frozen, prior_executed_calls, name, args):
    """Return ``None`` for an allowed read, otherwise a non-fatal tool gap.

    This function is side-effect free. Its caller must still recheck scope and
    cancellation before using a cache or executing a read. A gap is *not* an
    ``_error``: the provider must receive an output for every valid call ID and
    can then summarize within the existing model-request budget.
    """
    category = classify_tool(name, args)
    limits = tool_budget(frozen)
    if not isinstance(prior_executed_calls, (list, tuple)):
        raise ValueError('invalid_tool_budget')
    used = {'data_reads': 0, 'availability_reads': 0, 'reads': 0}
    for call in prior_executed_calls:
        if not isinstance(call, dict) or 'name' not in call or 'args' not in call:
            raise ValueError('invalid_tool_budget')
        prior_category = classify_tool(call['name'], call['args'])
        used[prior_category + '_reads'] += 1
        used['reads'] += 1
    limit = limits['max_data_reads' if category == 'data' else 'max_availability_reads']
    if used['reads'] < limits['max_reads'] and (limit is None or used[category + '_reads'] < limit):
        return None
    requested_day = args.get('date')
    result = {
        'ok': False, 'state': 'not_read', 'reason_code': 'tool_budget_exhausted',
        'tool_name': name, 'date': None if requested_day == 'today' else requested_day,
        'summary': '本轮未读取这项资料。请依据已经取得的资料直接回答，并说明这一项尚未取得；不能据此声称资料不存在、尚未采集或尚未到时点。不要再申请查询或联网，也不要只说准备查询。',
        'source': [], 'evidence': [], 'metrics': [],
        'gaps': [{'capability': name, 'state': 'not_read',
                  'reason': '本轮读取额度已用完，本项未执行；这不是资料不存在或未采集的证据。'}],
        'budget': {'limits': limits, 'used': used, 'requested_category': category},
    }
    if requested_day is not None:
        result['requested_date'] = requested_day
    return result
