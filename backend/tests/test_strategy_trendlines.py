from datetime import date, timedelta

import pytest

from app.analytics import strategy_trendlines as trend


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_support_retest_uses_preexisting_line_and_rejects_missing_signal(period):
    begin = date(2022, 1, 3)
    all_days = [begin+timedelta(days=i) for i in range(900)]
    calendar = [day.isoformat() for day in all_days if day.weekday() < 5]
    fields = {period: {'trendline_pullback'}}
    observation = calendar[-4]
    windows = trend.windows_for(calendar, observation, fields)
    groups = windows[period]
    lows = [14]*8 + [12, 11, 10, 12, 13, 14, 13, 12, 14, 15, 16, 17, 14.0]
    groups = groups[-len(lows):]
    rows = [{'date':day, 'low':low, 'high':18, 'open':17, 'close':17,
             'volume':100, 'corporate_action':False, 'corporate_action_coverage_known':True}
            for (_, days, _),low in zip(groups,lows) for day in days]
    signal_day = groups[-1][1][-1]
    node = {'field':'trendline_pullback','op':'matches','value':0.5}
    state = trend.summaries(rows, windows, fields)[period]
    check = trend.evaluate(node, {'trendline_state':state})
    assert check['result'] is True
    assert check['observed_date'] == signal_day
    assert check['evidence']['line']['confirmed_on'] < signal_day
    assert check['evidence']['line']['projected_price'] == pytest.approx(14)
    future = dict(rows[-1], date=calendar[-1], low=2)
    assert trend.summaries([*rows,future], windows, fields)[period] == state
    missing = trend.summaries(rows[:-1], windows, fields)[period]
    assert trend.evaluate(node, {'trendline_state':missing})['result'] is None


def test_horizontal_setup_uses_only_prior_confirmed_zones():
    from app.domain import conditions, sector_conditions

    start = date(2026, 1, 5)
    calendar = [(start + timedelta(days=i)).isoformat() for i in range(50)
                if (start + timedelta(days=i)).weekday() < 5]
    day = calendar[17]
    levels = [(10.2, 11.8)] * 18
    for index in (3, 8):
        levels[index] = (10, 11.8)
    for index in (5, 12):
        levels[index] = (10.2, 12)
    support = 'horizontal_support_close_near'
    breakout = 'horizontal_resistance_breakout'
    requested = {'D': {support, breakout}}
    windows = trend.windows_for(calendar, day, requested)
    rows = [{'date': date_, 'open': 11, 'close': 11, 'low': low, 'high': high, 'volume': 100,
             'corporate_action': False, 'corporate_action_coverage_known': True}
            for date_, (low, high) in zip(calendar, levels)]

    def evaluate(field, close, *, delete_signal=False, method='stock'):
        node = {'field': field, 'op': 'matches', 'value': .5, 'period': 'D'}
        values = [*rows[:-1], {**rows[-1], 'close': close, 'low': min(close, 10.2), 'high': max(close, 11.8)}]
        if delete_signal:
            values.pop()
        state = trend.summaries(values, windows, requested)['D']
        if method == 'sector':
            node = sector_conditions.validate(node, 'D')
            return sector_conditions.evaluate(node, {'D': {'trendline_state': state}})
        node = conditions.validate(node)
        return conditions.evaluate(node, {'trendline_state': state, 'indicator_period': 'D'})

    support_hit = evaluate(support, 10.03)
    assert support_hit['result'] is True
    assert support_hit['evidence']['zone']['end_date'] == calendar[16]
    assert support_hit['evidence']['zone']['touches'] == 2
    assert support_hit['evidence']['version'] == trend.HORIZONTAL_VERSION
    assert evaluate(support, 9.95)['result'] is False
    assert evaluate(breakout, 12.05)['result'] is False
    assert evaluate(breakout, 12.08, method='sector')['result'] is True
    assert evaluate(breakout, 12.08, delete_signal=True)['result'] is None
