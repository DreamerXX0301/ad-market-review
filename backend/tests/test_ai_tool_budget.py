"""Pure, offline limits: metadata does not consume substantive research reads."""
from copy import deepcopy
import pytest

from app.services.ai_tool_budget import assess_tool, classify_tool, tool_budget
from app.services.ai_agent_protocol import protocol


V4 = {
    'version': 'ai-agent-round-v4',
    'max_local_tools': 5,
    'local_tool_budget': {
        'version': 'ai-local-tool-budget-v2',
        'max_data_reads': 3,
        'max_availability_reads': 2,
        'max_reads': 5,
    },
}


def call(section='auction', day='today'):
    return {'name': 'read_market_data', 'args': {'date': day, 'section': section}}


def meta(day='today'):
    return {'name': 'check_data_availability', 'args': {'date': day}}


@pytest.mark.parametrize('name,args,expected', [
    ('check_data_availability', {'date': 'today'}, 'availability'),
    ('check_data_availability', {'date': '2026-10-30'}, 'availability'),
    ('read_market_data', {'date': 'today', 'section': 'availability'}, 'availability'),
    *[('read_market_data', {'date': '2026-10-30', 'section': section}, 'data')
      for section in ('overview', 'auction', 'noon', 'close', 'fund_flow')],
    *[('read_stock_research', {'instrument_id': code}, 'data')
      for code in ('', '600001.SH', '000001.SZ', '430001.BJ')],
])
def test_exact_supported_calls_are_classified(name, args, expected):
    assert classify_tool(name, args) == expected


@pytest.mark.parametrize('name,args', [
    ('run_sql', {'sql': 'select 1'}),
    ('request_web_search', {'query': 'fixture'}),
    ('read_market_data', []),
    ('read_market_data', {'date': 'today'}),
    ('read_market_data', {'date': 'today', 'section': 'sql'}),
    ('read_market_data', {'date': '2026-02-30', 'section': 'overview'}),
    ('read_market_data', {'date': '2026-1-1', 'section': 'overview'}),
    ('read_market_data', {'date': True, 'section': 'overview'}),
    ('read_market_data', {'date': 'today', 'section': 'availability', 'path': 'fixture'}),
    ('check_data_availability', {}),
    ('check_data_availability', {'date': 'today', 'section': 'availability'}),
    ('read_stock_research', {'instrument_id': '600001'}),
    ('read_stock_research', {'instrument_id': '600001.sh'}),
    ('read_stock_research', {'instrument_id': None}),
    ('read_stock_research', {'instrument_id': '', 'sql': 'select 1'}),
])
def test_invalid_or_unknown_calls_never_gain_metadata_allowance(name, args):
    with pytest.raises(ValueError, match='invalid_tool_arguments'):
        classify_tool(name, args)
    # Quota exhaustion must not turn invalid operations into accepted soft reads.
    with pytest.raises(ValueError, match='invalid_tool_arguments'):
        assess_tool(V4, [call(), call('noon'), call('close'), meta(), call('availability')], name, args)


def test_new_protocol_freezes_explicit_budget_without_increasing_model_requests():
    frozen = protocol()
    assert frozen['version'] == 'ai-agent-round-v5'
    assert frozen['max_model_requests'] == 3
    assert frozen['max_local_tools'] == 5
    assert frozen['local_tool_budget'] == V4['local_tool_budget']
    assert isinstance(frozen['finalize_system_prompt'], str) and frozen['finalize_system_prompt'].strip()
    assert tool_budget(frozen) == {
        'version': 'ai-local-tool-budget-v2', 'max_reads': 5,
        'max_data_reads': 3, 'max_availability_reads': 2,
    }


def test_two_metadata_and_three_data_calls_fit_same_frozen_round():
    executed = []
    sequence = [meta(), call('availability'), call('overview', '2026-10-30'), call(), call('noon')]
    frozen = deepcopy(V4)
    for item in sequence:
        before = deepcopy(executed)
        assert assess_tool(frozen, executed, item['name'], item['args']) is None
        assert executed == before and frozen == V4
        executed.append(item)


@pytest.mark.parametrize('prior,requested,category', [
    ([meta(), call('availability')], meta('2026-10-30'), 'availability'),
    ([call('overview'), call('auction'), call('noon')], call('close'), 'data'),
    ([meta(), call('availability'), call('overview'), call(), call('noon')], call('close'), 'data'),
])
def test_exhaustion_is_an_honest_unread_result_and_does_not_mutate_budget(prior, requested, category):
    frozen, original_prior = deepcopy(V4), deepcopy(prior)
    result = assess_tool(frozen, prior, requested['name'], requested['args'])
    assert result['ok'] is False and result['state'] == 'not_read'
    assert result['reason_code'] == 'tool_budget_exhausted'
    assert result['tool_name'] == requested['name']
    assert result['budget']['requested_category'] == category
    assert result['budget']['used']['reads'] == len(prior)
    assert result['budget']['limits']['max_reads'] == 5
    assert frozen == V4 and prior == original_prior
    # A limit is not evidence that market data is absent.
    assert result.get('evidence', []) == [] and result.get('metrics', []) == []
    assert 'summary' in result and result.get('state') != 'not_collected'


def test_legacy_no_nested_budget_keeps_three_total_reads_even_for_metadata():
    frozen = {'version': 'ai-agent-round-v3', 'max_local_tools': 3}
    prior = [meta(), call('availability'), call()]
    limits = tool_budget(frozen)
    assert limits['max_reads'] == 3
    assert limits['max_data_reads'] is None and limits['max_availability_reads'] is None
    result = assess_tool(frozen, prior, 'read_market_data', {'date': 'today', 'section': 'noon'})
    assert result['state'] == 'not_read' and result['reason_code'] == 'tool_budget_exhausted'
    assert result['budget']['limits']['max_reads'] == 3


@pytest.mark.parametrize('limit', [0, 1, 2, 3])
def test_legacy_bounds_are_preserved_without_silent_expansion(limit):
    assert tool_budget({'max_local_tools': limit})['max_reads'] == limit


@pytest.mark.parametrize('limit', [-1, 4, 99, True, 3.0, '3', None])
def test_invalid_legacy_budget_is_rejected(limit):
    with pytest.raises(ValueError, match='invalid_tool_budget'):
        tool_budget({'max_local_tools': limit})


@pytest.mark.parametrize('changes', [
    {'local_tool_budget': None},
    {'local_tool_budget': []},
    {'local_tool_budget': {**V4['local_tool_budget'], 'version': 'unreviewed'}},
    {'local_tool_budget': {**V4['local_tool_budget'], 'max_reads': 6}},
    {'local_tool_budget': {**V4['local_tool_budget'], 'max_data_reads': True}},
    {'local_tool_budget': {**V4['local_tool_budget'], 'max_availability_reads': 3}},
    {'local_tool_budget': {**V4['local_tool_budget'], 'extra': 1}},
    {'local_tool_budget': {k: v for k, v in V4['local_tool_budget'].items() if k != 'max_reads'}},
    {'max_local_tools': 3},
])
def test_malformed_or_inconsistent_nested_budget_never_increases_authority(changes):
    frozen = {**deepcopy(V4), **changes}
    with pytest.raises(ValueError, match='invalid_tool_budget'):
        tool_budget(frozen)


@pytest.mark.parametrize('version', ['ai-agent-round-v3', 'ai-agent-round-v6', None])
def test_nested_expansion_requires_the_explicit_new_round_protocol(version):
    frozen = deepcopy(V4)
    if version is None:
        frozen.pop('version')
    else:
        frozen['version'] = version
    with pytest.raises(ValueError, match='invalid_tool_budget'):
        tool_budget(frozen)
