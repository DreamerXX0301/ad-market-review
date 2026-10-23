"""Strict, causal two-bottom structure summaries for strategy D/W/M inputs.

This deliberately does not reuse display-wave merging. Every market session
must have one valid traded quote; missing periods are retained as invalid slots.
Only two confirmed local lows and a bounded validity count enter the summary.
"""
from collections import defaultdict, deque
from datetime import date, timedelta
from math import isfinite

from .period_dates import latest_closed_date

VERSION = 'higher-lows-v1'
FIELD = 'higher_lows'
STATE = 'higher_lows_state'
OPERATOR = 'confirmed_rising_lows'
MIN_WINDOW = 8
MAX_WINDOW = 750
NEIGHBORS = 2


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def _valid(row):
    return (row and all(_positive(row.get(key)) for key in ('open', 'high', 'low', 'close', 'volume'))
            and row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']
            and row.get('_higher_lows_action_coverage_known',row.get('corporate_action_coverage_known')) is True
            and row.get('corporate_action') is False)


def _key(day, period):
    return date.fromisoformat(day).isocalendar()[:2] if period == 'W' else day[:7] if period == 'M' else day


def _period_rows(rows, calendar, as_of, period):
    """Calendar-defined slots, including entirely missing weeks/months."""
    sessions = sorted(set(calendar))
    closed = latest_closed_date(sessions, as_of, period)
    if closed is None:
        return []
    by_date = defaultdict(list)
    for row in rows:
        day = row.get('date')
        if isinstance(day, str) and day <= closed:
            by_date[day].append(row)
    groups = defaultdict(list)
    for day in sessions:
        if day <= closed:
            groups[_key(day, period)].append(day)
    session_set = set(sessions)
    unexpected = {_key(day, period) for day in by_date if day not in session_set}
    result = []
    for key, days in groups.items():
        values = [by_date[day][0] for day in days if len(by_date[day]) == 1]
        complete = len(values) == len(days) and all(_valid(row) for row in values) and key not in unexpected
        boundary_known = True
        if period != 'D':
            first = date.fromisoformat(days[0])
            boundary = (first-timedelta(days=first.weekday())).isoformat() if period == 'W' else days[0][:7]+'-01'
            boundary_known = sessions[0] <= boundary
            complete = complete and boundary_known
        blocker = None
        if not complete:
            missing = [day for day in days if not by_date[day]]
            duplicates = [day for day in days if len(by_date[day]) > 1]
            uncovered = [row['date'] for row in values
                         if row.get('_higher_lows_action_coverage_known',row.get('corporate_action_coverage_known')) is not True
                         or not isinstance(row.get('corporate_action'),bool)]
            actions = [row['date'] for row in values if row.get('corporate_action') is True]
            invalid = [row['date'] for row in values if not _valid(row)]
            kind,label,affected = ('missing_quotes','缺少市场交易日报价',missing) if missing else (
                'duplicate_quotes','存在重复市场交易日报价',duplicates) if duplicates else (
                'unknown_action_coverage','公司行动覆盖未核验',uncovered) if uncovered else (
                'corporate_action','存在未复权公司行动，原价不可比',actions) if actions else (
                'invalid_traded_quote','存在无效OHLC或缺失/零成交量报价',invalid) if invalid else (
                'calendar_conflict','报价日期与已留存市场日历冲突',[]) if key in unexpected else (
                'incomplete_period_boundary','首个周期日历边界未完整核验',[])
            blocker={'period_end':days[-1], 'kind':kind, 'reason':label,
                     'market_dates':affected[:3], 'affected_market_days':len(affected)}
        result.append({'date': days[-1], 'low': min(row['low'] for row in values) if complete else None,
                       'close': values[-1]['close'] if complete else None, 'valid': bool(complete),
                       'blocker':blocker})
    return result


def _states(rows, calendar, as_of, period):
    if period not in ('D', 'W', 'M'):
        raise ValueError('底部抬高仅支持日/完整周/完整月')
    recent = deque(maxlen=5)
    lows = deque(maxlen=2)
    covered = 0
    minimum = None
    blocker = None
    for position, row in enumerate(_period_rows(rows, calendar, as_of, period)):
        if not row['valid']:
            blocker = row['blocker']
            covered = 0
            recent.clear()
            lows.clear()
            minimum = None
        else:
            covered = min(MAX_WINDOW+NEIGHBORS, covered+1)
            recent.append((position, row))
            if len(recent) == 5:
                center_position, center = recent[2]
                if all(center['low'] < other['low'] for offset, (_, other) in enumerate(recent) if offset != 2):
                    lows.append({'date': center['date'], 'confirmed_on': row['date'], 'price': center['low'],
                                 '_position': center_position})
                    minimum = row['close']
            if lows:
                minimum = min(minimum, row['close']) if minimum is not None else row['close']
        yield row['date'], {'version': VERSION, 'period': period, 'observed_date': row['date'],
            'covered_periods': covered, 'available_periods': position+1,
            'recent_lows': [{**{key: value for key, value in low.items() if key != '_position'},
                             'age_periods': position-low['_position']} for low in lows],
            'minimum_close_since_l2_confirmation': minimum,
            'last_blocker':blocker,
            'reason': None if row['valid'] else blocker['reason']}


def summaries(rows, calendar, as_of, period='D'):
    """One pass yields small summaries at each closed period, never future prices."""
    return dict(_states(rows,calendar,as_of,period))


def latest(rows, calendar, as_of, period='D'):
    value={'version': VERSION, 'period': period, 'observed_date': None,
        'covered_periods': 0, 'available_periods': 0, 'recent_lows': [],
        'minimum_close_since_l2_confirmation': None, 'last_blocker':None,
        'reason': '尚无已完整结束并可核验的市场周期'}
    for _,value in _states(rows,calendar,as_of,period):pass
    return value


def evaluate(node, metrics):
    """Window contains pivot centers; its first center needs two earlier bars."""
    window = node['value']
    state = metrics.get(STATE) or {}
    covered = state.get('covered_periods')
    valid = (state.get('version') == VERSION and isinstance(covered, int) and not isinstance(covered, bool)
             and covered >= window+NEIGHBORS)
    lows = [low for low in state.get('recent_lows', []) if isinstance(low, dict)
            and isinstance(low.get('age_periods'), int) and 2 <= low['age_periods'] < window]
    minimum = state.get('minimum_close_since_l2_confirmation')
    result = None
    reason = state.get('reason') or '回看窗口及左侧2根预热报价、周期完整性或价格可比性不足'
    blocker = state.get('last_blocker')
    required = window+NEIGHBORS
    if not valid and isinstance(covered,int) and not isinstance(covered,bool):
        prefix=(blocker['period_end']+' '+blocker['reason']+'；') if isinstance(blocker,dict) else ''
        reason=prefix+f'目前连续{covered}根可比完整周期，{window}根窗口加左侧2根预热需{required}根'
        available=state.get('available_periods')
        if isinstance(available,int) and available<required:reason+=f'；当前仅留存{available}个所选周期'
    if valid:
        if len(lows) < 2:
            result = False
            reason = '完整窗口内不足两个已确认严格低点'
        elif not all(_positive(low.get('price')) for low in lows) or not _positive(minimum):
            reason = '低点或确认后收盘证据无效，保持未知'
        else:
            rising = lows[-1]['price'] > lows[-2]['price']
            held = minimum >= lows[-1]['price']
            result = rising and held
            reason = None if result else '最近两低点未严格抬高' if not rising else '最近低点确认后曾收盘跌破，收回后本次结构仍失效'
    return {**node, 'actual': result, 'right_actual': window, 'result': result,
            'lookback_periods': window, 'structure_version': VERSION, 'left_bars': 2, 'right_bars': 2,
            'covered_periods': covered if isinstance(covered, int) and not isinstance(covered, bool) else None,
            'required_periods':required,
            'observed_date': state.get('observed_date'), 'confirmation_date': lows[-1]['confirmed_on'] if len(lows) == 2 else None,
            'evidence': {'recent_lows': lows, 'minimum_close_since_l2_confirmation': minimum,
                         'window_includes': 'pivot_centers', 'left_warmup_periods': 2,
                         'last_blocker':blocker, 'required_periods':required,
                         'hold_from': 'l2_confirmation_inclusive', 'price_basis': 'none'},
            'reason': reason}
