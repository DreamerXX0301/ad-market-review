"""Dated provider-index activity; never reconstruct indices from members."""
from collections import OrderedDict, Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import threading

from app.infrastructure.config import published_dir
from app.services.market_top_amount import _window, _instance_ref
from app.services.sector_history import sector_prices_for_date, NORMALIZATION_VERSION

VERSION = 'sector-index-top10-active-seven-sessions-v1'
_cache = OrderedDict()
_lock = threading.RLock()
CACHE_LIMIT = 32
METRICS = ('change_pct', 'amount')
_CODE = re.compile(r'\d{6}\.(?:TI|SH|SZ)')
SCOPE = '最近7个市场交易日含观察日；同来源、同指数代码、同分类及同行业层级的本地已留存指数榜。上涨前10仅正涨幅，成交额前10独立统计；不是完整历史全市场目录、主线确认或资金净流入。不完整样本的已观察次数不是完整榜次数下界，补齐竞争者后排名与次数可能改变'


def clear_sector_activity_cache():
    with _lock:
        _cache.clear()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _group(row):
    source = row.get('source', row.get('quote_source'))
    kind = row.get('kind')
    version = row.get('normalization_version')
    if not isinstance(source, str) or not source or kind not in ('industry', 'concept') or version != NORMALIZATION_VERSION:
        return None
    level = row.get('classification_level', row.get('level')) if kind == 'industry' else None
    level = str(level).strip() if isinstance(level, (str, int)) and not isinstance(level, bool) and str(level).strip() else None
    return source, version, kind, level


def _identity(row):
    code = row.get('topic_code')
    return code if isinstance(code, str) and _CODE.fullmatch(code) else None


def _signature(document, dates):
    """Invalidate mutable supplement heads and replaced/corrupt immutable files."""
    histories = document.get('sector_index_history', {})
    # The reader also gates descriptors by this head-level acceptance boundary.
    # Identical day descriptors under a revised anchor are not identical facts.
    result = [('target_date', sha256(json.dumps(document.get('target_date'), sort_keys=True,
                                               default=str).encode()).hexdigest())]
    for day in dates:
        record = histories.get(day, {}) if isinstance(histories, dict) else {}
        if not isinstance(record, dict):
            result.append((day, None)); continue
        descriptor_hash = sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
        if 'items' in record:
            result.append((day, descriptor_hash))
            continue
        signature = None
        relative = record.get('artifact')
        if isinstance(relative, str) and not Path(relative).is_absolute() and '..' not in Path(relative).parts:
            root = (published_dir() / 'market-supplement').resolve()
            path = (root / relative).resolve()
            try:
                if not path.is_relative_to(root):
                    result.append((day, descriptor_hash, None)); continue
                stat = path.stat()
                if path.is_file() and stat.st_size <= 8*1024*1024:
                    signature = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except OSError:
                pass
        result.append((day, descriptor_hash, signature))
    return tuple(result)


def _daily(record, day):
    groups = defaultdict(list)
    if not isinstance(record, dict) or record.get('date') != day or not isinstance(record.get('items'), list):
        return {}
    for row in record.get('items', []):
        if not isinstance(row, dict) or row.get('quote_date', row.get('date')) != day:
            continue
        group = _group(row)
        if group is not None:
            groups[group].append(row)
    output = {}
    for group, source_rows in groups.items():
        counts = Counter(_identity(row) for row in source_rows)
        duplicate = any(count != 1 for count in counts.values()) or None in counts
        unique = [row for row in source_rows if _identity(row) and counts[_identity(row)] == 1]
        for metric in METRICS:
            invalid = any(not _number(row.get(metric)) or metric == 'amount' and row[metric] < 0 for row in source_rows)
            valid = [row for row in unique if _number(row.get(metric)) and (row[metric] > 0 if metric == 'change_pct' else row[metric] >= 0)]
            valid.sort(key=lambda row: (-row[metric], _identity(row)))
            ranked = [{'topic_code': _identity(row), 'rank': index, 'value': row[metric]}
                      for index, row in enumerate(valid[:10], 1)]
            # Fewer than ten is a known local ranking when the source explicitly
            # verified its whole small retained directory; never a global claim.
            complete = record.get('complete') is True and not duplicate and not invalid
            output[(group, metric)] = {'date': day, 'items': ranked, 'complete': complete,
                                      'observed_count': len(unique), 'ranked_count': len(ranked)}
    return output


def _facts(market, status, day, reference, current_prices, price_reader):
    dates, calendar_known = _window(market, day)
    document = reference.get('_supplement', {})
    document = document if isinstance(document, dict) else {}
    key = (id(market), status.get('snapshot_id'), day, tuple(dates), VERSION, _signature(document, dates))
    with _lock:
        cached = _cache.get(key)
        if cached is not None and cached['instance']() is market:
            _cache.move_to_end(key)
            result = deepcopy(cached['facts'])
        else:
            result = None
    if result is None:
        result = {}
        for observed_day in dates:
            record = current_prices if observed_day == day and current_prices is not None else price_reader(document, observed_day)
            result[observed_day] = _daily(record, observed_day)
        with _lock:
            _cache[key] = {'instance': _instance_ref(market), 'facts': deepcopy(result)}
            _cache.move_to_end(key)
            while len(_cache) > CACHE_LIMIT:
                _cache.popitem(last=False)
    # The visible source table remains authoritative even if its separately
    # verified proof is refreshed while the historical descriptor head is stable.
    if current_prices is not None and day in dates:
        result[day] = _daily(current_prices, day)
    return result, dates, calendar_known


def decorate_sector_activity(market, status, day, rows, reference, current_prices=None, *, price_reader=None):
    if any(row.get('quote_basis') == 'provider_index' and _group(row) and _identity(row) for row in rows):
        facts, dates, calendar_known = _facts(market, status, day, reference, current_prices,
                                             price_reader or sector_prices_for_date)
    else:
        facts = {}
        dates, calendar_known = _window(market, day)
    output = []
    summaries = {}
    for row in rows:
        identity = _identity(row)
        group = _group(row) if row.get('quote_basis') == 'provider_index' and identity else None
        annotations = {}
        for metric in METRICS:
            available = [observed for observed in dates if group and (group, metric) in facts.get(observed, {})]
            missing = [observed for observed in dates if observed not in available]
            incomplete = [observed for observed in dates if not group or facts.get(observed, {}).get((group, metric), {}).get('complete') is not True]
            complete = bool(group) and calendar_known and len(dates) == 7 and not incomplete
            appearances = []
            for observed in available:
                entry = next((item for item in facts[observed][group, metric]['items'] if item['topic_code'] == identity), None)
                if entry:
                    appearances.append({'date': observed, 'rank': entry['rank'], 'value': entry['value']})
            count = len(appearances) if appearances else 0 if complete else None
            state = 'complete' if complete else 'observed_partial' if appearances else 'unknown'
            basis = '同类正涨幅前10' if metric == 'change_pct' else '同类成交额前10'
            reason = (None if complete else '缺少同源、同类、同行业层级的完整7日本地榜；仅展示已观察次数，补齐竞争者后排名与次数可能改变，不是次数下界')
            if group is None:
                reason = '本行是成员样本派生或来源/代码/层级口径未核验；不与来源指数榜混算'
            elif group[2] == 'industry' and group[3] is None:
                basis = '来源行业层级未知目录 · ' + basis
                reason = (reason + '；' if reason else '') + '行业层级未核验，独立成组，不视为一级行业排名'
            label = ('持续活跃 · ' if count >= 2 else '近7日 · ') + str(count) + '次' if complete else ('已观察'+str(count)+'次 · 样本不完整' if count is not None else '待核验')
            annotation = {'count': count, 'dates': [item['date'] for item in appearances], 'ranks': appearances,
                          'observed_days': len(available), 'expected_days': 7, 'complete': complete,
                          'state': state, 'label': label, 'reason': reason, 'window_start': dates[0] if dates else None,
                          'window_end': day, 'scope': SCOPE, 'basis': basis, 'metric': metric,
                          'group_kind': group[2] if group else row.get('kind'), 'group_level': group[3] if group else None,
                          'quote_basis': 'provider_index' if group else 'unknown', 'source': group[0] if group else None}
            annotations[metric] = annotation
            if group is not None:
                summaries[group, metric] = {'source': group[0], 'kind': group[2], 'level': group[3],
                                            'metric': metric, 'observed_days': len(available), 'complete': complete,
                                            'missing_dates': missing, 'incomplete_dates': incomplete, 'reason': reason}
        output.append({**row, 'sector_activity_7d': annotations})
    summary = {'algorithm_version': VERSION, 'expected_days': 7, 'window_start': dates[0] if dates else None,
               'window_end': day, 'scope': SCOPE, 'metrics': list(METRICS), 'groups': list(summaries.values())}
    return output, summary
