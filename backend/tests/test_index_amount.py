"""Index cards compare CNY turnover on exact adjacent market sessions only."""
from copy import deepcopy

import pytest

from app.analytics.market import INDEX_NAMES, MarketData
from app.services import market_service


DAYS = ['2026-10-16', '2026-10-19', '2026-10-20']


def bar(day, amount, close=100.):
    return {'date': day, 'open': close, 'high': close+1, 'low': close-1,
            'close': close, 'volume': 100., 'amount': amount,
            'source': 'fixture', 'price_basis': 'none', 'corporate_action': False}


@pytest.fixture
def market(tmp_path, monkeypatch):
    result = MarketData(tmp_path)
    result.fixture_reference = {
        'calendar': DAYS,
        'indices': {symbol: [bar(DAYS[0], 1e8), bar(DAYS[1], 2e8), bar(DAYS[2], 3e8, 110.)]
                    for symbol in INDEX_NAMES},
        'master': {symbol: {'instrument_id': symbol, 'name': name, 'kind': 'index'}
                   for symbol, name in INDEX_NAMES.items()},
    }
    monkeypatch.setattr(result, '_reference', lambda: result.fixture_reference)
    monkeypatch.setattr(result, 'status', lambda: {
        'ready': True, 'snapshot_id': 'fixture', 'data_begin': DAYS[0],
        'data_end': DAYS[-1], 'quality': 'partial',
    })
    monkeypatch.setattr(result, '_daily_frame', lambda: pytest.fail('index quotes cannot load all-stock history'))
    return result


@pytest.mark.parametrize('current,previous,expected', [
    (5e8, 2e8, 3e8), (1e8, 2e8, -1e8), (2e8, 2e8, 0.), (2e8, 0., 2e8),
])
def test_index_signed_delta_is_currency_not_price_return(market, current, previous, expected):
    rows = market.fixture_reference['indices']['000001.SH']
    rows[-2]['amount'] = previous
    rows[-1]['amount'] = current
    quote = market.index_quotes(DAYS[-1])[0]
    assert quote['amount_change'] == expected
    assert quote['amount_previous'] == previous
    assert quote['amount_previous_date'] == DAYS[-2]
    assert quote['amount_change_reason'] is None and quote['amount_unit'] == 'CNY'
    assert quote['amount'] == current
    assert quote['change_pct'] == pytest.approx(10.), 'price change remains separate from turnover'


@pytest.mark.parametrize('mode', [
    'no_previous', 'duplicate_previous', 'duplicate_current', 'missing_current',
    'current_none', 'current_nan', 'current_bool', 'current_negative',
    'previous_none', 'previous_infinite', 'previous_bool', 'previous_negative',
    'different_source', 'different_currency',
])
def test_unverifiable_adjacent_index_turnover_remains_unknown(market, mode):
    rows = market.fixture_reference['indices']['000001.SH']
    if mode == 'no_previous': rows.pop(1)
    elif mode == 'duplicate_previous': rows.append(deepcopy(rows[1]))
    elif mode == 'duplicate_current': rows.append(deepcopy(rows[-1]))
    elif mode == 'missing_current': rows.pop()
    elif mode == 'current_none': rows[-1]['amount'] = None
    elif mode == 'current_nan': rows[-1]['amount'] = float('nan')
    elif mode == 'current_bool': rows[-1]['amount'] = True
    elif mode == 'current_negative': rows[-1]['amount'] = -1.
    elif mode == 'previous_none': rows[1]['amount'] = None
    elif mode == 'previous_infinite': rows[1]['amount'] = float('inf')
    elif mode == 'previous_bool': rows[1]['amount'] = False
    elif mode == 'previous_negative': rows[1]['amount'] = -1.
    elif mode == 'different_source': rows[1]['source'] = 'other'
    else: rows[1]['currency'] = 'USD'
    quote = market.index_quotes(DAYS[-1])[0]
    assert quote['amount_change'] is None and quote['amount_change_reason']
    assert quote['amount_previous_date'] == DAYS[-2]
    assert quote['amount_previous'] is None


def test_index_dates_ignore_future_quotes_and_handle_weekend_adjacent_sessions(market):
    rows = market.fixture_reference['indices']['000001.SH']
    rows.append(bar('2026-10-21', 999e8, 999.))
    quote = market.index_quotes('2026-10-19')[0]
    assert quote['date'] == '2026-10-19'
    assert quote['amount_change'] == 1e8
    assert quote['amount_previous_date'] == '2026-10-16', 'previous market session, not previous calendar day'
    first = market.index_quotes(DAYS[0])[0]
    assert first['amount_change'] is None and first['amount_previous_date'] is None
    assert first['amount_change_reason']
    non_session = market.index_quotes('2026-10-18')[0]
    assert non_session['amount_change'] is None and non_session['amount_previous_date'] is None


def test_index_deltas_are_independent_and_never_sum_overlapping_indices(market):
    for i, symbol in enumerate(INDEX_NAMES):
        market.fixture_reference['indices'][symbol][-1]['amount'] = (i+3)*1e8
    quotes = market.index_quotes()
    assert [row['amount_change'] for row in quotes] == [1e8, 2e8, 3e8, 4e8]
    assert all('不能相加' in row['amount_change_scope'] for row in quotes)


@pytest.mark.parametrize('calendar', [DAYS[::-1], DAYS+[DAYS[-1]], DAYS[:-1]])
def test_unproved_calendar_cannot_identify_an_adjacent_pair(market, calendar):
    market.fixture_reference['calendar'] = calendar
    quote = market.index_quotes(DAYS[-1])[0]
    assert quote['amount_change'] is None and quote['amount_change_reason']


def test_market_overview_uses_real_index_quotes_contract(market, monkeypatch):
    day = DAYS[-1]
    monkeypatch.setattr(market, 'indices', lambda *_: pytest.fail('overview must use enhanced lightweight index_quotes'))
    monkeypatch.setattr(market, 'turnover_history', lambda *_: [])
    monkeypatch.setattr(market_service, 'panel', lambda *_: (market, market.status(), day, {}, []))
    monkeypatch.setattr(market_service, 'cached_gainers', lambda *_: ([], {}))
    result = market_service.overview(day)
    assert len(result['indices']) == 4
    assert result['indices'][0]['amount_change'] == 1e8
    assert result['total_amount'] == 0, 'overlapping index turnover is never used as the market total'


@pytest.mark.parametrize('day', DAYS)
def test_index_page_projection_matches_overview_without_any_stock_or_supplement_read(market, monkeypatch, day):
    monkeypatch.setattr(market_service, 'get_market', lambda: market)
    monkeypatch.setattr(market_service, 'panel', lambda *_: pytest.fail('index page cannot build a stock panel'))
    monkeypatch.setattr(market_service, 'cached_gainers', lambda *_: pytest.fail('index page cannot rank stocks'))
    monkeypatch.setattr(market_service, 'load_supplement', lambda *_: pytest.fail('index page cannot expand source histories'))
    monkeypatch.setattr(market_service, 'load_supplement_with_fingerprint', lambda *_: pytest.fail('index page cannot inspect supplement identities'))
    expected = market.index_quotes(day)
    result = market_service.index_overview(day)
    assert result['indices'] == expected
    assert result['date'] == day and result['snapshot_id'] == 'fixture'
    assert result['data_coverage'] == market_service.data_coverage('overview', market.status(), day, market.reference(), DAYS)
    assert set(result) == {'date', 'snapshot_id', 'indices', 'data_coverage', 'quality'}
    assert market_service.index_overview()['date'] == DAYS[-1]


def test_explicit_indices_route_precedes_market_catchall_and_rejects_invalid_dates(market, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(market_service, 'get_market', lambda: market)
    monkeypatch.setattr(market_service, 'market_page', lambda *_a, **_kw: pytest.fail('literal index route fell through'))
    client = TestClient(app)
    response = client.get('/api/v1/market/indices', params={'date': DAYS[1]})
    assert response.status_code == 200
    assert response.json() == market_service.index_overview(DAYS[1])
    for day in ('2026-10-18', '2026-10-21', '2025-10-19', 'not-a-date'):
        assert client.get('/api/v1/market/indices', params={'date': day}).status_code == 422
    monkeypatch.setattr(market, 'status', lambda: {'ready': False})
    assert client.get('/api/v1/market/indices').status_code == 409
