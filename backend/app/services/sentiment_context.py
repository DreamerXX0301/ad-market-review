"""Read-only, dated context for E/B/S observations; no stage or trading signal.

Current retained material can describe earlier dates but is not an as-known
archive. Source fetch time is preserved and never renamed first-known time.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
from statistics import mean, median, stdev

CONTEXT_VERSION = 'dated-three-axis-context-v1'
RULE_VERSION = 'descriptive-context-v1'
INDEX_NAMES = {'000001.SH': '上证指数', '399001.SZ': '深证成指',
               '399006.SZ': '创业板指', '000688.SH': '科创50'}
GROUP_LABELS = {'ordinary_10': '普通10%已核验组', 'twenty_20': '20%已核验组',
                'thirty_30': '30%已核验组', 'st': 'ST已核验组',
                'listing': '上市阶段组', 'unknown': '制度身份待核验组'}


def finite(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def fingerprint(value):
    def clean(item):
        if isinstance(item, float) and not math.isfinite(item): return None
        if isinstance(item, float) and item.is_integer(): return int(item)
        if isinstance(item, dict): return {str(key): clean(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)): return [clean(value) for value in item]
        return item
    return sha256(json.dumps(clean(value), sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                             allow_nan=False, default=str).encode()).hexdigest()


def metric(key, label, value, formula, reason=None, unit='%'):
    return {'key': key, 'label': label, 'value': value, 'unit': unit,
            'formula': formula, 'reason': reason}


def valid_market_calendar(calendar, day=None):
    if not isinstance(calendar, list) or any(not isinstance(value, str) for value in calendar): return False
    try: dates_valid = all(date.fromisoformat(value).isoformat() == value for value in calendar)
    except ValueError: return False
    return dates_valid and calendar == sorted(set(calendar)) and (day is None or day in calendar)


def index_environment(reference, calendar, day):
    """Each index uses exact market sessions; missing bars never slide a window."""
    valid_calendar = valid_market_calendar(calendar, day)
    position = calendar.index(day) if valid_calendar else -1
    requested = calendar[max(0, position-20):position+1] if valid_calendar else []
    items = []
    for symbol, name in INDEX_NAMES.items():
        raw = reference.get('indices', {}).get(symbol, [])
        eligible = [row for row in raw if isinstance(row, dict) and row.get('date') in requested]
        counts = Counter(row['date'] for row in eligible)
        indexed = {row['date']: row for row in eligible if counts[row['date']] == 1
                   and finite(row.get('close')) is not None and row['close'] > 0}
        sources = {row.get('source') for row in eligible if row.get('source')}
        comparable = len(sources) <= 1
        window = [indexed.get(value) for value in requested]
        complete = len(requested) == 21 and all(window) and comparable
        missing = [value for value in requested if value not in indexed]
        reason = None if complete else ('观察日不在唯一有序市场日历中' if not valid_calendar else
                 '严格21个市场交易日价格预热不足' if len(requested) < 21 else
                 '指数窗口来源不一致' if not comparable else '严格窗口存在缺失、重复或无效指数价格')
        closes = [row['close'] for row in window] if complete else []
        change20 = finite((closes[-1]/closes[0]-1)*100) if complete else None
        distance = finite((closes[-1]/mean(closes[-20:])-1)*100) if complete else None
        volatility = finite(stdev([math.log(right)-math.log(left) for left, right in zip(closes, closes[1:])])*100) if complete else None
        # Even daily return never substitutes the last observed index quote.
        daily_dates = requested[-2:]
        daily = (finite((indexed[day]['close']/indexed[daily_dates[0]]['close']-1)*100)
                 if len(daily_dates) == 2 and all(value in indexed for value in daily_dates) and comparable else None)
        state = 'insufficient' if not complete or change20 is None or distance is None else 'strong' if change20 > 0 and distance > 0 else 'weak' if change20 < 0 and distance < 0 else 'mixed'
        labels = {'insufficient': '严格窗口待核验', 'strong': '指数偏强', 'weak': '指数偏弱', 'mixed': '指数混合'}
        items.append({'instrument_id': symbol, 'name': name, 'date': day, 'state': state,
                      'state_label': labels[state], 'sample_count': len(indexed) if comparable else 0,
                      'observed_count': 21, 'unknown_count': 21-(len(indexed) if comparable else 0),
                      'coverage_pct': len(indexed)/21*100 if comparable else 0,
                      'window_begin': requested[0] if requested else None, 'window_end': day,
                      'missing_dates': missing, 'duplicate_dates': sorted(value for value, count in counts.items() if count > 1),
                      'window_fingerprint': fingerprint(eligible),
                      'scope': '单指数严格21个市场交易日；波动为20个日对数收益样本标准差，未年化',
                      'metrics': [metric('index_daily_return_pct', '当日涨幅', daily, 'I(T)/I(P)−1；P为相邻市场交易日', None if daily is not None else reason),
                                  metric('index_20d_return_pct', '20日涨幅', change20, '100×[I(T)/I(T−20)−1]；21个严格交易日价格', reason),
                                  metric('index_ma20_distance_pct', '相对MA20', distance, '100×[I(T)/过去20个严格交易日收盘均值−1]', reason),
                                  metric('index_20d_volatility_pct', '20日波动', volatility, '20个严格日对数收益的样本标准差×100，ddof=1，未年化', reason)]})
    states = [item['state'] for item in items]
    state = 'strong' if all(value == 'strong' for value in states) else 'weak' if all(value == 'weak' for value in states) else 'insufficient' if all(value == 'insufficient' for value in states) else 'mixed'
    return {'label': '指数环境', 'state': state, 'state_label': '分指数观察', 'indices': items,
            'scope': '四个指数分别观察，不合成全市场收益或心理温度'}


def dated_st_state(proof, symbol, day, quote, identity):
    """Only an explicitly dated Boolean source identity may prove ST status."""
    values = []
    if identity.get('is_st_date', identity.get('identity_date')) == day and isinstance(identity.get('is_st'), bool):
        values.append(identity['is_st'])
    for kind in ('up', 'down'):
        for record, by_code, *_ in proof._records(day, kind):
            if record.get('date') != day: continue
            for row in by_code.get(symbol[:6], []):
                formal = row.get('instrument_id') or row.get('thscode')
                if isinstance(formal, str) and '.' in formal and formal.upper() != symbol: continue
                if row.get('date', day) != day or not isinstance(row.get('is_st'), bool): continue
                price = finite(row.get('last_price'))
                if (quote and finite(quote.get('close')) is not None and price is not None
                        and abs(price-quote['close']) <= .011):
                    values.append(row['is_st'])
    return values[0] if values and len(set(values)) == 1 else None


def dated_regime(symbol, day, identity, calendar, is_st):
    """Classify dated evidence, never names or rounded daily percentage moves."""
    try: listing = date.fromisoformat(identity.get('list_date')).isoformat()
    except (TypeError, ValueError): return 'unknown', '上市日期未知，不能排除上市阶段例外'
    if listing > day: return 'unknown', '上市日期晚于观察日，身份冲突'
    sessions = [value for value in calendar if listing <= value <= day]
    if len(sessions) < 6:
        if listing in calendar: return 'listing', '尚处于保守隔离的上市前五个市场交易日'
        return 'unknown', '保留日历不足以证明已过上市前五个交易日'
    if is_st is None: return 'unknown', '缺少当日明确ST身份，不从当前名称或涨幅猜测普通股'
    if is_st: return 'st', '当日来源明确ST身份；单列观察，不假定统一涨跌幅'
    if symbol.endswith('.SZ') and symbol.startswith(('300', '301')) and day >= '2020-08-24':
        return 'twenty_20', '创业板20%制度已生效且已过上市前五日'
    if symbol.endswith('.SH') and symbol.startswith(('688', '689')) and day >= '2019-07-22':
        return 'twenty_20', '科创板20%制度已生效且已过上市前五日'
    if symbol.endswith('.BJ') and day >= '2021-11-15': return 'thirty_30', '北交所30%制度期，已过保守上市阶段隔离'
    if ((symbol.endswith('.SH') and symbol.startswith(('600', '601', '603', '605')))
            or (symbol.endswith('.SZ') and symbol.startswith(('000', '001', '002', '003')))):
        return 'ordinary_10', '沪深主板普通10%组；当日非ST且已过保守上市阶段隔离'
    return 'unknown', '该代码及观察日制度不在当前明确白名单中'


def cohort_group(group_id, members, *, source_complete, identity_complete):
    n = len(members); gains = [row['return_pct'] for row in members if finite(row.get('return_pct')) is not None]
    a = sum(row.get('promotion') is True for row in members)
    f = sum(row.get('promotion') is False for row in members); u = n-a-f
    positive = sum(value > 0 for value in gains)
    denominator_known = source_complete and identity_complete
    bounds = {'lower': a/n*100, 'upper': (a+u)/n*100} if n and denominator_known else None
    positive_bounds = {'lower': positive/n*100, 'upper': (positive+n-len(gains))/n*100} if n and denominator_known else None
    def possible_median(extreme):
        return median(sorted(gains+[extreme]*(n-len(gains)))) if n else None
    median_bounds = ({'lower': finite(possible_median(-math.inf)), 'upper': finite(possible_median(math.inf))}
                     if denominator_known and n else None)
    middle = median(gains) if gains else None
    coverage = len(gains)/n*100 if n else None
    state = 'insufficient' if not denominator_known or not gains or coverage < 80 else 'strong' if middle > 0 else 'weak' if middle < 0 else 'mixed'
    labels = {'insufficient': '样本有限', 'strong': '昨日涨停样本偏强', 'weak': '昨日涨停样本偏弱', 'mixed': '昨日涨停样本分化'}
    reason = None if bounds is not None else '昨日全集或制度分组身份未齐，缺少可证明的完整组分母'
    return {'id': group_id, 'label': GROUP_LABELS[group_id], 'state': state, 'state_label': labels[state],
            'scope': '按昨日有据制度身份冻结队列，今日身份不明或改变的价格/晋级反馈保留未知',
            'sample_count': len(gains), 'observed_count': n, 'unknown_count': n-len(gains),
            'candidate_count': n, 'confirmed_count': n, 'unverified_count': 0,
            'coverage_pct': coverage, 'cohort_source_complete': source_complete, 'identity_complete': identity_complete,
            'current_identity_unknown_count': sum(not row.get('current_identity_known') for row in members),
            'promoted_count': a, 'failed_count': f, 'promotion_unknown_count': u,
            'promotion_bounds_pct': bounds, 'promotion_bounds_reason': reason,
            'positive_ratio_bounds_pct': positive_bounds,
            'median_bounds_pct': median_bounds,
            'metrics': [metric('previous_limit_up_avg_return_pct', '昨日涨停今均涨幅', mean(gains) if gains else None, '同一冻结队列中可比收盘收益等权均值'),
                        metric('previous_limit_up_median_return_pct', '昨日涨停今中位数', middle, '同一冻结队列中可比收盘收益中位数'),
                        metric('positive_ratio_pct', '正反馈比例', positive/len(gains)*100 if gains else None, '可比收益>0家数/可比收益有效家数；完整队列范围另列'),
                        metric('promotion_rate_pct', '完整队列晋级率', a/n*100 if n and denominator_known and u == 0 else None, '已确认晋级/完整冻结队列；状态有未知时只展示区间', reason if bounds is None else '有未知晋级状态，精确比例不可判' if u else None),
                        metric('promoted_count', '已确认晋级', a if n else None, '冻结队列今日已确认涨停数', unit='家'),
                        metric('failed_count', '已确认未晋级', f if n else None, '冻结队列今日已确认未涨停数，未知不计入', unit='家')]}


def provenance(market, proof, rows, day, previous, indices, *, cohort_fingerprint=None):
    """Fingerprints identify retained inputs; they do not certify as-of timing."""
    status_method = getattr(market, 'status', None)
    status = status_method() if callable(status_method) else {}
    sources = []
    for target in [value for value in (previous, day) if value]:
        for kind in ('up', 'down'):
            for record, _, source, path, *_ in proof._records(target, kind):
                sources.append({'trade_date': target, 'pool': kind, 'source': source, 'source_ref': path,
                                'fetched_at': record.get('fetched_at'), 'first_known_at': record.get('first_known_at'),
                                'source_published_at': record.get('source_published_at'),
                                'source_sha256': record.get('raw_sha256'),
                                'record_fingerprint': fingerprint(record)})
    selected = [{key: row.get(key) for key in ('instrument_id', 'date', 'trade_date', 'close', 'change_pct', 'volume', 'price_continuity', 'corporate_action_coverage_known')}
                for row in rows if row.get('date', row.get('trade_date')) == day]
    known_times = [source.get('first_known_at') for source in sources]
    market_known = status.get('first_known_at')
    # An explicit field still needs an actual timezone-aware timestamp.
    def timestamp(value):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed if parsed.tzinfo is not None else None
        except (AttributeError, ValueError, TypeError): return None
    parsed = [timestamp(value) for value in [market_known, *known_times]]
    known = bool(sources) and all(value is not None for value in parsed)
    first_known = max(parsed).isoformat() if known else None
    return {'model': CONTEXT_VERSION, 'rule_version': RULE_VERSION, 'trade_date': day,
            'mode': 'retained_retrospective', 'label': '当前留存回看',
            'input_snapshot_ids': [status['snapshot_id']] if status.get('snapshot_id') else [],
            'input_fingerprint': fingerprint({'date': day, 'snapshot_id': status.get('snapshot_id'),
                                               'sources': sources, 'rows': sorted(selected, key=lambda row: str(row['instrument_id'])),
                                               'indices': indices, 'cohort_fingerprint': cohort_fingerprint}),
            'source_records': sources, 'market_collected_at': status.get('collected_at'),
            'first_known_at': first_known, 'known_time_complete': known,
            'computed_at': datetime.now(timezone.utc).isoformat(),
            'notice': '按当前已留存材料回看；抓取时间不等于来源发布时间或历史首次可知时间。尚无完整逐日首知档案，不声称历史当时可用。'}
