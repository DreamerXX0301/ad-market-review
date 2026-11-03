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
