"""Bounded, operator-authorized CLS telegraph metadata collector."""
from __future__ import annotations

from datetime import datetime
from hashlib import md5, sha1
import json
import re
import time
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx

CHINA = ZoneInfo('Asia/Shanghai')
ENDPOINT = 'https://www.cls.cn/v1/roll/get_roll_list'
PAGE_SIZE = 20
MAX_PAGES = 20
MAX_RESPONSE_BYTES = 256 * 1024
PAGING_SECONDS = 8
SOURCE = '财联社电报'
ARTICLE_HOSTS = {'api3.cls.cn', 'www.cls.cn', 'm.cls.cn'}
ID_PATTERN = re.compile(r'[0-9]{1,20}\Z')


def _parameters(cursor):
    # Public web-client signing documented by the MIT AKShare adapter:
    # https://github.com/akfamily/akshare/blob/main/akshare/stock/research/stock_info.py
    params = {'app': 'CailianpressWeb', 'category': '', 'last_time': cursor,
              'os': 'web', 'refresh_type': '1', 'rn': str(PAGE_SIZE), 'sv': '8.4.6'}
    encoded = urlencode(sorted(params.items())).encode()
    return {**params, 'sign': md5(sha1(encoded).hexdigest().encode()).hexdigest()}


def _page(client, cursor, deadline):
    with client.stream('GET', ENDPOINT, params=_parameters(cursor)) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=8192):
            if time.monotonic() >= deadline:
                raise ValueError('消息源响应超过采集时间预算')
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ValueError('消息源单页超出采集预算')
            body.extend(chunk)
    document = json.loads(body)
    if not isinstance(document, dict) or type(document.get('errno')) is not int or document['errno'] != 0:
        raise ValueError('消息源未返回成功回执')
    data = document.get('data')
    rows = data.get('roll_data') if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise ValueError('消息源分页结构不可核验')
    return rows


def _url(value):
    if (not isinstance(value, str) or len(value) > 2048
            or any(char.isspace() or char == '\\' or ord(char) < 32 for char in value)):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == 'https' and parsed.netloc in ARTICLE_HOSTS
                and parsed.path and parsed.path != '/'):
            return value
    except ValueError:
        pass
    return None


def _row(raw, published_at, observed):
    identifier = raw.get('id')
    identifier = str(identifier) if type(identifier) is int or isinstance(identifier, str) else ''
    if not ID_PATTERN.fullmatch(identifier):
        return None
    headline = raw.get('title')
    if not isinstance(headline, str) or not headline.strip():
        headline = raw.get('brief')
    headline = ' '.join(headline.split())[:160] if isinstance(headline, str) else ''
    url = _url(raw.get('shareurl'))
    if not headline or not url:
        return None
    topics = {}
    subjects = raw.get('subjects')
    if isinstance(subjects, list):
        for subject in subjects:
            name = subject.get('subject_name') if isinstance(subject, dict) else None
            if isinstance(name, str) and 0 < len(name := ' '.join(name.split())) <= 120:
                topics[name] = {'name': name, 'basis': '财联社来源标签'}
            if len(topics) == 12:
                break
    return {'id': f'cls:{identifier}', 'title': headline, 'source': SOURCE, 'url': url,
            'published_at': published_at.isoformat(), 'first_seen_at': observed.isoformat(),
            'topics': list(topics.values())}


def collect(client, start, cutoff, *, now=lambda: datetime.now(CHINA)):
    """Only promise a complete query when the source cursor reaches the window start."""
    first_cursor = int(cutoff.timestamp()) + 1
    cursor = first_cursor
    seen = {}
    reached_start = False
    gap = None
    deadline = time.monotonic() + PAGING_SECONDS
    for _ in range(MAX_PAGES):
        if time.monotonic() >= deadline:
            break
        try:
            rows = _page(client, cursor, deadline)
        except (httpx.HTTPError, ValueError):
            gap = '消息源请求或字段核验失败，已保留成功页'
            break
        observed = now()
        if not rows:
            reached_start = True
            break
        stamps = [row.get('ctime') if isinstance(row, dict) else None for row in rows]
        if (any(type(stamp) is not int or not 1 <= stamp <= cursor for stamp in stamps)
                or stamps != sorted(stamps, reverse=True)):
            gap = '消息源时间或分页顺序无法核验'
            break
        for raw, stamp in zip(rows, stamps):
            published_at = datetime.fromtimestamp(stamp, CHINA)
            if not start < published_at <= cutoff:
                continue
            record = _row(raw, published_at, observed)
            if record is None:
                gap = '消息源部分记录缺少有效标识、标题或原文链接'
                continue
            existing = seen.get(record['id'])
            if existing is None:
                seen[record['id']] = record
            elif any(existing[key] != record[key] for key in ('title', 'url', 'published_at')):
                gap = '消息源分页返回了相互冲突的同一记录'
        oldest = stamps[-1]
        if oldest <= int(start.timestamp()) or len(rows) < PAGE_SIZE:
            reached_start = True
            break
        # Overlap by one second so equal-ctime records at a page boundary
        # cannot silently disappear. Refuse to claim complete on a stuck cursor.
        next_cursor = oldest + 1
        if next_cursor >= cursor:
            gap = '消息源同秒分页无法继续，存在覆盖缺口'
            break
        cursor = next_cursor
    items = sorted(seen.values(), key=lambda item: (item['published_at'], item['id']), reverse=True)
    if not reached_start and gap is None:
        gap = '已达到消息采集预算，较早内容覆盖待核验'
    return {'status': ('partial' if items else 'unavailable') if gap else 'complete',
            'items': items, 'reason': gap}


def fetch_flash(start_at, cutoff_at):
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.cls.cn/telegraph'}
    with httpx.Client(headers=headers, timeout=2.0, trust_env=False, follow_redirects=False) as client:
        return collect(client, datetime.fromisoformat(start_at), datetime.fromisoformat(cutoff_at))
