"""Causal structural price references with independently verified volume evidence."""
from datetime import date, timedelta
import math

from .support_resistance import WING, _consecutive, _day, _number, _valid_bar, confirmed_pivots

VERSION = 'structure-volume-levels-v1'
SHORT_VERSION = 'short-week-risk-levels-v1'
LOOKBACK = 120
SHORT_LOOKBACK = 20
ATR_PERIOD = 14
VOLUME_PERIOD = 20
STOP_ATR = 0.5
BREAKOUT_ATR = 0.2
RECENT_BARS = 5
TOLERANCE = 1e-9


def _segment(bars, period, sessions, *, stock, basis, strict_daily_complete=False):
    closed, coverage = {'W': ('week_closed', 'weekly_quote_coverage_complete'),
                        'M': ('month_closed', 'monthly_quote_coverage_complete')}.get(period, (None, None))
    # Only an explicitly forming period may be omitted. A bad completed final
    # bar clears the segment and cannot make an older quotation look current.
    forming = bool(bars and closed and bars[-1].get(closed) is False)
    if forming:
        bars = bars[:-1]
    segment = []
    for bar in bars:
        if (not _valid_bar(bar, period) or bar['date'] not in sessions
                or bar.get('price_basis', basis) != basis
                or (strict_daily_complete and (bar.get('complete') is False or bar.get('noon_provisional') is True))
                or (closed and bar.get(closed) is False)
                or (coverage and bar.get(coverage) is False)
                or (stock and basis == 'none' and bar.get('corporate_action_coverage_known') is not True)):
            segment = []
            continue
        if stock and basis == 'none' and bar.get('corporate_action'):
            segment = []
            if period != 'D':
                continue
        if segment and not _consecutive(segment[-1], bar, period, sessions):
            segment = []
        segment.append(bar)
    return segment, forming


def _atr(bars):
    result = {'period': ATR_PERIOD, 'value': None, 'window_start': None,
              'window_end': bars[-1]['date'] if bars else None,
              'observed_bars': len(bars), 'state': 'unknown',
              'reason': '连续可比报价不足15根，ATR14保持未知'}
    if len(bars) <= ATR_PERIOD:
        return result
    ranges = [max(current['high'] - current['low'],
                  abs(current['high'] - previous['close']),
                  abs(current['low'] - previous['close']))
              for previous, current in zip(bars, bars[1:])]
    value = sum(ranges[:ATR_PERIOD]) / ATR_PERIOD
    for current in ranges[ATR_PERIOD:]:
        value = (value * (ATR_PERIOD - 1) + current) / ATR_PERIOD
    if not math.isfinite(value) or value <= 0:
        return {**result, 'reason': 'ATR14没有正的有效波动值'}
    return {**result, 'value': value, 'window_start': bars[0]['date'],
            'state': 'ready', 'reason': None}


def _volume(bars, chart):
    current = bars[-1] if bars else {}
    result = {'current': current.get('volume') if _number(current.get('volume')) else None,
              'mean_previous20': None, 'ratio': None, 'state': 'unknown',
              'mean_start': None, 'mean_end': None,
              'reason': '当前及此前20根完整可比周期的成交量不足'}
    if len(bars) <= VOLUME_PERIOD:
        return result
    window = bars[-(VOLUME_PERIOD + 1):]
    if not all(_number(row.get('volume')) for row in window):
        return {**result, 'reason': '量能窗口存在缺失或非正成交量，不能按零或更早周期补足'}
    if chart['instrument']['kind'] == 'stock':
        if any(row.get('corporate_action_coverage_known') is not True for row in window):
            return {**result, 'reason': '量能窗口的公司行动覆盖未知，原始成交量可比性待核验'}
        first = date.fromisoformat(window[0]['date'])
        if chart['period'] == 'W':
            first -= timedelta(days=first.weekday())
        elif chart['period'] == 'M':
            first = first.replace(day=1)
        events = (chart.get('adjustment') or {}).get('events') or []
        action = any(row.get('corporate_action') or row.get('corporate_action_original') for row in window)
        action = action or any(_day(event.get('date')) and first.isoformat() <= event['date'] <= current['date']
                               for event in events if isinstance(event, dict))
        if action:
            return {**result, 'reason': '量能窗口跨公司行动，成交量保留来源原值，未证明可比，不作放缩量判断'}
    average = sum(row['volume'] for row in window[:-1]) / VOLUME_PERIOD
    if not math.isfinite(average) or average <= 0:
        return {**result, 'reason': '此前20周期均量没有有效的正有限值'}
    ratio = current['volume'] / average
    if not math.isfinite(ratio):
        return {**result, 'reason': '成交量倍数超出可核验的有限范围'}
    return {**result, 'mean_previous20': average, 'ratio': ratio,
            'state': 'expanded' if ratio >= 1.5 else 'contracted' if ratio <= 0.8 else 'normal',
            'mean_start': window[0]['date'], 'mean_end': window[-2]['date'], 'reason': None}


def _level(pivot, reference):
    return {'price': pivot['price'], 'source': 'confirmed_pivot_low' if pivot['line_type'] == 'support' else 'confirmed_pivot_high',
            'formed_at': pivot['date'], 'confirmed_on': pivot['confirmed_on'],
            'distance_pct': (pivot['price'] / reference - 1) * 100}


def _active(pivots, bars, kind, *, inclusive=False):
    field = 'low' if kind == 'support' else 'high'
    close = bars[-1]['close']
    eligible = []
    for pivot in pivots:
        if pivot['line_type'] != kind or pivot['confirmed_on'] > bars[-1]['date']:
            continue
        price = pivot['price']
        same_side = price <= close if kind == 'support' else price >= close
        if not same_side or (not inclusive and price == close):
            continue
        later = [row for row in bars if row['date'] > pivot['confirmed_on']]
        breached = any(row[field] < price * (1 - TOLERANCE) if kind == 'support'
                       else row[field] > price * (1 + TOLERANCE) for row in later)
        if not breached:
            eligible.append(pivot)
    return min(eligible, key=lambda row: (abs(row['price'] - close), -date.fromisoformat(row['date']).toordinal())) if eligible else None


def _buffered(level, atr, multiple, reference, *, state=None):
    if level is None or atr['value'] is None:
        return None
    buffer = atr['value'] * abs(multiple)
    price = level['price'] + atr['value'] * multiple
    if not math.isfinite(price) or price <= 0:
        return None
    return {**level, 'price': price, 'base_price': level['price'], 'buffer_atr': abs(multiple),
            'buffer_value': buffer, 'atr_reference_date': atr['window_end'],
            'distance_pct': (price / reference - 1) * 100,
            **({'state': state} if state else {})}


def _recent_breakout(bars, pivots, chart, *, structure_lookback=None):
    adjustment_date = max(
        (event['date'] for event in (chart.get('adjustment') or {}).get('events', [])
         if isinstance(event, dict) and _day(event.get('date'))), default=None,
    ) if chart.get('price_basis') == 'forward_event_affine' else None
    for index in range(len(bars) - 1, max(0, len(bars) - RECENT_BARS) - 1, -1):
        history = bars[:index]
        if len(history) <= ATR_PERIOD or (adjustment_date and history[-1]['date'] < adjustment_date):
            continue
        candidates = pivots
        if structure_lookback is not None:
            cutoff = history[-structure_lookback]['date'] if len(history) >= structure_lookback else history[0]['date']
            candidates = [pivot for pivot in pivots if pivot['date'] >= cutoff]
        pivot = _active(candidates, history, 'resistance', inclusive=True)
        if pivot is None:
            continue
        previous_atr = _atr(history)
        level = _buffered(_level(pivot, bars[-1]['close']), previous_atr,
                          BREAKOUT_ATR, bars[-1]['close'])
        if level is None or bars[index]['close'] <= level['price']:
            continue
        # The source high and volatility buffer are both frozen before this bar.
        subsequent = bars[index + 1:]
        failed = any(row['close'] < level['base_price'] for row in subsequent)
        retest = (not failed and bool(subsequent)
                  and bars[-1]['low'] <= level['price']
                  and bars[-1]['close'] >= level['base_price'])
        event_volume = _volume(bars[:index + 1], chart)
        return {**level, 'date': bars[index]['date'], 'close': bars[index]['close'],
                'state': 'failed' if failed else 'retest' if retest else 'held',
                'volume': event_volume, 'volume_confirmed': event_volume['state'] == 'expanded'}
    return None


def _support_break(bars, pivots):
    if len(bars) < WING * 2 + 2:
        return None
    pivot = _active(pivots, bars[:-1], 'support', inclusive=True)
    if pivot is None or bars[-1]['close'] >= pivot['price']:
        return None
    return {**_level(pivot, bars[-1]['close']), 'date': bars[-1]['date'], 'close': bars[-1]['close']}


def _signal(result):
    if result['support_break']:
        return {'state': 'support_broken', 'label': '收盘跌破事前支撑',
                'reason': '最近完整周期收盘已低于此前确认的支撑，原支撑不能继续当作未失守止损依据'}
    event = result['recent_breakout']
    if event:
        if event['state'] == 'failed':
            return {'state': 'failed_breakout', 'label': '突破后回落', 'reason': '突破后有完整周期收盘回到原压力位下方'}
        if event['state'] == 'retest':
            return {'state': 'retest_holding', 'label': '回踩仍在原压力位上方',
                    'reason': '最近完整周期触及原突破区，收盘保持在原压力位上方；回踩量能单列核验'}
        volume = event['volume']
        if volume['state'] == 'expanded':
            return {'state': 'expanded_breakout', 'label': '收盘突破且放量',
                    'reason': '收盘超过事前确认压力位及0.2ATR缓冲，突破周期成交量至少为前20周期均量的1.5倍'}
        if volume['state'] == 'unknown':
            return {'state': 'breakout_volume_unknown', 'label': '收盘突破 · 量能待核验', 'reason': volume['reason']}
        return {'state': 'breakout_without_expansion', 'label': '收盘突破 · 未达放量门槛',
                'reason': '价格超过事前压力及缓冲，突破周期成交量未达到前20周期均量的1.5倍'}
    if result['reference_price'] is None:
        return {'state': 'unknown', 'label': '参考待核验', 'reason': result['reason']}
    return {'state': 'watching', 'label': '观察结构与量能',
            'reason': '窗口内未确认最近五个完整周期的收盘突破，候选价位随当前已确认结构展示'}


def _short_risk_boundary(base, atr, reference, reference_date, max_risk_pct):
    """A bounded observation reference, never a claimed support or cost stop."""
    if atr['value'] is None:
        return None
    candidates = [
        {'basis': 'reference_close_minus_atr14', 'price': reference - atr['value']},
        {'basis': 'maximum_risk_distance', 'price': reference * (1 - max_risk_pct / 100)},
    ]
    if base is not None:
        candidates.append({'basis': 'near_structure_minus_quarter_atr14',
                           'price': base['price'] - 0.25 * atr['value']})
    selected = max(candidates, key=lambda item: item['price'])
    price = selected['price']
    if not math.isfinite(price) or not 0 < price < reference:
        return None
    distance = reference - price
    return {'price': price, 'source': 'short_risk_boundary',
            'formed_at': reference_date, 'confirmed_on': reference_date,
            'base_price': reference, 'buffer_atr': distance / atr['value'],
            'buffer_value': distance, 'atr_reference_date': atr['window_end'],
            'buffer_basis': 'latest_complete_atr14',
            'distance_pct': (price / reference - 1) * 100,
            'max_risk_pct': max_risk_pct, 'risk_max_pct': max_risk_pct,
            'selected_basis': selected['basis'], 'candidates': candidates,
            'structure_reference': base,
            'notice': '按参考收盘计算的短线风险边界，不是真实支撑或个人成本止损；风险距离与ATR系数未经最优性回测，也不保证成交损失上限'}


def price_volume_levels(chart, calendar, *, expected_snapshot=None,
                        profile='structure', max_risk_pct=5.0):
    if profile not in ('structure', 'short_week_v1'):
        raise ValueError('Unsupported price-volume profile')
    if (isinstance(max_risk_pct, bool) or not isinstance(max_risk_pct, (int, float))
            or not math.isfinite(max_risk_pct) or not 0 < max_risk_pct < 100):
        raise ValueError('max_risk_pct must be a finite percentage between 0 and 100')
    short = profile == 'short_week_v1'
    instrument = chart.get('instrument') or {}
    period, as_of, basis = (chart.get(key) for key in ('period', 'as_of', 'price_basis'))
    result = {'algorithm': VERSION, 'instrument_id': instrument.get('instrument_id'),
              'snapshot_id': chart.get('snapshot_id'), 'as_of': as_of, 'period': period,
              'price_basis': basis, 'reference_date': None, 'reference_price': None,
              'state': 'unknown', 'reason': None, 'lookback': LOOKBACK,
              'support': None, 'resistance': None, 'stop': None, 'breakout': None,
              'recent_breakout': None, 'support_break': None, 'volume': _volume([], chart), 'atr': _atr([])}
    if short:
        result.update(algorithm=SHORT_VERSION, profile=profile,
                      structure_lookback=SHORT_LOOKBACK, max_risk_pct=max_risk_pct,
                      risk_max_pct=max_risk_pct,
                      support_reason='暂无可核验的近端支撑',
                      display_support=None, display_scope=None, display_distance_state=None,
                      breakout_reason='暂无可核验的近端突破参考',
                      notice='一周持股观察档位；结构取最近20根完整日K，ATR与量能沿用连续可比报价。参数未经最优性回测，风险边界不等同个人持仓止损')
        if period != 'D' or instrument.get('kind') != 'stock':
            result['reason'] = '一周短线参考仅支持个股完整日K'
            result['signal'] = _signal(result)
            return result
    if (instrument.get('kind') not in ('stock', 'index') or period not in ('D', 'W', 'M')
            or not _day(as_of) or not result['snapshot_id']
            or (expected_snapshot and result['snapshot_id'] != expected_snapshot)):
        result['reason'] = '证券、观察日、周期或已发布行情代际未能核验'
    elif basis not in ('none', 'forward_event_affine'):
        result['reason'] = '图表价格口径未能核验'
    elif basis == 'forward_event_affine' and ((chart.get('adjustment') or {}).get('usable') is not True
            or (chart.get('adjustment') or {}).get('anchor') != as_of):
        result['reason'] = '前复权价格没有绑定当前观察日，暂不计算价位'
    else:
        bars = list(chart.get('bars') or [])[-LOOKBACK:]
        days = [row.get('date') for row in bars]
        if (any(not _day(day) or day > as_of for day in days) or days != sorted(set(days))
                or not calendar or list(calendar) != sorted(set(calendar)) or as_of not in calendar):
            result['reason'] = '报价或交易日历存在缺失、重复、乱序或晚于观察日'
        elif not bars or bars[-1]['date'] != as_of:
            result['reason'] = '观察日没有可核验报价，不使用更早报价冒充当前参考'
        else:
            sessions = {day: index for index, day in enumerate(calendar)}
            segment, forming = _segment(bars, period, sessions, stock=instrument['kind'] == 'stock', basis=basis,
                                        strict_daily_complete=short)
            if not segment:
                result['reason'] = '最新完整周期价格或可比口径未核验，不回退更早报价'
            else:
                reference = segment[-1]['close']
                pivots = list(confirmed_pivots(segment, period, sessions))
                display_pivots = pivots
                if short:
                    cutoff = segment[-SHORT_LOOKBACK]['date'] if len(segment) >= SHORT_LOOKBACK else segment[0]['date']
                    pivots = [pivot for pivot in pivots if pivot['date'] >= cutoff]
                support, resistance = (_active(pivots, segment, kind) for kind in ('support', 'resistance'))
                if short:
                    # A risk tolerance is not evidence that a real support
                    # ceases to exist. Keep observation separate from the
                    # original nearby monitoring and risk-line contract.
                    display = support or _active(display_pivots, segment, 'support')
                    if display:
                        result['display_support'] = _level(display, reference)
                        result['display_scope'] = 'recent_20' if support else 'historical_120'
                        result['display_distance_state'] = ('far' if (reference - display['price']) / reference * 100 > max_risk_pct + TOLERANCE
                                                            else 'within_risk_distance')
                atr = _atr(segment)
                result.update(reference_date=segment[-1]['date'], reference_price=reference,
                              state='ready', volume=_volume(segment, chart), atr=atr,
                              observed_bars=len(segment), window_start=segment[0]['date'], window_end=segment[-1]['date'],
                              reason='当前周期尚未完成，参考截至最近完整周期' if forming else None)
                result['support'] = _level(support, reference) if support else None
                result['resistance'] = _level(resistance, reference) if resistance else None
                if short:
                    if result['support'] is None:
                        result['support_reason'] = '近20根完整日K暂无已确认且仍有效的近端支撑'
                    elif -result['support']['distance_pct'] > max_risk_pct + TOLERANCE:
                        result['support_reason'] = f'已确认支撑距参考收盘{-result["support"]["distance_pct"]:.2f}%，超过{max_risk_pct:g}%短线距离范围；暂无可用近端支撑'
                        result['support'] = None
                    else:
                        result['support_reason'] = None
                result['recent_breakout'] = _recent_breakout(segment, pivots, chart,
                    structure_lookback=SHORT_LOOKBACK if short else None)
                result['support_break'] = _support_break(segment, pivots)
                event = result['recent_breakout']
                stop_base = result['support']
                if (event and event['state'] != 'failed' and event['base_price'] <= reference
                        and (stop_base is None or event['base_price'] > stop_base['price'])):
                    stop_base = {**_level({'line_type': 'resistance', 'price': event['base_price'],
                                          'date': event['formed_at'], 'confirmed_on': event['confirmed_on']}, reference),
                                 'source': 'breakout_retest_reference', 'breakout_date': event['date']}
                result['stop'] = (_short_risk_boundary(stop_base, atr, reference, segment[-1]['date'], max_risk_pct)
                                  if short else _buffered(stop_base, atr, -STOP_ATR, reference))
                if result['stop'] is not None:
                    result['stop']['buffer_basis'] = 'latest_complete_atr14'
                if event and event['state'] != 'failed':
                    result['breakout'] = {key: value for key, value in event.items()
                                          if key not in ('date', 'close', 'volume', 'volume_confirmed')}
                else:
                    result['breakout'] = _buffered(result['resistance'], atr, BREAKOUT_ATR, reference, state='watching')
                if short:
                    result['breakout_reason'] = None if result['breakout'] else '近20根完整日K暂无可核验的近端压力与突破参考'
                    if atr['value'] is None or result['stop'] is None:
                        result['state'] = 'unknown'
                        result['reason'] = atr['reason'] or '无法形成正值且低于参考收盘的短线风险边界'
                    elif result['support'] is None:
                        result['reason'] = '暂无近端支撑；短线风险边界按波动与风险距离计算，不代表真实支撑'
    result['signal'] = _signal(result)
    return result


def price_volume_profiles(chart, calendar, *, expected_snapshot=None, max_risk_pct=5.0):
    """Keep the original structural contract and expose short-term data apart."""
    result = price_volume_levels(chart, calendar, expected_snapshot=expected_snapshot)
    result['short_term'] = price_volume_levels(chart, calendar, expected_snapshot=expected_snapshot,
                                             profile='short_week_v1', max_risk_pct=max_risk_pct)
    return result
