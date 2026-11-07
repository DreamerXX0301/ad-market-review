"""有界午评GET采集器；凭据与活动DuckDB永不进入回执。"""
from __future__ import annotations

from datetime import datetime, time
import gzip
import io
from hashlib import sha256
import json
import math
import os

from app.infrastructure.file_operations import open_exclusive_file, path_is_link_or_reparse
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from app.adapters.providers import ProviderError
from app.core import atomic_json, raw_dir

CHINA = ZoneInfo('Asia/Shanghai')
VERSION = 'noon-snapshot-v1'
CAPTURE_START = time(11, 31)
CAPTURE_END = time(13)
STOCK_CODE = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
SECTOR_CODE = re.compile(r'\d{6}\.TI\Z')
MAX_REQUESTS = 96
MAX_BYTES = 32 * 1024 * 1024
NOON_EVENT_POOLS = {
    'limit_up': '/api/a-share/special-data/limit-up-pool',
    'limit_down': '/api/a-share/special-data/limit-down-pool',
    'limit_break': '/api/a-share/special-data/limit-break-pool',
}


class WindowExpired(ValueError):
    pass


def in_window(day, now):
    local = now.astimezone(CHINA)
    return local.date().isoformat() == day and CAPTURE_START <= local.time().replace(tzinfo=None) < CAPTURE_END


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def contains_secret(value, secret):
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(contains_secret(k, secret) or contains_secret(v, secret) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(v, secret) for v in value)
    return False


def source_time(value, day, received):
    if not finite(value) or value <= 0:
        return {'value_ms': None, 'time': None, 'state': 'unknown'}
    try:
        actual = datetime.fromtimestamp(value / 1000, CHINA)
    except (ValueError, OSError, OverflowError):
        return {'value_ms': value, 'time': None, 'state': 'invalid'}
    state = 'observed' if actual.date().isoformat() == day else 'different_date'
    if actual.timestamp() > received.timestamp() + 5:
        state = 'future'
    return {'value_ms': value, 'time': actual.isoformat(), 'state': state}


class Collector:
    """同一手动任务恢复只复用验SHA的窗口内原件，不建立新采集。"""
    def __init__(self, job_id, day, adapter, guard, now, callback=None):
        if not re.fullmatch(r'job_[0-9a-f]{32}', job_id):
            raise ValueError('午评任务身份无效')
        self.job_id, self.day, self.adapter = job_id, day, adapter
        self.guard, self.now, self.callback = guard, now, callback
        self.root = raw_dir() / 'noon'
        if path_is_link_or_reparse(self.root):
            raise ValueError('午评原件根目录不能为符号链接')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = self.root / job_id
        if path_is_link_or_reparse(self.directory):
            raise ValueError('午评原件任务目录不能为符号链接')
        self.directory.mkdir(exist_ok=True, mode=0o700)
        self.manifest = self.directory / 'manifest.json'
        self.records, self.gaps = [], []
        self.started_at = now().isoformat()
        self.last_accepted_at = self.started_at
        if path_is_link_or_reparse(self.manifest):
            raise ValueError('午评原件清单不能为符号链接')
        if self.manifest.is_file():
            if self.manifest.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('午评恢复清单超过4MiB预算')
            document = json.loads(self.manifest.read_text(encoding='utf-8'))
            if (document.get('job_id') != job_id or document.get('date') != day
                    or document.get('version') != VERSION or not isinstance(document.get('sources'), list)):
                raise ValueError('午评恢复清单身份不符')
            self.started_at = document['collected_start_at']
            start = datetime.fromisoformat(self.started_at)
            if not in_window(day, start):
                raise ValueError('午评恢复清单不属于原日期午休窗口')
            self.last_accepted_at = document.get('collected_end_at', self.started_at)
            self.records = document['sources']
            self.gaps = document.get('gaps', [])
        self.checkpoint()

    def checkpoint(self):
        atomic_json(self.manifest, {'version': VERSION, 'job_id': self.job_id, 'date': self.day,
                    'collected_start_at': self.started_at, 'collected_end_at': self.last_accepted_at,
                    'sources': self.records, 'gaps': self.gaps})
        self.manifest.chmod(0o600)

    def gap(self, domain, reason):
        value = {'domain': domain, 'reason': reason}
        if value not in self.gaps:
            self.gaps.append(value)

    def _cached(self, record, endpoint, params):
        if record.get('endpoint') != endpoint or record.get('params') != params:
            raise ValueError('午评冻结请求参数变化，拒绝复用')
        if record.get('state') != 'received':
            return None
        relative = record.get('path', '')
        expected = f'noon/{self.job_id}/{record["name"]}.json.gz'
        file = raw_dir() / relative
        if relative != expected or file.is_symlink() or not file.is_file():
            raise ValueError('午评原件引用身份或路径不符')
        if file.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('午评缓存原件超过4MiB预算')
        stored = file.read_bytes()
        if len(stored) != record['bytes'] or sha256(stored).hexdigest() != record['stored_sha256']:
            raise ValueError('午评原件存储SHA核验失败')
        with gzip.GzipFile(fileobj=io.BytesIO(stored)) as reader:
            raw = reader.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024 or sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('午评原件内容SHA核验失败')
        if not in_window(self.day, datetime.fromisoformat(record['received_at'])):
            raise ValueError('午评缓存原件不属于原午休窗口')
        return json.loads(raw)

    def request(self, name, endpoint, params=None, *, dated=False):
        self.guard()
        if not in_window(self.day, self.now()):
            raise WindowExpired('午评采集窗口已结束，未请求午后数据')
        if not re.fullmatch(r'[a-z0-9_-]{1,90}', name):
            raise ValueError('午评原件名无效')
        params = params or {}
        old = next((r for r in self.records if r.get('name') == name), None)
        if old is not None:
            return self._cached(old, endpoint, params)
        if len(self.records) >= MAX_REQUESTS:
            self.gap(name, '达到本次午评96个逻辑请求预算，剩余范围未知')
            return None
        if sum(r.get('raw_bytes', 0) for r in self.records) >= MAX_BYTES:
            self.gap(name, '达到本次午评32MiB原件预算，剩余范围未知')
            return None
        started = self.now().isoformat()
        record = {'name': name, 'domain': name, 'source': 'fuyao', 'endpoint': endpoint, 'params': params,
                  'requested_at': started, 'started_at': started}
        self.records.append(record)
        try:
            def before_attempt():
                self.guard()
                if not in_window(self.day, self.now()):
                    raise WindowExpired('午评窗口已结束，未发起新的HTTP请求或重试')
            body = self.adapter.request(endpoint, params, before_attempt=before_attempt)
            self.guard()
            received = self.now()
            record['received_at'] = received.isoformat()
            record['finished_at'] = received.isoformat()
            if not in_window(self.day, received):
                record['state'] = 'outside_window'
                self.gap(name, '响应到达时已过13:00，不纳入午评')
                raise WindowExpired('午评采集跨过13:00，窗口外响应已剔除')
            if not isinstance(body, dict) or not isinstance(body.get('data'), dict):
                raise ValueError('来源响应结构变化')
            if contains_secret(body, getattr(self.adapter, 'key', None)):
                record['state'] = 'credential_echo_rejected'
                self.gap(name, '来源响应回显凭据，未保存原件或数据')
                return None
            raw = canonical(body)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('来源单页超过4MiB')
            stored = gzip.compress(raw, mtime=0)
            target = self.directory / (name + '.json.gz')
            descriptor = open_exclusive_file(target)
            with os.fdopen(descriptor, 'wb') as writer:
                writer.write(stored)
                writer.flush()
                os.fsync(writer.fileno())
            stamp = source_time(body['data'].get('timestamp'), self.day, received)
            record.update(state='received', path=f'noon/{self.job_id}/{target.name}',
                          sha256=sha256(raw).hexdigest(), stored_sha256=sha256(stored).hexdigest(),
                          bytes=len(stored), raw_bytes=len(raw), source_timestamp=stamp,
                          quote_as_of=None)
            record['rows'] = len(body['data']['item']) if isinstance(body['data'].get('item'), list) else None
            self.last_accepted_at = received.isoformat()
            if dated and stamp['state'] in ('different_date', 'future', 'invalid'):
                record['state'] = 'invalid_source_time'
                self.gap(name, '来源总时间跨日、未来或无效，数据未纳入午评')
                return None
            if dated and stamp['state'] == 'unknown':
                self.gap(name, '来源未提供可核验总时间，逐条行情时刻也未知')
            if self.callback:
                self.callback('正在手动采集午评快照', min(85, 5 + len(self.records) * 2))
            return body
        except ProviderError as error:
            record.update(state='failed', http_status=error.status if isinstance(error.status, int) else None,
                          code=error.code if isinstance(error.code, int) else None)
            self.gap(name, '来源请求失败或权限不足，保留未知')
            return None
        except WindowExpired:
            raise
        except ValueError:
            if record.get('state') != 'received':
                record['state'] = 'invalid_response'
            self.gap(name, '来源字段、数值或单页容量未通过核验，保留未知')
            return None
        finally:
            if 'finished_at' not in record:
                record['finished_at'] = self.now().isoformat()
            self.checkpoint()

    def sources(self):
        self.checkpoint()
        raw = self.manifest.read_bytes()
        return [*self.records, {'name': 'manifest', 'kind': 'manifest',
                'path': f'noon/{self.job_id}/manifest.json', 'sha256': sha256(raw).hexdigest(),
                'stored_sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}]


def items(body):
    data = body.get('data') if isinstance(body, dict) else None
    return data['item'] if isinstance(data, dict) and isinstance(data.get('item'), list) else None


def explicit_empty_pool_page(body):
    """Exact successful zero shape; caller must verify source and capture time."""
    if (not isinstance(body, dict) or type(body.get('code')) is not int
            or body['code'] != 0 or items(body) != []):
        return False
    pagination = body['data'].get('pagination')
    expected = {'page': 1, 'pages': 0, 'size': 200, 'total': 0}
    return isinstance(pagination, dict) and all(
        type(pagination.get(key)) is int and pagination[key] == value for key, value in expected.items())


def verified_empty_noon_pool_page(body, day, source, pool):
    """The noon provider's explicit successful zero-result page, not a missing pool."""
    first_page = f'{pool}_page_1'
    midnight = int(datetime.combine(datetime.fromisoformat(day).date(), time(), CHINA).timestamp() * 1000)
    if (pool not in NOON_EVENT_POOLS or source.get('name') != first_page or source.get('domain') != first_page
            or source.get('source') != 'fuyao' or source.get('state') != 'received'
            or source.get('endpoint') != NOON_EVENT_POOLS[pool]
            or type(source.get('rows')) is not int or source['rows'] != 0
            or source.get('params') != {'date_ms': midnight, 'page': 1, 'size': 200}
            or not explicit_empty_pool_page(body)):
        return False
    started, received = (datetime.fromisoformat(source[key]) for key in ('started_at', 'received_at'))
    if (started.tzinfo is None or received.tzinfo is None or started > received
            or source.get('requested_at') != source.get('started_at')
            or not in_window(day, started) or not in_window(day, received)):
        return False
    stamp = source_time(body['data'].get('timestamp'), day, received)
    return (stamp['state'] == 'observed' and stamp == source.get('source_timestamp')
            and in_window(day, datetime.fromisoformat(stamp['time'])))


def paged(collector, name, endpoint, params=None, *, market=False, verify_empty=None):
    size = 1000 if market else 200
    rows, declared, failures = [], None, []
    for page in range(1, 21 if market else 11):
        query = {**(params or {}), **({'limit': size, 'offset': (page - 1) * size} if market else {'page': page, 'size': size})}
        try:
            body = collector.request(f'{name}_page_{page}', endpoint, query, dated=True)
        except WindowExpired:
            failures.append('窗口已结束，剩余分页未采集')
            break
        batch = items(body)
        if batch is None:
            failures.append('来源页面缺失')
            break
        data = body['data']; pagination = data if market else data.get('pagination', {})
        total = pagination.get('total') if isinstance(pagination, dict) else None
        if isinstance(total, bool) or not isinstance(total, int) or total < 0 or (declared is not None and declared != total):
            failures.append('分页总数缺失或变化')
            break
        declared = total
        pages = max(1, math.ceil(total / size))
        if not market and (pagination.get('page') != page or pagination.get('size') != size
                           or pagination.get('pages') not in ({0, 1} if total == 0 else {pages})):
            failures.append('来源分页元数据不一致')
            break
        if len(batch) > size:
            failures.append('来源单页超过冻结大小')
            break
        rows.extend(batch)
        if page >= pages:
            break
    else:
        failures.append('来源范围超过冻结分页预算')
    codes = [r.get('thscode') for r in rows if isinstance(r, dict)]
    valid = [code for code in codes if isinstance(code, str) and STOCK_CODE.fullmatch(code)]
    if len(valid) != len(rows) or len(set(valid)) != len(valid):
        failures.append('来源存在无效或重复证券身份')
    if declared is None or len(rows) != declared:
        failures.append('实际行数与来源总数不一致')
    verified_zero = False
    if declared == 0 and not failures:
        if verify_empty is not None:
            verified_zero = verify_empty(body)
        elif endpoint == NOON_EVENT_POOLS.get(name):
            record = next((entry for entry in collector.records if entry.get('name') == f'{name}_page_1'), {})
            verified_zero = verified_empty_noon_pool_page(body, collector.day, record, name)
    complete = declared is not None and not failures and (declared > 0 or verified_zero)
    if declared == 0 and not verified_zero:
        failures.append('来源为空，不能确认真实零事件')
    for reason in failures:
        collector.gap(name, reason)
    return rows, declared, complete


def trading_days(body):
    rows = items(body)
    if rows is None:
        raise ValueError('午评交易日历来源不可核验，未采集行情')
    result = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('午评交易日历结构变化')
        stamp = row.get('date_ms')
        if finite(stamp) and stamp > 0:
            result.add(datetime.fromtimestamp(stamp / 1000, CHINA).date().isoformat())
        elif isinstance(row.get('date'), str) and re.fullmatch(r'\d{8}', row['date']):
            result.add(datetime.strptime(row['date'], '%Y%m%d').date().isoformat())
        else:
            raise ValueError('午评交易日历日期无效')
    return sorted(result)
