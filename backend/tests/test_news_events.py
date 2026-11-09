from datetime import date, datetime, timedelta
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app import core
from app.main import app
from app.services import news_events as news
from app.services import pane_read_cache as cache


TODAY = '2026-11-09'
SOURCE = 'example-authority'
URL = 'https://example.org/calendar'


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(cache, '_memory', cache.OrderedDict())
    monkeypatch.setattr(cache, '_memory_bytes', 0)
    clock = [datetime.fromisoformat(TODAY + 'T17:30:00+08:00')]
    monkeypatch.setattr(news, 'now_china', lambda: clock[0])
    return clock


def _item(day=TODAY, *, identifier='source-1', title='来源原始标题'):
    return {'id': identifier, 'title': title, 'source': '消息来源', 'url': 'https://example.org/item',
            'published_at': day + 'T08:50:00+08:00', 'first_seen_at': day + 'T09:00:02+08:00',
            'topics': [{'name': '示例题材', 'basis': '原文明确提及'}]}


def _save(day=TODAY, stage='auction', *, items=None, status='complete', **kwargs):
    return news.publish_snapshot(day, stage, cutoff_at=day + 'T09:00:00+08:00',
                                 captured_at=day + 'T09:00:05+08:00',
                                 status=status, items=[_item(day)] if items is None else items, **kwargs)


def _event(*, title='原定发布', event_date='2026-11-13', status='tentative'):
    return {'id': 'event-1', 'title': title, 'source': '原发机构', 'url': URL,
            'event_date': event_date, 'status': status, 'published_date': '2026-10-01'}


def _calendar(events, at):
    return news.publish_calendar(events, source=SOURCE, source_url=URL, captured_at=at,
                                 coverage_start='2026-01-01', coverage_end='2026-12-31')


def test_routes_read_three_archives_without_network_and_keep_exact_evidence(clock, monkeypatch):
    def network(*args, **kwargs):
        pytest.fail('API reads must not contact a source')

    monkeypatch.setattr('httpx.Client.stream', network)
    client = TestClient(app)
    response = client.get('/api/v1/market/news-events')
    assert response.status_code == 200
    assert [stage['status'] for stage in response.json()['stages']] == ['missing', 'missing', 'pending']
    assert _save()['inserted']
    assert not _save(items=[_item(title='不得覆盖归档')])['inserted']
    assert _save(stage='noon', status='unavailable', items=[], reason='来源超时')['inserted']
    result = client.get('/api/v1/market/news-events', params={'date': TODAY}).json()
    assert result['retention_days'] == 30
    auction, noon, close = result['stages']
    assert auction['items'][0]['title'] == '来源原始标题'
    assert auction['items'][0]['summary'] is None
    assert auction['items'][0]['published_at'] == TODAY + 'T08:50:00+08:00'
    assert auction['items'][0]['topics'] == [{'name': '示例题材', 'basis': '原文明确提及'}]
    assert (noon['status'], noon['reason'], noon['items']) == ('unavailable', '来源超时', [])
    assert close['status'] == 'pending'
    assert news.last_snapshot(statuses=('complete',))['stage'] == 'auction'
    assert news.last_snapshot(before_at=TODAY + 'T09:00:00+08:00') is None
    assert client.get('/api/v1/market/news-events', params={'date': '20261109'}).status_code == 422
    assert client.get('/api/v1/market/news-events/calendar', params={'days': 15}).status_code == 422
    with pytest.raises(sqlite3.IntegrityError, match='不可修改'), core.connect() as connection:
        connection.execute('UPDATE market_news_snapshot SET reason=? WHERE stage=?', ('修改', 'auction'))


def test_capture_rejects_future_unsafe_duplicate_and_cutoff_races(clock):
    future = _item()
    future['published_at'] = TODAY + 'T09:00:01+08:00'
    with pytest.raises(ValueError, match='发布时间'):
        _save(items=[future])
    with pytest.raises(ValueError, match='发布时间'):
        _save(window_start_at=TODAY + 'T08:50:00+08:00')
    with pytest.raises(ValueError, match='标识重复'):
        _save(items=[_item(), _item()])
    with pytest.raises(ValueError, match='HTTPS'):
        _save(items=[{**_item(), 'url': 'https://evil.example@example.org/item'}])
    with pytest.raises(TimeoutError, match='截止时间'):
        _save(latest_write_at=clock[0])
    assert news.get_snapshot_meta(TODAY, 'auction') is None


def test_recent_disk_and_older_24_hour_cache_rebuild_from_exact_archive(clock, monkeypatch):
    class Market:
        def calendar(self):
            return [(date(2026, 11, 1) + timedelta(days=index)).isoformat() for index in range(9)]

        def status(self):
            return {'data_begin': '2026-11-01', 'data_end': TODAY, 'snapshot_id': 'same-main-generation'}

    monkeypatch.setattr(core, 'get_market', lambda: Market())
    active = [4 * cache.HISTORY_TTL_MS]
    monkeypatch.setattr(cache, 'now_ms', lambda: active[0])
    older = '2026-11-02'
    _save(TODAY)
    _save(older)
    client = TestClient(app)
    path = '/api/v1/market/news-events'
    first = client.get(path).json()
    current_cache = cache._root() / TODAY / 'news-auction.json'
    assert current_cache.is_file()
    persisted = json.loads(current_cache.read_text())
    persisted['body'] = '[]'
    current_cache.write_text(json.dumps(persisted))
    cache._memory.clear()
    cache._memory_bytes = 0
    assert client.get(path).json() == first
    archived = client.get(path, params={'date': older}).json()
    folder = cache._root() / 'historical' / older
    expiry = folder / cache._HISTORY_EXPIRY
    initial_deadline = int(expiry.read_text())
    active[0] += cache.HISTORY_TTL_MS - 1
    assert client.get(path, params={'date': older}).json() == archived
    assert int(expiry.read_text()) == initial_deadline
    active[0] += 1
    assert client.get(path, params={'date': older}).json() == archived
    assert int(expiry.read_text()) > initial_deadline
    clock[0] = datetime.fromisoformat(TODAY + 'T09:25:30+08:00')
    monkeypatch.setattr(news, 'read_block', lambda *args, **kwargs: pytest.fail('auction cache I/O'))
    assert client.get(path).json()['stages'][0] == first['stages'][0]


def test_calendar_dedup_and_as_of_versions_do_not_leak_future_changes(clock):
    first = '2026-11-07T12:30:00+08:00'
    latest = '2026-11-09T12:30:00+08:00'
    assert _calendar([_event()], first)['inserted']
    assert not _calendar([_event()], '2026-11-08T12:30:00+08:00')['inserted']
    assert _calendar([_event(title='已延期', event_date='2026-11-20', status='postponed')], latest)['inserted']
    client = TestClient(app)
    path = '/api/v1/market/news-events/calendar'
    today = client.get(path, params={'date': TODAY, 'days': 30}).json()
    assert today['events'][0]['status'] == 'postponed'
    assert today['events'][0]['event_date'] == '2026-11-20'
    assert today['sources'][0]['captured_at'] == latest
    assert client.get(path, params={'date': '2026-11-08'}).json()['events'][0]['title'] == '原定发布'
    assert client.get(path, params={'date': '2026-11-06'}).json()['status'] == 'unavailable'
    assert client.get(path).json()['events'] == []


def test_30_day_horizon_hides_old_archives_and_prunes_on_maintenance(clock):
    clock[0] = datetime.fromisoformat('2026-10-09T17:30:00+08:00')
    _save('2026-10-09')
    _calendar([_event(title='旧版本')], '2026-10-08T12:30:00+08:00')
    _calendar([_event()], '2026-10-09T12:30:00+08:00')
    clock[0] = datetime.fromisoformat(TODAY + 'T17:30:00+08:00')
    _save('2026-10-11')
    assert news.read_view('2026-10-09')['stages'][0]['status'] == 'expired'
    assert news.read_view('2026-10-11')['stages'][0]['status'] == 'complete'
    result = news.prune_snapshots(TODAY)
    assert result == {'cutoff_date': '2026-10-11', 'snapshots': 1, 'calendar_revisions': 1}
    assert news.get_snapshot_meta('2026-10-09', 'auction') is None
    calendar = news.read_calendar('2026-10-11', 30)
    assert calendar['status'] == 'available'
    assert calendar['sources'][0]['captured_at'] == '2026-10-09T12:30:00+08:00'
