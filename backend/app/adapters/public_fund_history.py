"""Dated public sector fund-flow history from a bounded, explicit directory.

The directory is the sector menu on Eastmoney's public fund-flow page. It is
not interchangeable with the broader quote API's t:2 hierarchy. Daily samples
are provider size-classified order flows, not identified institutional accounts
or an intraday capital-rotation sequence.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import unescape
from html.parser import HTMLParser
import math
import re

import httpx

from app.adapters.providers import ProviderError

DIRECTORY_URL = 'https://data.eastmoney.com/bkzj/hy.html'
HISTORY_PATH = '/api/qt/stock/fflow/daykline/get'
SOURCE = 'eastmoney-public'
CALCULATION = '供应商按成交单大小分类的主力净流入；并非真实机构账户流向'
DIRECTORY_CONTRACT = 'eastmoney-fund-page-sector-menu-v1'


class _SectorMenuParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = []; self.depth = 0; self.active = None; self.link = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'div':
            self.depth += 1
            if 'pop-cont' in (attributes.get('class') or '').split():
                if self.active is not None:
                    raise ProviderError('资金流目录出现嵌套分类，拒绝截断')
                self.sections.append([]); self.active = (len(self.sections)-1, self.depth)
        if tag == 'a' and self.active is not None:
            href = attributes.get('href') or ''
            match = re.fullmatch(r'/bkzj/(BK\d{4})\.html', href)
            if not match:
                raise ProviderError('资金流目录链接格式变化，不能认定目录完整')
            if self.link is not None:
                raise ProviderError('资金流目录出现嵌套链接')
            self.link = [match.group(1), []]

    def handle_data(self, text):
        if self.link is not None:
            self.link[1].append(text)

    def handle_endtag(self, tag):
        if tag == 'a' and self.link is not None:
            self.sections[self.active[0]].append((self.link[0], ''.join(self.link[1]).strip()))
            self.link = None
        if tag == 'div':
            if self.active is not None and self.depth == self.active[1]:
                if self.link is not None:
                    raise ProviderError('资金流目录链接未闭合')
                self.active = None
            self.depth -= 1


def parse_directory(html: str, scope: str = 'industry') -> list[dict]:
    """Only the labelled sector menu is accepted; never enumerate guessed BKs."""
    if scope not in ('industry', 'concept', 'both'):
        raise ProviderError('资金流目录仅支持industry/concept/both范围')
    labels = re.search(r'<ul\b[^>]*class=["\'][^"\']*\bpop_slider\b[^"\']*["\'][^>]*>(.*?)</ul>', html, re.S | re.I)
    if not labels:
        raise ProviderError('资金流行业目录标签结构变化')
    names = [unescape(re.sub('<[^>]+>', '', item)).strip() for item in re.findall(r'<li\b[^>]*>(.*?)</li>', labels.group(1), re.S | re.I)]
    parser = _SectorMenuParser(); parser.feed(html); parser.close()
    sections = parser.sections
    if names[:3] != ['行业', '概念', '地域'] or len(sections) != 3 or parser.active is not None:
        raise ProviderError('资金流行业/概念目录顺序无法验证')
    result = []
    for index, kind in ((0, 'industry'), (1, 'concept')):
        if scope != 'both' and scope != kind:
            continue
        links = sections[index]
        rows = [{'topic_code': code, 'name': unescape(re.sub('<[^>]+>', '', name)).strip(), 'kind': kind} for code, name in links]
        if not rows or len(rows) > 1500 or any(not row['name'] or len(row['name']) > 100 for row in rows):
            raise ProviderError('资金流公开目录为空、过大或名称格式变化')
        if len({row['topic_code'] for row in rows}) != len(rows):
            raise ProviderError('资金流公开目录包含重复代码')
        result.extend(rows)
    if len({row['topic_code'] for row in result}) != len(result):
        raise ProviderError('行业与概念目录存在代码冲突，拒绝合并')
    return sorted(result, key=lambda row: (row['kind'] != 'industry', row['topic_code']))


def _directory(client):
    for attempt in range(3):
        try:
            with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False, transport=getattr(client, 'transport', None)) as connection:
                with connection.stream('GET', DIRECTORY_URL, headers={'User-Agent': 'Mozilla/5.0 ShixuWorkbench/0.1', 'Accept': 'text/html'}) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            client.sleep(2 ** attempt)
                            continue
                    if response.status_code != 200:
                        raise ProviderError('公开行业目录返回HTTP ' + str(response.status_code))
                    chunks = []; size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 4 * 1024 * 1024:
                            raise ProviderError('公开行业目录超过4MiB上限')
                        chunks.append(chunk)
                    raw = b''.join(chunks).decode('utf-8', errors='strict')
            if any(marker in raw.lower() for marker in ('captcha', '验证码', '安全验证')):
                raise ProviderError('公开行业目录返回安全验证页面，未绕过验证')
            return raw
        except ProviderError:
            raise
        except (httpx.HTTPError, UnicodeError):
            if attempt == 2:
                raise ProviderError('公开行业目录连接或编码异常，已重试3次') from None
            client.sleep(2 ** attempt)
    raise ProviderError('公开行业目录请求未完成')


def _number(raw, *, optional=False):
    if optional and raw in (None, '', '-'):
        return None
    if isinstance(raw, bool):
        raise ProviderError('资金流数值不能是布尔值')
    try:
        value = float(raw)
    except (ValueError, TypeError):
        raise ProviderError('资金流数值字段缺失或格式变化') from None
    if not math.isfinite(value):
        raise ProviderError('资金流数值不是有限数')
    return value


def normalize_history(payload: dict, topic: dict, target: str, calendar=None) -> dict:
    """Date comes only from f51; request/capture clocks never supply quote dates."""
    date.fromisoformat(target)
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or data.get('code') != topic['topic_code'] or data.get('market') != 90:
        raise ProviderError('资金流历史返回的板块身份与请求不符')
    raw_rows = data.get('klines')
    if not isinstance(raw_rows, list) or not raw_rows or len(raw_rows) > 1000:
        raise ProviderError('资金流历史为空或超过上限，不能认定完整')
    rows = []; seen = set()
    for raw in raw_rows:
        if not isinstance(raw, str):
            raise ProviderError('资金流历史行格式变化')
        values = raw.split(',')
        if len(values) < 11:
            raise ProviderError('资金流历史字段不足')
        try:
            day = date.fromisoformat(values[0]).isoformat()
        except ValueError:
            raise ProviderError('资金流历史缺少明确交易日期') from None
        if day in seen:
            raise ProviderError('资金流历史出现重复日期')
        seen.add(day)
        if day > target:
            continue
        rows.append({'topic_id': 'eastmoney-' + topic['topic_code'], 'topic_code': topic['topic_code'], 'name': topic['name'], 'provider_history_name': data.get('name'), 'kind': topic['kind'], 'date': day,
                     'net_inflow': _number(values[1]), 'small_net_inflow': _number(values[2]), 'medium_net_inflow': _number(values[3]), 'large_net_inflow': _number(values[4]), 'super_large_net_inflow': _number(values[5]),
                     'net_inflow_pct': _number(values[6]), 'change_pct': _number(values[12], optional=True) if len(values) > 12 else None,
                     'source': SOURCE, 'calculation': CALCULATION, 'amount_unit': 'CNY', 'percentage_unit': 'pct', 'date_evidence': 'provider-f51', 'directory_name_scope': 'current'})
    rows.sort(key=lambda row: row['date'])
    if not rows or rows[-1]['date'] != target:
        raise ProviderError('资金流历史缺少目标交易日 ' + target)
    expected = sorted({day for day in calendar or [] if day <= target})[-20:]
    if calendar is not None and (not expected or expected[-1] != target):
        raise ProviderError('目标日不属于已留存完成交易日')
    selected = [row for row in rows if row['date'] >= expected[0]] if expected else rows[-20:]
    present = {row['date'] for row in selected}
    missing = [day for day in expected if day not in present]
    unexpected = [day for day in present if calendar is not None and day not in expected]
    if unexpected:
        raise ProviderError('资金流历史日期与已留存交易日历不一致')
    return {'item': selected[-1], 'history': selected, 'history_complete': not missing and (len(selected) == 20 if calendar is None else len(selected) == len(expected)), 'missing_history_dates': missing}


def fetch_sector_fund_history(client, target: str, *, scope='industry', limit=None, max_requests=200, calendar=None, progress=None, cache_lookup=None, cache_save=None, request_interval=.12, workers=3) -> dict:
    """Fetch the declared public directory, then dated histories within a budget.

    max_requests bounds logical provider calls (each has at most three attempts).
    cache_lookup(code, target) may return a previously hash-verified raw response;
    it is revalidated here and consumes no provider call. Incomplete results have
    explicit failed/unrequested codes and never imply zero flows or full coverage.
    """
    date.fromisoformat(target)
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 2 <= max_requests <= 1000:
        raise ValueError('资金流请求上限应为2至1000')
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError('资金流数量上限应为空或正整数')
    if not 1 <= workers <= 3 or not 0 <= request_interval <= 5:
        raise ValueError('资金流并发上限3，间隔需为0至5秒')
    raw_directory = _directory(client)
    catalog = parse_directory(raw_directory, scope)
    selected = catalog[:limit] if limit is not None else catalog
    budget = max_requests - 1
    pages = [{'kind': 'directory', 'url': DIRECTORY_URL, 'directory_contract': DIRECTORY_CONTRACT, 'response_text': raw_directory}]
    items = []; history = []; failures = []; pending = []; cached = 0; history_complete = 0; gaps = []

    def accept(topic, payload, origin):
        nonlocal history_complete
        normalized = normalize_history(payload, topic, target, calendar)
        if cache_save and origin == 'public-request':
            cache_save(topic['topic_code'], target, normalized, payload)
        items.append(normalized['item']); history.extend(normalized['history'])
        if normalized['history_complete']:
            history_complete += 1
        else:
            gaps.append({'topic_code': topic['topic_code'], 'dates': normalized['missing_history_dates'], 'observed_days': len(normalized['history'])})
        pages.append({'kind': 'daily_history', 'topic_code': topic['topic_code'], 'source': origin, 'response': payload})

    for topic in selected:
        payload = cache_lookup(topic['topic_code'], target) if cache_lookup else None
        if payload is not None:
            try:
                accept(topic, payload, 'validated-cache'); cached += 1
                continue
            except (ProviderError, ValueError, TypeError):
                pass
        pending.append(topic)
    requested = pending[:budget]
    unrequested = pending[budget:]
    for topic in unrequested:
        failures.append({'topic_code': topic['topic_code'], 'name': topic['name'], 'kind': topic['kind'], 'reason': '达到本次资金流请求预算，未当作成功或零资金流', 'stage': 'request_budget'})

    def fetch(topic):
        if request_interval:
            client.sleep(request_interval)
        params = {'lmt': '0', 'klt': '101', 'secid': '90.' + topic['topic_code'], 'fields1': 'f1,f2,f3,f7', 'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63', 'ut': 'b2884a393a59ad64002292a3e90d46a5'}
        return client.request('eastmoney_history', HISTORY_PATH, params)

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(fetch, topic): topic for topic in requested}
    try:
        for future in as_completed(futures):
            topic = futures[future]
            try:
                payload = future.result()
                # Persist a response even when date/identity validation fails.
                try:
                    accept(topic, payload, 'public-request')
                except (ProviderError, ValueError, TypeError) as error:
                    pages.append({'kind': 'rejected_daily_history', 'topic_code': topic['topic_code'], 'response': payload})
                    raise error
            except (ProviderError, ValueError, TypeError) as error:
                failures.append({'topic_code': topic['topic_code'], 'name': topic['name'], 'kind': topic['kind'], 'reason': str(error), 'stage': 'daily_history'})
            if progress:
                progress(len(items) + len(failures), len(selected), topic['topic_code'])
    except BaseException:
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    complete = len(items) == len(catalog) and len(selected) == len(catalog) and not failures
    coverage = {'scope': scope, 'directory_contract': DIRECTORY_CONTRACT, 'directory_source': DIRECTORY_URL, 'directory_count': len(catalog), 'selected_count': len(selected), 'accepted_count': len(items), 'failed_count': len(failures), 'unrequested_count': len(unrequested), 'unrequested_codes': [topic['topic_code'] for topic in unrequested], 'unselected_count': len(catalog)-len(selected), 'cached_count': cached, 'logical_request_count': len(requested) + 1, 'max_logical_requests': max_requests, 'max_transport_attempts': max_requests * 3,
                'mode': 'declared-directory' if len(selected) == len(catalog) else 'sample', 'complete': complete, 'history_complete_count': history_complete, 'history_20d_complete': complete and history_complete == len(items), 'history_gaps': gaps, 'scope_label': '东财资金页行业目录' if scope == 'industry' else '东财资金页概念目录' if scope == 'concept' else '东财资金页行业及概念目录', 'current_directory_not_historical_pit': True}
    return {'items': sorted(items, key=lambda row: (-row['net_inflow'], row['topic_code'])), 'history': sorted(history, key=lambda row: (row['date'], row['topic_code'])), 'date': target, 'source': SOURCE, 'complete': complete, 'coverage': coverage, 'raw_pages': pages, 'failures': failures, 'intraday_sequence_available': False, 'history_kind': 'cross-day-fund-trend', 'calculation': CALCULATION}
