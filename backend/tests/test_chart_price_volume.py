from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.analytics.chart_price_volume import price_volume_levels


def fixture(period='D', kind='stock'):
    start = date(2025, 1, 3)
    if period == 'M':
        days = [date(2023 + index // 12, index % 12 + 1, 3).isoformat() for index in range(36)]
    else:
        days = [(start + timedelta(days=index * (7 if period == 'W' else 1))).isoformat() for index in range(36)]
    bars = [{'date': day, 'open': 10.0, 'close': 10.2, 'high': 10.6, 'low': 9.4,
             'volume': 100.0, 'complete': True, 'corporate_action': False,
             'corporate_action_coverage_known': True} for day in days]
    bars[8]['high'] = 12
    bars[12]['low'] = 8.5
    bars[25]['high'] = 11.5
    bars[28]['low'] = 9
    bars[-1].update(open=10.2, close=10.5, low=10.0, high=10.7, volume=200)
    return {'instrument': {'instrument_id': '001234.SZ', 'kind': kind},
            'snapshot_id': 'market-test', 'as_of': days[-1], 'period': period,
            'price_basis': 'none', 'bars': bars}, days


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_complete_structure_uses_confirmed_active_pivots_and_independent_atr_volume(period):
    chart, days = fixture(period)
    original = deepcopy(chart)
    value = price_volume_levels(chart, days, expected_snapshot='market-test')
    assert value['support']['price'] == 9
    assert value['resistance']['price'] == 11.5
    assert value['support']['formed_at'] == days[28]
    assert value['support']['confirmed_on'] == days[30]
    assert value['stop']['price'] == pytest.approx(9 - .5 * value['atr']['value'])
    assert value['breakout']['price'] == pytest.approx(11.5 + .2 * value['atr']['value'])
    assert value['volume'] == {'current': 200, 'mean_previous20': 100, 'ratio': 2,
                               'state': 'expanded', 'mean_start': days[-21], 'mean_end': days[-2], 'reason': None}
    assert chart == original


def test_breakout_known_before_event_bar_does_not_fabricate_pressure_above_new_high():
    chart, days = fixture()
    chart['bars'][-1].update(open=10.2, low=10.1, close=12.8, high=13)
    value = price_volume_levels(chart, days)
    event = value['recent_breakout']
    assert value['signal']['state'] == 'expanded_breakout'
    assert value['resistance'] is None
    assert event['base_price'] == 11.5
    assert event['date'] == days[-1] and event['confirmed_on'] < event['date']
    assert event['atr_reference_date'] == days[-2]
    assert event['volume_confirmed'] is True
    assert value['support']['price'] == 9
    assert value['stop']['source'] == 'breakout_retest_reference'
    assert value['stop']['base_price'] == 11.5
    assert value['stop']['price'] == pytest.approx(11.5 - .5 * value['atr']['value'])
    assert value['stop']['breakout_date'] == days[-1]
    previous = price_volume_levels({**chart, 'as_of': days[-2], 'bars': chart['bars'][:-1]}, days)
    assert event['price'] == previous['breakout']['price']
    chart['bars'][-1]['volume'] = 90
    assert price_volume_levels(chart, days)['signal']['state'] == 'breakout_without_expansion'


def test_later_retest_and_failed_breakout_use_original_threshold_and_event_volume():
    chart, days = fixture()
    chart['bars'][-1].update(open=10.2, low=10.1, close=12.8, high=13)
    before = price_volume_levels(chart, days)
    following = (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
    chart['bars'].append(dict(chart['bars'][-1], date=following, open=12.7, close=12.9, high=13, low=11.6, volume=70))
    chart['as_of'] = following
    result = price_volume_levels(chart, [*days, following])
    assert result['recent_breakout']['state'] == 'retest'
    assert result['recent_breakout']['date'] == days[-1]
    assert result['recent_breakout']['price'] == before['recent_breakout']['price']
    assert result['volume']['state'] == 'contracted'
    assert result['signal']['state'] == 'retest_holding'
    chart['bars'][-1].update(open=11.4, close=11.3, high=11.6, low=11.1)
    failed = price_volume_levels(chart, [*days, following])
    assert failed['recent_breakout']['state'] == 'failed'
    assert failed['signal']['state'] == 'failed_breakout'


def test_missing_volume_keeps_genuine_structure_but_does_not_claim_confirmation():
    chart, days = fixture()
    chart['bars'][-3]['volume'] = None
    value = price_volume_levels(chart, days)
    assert value['support']['price'] == 9 and value['stop'] is not None
    assert value['volume']['state'] == 'unknown'
    chart['bars'][-1].update(open=10.2, low=10.1, close=12.8, high=13)
    assert price_volume_levels(chart, days)['signal']['state'] == 'breakout_volume_unknown'


def test_forward_prices_do_not_invent_comparable_volume_across_corporate_actions():
    chart, days = fixture()
    chart.update(price_basis='forward_event_affine', adjustment={'usable': True, 'anchor': days[-1], 'events': [{'date': days[-10]}]})
    value = price_volume_levels(chart, days)
    assert value['support'] is not None and value['atr']['value'] is not None
    assert value['volume']['state'] == 'unknown'
    assert '公司行动' in value['volume']['reason']
    chart['adjustment']['anchor'] = days[-2]
    assert price_volume_levels(chart, days)['reference_price'] is None


def test_bad_final_bar_missing_observation_and_future_data_never_fall_back():
    chart, days = fixture()
    chart['bars'][-1]['close'] = 0
    value = price_volume_levels(chart, days)
    assert value['reference_price'] is None and value['stop'] is None
    chart, days = fixture()
    chart['bars'] = chart['bars'][:-1]
    assert price_volume_levels(chart, days)['reference_price'] is None
    chart, days = fixture()
    chart['as_of'] = days[-2]
    assert price_volume_levels(chart, days)['reference_price'] is None
    assert price_volume_levels(fixture()[0], fixture()[1], expected_snapshot='wrong')['reference_price'] is None


@pytest.mark.parametrize(('period', 'field'), [('W', 'week_closed'), ('M', 'month_closed')])
def test_forming_period_is_identified_but_bad_closed_period_cannot_be_skipped(period, field):
    chart, days = fixture(period)
    chart['bars'][-1].update(complete=False, **{field: False})
    value = price_volume_levels(chart, days)
    assert value['reference_date'] == days[-2] and value['reference_price'] == 10.2
    assert '尚未完成' in value['reason']
    chart['bars'][-1][field] = True
    assert price_volume_levels(chart, days)['reference_price'] is None


def test_raw_action_gap_and_insufficient_warmup_remain_honest():
    chart, days = fixture()
    chart['bars'][24]['corporate_action'] = True
    value = price_volume_levels(chart, days)
    assert value['volume']['state'] == 'unknown' and value['atr']['state'] == 'unknown'
    assert value['stop'] is None
    chart, days = fixture()
    del chart['bars'][-4]
    assert price_volume_levels(chart, days)['support'] is None
    assert price_volume_levels(chart, days)['atr']['state'] == 'unknown'


def test_broken_preexisting_support_is_not_displayed_as_an_unbroken_stop():
    chart, days = fixture()
    chart['bars'][-1].update(open=8.3, close=8, high=8.5, low=7.8)
    value = price_volume_levels(chart, days)
    assert value['signal']['state'] == 'support_broken'
    assert value['support_break']['price'] == 9
    assert value['support'] is None and value['stop'] is None


def test_wilder_atr_uses_prior_close_seed_and_recursive_smoothing():
    chart, days = fixture()
    for row in chart['bars']:
        row.update(open=11, high=12, low=10, close=11)
    chart['bars'][-1]['high'] = 14
    value = price_volume_levels(chart, days)
    assert value['atr']['value'] == pytest.approx((2 * 13 + 4) / 14)
    assert value['atr']['window_start'] == days[0]
    assert value['atr']['window_end'] == days[-1]


def test_historical_breakout_does_not_use_an_adjustment_occurring_after_event():
    chart, days = fixture()
    chart['bars'][-1].update(open=10.2, low=10.1, close=12.8, high=13)
    assert price_volume_levels(chart, days)['recent_breakout'] is not None
    following = (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
    chart['bars'].append(dict(chart['bars'][-1], date=following, open=12.7, close=12.9, high=13, low=12.5))
    chart.update(as_of=following, price_basis='forward_event_affine',
                 adjustment={'usable': True, 'anchor': following, 'events': [{'date': following}]})
    result = price_volume_levels(chart, [*days, following])
    assert result['recent_breakout'] is None
    assert result['reference_date'] == following
    assert result['volume']['state'] == 'unknown'
