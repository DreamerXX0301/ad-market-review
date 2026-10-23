"""Exact immutable entry-day highs/lows and conservative raw-price comparisons.

All filesystem/Parquet reads are explicit preparation outside business write
transactions. An old round never substitutes the newest generation for its
original entry snapshot, and recovered evidence never rewrites entry history.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
import math
import re
import threading

import duckdb

from app.core import connect, published_dir, hash_file
from app.analytics.market import date_ms
from app.analytics.market_storage import descriptor_path, parquet_input

FIELD = 'close_above_entry_high'
VERSION = 'entry-snapshot-exact-daily-high-v1'
LOW_FIELD = 'close_below_entry_low'
LOW_VERSION = 'entry-snapshot-exact-daily-low-v1'
_hashes = OrderedDict()
_highs = OrderedDict()
_lock = threading.RLock()


def clear_entry_high_cache():
    with _lock:
        _hashes.clear(); _highs.clear()


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None


def _e8(value):
    number = _number(value)
    return int((Decimal(str(number)) * 100000000).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if number is not None else None


def _day(value):
    try: return value if isinstance(value, str) and date.fromisoformat(value).isoformat() == value else None
    except ValueError: return None


def _basis(round_):
    try:
        value = json.loads(round_.get('entry_basis_json') or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError): return {}


def _key(round_):
    return round_['entry_snapshot_id'], round_['instrument_id'], round_['entry_business_date'], round_['entry_price_e8']


def _unknown(round_, reason, kind='high'):
    return {'version': VERSION if kind == 'high' else LOW_VERSION, 'state': 'unknown', 'date': round_['entry_business_date'],
            'instrument_id': round_['instrument_id'], 'entry_snapshot_id': round_['entry_snapshot_id'],
            'raw_' + kind: None, 'raw_' + kind + '_e8': None, 'price_basis': 'none', 'reason': reason}


def _frozen(round_, kind='high'):
    proof = _basis(round_).get('entry_' + kind)
    if not isinstance(proof, dict) or proof.get('state') != 'ready': return None
    generation = proof.get('market_snapshot_id')
    version = VERSION if kind == 'high' else LOW_VERSION
    raw = 'raw_' + kind
    valid = (proof.get('version') == version and proof.get('date') == round_['entry_business_date']
             and proof.get('instrument_id') == round_['instrument_id']
             and proof.get('entry_snapshot_id') == round_['entry_snapshot_id']
             and proof.get('price_basis') == 'none' and _basis(round_).get('price_basis') == 'none'
             and isinstance(generation, str) and re.fullmatch(r'market-[A-Za-z0-9_-]+', generation)
             and proof.get('source_artifact') in (generation + '/daily.parquet',generation + '/daily.manifest.json')
             and isinstance(proof.get('source_sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', proof['source_sha256'])
             and isinstance(proof.get(raw + '_e8'), int) and not isinstance(proof.get(raw + '_e8'), bool)
             and _e8(proof.get(raw)) == proof.get(raw + '_e8')
             and (proof[raw + '_e8'] >= round_['entry_price_e8'] > 0 if kind == 'high' else 0 < proof[raw + '_e8'] <= round_['entry_price_e8'])
             and proof.get('raw_close_e8') == round_['entry_price_e8'])
    return deepcopy(proof) if valid else None


def _artifact(snapshot_id, module, role):
    """Resolve one published artifact and verify its bytes, never a current head."""
    with connect() as connection:
        row = connection.execute("""SELECT s.*,a.object_key,a.sha256,a.byte_count
            FROM dataset_snapshot s JOIN snapshot_artifact a USING(snapshot_id)
            WHERE s.snapshot_id=? AND s.module=? AND s.state='published' AND a.role=?
            AND NOT EXISTS(SELECT 1 FROM snapshot_gc g WHERE g.snapshot_id=s.snapshot_id AND g.state<>'available')""",
            (snapshot_id, module, role)).fetchall()
    if len(row) != 1: raise ValueError('入池来源快照或唯一已发布行情文件不可用')
    descriptor = dict(row[0])
    lineage = json.loads(descriptor['lineage_json'])
    if not isinstance(lineage, dict): raise ValueError('来源快照血缘不是有效对象')
    generation = lineage.get('market_snapshot_id')
    if not isinstance(generation, str) or not re.fullmatch(r'market-[A-Za-z0-9_-]+', generation):
        raise ValueError('来源快照缺少有效不可变行情代际，不回读最新行情')
    root = published_dir().resolve(); path = (root / descriptor['object_key']).resolve()
    if not path.is_relative_to(root) or path != descriptor_path(root/generation,role).resolve():
        raise ValueError('来源行情文件路径与入池固定代际不符')
    stat = path.stat()
    if stat.st_size != descriptor['byte_count'] or not isinstance(descriptor['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', descriptor['sha256']):
        raise ValueError('来源行情文件大小或校验信息不符')
    signature = (str(root), str(path), descriptor['sha256'], stat.st_size, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns)
    with _lock: verified = signature in _hashes
    if not verified:
        if hash_file(path) != descriptor['sha256']: raise ValueError('来源行情文件校验失败，不能替换为最新数据')
        with _lock:
            _hashes[signature] = True; _hashes.move_to_end(signature)
            while len(_hashes) > 4: _hashes.popitem(last=False)
    descriptor.update(generation=generation, path=parquet_input(path), signature=signature)
    return descriptor


def load_entry_highs(rounds, *, _kind='high'):
    """Return key->proof, using a bounded exact-pair query for each old generation."""
    kind = _kind
    if kind not in ('high', 'low'): raise ValueError('unknown entry-day price kind')
    label = '最高价' if kind == 'high' else '最低价'
    raw = 'raw_' + kind
    result = {}; grouped = {}
    for round_ in rounds:
        key = _key(round_); frozen = _frozen(round_, kind)
        if frozen is not None: result[key] = frozen
        elif _basis(round_).get('price_basis') != 'none': result[key] = _unknown(round_, '入池口径不是已核验未复权价格，事件日' + label + '待核验', kind)
        else: grouped.setdefault(round_['entry_snapshot_id'], {})[key] = round_
    for snapshot, candidates in grouped.items():
        try:
            source = _artifact(snapshot, 'analysis_bars', 'daily.parquet')
            if source['price_basis'] != 'none': raise ValueError('入池日K快照不是未复权原始价格')
            pending = {}
            for key, round_ in candidates.items():
                day = round_['entry_business_date']
                if not _day(day) or not source['data_begin'] <= day <= source['data_end']:
                    result[key] = _unknown(round_, '入池事件日不在原快照明确覆盖区间中', kind); continue
                cache_key = (source['signature'], key, kind)
                with _lock: cached = _highs.get(cache_key)
                if cached is not None: result[key] = deepcopy(cached)
                else: pending[key] = round_
            if not pending: continue
            pairs = sorted({(round_['instrument_id'], round_['entry_business_date']) for round_ in pending.values()})
            con = duckdb.connect(':memory:')
            try:
                records = con.execute("""SELECT r.instrument_id,r.date,count(*) AS n,
                    min(r.open) AS open,min(r.high) AS high,min(r.low) AS low,
                    min(r.close) AS close,min(r.volume) AS volume
                    FROM read_parquet(?) r JOIN
                    (SELECT unnest(?) AS instrument_id,unnest(?) AS date) wanted
                    USING(instrument_id,date) GROUP BY r.instrument_id,r.date""",
                    [source['path'], [symbol for symbol, _ in pairs], [day for _, day in pairs]]).fetchall()
            finally: con.close()
            found = {(row[0], row[1]): row for row in records}
            for key, round_ in pending.items():
                row = found.get((round_['instrument_id'], round_['entry_business_date']))
                proof = _unknown(round_, '原入池代际缺少该事件日唯一有效日K' + label, kind)
                if row and row[2] == 1 and all(_number(value) is not None for value in row[3:]):
                    _, _, _, open_, high, low, close, _ = row
                    if low <= min(open_, close) <= max(open_, close) <= high and _e8(close) == round_['entry_price_e8']:
                        value = high if kind == 'high' else low
                        proof.update({'state': 'ready', raw: _e8(value) / 100000000, raw + '_e8': _e8(value)})
                        proof.update(raw_close_e8=_e8(close), market_snapshot_id=source['generation'],
                                     source_artifact=source['object_key'], source_sha256=source['sha256'], source_quality=source['quality'], reason=None)
                    else: proof['reason'] = '原日K价格结构或事件日收盘与本轮冻结入池价格冲突'
                result[key] = proof
                with _lock:
                    _highs[(source['signature'], key, kind)] = deepcopy(proof)
                    while len(_highs) > 1024: _highs.popitem(last=False)
        except (ValueError, OSError, KeyError, TypeError, duckdb.Error):
            for key, round_ in candidates.items():
                result[key] = _unknown(round_, '原入池行情快照缺失、损坏或未通过核验；不从最新行情回填事件日' + label, kind)
    return result


def entry_high_for(round_, prepared):
    return prepared.get(_key(round_), _unknown(round_, '未准备入池事件日最高价证据'))


def load_entry_lows(rounds):
    return load_entry_highs(rounds, _kind='low')


def entry_low_for(round_, prepared):
    return prepared.get(_key(round_), _unknown(round_, '未准备入池事件日最低价证据', 'low'))


def condition_metrics(round_, day, quote, anchor, normalized, normalization_state, normalization_reason, action_evidence, *, _kind='high'):
    """Pure strict state comparison; alert idempotency/lifecycle remains outside."""
    kind = _kind
    if kind not in ('high', 'low'): raise ValueError('unknown entry-day price kind')
    field = FIELD if kind == 'high' else LOW_FIELD
    label = '最高价' if kind == 'high' else '最低价'
    actual = None; reason = anchor.get('reason'); raw_close = quote.get('close') if quote else None
    if anchor.get('state') != 'ready': reason = reason or '入池事件日' + label + '未通过核验'
    elif not quote or quote.get('date') != day or _number(raw_close) is None or _number(quote.get('volume')) is None:
        reason = '观察日缺少唯一有效原始成交报价，保持未知'
    elif normalization_state not in ('not_required', 'verified') or _number(normalized) is None:
        reason = normalization_reason or '两日价格口径不能核对，不能直接比较未复权价格'
    elif day != round_['entry_business_date'] and action_evidence.get('state') != 'ready':
        reason = action_evidence.get('reason') or '期间公司行动覆盖不足，不能直接跨日比较'
    elif day != round_['entry_business_date'] and (not _day(action_evidence.get('coverage_start')) or not _day(action_evidence.get('coverage_end')) or not action_evidence['coverage_start'] <= round_['entry_business_date'] <= day <= action_evidence['coverage_end']):
        reason = '已发布公司行动文件未覆盖本轮入池日至观察日，保持未知'
    elif day != round_['entry_business_date'] and any(round_['entry_business_date'] <= observed <= day for observed in action_evidence.get('dates', [])):
        reason = '期间存在公司行动，未经核验的复权转换不触发事件日' + label + ('提醒' if kind == 'high' else '条件')
    else:
        actual = _e8(normalized) > anchor['raw_high_e8'] if kind == 'high' else _e8(normalized) < anchor['raw_low_e8']; reason = None
    evidence = {'entry_' + kind: deepcopy(anchor), 'observed_date': day, 'provider_observed_close_e8': _e8(raw_close),
                'normalized_observed_close_e8': _e8(normalized), 'normalization_state': normalization_state,
                'comparison_basis_id': _basis(round_).get('comparison_basis_id'), 'action_evidence': action_evidence,
                'comparison': 'strict_close_gt_frozen_entry_daily_high' if kind == 'high' else 'strict_close_lt_frozen_entry_daily_low', 'is_cross_event': False}
    return {field: actual, field + '_reason': reason, field + '_evidence': evidence}


def low_condition_metrics(round_, day, quote, anchor, normalized, normalization_state, normalization_reason, action_evidence):
    return condition_metrics(round_, day, quote, anchor, normalized, normalization_state, normalization_reason, action_evidence, _kind='low')


def load_action_evidence(snapshot_id, symbols, start, end, expected_generation=None):
    """A missing action file is unknown, never a proof of zero actions."""
    unknown = {'state': 'unknown', 'reason': '期间公司行动来源文件、日期覆盖或完整性未通过核验'}
    try:
        source = _artifact(snapshot_id, 'corporate_actions', 'corporate-actions.parquet')
        if not _day(start) or not _day(end) or start > end or not source['data_begin'] <= end <= source['data_end'] or expected_generation and source['generation'] != expected_generation:
            return {symbol: dict(unknown) for symbol in symbols}
        con = duckdb.connect(':memory:')
        try: records = con.execute('SELECT thscode,ex_date_ms FROM read_parquet(?)', [source['path']]).fetchall()
        finally: con.close()
        dated = {symbol: [] for symbol in symbols}
        for symbol, milliseconds in records:
            if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol.upper()) or _number(milliseconds) is None or milliseconds < 100000000000:
                return {symbol: dict(unknown) for symbol in symbols}
            day = date_ms(milliseconds)
            if symbol.upper() in dated and start <= day <= end: dated[symbol.upper()].append(day)
        return {symbol: {'state': 'ready', 'reason': None, 'dates': dates,
                         'snapshot_id': snapshot_id, 'market_snapshot_id': source['generation'],
                         'coverage_start': source['data_begin'], 'coverage_end': source['data_end'],
                         'source_quality': source['quality'],
                         'source_sha256': source['sha256'], 'scope': '已发布公司行动来源文件及声明日期范围；不声称交易所全集'} for symbol, dates in dated.items()}
    except (ValueError, OSError, KeyError, TypeError, OverflowError, duckdb.Error):
        return {symbol: dict(unknown) for symbol in symbols}
