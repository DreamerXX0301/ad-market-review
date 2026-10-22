"""Descriptive daily breadth and verified previous-limit-up cohort observations.

These are price sample statistics, never a measure of actual account profits or
an exchange-endorsed sentiment score. Missing evidence is not a losing trade.
"""
from collections import Counter
import math
import re
from statistics import mean, median

from app.services.strategy_limits import LimitConditionEvidence, batch_quote_indexes
from app.services.sentiment_context import (CONTEXT_VERSION, GROUP_LABELS, cohort_group,
    dated_regime, dated_st_state, fingerprint, index_environment, provenance)

MODEL = 'dated-return-cohort-v1'
REFERENCES = [
    {'title': '上交所研究：证券市场投资者情绪指数构建研究',
     'url': 'https://www.sse.com.cn/aboutus/research/report/c/10056610/files/2a3cc9eb887144759849e4287455e54b.pdf',
     'use': '区分问卷情绪与交易代理指标的研究方法；为研究人员观点，不是本项目阈值的交易所标准。'},
    {'title': 'AKShare官方：涨停股池及昨日涨停股池',
     'url': 'https://akshare.akfamily.xyz/data/stock/stock.html',
     'use': '仅用于解释来源字段与候选队列；本项目另核验交易日、原始收盘及成交量。'},
    {'title': '深交所：个人投资者状况调查报告',
     'url': 'https://www.szse.cn/aboutus/trends/news/t20170316_518921.html',
     'use': '投资者预期与行为不能由价格分布完整替代。'},
]


def finite(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def metric(key, label, value, unit, formula, reason=None):
    return {'key': key, 'label': label, 'value': value, 'unit': unit, 'formula': formula, 'reason': reason}


def comparable(row, day):
    if row.get('date', row.get('trade_date')) != day: return False
    return (finite(row.get('change_pct')) is not None and (finite(row.get('volume')) or 0) > 0
            and row.get('corporate_action_coverage_known') is True
            and row.get('price_continuity') is True)


def market_distribution(rows, day):
    candidates = [row for row in rows if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', str(row.get('instrument_id', '')))]
    duplicates = Counter(row['instrument_id'] for row in candidates)
    values = [row['change_pct'] for row in candidates if duplicates[row['instrument_id']] == 1 and comparable(row, day)]
    total = len(duplicates); count = len(values); unknown = total-count
    coverage = count/total*100 if total else None
    average = mean(values) if values else None
    middle = median(values) if values else None
    up = sum(value > 0 for value in values)/count*100 if count else None
    state = 'insufficient' if not count or coverage < 80 else 'profit' if middle > 0 and up >= 60 else 'loss' if middle < 0 and up <= 40 else 'mixed'
    names = {'insufficient': '可比样本不足', 'profit': '样本普遍上涨', 'loss': '样本普遍回落', 'mixed': '涨跌分化'}
    reason = None if values else '缺少当日有成交、前收可比且公司行动来源可核验的报价'
    return {'label': '市场赚钱效应', 'state': state, 'state_label': names[state],
            'scope': '沪深京已留存可比A股价格样本，非投资者账户盈亏',
            'sample_count': count, 'observed_count': total, 'unknown_count': unknown, 'coverage_pct': coverage,
            'metrics': [metric('equal_weight_return_pct', '样本等权涨幅', average, '%', '有效个股当日收盘涨幅之和 ÷ 有效样本数；每只股票同权', reason),
                        metric('median_return_pct', '涨幅中位数', middle, '%', '有效个股当日收盘涨幅排序后的中位数', reason),
                        metric('up_ratio_pct', '上涨占比', up, '%', '收盘涨幅>0的有效样本数 ÷ 全部有效样本数 ×100', reason),
                        metric('up_5pct_count', '涨幅≥5%', sum(value >= 5 for value in values) if values else None, '家', '有效样本中收盘涨幅≥5%的个股数', reason),
                        metric('down_5pct_count', '跌幅≥5%', sum(value <= -5 for value in values) if values else None, '家', '有效样本中收盘涨幅≤-5%的个股数', reason)],
            'rules': '自定义描述：可比率≥80%后，中位数>0且上涨占比≥60%为样本普遍上涨；中位数<0且上涨占比≤40%为样本普遍回落；其他为分化。缺失和停牌不计作0收益。'}


def _candidates(proof, day, by_code):
    found = {}; ambiguous = set()
    for record, _, _, _, _, _ in proof._records(day, 'up'):
        for row in record.get('items', []) if isinstance(record.get('items'), list) else []:
            if not isinstance(row, dict): continue
            formal = row.get('instrument_id') or row.get('thscode')
            if isinstance(formal, str) and re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', formal): symbol = formal
            else:
                code = str(row.get('ticker') or row.get('code') or '')
                symbol = by_code.get(code)
                if not symbol:
                    ambiguous.add(code or 'missing-identity'); continue
            found.setdefault(symbol, {}).update({key: value for key, value in row.items() if value is not None})
        # Rejected/raw-only identities are still cohort candidates. Omitting
        # them would turn an observed subset into a falsely complete universe.
        extras = [*record.get('raw_codes', []), *record.get('rejected_codes', [])]
        for item in record.get('quarantine', []):
            if isinstance(item, dict):
                item = item.get('record', item)
                extras.extend(item.get(key) for key in ('instrument_id','thscode','ticker','code') if item.get(key))
        for value in extras:
            text = str(value or '')
            symbol = text if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', text) else by_code.get(text[:6])
            if symbol: found.setdefault(symbol, {})
            else: ambiguous.add(text or 'missing-identity')
    return found, ambiguous


def _complete_cohort_source(proof, day):
    records = proof._records(day, 'up') if day else []
    witness = False
    for record, _, _, _, granular, allow_absence in records:
        items = record.get('items')
        if record.get('quarantine') or record.get('rejected_codes'): return False
        if not isinstance(items, list): return False
        accepted = set()
        for item in items:
            if not isinstance(item, dict): return False
            code = str(item.get('instrument_id') or item.get('thscode') or item.get('ticker') or item.get('code') or '')[:6]
            if len(code) != 6 or not code.isascii() or not code.isdigit(): return False
            accepted.add(code)
        if granular:
            raw = record.get('raw_codes')
            if (record.get('pagination_complete') is not True or record.get('ambiguous_code_rows') != 0
                    or not isinstance(raw, list) or set(raw) != accepted): return False
        if allow_absence and record.get('complete') is True and items: witness = True
    return witness


def short_term_cohort(market, reference, rows, day, *, proof=None):
    calendar_method = getattr(market, 'calendar', None)
    calendar = sorted(set(value for value in calendar_method() if value <= day)) if callable(calendar_method) else []
    previous = calendar[-2] if len(calendar) >= 2 and calendar[-1] == day else None
    proof = proof or LimitConditionEvidence(market)
    proof.reference = reference; proof.supplement = reference.get('_supplement', {})
    identity_method = getattr(market, 'instruments', None)
    identities = identity_method(limit=10000) if callable(identity_method) else []
    by_code = {}
    for row in identities:
        # A-share closing pools contain stocks, not indices. A same-code index
        # (000688.SH) must not make the stock (000688.SZ) ambiguous. Genuine
        # multiple stock identities still remain unresolved.
        if row.get('kind') != 'stock': continue
        symbol = row.get('instrument_id')
        if isinstance(symbol, str): by_code[symbol[:6]] = symbol if symbol[:6] not in by_code else None
    yesterday, ambiguous = _candidates(proof, previous, by_code) if previous else ({}, set())
    today, _ = _candidates(proof, day, by_code)
    symbols = sorted(set(yesterday) | set(today))
    quotes = batch_quote_indexes(market, symbols, day, calendar) if previous and symbols else {}
    indexed = {}
    for row in rows:
        symbol = row.get('instrument_id')
        indexed[symbol] = None if symbol in indexed else row
    master = {row['instrument_id']: row for row in identities if row.get('instrument_id')}
    confirmed = []; unverified = 0; gains = []; promoted = failed = unknown_state = 0
    members = []
    for symbol in yesterday:
        own = quotes.get(symbol, {})
        if proof._state(symbol, previous, own.get(previous))['state'] is not True:
            unverified += 1; continue
        confirmed.append(symbol)
        current = own.get(day); prior = own.get(previous)
        present = indexed.get(symbol)
        change = None
        if current and prior and present and comparable(present, day) and (finite(current.get('volume')) or 0) > 0 and (finite(current.get('close')) or 0) > 0 and (finite(prior.get('close')) or 0) > 0:
            change = (current['close']/prior['close']-1)*100
            if finite(change) is not None and abs(change-present['change_pct']) < 1e-7: gains.append(change)
            else: change = None
        state = proof._state(symbol, day, current)['state']
        if state is True: promoted += 1
        elif state is False: failed += 1
        else: unknown_state += 1
        identity = master.get(symbol, {})
        group, reason = dated_regime(symbol, previous, identity, calendar,
                                    dated_st_state(proof, symbol, previous, prior, identity))
        current_group, current_reason = dated_regime(symbol, day, identity, calendar,
                                    dated_st_state(proof, symbol, day, current, identity))
        stable = group != 'unknown' and current_group == group
        members.append({'instrument_id': symbol, 'group': group, 'identity_reason': reason,
                        'current_identity_reason': current_reason, 'current_identity_known': stable,
                        'listing_date': identity.get('list_date'),
                        'prior_close': prior.get('close') if prior else None,
                        'prior_volume': prior.get('volume') if prior else None,
                        'current_close': current.get('close') if current else None,
                        'current_change_pct': present.get('change_pct') if present else None,
                        'return_pct': change if stable else None, 'promotion': state if stable else None})
    absence = proof._count_day(previous)['default']['state'] is False if previous else False
    source_complete = bool(previous and absence and _complete_cohort_source(proof, previous) and unverified == 0 and not ambiguous)
    entire = source_complete and bool(confirmed) and unknown_state == 0
    rate = promoted/len(confirmed)*100 if entire else None
    rate_reason = None if entire else '昨日来源全集、逐股价格或今日涨停状态未全部核验，不能把已观察队列晋级率冒充完整晋级率'
    average = mean(gains) if gains else None; middle = median(gains) if gains else None
    coverage = len(gains)/len(confirmed)*100 if confirmed else None
    state = 'insufficient' if not source_complete or coverage is None or coverage < 80 else 'strong' if middle > 0 else 'weak' if middle < 0 else 'mixed'
    names = {'insufficient': '样本有限', 'strong': '昨日涨停样本偏强', 'weak': '昨日涨停样本偏弱', 'mixed': '昨日涨停样本分化'}
    heights = []
    for symbol, event in today.items():
        height = finite(event.get('continue_day_cnt', event.get('board_count', event.get('board_num'))))
        current = quotes.get(symbol, {}).get(day) if callable(getattr(market, 'history_for_instruments', None)) else indexed.get(symbol)
        if height is not None and height >= 1 and int(height) == height and proof._state(symbol, day, current)['state'] is True:
            heights.append(int(height))
    no_gain = None if gains else '昨日确认涨停样本今日缺少可比有效收益；不以0或亏损替代'
    identity_complete = not ambiguous and unverified == 0 and all(row['group'] != 'unknown' for row in members)
    groups = []
    for group_id in GROUP_LABELS:
        group_members = [row for row in members if row['group'] == group_id]
        group = cohort_group(group_id, group_members, source_complete=source_complete,
                             identity_complete=identity_complete and group_id != 'unknown')
        group.update(previous_date=previous, unallocated_candidate_count=unverified+len(ambiguous),
                     cohort_fingerprint=fingerprint({'date': previous, 'group': group_id, 'members': group_members}))
        groups.append(group)
    return {'label': '短线情绪', 'state': state, 'state_label': names[state],
            'scope': '前一市场交易日已确认收盘涨停队列，按当日收盘观察；非买入可成交收益',
            'previous_date': previous, 'sample_count': len(gains), 'observed_count': len(confirmed),
            'unknown_count': len(confirmed)-len(gains), 'coverage_pct': coverage,
            'cohort_candidate_count': len(yesterday)+len(ambiguous), 'previous_unverified_count': unverified+len(ambiguous),
            'cohort_source_complete': source_complete, 'promotion_unknown_count': unknown_state,
            'default_group_id': 'ordinary_10', 'groups': groups,
            'identity_complete': identity_complete,
            'cohort_fingerprint': fingerprint({'date': previous, 'members': members}),
            'promotion_bounds_pct': {'lower': promoted/len(confirmed)*100, 'upper': (promoted+unknown_state)/len(confirmed)*100} if source_complete and confirmed else None,
            'promotion_bounds_reason': None if source_complete and confirmed else '昨日全集与逐股涨停确认未齐，完整分母未知',
            'metrics': [metric('previous_limit_up_avg_return_pct', '昨日涨停今均涨幅', average, '%', '昨日价格核验涨停队列中，今日可比收盘涨幅的等权均值', no_gain),
                        metric('previous_limit_up_median_return_pct', '昨日涨停今中位数', middle, '%', '上述可比队列今日收盘涨幅的中位数', no_gain),
                        metric('positive_ratio_pct', '正反馈比例', sum(value > 0 for value in gains)/len(gains)*100 if gains else None, '%', '收益有效队列中收盘涨幅>0的数量/收益有效数；缺失不补零', no_gain),
                        metric('promotion_rate_pct', '完整队列晋级率', rate, '%', '昨日确认涨停且今日仍确认涨停数 ÷ 昨日完整确认涨停队列数 ×100；全集与逐股状态须齐备', rate_reason),
                        metric('promoted_count', '已确认晋级', promoted if confirmed else None, '家', '昨日、今日均经原始收盘和有效成交量核验涨停的队列数量；来源不齐时仅为观察下界', None if confirmed else '昨日没有可核验队列'),
                        metric('failed_count', '已确认未晋级', failed if confirmed else None, '家', '昨日确认涨停、今日有效报价及来源明确证明未涨停的数量；未知不计入', None if confirmed else '昨日没有可核验队列'),
                        metric('highest_board_count', '确认样本连板高度', max(heights) if heights else None, '板', '今日已核验收盘涨停样本的来源连板数最大值；来源不齐时不代表全市场最高', None if heights else '今日没有同时具备价格核验和有效来源连板数的样本')],
            'rules': '偏强/偏弱只评价昨日完整确认涨停队列的今日价格反馈，不等于已确认接力晋级强弱。来源全集未齐时保留已知均值与样本数，氛围待核验；新股、ST及20cm不混成同涨幅制度的打板收益。'}


def describe_sentiment(market, reference, rows, day):
    broad = market_distribution(rows, day)
    proof = LimitConditionEvidence(market)
    proof.reference = reference; proof.supplement = reference.get('_supplement', {})
    short = short_term_cohort(market, reference, rows, day, proof=proof)
    calendar_method = getattr(market, 'calendar', None)
    calendar = calendar_method() if callable(calendar_method) else []
    indices = index_environment(reference, calendar, day)
    return {'model': CONTEXT_VERSION, 'legacy_cohort_model': MODEL, 'date': day, 'scope': '指数环境、当日个股广度、前日涨停队列反馈三轴并列，不合成总分',
            'index_environment': indices, 'market': broad, 'short_term': short,
            'provenance': provenance(market, proof, rows, day, short['previous_date'], indices,
                                     cohort_fingerprint=short['cohort_fingerprint']),
            'atmosphere': {'label': '情绪氛围', 'summary': broad['state_label']+' · '+short['state_label']},
            'notice': '均为可核验价格样本的描述统计，不代表账户赚钱比例、心理预期、可成交收益或未来走势。没有通行统一的赚钱效应/情绪氛围公式，本项目公开计算样本与自定义描述阈值；炸板率需独立完整炸板池口径，本版未以开板次数或缺池强算。',
            'references': REFERENCES}
