"""Deterministic, frozen technical observations; no fetching or trade signals.

Calculate on the whole retained prefix before bounding the chart projection.
Missing expected periods and incomparable prices remain unknown, never an
invitation to select an older convenient observation or invent a target price.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from math import isfinite

from app.analytics.chart_indicators import indicator_gaps, quote_gaps
from app.analytics.indicators import INDICATOR_VERSION, enrich_bars, aggregate_weekly
from app.analytics.monthly import aggregate_monthly, enrich_monthly_bars
from app.analytics.waves import candidate_waves, _continuous_segments

VERSION = 'research-technical-v1'
TREND_PARAMETERS = {
    'D': {'fast': 20, 'slow': 60, 'slope_periods': 5},
    'W': {'fast': 10, 'slow': 20, 'slope_periods': 3},
    'M': {'fast': 5, 'slow': 10, 'slope_periods': 2},
}
CHART_LIMITS = {'D': 320, 'W': 260, 'M': 72}
LEVEL_THRESHOLDS = {'D': 2.0, 'W': 4.0, 'M': 8.0}
OBSERVATION_FIELDS = ['close', 'ma5', 'ma10', 'ma20', 'ma30', 'ma60', 'ma250',
                      'volume', 'amount', 'volume_ratio', 'macd_dif', 'macd_dea',
                      'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j', 'macd_cross',
                      'macd_dead_cross', 'kd_cross', 'kd_dead_cross']
INDICATOR_FIELDS = [*OBSERVATION_FIELDS[1:7], 'change_pct', 'pct_change',
                    'volume_ratio', 'volume_multiple_prev', 'return_30d',
                    *OBSERVATION_FIELDS[10:]]
CHART_FIELDS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount',
                *INDICATOR_FIELDS, 'price_basis', 'corporate_action',
                'corporate_action_coverage_known', 'price_continuity',
                'indicator_warmup_bars', 'indicator_reset_reason',
                'indicator_warmup_reason', 'missing_market_sessions_before',
                'complete', 'week_closed', 'month_closed', 'window_start_partial',
                'weekly_quote_coverage_complete', 'weekly_missing_market_sessions',
                'weekly_missing_market_dates', 'weekly_continuity_reset_reason',
                'monthly_quote_coverage_complete', 'monthly_missing_market_sessions',
                'monthly_missing_market_dates', 'monthly_continuity_reset_reason',
                'monthly_indicator_eligible', 'monthly_price_comparable']


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _day(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _clean(value):
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _key(day, period):
    if period == 'D':
        return day
    if period == 'M':
        return day[:7]
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f'{year}-W{week:02d}'


def _expected(calendar, as_of, period):
    sessions = [day for day in calendar if day <= as_of]
    if not sessions:
        return None, None
    if period == 'D':
        return sessions[-1], sessions[-1]
    grouped = defaultdict(list)
    for day in sessions:
        grouped[_key(day, period)].append(day)
    closed = []
    for key, days in grouped.items():
        last = days[-1]
        if period == 'W':
            following = next((day for day in calendar if day > last), None)
            complete = (date.fromisoformat(last).weekday() == 4 or
                        (following is not None and _key(following, 'W') != key))
        else:
            year, month = map(int, key.split('-'))
            next_month = date(year + (month == 12), month % 12 + 1, 1).isoformat()
            month_end = (date.fromisoformat(next_month) - timedelta(days=1)).isoformat()
            known_days = [day for day in calendar if day[:7] == key]
            complete = (key < as_of[:7] or as_of >= month_end or
                        (calendar[-1] >= next_month and known_days[-1] <= as_of))
        if complete:
            closed.append((key, last))
    return closed[-1] if closed else (None, None)


def _load(market, symbol, as_of):
    calendar = sorted({day for day in market.calendar() if _day(day)})
    try:
        sources = market.bars(symbol, as_of=as_of)
    except KeyError:
        sources = []
    dated = [dict(row) for row in sources if isinstance(row, dict) and _day(row.get('date')) and row['date'] <= as_of]
    counts = Counter(row['date'] for row in dated)
    warnings = []
    duplicates = sum(count > 1 for count in counts.values())
    if duplicates:
        warnings.append(f'{duplicates}个交易日有重复报价，已隔离，不参与可比周期')
    rows = []
    sessions = set(calendar)
    for row in sorted(dated, key=lambda row: row['date']):
        if counts[row['date']] != 1:
            continue
        prices = [row.get(field) for field in ('open', 'high', 'low', 'close')]
        valid = all(_finite(value) and value > 0 for value in prices)
        if valid:
            valid = row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']
        if not valid or not _finite(row.get('volume')) or row['volume'] <= 0 or row['date'] not in sessions:
            warnings.append(f"{row['date']}报价、有效成交量或市场日历待核验，未补K线")
            continue
        if row.get('price_basis', 'none') != 'none':
            warnings.append(f"{row['date']}不是本报告原始价格口径，已隔离")
            continue
        row['amount'] = row['amount'] if _finite(row.get('amount')) and row['amount'] >= 0 else float('nan')
        rows.append(row)
    if hasattr(market, 'actions'):
        actions = market.actions(symbol, end=as_of)
    elif rows and hasattr(market, 'corporate_actions_between'):
        actions = market.corporate_actions_between(symbol, rows[0]['date'], as_of)
    else:
        actions = []
    action_dates = {row['date'] for row in actions if isinstance(row, dict) and _day(row.get('date')) and row['date'] <= as_of}
    unknown_actions = any(not isinstance(row, dict) or not _day(row.get('date')) for row in actions)
    if unknown_actions:
        warnings.append('存在日期未核验公司行动，原始跨期价格比较保持未知')
    rows = [{**row, 'corporate_action': bool(row.get('corporate_action') or row['date'] in action_dates),
             **({'corporate_action_coverage_known': False} if unknown_actions else {})} for row in rows]
    return calendar, rows, warnings


def _weekly(rows, calendar, as_of):
    values = aggregate_weekly(rows, calendar, as_of)
    if rows and values and calendar:
        start = date.fromisoformat(rows[0]['date'])
        monday = (start - timedelta(days=start.weekday())).isoformat()
        known = [day for day in calendar if _key(day, 'W') == _key(rows[0]['date'], 'W')]
        if calendar[0] > monday or (known and rows[0]['date'] > known[0]):
            values[0] = {**values[0], 'complete': False, 'window_start_partial': True,
                         'weekly_continuity_reset_reason': '首周日历或报价边界不完整，不纳入可比周指标'}
    eligible, reasons = [], {}
    pending = None
    for source in values:
        reason = ('周内存在尚未复权公司行动，不纳入可比周指标' if source.get('corporate_action') else
                  source.get('weekly_continuity_reset_reason') or '周尚未完成或报价不完整，不纳入可比周指标')
        if source.get('complete') is not True or source.get('corporate_action'):
            reasons[source['date']] = pending = reason
            continue
        row = dict(source)
        if pending:
            row['weekly_continuity_reset_reason'] = row.get('weekly_continuity_reset_reason') or pending
        eligible.append(row)
        pending = None
    computed = {row['date']: row for row in enrich_bars(eligible)}
    return [{**computed[row['date']], 'research_period_eligible': True} if row['date'] in computed else
            {**row, **{field: None for field in INDICATOR_FIELDS}, 'research_period_eligible': False,
             'price_continuity': False, 'indicator_warmup_bars': 0,
             'indicator_reset_reason': reasons[row['date']], 'indicator_warmup_reason': reasons[row['date']]}
            for row in values]


def _prepare(market, symbol, as_of):
    if not _day(as_of):
        raise ValueError('研究观察日期必须为有效YYYY-MM-DD')
    calendar, raw, warnings = _load(market, symbol, as_of)
    grouped = {period: defaultdict(list) for period in ('W', 'M')}
    for row in raw:
        for period in grouped:
            grouped[period][_key(row['date'], period)].append(row)
    values = {'D': enrich_bars(raw, calendar), 'W': _weekly(raw, calendar, as_of),
              'M': enrich_monthly_bars(aggregate_monthly(raw, calendar, as_of))}
    # A retained calendar ending before a later weekday cannot establish that
    # no new session occurred. A Saturday/Sunday immediately after its Friday
    # is the only schedule-only exception, not an inference about holidays.
    horizon_known = bool(calendar and (calendar[-1] >= as_of or
                         (date.fromisoformat(as_of).weekday() >= 5 and
                          date.fromisoformat(calendar[-1]).weekday() == 4 and
                          (date.fromisoformat(as_of) - date.fromisoformat(calendar[-1])).days <= 2)))
    if not horizon_known:
        warnings.append('市场日历未覆盖观察日，不能确认最新应有周期，不把较早行情当作最新')
    periods = []
    for period, label in (('D', '日线'), ('W', '周线'), ('M', '月线')):
        bars = values[period]
        for row in bars:
            if period != 'D':
                constituents = grouped[period][_key(row['date'], period)]
                row['corporate_action_coverage_known'] = bool(constituents) and all(item.get('corporate_action_coverage_known') is True for item in constituents)
            row.setdefault('research_period_eligible', period == 'D' or row.get('monthly_indicator_eligible') is True)
        expected, expected_date = _expected(calendar, as_of, period) if horizon_known else (None, None)
        match = next((row for row in bars if _key(row['date'], period) == expected), None)
        latest = match if match and match.get('research_period_eligible') is True and match['date'] == expected_date else None
        periods.append({'period': period, 'label': label, 'bars': bars,
                        'eligible_bars': [row for row in bars if row.get('research_period_eligible') is True],
                        'latest': latest, 'expected_period': expected, 'expected_date': expected_date,
                        'current_period': bars[-1] if bars else None})
    return {'calendar': calendar, 'raw': raw, 'periods': periods, 'warnings': warnings,
            'calendar_horizon_known': horizon_known}


def build_legacy_trends(market, symbol, as_of):
    prepared = _prepare(market, symbol, as_of)
    raw = prepared['raw']
    if not raw:
        return []
    result = []
    fields = OBSERVATION_FIELDS[:16]
    for item in prepared['periods']:
        latest, period = item['latest'], item['period']
        observations = _clean({field: latest.get(field) if latest else None for field in fields})
        facts = []
        if latest:
            for window in (5, 10, 20, 30, 60, 250):
                average = latest.get('ma' + str(window))
                if average is not None:
                    facts.append('收盘' + ('高于' if latest['close'] > average else '低于' if latest['close'] < average else '等于') + 'MA' + str(window))
        missing = [field for field, value in observations.items() if value is None]
        warnings = list(prepared['warnings'])
        if not latest:
            warnings.append(f"最近应有的{item['label']}周期{item['expected_period'] or '尚未核验'}缺失、未闭合或不可比，不回退更早周期")
        if period == 'M':
            warnings.append(f"月线由已留存日OHLC聚合，共{len(item['bars'])}根、其中{len(item['eligible_bars'])}根完整可比月；首月、形成中月、报价缺口或尚未复权公司行动月不凑入35根可比月指标预热。")
        current = item['current_period']
        if period != 'D' and current and current.get('research_period_eligible') is not True:
            warnings.append('当前周期尚未完成、报价不完整或价格不可比，仅展示聚合预览；指标与观察核验最近应完成周期。')
        if missing:
            warnings.append('样本不足、缺口或公司行动后的预热不足指标保持未知；MACD/KDJ至少需要35根可比周期。')
        result.append({'period': period, 'label': item['label'], 'source': '本地已发布OHLC',
                       'price_basis': 'none', 'sample_bars': len(item['bars']), 'complete_bars': len(item['eligible_bars']),
                       'data_begin': raw[0]['date'], 'data_end': raw[-1]['date'], 'expected_period': item['expected_period'],
                       'latest_complete_date': latest['date'] if latest else None,
                       'indicator_warmup_bars': latest.get('indicator_warmup_bars', 0) if latest else 0,
                       'observations': observations, 'missing_indicators': missing, 'facts': facts,
                       'current_period': {key: current.get(key) for key in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'complete')} if current else None,
                       'warnings': warnings})
    return _clean(result)


def _instrument(market, symbol):
    try:
        return dict(market.instrument(symbol))
    except (AttributeError, KeyError, StopIteration):
        return {'instrument_id': symbol, 'name': symbol, 'kind': 'unknown'}


def _pct(value, reference):
    result = (value / reference - 1) * 100 if _finite(value) and _finite(reference) and reference > 0 else None
    return result if _finite(result) else None


def _known(row, instrument):
    return instrument.get('kind') == 'index' or row.get('corporate_action_coverage_known') is True


def _assessment(item, instrument):
    period, latest = item['period'], item['latest']
    config = TREND_PARAMETERS[period]
    fast, slow, slope = config['fast'], config['slow'], config['slope_periods']
    eligible = item['eligible_bars']
    index = next((index for index, row in enumerate(eligible) if latest and row['date'] == latest['date']), None)
    previous = eligible[index - slope] if index is not None and index >= slope else None
    observations = {field: latest.get(field) if latest else None for field in OBSERVATION_FIELDS}
    observations.update(ma_fast=latest.get(f'ma{fast}') if latest else None,
                        ma_slow=latest.get(f'ma{slow}') if latest else None,
                        ma_fast_previous=previous.get(f'ma{fast}') if previous else None,
                        slope_start_date=previous['date'] if previous else None,
                        ma_fast_window=fast, ma_slow_window=slow, slope_periods=slope)
    observations['slope_pct'] = _pct(observations['ma_fast'], observations['ma_fast_previous'])
    required = max(slow, fast + slope)
    window = eligible[max(0, index - required + 1):index + 1] if index is not None else []
    known = len(window) == required and all(_known(row, instrument) for row in window)
    comparable = latest and latest.get('indicator_warmup_bars', 0) >= required
    values = [observations[key] for key in ('close', 'ma_fast', 'ma_slow', 'ma_fast_previous')]
    ready = bool(comparable and known and all(_finite(value) for value in values))
    state, reason = 'unknown', ''
    if not latest:
        reason = f"最近应有完整{item['label']}周期{item['expected_period'] or '尚未核验'}缺失或不可比，不使用较早周期代替"
    elif not known:
        reason = f'所需{required}根连续可比周期或公司行动覆盖尚未核验'
    elif not ready:
        reason = f'缺口、公司行动或指标预热不足，需要{required}根连续可比周期'
    else:
        close, average, baseline, old_average = values
        strong = close > average > baseline and average > old_average
        weak = close < average < baseline and average < old_average
        state = 'strong' if strong else 'weak' if weak else 'mixed'
    labels = {'strong': '条件偏强', 'weak': '条件偏弱', 'mixed': '条件分歧', 'unknown': '待核验'}
    pairs = [('收盘价 > MA' + str(fast), observations['close'], observations['ma_fast']),
             (f'MA{fast} > MA{slow}', observations['ma_fast'], observations['ma_slow']),
             (f'MA{fast}高于{slope}个完整周期前', observations['ma_fast'], observations['ma_fast_previous'])]
    rules = [{'label': label, 'met': left > right if ready else None, 'left': left, 'right': right}
             for label, left, right in pairs]
    current = item['current_period']
    preview = ({key: current.get(key) for key in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'complete')} if current else None)
    if preview is not None:
        preview.update(forming=period != 'D' and current.get('week_closed' if period == 'W' else 'month_closed') is False,
                       comparable=current.get('research_period_eligible') is True and _known(current, instrument),
                       reason=(current.get('indicator_reset_reason') if current.get('research_period_eligible') is not True else
                               '公司行动覆盖待核验，不能证明原始跨期价格可比' if not _known(current, instrument) else None))
    return {'period': period, 'label': item['label'], 'state': state, 'state_label': labels[state],
            'observed_date': latest['date'] if latest else None, 'expected_period': item['expected_period'],
            'expected_date': item['expected_date'], 'observations': observations, 'rules': rules,
            'reason': reason, 'required_bars': required,
            'warmup_bars': latest.get('indicator_warmup_bars', 0) if latest else 0,
            'current_period': preview, 'warnings': [reason] if reason else []}


def _volume(prepared, instrument):
    daily = prepared['periods'][0]['latest']
    result = {'ratio_vs_previous20': None, 'previous20_average_volume': None,
              'current_volume': daily.get('volume') if daily else None, 'window_sessions': 20,
              'start_date': None, 'end_date': daily['date'] if daily else None,
              'unit': '来源股数', 'reason': ''}
    if not daily:
        return {**result, 'reason': '观察日没有有效成交报价，不用较早报价替代'}
    sessions = prepared['calendar']
    index = sessions.index(daily['date'])
    dates = sessions[max(0, index - 20):index]
    by_day = {row['date']: row for row in prepared['raw']}
    window = [by_day.get(day) for day in dates]
    if len(window) != 20 or any(row is None or not _known(row, instrument) for row in window) or not _known(daily, instrument):
        return {**result, 'reason': '前20个精确市场交易日的成交报价或公司行动覆盖不足'}
    if daily.get('corporate_action') or any(row.get('corporate_action') for row in window):
        return {**result, 'reason': '窗口内有未复权公司行动，股数口径可能变化，不混算量能倍数'}
    average = sum(row['volume'] for row in window) / 20
    if not _finite(average) or average <= 0:
        return {**result, 'reason': '窗口平均成交量超出可计算范围，保持未知'}
    return {**result, 'ratio_vs_previous20': daily['volume'] / average,
            'previous20_average_volume': average, 'start_date': dates[0]}


def _relative(market, prepared, symbol, as_of, benchmark, instrument):
    calendar = prepared['calendar']
    benchmark_instrument = _instrument(market, benchmark)
    _, benchmark_rows, _ = _load(market, benchmark, as_of)
    benchmark_by_day = {row['date']: row for row in benchmark_rows}
    stock_by_day = {row['date']: row for row in prepared['raw']}
    end = next((day for day in reversed(calendar) if day <= as_of), None) if prepared['calendar_horizon_known'] else None
    windows = []
    for length in (20, 60):
        end_index = calendar.index(end) if end else -1
        dates = calendar[max(0, end_index - length):end_index + 1] if end_index >= 0 else []
        start = dates[0] if len(dates) == length + 1 else None
        result = {'market_sessions': length, 'start_date': start, 'end_date': end,
                  'stock_return_pct': None, 'benchmark_return_pct': None, 'excess_return_pp': None,
                  'state': 'unknown', 'reason': '', 'expected_prices': length + 1,
                  'stock_observed': sum(day in stock_by_day for day in dates),
                  'benchmark_observed': sum(day in benchmark_by_day for day in dates)}
        reasons = []
        if not prepared['calendar_horizon_known']:
            reasons.append('市场日历未覆盖观察日，不能核验相同最新端点')
        if start is None:
            reasons.append(f'没有{length + 1}个精确市场交易日端点窗口')
        for key, mapping, identity in (('stock_return_pct', stock_by_day, instrument),
                                        ('benchmark_return_pct', benchmark_by_day, benchmark_instrument)):
            observations = [mapping.get(day) for day in dates]
            name = '个股' if key == 'stock_return_pct' else '基准'
            if start is None or any(row is None for row in observations):
                reasons.append(name + '同一端点窗口报价缺失，不跳日比较')
            elif any(not _known(row, identity) for row in observations):
                reasons.append(name + '公司行动覆盖待核验，原始跨期收益未知')
            elif any(row.get('corporate_action') for row in observations[1:]):
                reasons.append(name + '窗口包含未复权公司行动，原始跨期收益不可比')
            else:
                result[key] = _pct(observations[-1]['close'], observations[0]['close'])
        if benchmark_instrument.get('kind') != 'index':
            reasons.append('基准身份不是已核验指数')
        if not reasons and all(_finite(result[key]) for key in ('stock_return_pct', 'benchmark_return_pct')):
            result.update(state='available', excess_return_pp=result['stock_return_pct'] - result['benchmark_return_pct'])
        elif not reasons:
            reasons.append('收益计算超出可计算范围，保持未知')
        result['reason'] = '；'.join(dict.fromkeys(reasons))
        windows.append(result)
    return {'benchmark': {'instrument_id': benchmark, 'name': benchmark_instrument.get('name', benchmark)},
            'windows': windows, 'unit': '百分点', 'price_basis': 'none',
            'scope': '相同市场交易日端点的价格变化差，不含持仓分红、费用或未来超额收益预测',
            'industry': {'state': 'unavailable', 'reason': '首版未核验历史行业映射，不用当前题材成员回填历史行业'}}


def _levels(item, daily, instrument, daily_bars):
    reference = daily['close'] if daily else None
    result = {'support': None, 'resistance': None, 'reference_close': reference,
              'reference_date': daily['date'] if daily else None, 'price_basis': 'none',
              'confirmation_bars': 2, 'threshold_pct': LEVEL_THRESHOLDS[item['period']],
              'state': 'unknown', 'reason': '', 'invalidation': None}
    if not daily or not item['latest']:
        return {**result, 'reason': '观察日或最近应完成周期没有有效可比报价，参考位保持未知'}
    latest = item['latest']
    bridge = [row for row in daily_bars if latest['date'] < row['date'] <= daily['date']]
    if any(row.get('corporate_action') or not _known(row, instrument) or
           row.get('missing_market_sessions_before', 0) > 0 for row in bridge):
        return {**result, 'reason': '最近完整周期与报告日之间有公司行动、缺报价或覆盖待核验，未跨连续片段比较价位'}
    prefix = [dict(row) for row in item['bars'] if row['date'] <= latest['date']]
    # Unknown corporate-action coverage is not a bridge between comparable
    # runs. Mark a copy invalid; original chart/strategy evidence stays intact.
    for row in prefix:
        if not _known(row, instrument):
            row['complete'] = False
    runs, _, _ = _continuous_segments(prefix)
    run = runs[-1] if runs and runs[-1][-1]['date'] == latest['date'] else []
    if not run:
        return {**result, 'reason': '当前连续价格片段或公司行动覆盖不足，未跨片段寻找参考位'}
    waves = candidate_waves(run, confirmation_bars=2, threshold_pct=LEVEL_THRESHOLDS[item['period']])
    active = []
    for point in waves['points']:
        if point['confirmed_at'] > latest['date']:
            continue
        subsequent = [row for row in run if row['date'] > point['confirmed_at']]
        broken = any(row['close'] > point['price'] if point['kind'] == 'high' else row['close'] < point['price'] for row in subsequent)
        if broken:
            continue
        active.append({'price': point['price'], 'formed_at': point['date'],
                       'confirmed_at': point['confirmed_at'], 'period': item['period'],
                       'segment_id': point['segment_id'], 'distance_pct': _pct(point['price'], reference),
                       'kind': point['kind']})
    supports = [point for point in active if point['kind'] == 'low' and point['price'] <= reference]
    pressures = [point for point in active if point['kind'] == 'high' and point['price'] >= reference]
    support = max(supports, key=lambda point: (point['price'], point['confirmed_at'])) if supports else None
    pressure = min(pressures, key=lambda point: (point['price'], -date.fromisoformat(point['confirmed_at']).toordinal())) if pressures else None
    result.update(support=support, resistance=pressure, state='available' if support or pressure else 'no_reference',
                  reason='' if support and pressure else '当前连续样本内缺少一侧或两侧已确认有效参考位；不臆造目标价')
    if support:
        result['invalidation'] = {'price': support['price'], 'period': item['period'],
                                  'rule': '后续完整同周期收盘严格低于支撑参考价，当前守位观察失效；等于不算跌破',
                                  'scope': '仅技术结构观察，不是企业逻辑失效、止损指令或自动交易'}
    return result


def _charts(prepared, instrument, snapshot):
    end = prepared['raw'][-1]['date'] if prepared['raw'] else None
    charts = {}
    for item in prepared['periods']:
        period = item['period']
        bars = [{key: row[key] for key in CHART_FIELDS if key in row}
                for row in item['eligible_bars'][-CHART_LIMITS[period]:]]
        warnings = list(prepared['warnings'])
        if any(not _known(row, instrument) for row in item['eligible_bars'][-CHART_LIMITS[period]:]):
            warnings.append('部分原始报价的公司行动覆盖待核验；图中仅为按已知事件计算的价格观察，相应技术结论保持未知')
        if not item['latest']:
            warnings.append(f"本图仅保留历史有效报价；最近应完成{item['label']}周期{item['expected_period']}未核验，不能把旧价当最新观察")
        if period != 'D':
            warnings.append('仅画完整可比周期；形成中、缺报价和未复权公司行动聚合柱仅在报告周期预览说明')
        charts[period] = {'instrument': dict(instrument), 'period': period, 'as_of': end,
                          'bars': bars, 'snapshot_id': snapshot, 'requested_price_basis': 'none',
                          'price_basis': 'none', 'requested_indicator_continuity': 'market_sessions',
                          'indicator_continuity': 'market_sessions', 'indicator_version': INDICATOR_VERSION + '+' + VERSION,
                          'indicator_continuity_notice': '本报告冻结未复权、严格市场交易日和完整可比周期；浏览偏好不改写报告',
                          'adjustment': {'usable': True, 'price_basis': 'none', 'anchor': end},
                          'price_basis_options': [{'value': 'none', 'label': '报告未复权价格（固定）', 'available': True}],
                          'quote_gaps': quote_gaps(bars, prepared['calendar']) if period == 'D' else [],
                          'indicator_gaps': indicator_gaps(bars), 'waves': None,
                          'volume_unit': '股', 'amount_unit': 'CNY', 'price_unit': 'CNY',
                          'warnings': warnings, 'frozen_research_chart': True,
                          'projection_limit': CHART_LIMITS[period], 'calculated_before_projection': True}
    return charts


def build_research_technical(market, symbol, as_of, *, price_basis='none', benchmark='000001.SH'):
    """Pure local report calculation; caller owns snapshot/task persistence."""
    if price_basis != 'none':
        raise ValueError('首版研究技术评估仅支持冻结未复权价格口径')
    prepared = _prepare(market, symbol, as_of)
    instrument = _instrument(market, symbol)
    daily = prepared['periods'][0]['latest']
    try:
        snapshot = market.status().get('snapshot_id')
    except AttributeError:
        snapshot = None
    periods = []
    for item in prepared['periods']:
        assessed = _assessment(item, instrument)
        assessed['volume'] = _volume(prepared, instrument) if item['period'] == 'D' else None
        assessed['levels'] = _levels(item, daily, instrument, prepared['periods'][0]['bars'])
        periods.append(assessed)
    states = {item['state'] for item in periods}
    summary = {'conflict': 'strong' in states and 'weak' in states, 'has_unknown': 'unknown' in states,
               'text': '；'.join(item['label'] + item['state_label'] + '（' + (item['observed_date'] or '最近应完成周期未核验') + '）' for item in periods),
               'scope': '各周期独立条件状态，不投票合成上涨概率，阈值未经收益有效性验证'}
    relative = _relative(market, prepared, symbol, as_of, benchmark, instrument)
    contract = {'version': VERSION, 'price_basis': 'none', 'adjustment_anchor': None,
                'indicator_continuity': 'market_sessions', 'period_policy': 'complete_comparable_periods',
                'requested_as_of': as_of, 'data_cutoff': prepared['raw'][-1]['date'] if prepared['raw'] else None,
                'snapshot_id': snapshot, 'indicator_version': INDICATOR_VERSION,
                'chart_max_bars': dict(CHART_LIMITS), 'calculated_before_projection': True,
                'benchmark_instrument_id': benchmark}
    space = {'technical': {'state': 'available' if any(item['levels']['state'] == 'available' for item in periods) else 'unknown',
                            'label': '到已确认技术参考位的价格距离',
                            'formula': '(参考价 / 报告观察日同口径日收盘价 - 1) × 100%',
                            'reason': '按各周期levels分别列示；距离不是预计收益或上涨概率'},
             'institutional_target': {'state': 'unavailable', 'label': '机构目标价距离',
                                      'reason': '技术计算不推断机构目标价；需独立研报原文、日期、期限和价格口径证据'},
             'valuation_scenarios': {'state': 'not_calculated', 'label': '盈利预测估值情景',
                                     'reason': '未设置经核验盈利预测与估值假设，不由技术压力或当前PE补算'}}
    return _clean({'version': VERSION, 'as_of': as_of, 'anchor_date': prepared['periods'][0]['expected_date'],
                   'price_basis': 'none', 'indicator_continuity': 'market_sessions',
                   'parameters': {'trend': {key: dict(value) for key, value in TREND_PARAMETERS.items()},
                                  'level_confirmation_bars': 2, 'level_threshold_pct': dict(LEVEL_THRESHOLDS),
                                  'volume_previous_market_sessions': 20, 'relative_market_sessions': [20, 60]},
                   'analysis_contract': contract, 'periods': periods, 'relative_strength': relative,
                   'space_assessment': space, 'summary': summary, 'warnings': prepared['warnings'],
                   'frozen_charts_by_period': _charts(prepared, instrument, snapshot)})
