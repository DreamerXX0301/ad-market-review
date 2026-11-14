"""Bounded quote capture for an explicitly frozen intraday monitoring slot.

This module owns no scheduler, database or market publication. Batch timestamps
are evidence about the response, never fabricated per-security quote times.
"""
from collections import Counter
from datetime import date, datetime
from hashlib import sha256
import json
import re

import httpx

from app.adapters.providers import ProviderError
from app.services.noon_collection import CHINA, canonical, contains_secret, finite

ENDPOINT = '/api/a-share/prices/snapshot'
BATCH_SIZE = 100
MAX_INSTRUMENTS = 2000
MAX_BATCHES = 20
MAX_WINDOW_SECONDS = 120
MAX_RESPONSE_BYTES = 512 * 1024
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_SECONDS = 5
STOCK_CODE = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)\Z')


class CaptureExpired(ValueError):
    """The frozen slot is over; remaining observations stay unknown."""


def _local(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('盘中监控时刻必须包含时区')
    return value.astimezone(CHINA)


def _milliseconds(value):
    return int(value.timestamp() * 1000)


def _unknown(identifier, day, reason, *, received=None, stamp=None, source_sha=None):
    return dict(instrument_id=identifier, latest_price=None, change_pct=None, open=None,
                high=None, low=None, volume=None, turnover=None, previous_close=None,
                trade_date=day, received_at_ms=received, source_time_ms=stamp,
                quote_time_ms=None, quality='unknown', valid=False, price_basis='none',
                source_sha256=source_sha, reason=reason)


def _quote(raw, day, received, stamp, source_sha):
    fields = {'latest_price': 'last_price', 'change_pct': 'price_change_ratio_pct',
              'open': 'open_price', 'high': 'high_price', 'low': 'low_price',
              'volume': 'volume', 'turnover': 'turnover', 'previous_close': 'prev_price'}
    values = {name: raw.get(field) for name, field in fields.items()}
    if any(not finite(value) for value in values.values()):
        return None, '报价、涨跌幅或成交量额缺失或无效'
    if any(values[key] <= 0 for key in fields if key != 'change_pct'):
        return None, '报价或累计成交量额为零/负值，不能用旧价推定有效成交'
    lo, hi = values['low'], values['high']
    if lo > hi + 1e-8 or any(not lo - 1e-8 <= values[key] <= hi + 1e-8
                            for key in ('open', 'latest_price')):
        return None, '最新价或开盘价与累计高低价范围不一致'
    change = (values['latest_price'] / values['previous_close'] - 1) * 100
    if abs(change - values['change_pct']) > .001:
        return None, '涨跌幅与最新价、前收不一致'
    if 'price_change' in raw and (not finite(raw['price_change'])
            or abs(values['latest_price'] - values['previous_close'] - raw['price_change']) > .011):
        return None, '涨跌额与最新价、前收不一致'
    return dict(instrument_id=raw['thscode'], **values, trade_date=day,
                received_at_ms=received, source_time_ms=stamp, quote_time_ms=None,
                quality='batch_time_only', valid=True, price_basis='none',
                source_sha256=source_sha, reason=None), None


def collect_quotes(instrument_ids, *, day, scheduled_at, deadline_at, adapter, guard, now, progress=None):
    """Read at most 20 serial batches; never retry or fill from earlier quotes.

    ``guard`` must raise when the caller's lease, cancellation or frozen
    configuration no longer permits capture. Those exceptions propagate.
    Unknown quotes are explicit, including identifiers not reached by the
    slot deadline. Source envelopes are bounded canonical JSON objects for the
    caller to persist after collection, outside all network transactions.
    """
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('盘中监控日期无效')
    scheduled, deadline = _local(scheduled_at), _local(deadline_at)
    if (scheduled.date().isoformat() != day or deadline.date().isoformat() != day
            or not 0 < (deadline - scheduled).total_seconds() <= MAX_WINDOW_SECONDS):
        raise ValueError('盘中监控冻结窗口无效或超过120秒')
    if not isinstance(instrument_ids, (list, tuple)) or len(instrument_ids) > MAX_INSTRUMENTS:
        raise ValueError('盘中监控证券范围须为最多2000只的冻结列表')
    if any(not isinstance(identifier, str) or not STOCK_CODE.fullmatch(identifier)
           for identifier in instrument_ids):
        raise ValueError('盘中监控仅接受已核验A股证券代码')
    identifiers = list(dict.fromkeys(instrument_ids))
    started = _local(now())
    quotes = {identifier: _unknown(identifier, day, '本轮尚未采集') for identifier in identifiers}
    sources, gaps = [], {}
    received_bytes = 0
    attempts = 0

    def reject(batch, reason, **evidence):
        for identifier in batch:
            quotes[identifier] = _unknown(identifier, day, reason, **evidence)
            gaps[identifier] = reason

    def guard_window():
        guard()
        observed = _local(now())
        if not scheduled <= observed < deadline:
            raise CaptureExpired('当前时刻不在本轮冻结采集窗口，未请求或接受迟到报价')
        return observed

    stopped_reason = None
    for offset in range(0, len(identifiers), BATCH_SIZE):
        batch = identifiers[offset:offset + BATCH_SIZE]
        try:
            observed = guard_window()
            remaining = (deadline - observed).total_seconds()
            request_timeout = httpx.Timeout(min(MAX_REQUEST_SECONDS, remaining))
            # Provider pacing happens before this hook; recheck the actual
            # remaining window immediately before transport starts as well.
            def before_attempt():
                nonlocal attempts
                current = guard_window()
                if attempts >= MAX_BATCHES:
                    raise CaptureExpired('达到本轮20次实际来源请求预算')
                # The provider waits for its shared pacing before this hook.
                # Its options are resolved afterwards, so update the same
                # timeout object to the actual transport-start allowance.
                allowance = min(MAX_REQUEST_SECONDS, (deadline - current).total_seconds())
                for part in ('connect', 'read', 'write', 'pool'):
                    setattr(request_timeout, part, allowance)
                attempts += 1

            body = adapter.request(ENDPOINT, {'thscodes': ','.join(batch)},
                                   before_attempt=before_attempt, max_attempts=1,
                                   timeout=request_timeout)
            received = guard_window()
        except CaptureExpired as error:
            stopped_reason = str(error)
            reject(identifiers[offset:], stopped_reason)
            break
        except ProviderError as error:
            try:
                guard_window()
            except CaptureExpired as expired:
                stopped_reason = str(expired)
                reject(identifiers[offset:], stopped_reason)
                break
            denied = (error.status in (401, 403, 429)
                      or str(error.code) in ('2001', '2003', '4001'))
            reason = '来源认证、权限或限流拒绝，停止本轮后续请求' if denied else '来源请求失败，本批报价未知且不补旧值'
            reject(identifiers[offset:] if denied else batch, reason)
            if denied:
                stopped_reason = reason
                break
            continue

        received_ms = _milliseconds(received)
        if contains_secret(body, getattr(adapter, 'key', None)):
            reject(batch, '来源响应疑似回显凭据，未保存原件或报价', received=received_ms)
            continue
        try:
            if not isinstance(body, dict):
                raise ValueError('来源响应不是对象')
            raw = canonical(body)
            if len(raw) > MAX_RESPONSE_BYTES or received_bytes + len(raw) > MAX_CAPTURE_BYTES:
                raise ValueError('来源原件超过本轮容量预算')
        except (ValueError, TypeError, OverflowError, RecursionError):
            reject(batch, '来源响应结构或容量未通过核验，未保存原件或报价', received=received_ms)
            continue

        source_sha = sha256(raw).hexdigest()
        data = body.get('data')
        stamp = data.get('timestamp') if isinstance(data, dict) else None
        source_at, stamp_ms = None, None
        try:
            if finite(stamp) and stamp > 0 and float(stamp).is_integer():
                source_at = datetime.fromtimestamp(stamp / 1000, CHINA)
                stamp_ms = int(stamp)
        except (ValueError, OSError, OverflowError):
            pass
        source = dict(sha256=source_sha, payload=json.loads(raw), received_at_ms=received_ms,
                      source_time_ms=stamp_ms, requested_ids=batch, state='unknown')
        sources.append(source)
        received_bytes += len(raw)
        evidence = dict(received=received_ms, stamp=stamp_ms, source_sha=source_sha)
        if str(body.get('code')) in ('2001', '2003', '4001'):
            stopped_reason = '来源认证、权限或限流拒绝，停止本轮后续请求'
            source['state'] = 'rejected'
            reject(identifiers[offset:], stopped_reason, **evidence)
            break
        if (source_at is None or source_at.date().isoformat() != day
                or stamp_ms < _milliseconds(scheduled) - 120_000 or stamp_ms > received_ms + 5000):
            reject(batch, '来源批次时间缺失、错日、过旧或晚于接收时刻，单股时间仍未知', **evidence)
            continue
        if type(body.get('code')) is not int or body['code'] != 0 or not isinstance(data.get('item'), list):
            reject(batch, '来源业务码或报价列表结构未通过核验', **evidence)
            continue
        rows = data['item']
        if (len(rows) > BATCH_SIZE or any(not isinstance(row, dict)
                or not isinstance(row.get('thscode'), str) or row['thscode'] not in batch for row in rows)):
            reject(batch, '来源返回非本批证券、无效身份或超过本批容量，未混入其他证券', **evidence)
            continue
        counts = Counter(row['thscode'] for row in rows)
        by_id = {row['thscode']: row for row in rows}
        for identifier in batch:
            if counts[identifier] != 1:
                reject([identifier], '本批证券报价缺失或重复，未选择任一重复值', **evidence)
                continue
            quote, reason = _quote(by_id[identifier], day, received_ms, stamp_ms, source_sha)
            if reason:
                reject([identifier], reason, **evidence)
            else:
                quotes[identifier] = quote
        source['state'] = 'accepted' if all(quotes[key]['valid'] for key in batch) else 'partial'
        if progress:
            progress('采集关注股票盘中快照', min(95, round((offset + len(batch)) / len(identifiers) * 95)))

    ended = _local(now())
    for identifier in identifiers:
        if not quotes[identifier]['valid']:
            gaps.setdefault(identifier, quotes[identifier]['reason'])
    return dict(quotes_by_id=quotes, sources=sources,
                gaps=[dict(instrument_id=identifier, reason=reason) for identifier, reason in gaps.items()],
                collected_start_at=started.isoformat(), collected_end_at=ended.isoformat(),
                quality='complete' if all(quote['valid'] for quote in quotes.values()) else 'partial',
                request_count=attempts, response_bytes=received_bytes, stopped_reason=stopped_reason)
