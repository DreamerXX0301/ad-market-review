"""A rank window never materializes unrelated history or moves its endpoints."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.services.market_service import _gainers_histories, gainers_rows


SYMBOL = '600001.SH'


class LegacyMarket:
    def __init__(self):
        self.days = []; day = date(2026, 1, 5)
        while len(self.days) < 80:
            if day.weekday() < 5:self.days.append(day.isoformat())
            day += timedelta(days=1)
        self.day = self.days[60]; self.baseline = self.days[30]
        self.data = [{'instrument_id': SYMBOL, 'date': day,
                      'open': 100 + index, 'high': 101 + index,
                      'low': 99 + index, 'close': 100 + index,
                      'corporate_action': index == 45, 'limit_up': True}
                     for index, day in enumerate(self.days)]
        self.events = []; self.calls = []

    def calendar(self):return self.days

    def instruments(self, **kwargs):
        return [{'instrument_id': SYMBOL, 'code': SYMBOL[:6], 'name': '测试', 'kind': 'stock'}]

    def history_panel(self, as_of=None, lookback=None):
        return {SYMBOL: [dict(row) for row in self.data if row['date'] <= as_of][-lookback:]}

    def actions(self, symbol, start, end):
        return [dict(row) for row in self.events if start <= row['date'] <= end]

    def panel(self):
        return [dict(row, name='测试') for row in self.data if row['date'] == self.day][:1]


class BoundedMarket(LegacyMarket):
    def history_panel(self, *args, **kwargs):
        raise AssertionError('The bounded rank must not request all-market histories')

    def history_for_instruments(self, symbols, start=None, end=None):
        self.calls.append({'symbols': list(symbols), 'start': start, 'end': end})
        # Raw history intentionally has no joined action/source-pool flags.
        return {symbol: [{key: value for key, value in row.items()
                          if key not in ('corporate_action', 'limit_up')}
                         for row in self.data if start <= row['date'] <= end]
                for symbol in symbols}


@pytest.mark.parametrize('case', [
    'normal', 'suspension', 'missing_start', 'missing_end',
    'valid_action', 'unverified_action', 'action_on_baseline', 'duplicate_tail',
])
def test_bounded_rank_matches_legacy_full_output(case):
    legacy = LegacyMarket()
    if case in ('suspension', 'unverified_action'):
        missing = legacy.days[44]
        legacy.data = [row for row in legacy.data if row['date'] != missing]
    elif case == 'missing_start':
        legacy.data = [row for row in legacy.data if row['date'] != legacy.baseline]
    elif case == 'missing_end':
        legacy.data = [row for row in legacy.data if row['date'] != legacy.day]
    elif case == 'duplicate_tail':
        legacy.data.append(deepcopy(legacy.data[45]))
        legacy.data.sort(key=lambda row: row['date'])
    if case in ('valid_action', 'unverified_action', 'action_on_baseline'):
        legacy.events = [{'date': legacy.baseline if case == 'action_on_baseline' else legacy.days[45],
                          'dividend_per_share': 1, 'per_share_bonus': 0,
                          'allotment_ratio': 0, 'allotment_price': 0}]
    bounded = BoundedMarket(); bounded.__dict__.update(deepcopy(legacy.__dict__))
    expected = gainers_rows(legacy, {}, legacy.day, legacy.panel())
    actual = gainers_rows(bounded, {}, bounded.day, bounded.panel())
    assert actual == expected
    assert all(call['start'] == legacy.baseline and call['end'] == legacy.day
               for call in bounded.calls)
    result = actual[0][0]
    if case == 'suspension':
        assert result['return_30d_missing_sessions'] == 1
        assert result['return_30d_quality'] == 'endpoint_verified_with_gaps'
    elif case == 'valid_action':
        assert result['return_30d_price_basis'] == 'forward_event_affine'
        assert result['return_30d'] == pytest.approx((160 / 129 - 1) * 100)
    elif case in ('missing_start', 'missing_end', 'unverified_action', 'duplicate_tail'):
        assert result['rank'] is None and result['return_30d'] is None


def test_bounded_history_requests_only_current_quote_symbols_in_256_batches():
    market = BoundedMarket()
    symbols = [f'{600000 + index:06}.SH' for index in range(600)]
    rows = [{'instrument_id': symbol} for symbol in symbols]
    histories = _gainers_histories(market, market.day, market.baseline, rows)
    assert set(histories) == set(symbols)
    assert [len(call['symbols']) for call in market.calls] == [256, 256, 88]
    assert all(call['start'] == market.baseline and call['end'] == market.day for call in market.calls)


def test_short_calendar_does_not_request_unneeded_history():
    market = BoundedMarket(); market.day = market.days[20]
    rows, summary = gainers_rows(market, {}, market.day, market.panel())
    assert not market.calls
    assert rows[0]['return_30d_start'] is None and rows[0]['rank'] is None
    assert '不足31天' in rows[0]['return_30d_reason']
    assert summary['covered_stocks'] == 0
