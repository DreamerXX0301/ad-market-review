"""Manual local preview and atomic, idempotent, unarmed stock/index saving."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from app import core
from app.services import chart_annotations as marks, line_alerts
from app.routers.chart_annotations import router
from test_workspace import ws
from test_support_resistance import bars, chart


@pytest.fixture
def levels(ws, monkeypatch):
    market, client = ws
    client.app.include_router(router)
    monkeypatch.setattr(marks, 'get_market', lambda: market)
    market.data['600001.SH'] = bars(120)
    market.data['000001.SH'] = deepcopy(market.data['600001.SH'])
    market.days = [row['date'] for row in market.data['600001.SH']]
    monkeypatch.setattr(market, 'instruments', lambda **_: [
        {'instrument_id': '600001.SH', 'kind': 'stock'}, {'instrument_id': '000001.SH', 'kind': 'index'}])
    monkeypatch.setattr(market, 'status', lambda: {'data_end': market.days[-1], 'snapshot_id': 'fixture-levels'}, raising=False)
    def local_prices(symbol, as_of=None, price_basis='none'):
        rows = [dict(row) for row in market.data[symbol] if row['date'] <= as_of]
        return rows, {'usable': True}
    monkeypatch.setattr(market, 'prices', local_prices, raising=False)
    monkeypatch.setattr(market, 'chart', lambda *_args, **_kwargs: pytest.fail('automatic levels must not build all-market chart breadth'), raising=False)
    return market, client


def preview(client, symbol='600001.SH', **parameters):
    response = client.post(f'/api/v1/instruments/{symbol}/annotations/recommendations', json=parameters)
    assert response.status_code == 200, response.text
    return response.json()


def save(client, value, **changes):
    payload = {'parameters': value['parameters'], 'preview_id': value['preview_id'],
               'candidate_ids': [item['candidate_id'] for item in value['items']], **changes}
    return client.post(f"/api/v1/instruments/{value['instrument_id']}/annotations/recommendations/save", json=payload)


@pytest.mark.parametrize('symbol', ['600001.SH', '000001.SH'])
def test_preview_does_not_write_and_save_is_persistent_atomic_and_idempotent(levels, symbol):
    market, client = levels
    original = deepcopy(market.data)
    with core.connect() as connection:
        initial_jobs = connection.execute('SELECT COUNT(*) FROM job').fetchone()[0]
    value = preview(client, symbol)
    assert value['items'] and value['parameters']['snapshot_id'] == 'fixture-levels'
    assert client.get(f'/api/v1/instruments/{symbol}/annotations').json()['items'] == []
    response = save(client, value)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['created'] and not saved['reused'] and saved['alerts_enabled'] is False
    for line in saved['created']:
        assert line['alert_modes'] == [] and line['auto_watchlist'] is False
        assert line['origin']['snapshot_id'] == 'fixture-levels'
        assert line['origin']['as_of'] == market.days[-1]
        assert line['origin']['evidence']
    persisted = client.get(f'/api/v1/instruments/{symbol}/annotations').json()['items']
    by_id = lambda items: {item['annotation_id']: item for item in items}
    assert by_id(persisted) == by_id(saved['created'])
    second = save(client, value).json()
    assert not second['created'] and by_id(second['reused']) == by_id(persisted)
    assert all(item['saved'] for item in preview(client, symbol)['items'])
    assert market.data == original
    with core.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM job').fetchone()[0] == initial_jobs
        assert not line_alerts.active_rules(connection)
        assert connection.execute('SELECT COUNT(*) FROM strategy').fetchone()[0] == 0


def test_recalculate_does_not_overwrite_manual_or_edited_saved_lines(levels):
    _, client = levels
    manual = client.post('/api/v1/instruments/600001.SH/annotations', json={'price': 80, 'label': '手工支撑'}).json()
    value = preview(client)
    created = save(client, value).json()['created'][0]
    response = client.patch('/api/v1/instruments/600001.SH/annotations/' + created['annotation_id'], json={'price': created['price'] - 1})
    assert response.status_code == 200, response.text
    assert response.json()['origin']['manual_override'] is True
    assert save(client, preview(client)).json()['created'] == []
    result = client.get('/api/v1/instruments/600001.SH/annotations').json()['items']
    assert next(item for item in result if item['annotation_id'] == manual['annotation_id']) == manual
    assert next(item for item in result if item['annotation_id'] == created['annotation_id'])['price'] == created['price'] - 1


@pytest.mark.parametrize('payload', [
    {'period': 'X'}, {'lookback': 19}, {'lookback': 241}, {'lookback': True},
    {'lookback': 30.5}, {'as_of': '2099-01-01'}, {'as_of': '2025-02-30'},
    {'as_of': '2025-01-04'}, {'snapshot_id': 'different'}, {'price_basis': 'backward'},
    {'unexpected': 1},
])
def test_invalid_or_stale_context_never_generates_or_saves(levels, payload):
    _, client = levels
    response = client.post('/api/v1/instruments/600001.SH/annotations/recommendations', json=payload)
    assert response.status_code in (409, 422)
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['total'] == 0


def test_tampered_preview_unknown_candidate_and_atomic_line_limit(levels, monkeypatch):
    _, client = levels
    value = preview(client)
    assert save(client, value, preview_id='tampered').status_code == 409
    assert save(client, value, candidate_ids=['unknown']).status_code == 409
    assert save(client, value, candidate_ids=[]).status_code == 422
    assert save(client, value, candidate_ids=[value['items'][0]['candidate_id']] * 2).status_code == 422
    monkeypatch.setattr(marks, 'MAX_LINES', 1)
    assert len(value['items']) > 1
    assert save(client, value).status_code == 409
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['total'] == 0


def test_index_allows_quiet_manual_lines_but_cannot_activate_stock_rules(levels):
    _, client = levels
    path = '/api/v1/instruments/000001.SH/annotations'
    saved = client.post(path, json={'price': 3000, 'line_type': 'support'}).json()
    assert saved['price_basis'] == 'none'
    for payload in ({'alert_modes': ['support_reclaim']}, {'auto_watchlist': True}):
        assert client.patch(path + '/' + saved['annotation_id'], json=payload).status_code == 422
        assert client.post(path, json={'price': 3000, 'line_type': 'support', **payload}).status_code == 422
    assert client.post(path, json={'price': 3000, 'price_basis': 'forward_event_affine', 'anchor_date': '2025-01-06'}).status_code == 422
    assert client.post(path + '/recommendations', json={'price_basis': 'forward'}).status_code == 422
    with core.connect() as connection:
        assert connection.execute('SELECT instrument_kind FROM chart_annotation WHERE annotation_id=?', (saved['annotation_id'],)).fetchone()[0] == 'index'
        # Storage invariant also blocks a bypass of the API service.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE chart_annotation SET alert_modes_json='[\"support_reclaim\"]' WHERE annotation_id=?", (saved['annotation_id'],))
        assert not line_alerts.active_rules(connection)


def test_empty_candidates_preserve_reason_and_no_side_effects(levels, monkeypatch):
    market, client = levels
    market.data['600001.SH'] = market.data['600001.SH'][:4]
    value = preview(client)
    assert value['items'] == [] and value['reason']
    assert save(client, value).status_code == 422


def test_market_generation_change_during_compute_fails_closed(levels, monkeypatch):
    market, client = levels
    calls = [0]
    def status():
        calls[0] += 1
        return {'data_end': market.days[-1], 'snapshot_id': 'fixture-levels' if calls[0] == 1 else 'changed'}
    monkeypatch.setattr(market, 'status', status)
    response = client.post('/api/v1/instruments/600001.SH/annotations/recommendations', json={})
    assert response.status_code == 409


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('basis', ['none', 'forward'])
def test_current_period_and_price_basis_are_explicit_and_saved_with_provenance(levels, period, basis):
    market, client = levels
    value = preview(client, period=period, price_basis=basis, as_of=market.days[-1], lookback=60)
    assert value['parameters']['period'] == period and value['parameters']['lookback'] == 60
    assert value['parameters']['price_basis'] == basis
    assert value['reference_date'] == market.days[-1]
    assert value['reference_price'] == market.data['600001.SH'][-1]['close']
    for item in value['items']:
        assert item['origin']['period'] == period
        assert item['origin']['as_of'] == market.days[-1]
        assert item['anchor_date'] == (market.days[-1] if basis == 'forward' else None)
        assert item['price_basis'] == ('forward_event_affine' if basis == 'forward' else 'none')
        assert all(e['confirmed_on'] <= market.days[-1] for e in item['evidence'])
    if value['items']:
        assert save(client, value).status_code == 200
    else:
        assert value['reason']


def test_forward_basis_failure_does_not_silently_store_raw_candidate(levels, monkeypatch):
    market, client = levels
    prices = market.prices
    monkeypatch.setattr(market, 'prices', lambda *args, **kwargs: (prices(*args, **kwargs)[0], {'usable': False}))
    response = client.post('/api/v1/instruments/600001.SH/annotations/recommendations', json={'price_basis': 'forward'})
    assert response.status_code == 409
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['items'] == []


def test_concurrent_same_save_reuses_origin_keys_without_duplicate_marks(levels):
    _, client = levels
    value = preview(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: save(client, value), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert sum(len(response.json()['created']) for response in responses) == len(value['items'])
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['total'] == len(value['items'])
