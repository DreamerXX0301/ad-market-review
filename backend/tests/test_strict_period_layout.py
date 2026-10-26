"""Calendar reuse must preserve the pre-optimization structural proofs exactly.

The two small legacy input adapters below are frozen oracles for this reuse
change. Their quote-validity policies deliberately remain different.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from math import sin

import pytest

from app.analytics import boll_conditions as boll, higher_lows as hl
from app.analytics import strict_period_layout as layout
from app.analytics.period_dates import latest_closed_date


def _legacy_hl(rows, calendar, as_of, period):
    sessions = sorted(set(calendar))
    closed = latest_closed_date(sessions, as_of, period)
    if closed is None: return []
    by_date = defaultdict(list)
    for row in rows:
        day = row.get('date')
        if isinstance(day, str) and day <= closed: by_date[day].append(row)
    groups = defaultdict(list)
    for day in sessions:
        if day <= closed: groups[hl._key(day, period)].append(day)
    unexpected = {hl._key(day, period) for day in by_date if day not in set(sessions)}
    result = []
    for key, days in groups.items():
        values = [by_date[day][0] for day in days if len(by_date[day]) == 1]
        complete = len(values) == len(days) and all(hl._valid(row) for row in values) and key not in unexpected
        if period != 'D':
            first = date.fromisoformat(days[0])
            boundary = (first-timedelta(days=first.weekday())).isoformat() if period == 'W' else days[0][:7]+'-01'
            complete = complete and sessions[0] <= boundary
        blocker = None
        if not complete:
            missing = [day for day in days if not by_date[day]]
            duplicates = [day for day in days if len(by_date[day]) > 1]
            uncovered = [row['date'] for row in values if row.get('_higher_lows_action_coverage_known', row.get('corporate_action_coverage_known')) is not True or not isinstance(row.get('corporate_action'), bool)]
            actions = [row['date'] for row in values if row.get('corporate_action') is True]
            invalid = [row['date'] for row in values if not hl._valid(row)]
            kind, reason, affected = ('missing_quotes', '缺少市场交易日报价', missing) if missing else (
                'duplicate_quotes', '存在重复市场交易日报价', duplicates) if duplicates else (
                'unknown_action_coverage', '公司行动覆盖未核验', uncovered) if uncovered else (
                'corporate_action', '存在未复权公司行动，原价不可比', actions) if actions else (
                'invalid_traded_quote', '存在无效OHLC或缺失/零成交量报价', invalid) if invalid else (
                'calendar_conflict', '报价日期与已留存市场日历冲突', []) if key in unexpected else (
                'incomplete_period_boundary', '首个周期日历边界未完整核验', [])
            blocker = {'period_end': days[-1], 'kind': kind, 'reason': reason, 'market_dates': affected[:3], 'affected_market_days': len(affected)}
        result.append({'date': days[-1], 'low': min(row['low'] for row in values) if complete else None,
                       'close': values[-1]['close'] if complete else None, 'valid': bool(complete), 'blocker': blocker})
    return result


def _legacy_boll(rows, calendar, as_of, period):
    sessions = sorted(set(calendar))
    closed = latest_closed_date(sessions, as_of, period)
    if closed is None: return []
    by_day = defaultdict(list)
    for row in rows:
        day = row.get('date')
        try: valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
        except ValueError: valid_day = False
        if valid_day and day <= closed: by_day[day].append(row)
    groups = defaultdict(list)
    for day in sessions:
        if day <= closed: groups[boll._period_key(day, period)].append(day)
    unexpected = {boll._period_key(day, period) for day in by_day if day not in set(sessions)}
    result = []
    for key, days in groups.items():
        values = [by_day[day][0] for day in days if len(by_day[day]) == 1]
        missing = [day for day in days if not by_day[day]]
        duplicates = [day for day in days if len(by_day[day]) > 1]
        uncovered = [row['date'] for row in values if not boll._covered(row)]
        actions = [row['date'] for row in values if row.get('corporate_action') is True]
        invalid = [row['date'] for row in values if not boll._valid(row)]
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
            blocker = {'period_end': days[-1], 'kind': kind, 'reason': reason, 'market_dates': affected[:3], 'affected_market_days': len(affected)}
        result.append({'date': days[-1], 'close': float(values[-1]['close']) if complete else None,
                       'valid': complete, 'blocker': blocker})
    return result


def calendar(count=290, start='2025-01-06'):
    result = []; day = date.fromisoformat(start)
    while len(result) < count:
        if day.weekday() < 5: result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def quotes(days):
    return [{'date': day, 'open': 30+sin(i/3), 'close': 30+sin(i/3), 'low': 29+sin(i/3),
             'high': 31+sin(i/3), 'volume': 1000, 'corporate_action': False,
             'corporate_action_coverage_known': True} for i, day in enumerate(days)]


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('damage', ['none', 'missing', 'duplicate', 'zero', 'nan', 'action', 'uncovered',
                                   'missing_action_flag', 'hl_only', 'boll_only', 'mixed_overrides',
                                   'whole_period_missing', 'weekend', 'noncanonical_alias'])
def test_exact_legacy_period_rows_and_all_output_states(period, damage, monkeypatch):
    days = calendar(); rows = quotes(days)
    if damage == 'missing': del rows[24]
    if damage == 'duplicate': rows.append(dict(rows[24]))
    if damage == 'zero': rows[24]['volume'] = 0
    if damage == 'nan': rows[24]['high'] = float('nan')
    if damage == 'action': rows[24]['corporate_action'] = True
    if damage == 'uncovered': rows[24]['corporate_action_coverage_known'] = False
    if damage == 'missing_action_flag': del rows[24]['corporate_action']
    if damage == 'hl_only': rows[24]['_higher_lows_action_coverage_known'] = False
    if damage == 'boll_only': rows[24]['_boll_action_coverage_known'] = False
    if damage == 'mixed_overrides': rows[24].update(_higher_lows_action_coverage_known=True, corporate_action_coverage_known=False)
    if damage == 'whole_period_missing': rows = [row for row in rows if row['date'][:7] != '2025-03']
    if damage == 'weekend': rows.append({**rows[0], 'date': '2025-01-11'})
    if damage == 'noncanonical_alias': rows.append({**rows[0], 'date': '20250106'})
    # Future rows cannot alter a frozen observed-day proof. The calendar itself
    # stays complete; following sessions are legitimate closure evidence.
    rows.append({**rows[-1], 'date': '2099-01-01'})
    for as_of in (days[0], days[42], days[-4], days[-1]):
        expected_hl = _legacy_hl(rows, days, as_of, period)
        expected_boll = _legacy_boll(rows, days, as_of, period)
        assert hl._period_rows(rows, days, as_of, period) == expected_hl
        assert boll._period_rows(rows, days, as_of, period) == expected_boll
    as_of = days[-1]
    with monkeypatch.context() as patch:
        patch.setattr(hl, '_period_rows', _legacy_hl)
        expected_hl_summaries = hl.summaries(rows, days, as_of, period)
        expected_hl_latest = hl.latest(rows, days, as_of, period)
    assert hl.summaries(rows, days, as_of, period) == expected_hl_summaries
    assert hl.latest(rows, days, as_of, period) == expected_hl_latest
    configs = [boll.validate_config({'band_period': 3, 'rank_window': 10}),
               boll.validate_config({'mode': 'squeeze', 'band_period': 2, 'rank_window': 10}),
               boll.validate_config({'band_period': 4, 'rank_window': 11, 'middle_direction': 'rising',
                                     'require_expansion': True, 'slope_periods': 2})]
    with monkeypatch.context() as patch:
        patch.setattr(boll, '_period_rows', _legacy_boll)
        expected_boll_summaries = boll.states_for_bars(rows, days, as_of, period, configs)
        expected_boll_latest = boll.latest_states(rows, days, as_of, period, configs)
    assert boll.states_for_bars(rows, days, as_of, period, configs) == expected_boll_summaries
    assert boll.latest_states(rows, days, as_of, period, configs) == expected_boll_latest


def test_consumers_keep_distinct_validity_and_noncanonical_policies():
    days = calendar(); rows = quotes(days)
    rows[0].update(_higher_lows_action_coverage_known=True, corporate_action_coverage_known=False)
    assert hl._period_rows(rows, days, days[-1], 'D')[0]['valid'] is True
    assert boll._period_rows(rows, days, days[-1], 'D')[0]['blocker']['kind'] == 'unknown_action_coverage'
    rows = quotes(days); rows.append({**rows[0], 'date': '20250106'})
    assert hl._period_rows(rows, days, days[-1], 'W')[0]['blocker']['kind'] == 'calendar_conflict'
    assert boll._period_rows(rows, days, days[-1], 'W')[0]['valid'] is True
    rows.append({**rows[0], 'date': '2025-01-06x'})
    with pytest.raises(ValueError): hl._period_rows(rows, days, days[-1], 'W')
    assert boll._period_rows(rows, days, days[-1], 'W')[0]['valid'] is True


@pytest.mark.parametrize('period,observed,next_session', [('W', '2025-01-08', '2025-01-13'),
                                                       ('M', '2025-01-30', '2025-02-03')])
def test_complete_future_calendar_is_in_cache_key(period, observed, next_session):
    layout.clear_period_layout_cache()
    earlier = ['2025-01-06', '2025-01-07', observed]
    without = layout.period_layout(earlier, observed, period)
    with_following = layout.period_layout([*earlier, next_session], observed, period)
    assert without.closed is None and with_following.closed == observed
    assert layout.period_layout(earlier, observed, period) is without
    assert layout.period_layout([*earlier, next_session], observed, period) is with_following


def test_warm_layout_is_shared_without_per_stock_date_parse(monkeypatch):
    layout.clear_period_layout_cache()
    days = calendar(80); rows = quotes(days)
    expected = hl._period_rows(rows, days, days[-1], 'W')
    before = layout._cached_layout.cache_info()
    def forbidden(*args): pytest.fail('calendar/closure/date parsing must be reused for another stock/consumer')
    monkeypatch.setattr(layout, 'latest_closed_date', forbidden)
    monkeypatch.setattr(layout, '_canonical', forbidden)
    assert hl._period_rows(rows, days, days[-1], 'W') == expected
    assert boll._period_rows(rows, days, days[-1], 'W') == _legacy_boll(rows, days, days[-1], 'W')
    after = layout._cached_layout.cache_info()
    assert after.misses == before.misses and after.hits >= before.hits+2


def test_layout_is_immutable_bounded_and_oversized_inputs_bypass_lru(monkeypatch):
    layout.clear_period_layout_cache()
    days = calendar(40)
    plan = layout.period_layout(days, days[-1], 'D')
    with pytest.raises(TypeError): plan.groups[0][1][0] = 'changed'
    with pytest.raises(AttributeError): plan.session_set.add('changed')
    for day in days: layout.period_layout(days, day, 'D')
    assert layout._cached_layout.cache_info().currsize == layout.MAX_LAYOUTS
    monkeypatch.setattr(layout, 'MAX_CACHED_CALENDAR_SESSIONS', 2)
    before = layout._cached_layout.cache_info()
    assert layout.period_layout(days, days[-1], 'D') == plan
    assert layout._cached_layout.cache_info() == before


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_calendar_order_duplicates_boundaries_and_caller_mutation_do_not_leak(period):
    days = calendar(75, start='2025-01-08'); rows = quotes(days)
    shuffled = list(reversed(days))+days[:4]
    actual = hl._period_rows(rows, shuffled, days[-1], period)
    assert actual == _legacy_hl(rows, days, days[-1], period)
    if period != 'D': assert actual[0]['blocker']['kind'] == 'incomplete_period_boundary'
    changed = deepcopy(actual)
    if changed and changed[0]['blocker']: changed[0]['blocker']['reason'] = 'caller edit'
    assert hl._period_rows(rows, days, days[-1], period) == _legacy_hl(rows, days, days[-1], period)
    assert boll._period_rows(rows, shuffled, days[-1], period) == _legacy_boll(rows, days, days[-1], period)
