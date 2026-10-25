"""Bounded daily price paths relative to one already-bound historical event.

Prices use the context's explicit basis and strict comparability guard. Raw
contexts block actions; verified forward contexts project both sides at the
same observation anchor. A later quote gap cannot erase an earlier failure.
"""
from math import isfinite
from decimal import Decimal, localcontext

VERSION = 'daily-history-price-path-v1'
RECLAIM_MODES = ('close_at_or_above', 'close_reclaim', 'first_close_reclaim',
                 'high_at_or_above', 'touch')


def _number(value, low, high, *, inclusive_high=True):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value >= low and (value <= high if inclusive_high else value < high)
            and isfinite(value))


def _positive_price(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value) and value>0


def validate_price_path(value):
    """No free-form expressions, extra keys, or implicit activation of paths."""
    if not isinstance(value, dict) or not value or set(value)-{'guard', 'reclaim', 'cross_cycle'}:
        raise ValueError('价格过程仅接受非空guard/reclaim/cross_cycle配置')
    result = {}
    if 'guard' in value:
        guard = value['guard']
        if not isinstance(guard, dict) or set(guard)-{'max_drawdown_pct', 'require_pullback', 'near_floor'}:
            raise ValueError('守位仅接受max_drawdown_pct/require_pullback/near_floor')
        ratio = guard.get('max_drawdown_pct')
        if not _number(ratio, 0, 100, inclusive_high=False):
            raise ValueError('允许回撤应为大于等于0且小于100的有限百分比')
        pullback = guard.get('require_pullback', False)
        if not isinstance(pullback, bool):
            raise ValueError('require_pullback必须为true/false')
        clean = {'max_drawdown_pct': ratio, 'require_pullback': pullback}
        if 'near_floor' in guard:
            near = guard['near_floor']
            if not isinstance(near, dict) or set(near) != {'sessions', 'band_pct'}:
                raise ValueError('靠近保护价需sessions和band_pct')
            sessions = near['sessions']; band = near['band_pct']
            if not isinstance(sessions, int) or isinstance(sessions, bool) or not 1 <= sessions <= 750:
                raise ValueError('回踩窗口应为1至750个市场交易日')
            if not _number(band, 0, 100):
                raise ValueError('靠近保护价幅度应为0至100的有限百分比')
            clean['near_floor'] = {'sessions': sessions, 'band_pct': band}
        result['guard'] = clean
    if 'reclaim' in value:
        reclaim = value['reclaim']
        if not isinstance(reclaim, dict) or set(reclaim) != {'event_field', 'mode'}:
            raise ValueError('收复配置需event_field和mode')
        if reclaim['event_field'] not in ('close', 'high', 'low') or reclaim['mode'] not in RECLAIM_MODES:
            raise ValueError('事件基准仅支持close/high/low，收复方式不受支持')
        result['reclaim'] = dict(reclaim)
    if 'cross_cycle' in value:
        cycle=value['cross_cycle']
        if (not isinstance(cycle,dict) or set(cycle)!={'indicator','require_dead_cross'}
                or cycle['indicator'] not in ('macd','kdj') or cycle['require_dead_cross'] is not True):
            raise ValueError('金叉过程需indicator=macd/kdj及require_dead_cross=true')
        result['cross_cycle']=dict(cycle)
    return result


def cross_cycle_fields(indicator):
    return ('macd_cross','macd_dead_cross') if indicator=='macd' else ('kd_cross','kd_dead_cross')


def validate_cross_cycle_event(event):
    cycle=event.get('price_path',{}).get('cross_cycle')
    if cycle is None:return
    where=event['where']
    if set(where)=={'all'} and len(where['all'])==1:where=where['all'][0]
    golden,_=cross_cycle_fields(cycle['indicator'])
    expected={'field':golden,'op':'==','value':True,'period':'D'}
    if event['window']['from']!=1 or event['match']!='latest' or where!=expected:
        raise ValueError('上次金叉过程仅支持从昨日回看/最近一次/纯同类日K金叉')


def all_results(values):
    return False if False in values else None if None in values else True


def _first_reason(checks, result):
    return next((item.get('reason') for item in checks if item['result'] is result and item.get('reason')), None)


def evaluate_price_path(spec, context, event, day):
    """Evaluate at T, never against the context's possibly later replay end."""
    start_position = context.positions.get(event.get('date'))
    end_position = context.positions.get(day)
    result = {'type': 'history_price_path', 'model': VERSION, 'result': None,
              'event_date': event.get('date'), 'observation_date': day,
              'observation_period': 'D', 'price_basis': context.price_basis,
              'reason': '缺少已绑定事件或精确市场交易日，价格过程保持未知'}
    if context.price_context:result['anchor_date']=context.price_context.get('anchor_date')
    if 'cross_cycle' in spec:
        result['model']='daily-history-price-path-v2-cross-cycle'
    if start_position is None or end_position is None or start_position >= end_position or day > context.as_of:
        return result

    days = context.calendar[start_position+1:end_position+1]
    comparable = {}
    blocking_date = blocking_reason = None
    # At most the event window's 750 sessions. price_binding uses sorted
    # blocking dates and disallows every price beyond the first obstruction.
    for observed in days:
        close, reason = context.price_binding(event, observed)
        if close is not None:
            comparable[observed] = close
        elif blocking_date is None:
            blocking_date, blocking_reason = observed, reason

    checks = []
    if 'guard' in spec:
        guard = _guard(spec['guard'], event, day, days, comparable,
                       blocking_date, blocking_reason)
        result['guard'] = guard; checks.append(guard)
    if 'reclaim' in spec:
        reclaim = _reclaim(spec['reclaim'], context, event, day, days, comparable,
                           blocking_date, blocking_reason)
        result['reclaim'] = reclaim; checks.append(reclaim)
    if 'cross_cycle' in spec:
        cycle=_cross_cycle(spec['cross_cycle'],context,event,day,days,comparable,blocking_date,blocking_reason)
        result['cross_cycle']=cycle;checks.append(cycle)
    state = all_results([item['result'] for item in checks])
    result.update(result=state, reason=_first_reason(checks, state) if state is not True else None)
    return result


def _cross_cycle(spec,context,event,day,days,comparable,blocking_date,blocking_reason):
    """The cycle is technical only; the independent then tree owns prices."""
    golden,dead=cross_cycle_fields(spec['indicator'])
    def signal(observed,field):
        value=context.daily.get(observed,{}).get(field)
        return value if isinstance(value,bool) else None
    event_cross=signal(event['date'],golden)
    current_cross=signal(day,golden) if day in comparable else None
    known=0;dead_date=intervening_cross=None;unknown_dead=unknown_golden=False
    for observed in days[:-1]:
        dead_value=signal(observed,dead) if observed in comparable else None
        golden_value=signal(observed,golden) if observed in comparable else None
        if isinstance(dead_value,bool) and isinstance(golden_value,bool):known+=1
        if dead_value is None:unknown_dead=True
        if golden_value is None:unknown_golden=True
        if dead_value is True and dead_date is None:dead_date=observed
        if golden_value is True and intervening_cross is None:intervening_cross=observed
    has_dead=True if dead_date else None if unknown_dead else False
    previous_confirmed=False if intervening_cross else None if unknown_golden else True
    state=all_results([event_cross,current_cross,has_dead,previous_confirmed])
    current_close=comparable.get(day)
    reason=(None if state is True else
            '绑定事件不是所选指标的已确认日线金叉' if event_cross is False else
            '观察日未出现所选指标的已确认日线金叉' if current_cross is False else
            '所选事件与今日之间还有更近的同类金叉，不能将旧事件当作上一次' if previous_confirmed is False else
            '事件与今日之间没有已确认的同类死叉，尚未完成金叉→死叉→再金叉' if has_dead is False else
            blocking_reason or '金叉、死叉或指标预热证据不足，不能把未知当作未发生')
    return {**spec,'result':state,'event_cross':event_cross,'current_cross':current_cross,
            'event_close':event['close'],'current_close':current_close,
            'close_above_event':current_close>event['close'] if _positive_price(current_close) and _positive_price(event.get('close')) else None,
            'dead_cross_result':has_dead,'dead_cross_date':dead_date,'expected_sessions':len(days)-1,
            'known_signal_sessions':known,'intervening_cross_date':intervening_cross,
            'previous_cross_confirmed':previous_confirmed,'blocking_date':blocking_date,'reason':reason}


def _guard(spec, event, day, days, comparable, blocking_date, blocking_reason):
    event_close = event['close']; ratio = spec['max_drawdown_pct']
    if not _positive_price(event_close):
        reason=blocking_reason or '缺少可核验的同锚事件收盘价，守位保持未知'
        guard={'result':None,'holding_result':None,'floor':None,'floor_decimal':None,
               'event_close':event_close,'max_drawdown_pct':ratio,'formula':f'事件收盘价 × (1 − {ratio}/100)',
               'start':days[0],'end':day,'expected_sessions':len(days),'comparable_sessions':len(comparable),
               'min_close':None,'min_close_date':None,'first_breach_date':None,'first_breach_close':None,
               'blocking_date':blocking_date,'reason':reason}
        if spec.get('require_pullback'):guard['pullback']={'result':None,'date':None,'close':None,'reason':reason}
        if 'near_floor' in spec:
            near=spec['near_floor'];selected=days[:-1][-near['sessions']:]
            guard['near_floor']={'result':None,**near,'start':selected[0] if selected else None,
                'end':selected[-1] if selected else None,'date':None,'close':None,'upper':None,'upper_decimal':None,
                'expected_sessions':len(selected),'reason':reason}
        return guard
    # Multiplication on binary floats can turn an exact 8.20 floor into
    # 8.200000000000001. Compare the unrounded decimal expression instead.
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        floor_decimal = Decimal(str(event_close)) * (1-Decimal(str(ratio))/100)
    floor = float(floor_decimal)
    minimum = min(comparable.items(), key=lambda item: item[1]) if comparable else (None, None)
    breach = next(((date, close) for date, close in comparable.items() if Decimal(str(close)) < floor_decimal), (None, None))
    holding = False if breach[0] else None if len(comparable) != len(days) else True
    guard = {'result': holding, 'holding_result': holding, 'floor': floor,
             'floor_decimal': format(floor_decimal, 'f'),
             'event_close': event_close, 'max_drawdown_pct': ratio,
             'formula': f'事件收盘价 × (1 − {ratio}/100)',
             'start': days[0], 'end': day, 'expected_sessions': len(days),
             'comparable_sessions': len(comparable), 'min_close': minimum[1],
             'min_close_date': minimum[0], 'first_breach_date': breach[0],
             'first_breach_close': breach[1], 'blocking_date': blocking_date,
             'reason': (f'{breach[0]}收盘价低于保护价，随后收回也不满足始终守位' if breach[0]
                        else blocking_reason if holding is None else None)}
    states = [holding]; subchecks = []
    past_days = days[:-1]
    if spec.get('require_pullback'):
        found = next(((date, comparable[date]) for date in past_days
                      if date in comparable and comparable[date] < event_close), (None, None))
        state = True if found[0] else None if any(date not in comparable for date in past_days) else False
        proof = {'result': state, 'date': found[0], 'close': found[1],
                 'reason': (None if state is True else blocking_reason if state is None
                            else '事件后至昨日未出现收盘低于事件收盘价的真实回落')}
        guard['pullback'] = proof; states.append(state); subchecks.append(proof)
    if 'near_floor' in spec:
        near = spec['near_floor']; selected = past_days[-near['sessions']:]
        with localcontext() as arithmetic:
            arithmetic.prec = 80
            upper_decimal = floor_decimal * (1+Decimal(str(near['band_pct']))/100)
        upper = float(upper_decimal)
        found = next(((date, comparable[date]) for date in selected
                      if date in comparable and floor_decimal <= Decimal(str(comparable[date])) <= upper_decimal
                      and comparable[date] < event_close), (None, None))
        state = True if found[0] else None if any(date not in comparable for date in selected) else False
        proof = {'result': state, **near, 'start': selected[0] if selected else None,
                 'end': selected[-1] if selected else None, 'date': found[0], 'close': found[1],
                 'upper': upper if isfinite(upper) else None,
                 'upper_decimal': format(upper_decimal, 'f'), 'expected_sessions': len(selected),
                 'reason': (None if state is True else blocking_reason if state is None
                            else '事件后所选回踩窗口没有收盘靠近保护价且低于事件收盘价')}
        guard['near_floor'] = proof; states.append(state); subchecks.append(proof)
    guard['result'] = all_results(states)
    if guard['result'] is not True and holding is not guard['result']:
        guard['reason'] = _first_reason(subchecks, guard['result'])
    return guard


def _reclaim(spec, context, event, day, days, comparable, blocking_date, blocking_reason):
    reference = event[spec['event_field']]; mode = spec['mode']
    previous_date = context.calendar[context.positions[day]-1]
    current = context.quotes.get(day) or {}
    previous_close = comparable.get(previous_date)
    close = comparable.get(day)
    current_known = close is not None
    proof = {**spec, 'result': None, 'reference_price': reference,
             'previous_date': previous_date, 'previous_close': previous_close,
             'current_date': day, 'current_close': close,
             'current_high': current.get('high') if current_known else None,
             'current_low': current.get('low') if current_known else None,
             'first_prior_reclaim_date': None, 'blocking_date': blocking_date, 'reason': blocking_reason}
    if not _positive_price(reference):
        proof['reason']=blocking_reason or '缺少可核验的同锚事件参照价，价格收复保持未知'
        return proof
    if mode == 'close_at_or_above':
        state = close >= reference if current_known else None
    elif mode == 'high_at_or_above':
        state = current['high'] >= reference if current_known else None
    elif mode == 'touch':
        state = current['low'] <= reference <= current['high'] if current_known else None
    elif previous_date <= event['date']:
        state = False
        proof['reason'] = '事件后尚无独立的昨日回踩交易日，不能认定重新收复'
    else:
        before = previous_close < reference if previous_close is not None else None
        after = close >= reference if current_known else None
        state = all_results([before, after])
        if mode == 'first_close_reclaim':
            previous_value = event['close']
            first_prior = None
            for observed in days[:-1]:
                value = comparable.get(observed)
                if value is not None and previous_value is not None and previous_value < reference <= value:
                    first_prior = observed; break
                previous_value = value
            proof['first_prior_reclaim_date'] = first_prior
            first = False if first_prior else None if any(date not in comparable for date in days[:-1]) else True
            state = all_results([state, first])
            if first_prior:
                proof['reason'] = f'{first_prior}已发生过价格收复，今日不是事件后第一次；不因当日其他技术条件失败而重计'
    proof['result'] = state
    if state is True:
        proof['reason'] = None
    elif state is False and mode in ('close_reclaim', 'first_close_reclaim') and previous_close is not None and previous_close >= reference and not proof['first_prior_reclaim_date']:
        proof['reason'] = '昨日收盘未低于事件基准，不满足由下向上重新收复；其他缺口单列保留'
    elif state is False and proof['reason'] is None:
        proof['reason'] = '观察日价格未满足所选收复方式' if mode in ('close_at_or_above', 'high_at_or_above', 'touch') else '需昨日收盘低于事件基准、今日收盘达到或超过该基准'
    return proof
