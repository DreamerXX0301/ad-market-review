"""Read retained, dated reference artifacts without opening old stock frames.

Only a saved source date can make an archive date selectable. A later current
membership list is never projected backwards over the price-history interval.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

from app.infrastructure.config import published_dir, sqlite_path

REFERENCE_CACHE_LIMIT = 4
INDEX_CACHE_LIMIT = 2
CATALOG_ROW_LIMIT = 4096
_references = OrderedDict()
_indexes = OrderedDict()
_lock = threading.RLock()
_FIELDS = ('topics', 'concept_catalog', 'industry_catalog', 'sectors_snapshot',
           'sectors_snapshot_date_semantics', 'sectors_snapshot_trade_date',
           'abnormal', 'abnormal_observed_date', 'abnormal_fetched_at')


def clear_history_cache():
    with _lock:
        _references.clear()
        _indexes.clear()


def _day(value):
    if not isinstance(value, str):
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _subset(reference):
    return {key: reference[key] for key in _FIELDS if key in reference}


def _catalog():
    path = sqlite_path()
    if not path.is_file():
        return [], False
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("""SELECT s.snapshot_id,s.module,s.data_end,
            s.lineage_json,s.published_at_ms,a.object_key,a.sha256,a.byte_count
            FROM dataset_snapshot s JOIN snapshot_artifact a ON a.snapshot_id=s.snapshot_id
            WHERE s.state='published' AND s.module IN ('topics','market_review')
            AND a.role='reference.json' AND NOT EXISTS(
                SELECT 1 FROM snapshot_gc g WHERE g.snapshot_id=s.snapshot_id AND g.state<>'available')
            ORDER BY s.data_end DESC,s.published_at_ms DESC,s.snapshot_id LIMIT ?""",
            (CATALOG_ROW_LIMIT + 1,)).fetchall()
    except sqlite3.OperationalError:
        return [], False
    finally:
        connection.close()
    return [dict(row) for row in rows[:CATALOG_ROW_LIMIT]], len(rows) > CATALOG_ROW_LIMIT


def _artifact(row, root):
    try:
        generation = json.loads(row['lineage_json']).get('market_snapshot_id')
        if not isinstance(generation, str) or not re.fullmatch(r'market-[A-Za-z0-9_-]+', generation):
            return None
        path = (root / row['object_key']).resolve()
        if path != (root / generation / 'reference.json').resolve() or not path.is_relative_to(root):
            return None
        stat = path.stat()
        if stat.st_size != row['byte_count']:
            return None
        signature = (str(path), row['sha256'], stat.st_ino, stat.st_size,
                     stat.st_mtime_ns, stat.st_ctime_ns)
        return generation, path, signature
    except (ValueError, TypeError, KeyError, OSError):
        return None


def _read_reference(path, signature):
    with _lock:
        if signature in _references:
            _references.move_to_end(signature)
            return _references[signature]
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != signature[1]:
        raise ValueError('历史参考文件校验失败')
    value = _subset(json.loads(raw))
    with _lock:
        _references[signature] = value
        _references.move_to_end(signature)
        while len(_references) > REFERENCE_CACHE_LIMIT:
            _references.popitem(last=False)
    return value


def _topics_for_day(reference, snapshot_day, day):
    return [topic for topic in reference.get('topics', [])
            if isinstance(topic, dict) and isinstance(topic.get('instrument_ids'), list)
            and _day(topic.get('observed_date')) is not None
            and topic['observed_date'] <= day
            and (snapshot_day == day or topic['observed_date'] == day)]


def _source_dates(reference, snapshot_day):
    topics = reference.get('topics', [])
    dates = {_day(topic.get('observed_date')) for topic in topics if isinstance(topic, dict)}
    if _topics_for_day(reference, snapshot_day, snapshot_day):
        dates.add(snapshot_day)
    dates = {day for day in dates if day and _topics_for_day(reference, snapshot_day, day)}
    abnormal = _day(reference.get('abnormal_observed_date')) if isinstance(reference.get('abnormal'), list) else None
    return sorted(dates), abnormal


def _archive_index():
    root = published_dir().resolve()
    rows, truncated = _catalog()
    descriptors = []
    seen = set()
    listed_generations = set()
    failures = 0
    for row in rows:
        try:
            listed_generations.add(json.loads(row['lineage_json']).get('market_snapshot_id'))
        except (ValueError, TypeError):
            failures += 1
        artifact = _artifact(row, root)
        if artifact is None:
            failures += 1
            continue
        generation, path, signature = artifact
        key = (generation, row['data_end'], signature)
        if key in seen:
            continue
        seen.add(key)
        descriptors.append({'generation': generation, 'snapshot_day': row['data_end'],
                            'published_at_ms': row['published_at_ms'],
                            'path': str(path), 'signature': signature})
    fingerprint = (str(root), str(sqlite_path()), truncated, failures,
                   tuple(sorted(str(value) for value in listed_generations)),
                   tuple((item['generation'], item['snapshot_day'], item['published_at_ms'],
                          item['signature']) for item in descriptors))
    with _lock:
        if fingerprint in _indexes:
            _indexes.move_to_end(fingerprint)
            return _indexes[fingerprint]
    entries = []
    for descriptor in descriptors:
        try:
            reference = _read_reference(Path(descriptor['path']), descriptor['signature'])
            dates, abnormal = _source_dates(reference, descriptor['snapshot_day'])
            entries.append({**descriptor, 'topic_dates': dates, 'abnormal_date': abnormal})
        except (OSError, ValueError, TypeError):
            failures += 1
    result = {'entries': entries, 'listed_generations': listed_generations,
              'failure_count': failures, 'truncated': truncated}
    with _lock:
        _indexes[fingerprint] = result
        _indexes.move_to_end(fingerprint)
        while len(_indexes) > INDEX_CACHE_LIMIT:
            _indexes.popitem(last=False)
    return result


def resolve_reference(kind, day, market_status, current_reference):
    """Return only branch-local dated metadata, never a replacement global ref."""
    index = _archive_index()
    entries = list(index['entries'])
    generation = market_status.get('snapshot_id')
    # Fixtures and older non-catalog imports may have a current trusted ref. It
    # is only a dated source, never a fallback around a damaged catalog entry.
    current = None
    if generation not in index['listed_generations']:
        current = _subset(current_reference)
        topic_dates, abnormal = _source_dates(current, market_status['data_end'])
        entries.append({'generation': generation, 'snapshot_day': market_status['data_end'],
                        'published_at_ms': 0, 'topic_dates': topic_dates,
                        'abnormal_date': abnormal, 'current': True})
    available = sorted({value for entry in entries
                        for value in (entry['topic_dates'] if kind == 'sectors' else [entry['abnormal_date']])
                        if value and value <= market_status['data_end']})
    candidates = [entry for entry in entries if day in entry['topic_dates']] if kind == 'sectors' else [entry for entry in entries if entry['abnormal_date'] == day]
    candidates.sort(key=lambda entry: (entry['snapshot_day'] == day, entry['published_at_ms'] or 0), reverse=True)
    chosen = None
    reference = {}
    for entry in candidates:
        try:
            source = current if entry.get('current') else _read_reference(Path(entry['path']), entry['signature'])
            selected = _subset(source)
            if kind == 'sectors':
                selected['topics'] = _topics_for_day(source, entry['snapshot_day'], day)
                if not selected['topics']:
                    continue
            elif selected.get('abnormal_observed_date') != day or not isinstance(selected.get('abnormal'), list):
                continue
            chosen = entry
            reference = deepcopy(selected)
            break
        except (ValueError, OSError, TypeError):
            continue
    if kind == 'sectors':
        notice = '仅使用选择日已有留存、当日所知的成员样本；成员采集日期可能早于盘面日，须不晚于观察日。日增量沿用成员，完整成员需手动刷新；这不是当日新采集或完整历史时点成分。存档日期是离散日期，不保证中间每日连续；未确认交易日的行业指数快照不冒充指数涨跌幅。'
        label = '行业与题材成员存档'
    else:
        notice = '仅显示名单来源观察日与选择日完全一致的来源监控名单；日期无留存即未知，监控标签不等于交易所正式函件。'
        label = '异动与监管名单存档'
    if index['failure_count']:
        notice += f' {index["failure_count"]}条存档缺失或校验失败，已跳过。'
    if index['truncated']:
        notice += ' 存档目录超过本次4096条元数据保护上限，仅列已核验范围。'
    coverage = {'label': label, 'start': available[0] if available else None,
                'end': available[-1] if available else None, 'available_dates': available,
                'basis': '已留存、日期可核验的成员或监控名单快照', 'notice': notice,
                'selected_date_available': chosen is not None, 'partial': True,
                'source_snapshot_id': chosen['generation'] if chosen else None,
                'source_snapshot_day': chosen['snapshot_day'] if chosen else None}
    return reference, coverage


def data_coverage(kind, status, day, reference, calendar, page=None):
    """Describe this feature's actual dates, separately from global OHLC dates."""
    if kind in ('overview', 'emotion', 'gainers'):
        notice = ('指数、市场广度和技术观察按已留存日行情计算；首日、预热或缺报价处保持未知。'
                  '涨跌停、封板和异动等附加来源有独立覆盖，不能把本区间理解为整页所有字段完整。')
        if kind == 'gainers':
            notice += ' 近30日涨幅需要T与T-30两个市场交易日端点，最初30日不足时不排名。'
        return {'label': '已留存行情推导范围', 'start': status.get('data_begin'),
                'end': status.get('data_end'), 'basis': '已发布日行情推导，附加来源单独核验',
                'notice': notice, 'selected_date_available': day in calendar, 'partial': True}
    supplement = reference.get('_supplement', {})
    if kind == 'ladder':
        dates = set()
        for value, pool in reference.get('pool_history', {}).items():
            if isinstance(pool.get('limitup'), list):
                dates.add(value)
        if isinstance(reference.get('limitup'), list):
            dates.add(reference.get('pool_trade_date'))
        for key in ('annual_pools', 'public_pools'):
            dates.update(value for value, pool in supplement.get(key, {}).items() if isinstance(pool.get('items'), list))
        dates.update(row.get('date') for row in reference.get('ladder', {}).get('item', []))
        details = sorted(value for value, pool in supplement.get('public_pools', {}).items() if pool.get('items') and _day(value) and value <= status['data_end'])
        notice = ('涨跌停与连板使用已留存逐日来源池，整池覆盖与逐股可核验覆盖不同；不完整时次数只是已确认下界。'
                  f' 封板细节由近20日采集任务逐日留存，当前可读{len(details)}日；只按实际存档日期展示，缺字段保持未知。')
        label = '涨跌停与梯队来源存档'
    elif kind == 'flow':
        dates = set()
        for record in supplement.get('fund_flows', {}).values():
            dates.update(row.get('date') for row in record.get('items', []) + record.get('history', []))
        details = []
        coverage = (page or {}).get('coverage', {})
        notice = ('仅为当前行业目录留存的跨日资金趋势；每次任务按近20日窗口核验，实际可回看按已留存离散日，有日期不代表全行业覆盖。不代表全部概念或全天盘中轮动。'
                  f' 所选日已核验{coverage.get("accepted_count", 0)}个行业，目录{coverage.get("directory_count", "未知")}个；失败或预算未请求均保留缺口。')
        label = '行业日级资金实际留存'
    else:
        raise ValueError('unsupported market coverage kind')
    dates = sorted(value for value in dates if _day(value) and value <= status['data_end'])
    result = {'label': label, 'start': dates[0] if dates else None,
              'end': dates[-1] if dates else None, 'available_dates': dates,
              'basis': '逐日已留存来源事件池' if kind == 'ladder' else '供应商明确日期的行业日级历史序列',
              'notice': notice, 'selected_date_available': day in dates, 'partial': True}
    if kind == 'ladder':
        result['detail_available_dates'] = details
    return result
