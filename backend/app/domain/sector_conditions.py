"""Independent, three-valued board-index rules with explicit period scopes."""
from copy import deepcopy
from math import isfinite

EVALUATOR_VERSION = 'sector-index-dwm-v1'
PERIODS = ('D', 'W', 'M')
WINDOWS = (5, 10, 20, 30, 60, 250)
NUMBERS = ('open', 'high', 'low', 'close', 'change_pct', 'volume', 'amount',
           'volume_ratio', 'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j')
BOOLEANS = ('macd_cross', 'kd_cross', 'macd_dead_cross', 'kd_dead_cross',
            'bullish_ma_alignment', 'volume_expand', 'volume_shrink')
FIELDS = {key: {'type': 'number', 'operators': ['>', '>=', '<', '<=', '==', '!=']}
          for key in (*NUMBERS, *(f'ma{n}' for n in WINDOWS))}
FIELDS.update({key: {'type': 'boolean', 'operators': ['==', '!=']}
               for key in (*BOOLEANS, *(f'{action}_ma{n}' for action in
                                      ('close_above', 'close_below', 'cross_above') for n in WINDOWS))})
FIELDS['close_breakout_high'] = {'type': 'number', 'operators': ['new_high'],
    'integer': True, 'min': 1, 'max': 750, 'step': 1, 'default': 60,
    'constant_only': True, 'fixed_period': 'D', 'parameter_kind': 'lookback_sessions'}
FIELDS['intraday_high_change_pct'] = {'type': 'number', 'operators': ['>=', '>', '<=', '<', '==', '!='],
                                      'fixed_period': 'D', 'default': 3}
for key in ('tracking_change_pct', 'trading_age'):
    FIELDS[key] = {'type': 'number', 'operators': ['>=', '>', '<=', '<', '==', '!='], 'tracking_only': True, 'fixed_period': 'D'}
for key in ('close_below_entry', 'close_above_entry_high', 'close_below_entry_low'):
    FIELDS[key] = {'type': 'boolean', 'operators': ['==', '!='], 'tracking_only': True, 'fixed_period': 'D'}
FIELDS['close_above_entry_high']['alerts_only'] = True
LABELS = {'open': '开盘点位', 'high': '最高点位', 'low': '最低点位', 'close': '收盘点位',
    'change_pct': '所选周期涨幅(%)', 'volume': '来源成交量', 'amount': '成交额(元)',
    'volume_ratio': '较前一可比周期成交量倍数', 'macd_dif': 'MACD DIF', 'macd_dea': 'MACD DEA',
    'macd_hist': 'MACD柱', 'kdj_k': 'KDJ K', 'kdj_d': 'KDJ D', 'kdj_j': 'KDJ J',
    'macd_cross': 'MACD金叉', 'kd_cross': 'KDJ金叉', 'macd_dead_cross': 'MACD死叉',
    'kd_dead_cross': 'KDJ死叉', 'bullish_ma_alignment': '均线多头排列（5>10>20>30>60）',
    'volume_expand': '所选周期放量', 'volume_shrink': '所选周期缩量',
    'close_breakout_high': '收盘价突破前N个市场交易日最高价',
    'intraday_high_change_pct': '观察日最高涨幅（相对前收，%）',
    'tracking_change_pct': '本轮跟踪涨幅(%)', 'trading_age': '本轮跟踪交易日',
    'close_below_entry': '收盘跌破本轮事件日收盘点位',
    'close_above_entry_high': '收盘高于本轮事件日最高点位',
    'close_below_entry_low': '收盘跌破本轮事件日最低点位'}


def specs():
    result = []
    for field, spec in FIELDS.items():
        label = LABELS.get(field)
        if label is None:
            n = field.rsplit('ma', 1)[-1]
            label = ('均线MA' if field.startswith('ma') else '收盘高于MA' if field.startswith('close_above')
                     else '收盘低于MA' if field.startswith('close_below') else '本周期上穿MA')+n
        category = '跟踪状态' if spec.get('tracking_only') else '事件信号' if field in (
            'macd_cross', 'kd_cross', 'macd_dead_cross', 'kd_dead_cross') or field.startswith('cross_above') else '数值与趋势'
        result.append({'field': field, 'label': label, 'category': category,
            'periods': ['D'] if spec.get('fixed_period') else list(PERIODS), **deepcopy(spec)})
    return result


def validate(node, default_period='D', scope='selection', _depth=0, _counter=None):
    if default_period not in PERIODS or scope not in ('selection', 'alert', 'exit'):
        raise ValueError('板块条件的周期或使用场景无效')
    counter = _counter if _counter is not None else [0]
    counter[0] += 1
    if counter[0] > 100 or _depth > 8 or not isinstance(node, dict):
        raise ValueError('板块条件需要对象，最多100节点、8层')
    group = 'all' if 'all' in node else 'any' if 'any' in node else None
    if group:
        if set(node) != {group} or not isinstance(node[group], list) or not 1 <= len(node[group]) <= 50:
            raise ValueError('板块组合条件只接受all/any及1至50个子条件')
        return {group: [validate(child, default_period, scope, _depth+1, counter) for child in node[group]]}
    if set(node)-{'field', 'op', 'value', 'right_field', 'period'}:
        raise ValueError('板块条件包含不支持的参数；不使用个股或历史事件条件')
    field = node.get('field'); op = node.get('op')
    if not isinstance(field, str): raise ValueError('板块条件字段需要字符串')
    spec = FIELDS.get(field)
    if spec is None or op not in spec['operators']:
        raise ValueError('不支持的板块条件字段或比较符：'+str(field))
    if spec.get('tracking_only') and scope == 'selection':
        raise ValueError('本轮跟踪条件不能用于选板')
    if spec.get('alerts_only') and scope != 'alert':
        raise ValueError('事件日最高点位条件只用于提醒，不自动结束跟踪')
    period = node.get('period', spec.get('fixed_period', default_period))
    if period not in PERIODS or spec.get('fixed_period') and period != spec['fixed_period']:
        raise ValueError('该条件只能使用日K市场交易日窗口' if spec.get('fixed_period') else '板块条件只支持日/周/月周期')
    result = {'field': field, 'op': op, **({'period': period} if 'period' in node or spec.get('fixed_period') else {})}
    if 'right_field' in node:
        right = FIELDS.get(node['right_field']) if isinstance(node['right_field'], str) else None
        if ('value' in node or not right or spec['type'] != 'number' or right['type'] != 'number'
                or spec.get('constant_only') or right.get('constant_only') or right.get('tracking_only') and scope == 'selection'
                or right.get('fixed_period') and right['fixed_period'] != period):
            raise ValueError('板块字段比较仅支持同周期数值白名单；新高窗口只接受常量整数')
        result['right_field'] = node['right_field']
    else:
        value = node.get('value')
        if spec['type'] == 'boolean' and not isinstance(value, bool):
            raise ValueError('布尔板块条件需要是/否')
        if spec['type'] == 'number' and finite(value) is None:
            raise ValueError('数值板块条件需要有限数字')
        if spec.get('integer') and (not isinstance(value, int) or isinstance(value, bool) or not spec['min'] <= value <= spec['max']):
            raise ValueError('新高窗口需要1至750个市场交易日的整数')
        result['value'] = value
    return result


def finite(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def evaluate(node, metrics_by_period, default_period='D'):
    group = 'all' if 'all' in node else 'any' if 'any' in node else None
    if group:
        checks = [evaluate(child, metrics_by_period, default_period) for child in node[group]]
        values = [check['result'] for check in checks]
        result = (False if False in values else None if None in values else True) if group == 'all' else (True if True in values else None if None in values else False)
        return {'logic': 'AND' if group == 'all' else 'OR', 'result': result, 'checks': checks}
    field = node['field']; spec = FIELDS[field]
    period = spec.get('fixed_period', node.get('period', default_period))
    metrics = metrics_by_period.get(period) or {}
    actual = metrics.get(field); right = metrics.get(node['right_field']) if 'right_field' in node else node['value']
    result = None
    if field == 'close_breakout_high':
        from app.domain.conditions import _evaluate_breakout
        return {**_evaluate_breakout(node, metrics), 'period': 'D', 'observation_date': metrics.get('observed_date'), 'price_basis': 'none'}
    actual = (actual if isinstance(actual, bool) else None) if spec['type'] == 'boolean' else finite(actual)
    if spec['type'] == 'number': right = finite(right)
    if actual is not None and right is not None:
        op = node['op']
        result = actual > right if op == '>' else actual >= right if op == '>=' else actual < right if op == '<' else actual <= right if op == '<=' else actual == right if op == '==' else actual != right
    return {**node, 'period': period, 'actual': actual, 'right_actual': right, 'result': result,
            'observation_date': metrics.get('observed_date'), 'price_basis': 'none',
            **({'evidence': metrics[field+'_evidence']} if field+'_evidence' in metrics else {}),
            'reason': metrics.get(field+'_reason') or metrics.get('reason') or '板块报价、周期完整性或指标预热不足' if result is None else None}


def periods_for(node, default_period='D'):
    if 'field' in node:
        return {FIELDS[node['field']].get('fixed_period', node.get('period', default_period))}
    return set().union(*(periods_for(child, default_period) for child in node.get('all', node.get('any', []))))


def policy_normalized(payload=None, default_period='D'):
    value = {} if payload is None else payload
    keys = {'duration_sessions', 'allow_entry_day_alerts', 'allow_entry_day_exits', 'alert_logic', 'alerts', 'exit_logic', 'exits'}
    if not isinstance(value, dict) or set(value)-keys:
        raise ValueError('板块跟踪政策参数无效')
    duration = value.get('duration_sessions', 30)
    if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 750:
        raise ValueError('板块跟踪期限需要1至750个市场交易日')
    result = {'duration_sessions': duration}
    for key in ('allow_entry_day_alerts', 'allow_entry_day_exits'):
        result[key] = value.get(key, False)
        if not isinstance(result[key], bool): raise ValueError('事件日提醒/结束开关需要是/否')
    for kind, scope in (('alert', 'alert'), ('exit', 'exit')):
        logic = value.get(kind+'_logic', 'ANY')
        conditions = value.get('alerts' if kind == 'alert' else 'exits', [])
        if logic not in ('ANY', 'ALL') or not isinstance(conditions, list) or len(conditions) > 50:
            raise ValueError('板块提醒/结束需要ANY或ALL及最多50项条件')
        result[kind+'_logic'] = logic
        result['alerts' if kind == 'alert' else 'exits'] = [validate(node, default_period, scope) for node in conditions]
    return result
