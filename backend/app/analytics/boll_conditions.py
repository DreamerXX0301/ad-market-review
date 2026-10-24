"""Causal, configurable BOLL rule summaries over strict market periods.

Only compact proofs are retained. Missing/invalid traded sessions and unverified
corporate actions reset the series; display chart continuity is never reused.
"""
from bisect import bisect_left, insort
from collections import defaultdict, deque
from datetime import date, timedelta
from hashlib import sha256
from math import fsum, isclose, isfinite, sqrt
import json

from .period_dates import latest_closed_date

VERSION = 'boll-signal-v1'
FIELD = 'boll_signal'
STATE = 'boll_states'
OPERATOR = 'matches'
DEFAULTS = {'mode': 'breakout_up', 'band_period': 20, 'deviations': 2.0,
    'rank_window': 125, 'squeeze_percentile': 10.0, 'recent_squeeze_periods': 5,
    'require_squeeze': True, 'middle_direction': 'any', 'slope_periods': 5,
    'require_expansion': False}
MODES = ('squeeze', 'breakout_up', 'breakout_down', 'reentry_from_above', 'reentry_from_below')
COMPARISON_TOLERANCE = {'relative': 1e-12, 'absolute': 1e-12}


def field_spec():
    return {'type': 'boll_config', 'operators': [OPERATOR], 'constant_only': True,
        'parameter_kind': FIELD, 'signal_version': VERSION, 'default': dict(DEFAULTS),
        'parameters': {
            'mode': {'type': 'enum', 'choices': list(MODES)},
            'band_period': {'type': 'integer', 'min': 2, 'max': 250},
            'deviations': {'type': 'number', 'min': .1, 'max': 10},
            'rank_window': {'type': 'integer', 'min': 10, 'max': 750},
            'squeeze_percentile': {'type': 'number', 'min': 1, 'max': 100},
            'recent_squeeze_periods': {'type': 'integer', 'min': 1, 'max': 60},
            'require_squeeze': {'type': 'boolean'},
            'middle_direction': {'type': 'enum', 'choices': ['any', 'rising', 'falling']},
            'slope_periods': {'type': 'integer', 'min': 1, 'max': 60},
            'require_expansion': {'type': 'boolean'}}}


def validate_config(value):
    """Accept only bounded JSON data, filling omitted parameter defaults."""
    if not isinstance(value, dict) or set(value)-set(DEFAULTS):
        raise ValueError('BOLL参数需要对象且只能包含白名单参数')
    result = {**DEFAULTS, **value}
    if result['mode'] not in MODES or not isinstance(result['mode'], str):
        raise ValueError('BOLL信号仅支持收口、向上/向下首次突破、从上/下方回到带内')
    if result['middle_direction'] not in ('any', 'rising', 'falling'):
        raise ValueError('BOLL中轨方向仅支持不限、向上或向下')
    for key, low, high in (('band_period', 2, 250), ('rank_window', 10, 750),
                          ('recent_squeeze_periods', 1, 60), ('slope_periods', 1, 60)):
        if not isinstance(result[key], int) or isinstance(result[key], bool) or not low <= result[key] <= high:
            raise ValueError(f'BOLL {key}需要{low}至{high}的整数')
    for key, low, high in (('deviations', .1, 10), ('squeeze_percentile', 1, 100)):
        item = result[key]
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not isfinite(item) or not low <= item <= high:
            raise ValueError(f'BOLL {key}需要{low}至{high}的有限数值')
        result[key] = float(item)
    for key in ('require_squeeze', 'require_expansion'):
        if not isinstance(result[key], bool):
            raise ValueError('BOLL收口和扩张开关需要是/否')
    return result


def config_key(config):
    canonical = validate_config(config)
    return sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()[:24]


def configs_for(trees, default_period='D'):
    """Deduplicate exact configurations; event today clauses always use D."""
    if default_period not in ('D', 'W', 'M'):
        raise ValueError('BOLL仅支持日/完整周/完整月')
    result = {period: {} for period in ('D', 'W', 'M')}

    def visit(node, forced=None):
        if not isinstance(node, dict):
            return
        if 'history_event' in node:
            visit(node['history_event'].get('then', {}), 'D')
        elif node.get('field') == FIELD:
            config = validate_config(node.get('value'))
            period = forced or node.get('period', default_period)
            if period not in result:
                raise ValueError('BOLL仅支持日/完整周/完整月')
            result[period][config_key(config)] = config
        else:
            for child in node.get('all', node.get('any', node.get('conditions', []))):
                visit(child, forced)

    for tree in [trees] if isinstance(trees, dict) else trees:
        visit(tree)
    return {period: list(values.values()) for period, values in result.items()}


def warmup_for(config):
    config = validate_config(config)
    n = config['band_period']
    required = n+config['rank_window'] if config['mode'] == 'squeeze' else n+1
    if config['mode'] != 'squeeze' and config['require_squeeze']:
        required = max(required, n+config['rank_window']+config['recent_squeeze_periods'])
    if config['middle_direction'] != 'any':
        required = max(required, n+config['slope_periods'])
    if config['require_expansion']:
        required = max(required, n+1)
    return required


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def _covered(row):
    return (row.get('_boll_action_coverage_known', True) is True
            and row.get('corporate_action_coverage_known') is True
            and isinstance(row.get('corporate_action'), bool))


def _valid(row):
    return (all(_positive(row.get(key)) for key in ('open', 'high', 'low', 'close', 'volume'))
            and row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']
            and _covered(row) and row['corporate_action'] is False)


def _period_key(day, period):
    return date.fromisoformat(day).isocalendar()[:2] if period == 'W' else day[:7] if period == 'M' else day


def _period_rows(rows, calendar, as_of, period):
    """Keep missing calendar slots, including wholly absent weeks/months."""
    sessions = sorted(set(calendar))
    closed = latest_closed_date(sessions, as_of, period)
    if closed is None:
        return []
    by_day = defaultdict(list)
    for row in rows:
        day = row.get('date')
        try:
            valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
        except ValueError:
            valid_day = False
        if valid_day and day <= closed:
            by_day[day].append(row)
    groups = defaultdict(list)
    for day in sessions:
        if day <= closed:
            groups[_period_key(day, period)].append(day)
    session_set = set(sessions)
    unexpected = {_period_key(day, period) for day in by_day if day not in session_set}
    result = []
    for key, days in groups.items():
        values = [by_day[day][0] for day in days if len(by_day[day]) == 1]
        missing = [day for day in days if not by_day[day]]
        duplicates = [day for day in days if len(by_day[day]) > 1]
        uncovered = [row['date'] for row in values if not _covered(row)]
        actions = [row['date'] for row in values if row.get('corporate_action') is True]
        invalid = [row['date'] for row in values if not _valid(row)]
        boundary_known = True
        if period != 'D':
            first = date.fromisoformat(days[0])
            boundary = (first-timedelta(days=first.weekday())).isoformat() if period == 'W' else days[0][:7]+'-01'
            boundary_known = sessions[0] <= boundary
        complete = not (missing or duplicates or invalid or key in unexpected) and boundary_known
        blocker = None
        if not complete:
            kind, reason, affected = ('missing_quotes', '缺少市场交易日报价', missing) if missing else (
                'duplicate_quotes', '存在重复市场交易日报价', duplicates) if duplicates else (
                'unknown_action_coverage', '公司行动覆盖未核验', uncovered) if uncovered else (
                'corporate_action', '存在未复权公司行动，原价不可比', actions) if actions else (
                'invalid_traded_quote', '存在无效OHLC或缺失/零成交量报价', invalid) if invalid else (
                'calendar_conflict', '报价日期与已留存市场日历冲突', []) if key in unexpected else (
                'incomplete_period_boundary', '首个周期日历边界未完整核验', [])
            blocker = {'period_end': days[-1], 'kind': kind, 'reason': reason,
                       'market_dates': affected[:3], 'affected_market_days': len(affected)}
        result.append({'date': days[-1], 'close': float(values[-1]['close']) if complete else None,
                       'valid': complete, 'blocker': blocker})
    return result


def _all(values):
    return False if False in values else None if None in values else True


def _greater(left, right):
    # Reversible variance can accumulate sub-tick rounding. A theoretical band
    # touch must remain equality instead of creating a false first crossing.
    return left > right and not isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def _quantile(ordered, percentile):
    pos = (len(ordered)-1)*percentile/100
    lower = int(pos); upper = min(lower+1, len(ordered)-1)
    return ordered[lower]+(ordered[upper]-ordered[lower])*(pos-lower)


class _RollingBands:
    """Bounded O(1) rolling population variance with reversible Welford sums."""
    def __init__(self, window):
        self.window = window
        self.values = deque()
        self.mean = self.m2 = 0.0
        self.anchor = None
        self.updates = 0

    def add(self, value, deviations):
        if self.anchor is None:
            self.anchor = value
        count = len(self.values)
        if count == self.window:
            old = self.values.popleft()-self.anchor
            next_mean = self.mean+(self.mean-old)/(count-1)
            self.m2 -= (old-self.mean)*(old-next_mean)
            self.mean = next_mean
            count -= 1
        self.values.append(value)
        centered = value-self.anchor
        delta = centered-self.mean
        self.mean += delta/(count+1)
        self.m2 += delta*(centered-self.mean)
        self.updates += 1
        if len(self.values) != self.window:
            return None
        # Rebase once per full window: O(N) every N updates is amortized O(1),
        # limits reversible-update drift and retains tiny variance near a
        # large price level. Work with offsets rather than absolute prices.
        if self.m2 < 0 or self.updates % self.window == 0:
            self.anchor = value
            offsets = [item-self.anchor for item in self.values]
            self.mean = fsum(item/self.window for item in offsets)
            self.m2 = fsum((item-self.mean)*(item-self.mean) for item in offsets)
        spread = deviations*sqrt(self.m2/self.window)
        middle = self.anchor+self.mean
        if not isfinite(middle) or middle <= 0:
            return None
        upper, lower = middle+spread, middle-spread
        width = (upper-lower)/middle
        if not all(isfinite(item) for item in (middle, upper, lower, width)):
            return None
        return {'middle': middle, 'upper': upper, 'lower': lower, 'bandwidth': width}


def _empty_state(config, period, day=None, *, required_periods=None):
    return {'version': VERSION, 'config': config, 'period': period, 'observed_date': day,
        'covered_periods': 0, 'required_periods': warmup_for(config) if required_periods is None else required_periods, 'result': None,
        'reason': '尚无已完整结束并可核验的市场周期',
        'evidence': {'period': period, 'price_basis': 'none', 'last_blocker': None}}


def _states(period_rows, period, config, *, latest_only=False):
    required_periods = warmup_for(config)
    bands = _RollingBands(config['band_period'])
    width_history = deque(); ordered = []
    recent = deque(maxlen=config['recent_squeeze_periods'])
    middles = deque(maxlen=config['slope_periods']+1)
    previous = None; covered = 0; blocker = None
    for position, row in enumerate(period_rows):
        emit = not latest_only or position+1 == len(period_rows)
        state = _empty_state(config, period, row['date'], required_periods=required_periods) if emit else None
        if not row['valid']:
            bands = _RollingBands(config['band_period'])
            width_history.clear(); ordered.clear(); recent.clear(); middles.clear()
            previous = None; covered = 0; blocker = row['blocker']
            if emit:
                state['reason'] = blocker['reason']
                state['evidence']['last_blocker'] = blocker
                yield row['date'], state
            continue
        covered += 1
        current = bands.add(row['close'], config['deviations'])
        if current is None and len(bands.values) == config['band_period']:
            blocker = {'period_end': row['date'], 'kind': 'nonfinite_boll_calculation',
                       'reason': '价格尺度超出BOLL有限数值计算范围，重新预热',
                       'market_dates': [row['date']], 'affected_market_days': 1}
            bands = _RollingBands(config['band_period'])
            width_history.clear(); ordered.clear(); recent.clear(); middles.clear()
            previous = None; covered = 0
            if emit:
                state['reason'] = blocker['reason']
                state['evidence']['last_blocker'] = blocker
                yield row['date'], state
            continue
        if not emit:
            # Latest-only selection still advances every original band/rank
            # recurrence. Historical proof dictionaries are the only omitted
            # work; the last state's counts, blockers and floating values are
            # identical to the full dated history used by backtests.
            if current is not None:
                squeeze = None
                if len(ordered) == config['rank_window']:
                    squeeze = not _greater(current['bandwidth'], _quantile(ordered, config['squeeze_percentile']))
                middles.append(current['middle'])
                recent.append({'date': row['date'], 'squeeze': squeeze})
                width_history.append(current['bandwidth']); insort(ordered, current['bandwidth'])
                if len(width_history) > config['rank_window']:
                    old = width_history.popleft(); ordered.pop(bisect_left(ordered, old))
                previous = {**current, 'close': row['close']}
            continue
        state['covered_periods'] = covered
        proof = {'period': period, 'price_basis': 'none', 'last_blocker': blocker,
                 'close': row['close'], 'upper': None, 'middle': None, 'lower': None,
                 'bandwidth_pct': None, 'historical_threshold_pct': None,
                 'historical_width_count': len(ordered), 'squeeze': None,
                 'recent_squeeze': None, 'recent_squeeze_date': None,
                 'previous_close': previous['close'] if previous else None,
                 'previous_upper': previous['upper'] if previous else None,
                 'previous_lower': previous['lower'] if previous else None,
                 'middle_reference': None, 'middle_change_pct': None, 'expanding': None,
                 'primary_match': None, 'middle_match': None,
                 'expansion_match': None, 'squeeze_match': None,
                 'comparison_tolerance': dict(COMPARISON_TOLERANCE)}
        primary = None; middle_match = True if config['middle_direction'] == 'any' else None
        expansion_match = True if not config['require_expansion'] else None
        squeeze_match = True if config['mode'] == 'squeeze' or not config['require_squeeze'] else None
        if current is not None:
            proof.update({key: current[key] for key in ('middle', 'upper', 'lower')})
            proof['bandwidth_pct'] = current['bandwidth']*100
            squeeze = None
            if len(ordered) == config['rank_window']:
                threshold = _quantile(ordered, config['squeeze_percentile'])
                squeeze = not _greater(current['bandwidth'], threshold)
                proof.update(historical_threshold_pct=threshold*100, squeeze=squeeze)
            matches = [item for item in recent if item['squeeze'] is True]
            recent_match = True if matches else None if len(recent) < config['recent_squeeze_periods'] or any(item['squeeze'] is None for item in recent) else False
            proof.update(recent_squeeze=recent_match,
                         recent_squeeze_date=matches[-1]['date'] if matches else None)
            if config['mode'] == 'squeeze':
                primary = squeeze
            elif previous is not None:
                close = row['close']; old_close = previous['close']
                mode = config['mode']
                inside = not _greater(current['lower'], close) and not _greater(close, current['upper'])
                primary = (_greater(close, current['upper']) and not _greater(old_close, previous['upper'])) if mode == 'breakout_up' else (
                    _greater(current['lower'], close) and not _greater(previous['lower'], old_close)) if mode == 'breakout_down' else (
                    inside and _greater(old_close, previous['upper'])) if mode == 'reentry_from_above' else (
                    inside and _greater(previous['lower'], old_close))
            if config['mode'] != 'squeeze' and config['require_squeeze']:
                squeeze_match = recent_match
            middles.append(current['middle'])
            if len(middles) == config['slope_periods']+1:
                reference = middles[0]
                proof.update(middle_reference=reference, middle_change_pct=(current['middle']/reference-1)*100)
                if config['middle_direction'] != 'any':
                    middle_match = _greater(current['middle'], reference) if config['middle_direction'] == 'rising' else _greater(reference, current['middle'])
            if previous is not None:
                proof['expanding'] = _greater(current['bandwidth'], previous['bandwidth'])
                if config['require_expansion']:
                    expansion_match = proof['expanding']
            # Threshold and recent setup are evaluated before today's values
            # enter either rolling history: a breakout cannot create its setup.
            recent.append({'date': row['date'], 'squeeze': squeeze})
            width_history.append(current['bandwidth']); insort(ordered, current['bandwidth'])
            if len(width_history) > config['rank_window']:
                old = width_history.popleft(); ordered.pop(bisect_left(ordered, old))
            previous = {**current, 'close': row['close']}
        proof.update(primary_match=primary, middle_match=middle_match,
                     expansion_match=expansion_match, squeeze_match=squeeze_match)
        state['result'] = _all([primary, middle_match, expansion_match, squeeze_match])
        state['evidence'] = proof
        if state['result'] is None:
            prefix = blocker['period_end']+' '+blocker['reason']+'；' if blocker else ''
            state['reason'] = prefix+f'连续{covered}根可比完整周期；BOLL信号所需带宽、历史收口或可选过滤尚未预热（完整预热最多需{state["required_periods"]}根）'
        elif state['result']:
            state['reason'] = None
        else:
            state['reason'] = '当期未满足所选BOLL事件' if primary is False else '中轨方向不满足' if middle_match is False else '带宽未严格扩张' if expansion_match is False else '此前指定周期内未出现收口'
        yield row['date'], state


def states_for_bars(daily_rows, calendar, as_of, period='D', configs=None):
    if period not in ('D', 'W', 'M'):
        raise ValueError('BOLL仅支持日/完整周/完整月')
    canonical = {config_key(config): validate_config(config) for config in (configs or [])}
    if not canonical:
        return {}
    rows = _period_rows(daily_rows, calendar, as_of, period)
    result = {row['date']: {} for row in rows}
    for key, config in canonical.items():
        for day, state in _states(rows, period, config):
            result[day][key] = state
    return result


def latest_states(daily_rows, calendar, as_of, period='D', configs=None):
    if period not in ('D', 'W', 'M'):
        raise ValueError('BOLL仅支持日/完整周/完整月')
    rows = _period_rows(daily_rows, calendar, as_of, period)
    result = {}
    for source in configs or []:
        config = validate_config(source); key = config_key(config)
        state = _empty_state(config, period)
        for _, state in _states(rows, period, config, latest_only=True):
            pass
        result[key] = state
    return result


def evaluate(node, metrics):
    config = validate_config(node['value'])
    state = (metrics.get(STATE) or {}).get(config_key(config)) or {}
    valid = state.get('version') == VERSION and state.get('config') == config
    actual = state.get('result') if valid and (isinstance(state.get('result'), bool) or state.get('result') is None) else None
    return {**node, 'value': config, 'actual': actual, 'right_actual': config,
        'result': actual, 'signal_version': VERSION,
        'observed_date': state.get('observed_date') if valid else None,
        'covered_periods': state.get('covered_periods') if valid else None,
        'required_periods': warmup_for(config),
        'evidence': state.get('evidence') if valid else {'price_basis': 'none'},
        'reason': state.get('reason') if valid else '缺少当前参数与固定行情代际对应的严格BOLL信号摘要，保持未知'}
