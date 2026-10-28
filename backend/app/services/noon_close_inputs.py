"""Bounded same-day projections from one named immutable publication.

Only the worker calls this module. No current-head following, provider requests,
active DuckDB or full-history daily-frame loading is involved. The resulting
SQLite evidence is a derived projection, not a vendor response.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

import pyarrow.parquet as pq

from app.core import dumps, published_dir
from app.analytics.market_storage import safe_object, verify_immutable_file, load_reference
from app.services.noon_collection import finite
from app.services.noon_context import _event_cohorts

GENERATION = re.compile(r'market-[A-Za-z0-9_-]{1,160}\Z')
SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
MAX_STOCKS = 20000
MAX_PROJECTED = 8 * 1024 * 1024
MAX_PARQUET = 128 * 1024 * 1024
INDEX_CODES = ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')


def _json(path, budget):
    if not path.is_file() or not 0 < path.stat().st_size <= budget:
        raise ValueError('收盘来源对象大小超过有界预算或缺失')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('收盘来源清单结构无效')
    return value


def _role(root, generation, summary, role, names, budget):
    rows = [x for x in summary.get('files', []) if isinstance(x, dict) and x.get('role') == role]
    if len(rows) != 1:
        raise ValueError('收盘来源没有唯一指定角色文件')
    row = rows[0]
    if (row.get('path') not in {f'published/{generation}/{name}' for name in names}
            or not isinstance(row.get('sha256'), str) or not SHA.fullmatch(row['sha256'])
            or isinstance(row.get('bytes'), bool) or not isinstance(row.get('bytes'), int)
            or not 0 < row['bytes'] <= budget):
        raise ValueError('收盘来源角色身份、大小或SHA无效')
    path = safe_object(root, row['path'].removeprefix('published/'))
    verify_immutable_file(path, row['sha256'], row['bytes'])
    return path, {'role': role, 'sha256': row['sha256'], 'bytes': row['bytes']}


def _reference(root, generation, summary, day):
    path, proof = _role(root, generation, summary, 'reference.json', ('reference.json',), 32 * 1024 * 1024)
    document = _json(path, 32 * 1024 * 1024)
    proofs = [proof]
    if document.get('storage_version') not in ('market-reference-parts-v1', 'market-reference-parts-v2'):
        return document, proofs
    fields = document.get('fields', {})
    if not isinstance(fields, dict):
        raise ValueError('收盘参考字段清单无效')
    keys = ('calendar', 'pool_trade_date', 'pool_coverage', 'limitup', 'limitdown', 'breakpool')
    selected = {k: fields[k] for k in keys if k in fields}
    def read_projection(chosen, indices=None):
        descriptors = list(chosen.values()) + [r for parts in (indices or {}).values() for r in parts]
        if (any(not isinstance(x, dict) or isinstance(x.get('bytes'), bool) or not isinstance(x.get('bytes'), int) for x in descriptors)
                or sum(x['bytes'] for x in descriptors) > MAX_PROJECTED):
            raise ValueError('收盘参考投影超过有界预算')
        proofs.extend({'role': 'reference-projection', 'sha256': x['sha256'], 'bytes': x['bytes']} for x in descriptors)
        return load_reference(path, document={'storage_version': document['storage_version'], 'fields': chosen, 'indices': indices or {}})
    index_parts = document.get('indices', {})
    indices = {symbol: [x for x in index_parts.get(symbol, []) if isinstance(x, dict) and x.get('month') == day[:7]]
               for symbol in INDEX_CODES} if isinstance(index_parts, dict) else {}
    reference = read_projection(selected, indices)
    if reference.get('pool_trade_date') != day and 'pool_history' in fields:
        reference.update(read_projection({'pool_history': fields['pool_history']}))
    return reference, proofs


def _prices(root, generation, summary, day):
    path, proof = _role(root, generation, summary, 'daily.parquet', ('daily.manifest.json',), 2 * 1024 * 1024)
    descriptor = _json(path, 2 * 1024 * 1024)
    parts = descriptor.get('partitions', [])
    if descriptor.get('version') != 'market-partitions-v1' or descriptor.get('role') != 'daily.parquet' or not isinstance(parts, list) or len(parts) > 240:
        raise ValueError('收盘日线缺少已验收月分片清单')
    chosen = [x for x in parts if isinstance(x, dict) and x.get('month') == day[:7]]
    if len(chosen) != 1:
        raise ValueError('收盘日线没有唯一指定月份')
    part = chosen[0]; digest = part.get('sha256'); size = part.get('bytes')
    if (not isinstance(digest, str) or not SHA.fullmatch(digest)
            or part.get('object_key') != f'market-objects/daily/{digest[:2]}/{digest}.parquet'
            or isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_PARQUET
            or not isinstance(part.get('data_begin'), str) or not isinstance(part.get('data_end'), str)
            or not part['data_begin'] <= day <= part['data_end']):
        raise ValueError('收盘日线分片身份或日期无效')
    target = safe_object(root, part['object_key']); verify_immutable_file(target, digest, size)
    metadata = pq.ParquetFile(target).metadata
    if metadata.num_rows > 1000000 or metadata.num_row_groups > 256:
        raise ValueError('收盘日线月份超过有界行数')
    fields = ['instrument_id', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'source', 'price_basis']
    rows = pq.read_table(target, columns=fields, filters=[('date', '=', day)]).to_pylist()
    if len(rows) > MAX_STOCKS or len({x.get('instrument_id') for x in rows}) != len(rows):
        raise ValueError('收盘日线证券重复或超出容量')
    if any(not isinstance(x.get('instrument_id'), str) or not SYMBOL.fullmatch(x['instrument_id'])
           or x.get('date') != day or x.get('source') != 'fuyao' or x.get('price_basis') != 'none' for x in rows):
        raise ValueError('收盘日线日期、身份或价格口径不符')
    return rows, [proof, {'role': 'daily-month-projection', 'sha256': digest, 'bytes': size}]


def load_inputs(generation, day, noon_payload):
    """Return content inputs and separate origin metadata, with exact-day proofs."""
    if not isinstance(generation, str) or not GENERATION.fullmatch(generation):
        raise ValueError('收盘核验需要明确不可变行情代际')
    root = published_dir()
    manifest_path = safe_object(root, generation + '/manifest.json')
    manifest = _json(manifest_path, 2 * 1024 * 1024); summary = manifest.get('summary', {})
    if (not isinstance(summary, dict) or summary.get('snapshot_id') != generation
            or summary.get('source') != 'fuyao' or summary.get('price_basis', 'none') != 'none'
            or not isinstance(summary.get('data_end'), str) or summary['data_end'] < day):
        raise ValueError('收盘代际身份、日期或口径无效')
    reference, proofs = _reference(root, generation, summary, day)
    if day not in reference.get('calendar', []):
        raise ValueError('收盘来源未留存该交易日')
    prices, price_proofs = _prices(root, generation, summary, day)
    if not prices:
        raise ValueError('收盘来源没有严格同日价格')
    proofs += price_proofs
    from app.services.market_supplement import load_supplement_with_fingerprint
    supplement, fingerprint = load_supplement_with_fingerprint(generation, dates=(day,), fields=('annual_pools', 'annual_down_pools', 'public_pools', 'sector_index_history'))
    if not isinstance(supplement, dict) or supplement.get('snapshot_id') not in (None, generation):
        raise ValueError('同日补充数据结构或行情身份不符')
    if supplement.get('failure_reason'):
        raise ValueError('同日盘面补充原件未通过核验，未把损坏当作无覆盖')
    reference['_supplement'] = supplement
    indexed = {r['instrument_id']: r for r in prices}
    events = _event_cohorts(reference, day, indexed)
    record = reference if reference.get('pool_trade_date') == day else reference.get('pool_history', {}).get(day, {})
    coverages = record.get('pool_coverage', record.get('coverage', {})) if isinstance(record, dict) else {}
    for key, name in (('limit_up', 'limitup'), ('limit_down', 'limitdown'), ('limit_break', 'breakpool')):
        pool = events[key]
        declared_complete = isinstance(coverages, dict) and isinstance(coverages.get(name), dict) and coverages[name].get('complete') is True
        pool['complete'] = bool(declared_complete and pool.get('source_count') is not None
                                and pool.get('unverified_count') == 0 and pool.get('verified_count') == len(pool['items']))
        if pool['complete']:
            pool['state'] = 'observed'
    noon_index = {r.get('instrument_id'): r for r in noon_payload.get('stocks', []) if isinstance(r, dict)}
    stocks = []
    for original in prices:
        row = dict(original); noon_row = noon_index.get(row['instrument_id'], {})
        dated_noon = noon_row.get('price_basis') == 'none' and all(noon_row[key] == day for key in ('date', 'trade_date', 'quote_date') if key in noon_row)
        prior = noon_row.get('prev_close') if dated_noon else None
        close = row.get('close')
        row.update(latest_price=close, change_pct=(close / prior - 1) * 100 if finite(close) and close > 0 and finite(prior) and prior > 0 else None,
                   currency='CNY', amount_unit='CNY' if summary.get('amount_unit') == 'CNY' else None)
        stocks.append(row)
    indices = []
    for symbol in INDEX_CODES:
        matching = [r for r in reference.get('indices', {}).get(symbol, []) if isinstance(r, dict) and r.get('date') == day]
        if len(matching) != 1:
            continue
        row = dict(matching[0]); old = next((r for r in noon_payload.get('indices', []) if r.get('instrument_id') == symbol), {})
        dated_noon = old.get('price_basis') == 'none' and all(old[key] == day for key in ('date', 'trade_date', 'quote_date') if key in old)
        prior = old.get('prev_close') if dated_noon else None; close = row.get('close')
        row.update(instrument_id=symbol, latest_price=close, date=day, price_basis='none', source='fuyao', currency='CNY', amount_unit='CNY' if summary.get('amount_unit') == 'CNY' else None,
                   change_pct=(close / prior - 1) * 100 if finite(close) and close > 0 and finite(prior) and prior > 0 else None)
        indices.append(row)
    from app.services.sector_history import sector_prices_for_date, NORMALIZATION_VERSION
    sector_descriptor = supplement.get('sector_index_history', {}).get(day, {})
    if isinstance(sector_descriptor, dict) and 'artifact' in sector_descriptor and 'items' not in sector_descriptor:
        path = safe_object(root / 'market-supplement', sector_descriptor['artifact'])
        digest = sector_descriptor.get('sha256')
        if not isinstance(digest, str) or not SHA.fullmatch(digest) or not path.is_file() or not 0 < path.stat().st_size <= MAX_PROJECTED:
            raise ValueError('同日板块原件身份或大小无效')
        verify_immutable_file(path, digest, path.stat().st_size)
        proofs.append({'role': 'sector-day-projection', 'sha256': digest, 'bytes': path.stat().st_size})
    sector = sector_prices_for_date(supplement, day)
    sectors = []
    for raw in sector.get('items', []):
        if raw.get('price_basis') != 'none' or raw.get('source') != 'fuyao' or raw.get('normalization_version') != NORMALIZATION_VERSION:
            continue
        sectors.append({**{key: raw.get(key) for key in ('topic_code', 'name', 'kind', 'date', 'quote_date',
            'previous_date', 'previous_close', 'close', 'change_pct', 'amount', 'open', 'high', 'low',
            'source', 'price_basis', 'amount_unit', 'normalization_version', 'request_signature')},
            'instrument_id': raw['topic_code'], 'currency': 'CNY'})
    if len(sectors) > 5000:
        raise ValueError('收盘行业概念指数超过单日投影预算')
    # Generation identities live only in provenance, not the semantic input
    # digest: an unchanged day in a new publication must not create a revision.
    for pool in events.values():
        pool.pop('scope', None)
    quote_sources = [r for r in noon_payload.get('sources', []) if isinstance(r, dict) and str(r.get('name', '')).startswith('stocks_page_')]
    amount_comparable = bool(summary.get('amount_unit') == 'CNY' and quote_sources
                             and all(r.get('source') == 'fuyao' and r.get('state') == 'received' for r in quote_sources))
    content = {'date': day, 'source': 'fuyao', 'price_basis': 'none', 'currency': 'CNY', 'amount_unit': 'CNY' if summary.get('amount_unit') == 'CNY' else None,
               'amount_comparable': amount_comparable, 'stocks': sorted(stocks, key=lambda r: r['instrument_id']),
               'indices': indices, 'events': events, 'sectors': sectors,
               'pool_semantics': {'limit_break': 'unknown'}, 'max_focus_items': MAX_STOCKS}
    if len(dumps(content).encode()) > MAX_PROJECTED:
        raise ValueError('收盘单日派生证据超过8MiB预算')
    origin = {'kind': 'derived-same-day-projection', 'notice': '按所列原SHA核验后提取的单日派生证据，非供应商原始响应；不依赖整个旧代际保留。',
              'source_snapshot_id': generation, 'manifest_sha256': sha256(manifest_path.read_bytes()).hexdigest(), 'objects': proofs,
              'supplement_index_sha256': fingerprint[3] if fingerprint else None}
    return content, origin
