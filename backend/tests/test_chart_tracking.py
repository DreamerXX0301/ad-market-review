"""Search-open charts discover existing frozen references without new research."""
import pytest
from app import core
from app.routers.platform import router
from app.services import chart_tracking
from test_workspace import ws, make_strategy, add_one, advance


@pytest.fixture
def baselines(ws):
    market, client = ws
    client.app.include_router(router)
    first = make_strategy(client, {'field': 'close', 'op': '>', 'value': 100})
    _, older = add_one(client, first)
    advance(market, 75)
    second = make_strategy(client, {'field': 'close', 'op': '>', 'value': 101})
    _, recent = add_one(client, second)
    return market, client, older, recent


def test_search_loads_own_frozen_rounds_without_market_reads_or_writes(baselines, monkeypatch):
    market, client, older, recent = baselines
    monkeypatch.setattr(market, 'panel', lambda *a, **k: pytest.fail('No market history is needed'))
    monkeypatch.setattr(market, 'history_panel', lambda *a, **k: pytest.fail('No full panel is needed'))
    with core.connect() as connection:
        counts = [connection.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('job', 'tracking_round', 'tracking_event')]
    result = client.get('/api/v1/instruments/600001.sh/tracking-baselines').json()
    assert result['instrument_id'] == '600001.SH'
    assert result['default_round_id'] == recent['round_id']
    assert [row['round_id'] for row in result['items']] == [recent['round_id'], older['round_id']]
    assert result['items'][1]['entry_price'] == older['entry_price']
    assert result['items'][0]['entry_basis']['price_basis'] == 'none'
    assert client.get('/api/v1/instruments/000001.SZ/tracking-baselines').json()['items'] == []
    with core.connect() as connection:
        assert counts == [connection.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('job', 'tracking_round', 'tracking_event')]


def test_observation_date_excludes_later_entry_and_exit(baselines):
    market, client, older, recent = baselines
    assert client.post('/api/v1/tracking-rounds/'+older['round_id']+'/remove', json={}).status_code == 200
    earlier = client.get('/api/v1/instruments/600001.SH/tracking-baselines', params={'as_of': market.entry}).json()
    assert [row['round_id'] for row in earlier['items']] == [older['round_id']]
    assert earlier['items'][0]['status'] == 'tracking'
    assert earlier['items'][0]['end_business_date'] is None
    current = client.get('/api/v1/instruments/600001.SH/tracking-baselines').json()
    assert current['default_round_id'] == recent['round_id']
    assert current['items'][1]['status'] == 'manual_removed'
    assert client.get('/api/v1/instruments/600001.SH/tracking-baselines', params={'as_of': market.days[69]}).json()['items'] == []


def test_bounded_history_and_deterministic_active_default(baselines, monkeypatch):
    _, client, _, recent = baselines
    monkeypatch.setattr(chart_tracking, 'MAX_BASELINES', 1)
    result = client.get('/api/v1/instruments/600001.SH/tracking-baselines').json()
    assert result['total'] == 2 and result['truncated'] is True
    assert result['default_round_id'] == recent['round_id'] and len(result['items']) == 1


@pytest.mark.parametrize('day', ['2026-02-30', 'not-a-date', '2099-01-01'])
def test_invalid_and_future_dates_do_not_load_references(baselines, day):
    _, client, _, _ = baselines
    assert client.get('/api/v1/instruments/600001.SH/tracking-baselines', params={'as_of': day}).status_code == 422
