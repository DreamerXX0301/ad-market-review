"""Dated, read-only market observations and an untriggered next-session checklist.

This is a transparent descriptive rule set. It neither promotes the offline
six-stage research draft nor predicts a bottom, a leader, or an executable trade.
All inputs stop at the observation date; later prices and current memberships
must not change a historical checklist.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import math
import re

from app.services.chart_limits import _combine, _evidence, _pattern_identity_matches, _pattern_dates_match
from app.services.market_amount import amount_change_metadata
from app.services.sentiment_context import INDEX_NAMES, fingerprint, valid_market_calendar
from app.services.strategy_limits import LimitConditionEvidence

VERSION = 'dated-market-outlook-v1'
MAX_SOURCE_CANDIDATES = 1024
TOPICS_LIMIT = 3
TOPIC_STOCKS_LIMIT = 3
STOCKS_LIMIT = 5
EXPANSION_THRESHOLD_PCT = 20
ICE_LOOKBACK_SESSIONS = 5
STOCK = re.compile(r'\d{6}\.(SH|SZ|BJ)')
# Exact, published labels only. These describe trading eligibility or index
# membership, not an industrial/policy direction. Original catalog and ladders
# retain them; this exclusion applies solely to this small direction shortlist.
TOPIC_FILTER_VERSION = 'direction-label-exclusions-v1'
NON_DIRECTION_LABELS = frozenset({'融资融券', '融资标的', '融券标的', '转融券标的',
    '沪股通', '深股通', '陆股通', '沪港通', '深港通', 'MSCI概念', 'MSCI中国', 'MSCI成份',
    '富时罗素概念', '富时罗素', '标普道琼斯A股', '上证50', '上证180', '沪深300',
    '中证500', '中证1000', '中证2000', '深证100', '创业板指', '科创50'})
NORMALIZED_NON_DIRECTION_LABELS = frozenset(value.casefold() for value in NON_DIRECTION_LABELS)
NOTICE = ('依据所选交易日及以前的当前留存证据生成条件预案，不是次日行情预测。'
          '所有触发条件尚未观察；不自动下单、加自选或调整仓位。历史日期为留存材料回看，'
          '不代表当时盘前已经生成或可知。')
RULES = [
    {'id': 'temperature', 'label': '广度温度',
     'rule': '仅用同口径沪深上涨/(上涨+下跌+平盘)×100；未知不补0，样本可比率不足80%不判阶段。'},
    {'id': 'ice', 'label': '冰点', 'rule': '广度温度≤15且上涨家数<500；只描述当日低广度，不认定底部。'},
    {'id': 'climax', 'label': '高潮',
     'rule': '温度≥85、上涨≥3000；完整来源候选逐股同日原始收盘及正成交量核验后，沪深涨停≥80、跌停≤5。缺池或冲突不确认高潮。'},
    {'id': 'repair', 'label': '修复', 'rule': '严格相邻、同口径且可比率均≥80%的前日温度≤35，今日>35，回升≥15个百分点。'},
    {'id': 'ebb', 'label': '退潮', 'rule': '严格相邻、同口径且可比率均≥80%的前日温度≥60，今日<60，回落≥15个百分点。'},
    {'id': 'broad_rise', 'label': '普涨', 'rule': '温度≥60且上涨≥3000，且未命中前述优先规则。'},
    {'id': 'mixed', 'label': '混沌', 'rule': '未命中前述规则，且已知指数环境、广度或昨日涨停价格反馈方向有明确冲突；缺数据本身不叫混沌。'},
    {'id': 'divergence', 'label': '分化', 'rule': '广度可判但未命中其他规则；不强行套入完整情绪周期。'},
    {'id': 'direction_shortlist', 'label': '方向候选范围',
     'rule': '仅本页方向候选排除明确的融资/互联互通资格及指数成员标签，采用公开固定名称表；原目录与梯队保持不变，政策和产业题材仍保留。'},
    {'id': 'turnover_expansion', 'label': '显著放量修复',
     'rule': '与盘面总览相同的沪深京A股股票成交额，严格前一市场交易日同口径增加至少20%；20%是本项目透明默认阈值，不是经研究证明的最优参数。'},
    {'id': 'bottom_candidate', 'label': '短底观察',
     'rule': '严格前一交易日已观察冰点、当日广度修复且成交额显著放大，并有四指数当日低点与收盘风险参照时，仅记短底候选；不把后续确认回填候选日。'},
    {'id': 'entry_gate', 'label': '禁止开仓与解除条件',
     'rule': '冰点、退潮或必要广度/成交额证据未知时禁止开仓；修复路径须满足量价门槛，趋势路径独立核验。放量修复候选只解除到转强确认后试错，未来触发仍未观察。'},
    {'id': 'entry_routes', 'label': '趋势与接力分别复核',
     'rule': '必要广度和相邻成交额可比后，四指数偏强且广度温度≥60可复核趋势；超短接力另需昨板反馈偏强及普通10%组证据。阶段标签不单独否决同样的量价条件。'},
    {'id': 'candidate_replay', 'label': '候选后续观察',
     'rule': '只回放截至观察日最近20个严格市场交易日；后续任一指数收盘低于候选日低点即失效，价格齐且未破低点、广度≥60记延续，缺资料待核验；不确认底部。'},
]


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _positive(value):
    value = _number(value)
    return value if value is not None and value > 0 else None


def _count(value):
    value = _number(value)
    return int(value) if value is not None and value >= 0 and int(value) == value else None


def _day(value):
    try:
        return value if isinstance(value, str) and date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _reading(breadth):
    values = [_count(breadth.get(key)) for key in ('up', 'down', 'flat')]
    if any(value is None for value in values):
        return None, None
    comparable = sum(values)
    if not comparable:
        return None, None
    total = _count(breadth.get('total', breadth.get('observed')))
    expected = _count(breadth.get('expected_current_universe'))
    unknown, missing = _count(breadth.get('unknown')), _count(breadth.get('missing'))
    denominator = max(total or 0, expected or 0, comparable + (unknown or 0) + (missing or 0))
    return values[0] / comparable * 100, comparable / denominator * 100 if denominator else None


def _bounded_reading(breadth, universe_upper_bound):
    value, original_coverage = _reading(breadth)
    comparable = sum(_count(breadth.get(key)) or 0 for key in ('up', 'down', 'flat'))
    observed = max(_count(breadth.get('total', breadth.get('observed'))) or 0,
                   comparable + (_count(breadth.get('unknown')) or 0))
    expected = _count(breadth.get('expected_current_universe'))
    expected_known = (breadth.get('universe_expected_known', expected is not None) is True
                      and expected is not None and expected > 0)
    upper = _count(universe_upper_bound)
    upper = upper if upper is not None and upper > 0 else None
    bound_known = expected_known or upper is not None
    denominator = max(observed, expected or 0,
                      comparable + (_count(breadth.get('unknown')) or 0) + (_count(breadth.get('missing')) or 0), upper or 0)
    coverage = comparable / denominator * 100 if value is not None and denominator and bound_known else None
    return {'value': value, 'coverage_pct': coverage,
            'observed_coverage_pct': comparable / observed * 100 if value is not None and observed else None,
            'coverage_denominator_count': denominator if bound_known else None,
            'universe_expected_known': expected_known, 'universe_upper_bound': upper,
            'universe_bound_known': bound_known,
            'coverage_basis': 'known_expected_universe' if expected_known else
                              'retained_master_upper_bound' if upper is not None else 'unbounded_observed_sample'}


def _metric(dimension, key):
    matches = [row for row in dimension.get('metrics', [])
               if isinstance(row, dict) and row.get('key') == key]
    return _number(matches[0].get('value')) if len(matches) == 1 else None


def _breadth_scope(value):
    # market_service gives the retained SH/SZ series a clearer display label.
    # These two exact labels refer to the same existing source calculation;
    # no other unknown universe is treated as equivalent merely by wording.
    if value in ('沪深已留存可比A股样本', '沪深已留存可比标的；除权未知剔除'):
        return 'retained-shsz-comparable'
    return value


def _history_matches(reading, retained):
    scope = _breadth_scope(reading.get('scope'))
    if not isinstance(scope, str) or not scope or scope != _breadth_scope(retained.get('scope')):
        return False
    if reading.get('date', retained.get('date')) != retained.get('date'):
        return False
    if any(_count(reading.get(key)) != _count(retained.get(key)) for key in ('up', 'down', 'flat')):
        return False
    if reading.get('source') and retained.get('source') and reading['source'] != retained['source']:
        return False
    return True


def _temperature(day, calendar, breadth, previous, history, universe_upper_bound):
    valid = valid_market_calendar(calendar, day)
    reading = _bounded_reading(breadth, universe_upper_bound)
    value, coverage = reading['value'], reading['coverage_pct']
    if breadth.get('date', day) != day:
        value, coverage = None, None
    position = calendar.index(day) if valid else -1
    prior_day = calendar[position - 1] if position > 0 else None
    prior = _bounded_reading(previous or {}, universe_upper_bound)
    prior_value, prior_coverage = prior['value'], prior['coverage_pct']
    current_history = [row for row in history if isinstance(row, dict) and row.get('date') == day]
    previous_history = [row for row in history if isinstance(row, dict) and row.get('date') == prior_day]
    current_conflict = len(current_history) > 1 or (len(current_history) == 1 and not _history_matches(breadth, current_history[0]))
    previous_conflict = (len(previous_history) > 1 or (previous and previous.get('date') == prior_day
                         and len(previous_history) == 1 and not _history_matches(previous, previous_history[0])))
    consistent = not current_conflict and not previous_conflict
    adjacent = bool(prior_day and previous and previous.get('date') == prior_day
                    and _breadth_scope(previous.get('scope')) == _breadth_scope(breadth.get('scope'))
                    and previous.get('source') == breadth.get('source')
                    and len(current_history) == len(previous_history) == 1
                    and _breadth_scope(current_history[0].get('scope')) == _breadth_scope(previous_history[0].get('scope'))
                    and consistent
                    and prior_coverage is not None and prior_coverage >= 80)
    prior_value = prior_value if adjacent else None
    return {**reading, 'value': value, 'previous_value': prior_value,
            'delta': value - prior_value if value is not None and prior_value is not None else None,
            'scope': breadth.get('scope', '沪深已留存可比A股样本'), 'coverage_pct': coverage,
            'previous_date': prior_day if adjacent else None,
            'evidence_consistent': consistent,
            'calendar_valid': valid, 'comparable_count': sum(_count(breadth.get(key)) or 0 for key in ('up', 'down', 'flat')),
            'universe_upper_bound_scope': '当前留存沪深个股主表数量的保守范围上界，包含上市日期未知或可能非当日有效者；不声称历史真实应有证券数'}


def _axes(sentiment, temperature, day):
    environment = sentiment.get('index_environment', {})
    indices = [row for row in environment.get('indices', [])
               if isinstance(row, dict) and row.get('date', day) == day]
    index_states = [row.get('state') for row in indices]
    index_complete = (len(indices) == 4 and {row.get('instrument_id') for row in indices} == set(INDEX_NAMES)
                      and all(row.get('date', day) == day for row in indices)
                      and all(value in ('strong', 'weak', 'mixed') for value in index_states))
    index_state = ('strong' if index_complete and all(value == 'strong' for value in index_states) else
                   'weak' if index_complete and all(value == 'weak' for value in index_states) else
                   'mixed' if index_complete else 'unknown')
    index_labels = {'strong': '四指数环境偏强', 'weak': '四指数环境偏弱',
                    'mixed': '指数环境分化', 'unknown': '指数窗口待核验'}
    index_evidence = [{'instrument_id': row.get('instrument_id'), 'name': row.get('name'),
                       'state': row.get('state'), 'state_label': row.get('state_label'),
                       'return_20d_pct': _metric(row, 'index_20d_return_pct'),
                       'ma20_distance_pct': _metric(row, 'index_ma20_distance_pct'),
                       'coverage_pct': row.get('coverage_pct')} for row in indices]
    value = temperature['value']
    broad_known = value is not None and temperature['coverage_pct'] is not None and temperature['coverage_pct'] >= 80
    broad_state = ('unknown' if not broad_known else 'strong' if value >= 60 else 'weak' if value <= 40 else 'mixed')
    short = sentiment.get('short_term', {})
    coverage = _number(short.get('coverage_pct'))
    short_known = (short.get('cohort_source_complete') is True and coverage is not None and 80 <= coverage <= 100
                   and _count(short.get('sample_count')) not in (None, 0))
    short_state = short.get('state') if short_known and short.get('state') in ('strong', 'weak', 'mixed') else 'unknown'
    ordinary_groups = [row for row in short.get('groups', []) if isinstance(row, dict) and row.get('id') == 'ordinary_10']
    ordinary = ordinary_groups[0] if len(ordinary_groups) == 1 else {}
    ordinary_coverage = _number(ordinary.get('coverage_pct'))
    ordinary_ready = (ordinary.get('cohort_source_complete') is True and ordinary.get('identity_complete') is True
                      and ordinary_coverage is not None and 80 <= ordinary_coverage <= 100
                      and (_count(ordinary.get('observed_count')) or 0) >= 10)
    short_evidence = [{'label': '昨日涨停队列可比中位涨幅', 'value': _metric(short, 'previous_limit_up_median_return_pct'), 'unit': '%'},
                      {'label': '昨日涨停队列正反馈占比', 'value': _metric(short, 'positive_ratio_pct'), 'unit': '%'},
                      {'label': '价格样本可比率', 'value': coverage, 'unit': '%'},
                      {'label': '普通10%制度组可比率', 'value': ordinary_coverage, 'unit': '%'},
                      {'label': '普通10%制度组证据', 'value': '已齐备' if ordinary_ready else '待核验',
                       'reason': '不使用全体队列价格反馈替代普通组晋级证据'}]
    axes = [
        {'id': 'index', 'label': '指数环境', 'state': index_state, 'state_label': index_labels[index_state],
         'evidence': index_evidence, 'scope': '四指数严格21个市场交易日价格窗口，分别观察20日收益与MA20位置'},
        {'id': 'breadth', 'label': '个股广度', 'state': broad_state,
         'state_label': {'strong': '多数样本上涨', 'weak': '多数样本回落', 'mixed': '涨跌分化', 'unknown': '广度待核验'}[broad_state],
         'evidence': [{'label': '沪深广度温度', 'value': value, 'unit': '/100'},
                      {'label': '样本可比率', 'value': temperature['coverage_pct'], 'unit': '%'}],
         'scope': temperature['scope']},
        {'id': 'short_term', 'label': '短线反馈', 'state': short_state,
         'state_label': {'strong': '昨日涨停价格反馈偏强', 'weak': '昨日涨停价格反馈偏弱',
                         'mixed': '昨日涨停价格反馈分化', 'unknown': '昨日涨停反馈待核验'}[short_state],
         'evidence': short_evidence, 'scope': '昨日确认收盘涨停队列的今日价格反馈；不是买入可成交收益或已确认的制度组接力强弱',
         'ordinary_group_ready': ordinary_ready},
    ]
    conflicts = []
    if broad_state == 'strong' and index_state == 'weak':
        conflicts.append('当日多数样本上涨，但四指数20日收益和MA20位置仍偏弱。')
    if broad_state == 'weak' and index_state == 'strong':
        conflicts.append('当日多数样本回落，但四指数中期环境仍偏强。')
    if {broad_state, short_state} == {'strong', 'weak'}:
        conflicts.append('当日广度与昨日涨停队列价格反馈方向相反。')
    if 'strong' in index_states and 'weak' in index_states:
        conflicts.append('四指数中同时存在偏强和偏弱的严格窗口。')
    return axes, conflicts


def _symbol(row, by_code):
    formal = row.get('instrument_id') or row.get('thscode')
    if isinstance(formal, str) and STOCK.fullmatch(formal):
        for key in ('instrument_id', 'thscode'):
            if key in row and row[key] != formal:
                return None
        if any(key in row and row[key] != formal[:6] for key in ('ticker', 'code')):
            return None
        return formal
    code = row.get('ticker') or row.get('code')
    return by_code.get(code) if isinstance(code, str) else None


def _retained_proof(reference, snapshot_id):
    """Bind already-read immutable inputs without loading supplement twice."""
    class RetainedInputs:
        # No status method means the constructor cannot start another local
        # supplement read. These inputs were already read by the one panel.
        def reference(self):
            return reference
    proof = LimitConditionEvidence(RetainedInputs())
    proof.snapshot_id = snapshot_id
    supplement = reference.get('_supplement', {})
    proof.supplement = supplement if supplement.get('snapshot_id') in (None, snapshot_id) else {}
    return proof


def _verified_events(market, reference, rows, day, *, proof=None):
    """Bounded, same-day source candidates; no history scan or remote request."""
    if proof is None:
        status_method = getattr(market, 'status', None)
        snapshot_id = status_method().get('snapshot_id') if callable(status_method) else None
        proof = _retained_proof(reference, snapshot_id)
    current = {}
    for row in rows:
        if row.get('date', row.get('trade_date')) != day:
            continue
        symbol = row.get('instrument_id')
        if not isinstance(symbol, str) or not STOCK.fullmatch(symbol):
            continue
        current[symbol] = None if symbol in current else row
    by_code = {}
    for symbol in current:
        by_code[symbol[:6]] = symbol if symbol[:6] not in by_code else None
    record_sets = {kind: proof._records(day, kind) for kind in ('up', 'down')}
    candidates, witnesses, problems = {'up': set(), 'down': set()}, {}, {}
    unresolved = {'up': set(), 'down': set()}
    for kind, records in record_sets.items():
        witness = False
        issues = []
        if not records:
            issues.append('未留存该日来源池')
        for record, _, _, _, granular, allow_absence in records:
            items = record.get('items')
            if record.get('date') != day or not isinstance(items, list) or not items:
                issues.append('来源日期不符或空池无法证明真实零事件')
                continue
            if record.get('pool_kind') not in (None, 'limitup' if kind == 'up' else 'limitdown'):
                issues.append('来源事件方向不符')
                continue
            if record.get('quarantine') or record.get('rejected_codes'):
                issues.append('来源存在隔离或拒绝事件')
            if len(items) > MAX_SOURCE_CANDIDATES:
                issues.append('来源候选超过有界核验上限')
            accepted = []
            for item in items[:MAX_SOURCE_CANDIDATES]:
                symbol = _symbol(item, by_code) if isinstance(item, dict) else None
                if symbol is None:
                    issues.append('存在无法消歧的证券标识')
                    unresolved[kind].add(fingerprint(item))
                    continue
                candidates[kind].add(symbol)
                accepted.append(symbol[:6])
            raw = record.get('raw_codes')
            for code in [*(raw if isinstance(raw, list) else []), *record.get('rejected_codes', [])]:
                if isinstance(code, str) and re.fullmatch(r'\d{6}', code) and code not in accepted:
                    symbol = by_code.get(code)
                    if symbol:
                        candidates[kind].add(symbol)
                    else:
                        unresolved[kind].add(fingerprint({'unresolved_code': code}))
            codes_complete = (not granular or (record.get('pagination_complete') is True
                              and record.get('ambiguous_code_rows') == 0 and isinstance(raw, list)
                              and all(isinstance(code, str) and re.fullmatch(r'\d{6}', code) for code in raw)
                              and len(raw) == len(set(raw)) and set(raw) == set(accepted)
                              and len(accepted) == len(set(accepted))))
            if allow_absence and record.get('complete') is True and codes_complete and not record.get('quarantine') and not record.get('rejected_codes'):
                witness = True
            if granular and not codes_complete:
                issues.append('完整分页代码集合未通过核验')
        witnesses[kind] = witness
        problems[kind] = list(dict.fromkeys(issues))
    counts = {'up': 0, 'down': 0}
    unknowns = {'up': 0, 'down': 0}
    for symbol in sorted(candidates['up'] | candidates['down']):
        quote = current.get(symbol)
        valid = (quote and quote.get('date', quote.get('trade_date')) == day
                 and _positive(quote.get('close')) is not None and _positive(quote.get('volume')) is not None
                 and _pattern_identity_matches(quote, symbol) and _pattern_dates_match(quote, day)
                 and quote.get('price_basis', 'none') in ('none', 'raw', 'unadjusted'))
        states = {}
        for kind in ('up', 'down'):
            proofs = []
            if valid:
                for record, by_source_code, source, path, granular, allow_absence in record_sets[kind]:
                    raw_items = record.get('items')
                    selected = by_source_code.get(symbol[:6], [])
                    narrowed = {**record, 'items': selected or [{'instrument_id': 'unrelated-source-row'}]} if isinstance(raw_items, list) and raw_items else record
                    proofs.append(_evidence(narrowed, symbol, day, quote['close'], source=source,
                                            path=path, kind=kind, granular=granular, allow_absence=allow_absence))
            states[kind] = _combine(proofs)
        conflict = any(value['status'] == 'conflict' for value in states.values()) or all(value['state'] is True for value in states.values())
        for kind in ('up', 'down'):
            if symbol not in candidates[kind]:
                continue
            value = states[kind]
            positives = [item for item in value['evidence'] if item.get('state') is True]
            exact = positives and all(item.get('pattern_validation', {}).get('source_identity_valid') is True
                                      and item.get('pattern_validation', {}).get('source_dates_valid') is True
                                      and item.get('pattern_validation', {}).get('instrument_id') == symbol
                                      and item.get('pattern_validation', {}).get('trade_date') == day
                                      and item.get('observed_prices')
                                      and all(_positive(price) is not None and abs(price - quote['close']) < 1e-7
                                              for price in item['observed_prices']) for item in positives)
            if not conflict and value['state'] is True and exact:
                if symbol.endswith(('.SH', '.SZ')):
                    counts[kind] += 1
            else:
                unknowns[kind] += 1
    result = {}
    for kind in ('up', 'down'):
        complete = witnesses[kind] and not problems[kind] and unknowns[kind] == 0
        result[kind] = {'confirmed_count': counts[kind], 'candidate_count': len(candidates[kind]) + len(unresolved[kind]),
                        'unknown_count': unknowns[kind] + len(unresolved[kind]),
                        'unresolved_identity_count': len(unresolved[kind]), 'complete': bool(complete),
                        'reason': None if complete else '；'.join(problems[kind] or ['来源全集或逐股同日原价/正成交量未全部核验'])}
    return {'date': day, 'scope': '沪深逐股同日原始收盘与正成交量核验的来源事件候选；完整仅指留存来源范围',
            'up': result['up'], 'down': result['down'],
            'complete': result['up']['complete'] and result['down']['complete']}


def _stage(temperature, breadth, axes, conflicts, event_counts):
    value, coverage = temperature['value'], temperature['coverage_pct']
    previous, delta = temperature['previous_value'], temperature['delta']
    limitations = ['阶段为本项目公开阈值的单日观察，不是已验证的完整情绪周期，也不代表底部或未来走势。']
    if not temperature['universe_expected_known']:
        limitations.append('应有证券范围尚未全部核实；有据主表上界只提供保守样本可比率，不代表历史全市场覆盖。' if temperature['universe_bound_known'] else
                           '应有证券范围和有据主表上界均未核实；仅保留观察样本温度，不以100%观察分母判阶段或解除开仓门槛。')
    if previous is None:
        limitations.append('缺少严格相邻同口径且可比率达标的前日读数，不能确认修复或退潮转变。')
    if not event_counts['complete']:
        limitations.append('来源事件全集或逐股原价证据未齐，不能确认高潮；空池不当作零事件。')
    short_axis = next(axis for axis in axes if axis['id'] == 'short_term')
    if not short_axis['ordinary_group_ready']:
        limitations.append('普通10%制度组证据不足；全体昨日涨停价格反馈不替代该组接力晋级结论。')
    if not temperature['calendar_valid'] or not temperature['evidence_consistent'] or value is None or coverage is None or coverage < 80:
        return {'code': 'unknown', 'label': '待核验', 'status': 'unknown',
                'summary': '当前证据不足，先核对数据再制定执行条件。',
                'reasons': ['严格交易日历、跨输入一致性、当日有效广度或至少80%的样本可比率尚未同时具备。'],
                'conflicts': conflicts, 'limitations': limitations}
    up = _count(breadth.get('up'))
    code, label, reason = 'divergence', '分化', '样本可比率达标，但未命中冰点、高潮、修复、退潮或普涨阈值。'
    if value <= 15 and up is not None and up < 500:
        code, label, reason = 'ice', '冰点', '温度≤15且上涨家数<500；只说明当日广度极低。'
    elif (value >= 85 and up is not None and up >= 3000 and event_counts['complete']
          and event_counts['up']['confirmed_count'] >= 80 and event_counts['down']['confirmed_count'] <= 5):
        code, label, reason = 'climax', '高潮', '高广度与逐股核验的完整来源涨跌停候选同时达到公开阈值。'
    elif previous is not None and previous <= 35 and value > 35 and delta >= 15:
        code, label, reason = 'repair', '修复', '从严格前一交易日低广度回升至少15个百分点，并越过35。'
    elif previous is not None and previous >= 60 and value < 60 and delta <= -15:
        code, label, reason = 'ebb', '退潮', '从严格前一交易日高广度回落至少15个百分点，并跌破60。'
    elif value >= 60 and up is not None and up >= 3000:
        code, label, reason = 'broad_rise', '普涨', '温度≥60且上涨家数≥3000；不单凭普涨认定高潮。'
    elif conflicts:
        code, label, reason = 'mixed', '混沌', '已知方向存在冲突，暂未形成同向共振；并非用缺数据代替混沌。'
    reasons = [reason]
    reasons.extend(axis['state_label'] for axis in axes)
    summary = {'ice': '低广度先等止跌与修复，不能把冰点直接当底部。',
               'repair': '广度明显修复，下一交易日仍需确认能否延续。',
               'ebb': '广度由强转弱，下一交易日以核对风险和失效条件为先。',
               'climax': '广度和来源事件同时高热，先防次日分歧和不可成交。',
               'broad_rise': '多数样本上涨，再核对指数与短线反馈是否同步。',
               'mixed': '已知证据互有冲突，先观察方向选择。',
               'divergence': '样本涨跌分化，等待更明确的同向证据。'}[code]
    if code == 'repair' and axes[0]['state'] == 'weak':
        summary = '广度大幅修复，但四指数中期环境仍弱；先等次日转强确认。'
    return {'code': code, 'label': label, 'status': 'observation', 'summary': summary,
            'reasons': reasons, 'conflicts': conflicts, 'limitations': limitations}


def _turnover(market, day, calendar, rows, history=None):
    selected = [row for row in rows if row.get('date', row.get('trade_date')) == day
                and isinstance(row.get('instrument_id'), str) and STOCK.fullmatch(row['instrument_id'])]
    duplicate = len({row['instrument_id'] for row in selected}) != len(selected)
    amount = (sum(row['amount'] for row in selected) if selected and not duplicate
              and all(_number(row.get('amount')) is not None and row['amount'] >= 0 for row in selected) else None)
    valid_calendar = valid_market_calendar(calendar, day)
    if history is None:
        method = getattr(market, 'turnover_history', None)
        history = method(day) if valid_calendar and callable(method) else []
    history = [row for row in history if isinstance(row, dict) and _day(row.get('date')) is not None and row['date'] <= day]
    metadata = (amount_change_metadata(amount, day, calendar, history) if valid_calendar else
                {'amount_previous_date': None, 'amount_change_pct': None, 'amount_change_reason': '市场日历缺失、重复或无观察日，不能比较成交额'})
    previous_day = metadata['amount_previous_date']
    previous = [row for row in history if row.get('date') == previous_day]
    previous_amount = _number(previous[0].get('amount')) if len(previous) == 1 else None
    if previous_amount is not None and previous_amount < 0:
        previous_amount = None
    change = _number(metadata.get('amount_change_pct'))
    complete = change is not None and amount is not None
    expanded = (Decimal(str(amount)) * 100 >= Decimal(str(previous_amount)) * (100 + EXPANSION_THRESHOLD_PCT)
                if complete and previous_amount is not None and previous_amount > 0 else None)
    reason = metadata.get('amount_change_reason')
    if not complete and reason is None:
        reason = '前日成交额为零或相邻同口径成交额未齐，无法核验放量比例'
    position = calendar.index(day) if valid_calendar else -1
    prior_dates = calendar[max(0, position - 5):position] if valid_calendar else []
    indexed = {}
    for row in history:
        if row['date'] in prior_dates:
            indexed[row['date']] = None if row['date'] in indexed else row
    current_source = next((row for row in history if row.get('date') == day), {})
    mean_valid = len(prior_dates) == 5 and all(
        indexed.get(target) and _number(indexed[target].get('amount')) is not None and indexed[target]['amount'] >= 0
        and indexed[target].get('scope') == current_source.get('scope')
        and indexed[target].get('source') == current_source.get('source') for target in prior_dates)
    mean5 = sum(indexed[target]['amount'] for target in prior_dates) / 5 if mean_valid else None
    return {'amount': amount, 'previous_date': previous_day, 'previous_amount': previous_amount,
            'change_pct': change, 'expansion_threshold_pct': EXPANSION_THRESHOLD_PCT,
            'expanded': expanded, 'state': 'expanded' if expanded is True else 'not_expanded' if expanded is False else 'unknown',
            'comparison_complete': complete,
            'scope': '沪深京已留存A股股票成交额，与盘面总览同口径；不将各指数成交额相加',
            'unit': 'CNY', 'reason': reason,
            'previous_5d_mean': mean5, 'previous_5d_mean_complete': mean_valid,
            'vs_previous_5d_mean_pct': (amount / mean5 - 1) * 100 if amount is not None and mean5 is not None and mean5 > 0 else None}


def _ice_history(day, calendar, history, universe_upper_bound):
    valid = valid_market_calendar(calendar, day)
    position = calendar.index(day) if valid else -1
    requested = calendar[max(0, position - ICE_LOOKBACK_SESSIONS):position] if valid else []
    current = [row for row in history if isinstance(row, dict) and row.get('date') == day]
    scope = _breadth_scope(current[0].get('scope')) if len(current) == 1 else None
    source = current[0].get('source') if len(current) == 1 else None
    observations = []
    for target in requested:
        matches = [row for row in history if isinstance(row, dict) and row.get('date') == target]
        row = matches[0] if len(matches) == 1 else {}
        bounded = _bounded_reading(row, universe_upper_bound)
        value, coverage = bounded['value'], bounded['coverage_pct']
        known = (len(matches) == 1 and scope is not None and _breadth_scope(row.get('scope')) == scope
                 and row.get('source') == source and coverage is not None and coverage >= 80)
        ice = value <= 15 and (_count(row.get('up')) or 0) < 500 if known and value is not None else None
        observations.append({'date': target, 'temperature': value if known else None,
                             'coverage_pct': coverage if known else None, 'is_ice': ice,
                             'state': 'known' if ice is not None else 'unknown',
                             'reason': None if ice is not None else '该严格交易日同口径广度缺失、重复或可比率不足，不能补成冰点或非冰点'})
    streak = None if not observations or observations[-1]['is_ice'] is None else 0
    streak_complete = streak == 0
    if streak is not None:
        for row in reversed(observations):
            if row['is_ice'] is True:
                streak += 1
                streak_complete = False
            elif row['is_ice'] is False:
                streak_complete = True
                break
            else:
                break
    known = [row for row in observations if row['is_ice'] is not None]
    return {'lookback_sessions': ICE_LOOKBACK_SESSIONS, 'observations': observations,
            'history_complete': len(observations) == ICE_LOOKBACK_SESSIONS and len(known) == ICE_LOOKBACK_SESSIONS,
            'prior_ice_streak': streak, 'prior_ice_streak_complete': streak_complete,
            'prior_ice_streak_at_least': streak is not None and streak > 0 and not streak_complete,
            'recent_ice_count': sum(row['is_ice'] is True for row in known) if known else None,
            'recent_ice_count_complete': len(observations) == ICE_LOOKBACK_SESSIONS and len(known) == ICE_LOOKBACK_SESSIONS,
            'scope': '本页同口径沪深广度的严格前5个市场交易日；缺日不压缩，已确认冰点数在缺口时仅为观察下界'}


def _index_references(reference, day):
    result = []
    for symbol, name in INDEX_NAMES.items():
        matches = [row for row in reference.get('indices', {}).get(symbol, [])
                   if isinstance(row, dict) and row.get('date') == day]
        row = matches[0] if len(matches) == 1 else {}
        low, close = _positive(row.get('low')), _positive(row.get('close'))
        if (low is None or close is None or low > close or row.get('price_basis', 'none') not in ('none', 'raw', 'unadjusted')
                or not _pattern_identity_matches(row, symbol) or not _pattern_dates_match(row, day)):
            continue
        result.append({'instrument_id': symbol, 'name': name, 'date': day, 'low': low, 'close': close,
                       'source': row.get('source'), 'price_basis': 'none', 'role': '候选日实际低点风险参照，不是已确认支撑位'})
    return result


def _bottom_watch(day, calendar, history, reference, stage, turnover, universe_upper_bound):
    ice = _ice_history(day, calendar, history, universe_upper_bound)
    references = _index_references(reference, day)
    risk_complete = len(references) == len(INDEX_NAMES)
    prior_ice = ice['prior_ice_streak']
    candidate = stage['code'] in ('repair', 'climax') and turnover['expanded'] is True and prior_ice is not None and prior_ice >= 1 and risk_complete
    missing = stage['status'] == 'unknown' or turnover['state'] == 'unknown' or prior_ice is None or not risk_complete
    state = 'candidate' if candidate else 'unknown' if missing else 'unconfirmed'
    return {'status': state, 'label': {'candidate': '短底候选，待后续确认', 'unconfirmed': '短底尚未确认', 'unknown': '短底证据待核验'}[state],
            'candidate_date': day if candidate else None, 'index_references': references,
            'index_references_complete': risk_complete, 'confirmed': False,
            'reason': ('前一交易日已有真实冰点，今日广度修复且成交额达到显著放量门槛；只形成候选，不证明底部已经成立。' if candidate else
                       '尚未同时具备前日真实冰点、当日广度修复、显著放量及候选日指数低点参照。'),
            'confirmation_conditions': ['后续交易日核对主要指数未有效跌破候选日实际低点，或回踩后重新收回。',
                                        '广度与候选方向保持承接，不能只有少数权重反弹；后续真实数据才可提高确认程度。'],
            'invalidations': ['后续主要指数跌破候选日低点且不能收回，或市场广度修复失效。',
                              '观察候选所属方向集体转弱，或反弹缺少真实成交承接。'],
            'trigger_state': 'unobserved', **ice}


def _entry_gate(stage, axes, turnover, bottom):
    from app.services.market_outlook_insights import entry_routes
    routes = entry_routes(stage, axes, turnover, bottom)
    aligned = routes[0]['status'] == 'conditional'
    blocked = (turnover['state'] == 'unknown' or stage['code'] in ('unknown', 'ice', 'ebb')
               or (stage['code'] == 'repair' and bottom['status'] != 'candidate' and not aligned))
    conditional = bottom['status'] == 'candidate' or aligned
    state = 'blocked' if blocked else 'conditional' if conditional else 'wait'
    reason = ('相邻市场交易日成交额缺失、重复或不可比，量能条件尚未核验，禁止开仓。' if turnover['state'] == 'unknown' else
              '冰点、退潮或必要证据未齐，开仓门槛未解除。' if stage['code'] in ('unknown', 'ice', 'ebb') else
              '当前修复尚未同时满足真实前日冰点、显著放量和风险参照门槛，继续禁止开仓。' if blocked else
              '已观察冰点后的量价修复，只解除到下一交易日转强确认后试错；指数中期仍弱时加仓继续等待。' if bottom['status'] == 'candidate' else
              '观察日指数与广度同向，趋势条件可独立复核；普通10%资料缺口仅限制对应接力路径。' if aligned else
              '当前未形成明确同向条件，等待下一交易日确认。')
    releases = ['先核验必要广度及严格相邻同口径成交额比较，冰点、退潮和未知证据限制优先。',
                '趋势路径：四指数偏强、广度多数上涨；不要求普通10%接力证据或20%放量。',
                f'冰点修复路径：严格前日冰点、当日广度修复，成交额增加至少{EXPANSION_THRESHOLD_PCT}%并有四指数低点参照。',
                '接力路径：趋势条件之外，再核验昨板价格反馈与普通10%制度组资料。']
    releases.append('下一交易日再人工确认广度、指数与候选方向承接及真实可成交；不把收盘条件提前视作盘中已触发。')
    return {'status': state, 'label': '禁止开仓' if state == 'blocked' else '转强确认后试错' if bottom['status'] == 'candidate' else '满足条件后复核' if state == 'conditional' else '等待确认',
            'reasons': [reason, f"显著放量门槛：较严格前一交易日同口径成交额增加至少{EXPANSION_THRESHOLD_PCT}%。"],
            'release_conditions': releases,
            'blocked_conditions': ['广度仍处冰点、退潮，或相邻同口径成交额等必要证据未知。',
                                   '修复路径尚未满足量价候选且趋势路径也不成立；各路径缺口分别核对。',
                                   '下一交易日广度修复失败，或主要指数跌破候选日低点且不能收回。'],
            'trigger_state': 'unobserved', 'routes': routes}


def _plan(stage, axes, entry_gate, bottom):
    by_id = {axis['id']: axis for axis in axes}
    constructive = any(route['id'] == 'trend' and route['status'] == 'conditional'
                       for route in entry_gate.get('routes', []))
    defensive = stage['code'] == 'ebb'
    stance = 'defensive' if defensive else 'conditional' if constructive else 'evidence_gap' if stage['code'] == 'unknown' else 'wait_confirmation'
    labels = {'defensive': '防守优先', 'conditional': '满足条件再复核',
              'evidence_gap': '先补足证据', 'wait_confirmation': '先等次日确认'}
    open_status = entry_gate['status']
    add_status = ('avoid' if defensive or stage['code'] == 'ice' else
                  'conditional' if constructive and entry_gate['status'] != 'blocked' else 'wait')
    reasons = {
        'open': ('开仓门槛未解除，继续禁止开仓；' + entry_gate['reasons'][0] if open_status == 'blocked' else
                 '冰点后已观察显著放量修复；下一交易日转强与可成交条件确认后，才重新评估试错。' if bottom['status'] == 'candidate' else
                 '广度由强转弱，先观察风险释放，不用反抽替代转强确认。' if defensive else
                 '指数与广度同向，趋势路径可独立复核；明日触发条件仍需实际核验。' if constructive else
                 '当前仅有观察日收盘证据，先确认指数与所属方向在下一交易日同步转强。'),
        'add': ('冰点或退潮阶段不因浮亏摊薄而加仓。' if add_status == 'avoid' else
                '仅对已有持仓单独复核：原定失效条件未触发，且市场与所属方向同步转强。'),
        'bottom': ('短底目前只是候选，等待后续不破候选日低点或回踩收回；不能凭单日放量就认定底部。' if bottom['status'] == 'candidate' else
                   '冰点、大跌或单日反弹都不能证明底部；先观察止跌及后续承接，不直接抄底。'),
    }
    actions = []
    for action, label, state in (('open', '开仓', open_status), ('add', '加仓', add_status), ('bottom', '抄底', 'wait' if bottom['status'] == 'candidate' else 'avoid')):
        triggers = (['有可核验的盘中报价后，人工确认同口径上涨占比达到60%以上，主要指数和候选所属方向同步转强。',
                     '候选出现实际可成交报价并守住观察日收盘参照；日末一字封板报价不代表次日可以买到。'] if action == 'open' else
                    ['仅复核已有持仓：原有失效条件未触发，且指数、广度与所属方向同步转强。',
                     '先确认真实可成交和持仓承受条件，再决定是否执行；系统不据此自动加仓。'] if action == 'add' else
                    ['先出现可核验的止跌与后续承接，重新建立明确的失效条件；不以温度低作为买入触发。'])
        invalidations = ['指数与多数样本再次转弱，或所属方向的观察候选集体转弱。',
                         '候选跌回观察日收盘参照下方未能收复，或缺少可成交报价。']
        if action == 'add':
            invalidations = ['已有持仓原定失效条件已触发。', '市场或所属方向转弱；不得以摊低成本替代原定风险条件。']
        observation = ('冰点后放量修复候选已出现。' if bottom['status'] == 'candidate' and action != 'add' else
                       '趋势与广度同向，已有持仓可独立复核。' if constructive and action == 'add' else
                       '短期修复已出现，中期趋势及持仓方向仍须单独检查。' if bottom['status'] == 'candidate' else stage['summary'])
        actions.append({'id': action, 'label': label, 'status': state, 'observation': observation,
                        'status_label': ('转强确认后试错' if action == 'open' and bottom['status'] == 'candidate' else
                                         {'blocked': '禁止开仓', 'avoid': '暂不考虑', 'wait': '等待确认', 'conditional': '满足条件后复核'}[state]),
                        'reason': reasons[action], 'triggers': triggers, 'invalidations': invalidations,
                        'trigger_state': 'unobserved', 'execution_state': 'unobserved'})
    scenarios = [
        {'id': 'strength', 'label': '转强延续', 'triggers': ['主要指数、同口径广度和候选所属方向同步转强，并有真实可成交报价。'],
         'actions': ['仅重新核对开仓清单；已有持仓另核对原定失效条件，满足后才考虑加仓。'],
         'invalidations': ['指数与广度背离，或方向候选失去承接。'], 'trigger_state': 'unobserved'},
        {'id': 'divergence', 'label': '冲高分歧', 'triggers': ['指数冲高但多数样本与所属方向没有同步，或观察候选明显分化。'],
         'actions': ['暂停新增与加仓，保留已有持仓的原定失效条件。'],
         'invalidations': ['随后出现可核验的指数、广度与方向同步转强，才重评转强预案。'], 'trigger_state': 'unobserved'},
        {'id': 'weakness', 'label': '继续转弱', 'triggers': ['主要指数、同口径广度或候选方向继续走弱，原有失效条件触发。'],
         'actions': ['按原定风险条件处理已有持仓；不以低温或浮亏为由抄底、摊薄。'],
         'invalidations': ['风险条件解除并出现新的独立承接证据后，重新制定计划。'], 'trigger_state': 'unobserved'},
    ]
    if entry_gate['status'] == 'blocked':
        stance = 'blocked'
        labels['blocked'] = '禁止开仓，先等量价修复'
    elif bottom['status'] == 'candidate':
        stance = 'conditional'
        labels['conditional'] = '放量修复，转强确认后试错'
    return {'stance': stance, 'label': labels[stance], 'summary': stage['summary'], 'actions': actions,
            'scenarios': scenarios, 'trigger_state': 'unobserved',
            'verification_mode': 'manual_next_session',
            'notice': '盘中条件需要下一交易日真实数据人工核对；系统当前未观察到这些条件，收盘统计不能提前当作盘中触发。'}


def _stock(row, day):
    symbol = row.get('instrument_id')
    evidence = row.get('limit_up_evidence', {})
    positives = [item for item in evidence.get('evidence', []) if isinstance(item, dict) and item.get('state') is True]
    if (not isinstance(symbol, str) or not STOCK.fullmatch(symbol) or row.get('date') != day
            or not _pattern_identity_matches(row, symbol) or not _pattern_dates_match(row, day)
            or row.get('limit_up_state') is not True or evidence.get('state') is not True or not positives
            or _positive(row.get('close')) is None or _positive(row.get('volume')) is None
            or row.get('price_basis', 'none') not in ('none', 'raw', 'unadjusted')):
        return None
    if any(item.get('pattern_validation', {}).get('source_identity_valid') is not True
           or item.get('pattern_validation', {}).get('source_dates_valid') is not True
           or item.get('pattern_validation', {}).get('instrument_id') != symbol
           or item.get('pattern_validation', {}).get('trade_date') != day
           or not item.get('observed_prices')
           or any(_positive(value) is None or abs(value - row['close']) >= 1e-7 for value in item['observed_prices'])
           for item in positives):
        return None
    height = _count(row.get('board_count'))
    height = height if height and height >= 1 else None
    source = list(dict.fromkeys(item.get('source') for item in positives if item.get('source')))
    return {'instrument_id': symbol, 'name': row.get('name') or symbol, 'code': symbol[:6], 'role': '观察候选',
            **({'board_presentation': row['board_presentation']} if isinstance(row.get('board_presentation'), dict) else {}),
            'close': row['close'], 'change_pct': _number(row.get('change_pct')), 'board_count': height,
            'evidence_date': day, 'reason': '观察日原始收盘和正成交量已核验来源涨停；来源连板数仅作排序，不自动认定龙头。',
            'trigger': f"先观察{row['close']:g}（{day}原始收盘参照）的承接，再核对同方向样本同步转强及真实可成交。",
            'invalidations': ['跌回观察日收盘参照下方未能收复，或同方向样本集体转弱。',
                              '一字封板等缺少可成交报价的情形不视为已执行。'],
            'source': ' + '.join(source), 'price_basis': 'none',
            'event_basis': 'same-day-source-close-and-positive-volume',
            'source_fingerprint': fingerprint(positives)}


def _watchlist(ladder, day):
    rows = [row for row in ladder.get('items', []) if isinstance(row, dict) and row.get('date') == day]
    counts = Counter(row.get('instrument_id') for row in rows)
    stocks = {}
    original = {}
    for row in rows:
        if counts[row.get('instrument_id')] != 1:
            continue
        item = _stock(row, day)
        if item is not None:
            stocks[item['instrument_id']] = item
            original[item['instrument_id']] = row
    def stock_key(item):
        row = original[item['instrument_id']]
        amount = _number(row.get('amount'))
        return (-(item['board_count'] or 0), -(amount if amount is not None and amount >= 0 else -1), item['instrument_id'])
    ordered = sorted(stocks.values(), key=stock_key)
    membership = ladder.get('theme_coverage', {})
    topics = []
    excluded = []
    if membership.get('selected_date_available') is True:
        for topic in ladder.get('theme_counts', []):
            if not isinstance(topic, dict) or not isinstance(topic.get('topic_id'), str) or not topic.get('name'):
                continue
            observed = topic.get('membership_observed_date')
            if _day(observed) is None or observed > day:
                continue
            members = [stock for stock in ordered if any(isinstance(tag, dict)
                       and tag.get('topic_id') == topic['topic_id'] and tag.get('kind', 'concept') == topic.get('kind', 'concept')
                       and _day(tag.get('observed_date')) is not None and tag['observed_date'] <= day
                       for tag in original[stock['instrument_id']].get('topics', []))]
            hits = _count(topic.get('limit_up_observed_count'))
            if not members or hits is None or hits < 1:
                continue
            if str(topic['name']).strip().casefold() in NORMALIZED_NON_DIRECTION_LABELS:
                excluded.append({'id': topic['topic_id'], 'name': topic['name'],
                                 'reason': '交易资格或指数成员属性，不作为本页产业/政策方向候选。'})
                continue
            topics.append({'id': topic['topic_id'], 'name': topic['name'], 'kind': topic.get('kind', 'concept'),
                           'role': '观察候选', 'reason': f"观察日来源涨停样本{'已确认' if topic.get('complete') is True else '至少'}{hits}只；按已确认数下界、可核验证券数排序，归属重叠分别计数。",
                           'evidence_date': day, 'membership_observed_date': observed,
                           'confirmed_limit_up_count': topic.get('limit_up_count') if topic.get('complete') is True else None,
                           'observed_limit_up_count': hits, 'complete': topic.get('complete') is True,
                           'stocks': members[:TOPIC_STOCKS_LIMIT]})
    topics.sort(key=lambda topic: (-topic['observed_limit_up_count'], -len(topic['stocks']), str(topic['name']), topic['id']))
    main = ladder.get('main_coverage', {})
    coverage = {'source_pool_complete': main.get('source_pool_complete') is True,
                'event_candidates_complete': main.get('complete') is True,
                'membership_available': membership.get('selected_date_available') is True,
                'membership_snapshot_day': membership.get('source_snapshot_day'),
                'stock_candidate_count': len(ordered), 'topic_candidate_count': len(topics),
                'excluded_topic_count': len(excluded), 'excluded_topics': excluded,
                'topic_filter_version': TOPIC_FILTER_VERSION,
                'excluded_topic_names': sorted(NON_DIRECTION_LABELS),
                'stock_limit': STOCKS_LIMIT, 'topic_limit': TOPICS_LIMIT, 'per_topic_stock_limit': TOPIC_STOCKS_LIMIT,
                'selection_basis': '仅观察日同日原始报价与来源涨停正例；个股按来源连板数、当日成交额、代码排序；题材按已确认涨停下界排序。',
                'scope': '已留存来源和指定日成员中的观察候选；不是全市场龙头评级或买入名单',
                'membership_reason': membership.get('notice') or '缺少指定日成员时只保留个股候选，不使用当前题材回填历史。'}
    empty = None if ordered else '没有同时通过观察日原始收盘、正成交量和来源事件核验的候选；不补静态核心股。'
    return {'topics': topics[:TOPICS_LIMIT], 'stocks': ordered[:STOCKS_LIMIT],
            'coverage': coverage, 'empty_reason': empty,
            'topics_empty_reason': None if topics else '该日没有可核验的题材归属或同日题材涨停样本；未用当前成员回填历史。'}


def build_market_outlook(market, status, day, reference, rows, breadth, emotion, sentiment, history,
                         *, previous_breadth=None, ladder=None, turnover_history=None, universe_upper_bound=None):
    """One existing panel, one bounded ladder read; never a task or data writer."""
    calendar_method = getattr(market, 'calendar', None)
    calendar = calendar_method() if callable(calendar_method) else []
    temperature = _temperature(day, calendar, breadth, previous_breadth, history, universe_upper_bound)
    axes, conflicts = _axes(sentiment, temperature, day)
    proof = _retained_proof(reference, status.get('snapshot_id'))
    event_counts = _verified_events(market, reference, rows, day, proof=proof)
    stage = _stage(temperature, breadth, axes, conflicts, event_counts)
    if turnover_history is None:
        turnover_method = getattr(market, 'turnover_history', None)
        turnover_history = turnover_method(day) if temperature['calendar_valid'] and callable(turnover_method) else []
    turnover = _turnover(market, day, calendar, rows, turnover_history)
    bottom = _bottom_watch(day, calendar, history, reference, stage, turnover, universe_upper_bound)
    gate = _entry_gate(stage, axes, turnover, bottom)
    plan = _plan(stage, axes, gate, bottom)
    from app.services.market_outlook_insights import build_insights
    insights = build_insights(day=day, snapshot_id=status.get('snapshot_id'), calendar=calendar,
                              breadth=breadth, previous=previous_breadth, history=history, temperature=temperature,
                              stage=stage, axes=axes, turnover=turnover, turnover_history=turnover_history,
                              reference=reference, bottom=bottom, routes=gate['routes'], universe_upper_bound=universe_upper_bound)
    if ladder is None:
        if temperature['calendar_valid'] and callable(getattr(market, 'history_for_instruments', None)):
            from app.services.market_ladder import build_ladder
            from app.services.market_service import dated_events
            ladder = build_ladder(market, status, day, reference, rows, dated_events(reference, day), include_patterns=True)
        else:
            ladder = {}
    watchlist = _watchlist(ladder, day)
    position = calendar.index(day) if temperature['calendar_valid'] else -1
    following = calendar[position + 1] if 0 <= position < len(calendar) - 1 else None
    next_session = {'date': following, 'label': following or '下一交易日',
                    'calendar_reason': '使用已留存市场交易日历的严格相邻交易日；不读取其价格。' if following else
                    '当前已留存市场日历没有观察日之后的交易日，保持下一交易日，不用自然日推算。'}
    proof = sentiment.get('provenance', {})
    inputs = {'version': VERSION, 'date': day, 'snapshot_id': status.get('snapshot_id'),
              'temperature': temperature, 'axes': axes, 'stage': stage, 'event_counts': event_counts,
              'turnover': turnover, 'bottom_watch': bottom, 'entry_gate': gate,
              'insights': insights,
              'watchlist': watchlist, 'next_session': next_session,
              'source_input_fingerprint': proof.get('input_fingerprint')}
    provenance = {'rule_version': VERSION, 'mode': 'retained_retrospective', 'label': '当前留存回看与条件预案',
                  'input_snapshot_ids': [status['snapshot_id']] if status.get('snapshot_id') else [],
                  'input_fingerprint': fingerprint(inputs), 'source_input_fingerprint': proof.get('input_fingerprint'),
                  'computed_at': datetime.now(timezone.utc).isoformat(),
                  'market_collected_at': proof.get('market_collected_at'), 'first_known_at': None,
                  'known_time_complete': False, 'notice': NOTICE}
    return {'version': VERSION, 'date': day, 'snapshot_id': status.get('snapshot_id'),
            'temperature': temperature, 'stage': stage, 'axes': axes, 'next_session': next_session,
            'plan': plan, 'watchlist': watchlist, 'event_counts': event_counts,
            'turnover': turnover, 'bottom_watch': bottom, 'entry_gate': gate, 'insights': insights,
            'provenance': provenance, 'notice': NOTICE, 'rules': deepcopy(RULES),
            'legacy_emotion_model': emotion.get('model')}
