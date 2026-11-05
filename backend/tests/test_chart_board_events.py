"""Broken/resealed display events require original pool evidence, never OHLC guesses."""
from copy import deepcopy

import pytest

from app.services.chart_limits import enrich_chart_limits
from app.services.chart_patterns import attach_intraday_board_patterns
from test_chart_limits import LimitMarket, entry, annual, flat_quote, SYMBOL


def source(*, broken=False, open_times=None):
    market = LimitMarket()
    index = 2
    row = market.rows[index]
    row.update(open=11., high=12., low=10., close=11.5, volume=100, source='fuyao')
    day = row['date']
    event = entry(market, index)
    if open_times is not None:
        event['open_times'] = open_times
    market.ref = {'pool_trade_date': day, 'breakpool' if broken else 'limitup': [event],
                  'pool_coverage': {'breakpool' if broken else 'limitup': {'complete': True}}}
    return market, index


def test_original_broken_pool_and_exact_raw_price_create_display_event_without_changing_limit_states():
    market, index = source(broken=True)
    before = deepcopy(market.ref)
    result = enrich_chart_limits(market, market.chart(), {})
    bar = result['bars'][index]
    assert bar['limit_up_state'] is None and bar['limit_down_state'] is None
    assert [event['kind'] for event in bar['pattern_events']] == ['broken_board']
    event = bar['pattern_events'][0]
    assert event['date'] == market.days[index] == event['bar_date']
    assert event['chart_price'] == event['raw_close'] == 11.5
    assert event['evidence']['closing_pool'][0]['path'] == 'reference.breakpool'
    assert market.ref == before


@pytest.mark.parametrize('damage', ['high_only', 'zero_volume', 'price_cent', 'identity', 'date', 'sealed_conflict'])
def test_high_price_or_invalid_source_never_invents_broken_board(damage):
    market, index = source(broken=True)
    event = market.ref['breakpool'][0]
    if damage == 'high_only':
        market.ref = {}
    elif damage == 'zero_volume':
        market.rows[index]['volume'] = 0
    elif damage == 'price_cent':
        event['last_price'] += .01
    elif damage == 'identity':
        event['thscode'] = '600001.SZ'
    elif damage == 'date':
        event['trade_date'] = market.days[index + 1]
    else:
        market.ref['limitup'] = [entry(market, index)]
    bar = enrich_chart_limits(market, market.chart(), {})['bars'][index]
    assert not any(event['kind'] in ('broken_board', 'resealed_board') for event in bar['pattern_events'])
    assert bar['pattern_status']['broken_board']['state'] is None


@pytest.mark.parametrize('open_times,expected', [(2, True), (0, False), (None, None), (True, None), (-1, None)])
def test_resealed_requires_explicit_positive_open_times_in_verified_sealed_pool(open_times, expected):
    market, index = source(open_times=open_times)
    bar = enrich_chart_limits(market, market.chart(), {})['bars'][index]
    assert bar['limit_up_state'] is True
    assert bar['pattern_status']['resealed_board']['state'] is expected
    assert any(event['kind'] == 'resealed_board' for event in bar['pattern_events']) is (expected is True)


def test_conflicting_open_counts_do_not_guess_reseal_and_week_month_keep_daily_dates():
    market, index = source(open_times=2)
    supplement = {'public_pools': {market.days[index]: {'date': market.days[index], 'source': 'eastmoney-public',
                      'items': [entry(market, index, open_times=0)]}}}
    bar = enrich_chart_limits(market, market.chart(), supplement)['bars'][index]
    assert bar['limit_up_state'] is True and bar['pattern_status']['resealed_board']['state'] is None
    raw = enrich_chart_limits(market, market.chart(), {})['pattern_events']
    forward = enrich_chart_limits(market, market.chart(price_basis='forward'), {})['pattern_events']
    assert next(row for row in forward if row['kind'] == 'resealed_board')['chart_price'] == 11.5 / 2
    assert next(row for row in raw if row['kind'] == 'resealed_board')['raw_close'] == 11.5
    for period in ('W', 'M'):
        chart = market.chart(period=period)
        if period == 'M':
            from app.analytics.monthly import aggregate_monthly
            chart['bars'] = aggregate_monthly(market.rows, market.days, market.days[-1])
        result = enrich_chart_limits(market, chart, {})
        event = next(row for row in result['pattern_events'] if row['kind'] == 'resealed_board')
        assert event['date'] == market.days[index] and event['chart_price'] is None
        bar = next(row for row in result['bars'] if row['date'] == event['bar_date'])
        assert bar['pattern_summary']['resealed_board']['count'] == 1
    assert not enrich_chart_limits(market, market.chart(as_of=market.days[index - 1]), {})['pattern_events']


def test_intraday_pattern_uses_current_pool_sha_and_does_not_call_sealed_a_reseal():
    from test_intraday_board_changes import frame, A

    quote = {'instrument_id': A, 'latest_price': 11., 'amount': 1000, 'volume': None}
    snapshot = frame('1430', [], [A])
    attach_intraday_board_patterns(quote, snapshot, content_sha256='a' * 64)
    assert quote['limit_up_state'] == 'broken'
    assert quote['pattern_events'][0]['kind'] == 'broken_board'
    assert quote['pattern_events'][0]['evidence']['snapshot_sha256'] == 'a' * 64
    quote = {'instrument_id': A, 'latest_price': 11., 'amount': 1000}
    snapshot = frame('1430', [A])
    attach_intraday_board_patterns(quote, snapshot, content_sha256='b' * 64)
    assert quote['limit_up_state'] == 'sealed' and not quote['pattern_events']
    attach_intraday_board_patterns(quote, snapshot, content_sha256='b' * 64, resealed=True)
    assert quote['board_transition'] == 'resealed' and quote['pattern_events'][0]['kind'] == 'resealed_board'
    conflicted = frame('1430', [A], [A])
    attach_intraday_board_patterns(quote, conflicted, content_sha256='c' * 64, resealed=True)
    assert quote['limit_up_state'] == 'unknown' and not quote['pattern_events']


def test_intraday_quote_endpoint_exposes_only_current_checked_pool_pattern(monkeypatch):
    from contextlib import nullcontext
    from hashlib import sha256
    from types import SimpleNamespace
    import json
    import sqlite3
    from app.services import intraday_stock_detail as detail
    from test_intraday_board_changes import frame, A, DAY

    snapshot = frame('1430', [], [A])
    snapshot.update(id='intraday_' + '1' * 32, slot_label='尾盘观察')
    snapshot['comparison_input']['stocks'][0].update(open=10., high=11., low=10., amount=1000.)
    raw = json.dumps(snapshot)
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.execute('CREATE TABLE market_intraday_capture(snapshot_id,payload_json,content_sha256,trade_date,slot)')
    connection.execute('INSERT INTO market_intraday_capture VALUES(?,?,?,?,?)',
                       (snapshot['id'], raw, sha256(raw.encode()).hexdigest(), DAY, '1430'))
    monkeypatch.setattr(detail, 'connect', lambda: nullcontext(connection))
    monkeypatch.setattr(detail, 'get_market', lambda: SimpleNamespace(calendar=lambda: ['2026-11-04', DAY]))
    monkeypatch.setattr(detail, 'suspension_status', lambda *_: None)
    value = detail.read_quote(snapshot['id'], A)
    assert value['quote']['pattern_events'][0]['kind'] == 'broken_board'
    assert connection.execute('SELECT payload_json FROM market_intraday_capture').fetchone()[0] == raw
    connection.close()
