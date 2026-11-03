"""Selected-day gainers lookup over local fixtures and mocked model transport."""
from copy import deepcopy
from datetime import datetime
import json

import httpx
import pytest

from app.services import ai_read_tools as tools, ai_scope_semantic, market_history, market_service
from app.services.ai_runtime import AIRuntime
from app.services.market_rank_distribution import classification_lookup
from test_ai_agent_round import create, complete, final, function, mock_network, modern_agent_client
from test_ai_market_detail_tools import DAY, PREVIOUS, SNAPSHOT, Market
from test_ai_semantic_round import assert_initial_plan_only, no_ip_network, planned
from test_ai_service import drive, enable, fake
from test_topic_members import A, B, C, D, memberships
from test_workspace import ws


QUESTION = '30涨幅榜，里面有没有电池相关的票'
REAL_READ_TOOL = tools.execute_read_tool


def symbol(rank):
    return f'{600000 + rank}.SH'


@pytest.fixture
def gainers_source(monkeypatch):
    market = Market()
    rows = [{'instrument_id': symbol(rank), 'name': f'合成股票{rank}', 'date': PREVIOUS,
             'rank': rank, 'change_30d_pct': 1200 - rank,
             'source': 'fixture-quote', 'return_30d_start': '2026-09-11',
             'return_30d_end': PREVIOUS, 'return_30d_price_basis': 'none',
             'reason': '合成回归字段' * 80} for rank in range(1, 1002)]
    rows.append({'instrument_id': symbol(1002), 'date': PREVIOUS, 'rank': None})

    def category(key, name, ranks, *, observed=PREVIOUS, complete=True):
        return {'topic_id': key, 'name': name, 'kind': 'concept', 'source': 'fixture',
                'observed_date': observed, 'members_complete': complete,
                'instrument_ids': [symbol(rank) for rank in ranks]}

    topics = [category(f'large-{i}', f'无关题材{i}', [*range(1, 11), 13] if i < 3 else range(1, 11))
              for i in range(5)]
    topics.extend([
        category('battery-cell', '锂电池', [13, 31, 201, 201, 1001]),
        category('battery-solid', '固态电池', [201, 202]),
        category('battery-empty', '钠电池', []),
        category('battery-future', '新电池', [1], observed=DAY),
    ])
    reference = {'topics': topics}
    coverage = {'selected_date_available': True, 'source_snapshot_id': 'fixture-archive',
                'source_snapshot_day': PREVIOUS, 'notice': '合成指定日分类存档'}

    def archive(kind, day, status, current):
        assert kind == 'sectors' and day == PREVIOUS
        return deepcopy(reference), dict(coverage)

    monkeypatch.setattr(market_history, 'resolve_reference', archive)
    monkeypatch.setattr(market_service, 'panel', lambda day, **_: (market, market.status(), day, {}, []))
    monkeypatch.setattr(market_service, 'cached_gainers', lambda *_: (rows, {}))
    monkeypatch.setattr(tools, '_market', lambda *_: market)
    monkeypatch.setattr(tools, '_heads', lambda: {'daily_raw': {'snapshot_id': 'daily-fixture',
                                                              'market_snapshot_id': SNAPSHOT}})
    monkeypatch.setattr(tools, '_supplement_head', lambda: {'snapshot_id': 'supplement-fixture'})
    monkeypatch.setattr(tools, 'local_now', lambda: datetime.fromisoformat(DAY + 'T20:00:00+08:00'))
    return reference, coverage, category


def read_gainers(section, query=''):
    return tools.execute_read_tool('read_market_detail', {'date': PREVIOUS, 'section': section, 'query': query})


@pytest.fixture
def latest_gainers_source(gainers_source, monkeypatch):
    reference, _, _ = gainers_source
    market = tools._market({})
    market.head['data_end'] = PREVIOUS
    monkeypatch.setattr(market_service, 'panel', lambda day, **_: (market, market.status(), day, reference, []))


def test_unfiltered_gainers_preview_is_not_a_membership_search(gainers_source):
    value = read_gainers('gainers')
    data = value['evidence'][0]['data']
    assert data['stocks']['observed_count'] == 1000
    assert data['stocks']['returned_count'] <= 12 and data['stocks']['truncated']
    assert all('电池' not in tag['name'] for row in data['stocks']['items'] for tag in row['topics'])
    assert all('电池' not in tag['name'] for tag in data['ranking_distribution']['topics']['top'])
    filtered = read_gainers('gainers', symbol(13))['evidence'][0]['data']['stocks']['items'][0]
    assert filtered['rank'] == 13 and filtered['topics_truncated']
    assert [tag['topic_id'] for tag in filtered['topics']] == ['large-0', 'large-1', 'large-2']


def lookup(value):
    assert value['ok'] and value['date'] == PREVIOUS
    evidence = value['evidence'][0]
    assert evidence['dates']['trade_date'] == PREVIOUS
    return evidence['data']['classification_lookup'], evidence['data']['stocks']


def test_related_lookup_uses_all_sample_members_and_all_labels_before_bounds(gainers_source):
    result, stocks = lookup(read_gainers('gainers_related', '电池'))
    assert result['query'] == '电池' and result['state'] == 'matched'
    assert result['sample_count'] == 1000 and result['matched_count'] == result['observed_count'] == 4
    assert result['sample_scope'] == 'top_1000_before_filters' and result['sample_limit'] == 1000
    assert result['coverage']['classification_snapshot_id'] == 'fixture-archive'
    assert result['coverage']['classification_snapshot_day'] == PREVIOUS
    assert stocks['observed_count'] == stocks['returned_count'] == 4
    assert stocks['truncated'] is False
    assert [(row['rank'], row['instrument_id'], row['change_30d_pct']) for row in stocks['items']] == [
        (13, symbol(13), 1187), (31, symbol(31), 1169),
        (201, symbol(201), 999), (202, symbol(202), 998),
    ]
    assert all((row['quote_date'], row['quote_source'], row['return_30d_start'], row['return_30d_end'],
                row['return_30d_price_basis']) == (PREVIOUS, 'fixture-quote', '2026-09-11', PREVIOUS, 'none')
               for row in stocks['items'])
    assert [tag['topic_id'] for tag in stocks['items'][0]['matched_classifications']] == ['battery-cell']
    overlapping = stocks['items'][2]['matched_classifications']
    assert {tag['topic_id'] for tag in overlapping} == {'battery-cell', 'battery-solid'}
    assert all(tag['source_observed_date'] == PREVIOUS for tag in overlapping)
    categories = result['categories']
    assert categories['observed_count'] == categories['returned_count'] == 3
    assert {tag['topic_id'] for tag in categories['items']} == {'battery-cell', 'battery-solid', 'battery-empty'}


def test_exact_lookup_does_not_borrow_members_from_matching_id_prefix(gainers_source):
    reference, _, category = gainers_source
    reference['topics'].append(category('battery-cell-child', '锂电池设备', [9]))
    result, stocks = lookup(read_gainers('gainers_topic', 'battery-cell'))
    assert result['state'] == 'matched' and result['matched_count'] == 3
    assert [row['rank'] for row in stocks['items']] == [13, 31, 201]
    assert all([tag['topic_id'] for tag in row['matched_classifications']] == ['battery-cell']
               for row in stocks['items'])


@pytest.mark.parametrize('section,query,complete,expected_state,expected_count', [
    ('gainers_topic', 'battery-empty', True, 'confirmed_zero', 0),
    ('gainers_topic', 'battery-empty', False, 'unknown', None),
    ('gainers_topic', 'battery-future', True, 'unknown', None),
    ('gainers_topic', 'battery', True, 'unknown', None),
    ('gainers_related', '钠电池', True, 'confirmed_zero', 0),
    ('gainers_related', '没有匹配的已存题材', True, 'unknown', None),
])
def test_zero_requires_a_dated_complete_classification(gainers_source, section, query, complete,
                                                       expected_state, expected_count):
    reference, _, _ = gainers_source
    next(tag for tag in reference['topics'] if tag['topic_id'] == 'battery-empty')['members_complete'] = complete
    result, stocks = lookup(read_gainers(section, query))
    assert result['state'] == expected_state and result['matched_count'] == expected_count
    assert result['observed_count'] == 0 and result['sample_count'] == 1000
    assert stocks['items'] == [] and stocks['returned_count'] == 0 and stocks['truncated'] is False


def test_incomplete_membership_keeps_observed_union_and_unknown_total(gainers_source):
    reference, _, _ = gainers_source
    next(tag for tag in reference['topics'] if tag['topic_id'] == 'battery-cell')['members_complete'] = False
    result, stocks = lookup(read_gainers('gainers_related', '电池'))
    assert result['state'] == 'matched' and result['matched_count'] is None
    assert result['observed_count'] == stocks['observed_count'] == 4
    assert [row['rank'] for row in stocks['items']] == [13, 31, 201, 202]


def test_unavailable_archive_never_uses_labels_from_unverified_source(gainers_source):
    _, coverage, _ = gainers_source
    coverage.update(selected_date_available=False, source_snapshot_id=None, source_snapshot_day=None)
    result, stocks = lookup(read_gainers('gainers_related', '电池'))
    assert result['state'] == 'unknown' and result['matched_count'] is None
    assert result['observed_count'] == 0 and result['coverage']['classification_snapshot_day'] is None
    assert stocks['items'] == []


def test_malformed_members_cannot_prove_a_zero_intersection(gainers_source):
    reference, _, _ = gainers_source
    empty = next(tag for tag in reference['topics'] if tag['topic_id'] == 'battery-empty')
    empty['instrument_ids'] = ['600013', None]
    result, stocks = lookup(read_gainers('gainers_topic', 'battery-empty'))
    assert result['state'] == 'unknown' and result['matched_count'] is None
    assert result['observed_count'] == 0 and stocks['items'] == []


def test_stock_payload_truncation_does_not_truncate_union_or_sample_counts(gainers_source):
    reference, _, category = gainers_source
    reference['topics'].append(category('battery-large', '动力电池', range(300, 531)))
    result, stocks = lookup(read_gainers('gainers_related', '电池'))
    assert result['matched_count'] == result['observed_count'] == stocks['observed_count'] == 235
    assert result['sample_count'] == 1000
    assert stocks['truncated'] and 0 < stocks['returned_count'] < stocks['observed_count']
    assert len(stocks['items']) == stocks['returned_count']
    assert [row['rank'] for row in stocks['items']][:4] == [13, 31, 201, 202]


def test_today_without_completed_quotes_does_not_fall_back_to_latest(latest_gainers_source):
    value = tools.execute_read_tool('read_market_detail', {
        'date': 'today', 'section': 'gainers_related', 'query': '电池',
    })
    assert value['date'] == DAY and value['state'] == 'unknown' and value['evidence'] == []
    assert value['summary']['latest_completed_date'] == PREVIOUS


def test_current_lookup_reads_manual_members_and_respects_provider_exclusion(memberships):
    market, client, ids, _, _ = memberships
    day = market.entry
    excluded = client.post('/api/v1/topics/' + ids['概念甲'] + '/members',
                           json={'instrument_id': A, 'action': 'exclude'})
    assert excluded.status_code == 200, excluded.text
    manual = client.post('/api/v1/topics', json={'name': '概念甲人工', 'instrument_ids': [C]})
    assert manual.status_code == 200, manual.text
    ranks = [{'instrument_id': security, 'rank': rank} for rank, security in enumerate((A, B, C, D), 1)]
    rows, result = classification_lookup(market, {'data_end': day}, {}, day, ranks,
                                         mode='related_name', query='概念甲')
    assert [row['instrument_id'] for row in rows] == [B, C, D]
    assert result['coverage']['membership_business_date'] == day
    assert [tag['topic_id'] for tag in rows[1]['matched_classifications']] == [manual.json()['topic_id']]
    assert rows[1]['matched_classifications'][0]['source'] == 'local-manual'


def test_new_lookup_section_cannot_expand_an_older_frozen_enum():
    frozen_tools = tools.tool_schemas()
    detail = next(tool for tool in frozen_tools if tool['name'] == 'read_market_detail')
    detail['parameters']['properties']['section']['enum'] = ['indices', 'sectors', 'emotion', 'gainers', 'abnormal', 'broken_boards']
    diagnostic = {}
    with pytest.raises(ValueError, match='invalid_tool_arguments'):
        ai_scope_semantic.classify_frozen_read({'tool_schemas': frozen_tools}, 'read_market_detail',
                                              {'date': PREVIOUS, 'section': 'gainers_related', 'query': '电池'},
                                              diagnostic=diagnostic)
    assert diagnostic == {'code': 'argument_enum', 'path': 'arguments.section'}


def test_user_battery_question_plans_lookup_and_streams_after_real_read(
        modern_agent_client, latest_gainers_source, monkeypatch):
    client = modern_agent_client
    runtime = AIRuntime(fake)
    calls = []
    answer = '所选交易日的合成近30交易日涨幅榜前1000名中，有4只股票匹配本地电池分类。'
    read = {'name': 'read_market_detail', 'arguments': {
        'date': 'latest', 'section': 'gainers_related', 'query': '电池',
    }}
    # The API fixture replaces the entire tool; put back the real read adapter.
    monkeypatch.setattr(tools, 'execute_read_tool', REAL_READ_TOOL)

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            assert_initial_plan_only(payload)
            context = json.loads(payload['input'][-1]['content'])
            assert context['scope_input']['current_text'] == QUESTION
            assert context['observation_date'] == '2026-10-29'
            tool = payload['tools'][0]['parameters']['properties']['reads']['items']['anyOf']
            detail = next(branch for branch in tool if branch['properties']['name']['enum'] == ['read_market_detail'])
            assert 'gainers_related' in detail['properties']['arguments']['properties']['section']['enum']
            plan = planned(QUESTION, reads=[read])
            return httpx.Response(200, content=complete([function('plan_stock_task', plan, 'battery-plan')]))
        assert len(calls) == 2
        plan_output = next(json.loads(item['output']) for item in payload['input']
                           if item.get('type') == 'function_call_output' and item['call_id'] == 'battery-plan')
        assert plan_output['ok'] and len(plan_output['tool_results']) == 1
        result, stocks = lookup(plan_output['tool_results'][0]['result'])
        assert result['matched_count'] == 4 and result['sample_count'] == 1000
        assert [row['rank'] for row in stocks['items']] == [13, 31, 201, 202]
        return httpx.Response(200, content=complete([final(answer)], answer))

    mock_network(monkeypatch, handler)
    try:
        enable(client, runtime, 'openai')
        response = create(client, text=QUESTION, as_of='2026-10-29')
        assert response.status_code == 202, response.text
        created = response.json()
        task = drive(runtime, created['task_id'])
        assert task['state'] == 'completed', task
        assert len(calls) == 2
        message = client.get('/api/v1/ai/sessions/' + created['session_id']).json()['messages'][-1]
        assert message['text'] == answer
        assert message['source_context']['web']['used'] is False
        assert len(message['source_context']['tool_results']) == 1
        result, stocks = lookup(message['source_context']['tool_results'][0])
        assert result['matched_count'] == 4 and stocks['truncated'] is False
    finally:
        runtime.shutdown()
