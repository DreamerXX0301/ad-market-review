"""Locally retained message archives and first-known event calendar revisions."""
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.core import connect, dumps
from app.services.pane_read_cache import MAX_BLOCK_BYTES, read_block

CHINA = ZoneInfo('Asia/Shanghai')
RETENTION_DAYS = 30
STAGES = ('auction', 'noon', 'after_close')
STAGE_TIMES = {'auction': time(9), 'noon': time(12, 30)}
STAGE_GRACE_END = {'auction': time(9, 2), 'noon': time(12, 35)}
STATUSES = ('complete', 'partial', 'unavailable')
EVENT_STATUSES = ('confirmed', 'tentative', 'postponed', 'cancelled')
MAX_ITEMS = 500
_SNAPSHOT_META = 'trade_date,stage,status,cutoff_at,window_start_at,captured_at,reason,content_sha256'


def now_china():
    return datetime.now(CHINA)


def _day(value):
    if not isinstance(value, str):
        raise ValueError('日期必须采用 YYYY-MM-DD')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value or not 1900 <= parsed.year < 9999:
        raise ValueError('日期必须采用 YYYY-MM-DD')
    return parsed


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('时间必须含明确时区')
    return value.astimezone(CHINA)


def _text(value, field, maximum, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not (value := value.strip()) or len(value) > maximum:
        raise ValueError(f'{field}为空或超出长度限制')
    return value


def _url(value):
    value = _text(value, '原文链接', 2048)
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or any(char.isspace() or char == '\\' or ord(char) < 32 for char in value)):
        raise ValueError('原文链接必须为安全的 HTTPS 地址')
    return value


def _links(values, key):
    if not isinstance(values, list) or len(values) > 12:
        raise ValueError('关联条目超过归档上限')
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('关联条目格式无效')
        item = {key: _text(value.get(key), '关联对象', 120), 'basis': _text(value.get('basis'), '关联依据', 300)}
        if key == 'symbol' and value.get('name') is not None:
            item['name'] = _text(value['name'], '证券名称', 120)
        result.append(item)
    return result


def _archive_fields(item):
    if not isinstance(item, dict):
        raise ValueError('归档条目格式无效')
    return {
        'id': _text(item.get('id'), '来源标识', 200),
        'title': _text(item.get('title'), '标题', 350),
        'source': _text(item.get('source'), '来源名称', 120),
        'url': _url(item.get('url')),
        'summary': _text(item.get('summary'), '摘要', 500, optional=True),
        'topics': _links(item.get('topics', []), 'name'),
    }


def _news_item(item, start, cutoff, capture):
    result = _archive_fields(item)
    published = _time(item.get('published_at'))
    if published > min(cutoff, capture) or (start is not None and published <= start):
        raise ValueError('新闻发布时间不在本档归档区间')
    seen = _time(item['first_seen_at']) if item.get('first_seen_at') is not None else None
    if seen is not None and not published <= seen <= capture:
        raise ValueError('新闻首次观测时间与归档时间不符')
    return {**result, 'published_at': published.isoformat(), 'first_seen_at': seen.isoformat() if seen else None,
            'symbols': _links(item.get('symbols', []), 'symbol'),
            'event_id': _text(item.get('event_id'), '关联事件标识', 200, optional=True)}


def _event_item(item, capture):
    result = _archive_fields(item)
    status = item.get('status')
    if status not in EVENT_STATUSES:
        raise ValueError('事件状态无效')
    published = _time(item['published_at']) if item.get('published_at') is not None else None
    published_day = _day(item['published_date']) if item.get('published_date') is not None else None
    if (published and (published > capture or (published_day and published.date() != published_day))
            or published_day and published_day > capture.date()):
        raise ValueError('事件来源时间晚于归档时间或不一致')
    return {**result, 'status': status, 'event_date': _day(item.get('event_date')).isoformat(),
            'published_at': published.isoformat() if published else None,
            'published_date': published_day.isoformat() if published_day else None}


def _payload(items, normalize, *, sort_key, reverse=False):
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError('归档条目超过上限')
    normalized = [normalize(item) for item in items]
    if len({item['id'] for item in normalized}) != len(normalized):
        raise ValueError('归档条目的来源标识重复')
    body = dumps(sorted(normalized, key=sort_key, reverse=reverse))
    encoded = body.encode()
    if len(encoded) > MAX_BLOCK_BYTES:
        raise ValueError('归档内容超过大小限制')
    return body, sha256(encoded).hexdigest()


def _read_payload(row):
    body = row['payload_json']
    if len(body) > MAX_BLOCK_BYTES or sha256(body.encode()).hexdigest() != row['content_sha256']:
        raise ValueError('归档内容校验不通过')
    return json.loads(body)


def _begin_write(connection, deadline=None):
    connection.execute('PRAGMA busy_timeout=250')
    _check_deadline(deadline)
    connection.execute('BEGIN IMMEDIATE')
    _check_deadline(deadline)


def _check_deadline(deadline):
    if deadline is not None and now_china() >= deadline:
        raise TimeoutError('消息归档已到写入截止时间')


def get_snapshot_meta(day, stage):
    day = _day(day).isoformat()
    if stage not in STAGES:
        raise ValueError('消息档位无效')
    with connect() as connection:
        row = connection.execute(f'''SELECT {_SNAPSHOT_META} FROM market_news_snapshot
            WHERE trade_date=? AND stage=?''', (day, stage)).fetchone()
    return dict(row) if row else None


def last_snapshot(stage=None, *, before_at=None, statuses=None):
    conditions, args = [], []
    if stage is not None:
        if stage not in STAGES:
            raise ValueError('消息档位无效')
        conditions.append('stage=?')
        args.append(stage)
    if before_at is not None:
        conditions.append('cutoff_at<?')
        args.append(_time(before_at).isoformat())
    if statuses is not None:
        statuses = tuple(statuses)
        if not statuses or any(status not in STATUSES for status in statuses):
            raise ValueError('消息归档状态无效')
        conditions.append('status IN (' + ','.join('?' for _ in statuses) + ')')
        args.extend(statuses)
    where = 'WHERE ' + ' AND '.join(conditions) if conditions else ''
    with connect() as connection:
        row = connection.execute(f'''SELECT {_SNAPSHOT_META} FROM market_news_snapshot {where}
            ORDER BY cutoff_at DESC LIMIT 1''', args).fetchone()
    return dict(row) if row else None


def publish_snapshot(day, stage, *, cutoff_at, status, items, window_start_at=None, captured_at=None,
                     reason=None, latest_write_at=None):
    day = _day(day)
    if stage not in STAGES or status not in STATUSES:
        raise ValueError('消息档位或归档状态无效')
    if status == 'unavailable' and items:
        raise ValueError('来源不可用档位不能包含新闻条目')
    now = now_china()
    cutoff = _time(cutoff_at)
    capture = _time(captured_at) if captured_at is not None else now
    start = _time(window_start_at) if window_start_at is not None else None
    deadline = _time(latest_write_at) if latest_write_at is not None else None
    if cutoff.date() != day or cutoff > capture or capture > now or (start is not None and start >= cutoff):
        raise ValueError('消息归档日期或时间边界无效')
    if day < now.date() - timedelta(days=RETENTION_DAYS - 1):
        raise ValueError('消息归档已超出留存范围')
    body, proof = _payload(items, lambda item: _news_item(item, start, cutoff, capture),
                           sort_key=lambda item: (item['published_at'], item['id']), reverse=True)
    reason = _text(reason, '归档说明', 600, optional=True)
    with connect() as connection:
        _begin_write(connection, deadline)
        result = connection.execute('''INSERT INTO market_news_snapshot
            (trade_date,stage,status,cutoff_at,window_start_at,captured_at,reason,payload_json,content_sha256)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_date,stage) DO NOTHING''',
            (day.isoformat(), stage, status, cutoff.isoformat(), start.isoformat() if start else None,
             capture.isoformat(), reason, body, proof))
        retained = connection.execute(f'''SELECT {_SNAPSHOT_META} FROM market_news_snapshot
            WHERE trade_date=? AND stage=?''', (day.isoformat(), stage)).fetchone()
        _check_deadline(deadline)
    return {**dict(retained), 'inserted': result.rowcount == 1}


def publish_calendar(events, *, source, source_url, captured_at=None, coverage_start=None, coverage_end=None,
                     latest_write_at=None):
    source = _text(source, '日历来源标识', 120)
    source_url = _url(source_url)
    now = now_china()
    capture = _time(captured_at) if captured_at is not None else now
    deadline = _time(latest_write_at) if latest_write_at is not None else None
    if capture > now or capture.date() < now.date() - timedelta(days=RETENTION_DAYS - 1):
        raise ValueError('日历来源观测时间无效')
    start = _day(coverage_start).isoformat() if coverage_start is not None else None
    end = _day(coverage_end).isoformat() if coverage_end is not None else None
    if (start is None) != (end is None) or (start and start > end):
        raise ValueError('日历来源覆盖区间无效')
    body, proof = _payload(events, lambda item: _event_item(item, capture),
                           sort_key=lambda item: (item['event_date'], item['id']))
    with connect() as connection:
        _begin_write(connection, deadline)
        previous = connection.execute('''SELECT captured_at,source_url,coverage_start,coverage_end,content_sha256
            FROM market_news_calendar_archive WHERE source=? ORDER BY captured_at DESC LIMIT 1''',
            (source,)).fetchone()
        if previous and tuple(previous)[1:] == (source_url, start, end, proof):
            return {'source': source, 'captured_at': previous['captured_at'], 'inserted': False}
        if previous and capture.isoformat() <= previous['captured_at']:
            raise ValueError('日历来源修订必须使用新的实际观测时间')
        connection.execute('''INSERT INTO market_news_calendar_archive
            (source,captured_at,source_url,coverage_start,coverage_end,payload_json,content_sha256)
            VALUES(?,?,?,?,?,?,?)''', (source, capture.isoformat(), source_url, start, end, body, proof))
        _check_deadline(deadline)
    return {'source': source, 'captured_at': capture.isoformat(), 'inserted': True}


def prune_snapshots(anchor):
    day = _day(anchor)
    if day > now_china().date():
        raise ValueError('消息清理锚点不能晚于今天')
    cutoff = day - timedelta(days=RETENTION_DAYS - 1)
    as_of = datetime.combine(cutoff, time(), CHINA).isoformat()
    with connect() as connection:
        _begin_write(connection)
        snapshots = connection.execute('DELETE FROM market_news_snapshot WHERE trade_date<?',
                                       (cutoff.isoformat(),)).rowcount
        # Preserve the immediately preceding version per source as the basis
        # for the oldest retained observation date.
        revisions = connection.execute('''DELETE FROM market_news_calendar_archive AS old
            WHERE old.captured_at<? AND EXISTS (SELECT 1 FROM market_news_calendar_archive newer
                WHERE newer.source=old.source AND newer.captured_at>old.captured_at AND newer.captured_at<?)''',
            (as_of, as_of)).rowcount
    return {'cutoff_date': cutoff.isoformat(), 'snapshots': snapshots, 'calendar_revisions': revisions}


def _retained(day, today):
    return day >= today - timedelta(days=RETENTION_DAYS - 1)


def _cached(name, day, evidence, build, now):
    # The news screen can still read SQLite during the auction, but never
    # takes a disk cache lock or resolves market inputs in the protected window.
    if time(9, 20) <= now.time() < time(9, 30):
        return build()
    return read_block(name, day, evidence, build, live_snapshot=True)


def _news_payload(row):
    with connect() as connection:
        body = connection.execute('''SELECT payload_json,content_sha256 FROM market_news_snapshot
            WHERE trade_date=? AND stage=? AND content_sha256=?''',
            (row['trade_date'], row['stage'], row['content_sha256'])).fetchone()
    if body is None:
        raise ValueError('消息归档已清理或变更')
    return _read_payload(body)


def _missing_stage(stage, day, now):
    if not _retained(day, now.date()):
        status, reason = 'expired', '该日期已超出 30 个自然日留存范围'
    elif day > now.date() or (day == now.date() and now.time() < STAGE_TIMES.get(stage, time(17))):
        status, reason = 'pending', '尚未到该档采集时间'
    elif day == now.date() and stage == 'after_close':
        status, reason = 'pending', '等待当天盘后增量发布后归档'
    elif day == now.date() and now.time() < STAGE_GRACE_END[stage]:
        status, reason = 'pending', '正在等待本档归档'
    else:
        status, reason = 'missing', '该日期没有该档的留存记录'
    return {'stage': stage, 'status': status, 'cutoff_at': None, 'window_start_at': None,
            'captured_at': None, 'reason': reason, 'items': []}


def read_view(day=None):
    now = now_china()
    day = _day(day) if day is not None else now.date()
    stamp = day.isoformat()
    with connect() as connection:
        saved = {row['stage']: row for row in connection.execute(f'''SELECT {_SNAPSHOT_META}
            FROM market_news_snapshot WHERE trade_date=?''', (stamp,))} if _retained(day, now.date()) else {}
    stages = []
    for stage in STAGES:
        row = saved.get(stage)
        if row is None:
            stages.append(_missing_stage(stage, day, now))
            continue
        fields = {key: row[key] for key in ('stage', 'status', 'cutoff_at', 'window_start_at', 'captured_at', 'reason')}
        try:
            items = _cached('news-' + stage.replace('_', '-'), stamp,
                            (row['captured_at'], row['content_sha256']), lambda: _news_payload(row), now)
        except (ValueError, TypeError):
            stages.append({**fields, 'status': 'unavailable', 'reason': '留存归档无法通过校验', 'items': []})
        else:
            stages.append({**fields, 'items': items})
    return {'date': stamp, 'retention_days': RETENTION_DAYS, 'stages': stages}


def _calendar_revisions(as_of):
    with connect() as connection:
        return [dict(row) for row in connection.execute('''SELECT a.source,a.captured_at,a.source_url,
            a.coverage_start,a.coverage_end,a.content_sha256 FROM market_news_calendar_archive AS a
            WHERE a.captured_at<=? AND NOT EXISTS (SELECT 1 FROM market_news_calendar_archive AS newer
                WHERE newer.source=a.source AND newer.captured_at>a.captured_at AND newer.captured_at<=?)
            ORDER BY a.source''', (as_of, as_of))]


def _calendar_items(revisions, start, end):
    events = {}
    with connect() as connection:
        connection.execute('BEGIN')
        for revision in revisions:
            row = connection.execute('''SELECT payload_json,content_sha256 FROM market_news_calendar_archive
                WHERE source=? AND captured_at=? AND content_sha256=?''',
                (revision['source'], revision['captured_at'], revision['content_sha256'])).fetchone()
            if row is None:
                raise ValueError('事件日历来源版本已清理或变更')
            for event in _read_payload(row):
                if start <= event['event_date'] <= end:
                    events.setdefault(event['id'], event)
    return sorted(events.values(), key=lambda item: (item['event_date'], item['id']))


def read_calendar(day=None, days=7):
    if days not in (7, 30):
        raise ValueError('事件日历只支持未来 7 天或 30 天')
    now = now_china()
    day = _day(day) if day is not None else now.date()
    stamp = day.isoformat()
    end = (day + timedelta(days=days - 1)).isoformat()
    result = {'date': stamp, 'days': days, 'end_date': end, 'status': 'unavailable', 'events': [],
              'sources': [], 'reason': '该观察日暂无可核验的事件日历来源'}
    if not _retained(day, now.date()):
        return {**result, 'reason': '该观察日已超出 30 个自然日留存范围'}
    as_of = min(now, datetime.combine(day, time.max, CHINA)).isoformat()
    revisions = _calendar_revisions(as_of)
    if not revisions:
        return result
    evidence = [(row['source'], row['captured_at'], row['content_sha256']) for row in revisions]
    try:
        events = _cached(f'news-calendar-{days}', stamp, evidence,
                         lambda: _calendar_items(revisions, stamp, end), now)
    except (ValueError, TypeError):
        return {**result, 'reason': '事件日历归档无法通过校验'}
    covered = any(row['coverage_start'] is not None and row['coverage_start'] <= stamp
                  and row['coverage_end'] >= end for row in revisions)
    sources = [{key: value for key, value in row.items() if key != 'content_sha256'} for row in revisions]
    return {**result, 'status': 'available', 'events': events, 'sources': sources,
            'reason': None if covered else '已留存来源未确认覆盖所选全部日期'}
