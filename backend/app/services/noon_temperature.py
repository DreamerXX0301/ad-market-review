"""Transparent noon sentiment reference derived from one retained JSON body.

This is an independent display rule. It performs no I/O and never calls the
post-close sentiment model, selection rules, tracking or backtests. Missing
inputs retain their original weight and prevent a composite score.
"""
from collections import Counter
from datetime import date
import math
import re

VERSION = 'noon-temperature-v1'
INDEX_CODES = ('000001.SH', '399001.SZ', '399006.SZ', '000688.SH')
STOCK_CODE = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
SCOPE = ('本地透明规则参考，仅描述本次午评已核验样本；不是官方指标、收盘确认、'
         '预测或策略信号，不触发选股、跟踪或回测。各域采集时点不保证同秒。')


def _object(value):
    return value if isinstance(value, dict) else {}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def temperature_label(score):
    """Fixed reference bands; none of the bands is a trading instruction."""
    value = _number(score)
    if value is None or not 0 <= value <= 100:
        return '数据不足'
    return ('偏冷' if value < 20 else '偏弱' if value < 40 else '中性'
            if value < 60 else '偏暖' if value < 80 else '偏热')


def _component(key, label, weight, value=None, reason=None, evidence=''):
    return {'key': key, 'label': label, 'weight': weight, 'value': value,
            'contribution': value * weight / 100 if value is not None else None,
            'reason': reason, 'evidence': evidence}


def _breadth(payload):
    summary = _object(payload.get('summary'))
    breadth = _object(summary.get('breadth'))
    coverage = _object(summary.get('coverage'))
    up, down, flat, valid, total = [_count(breadth.get(key)) for key in ('up', 'down', 'flat', 'valid', 'total')]
    reason = None
    if coverage.get('enumeration_complete') is not True:
        reason = '股票名录尚未完整枚举，不能用局部范围代替市场广度。'
    elif any(value is None for value in (up, down, flat, valid, total)) or not total or not valid:
        reason = '涨跌家数、有效报价或名录总数缺失，不补零。'
    elif valid > total or up + down + flat != valid:
        reason = '涨跌家数与有效报价数量不一致，未计算广度。'
    elif valid / total < .95:
        reason = '可比报价不足已枚举名录的95%，广度分项保持未知。'
    elif not up + down:
        reason = '全部可比样本为平盘，涨跌分母为零，未强行打分。'
    value = None if reason else up / (up + down) * 100
    evidence = (f'上涨{up}、下跌{down}、平盘{flat}；可比{valid}/{total}。平盘不进入涨跌比的分母。'
                if all(value is not None for value in (up, down, flat, valid, total)) else '可比报价与名录范围待核验。')
    return _component('breadth', '涨跌广度', 25, value, reason, evidence)


def _pool(payload, key):
    pool = _object(_object(payload.get('events')).get(key))
    rows = pool.get('items')
    if pool.get('state') != 'observed' or not isinstance(rows, list):
        return None, '来源池覆盖未完整核验。'
    codes = []
    for row in rows:
        symbol = _object(row).get('instrument_id')
        if not isinstance(symbol, str) or not STOCK_CODE.fullmatch(symbol):
            return None, '来源池证券身份未通过核验。'
        codes.append(symbol)
    if len(set(codes)) != len(codes):
        return None, '来源池存在重复证券，未重复计数。'
    declared = pool.get('count')
    if declared is not None and _count(declared) != len(codes):
        return None, '来源池家数与逐股明细不一致。'
    excluded = pool.get('excluded_rows')
    if excluded is not None and _count(excluded) != 0:
        return None, '来源池存在未核验记录，不能视为完整覆盖。'
    return set(codes), None


def _pool_pair(payload, second, *, key, label):
    up, reason_up = _pool(payload, 'limit_up')
    other, reason_other = _pool(payload, second)
    reason = reason_up or reason_other
    value = None
    if reason is None:
        if up & other:
            reason = '两来源池证券交集冲突，不能据此给出情绪分数。'
        elif not len(up) + len(other):
            reason = '两池均为空，比较分母为零，不把空样本判为强或弱。'
        else:
            value = len(up) / (len(up) + len(other)) * 100
    second_label = '炸板' if second == 'limit_break' else '跌停'
    evidence = (f'本次涨停池{len(up)}、{second_label}池{len(other)}；只描述午间来源样本。'
                if up is not None and other is not None else '本次涨停与对照池覆盖待核验。')
    return _component(key, label, 20, value, reason, evidence)


def _previous(payload, feedback):
    previous = _object(payload.get('previous_review'))
    if feedback is None:
        feedback = _object(previous.get('cohort_feedback'))
    feedback = _object(feedback)
    calendar = _object(payload.get('calendar'))
    prior, current = feedback.get('date'), payload.get('date')
    source = _count(feedback.get('source_count'))
    verified = _count(feedback.get('verified_count', feedback.get('observed_count')))
    valid = _count(feedback.get('valid_count', feedback.get('current_valid')))
    up, down, flat = [_count(feedback.get(key)) for key in ('up', 'down', 'flat')]
    reason = None
    if (not _day(prior) or not _day(current) or prior >= current
            or calendar.get('verified') is not True or calendar.get('previous_day') != prior
            or previous.get('date') != prior):
        reason = '昨日样本日期未与严格前一市场交易日核对。'
    elif feedback.get('state') not in ('observed', 'observed_sample', 'partial') or not source:
        reason = '未留存可核验的昨日强势股来源样本。'
    elif (any(value is None for value in (verified, valid, up, down, flat))
          or not valid or valid > verified or verified > source or up + down + flat != valid):
        reason = '昨日样本与午间有效报价统计缺失或不一致。'
    elif valid / source < .95:
        reason = '昨日来源样本的核验与当前报价交集不足95%，不把部分结果当作完整样本表现。'
    value = None if reason else up / valid * 100
    evidence = (f'昨日来源样本{source}、已核验{verified}、当前可比{valid}；上涨{up}、下跌{down}、平盘{flat}。只代表留存样本。'
                if all(value is not None for value in (source, verified, valid, up, down, flat)) else '昨日强势股留存样本与当前报价交集待核验。')
    return _component('previous_feedback', '昨日强势样本', 20, value, reason, evidence)


def _indices(payload):
    rows = payload.get('indices')
    rows = rows if isinstance(rows, list) else []
    rows = [row for row in rows if isinstance(row, dict) and row.get('instrument_id') in INDEX_CODES]
    counts = Counter(row['instrument_id'] for row in rows)
    reason = None
    if set(counts) != set(INDEX_CODES) or any(count != 1 for count in counts.values()):
        reason = '四个指定指数存在缺项或重复，指数支持分项保持未知。'
    values = []
    if reason is None:
        for row in rows:
            price, change = _number(row.get('latest_price')), _number(row.get('change_pct'))
            if price is None or price <= 0 or change is None:
                reason = '指定指数最新价或涨幅缺失，不以零涨幅替代。'
                break
            if any(key in row and row[key] != payload.get('date') for key in ('date', 'trade_date', 'quote_date')):
                reason = '指定指数报价所属日与午评日期冲突。'
                break
            values.append(change)
    value = None if reason else sum(1 if change > 0 else .5 if change == 0 else 0 for change in values) / 4 * 100
    evidence = (f'四指数上涨{sum(change > 0 for change in values)}、下跌{sum(change < 0 for change in values)}、平盘{sum(change == 0 for change in values)}；平盘按半份支持计算。'
                if reason is None else '上证、深成指、创业板指、科创50逐项核验。')
    return _component('indices', '指数支持', 15, value, reason, evidence)


def build_noon_temperature(payload, previous_feedback=None):
    """Return all five evidence components and a score only when all are known."""
    payload = _object(payload)
    components = [_breadth(payload),
                  _pool_pair(payload, 'limit_break', key='seal_sample', label='当前封板样本'),
                  _pool_pair(payload, 'limit_down', key='limit_balance', label='涨跌停对比'),
                  _previous(payload, previous_feedback), _indices(payload)]
    known = [component for component in components if component['value'] is not None]
    available = len(known) == len(components)
    score = round(sum(component['contribution'] for component in known), 1) if available else None
    return {'version': VERSION, 'state': 'available' if available else 'unavailable',
            'score': score, 'label': temperature_label(score), 'components': components,
            'coverage': {'available_components': len(known), 'total_components': len(components),
                         'weight_covered': sum(component['weight'] for component in known)},
            'scope': SCOPE,
            'reason': None if available else '以下分项尚未通过核验：' + '、'.join(
                component['label'] for component in components if component['value'] is None) + '。缺失项不重新分配权重，暂不计算综合温度。'}
