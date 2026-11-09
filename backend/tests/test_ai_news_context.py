"""Ground news interpretations in the exact retained page selection."""
from datetime import datetime
import json

import httpx
import pytest

from app.services import ai_read_tools, news_events
from app.services.ai_runtime import AIRuntime
from test_ai_agent_round import complete, create, final, function, mock_network
from test_ai_agent_round import modern_agent_client as client
from test_ai_service import drive, enable, fake


DAY = '2026-11-10'
OVERNIGHT = '2026-11-09T20:10:00+08:00'
SOURCE = '隔夜新闻来源'
URL = 'https://example.org/news/original'
TOOL = 'read_news_events'
ARGS = {'scope': 'selection'}
ITEM = {'kind': 'news', 'source': SOURCE, 'id': 'item:1'}
execute = ai_read_tools.execute_read_tool


@pytest.fixture
def archive(client, monkeypatch):
    monkeypatch.setattr(news_events, 'now_china', lambda: datetime.fromisoformat(DAY + 'T18:00:00+08:00'))
    monkeypatch.setattr(ai_read_tools, 'execute_read_tool', execute)
    item = {'id': ITEM['id'], 'source': SOURCE, 'title': '隔夜报道标题', 'url': URL,
            'published_at': OVERNIGHT, 'first_seen_at': DAY + 'T09:00:05+08:00'}
    news_events.publish_snapshot(
        DAY, 'auction', status='partial', items=[item],
        window_start_at='2026-11-09T17:30:00+08:00', cutoff_at=DAY + 'T09:00:01+08:00',
        captured_at=DAY + 'T09:00:05+08:00', reason='达到来源分页上限')
    news_events.publish_snapshot(
        DAY, 'noon', status='complete',
        items=[{**item, 'source': '另一新闻来源', 'title': '不同归档',
                'published_at': DAY + 'T10:00:00+08:00', 'first_seen_at': None}],
        window_start_at=DAY + 'T09:00:01+08:00', cutoff_at=DAY + 'T12:30:01+08:00',
        captured_at=DAY + 'T12:30:05+08:00')
    news_events.publish_calendar(
        [{'id': 'future:1', 'source': '发布机关', 'title': '已留存的暂定日程', 'url': 'https://example.org/event',
          'published_date': '2026-11-07', 'event_date': '2026-11-22', 'status': 'tentative'}],
        source='official-calendar-key', source_url='https://example.org/calendar',
        captured_at='2026-11-09T10:00:00+08:00', coverage_start='2026-11-09', coverage_end='2026-11-27')
    return client


def read(selection, day=DAY, args=ARGS):
    return execute(TOOL, args, page_context={'as_of': day, 'news_context': selection})


def test_exact_selection_and_observation_coverage(archive):
    one = read(ITEM)
    assert one['state'] == 'partial'
    assert one['summary']['archived_count'] == 2
    assert one['summary']['stages'][0]['reason'] == '达到来源分页上限'
    assert [row['title'] for row in one['evidence']] == ['隔夜报道标题']
    row = one['evidence'][0]
    assert row['dates'] == {'observation_date': DAY, 'published_at': OVERNIGHT,
                            'first_seen_at': DAY + 'T09:00:05+08:00', 'archive_captured_at': DAY + 'T09:00:05+08:00'}
    assert row['data']['url'] == URL and row['data']['summary'] is None
    assert len(read({'kind': 'day'})['evidence']) == 2
    assert read(ITEM, '2026-11-09')['evidence'] == []
    assert read({**ITEM, 'source': '假定来源'})['evidence'] == []
    assert read(ITEM, args={**ARGS, 'url': URL})['ok'] is False
    assert execute(TOOL, ARGS, page_context={'as_of': DAY})['ok'] is False
    assert TOOL not in {schema['name'] for schema in ai_read_tools.tool_schemas()}


def test_calendar_selection_respects_range_and_retained_revision(archive):
    event = {'kind': 'event', 'source': '发布机关', 'id': 'future:1', 'days': 7}
    assert read(event)['evidence'] == []
    result = read({**event, 'days': 30})
    assert result['summary']['end_date'] == '2026-12-09'
    assert result['gaps'][0]['capability'] == 'calendar_coverage'
    row = result['evidence'][0]
    assert row['data']['status'] == 'tentative'
    assert row['dates'] == {'observation_date': DAY, 'event_date': '2026-11-22', 'published_at': None,
                            'published_date': '2026-11-07', 'archive_captured_at': '2026-11-09T10:00:00+08:00'}
    assert read({**event, 'days': 30}, '2026-11-08')['evidence'] == []


def test_day_digest_includes_all_archived_stages_when_close_alone_fills_the_budget(archive):
    news_events.publish_snapshot(
        DAY, 'after_close', status='complete',
        items=[{'id': 'close:' + str(index), 'source': SOURCE, 'title': '盘后标题 ' + str(index), 'url': URL,
                'published_at': DAY + f'T14:{index:02}:00+08:00'} for index in range(25)],
        window_start_at=DAY + 'T12:30:01+08:00', cutoff_at=DAY + 'T17:01:00+08:00',
        captured_at=DAY + 'T17:01:05+08:00')
    result = read({'kind': 'day'})
    assert result['summary']['archived_count'] == 27
    assert [stage['included_count'] for stage in result['summary']['stages']] == [1, 1, 18]
    assert [row['title'] for row in result['evidence']][-2:] == ['不同归档', '隔夜报道标题']
    assert any(gap['capability'] == 'archive_projection' for gap in result['gaps'])


def test_accepted_opening_round_gets_exact_archived_news_without_an_explicit_model_read(archive, monkeypatch):
    runtime = AIRuntime(fake)
    question = '请用本地资料解读这条消息与 A 股的关系。'
    calls = []

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert ITEM['id'] not in json.dumps(payload, ensure_ascii=False)
        if len(calls) == 1:
            assert '隔夜报道标题' not in json.dumps(payload, ensure_ascii=False)
            plan = {'purpose': 'stock_impact', 'current_user_quote': question, 'context_user_quote': '',
                    'goal': '依据本地归档解读消息', 'reads': [], 'web_query': '', 'web_reason': ''}
            return httpx.Response(200, content=complete([function('plan_stock_task', plan, 'plan')]))
        result = next(json.loads(item['output']) for item in payload['input']
                      if item.get('type') == 'function_call_output' and item['call_id'] == 'plan')
        [read_result] = result['tool_results']
        assert read_result['name'] == TOOL and read_result['args'] == ARGS
        assert [row['data']['url'] for row in read_result['result']['evidence']] == [URL]
        answer = '根据留存的标题说明已知事实。'
        return httpx.Response(200, content=complete([final(answer)], answer))

    mock_network(monkeypatch, handler)
    try:
        invalid = create(archive, 'invalid-news', text=question, as_of=DAY, news_context={**ITEM, 'url': URL})
        assert invalid.status_code == 422
        unconfirmed = create(archive, 'no-consent', text=question, as_of=DAY, news_context=ITEM, consent=False)
        assert unconfirmed.status_code == 422
        enable(archive, runtime, 'openai')
        response = create(archive, 'read-news', text=question, as_of=DAY, news_context=ITEM)
        assert response.status_code == 202, response.text
        saved = response.json()
        result = drive(runtime, saved['task_id'])
        assert result['state'] == 'completed', result
        session = archive.get('/api/v1/ai/sessions/' + saved['session_id']).json()
        assert session['session']['context']['news_context'] == ITEM
        [read_result] = session['messages'][-1]['source_context']['tool_results']
        assert read_result['evidence'][0]['data']['url'] == URL
        assert len(calls) == 2
    finally:
        runtime.executor.shutdown(wait=True)
