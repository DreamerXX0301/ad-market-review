from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json

import pytest

from app.analytics.chart_price_volume import price_volume_levels, price_volume_profiles


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


@pytest.mark.parametrize(('period', 'original_sha256'), [
    ('D', '5aaf4a769e511471e5f4d021ccb6635017d2a63d2dff7b321d1df2e66fd7be6d'),
    ('W', 'facceab32720dae1533fc4d7d2fc473e98550f642aad94869f8a2f836a63dd2b'),
    ('M', '916974d002d3feed1072db5f9cd03600c58f544fb9a7d8c5239d178b02e47a40'),
])
def test_structure_output_preserves_every_field_from_before_short_profile(period, original_sha256):
    # These fingerprints were taken from the committed v1 implementation.
    chart, days = fixture(period)
    original = deepcopy(chart)
    value = price_volume_levels(chart, days, expected_snapshot='market-test')
    assert hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == original_sha256
    assert price_volume_levels(chart, days, profile='structure') == value
    combined = price_volume_profiles(chart, days, expected_snapshot='market-test')
    assert {key: item for key, item in combined.items() if key != 'short_term'} == value
    assert 'profile' not in value and 'short_term' not in value
    assert chart == original


def test_short_profile_rejects_far_support_without_moving_its_real_price():
    chart, days = fixture()
    structure = price_volume_levels(chart, days)
    short = price_volume_levels(chart, days, profile='short_week_v1')
    assert structure['support']['price'] == 9
    assert short['support'] is None
    assert '暂无可用近端支撑' in short['support_reason']
    assert short['algorithm'] == 'short-week-risk-levels-v1'
    assert short['max_risk_pct'] == short['risk_max_pct'] == 5
    assert short['structure_lookback'] == 20 and short['lookback'] == 120
    assert short['atr'] == structure['atr'] and short['volume'] == structure['volume']
    assert short['stop']['source'] == 'short_risk_boundary'
    assert short['stop']['price'] >= chart['bars'][-1]['close'] * .95
    assert '不代表真实支撑' in short['reason']
    assert short['display_support'] == structure['support']
    assert short['display_scope'] == 'recent_20'
    assert short['display_distance_state'] == 'far'


@pytest.mark.parametrize(('risk', 'original_sha'), [
    (3, 'ae441cd747e4fedc7ae6bab2cc13bb87038ccce759a699689de0c42143e70fc9'),
    (5, 'ca3677783a70db63c8609cd104ac277fa67128685407d0a96fe8fc1515e8f2b4'),
    (8, 'fd9b42beaf8d0de926e1d8839a6f3ec505e7fabf2f7513dd93fb37efedc9b3ed'),
    (20, '8ec3b5ccb8c3620bee6806ee2161b6dd81c451e59dea582fe2d59f95e2508c3e'),
])
def test_display_support_is_additive_without_changing_frozen_risk_or_monitoring(risk, original_sha):
    chart, days = fixture()
    result = price_volume_levels(chart, days, profile='short_week_v1', max_risk_pct=risk)
    display = {key: result.pop(key) for key in ('display_support', 'display_scope', 'display_distance_state')}
    assert display['display_support']['price'] == 9
    assert display['display_support']['formed_at'] == days[28]
    assert hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == original_sha


@pytest.mark.parametrize('bad_field', ['complete', 'noon_provisional'])
def test_display_support_cannot_reach_across_an_incomplete_daily_segment(bad_field):
    chart, days = fixture()
    chart['bars'][-3][bad_field] = False if bad_field == 'complete' else True
    result = price_volume_profiles(chart, days)['short_term']
    assert result['display_support'] is None
    assert result['display_scope'] is None
    assert result['stop'] is None


def test_display_support_stays_unknown_without_a_confirmed_pivot_or_current_quote():
    chart, days = fixture()
    for row in chart['bars']:
        row.update(open=10, close=10, low=9, high=11)
    assert price_volume_profiles(chart, days)['short_term']['display_support'] is None
    chart, days = fixture()
    chart['bars'][-1]['close'] = 0
    assert price_volume_profiles(chart, days)['short_term']['display_support'] is None


def test_short_profile_uses_recent_formed_pivots_without_old_structure_fallback():
    chart, days = fixture()
    chart['bars'][25]['high'] = 10.6
    chart['bars'][28]['low'] = 9.4
    structure = price_volume_levels(chart, days)
    short = price_volume_levels(chart, days, profile='short_week_v1')
    assert structure['support']['formed_at'] == days[12]
    assert structure['resistance']['formed_at'] == days[8]
    assert short['support'] is None and short['resistance'] is None
    assert short['breakout'] is None and short['recent_breakout'] is None
    assert '近20根' in short['support_reason'] and '近20根' in short['breakout_reason']
    assert short['stop'] is not None
    assert short['atr']['window_start'] == days[0]
    assert short['display_support'] == structure['support']
    assert short['display_scope'] == 'historical_120'
    assert short['display_distance_state'] == 'far'


def test_short_profile_keeps_genuine_near_support_and_its_confirmation_dates():
    chart, days = fixture()
    for bar in chart['bars'][-20:]:
        bar.update(low=10.3, high=10.7, close=10.4, open=10.4)
    chart['bars'][28]['low'] = 10.1
    chart['bars'][-1]['close'] = 10.5
    short = price_volume_levels(chart, days, profile='short_week_v1')
    assert short['support']['price'] == 10.1
    assert short['support']['formed_at'] == days[28]
    assert short['support']['confirmed_on'] == days[30]
    assert short['support_reason'] is None
    boundary = short['stop']
    assert boundary['formed_at'] == boundary['confirmed_on'] == days[-1]
    assert boundary['base_price'] == 10.5
    assert boundary['structure_reference'] == short['support']
    assert boundary['price'] >= 10.1 - .25 * short['atr']['value']


def test_rapid_rise_short_risk_boundary_is_bounded_and_not_called_support():
    chart, days = fixture()
    chart['bars'][-1].update(open=49.5, low=49, high=51, close=50)
    short = price_volume_levels(chart, days, profile='short_week_v1', max_risk_pct=5)
    boundary = short['stop']
    assert short['support'] is None
    assert boundary['price'] == pytest.approx(47.5)
    assert boundary['selected_basis'] == 'maximum_risk_distance'
    assert boundary['source'] == 'short_risk_boundary'
    assert boundary['distance_pct'] == pytest.approx(-5)
    assert boundary['buffer_value'] == pytest.approx(50 - boundary['price'])
    assert boundary['buffer_atr'] == pytest.approx(boundary['buffer_value'] / short['atr']['value'])
    assert boundary['atr_reference_date'] == days[-1]
    assert '不是真实支撑或个人成本止损' in boundary['notice']
    assert '未经最优性回测' in boundary['notice']


def test_short_risk_reference_respects_atr_and_configured_distance_independently():
    chart, days = fixture()
    short = price_volume_levels(chart, days, profile='short_week_v1', max_risk_pct=20)
    assert short['stop']['price'] == pytest.approx(short['reference_price'] - short['atr']['value'])
    assert short['stop']['selected_basis'] == 'reference_close_minus_atr14'
    tight = price_volume_levels(chart, days, profile='short_week_v1', max_risk_pct=.5)
    assert tight['support'] is None
    assert tight['stop']['price'] == pytest.approx(tight['reference_price'] * .995)
    assert tight['stop']['distance_pct'] == pytest.approx(-.5)


@pytest.mark.parametrize(('period', 'kind'), [('W', 'stock'), ('M', 'stock'), ('D', 'index')])
def test_short_profile_does_not_substitute_other_chart_periods_or_instruments(period, kind):
    chart, days = fixture(period, kind)
    value = price_volume_levels(chart, days, profile='short_week_v1')
    assert value['state'] == 'unknown'
    assert value['reference_price'] is None
    assert value['support'] is value['stop'] is value['breakout'] is None
    assert '仅支持个股完整日K' in value['reason']


@pytest.mark.parametrize('mutation', ['gap', 'action', 'bad_quote', 'future', 'snapshot'])
def test_short_profile_missing_atr_bad_quotes_and_future_data_stay_unknown(mutation):
    chart, days = fixture()
    expected = None
    if mutation == 'gap':
        del chart['bars'][-4]
    elif mutation == 'action':
        chart['bars'][-8]['corporate_action'] = True
    elif mutation == 'bad_quote':
        chart['bars'][-1]['close'] = 0
    elif mutation == 'future':
        chart['as_of'] = days[-2]
    else:
        expected = 'other-snapshot'
    value = price_volume_levels(chart, days, profile='short_week_v1', expected_snapshot=expected)
    assert value['state'] == 'unknown'
    assert value['stop'] is None


@pytest.mark.parametrize('risk', [None, True, False, '5', 0, -1, 100, float('nan'), float('inf')])
def test_short_profile_rejects_invalid_risk_percentages(risk):
    chart, days = fixture()
    with pytest.raises(ValueError, match='max_risk_pct'):
        price_volume_levels(chart, days, profile='short_week_v1', max_risk_pct=risk)


def test_unknown_profile_is_rejected_without_silent_structure_fallback():
    chart, days = fixture()
    with pytest.raises(ValueError, match='profile'):
        price_volume_levels(chart, days, profile='unknown')


@pytest.mark.parametrize(('field', 'value'), [('complete', False), ('noon_provisional', True)])
def test_short_profile_rejects_forming_or_noon_quotes_without_falling_back(field, value):
    chart, days = fixture()
    chart['bars'][-1][field] = value
    short = price_volume_levels(chart, days, profile='short_week_v1')
    assert short['state'] == 'unknown'
    assert short['reference_price'] is None and short['reference_date'] is None
    assert short['support'] is short['stop'] is short['breakout'] is None
    assert short['atr']['state'] == 'unknown'
    # This profile does not expand the old structural contract's validation.
    structure = price_volume_levels(chart, days)
    assert structure['reference_date'] == days[-1]
    assert 'structure_lookback' not in structure


@pytest.mark.parametrize(('field', 'value'), [('complete', False), ('noon_provisional', True)])
def test_short_incomplete_interior_quote_restarts_warmup_without_skipping_it(field, value):
    chart, days = fixture()
    chart['bars'][-4][field] = value
    short = price_volume_levels(chart, days, profile='short_week_v1')
    assert short['reference_date'] == days[-1]
    assert short['window_start'] == days[-3]
    assert short['observed_bars'] == 3
    assert short['state'] == 'unknown'
    assert short['support'] is short['stop'] is short['breakout'] is None
    assert short['atr']['observed_bars'] == 3
