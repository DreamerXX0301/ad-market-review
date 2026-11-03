from datetime import date, timedelta

import pytest

from app.analytics.chart_trendlines import automatic_trendlines
from app.services.chart_transport import chart_payload


def build(period='D', kind='stock'):
    start = date(2025, 1, 6)
    if period == 'M':
        days = [date(2025, month, 3).isoformat() for month in range(1, 13)]
    else:
        days = [(start + timedelta(days=i * (7 if period == 'W' else 1))).isoformat() for i in range(12)]
    lows = [12, 11, 10, 12, 13, 14, 13, 12, 14, 15, 16, 17]
    highs = [19, 20, 21, 23, 21, 19, 18, 19, 21, 19, 18, 18]
    bars = [{'date': day, 'open': (low+high)/2, 'close': (low+high)/2, 'low': low, 'high': high,
             'volume': 100, 'complete': True, 'corporate_action_coverage_known': True}
            for day, low, high in zip(days, lows, highs)]
    chart = {'instrument': {'instrument_id': 'test', 'kind': kind}, 'snapshot_id': 'test-market',
             'price_basis': 'none', 'period': period, 'as_of': days[-1], 'bars': bars}
    return chart, days


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('kind', ['stock', 'index'])
def test_confirmed_support_and_resistance_project_to_existing_period_bar(period, kind):
    chart, days = build(period, kind)
    value = automatic_trendlines(chart, days, expected_snapshot='test-market')
    support, resistance = value['items']
    assert (value['as_of'], value['price_basis'], value['period']) == (days[-1], 'none', period)
    assert (support['line_type'], support['start_date'], support['end_date'], support['confirmed_on']) == (
        'support', days[2], days[-1], days[9])
    assert [p['date'] for p in support['anchors']] == [days[2], days[7]]
    assert support['end_price'] == pytest.approx(13.6)
    assert resistance['line_type'] == 'resistance'
    assert resistance['confirmed_on'] == days[10] and resistance['end_price'] == pytest.approx(19.8)
    assert chart_payload({**chart, 'automatic_trendlines': value}, 'columns-v1')['automatic_trendlines'] == value


def test_history_has_no_future_confirmation_and_intervening_gap_does_not_bridge():
    chart, days = build()
    chart['bars'] = chart['bars'][:9]
    chart['as_of'] = days[8]
    assert not automatic_trendlines(chart, days)['items']
    chart, days = build()
    del chart['bars'][5]
    assert not automatic_trendlines(chart, days)['items']


@pytest.mark.parametrize(('period', 'closed_key'), [('W', 'week_closed'), ('M', 'month_closed')])
def test_forming_period_is_not_confirmation_or_extension(period, closed_key):
    chart, days = build(period)
    chart['bars'][-1].update(complete=False, **{closed_key: False})
    items = automatic_trendlines(chart, days)['items']
    assert len(items) == 2
    assert all(item['end_date'] == days[-2] for item in items)
    chart['bars'][-1][closed_key] = True
    assert not automatic_trendlines(chart, days)['items']


def test_unadjusted_actions_unknown_coverage_and_broken_wick_exclude_lines():
    chart, days = build()
    chart['bars'][6]['corporate_action'] = True
    assert not automatic_trendlines(chart, days)['items']
    chart['bars'][6].update(corporate_action=False, corporate_action_coverage_known=False)
    assert not automatic_trendlines(chart, days)['items']
    chart, days = build()
    chart['bars'][-1]['low'] = 12
    assert [item['line_type'] for item in automatic_trendlines(chart, days)['items']] == ['resistance']


def test_forward_basis_binding():
    chart, days = build()
    chart.update(price_basis='forward_event_affine', requested_price_basis='forward',
                 adjustment={'usable': True, 'anchor': days[-2]})
    assert not automatic_trendlines(chart, days)['items']
    chart['adjustment']['anchor'] = days[-1]
    assert automatic_trendlines(chart, days)['items']
    assert not automatic_trendlines(chart, days, expected_snapshot='another')['items']


def test_forward_fallback_uses_actual_raw_chart_and_keeps_action_boundary():
    chart, days = build()
    original = automatic_trendlines(chart, days)
    assert original['items']
    chart.update(requested_price_basis='forward', adjustment={'usable': False})
    assert automatic_trendlines(chart, days) == original
    chart['bars'][6]['corporate_action_coverage_known'] = False
    assert not automatic_trendlines(chart, days)['items']


@pytest.mark.parametrize(('close', 'state'), [(18, 'wick_cross'), (20, 'close_cross')])
def test_cross_records_the_preexisting_line_on_event_bar(close, state):
    chart, days = build()
    chart['bars'][-1].update(high=21, close=close)
    result = automatic_trendlines(chart, days)
    event = result['events'][0]
    assert (event['line_type'], event['date'], event['state']) == ('resistance', days[-1], state)
    assert event['price'] == pytest.approx(19.8)
    assert event['line']['end_date'] == days[-1]
    assert event['line']['confirmed_on'] == days[-2]
    assert all(line['line_type'] != 'resistance' for line in result['items'])
    if state == 'close_cross':
        following = (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
        chart['bars'].append(dict(chart['bars'][-1], date=following))
        chart['as_of'] = following
        confirmation = automatic_trendlines(chart, [*days, following])['events'][0]
        assert (confirmation['state'], confirmation['confirmed_on']) == ('confirmed_cross', following)


def test_horizontal_zone_requires_repeated_confirmed_pivots():
    chart, days = build()
    chart['bars'][7]['low'] = 10.1
    result = automatic_trendlines(chart, days)
    zone = next(zone for zone in result['zones'] if zone['line_type'] == 'support')
    assert zone == {'line_type': 'support', 'lower_price': 10, 'upper_price': 10.1,
                    'start_date': days[2], 'end_date': days[-1], 'touches': 2}
    assert not result['near_items']  # Same lines should not repeat as nearby references.
