from datetime import datetime, timedelta
import json
import subprocess
import sys

import httpx
import pytest

from app import core
from app.adapters import news_flash
from app.infrastructure import database
from app.services import news_events, news_scheduler as scheduler, task_service
from app.services.sync_clock import CHINA


DAY = '2026-11-09'


def at(clock):
    return datetime.fromisoformat(f'{DAY}T{clock}+08:00').astimezone(CHINA)


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for name in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR',
                 'SHIXU_NEWS_CLS_LOCAL_AUTHORIZED', 'SHIXU_EVENT_CALENDAR_ENABLED'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('SHIXU_EVENT_CALENDAR_ENABLED', '0')
    now = [at('09:00:00')]
    monkeypatch.setattr(news_events, 'now_china', lambda: now[0])
    core.ensure_ready()
    return now


def confirm_calendar():
    core.set_setting(scheduler.TRADE_CALENDAR_KEYS[0], {
        'date': '2026-11-06', 'source_id': 'fuyao', 'state': 'ready',
        'source_sha256': 'a' * 64, 'days': ['2026-11-06', DAY, '2026-11-10'],
    })


def publish_price(kind, day, *, finish=True):
    job = task_service.create_task(kind, {}, target_date=day, request_key=f'fixture-{kind}-{day}')
    path = core.published_dir() / f'{day}.json'
    core.atomic_json(path, {'synthetic': True})
    core.publish_snapshot(module='daily_raw', job_id=job['job_id'], start='2026-11-06', end=day,
                          artifacts=[{'object_key': path.name, 'sha256': core.hash_file(path),
                                      'byte_count': path.stat().st_size, 'row_count': 1}])
    if finish:
        task_service.finish(job['job_id'])
    return job['job_id']


def test_news_child_imports_its_entry_point_with_isolated_environment(monkeypatch):
    monkeypatch.setenv('PYTHONPATH', '/synthetic/backend')
    monkeypatch.setenv('SHIXU_EVENT_CALENDAR_ENABLED', '1')

    def launch(command, **options):
        assert command == [
            sys.executable, '-B', '-c', 'from app.services.news_fetch import main; main()',
            'calendar', DAY,
        ]
        assert options['timeout'] == scheduler.CHILD_TIMEOUT_SECONDS
        assert options['stdin'] == subprocess.DEVNULL
        assert options['env']['PYTHONPATH'] == '/synthetic/backend'
        assert 'SHIXU_EVENT_CALENDAR_ENABLED' not in options['env']
        return subprocess.CompletedProcess(command, 0, stdout=b'{"events":[]}')

    monkeypatch.setattr(scheduler.subprocess, 'run', launch)
    assert scheduler._collect('calendar', DAY) == {'events': []}


@pytest.mark.parametrize('calendar_override', [None, '1'])
def test_auction_protection_skips_even_database_admission(monkeypatch, clock, calendar_override):
    monkeypatch.setenv('SHIXU_NEWS_CLS_LOCAL_AUTHORIZED', '1')
    if calendar_override is None:
        monkeypatch.delenv('SHIXU_EVENT_CALENDAR_ENABLED')
    else:
        monkeypatch.setenv('SHIXU_EVENT_CALENDAR_ENABLED', calendar_override)
    monkeypatch.setattr(scheduler, 'connect', lambda: pytest.fail('auction guard opened SQLite'))
    worker = scheduler.NewsScheduler(clock=lambda: clock[0], collector=lambda *_: pytest.fail('auction guard called source'))
    for instant in ('09:23:00', '09:25:30', '09:29:59'):
        clock[0] = at(instant)
        worker.tick()


@pytest.mark.parametrize('calendar_override', [None, ''])
def test_fresh_install_defaults_only_to_official_calendar(monkeypatch, clock, calendar_override):
    if calendar_override is None:
        monkeypatch.delenv('SHIXU_EVENT_CALENDAR_ENABLED')
    else:
        monkeypatch.setenv('SHIXU_EVENT_CALENDAR_ENABLED', calendar_override)
    calls = []

    def collect(*args):
        calls.append(args)
        return {'events': [], 'source': 'fixture-source', 'source_url': 'https://example.org/calendar'}

    assert core.setting(database.EVENT_CALENDAR_INSTALL_DEFAULT) is True
    scheduler.NewsScheduler(clock=lambda: clock[0], collector=collect).tick()
    assert calls == [('calendar', DAY)]
    assert news_events.get_snapshot_meta(DAY, 'auction') is None


def test_existing_database_stays_off_and_explicit_override_wins(monkeypatch, clock):
    def no_source(*_):
        pytest.fail('disabled source was called')

    scheduler.NewsScheduler(clock=lambda: clock[0], collector=no_source).tick()
    with core.connect() as connection:
        connection.execute('DELETE FROM platform_setting WHERE key=?', (database.EVENT_CALENDAR_INSTALL_DEFAULT,))
    monkeypatch.delenv('SHIXU_EVENT_CALENDAR_ENABLED')
    monkeypatch.setattr(database, '_ready', {})
    core.ensure_ready()
    assert core.setting(database.EVENT_CALENDAR_INSTALL_DEFAULT) is None
    scheduler.NewsScheduler(clock=lambda: clock[0], collector=no_source).tick()

    monkeypatch.setenv('SHIXU_EVENT_CALENDAR_ENABLED', '1')
    worker = scheduler.NewsScheduler(clock=lambda: clock[0], collector=lambda *_: None)
    assert worker.calendar_enabled is True and worker.flash_enabled is False


def test_worker_rechecks_time_after_source_return(monkeypatch, clock):
    monkeypatch.setenv('SHIXU_NEWS_CLS_LOCAL_AUTHORIZED', '1')
    confirm_calendar()

    def collect(*args):
        assert args[0] == 'flash'
        clock[0] = at('09:24:00')
        return {'status': 'complete', 'items': [], 'reason': None}

    scheduler.NewsScheduler(clock=lambda: clock[0], collector=collect).tick()
    assert news_events.get_snapshot_meta(DAY, 'auction') is None


def test_reuse_real_cutoffs_and_wait_for_exact_increment(monkeypatch, clock):
    monkeypatch.setenv('SHIXU_NEWS_CLS_LOCAL_AUTHORIZED', '1')
    confirm_calendar()
    publish_price('initialise', '2026-11-06')
    calls = []

    def collect(*args):
        calls.append(args)
        return {'status': 'complete', 'items': [], 'reason': None}

    worker = scheduler.NewsScheduler(clock=lambda: clock[0], collector=collect)

    def tick(value):
        clock[0] = at(value)
        worker.next_check = 0
        worker.tick()

    tick('09:00:04')
    tick('09:01:00')
    tick('12:05:00')
    tick('12:30:03')
    tick('17:03:00')
    job = publish_price('increment', DAY, finish=False)
    tick('17:04:00')
    assert len(calls) == 2
    task_service.finish(job)
    tick('17:05:00')
    assert calls == [
        ('flash', '2026-11-06T17:00:00+08:00', at('09:00:04').isoformat()),
        ('flash', at('09:00:04').isoformat(), at('12:30:03').isoformat()),
        ('flash', at('12:30:03').isoformat(), at('17:05:00').isoformat()),
    ]
    assert news_events.get_snapshot_meta(DAY, 'auction')['status'] == 'partial'
    assert news_events.get_snapshot_meta(DAY, 'after_close')['status'] == 'complete'


def test_calendar_can_run_without_flash_or_verified_trading_day_and_retries_once(monkeypatch, clock):
    monkeypatch.setenv('SHIXU_EVENT_CALENDAR_ENABLED', '1')
    calls = []

    def collect(*args):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired('fixture', 12)
        return {'events': [], 'source': 'fixture-source', 'source_url': 'https://example.org/calendar'}

    worker = scheduler.NewsScheduler(clock=lambda: clock[0], collector=collect)
    worker.tick()
    publish_price('initialise', '2026-11-06')
    publish_price('increment', DAY)
    for value in ('12:30:00', '17:01:00', '17:02:00'):
        worker.next_check = 0
        clock[0] = at(value)
        worker.tick()
    assert calls == [('calendar', DAY)] * 2
    assert core.setting(scheduler.EVENT_CALENDAR_RECEIPT)['ready'] is True
    assert news_events.get_snapshot_meta(DAY, 'auction') is None


@pytest.mark.parametrize('trade_day', [True, False])
def test_trade_calendar_fallback_needs_no_market_task(monkeypatch, clock, trade_day):
    monkeypatch.setenv('SHIXU_NEWS_CLS_LOCAL_AUTHORIZED', '1')
    calls = []

    def collect(*args):
        calls.append(args[0])
        if args[0] == 'trade_calendar':
            days = ['2026-11-06', DAY, '2026-11-10'] if trade_day else ['2026-11-06', '2026-11-10']
            return {'state': 'ready', 'source_id': 'fuyao', 'source_sha256': 'a' * 64, 'days': days}
        return {'status': 'complete', 'items': [], 'reason': None}

    worker = scheduler.NewsScheduler(clock=lambda: clock[0], collector=collect)
    worker.tick()
    clock[0] = at('12:30:00')
    worker.next_check = 0
    worker.tick()
    assert calls == (['trade_calendar', 'flash', 'flash'] if trade_day else ['trade_calendar'])
    assert core.setting(scheduler.TRADE_CALENDAR_KEYS[0]) is None
    assert (news_events.get_snapshot_meta(DAY, 'auction') is not None) is trade_day
    assert (news_events.get_snapshot_meta(DAY, 'noon') is not None) is trade_day


def test_flash_checks_original_evidence_and_overlap(monkeypatch):
    cutoff = at('09:00:00')
    start = cutoff - timedelta(seconds=30)
    start_stamp = int(start.timestamp())
    end_stamp = int(cutoff.timestamp())
    requests = []
    monkeypatch.setattr(news_flash, 'PAGE_SIZE', 3)

    def source(request):
        params = request.url.params
        cursor = int(params['last_time'])
        requests.append(cursor)
        assert params['sign'] == news_flash._parameters(cursor)['sign']
        body = [
            (1, end_stamp, 'https://api3.cls.cn/share/article/1'),
            (2, end_stamp - 5, 'https://api3.cls.cn/share/article/2'),
            (3, end_stamp - 5, 'https://api3.cls.cn/share/article/3'),
            (4, end_stamp - 9, 'https://unverified.example/article/4'),
            (5, start_stamp, 'https://api3.cls.cn/share/article/5'),
        ]
        rows = [{'id': identifier, 'ctime': stamp, 'title': f'模拟标题 {identifier}', 'shareurl': url,
                 'content': '不留存正文', 'subjects': [{'subject_name': '示例专题'}, {'subject_name': ' 示例专题 '}],
                 'stock_list': []} for identifier, stamp, url in body if stamp < cursor][:3]
        return httpx.Response(200, json={'errno': 0, 'data': {'roll_data': rows}})

    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        result = news_flash.collect(client, start, cutoff, now=lambda: cutoff + timedelta(seconds=1))
    assert requests == [end_stamp + 1, end_stamp - 4, end_stamp - 8]
    assert result['status'] == 'partial'
    assert {row['id'] for row in result['items']} == {'cls:1', 'cls:2', 'cls:3'}
    assert result['items'][0]['topics'] == [{'name': '示例专题', 'basis': '财联社来源标签'}]
    assert 'symbols' not in result['items'][0]
    assert '不留存正文' not in json.dumps(result, ensure_ascii=False)
