"""Evidence-bounded reads must equal the original full-history member metrics.

The ten-year calendar and prices below are synthetic fixtures, not market data.
The reference path changes only the read start back to calendar[0], preserving
the original full-materialization algorithm for an exact nested-result parity.
"""
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.services import topic_member_metrics as service
from test_topic_member_metrics import A, B, C, MetricMarket, action, evaluate, source_pool


class TenYearMarket(MetricMarket):
    def __init__(self):
        current = date(2016, 1, 1)
        self.days = []
        while current <= date(2026, 10, 22):
            if current.weekday() < 5:self.days.append(current.isoformat())
            current += timedelta(days=1)
        self.day = '2026-10-20'
        self.calls = Counter(); self.history_requests = []; self.materialized_rows = 0
        self.events = {}; self.reference_data = {}
        self.master = {symbol: {'instrument_id': symbol, 'code': symbol[:6], 'name': symbol,
                                'kind': 'stock', 'list_date': '2010-01-01'} for symbol in (A, B, C)}
        self.data = {}
        for number, symbol in enumerate(self.master):
            self.data[symbol] = []
            for index, day in enumerate(self.days):
                close = 10 + number * 3 + index * .001
                self.data[symbol].append({'instrument_id': symbol, 'date': day, 'open': close,
                                          'close': close, 'high': close + .1, 'low': close - .1,
                                          'volume': 1000, 'amount': close * 1000,
                                          'corporate_action': False})

    def history_for_instruments(self, symbols, start=None, end=None):
        result = super().history_for_instruments(symbols, start=start, end=end)
        self.materialized_rows += sum(len(rows) for rows in result.values())
        return result


def full_history_parity(market, supplement, monkeypatch, *, symbols=None, day=None):
    symbols = symbols or [A, B, C]; day = day or market.day
    before = market.materialized_rows
    actual = evaluate(market, day=day, symbols=symbols, supplement=supplement)
    narrowed = {'request': deepcopy(market.history_requests[-1]),
                'rows': market.materialized_rows-before}
    with monkeypatch.context() as context:
        context.setattr(service, '_history_start', lambda calendar, *_: calendar[0])
        before = market.materialized_rows
        expected = evaluate(market, day=day, symbols=symbols, supplement=supplement)
        full_rows = market.materialized_rows-before
    assert actual == expected, 'every value, reason, basis and coverage field must match full-history reads'
    return actual, narrowed, full_rows


def annual_sources(market, events=None):
    events = events or {}
    return {'annual_pools': {day: source_pool(market, day, events.get(day, [B]))
                             for day in market.days if day.startswith('2026-') and day <= market.day}}


def test_ten_year_prices_without_old_evidence_read_only_return_windows_and_keep_full_unknown_calendar(monkeypatch):
    market = TenYearMarket()
    # Duplicate and invalid very old quotes have no event source or return role.
    duplicate = deepcopy(market.data[A][20]); duplicate['close'] += 10
    market.data[A].append(duplicate)
    actual, narrowed, full_rows = full_history_parity(market, {}, monkeypatch)
    assert narrowed['request']['start'] == '2025-12-31'
    assert narrowed['request']['end'] == market.day
    assert narrowed['rows'] < full_rows / 8
    coverage = actual[A]['last_limit_up_coverage']
    assert coverage['since'] == market.days[0]
    assert coverage['expected_days'] == coverage['unknown_days'] == market.days.index(market.day)+1
    assert actual[A]['last_limit_up_date'] is None


@pytest.mark.parametrize('family', ['annual_pools', 'public_pools', 'reference_history', 'reference_latest', 'down_conflict'])
def test_old_year_positive_and_down_conflict_sources_are_never_trimmed_to_one_year(family, monkeypatch):
    market = TenYearMarket(); old_day = '2018-03-15'
    supplement = annual_sources(market)
    record = source_pool(market, old_day, [A], complete=False)
    if family == 'annual_pools':supplement['annual_pools'][old_day] = record
    elif family == 'public_pools':supplement['public_pools'] = {old_day: {**record, 'source': 'eastmoney-public'}}
    elif family == 'reference_history':
        market.reference_data = {'pool_history': {old_day: {'limitup': record['items'], 'coverage': {'limitup': {'complete': False}}}}}
    elif family == 'reference_latest':
        market.reference_data = {'pool_trade_date': old_day, 'limitup': record['items'], 'pool_coverage': {'limitup': {'complete': False}}}
    else:
        supplement['annual_pools'][old_day] = record
        supplement['annual_down_pools'] = {old_day: {**deepcopy(record), 'pool_kind': 'limitdown'}}
    actual, narrowed, _ = full_history_parity(market, supplement, monkeypatch)
    assert narrowed['request']['start'] == old_day
    assert actual[A]['limit_up_ytd'] == 0, 'an old-year event is not a current-year count'
    assert actual[A]['last_limit_up_date'] == (None if family == 'down_conflict' else old_day)
    if family != 'down_conflict':
        assert actual[A]['sessions_since_limit_up'] == market.days.index(market.day)-market.days.index(old_day)
        assert actual[A]['last_limit_up_coverage']['state'] == 'latest_observed'


@pytest.mark.parametrize('family', ['annual_pools', 'annual_down_pools', 'public_pools', 'reference_history'])
def test_even_an_old_empty_complete_record_preserves_its_quote_boundary_and_unknown_semantics(family, monkeypatch):
    market = TenYearMarket(); old_day = '2017-06-16'
    record = source_pool(market, old_day, [], empty=True)
    supplement = annual_sources(market)
    if family == 'reference_history':
        market.reference_data = {'pool_history': {old_day: {'limitup': [], 'coverage': {'limitup': {'complete': True}}}}}
    else:
        if family == 'annual_down_pools':record['pool_kind'] = 'limitdown'
        supplement.setdefault(family, {})[old_day] = record
    actual, narrowed, _ = full_history_parity(market, supplement, monkeypatch)
    assert narrowed['request']['start'] == old_day
    assert actual[A]['last_limit_up_date'] is None
    assert actual[A]['last_limit_up_coverage']['unknown_days'] > 2000


@pytest.mark.parametrize('change', ['duplicate_event', 'duplicate_baseline', 'missing_action_previous', 'verified_action', 'invalid_action'])
def test_current_window_duplicates_and_actions_keep_identical_price_and_event_coverage(change, monkeypatch):
    market = TenYearMarket(); event_day = '2026-10-14'
    supplement = annual_sources(market, {event_day: [A], '2026-02-20': [A]})
    if change.startswith('duplicate'):
        target = event_day if change == 'duplicate_event' else '2025-12-31'
        duplicate = deepcopy(next(row for row in market.data[A] if row['date'] == target))
        duplicate['close'] += 1; market.data[A].append(duplicate)
    else:
        market.events[A] = [action('2026-10-12', currency='USD' if change == 'invalid_action' else 'CNY')]
        if change == 'missing_action_previous':
            market.data[A] = [row for row in market.data[A] if row['date'] != '2026-10-09']
    actual, narrowed, _ = full_history_parity(market, supplement, monkeypatch)
    assert narrowed['request']['start'] == '2025-12-31'
    if change == 'verified_action':assert actual[A]['return_ytd_coverage']['price_basis'] == 'forward_event_affine'
    if change in ('missing_action_previous', 'invalid_action'):assert actual[A]['return_ytd_pct'] is None
    if change == 'duplicate_event':assert actual[A]['last_limit_up_date'] == '2026-02-20'


def test_short_historical_view_without_baselines_needs_only_business_quote_and_never_future_evidence(monkeypatch):
    market = TenYearMarket(); historical_day = market.days[9]
    supplement = {'annual_pools': {'2026-10-14': source_pool(market, '2026-10-14', [A])}}
    actual, narrowed, _ = full_history_parity(market, supplement, monkeypatch, day=historical_day)
    assert narrowed['request']['start'] == historical_day
    assert actual[A]['return_ytd_pct'] is None and actual[A]['change_30d_pct'] is None
    assert actual[A]['last_limit_up_date'] is None


def test_old_explicit_absence_source_keeps_quote_requirement_and_known_day_count(monkeypatch):
    market = TenYearMarket(); old_day = '2016-11-04'
    record = source_pool(market, old_day, [B], complete=False)
    supplement = {'annual_pools': {old_day: record}}
    # Raw codes/pagination can prove A absent only with A's own valid same-day quote.
    market.data[C] = [row for row in market.data[C] if row['date'] != old_day]
    actual, narrowed, _ = full_history_parity(market, supplement, monkeypatch)
    assert narrowed['request']['start'] == old_day
    assert actual[A]['last_limit_up_coverage']['covered_days'] == 1
    assert actual[C]['last_limit_up_coverage']['covered_days'] == 0
