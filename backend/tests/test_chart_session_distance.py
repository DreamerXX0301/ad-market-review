"""Display distances use the retained market calendar, never quote counts."""
from copy import deepcopy

import pytest

from app.analytics.chart_session_distance import attach_chart_session_distance
from app.analytics.market import MarketData


# Intentionally sparse: this test calendar includes the supplied exchange
# holidays and quotation gaps; a weekday heuristic would give another result.
CALENDAR = ['2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23',
            '2026-09-24', '2026-09-28', '2026-09-29', '2026-09-30',
            '2026-10-08', '2026-10-09', '2026-10-12', '2026-10-13',
            '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-19',
            '2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23']


def chart():
    return {'as_of': CALENDAR[-1], 'period': 'D', 'bars': [
        {'date': CALENDAR[0], 'close': 10, 'ma5': None},
        {'date': CALENDAR[-1], 'close': 11, 'ma5': None}],
        'quote_gaps': [{'missing_market_dates': CALENDAR[1:-1]}]}


def test_distance_includes_market_days_without_quotes_and_never_mutates_inputs():
    original = chart(); before = deepcopy(original); days = list(CALENDAR)
    result = attach_chart_session_distance(original, days)
    assert [row['trading_days_to_as_of'] for row in result['bars']] == [19, 0]
    assert result['trading_day_distance']['anchor_date'] == CALENDAR[-1]
    assert result['trading_day_distance']['state'] == 'available'
    assert result['trading_day_distance']['basis'] == 'market_calendar'
    assert original == before and days == CALENDAR
    del result['trading_day_distance']
    for row in result['bars']: del row['trading_days_to_as_of']
    assert result == original  # Prices, indicators and coverage retain their values.


def test_historical_anchor_ignores_later_calendar_dates():
    original = chart(); original.update(as_of=CALENDAR[8])
    result = attach_chart_session_distance(original, CALENDAR)
    assert result['bars'][0]['trading_days_to_as_of'] == 8
    assert result['bars'][1]['trading_days_to_as_of'] is None  # Future quotation.
    assert result['trading_day_distance']['anchor_date'] == CALENDAR[8]


@pytest.mark.parametrize('calendar', [None, [], ['2026-10-23', '2026-10-23'],
                                    list(reversed(CALENDAR)), ['2026-02-30'],
                                    ['2026-9-18', '2026-10-23'], [None]])
def test_missing_or_invalid_calendar_cannot_guess_distances(calendar):
    result = attach_chart_session_distance(chart(), calendar)
    assert result['trading_day_distance']['state'] == 'unknown'
    assert all(row['trading_days_to_as_of'] is None for row in result['bars'])


@pytest.mark.parametrize('anchor', [None, '2026-10-25', '2026-02-30', '2026-9-18'])
def test_anchor_must_be_a_retained_market_day(anchor):
    result = attach_chart_session_distance({**chart(), 'as_of': anchor}, CALENDAR)
    assert result['trading_day_distance']['state'] == 'unknown'
    assert all(row['trading_days_to_as_of'] is None for row in result['bars'])


def test_calendar_generation_changes_empty_charts_and_invalid_bar_dates_stay_unknown():
    changed = attach_chart_session_distance(chart(), CALENDAR, same_snapshot=False)
    assert changed['trading_day_distance']['state'] == 'unknown'
    assert '代际变化' in changed['trading_day_distance']['reason']
    assert all(row['trading_days_to_as_of'] is None for row in changed['bars'])
    empty = attach_chart_session_distance({**chart(), 'bars': []}, CALENDAR)
    assert empty['trading_day_distance']['state'] == 'unknown'
    invalid = attach_chart_session_distance({**chart(), 'bars': [
        {'date': '2026-09-25'}, {'date': '2026-10-24'}, {'date': 'bad'}, {}]}, CALENDAR)
    assert all(row['trading_days_to_as_of'] is None for row in invalid['bars'])


class CalendarMarket(MarketData):
    """The chart integration shares a calendar even if a symbol did not quote."""
    def __init__(self, kind='stock', switch_generation=False):
        self.kind = kind; self.switch_generation = switch_generation; self.reads = 0
        self.rows = [{'date': day, 'open': 10, 'high': 11, 'low': 9, 'close': 10,
                      'volume': 100, 'amount': 1000, 'corporate_action': False,
                      'corporate_action_coverage_known': True}
                     for day in CALENDAR if day not in ('2026-09-22', '2026-10-21')]

    def calendar(self): return list(CALENDAR)
    def status(self):
        self.reads += 1
        return {'snapshot_id': 'changed' if self.switch_generation and self.reads > 1 else 'fixed',
                'data_end': CALENDAR[-1], 'warnings': [], 'quality': 'partial'}
    def bars(self, symbol, as_of=None):
        return [dict(row) for row in self.rows if not as_of or row['date'] <= as_of]
    def prices(self, symbol, as_of=None, price_basis='none'):
        return self.bars(symbol, as_of), {'usable': False, 'reason': '测试固定原价'}
    def instrument(self, symbol): return {'instrument_id': symbol, 'kind': self.kind}


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('kind', ['stock', 'index'])
def test_stock_and_index_chart_periods_attach_own_calendar_evidence(period, kind):
    market = CalendarMarket(kind); before = deepcopy(market.rows)
    result = market.chart('399001.SZ' if kind == 'index' else '600001.SH', period=period)
    assert result['trading_day_distance']['anchor_date'] == CALENDAR[-1]
    assert result['trading_day_distance']['state'] == 'available'
    assert market.rows == before
    for bar in result['bars']:
        assert bar['trading_days_to_as_of'] == len(CALENDAR) - 1 - CALENDAR.index(bar['date'])
    assert result['bars'][-1]['trading_days_to_as_of'] == 0
    if period != 'D':
        assert result['bars'][-1]['complete'] is False
        assert result['bars'][-1]['date'] == CALENDAR[-1]  # Actual quote, not period label.
    historical = market.chart('600001.SH', period=period, as_of='2026-10-20')
    assert historical['trading_day_distance']['anchor_date'] == '2026-10-20'
    assert historical['bars'][-1]['trading_days_to_as_of'] == 0


def test_stock_chart_does_not_bind_a_calendar_from_another_generation():
    result = CalendarMarket(switch_generation=True).chart('600001.SH')
    assert result['trading_day_distance']['state'] == 'unknown'
    assert all(row['trading_days_to_as_of'] is None for row in result['bars'])


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_missing_tail_quotes_still_counts_to_the_chart_observation_market_day(period):
    market = CalendarMarket()
    market.rows = [row for row in market.rows if row['date'] <= '2026-10-20']
    result = market.chart('600001.SH', period=period, as_of='2026-10-23')
    assert result['as_of'] == '2026-10-23'
    assert result['trading_day_distance']['state'] == 'available'
    assert result['bars'][-1]['date'] == '2026-10-20'
    assert result['bars'][-1]['trading_days_to_as_of'] == 3
