"""Read missing prior-session events from a noon body's named original generation.

The original noon JSON is never changed. This optional evidence reader follows
no current head, opens no database, and resolves only bounded reference objects
and one exact month's immutable price file for the requested previous session.
"""
from copy import deepcopy
from datetime import date
import json
import re
import stat

import pyarrow.parquet as pq

from app.analytics.market_storage import safe_object, verify_immutable_file, load_reference
from app.infrastructure.config import published_dir
from app.services.noon_membership_context import _source_id

GENERATION = re.compile(r'market-[A-Za-z0-9_-]{1,160}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
POOLS = {'limit_break': 'breakpool', 'limit_down': 'limitdown'}
MAX_MANIFEST = 2 * 1024 * 1024
MAX_REFERENCE = 32 * 1024 * 1024
MAX_PROJECTED = 8 * 1024 * 1024
MAX_PRICE_FILE = 64 * 1024 * 1024
MAX_POOL_ROWS = 1000
SCOPE = '原午评采集代际的严格前日事件与同日未复权正成交量收盘交叉核验；不是当前事件池或完整市场推断'


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _unknown(day, reason):
    return {'date': day, 'state': 'unknown', 'source_count': None,
            'verified_count': None, 'unverified_count': None, 'items': [],
            'reason': reason, 'scope': SCOPE}


def _json(path, budget):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= budget:
        raise ValueError('原代际来源文件不在读取边界内')
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise ValueError('原代际来源结构无效')
    return document


def _file(root, generation, summary, role, names, budget):
    files = summary.get('files')
    rows = [row for row in files if isinstance(row, dict) and row.get('role') == role] if isinstance(files, list) else []
    if len(rows) != 1:
        raise ValueError('原代际没有唯一指定角色文件')
    row = rows[0]
    expected = row.get('sha256'); size = _count(row.get('bytes'))
    if (not isinstance(expected, str) or not SHA.fullmatch(expected) or size is None or not 0 < size <= budget
            or row.get('path') not in {f'published/{generation}/{name}' for name in names}):
        raise ValueError('原代际文件路径、大小或SHA无效')
    path = safe_object(root, row['path'].removeprefix('published/'))
    verify_immutable_file(path, expected, size)
    return path, expected


def _reference(root, generation, prior):
    manifest = _json(safe_object(root, generation + '/manifest.json'), MAX_MANIFEST)
    summary = manifest.get('summary')
    if (not isinstance(summary, dict) or summary.get('snapshot_id') != generation
            or summary.get('source') != 'fuyao' or not _day(summary.get('data_end'))
            or summary['data_end'] > prior or summary.get('price_basis', 'none') != 'none'):
        raise ValueError('原代际身份、来源或完成交易日不符')
    path, digest = _file(root, generation, summary, 'reference.json', ('reference.json',), MAX_REFERENCE)
    document = _json(path, MAX_REFERENCE)
    if document.get('storage_version') in ('market-reference-parts-v1', 'market-reference-parts-v2'):
        fields = document.get('fields')
        if not isinstance(fields, dict):
            raise ValueError('原代际参考字段清单无效')
        keys = ('pool_trade_date', 'pool_coverage', 'limitup', 'limitdown', 'breakpool')
        selected = {key: fields[key] for key in keys if key in fields}
        if any(not isinstance(row, dict) or _count(row.get('bytes')) is None for row in selected.values()) or sum(row['bytes'] for row in selected.values()) > MAX_PROJECTED:
            raise ValueError('原代际事件对象超过读取预算')
        reference = load_reference(path, document={'storage_version': document['storage_version'], 'fields': selected, 'indices': {}})
        if reference.get('pool_trade_date') != prior and 'pool_history' in fields:
            descriptor = fields['pool_history']
            if not isinstance(descriptor, dict) or _count(descriptor.get('bytes')) is None or descriptor['bytes'] > MAX_PROJECTED:
                raise ValueError('原代际日期事件对象超过读取预算')
            reference.update(load_reference(path, document={'storage_version': document['storage_version'],
                                                           'fields': {'pool_history': descriptor}, 'indices': {}}))
    else:
        reference = document
    history = reference.get('pool_history')
    record = reference if reference.get('pool_trade_date') == prior else history.get(prior) if isinstance(history, dict) else None
    if not isinstance(record, dict):
        raise ValueError('原代际没有严格前日事件记录')
    if record.get('source') not in (None, 'fuyao'):
        raise ValueError('原代际指定日事件来源与行情代际冲突')
    for key in ('limitup', 'limitdown', 'breakpool'):
        if key in record and (not isinstance(record[key], list) or len(record[key]) > MAX_POOL_ROWS):
            raise ValueError('原代际事件样本超过有界容量或结构无效')
    return reference, record, summary, digest


def _prices(root, generation, summary, prior, symbols):
    if not symbols:
        return {}, None
    path, _ = _file(root, generation, summary, 'daily.parquet', ('daily.manifest.json',), MAX_MANIFEST)
    descriptor = _json(path, MAX_MANIFEST)
    parts = descriptor.get('partitions')
    if descriptor.get('version') != 'market-partitions-v1' or descriptor.get('role') != 'daily.parquet' or not isinstance(parts, list) or len(parts) > 240:
        raise ValueError('原代际不具备有界月行情清单')
    selected = [row for row in parts if isinstance(row, dict) and row.get('month') == prior[:7]]
    if len(selected) != 1:
        raise ValueError('原代际没有唯一前日月份行情对象')
    part = selected[0]; digest = part.get('sha256'); size = _count(part.get('bytes'))
    if (not isinstance(digest, str) or not SHA.fullmatch(digest) or size is None or not 0 < size <= MAX_PRICE_FILE
            or part.get('object_key') != f'market-objects/daily/{digest[:2]}/{digest}.parquet'
            or not _day(part.get('data_begin')) or not _day(part.get('data_end'))
            or not part['data_begin'] <= prior <= part['data_end']):
        raise ValueError('原代际月行情路径、SHA或日期边界不符')
    price_path = safe_object(root, part['object_key']); verify_immutable_file(price_path, digest, size)
    metadata = pq.ParquetFile(price_path).metadata
    if metadata.num_rows > 500000 or metadata.num_row_groups > 128:
        raise ValueError('原代际月行情行数超过读取预算')
    rows = pq.read_table(price_path, columns=['instrument_id', 'date', 'close', 'volume', 'price_basis', 'source'],
                         filters=[('date', '=', prior), ('instrument_id', 'in', sorted(symbols))]).to_pylist()
    if len(rows) > len(symbols) or len({row['instrument_id'] for row in rows}) != len(rows):
        raise ValueError('原代际同日收盘身份重复')
    return {row['instrument_id']: row for row in rows
            if row.get('date') == prior and row.get('source') == 'fuyao' and row.get('price_basis') == 'none'}, digest


def build_prior_event_context(payload):
    """Prefer frozen pools; otherwise return independently identified old evidence."""
    payload = payload if isinstance(payload, dict) else {}
    previous = payload.get('previous_review') or {}; calendar = payload.get('calendar') or {}
    prior = previous.get('date') if isinstance(previous, dict) else None
    valid = (_day(prior) and _day(payload.get('date')) and prior < payload['date']
             and isinstance(calendar, dict) and calendar.get('verified') is True and calendar.get('previous_day') == prior)
    reason = '严格前一交易日证据缺失，未以更早日期或当前事件替代'
    output = {key: _unknown(prior, reason) for key in POOLS}
    generation = _source_id(payload) if valid else None
    frozen = previous.get('event_cohorts') if isinstance(previous, dict) else None
    frozen = frozen if isinstance(frozen, dict) else {}
    pending = []
    for key in POOLS:
        if key in frozen:
            row = frozen[key]
            output[key] = deepcopy(row) if valid and isinstance(row, dict) and row.get('date') == prior else _unknown(prior, '原冻结事件所属日期或结构不符，未追读其他资料改写')
        else:
            pending.append(key)
    digest = None
    if pending and generation is not None and GENERATION.fullmatch(generation):
        try:
            root = published_dir().resolve()
            reference, record, summary, digest = _reference(root, generation, prior)
            symbols = {row.get('thscode') or row.get('instrument_id') for key in ('limitup', 'limitdown', 'breakpool')
                       for row in record.get(key, []) if isinstance(row, dict)}
            symbols = {symbol for symbol in symbols if isinstance(symbol, str) and SYMBOL.fullmatch(symbol)}
            prices, price_sha = _prices(root, generation, summary, prior, symbols)
            # This existing helper only checks supplied rows; it performs no I/O.
            from app.services.noon_context import _event_cohorts
            cohorts = _event_cohorts(reference, prior, prices)
            coverage = record.get('pool_coverage') if record is reference else record.get('coverage')
            coverage = coverage if isinstance(coverage, dict) else {}
            for key in pending:
                name = POOLS[key]; cohort = cohorts[key]
                proof = coverage.get(name); proof = proof if isinstance(proof, dict) else {}
                declared = _count(proof.get('declared_total'))
                raw_count = cohort.get('source_count')
                complete = (proof.get('complete') is True and (declared is None or declared == raw_count)
                            and _count(proof.get('quarantined_rows')) in (None, 0)
                            and proof.get('state') in (None, 'complete', 'observed', 'ready'))
                if declared is not None and raw_count is not None:
                    cohort['source_count'] = max(declared, raw_count)
                    cohort['unverified_count'] = cohort['source_count'] - cohort['verified_count']
                if cohort['verified_count']:
                    cohort['state'] = 'observed_sample' if complete and not cohort['unverified_count'] else 'partial'
                elif complete and raw_count == 0 and declared == 0:
                    cohort['state'] = 'observed_sample'
                else:
                    cohort['state'] = 'unknown'
                cohort.update(scope=SCOPE, source_snapshot_id=generation, source_sha256=digest,
                              source_price_sha256=price_sha, source_coverage={field: proof.get(field) for field in (
                                  'complete', 'declared_total', 'identified_rows', 'observed_rows', 'quarantined_rows', 'state')})
                cohort['reason'] = ('读取原采集代际同日事件与收盘核验；' +
                    ('来源完整，仍仅描述已留存样本。' if complete else
                     f'来源覆盖不完整，声明{declared if declared is not None else "未知"}条，已核验{cohort["verified_count"]}条，隔离{proof.get("quarantined_rows", "未知")}条；不视为完整市场。'))
                output[key] = cohort
        except (OSError, ValueError, TypeError, KeyError):
            for key in pending:
                output[key] = _unknown(prior, '原采集代际前日事件或单月收盘未通过路径、身份、日期、大小或SHA核验；未读取当前资料替代')
    elif pending:
        for key in pending:
            output[key] = _unknown(prior, '未记录可核验的原采集代际，无法恢复昨日事件；不以当前事件池补写')
    known = [row for row in output.values() if row.get('state') != 'unknown']
    state = ('observed_sample' if len(known) == len(output) and all(row.get('state') == 'observed_sample' for row in known)
             else 'partial' if known else 'unknown')
    return {'date': prior, 'state': state, 'source_snapshot_id': generation,
            'event_cohorts': output, 'source_sha256': digest,
            'reason': '独立原代际证据展示，不修改午评原正文、SHA或每日采集名额'}
