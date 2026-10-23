"""Controlled price paths verify retrospective levels, boundaries and actions."""
from copy import deepcopy
from datetime import date, timedelta
import math

import pytest

from app.analytics.support_resistance import local_levels, VERSION


def bars(count=120, period='D'):
    result, day = [], date(2025, 1, 6)
    while len(result) < count:
        if day.weekday() < 5:
            i = len(result)
            value = 100 + 8 * math.sin(i * math.pi / 6)
            result.append({'date': day.isoformat(), 'open': value, 'close': value,
                           'high': value + 1, 'low': value - 1, 'complete': True,
                           'volume': 100, 'amount': value * 100})
        day += timedelta(days=1)
    if period == 'W':
        for i, row in enumerate(result):
            row['date'] = (date(2020, 1, 10) + timedelta(days=i * 7)).isoformat()
    if period == 'M':
        for i, row in enumerate(result):
            row['date'] = date(2015 + i // 12, i % 12 + 1, 28).isoformat()
    return result


def chart(rows, period='D', basis='none', as_of=None):
    return {'bars': rows, 'period': period, 'as_of': as_of or rows[-1]['date'], 'price_basis': basis}


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_confirmed_levels_are_bounded_correct_sides_and_do_not_mutate_prices(period):
    rows = bars(period=period)
    before = deepcopy(rows)
    result = local_levels(chart(rows, period), [row['date'] for row in rows])
    assert result['items'] and len(result['items']) <= 6
    assert result['algorithm'] == VERSION
    assert rows == before
    for item in result['items']:
        assert (item['price'] <= result['reference_price']) if item['line_type'] == 'support' else (item['price'] >= result['reference_price'])
        assert item['touches'] == len(item['evidence'])
        assert item['confirmed_on'] <= rows[-1]['date']
        for evidence in item['evidence']:
            at = next(i for i, row in enumerate(rows) if row['date'] == evidence['date'])
            assert rows[at + 2]['date'] == evidence['confirmed_on']
            field = 'low' if item['line_type'] == 'support' else 'high'
            assert evidence['price'] == rows[at][field]


def test_future_prices_and_duplicate_dates_are_rejected_instead_of_silently_used():
    rows = bars(30)
    for invalid in [rows, rows[:-1] + [rows[-2]], rows[::-1]]:
        with pytest.raises(ValueError):
            local_levels(chart(invalid, as_of=rows[-2]['date']), [row['date'] for row in rows])


def test_latest_two_bars_cannot_be_confirmed_pivots_and_historical_horizon_is_stable():
    rows = bars(60)
    result = local_levels(chart(rows), [row['date'] for row in rows])
    assert all(evidence['date'] <= rows[-3]['date'] for item in result['items'] for evidence in item['evidence'])
    # Later storage must not change the result at a historical observation.
    calendar = [row['date'] for row in bars(120)]
    assert local_levels(chart(rows), calendar) == result


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_missing_period_is_a_barrier_for_confirmation(period):
    rows = bars(9, period)
    # A single isolated low would be a support, except its right quote is missing.
    for row in rows:
        row.update(open=100, close=100, high=101, low=99)
    rows[4]['low'] = 80
    complete = local_levels(chart(rows, period), [row['date'] for row in rows])
    assert complete['items'][0]['price'] == 80
    missing = rows[:5] + rows[6:]
    assert not local_levels(chart(missing, period), [row['date'] for row in rows])['items']


@pytest.mark.parametrize('period', ['W', 'M'])
def test_incomplete_periods_never_confirm_a_pivot_or_set_reference_price(period):
    rows = bars(12, period)
    rows[-1].update(complete=False, close=1000, high=1001)
    result = local_levels(chart(rows, period), [row['date'] for row in rows])
    assert result['reference_date'] == rows[-2]['date']
    assert all(e['confirmed_on'] < rows[-1]['date'] for item in result['items'] for e in item['evidence'])


def test_raw_action_resets_comparable_window_forward_basis_keeps_verified_history():
    rows = bars(120)
    rows[-4]['corporate_action'] = True
    raw = local_levels(chart(rows), [row['date'] for row in rows])
    assert not raw['items'] and raw['window_start'] == rows[-4]['date']
    adjusted = local_levels(chart(rows, basis='forward_event_affine'), [row['date'] for row in rows])
    assert adjusted['items']


def test_lookback_is_respected_and_equal_price_plateaus_do_not_invent_touch_counts():
    rows = bars(120)
    result = local_levels(chart(rows), [row['date'] for row in rows], 20)
    assert result['observed_bars'] == 20
    assert all(e['date'] >= rows[-20]['date'] for item in result['items'] for e in item['evidence'])
    for row in rows:
        row.update(open=100, close=100, high=101, low=99)
    assert not local_levels(chart(rows), [row['date'] for row in rows])['items']


def test_cluster_tolerance_cannot_chain_into_a_wide_price_band():
    rows = bars(120)
    for i, row in enumerate(rows):
        row.update(open=110, close=110, high=111, low=109)
        if i % 6 == 3:
            row['low'] = 90 + (i // 6) * .5
    result = local_levels(chart(rows), [row['date'] for row in rows])
    for item in result['items']:
        prices = [e['price'] for e in item['evidence']]
        assert (max(prices) / min(prices) - 1) * 100 <= .8


def test_invalid_ohlc_and_insufficient_history_return_unknown_without_fabricated_levels():
    rows = bars(4)
    result = local_levels(chart(rows), [row['date'] for row in rows])
    assert not result['items'] and result['reason']
    rows = bars(12)
    for row in rows:
        row['close'] = float('nan')
    result = local_levels(chart(rows), [row['date'] for row in rows])
    assert result['reference_price'] is None and not result['items']


def test_stock_unknown_action_coverage_is_not_silently_treated_as_no_action():
    rows = bars(120)
    rows[-5]['corporate_action_coverage_known'] = False
    result = local_levels({**chart(rows), 'instrument_kind': 'stock'}, [row['date'] for row in rows])
    assert not result['items'] and '公司行动' in result['reason']


def test_monthly_structure_uses_observation_daily_close_to_classify_current_sides():
    rows = bars(24, 'M')
    as_of = rows[-1]['date']
    # Latest incomplete month cannot confirm structure; its published daily
    # close is nevertheless the appropriate current price reference.
    rows[-1]['complete'] = False
    daily = {'date': as_of, 'close': 95}
    result = local_levels({**chart(rows, 'M'), 'reference_quote': daily}, [row['date'] for row in rows])
    assert result['reference_date'] == as_of and result['reference_price'] == 95
    assert result['items']
    for item in result['items']:
        assert item['price'] <= 95 if item['line_type'] == 'support' else item['price'] >= 95
        assert all(e['confirmed_on'] < as_of for e in item['evidence'])
