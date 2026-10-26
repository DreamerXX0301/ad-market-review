"""Dated provider sector indices, independent of current constituent snapshots.

Only this collector performs I/O. Readers use the exact requested day and never
derive a historical index from a later membership list. A valid cache is read
back to its raw provider response and normalized again on every collection.
"""
from __future__ import annotations
from app.services.supplement_access import supplement_reader

from collections import Counter
from datetime import date, datetime, time as datetime_time
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

from app.adapters.providers import ProviderError
from app.core import digest
from app.infrastructure.config import published_dir

CHINA = ZoneInfo('Asia/Shanghai')
NORMALIZATION_VERSION = 'fuyao-sector-index-history-v1'
MAX_CODES = 800
REQUEST_INTERVAL = 1.2
HISTORY_PATH = '/api/a-share-index/prices/historical'
SCOPE = '当前同花顺目录中、来源真实返回的指定日行业/概念指数；不是完整历史目录或历史成分股'
_CODE = re.compile(r'\d{6}\.(?:TI|SH|SZ)')


def _now():
    return datetime.now(CHINA).isoformat()


def _day(value):
    if not isinstance(value, str):
        raise ValueError('板块行情日期必须为YYYY-MM-DD')
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError('板块行情日期必须为YYYY-MM-DD') from None
    return value


def _midnight(day):
    return int(datetime.combine(date.fromisoformat(day), datetime_time(), CHINA).timestamp() * 1000)


def _number(value, *, positive=False, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('板块历史行情数值缺失或无效')
    if value < 0 or (positive and value <= 0):
        raise ValueError('板块历史行情价格或量额越界')
    return value


def request_signature(code, target, previous):
    """Price evidence does not depend on mutable names or member snapshots."""
    return digest({'source': 'fuyao', 'thscode': code, 'date': target,
                   'previous_date': previous, 'interval': '1d',
                   'contract': NORMALIZATION_VERSION})


def normalize_sector_history(payload, topic, target, previous):
    """Require exact raw T/T-1 endpoints; optional amount stays unknown."""
    _day(target); _day(previous)
    if previous >= target:
        raise ValueError('板块前一交易日必须早于目标日')
    code = topic['topic_code']
    if not isinstance(payload, dict) or type(payload.get('code')) is not int or payload['code'] != 0:
        raise ValueError('板块历史来源业务响应未通过')
    data = payload.get('data')
    if (not isinstance(data, dict) or data.get('thscode') != code
            or data.get('interval') != '1d' or data.get('adjust') is not None):
        raise ValueError('板块历史响应代码、日线或原始指数口径不符')
    rows = data.get('item')
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ValueError('板块历史为空或超过有界窗口，未当完整空结果')
    bars = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError('板块历史行结构变化')
        stamp = raw.get('date_ms')
        if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                or not math.isfinite(stamp) or stamp != int(stamp)):
            raise ValueError('板块历史时间戳无效')
        try:
            day = datetime.fromtimestamp(stamp / 1000, CHINA).date().isoformat()
        except (ValueError, OverflowError, OSError):
            raise ValueError('板块历史时间戳无效') from None
        if not previous <= day <= target or stamp != _midnight(day):
            raise ValueError('板块历史日期超出请求窗口或不是日线零点')
        if raw.get('thscode') not in (None, code):
            raise ValueError('板块历史行证券身份不符')
        if raw.get('date') not in (None, day, day.replace('-', '')):
            raise ValueError('板块历史显式日期与时间戳冲突')
        if day in bars:
            raise ValueError('板块历史目标或前收交易日重复')
        opening, high, low, close = (_number(raw.get(key), positive=True) for key in
                                      ('open_price', 'high_price', 'low_price', 'close_price'))
        if high < max(opening, low, close) or low > min(opening, high, close):
            raise ValueError('板块历史OHLC关系无效')
        bars[day] = {'open': opening, 'high': high, 'low': low, 'close': close,
                     'volume': _number(raw.get('volume'), optional=True),
                     'amount': _number(raw.get('turnover'), optional=True)}
    if set(bars) != {previous, target}:
        raise ValueError('缺少目标日或精确前一市场交易日指数收盘，涨幅保持未知')
    current = bars[target]
    return {**topic, **current, 'thscode': code, 'date': target, 'quote_date': target,
            'previous_date': previous, 'previous_close': bars[previous]['close'],
            'change_pct': (current['close'] / bars[previous]['close'] - 1) * 100,
            'source': 'fuyao', 'price_basis': 'none', 'amount_unit': 'CNY',
            'amount_reason': '来源未提供该交易日成交额，保持未知' if current['amount'] is None else None,
            'calculation': '来源同花顺板块指数历史日K，收盘较前一市场交易日收盘',
            'date_evidence': 'provider-date_ms-exact-T-and-previous-market-session',
            'normalization_version': NORMALIZATION_VERSION,
            'request_signature': request_signature(code, target, previous)}


def _catalog(reference):
    result = {}; invalid = []; ambiguous = set(); duplicates = 0
    for field, kind in (('industry_catalog', 'industry'), ('concept_catalog', 'concept')):
        rows = reference.get(field)
        if not isinstance(rows, list):
            invalid.append({'stage': 'catalog', 'kind': kind, 'reason': '当前来源目录未留存，未当完整空目录'})
            continue
        for raw in rows:
            code = raw.get('thscode') if isinstance(raw, dict) else None
            name = raw.get('name') if isinstance(raw, dict) else None
            if not isinstance(code, str) or not _CODE.fullmatch(code) or not isinstance(name, str) or not name.strip():
                invalid.append({'stage': 'catalog', 'kind': kind, 'reason': '当前目录代码或名称无效，禁止猜测供应商标识'})
                continue
            item = {'topic_id': 'fuyao-' + code, 'topic_code': code, 'name': name, 'kind': kind}
            if code in result:
                duplicates += 1
                if result[code] != item:
                    ambiguous.add(code)
            else:
                result[code] = item
    for code in sorted(ambiguous):
        result.pop(code)
        invalid.append({'stage': 'catalog', 'topic_code': code, 'reason': '同一目录代码存在名称或分类冲突，未静默合并'})
    return result, invalid, duplicates, len(ambiguous)


def _raw_cache(cache, record):
    root = Path(cache.root).resolve()
    path = (root / str(record.get('raw_artifact', ''))).resolve()
    if (not path.is_relative_to(root) or not path.is_file()
            or sha256(path.read_bytes()).hexdigest() != record.get('raw_sha256')):
        raise ValueError('板块历史缓存原始响应缺失或SHA不符')
    return json.loads(path.read_text())


def full_sector_pair_from_cache(market, topic, target, previous):
    """Derive T/T-1 only after revalidating a full chart's immutable raw SHA.

    Current constituents are not consulted. A full chart may be partial at its
    far beginning while still proving both exact recent price endpoints.
    """
    status = market.status()
    if not status.get('data_begin') or not status.get('snapshot_id'):
        return None
    from app.services.sector_charts import plan_sector_chart, _read_cached
    plan = plan_sector_chart(market, topic['topic_code'], target)
    instrument = plan['instrument']
    if any(topic.get(key) != instrument[value] for key, value in
           (('topic_id', 'topic_id'), ('name', 'name'), ('kind', 'sector_kind'))):
        raise ValueError('完整板块K线与日行情所用当前目录身份不符')
    from app.services.sector_strategies import verified_recent_pair
    prepared = verified_recent_pair(market, topic['topic_code'], target, previous)
    if prepared is not None:
        record = prepared; pointer = prepared['pointer']; pair = prepared['bars']
    else:
        cached = _read_cached(plan, market.calendar())
        if cached is None: return None
        record, pointer = cached
        pair = [row for row in record['bars'] if row['date'] in (previous, target)]
    if len(pair) != 2 or {row['date'] for row in pair} != {previous, target}:
        return None
    # Convert the raw-revalidated daily prices back to the existing pair
    # normalizer, retaining its identity/date/amount/pct contract unchanged.
    payload = {'code': 0, 'data': {'thscode': topic['topic_code'], 'interval': '1d',
               'adjust': None, 'item': [{'date_ms': _midnight(row['date']),
               'open_price': row['open'], 'high_price': row['high'],
               'low_price': row['low'], 'close_price': row['close'],
               'volume': row['volume'], 'turnover': row['amount']} for row in pair]}}
    row = normalize_sector_history(payload, topic, target, previous)
    row.update(fetched_at=record.get('fetched_at'), raw_artifact=record.get('raw_artifact'),
               raw_sha256=record.get('raw_sha256'), full_chart_artifact=pointer['artifact'],
               full_chart_sha256=pointer['sha256'],
               price_evidence_basis='完整板块K线原始响应或增量链SHA复验后的精确T/T-1日线')
    return row


def collect_sector_history(market, target, cache, adapter, progress=None, guard=None):
    """Collect at most800 sequential codes, one provider call per uncached code.

    ProviderClient already owns its bounded three-attempt transport retry.
    This layer never retries on its own, resumes paused jobs or fetches members.
    """
    _day(target)
    if guard: guard()
    calendar = sorted(set(market.calendar()))
    if target not in calendar or target > market.status().get('data_end', ''):
        raise ValueError('板块补充目标不是已发布完成的市场交易日')
    position = calendar.index(target)
    previous = calendar[position-1] if position else None
    catalog, failures, duplicates, ambiguous = _catalog(market.reference())
    entries = list(catalog.values()); selected = entries[:MAX_CODES]
    if not entries and not failures:
        failures.append({'stage': 'catalog', 'reason': '当前来源目录为空，未当完整历史空目录'})
    for item in entries[MAX_CODES:]:
        failures.append({'stage': 'request_budget', 'topic_code': item['topic_code'],
                         'reason': '达到本次最多800个指数保护上限，未请求项保持未知'})
    items = []; cached_count = 0; full_chart_cached_count = 0; requests = 0; last_request = None
    source_blocked = None; blocked_count = 0
    observed = _now()
    if not previous:
        failures.append({'stage': 'calendar', 'date': target, 'reason': '留存日历缺少目标日前一交易日，无法核验日涨幅'})
    else:
        from contextlib import nullcontext
        from .sector_read_context import planning_scope
        status = market.status()
        # Older readers without a full-chart scope retain their pair-only path.
        scope = (planning_scope(market, target,
                 catalog={topic['topic_code']: topic for topic in selected},
                 calendar=calendar, status=status)
                 if status.get('data_begin') and status.get('snapshot_id') else nullcontext())
        with scope:
            for done, topic in enumerate(selected, 1):
                if guard: guard()
                code = topic['topic_code']; signature = request_signature(code, target, previous)
                try:
                    cached = cache.reuse('fuyao', 'sector-index', target+'-'+code, signature)
                    if cached is not None:
                        if cached.get('date') != target or cached.get('topic_code') != code:
                            raise ValueError('板块历史缓存日期或证券代码与请求不符')
                        row = normalize_sector_history(_raw_cache(cache, cached), topic, target, previous)
                        row.update(fetched_at=cached.get('fetched_at'),
                                   raw_artifact=cached.get('raw_artifact'), raw_sha256=cached.get('raw_sha256'))
                        items.append(row); cached_count += 1
                        if progress: progress(done, len(entries), code)
                        continue
                    full = full_sector_pair_from_cache(market, topic, target, previous)
                    if full is not None:
                        if guard: guard()
                        items.append(full); cached_count += 1; full_chart_cached_count += 1
                        if progress: progress(done, len(entries), code)
                        continue
                except (ValueError, TypeError, OSError, ProviderError) as error:
                    failures.append({'stage': 'cache_validation', 'topic_code': code, 'reason': str(error)})
                    if progress: progress(done, len(entries), code)
                    continue
                if source_blocked is not None:
                    blocked_count += 1
                    failures.append({'stage': 'source_blocked', 'topic_code': code,
                                     'reason': source_blocked})
                    if progress: progress(done, len(entries), code)
                    continue
                if last_request is not None:
                    delay = REQUEST_INTERVAL - (time.monotonic()-last_request)
                    if delay > 0: time.sleep(delay)
                if guard: guard()
                last_request = time.monotonic(); requests += 1
                try:
                    payload = adapter.request(HISTORY_PATH, {'thscode': code, 'interval': '1d',
                        'start': _midnight(previous), 'end': _midnight(target)+86400000-1})
                    row = normalize_sector_history(payload, topic, target, previous)
                except (ValueError, TypeError, OSError, ProviderError) as error:
                    failures.append({'stage': 'daily_history', 'topic_code': code, 'reason': str(error)})
                    if isinstance(error, ProviderError) and (error.status in (401, 403, 429) or error.code == 2001):
                        source_blocked = '来源认证、权限或限流已拒绝后续远程请求；保持未知：'+str(error)
                    if progress: progress(done, len(entries), code)
                    continue
                if guard: guard()
                try:
                    accepted = cache.accept('fuyao', 'sector-index', target+'-'+code, signature,
                        {'date': target, 'topic_code': code, 'complete': True,
                         'normalization_version': NORMALIZATION_VERSION, 'items': [row]}, payload)
                    row.update(fetched_at=accepted.get('fetched_at'),
                               raw_artifact=accepted.get('raw_artifact'), raw_sha256=accepted.get('raw_sha256'))
                    items.append(row)
                except (ValueError, TypeError, OSError, ProviderError) as error:
                    failures.append({'stage': 'cache_accept', 'topic_code': code, 'reason': str(error)})
                if progress: progress(done, len(entries), code)
    if guard: guard()
    amount_available = sum(row['amount'] is not None for row in items)
    complete = bool(entries) and not failures and len(items) == len(entries) and amount_available == len(items)
    coverage = {'catalog_count': len(entries)+ambiguous, 'selected_count': len(selected),
                'accepted_count': len(items), 'failed_count': len(failures),
                'unrequested_count': max(0, len(entries)-MAX_CODES)+blocked_count,
                'source_blocked_count': blocked_count, 'cached_count': cached_count,
                'full_chart_cached_count': full_chart_cached_count,
                'request_count': requests, 'max_codes': MAX_CODES,
                'max_provider_transport_attempts': requests*3,
                'amount_available_count': amount_available, 'amount_unknown_count': len(items)-amount_available,
                'expected_previous_date': previous, 'current_catalog': True,
                'historical_catalog_complete': False, 'membership_independent': True,
                'catalog_observed_at': observed,
                'catalog_observation_basis': '本任务读取的当前目录；不是所选历史日目录或成员档案',
                'duplicate_catalog_rows': duplicates,
                'kind_counts': {kind: sum(row['kind'] == kind for row in entries) for kind in ('industry', 'concept')},
                'complete': complete}
    if len(entries) > MAX_CODES and progress: progress(len(entries), len(entries), '目录保护上限')
    return {'date': target, 'source': 'fuyao', 'items': items, 'coverage': coverage,
            'failures': failures, 'complete': complete,
            'normalization_version': NORMALIZATION_VERSION, 'scope': SCOPE}


@supplement_reader
def sector_prices_for_date(document, day):
    """Return exact-day, versioned price proofs only, never a nearest date."""
    empty = {'date': day, 'source': 'fuyao', 'items': [], 'coverage': {},
             'failures': [], 'complete': False, 'normalization_version': NORMALIZATION_VERSION,
             'scope': SCOPE, 'unavailable_reason': '所选日没有已核验板块指数历史行情'}
    try:
        _day(day)
        anchor = _day(document.get('target_date'))
        histories = document.get('sector_index_history', {})
        if not isinstance(histories, dict): return empty
        retained = []
        for key, descriptor in histories.items():
            try:
                if (_day(key) <= anchor and isinstance(descriptor, dict)
                        and descriptor.get('date') == key and descriptor.get('source') == 'fuyao'
                        and descriptor.get('normalization_version') == NORMALIZATION_VERSION):
                    retained.append(key)
            except ValueError:
                continue
        empty['coverage'] = {'available_dates': sorted(retained), 'selected_date_available': False,
                             'available_dates_basis': '留存日期描述符；仅选择日读取文件并核验SHA与价格证明'}
        record = histories.get(day)
        if (day > anchor or not isinstance(record, dict) or record.get('date') != day
                or record.get('source') != 'fuyao' or record.get('normalization_version') != NORMALIZATION_VERSION):
            return empty
        # A long-lived head stores only per-day descriptors. Resolve one
        # selected immutable artifact, not all historic sector payloads.
        if 'items' not in record:
            relative = record.get('artifact')
            if not isinstance(relative, str): return empty
            relative_path = Path(relative)
            if relative_path.is_absolute() or '..' in relative_path.parts: return empty
            root = (published_dir()/'market-supplement').resolve()
            path = (root/relative_path).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 8*1024*1024:
                return empty
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != record.get('sha256'): return empty
            record = json.loads(raw)
            if (not isinstance(record, dict) or record.get('date') != day or record.get('source') != 'fuyao'
                    or record.get('normalization_version') != NORMALIZATION_VERSION): return empty
        previous = _day(record.get('coverage', {}).get('expected_previous_date'))
        if previous >= day or not isinstance(record.get('items'), list): return empty
    except (ValueError, TypeError, AttributeError, OSError):
        return empty
    if not isinstance(record.get('failures', []), list): return empty
    items = []; failures = list(record.get('failures', [])); seen = set()
    codes = [row.get('topic_code') for row in record['items']
             if isinstance(row, dict) and isinstance(row.get('topic_code'), str)]
    duplicates = {code for code, count in Counter(codes).items() if count > 1}
    for row in record['items']:
        try:
            code = row['topic_code']
            if (not isinstance(code, str) or not _CODE.fullmatch(code) or code in seen or code in duplicates
                    or row.get('thscode') != code or row.get('topic_id') != 'fuyao-'+code
                    or not isinstance(row.get('name'), str) or not row['name'].strip()
                    or row.get('kind') not in ('industry', 'concept')
                    or row.get('date') != day or row.get('quote_date') != day
                    or row.get('previous_date') != previous or row.get('source') != 'fuyao'
                    or row.get('normalization_version') != NORMALIZATION_VERSION
                    or row.get('request_signature') != request_signature(code, day, previous)):
                raise ValueError('板块历史行日期、身份或请求证明不符')
            close = _number(row.get('close'), positive=True)
            old = _number(row.get('previous_close'), positive=True)
            change = row.get('change_pct')
            if (isinstance(change, bool) or not isinstance(change, (int, float))
                    or not math.isfinite(change) or not math.isclose(change, (close/old-1)*100, abs_tol=1e-10, rel_tol=1e-10)):
                raise ValueError('板块历史涨幅与精确两日价格不一致')
            _number(row.get('amount'), optional=True)
            seen.add(code); items.append(dict(row))
        except (KeyError, TypeError, ValueError):
            failures.append({'stage': 'published_row_validation', 'reason': '已发布板块行的日期、身份或数值证明无效，未回填其他日期'})
    coverage = {**record.get('coverage', {}), **empty['coverage'],
                'selected_date_available': bool(items), 'accepted_count': len(items),
                'failed_count': len(failures),
                'amount_available_count': sum(row.get('amount') is not None for row in items),
                'amount_unknown_count': sum(row.get('amount') is None for row in items)}
    complete = (bool(record.get('complete')) and len(items) == len(record['items'])
                and not failures and coverage['amount_unknown_count'] == 0)
    coverage['complete'] = complete
    return {**empty, 'items': items, 'coverage': coverage, 'failures': failures,
            'complete': complete, 'unavailable_reason': None if items else empty['unavailable_reason']}
