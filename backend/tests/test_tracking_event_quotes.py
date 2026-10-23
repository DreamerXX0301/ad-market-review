"""Alert browsing values each event on its own session in a single batch."""
import pytest

from test_workspace import ws, make_strategy, add_one, advance
from app import core
from app.domain.conditions import metrics_for
from app.services.workspace_common import insert, js


def seed_alert(round_, day, number):
    with core.connect() as connection:
        insert(connection, 'tracking_event', {
            'event_id': f'fixture-alert-{number}',
            'event_key': f'fixture-alert-key-{number}',
            'round_id': round_['round_id'], 'event_type': 'alert',
            'business_date': day, 'data_snapshot_id': 'bars',
            'data_state': 'ready', 'outcome': 'satisfied',
            'trading_age': max(1, number), 'evidence_json': js({'policy_version': 1, 'metrics': {'close': 999, 'change_pct': 999}}),
            'recorded_at_ms': core.now_ms()+number,
        })


def batch_reader(monkeypatch, market):
    calls = []
    def read(symbols, start=None, end=None):
        calls.append((symbols, start, end))
        return {symbol: [dict(row) for row in market.data[symbol] if start <= row['date'] <= end]
                for symbol in symbols}
    monkeypatch.setattr(market, 'history_for_instruments', read, raising=False)
    monkeypatch.setattr(market, 'actions_for_instruments', lambda symbols: {symbol: [] for symbol in symbols}, raising=False)
    return calls


def test_events_batch_exact_trigger_dates_and_preserve_entry_baseline(ws, monkeypatch):
    market, client = ws
    strategy = make_strategy(client)
    _, round_ = add_one(client, strategy)
    advance(market, 76)
    days = [market.days[71], market.days[73]]
    for number, day in enumerate([days[0], days[1], days[1]]):
        seed_alert(round_, day, number)
    # Ending the round must not move earlier alert quotes to the end date.
    removed = client.post('/api/v1/tracking-rounds/'+round_['round_id']+'/remove', json={})
    assert removed.status_code == 200
    calls = batch_reader(monkeypatch, market)
    response = client.get('/api/v1/tracking-events?event_type=alert')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['total'] == 3 and len(calls) == 1
    assert calls[0] == ([round_['instrument_id']], market.days[70], market.days[73])
    for event in payload['items']:
        expected = metrics_for(market.data[round_['instrument_id']], event['business_date'], market.days)
        assert event['today_change_pct'] == pytest.approx(expected['change_pct'])
        assert event['observed_close'] == expected['close']
        assert event['valuation_date'] == event['business_date']
        assert event['entry_business_date'] == round_['entry_business_date']
        assert event['entry_price'] == round_['entry_price']
        assert event['entry_basis']['entry_price_date'] == round_['entry_business_date']
        assert event['evidence']['metrics']['change_pct'] == 999
        assert 'entry_price_e8' not in event and 'entry_basis_json' not in event


@pytest.mark.parametrize('case', ['previous_missing', 'current_missing', 'actions_unknown'])
def test_event_return_never_reuses_another_day_or_unproven_action(ws, monkeypatch, case):
    market, client = ws
    _, round_ = add_one(client, make_strategy(client))
    date = market.days[72]
    advance(market, 76)
    seed_alert(round_, date, 1)
    calls = batch_reader(monkeypatch, market)
    if case == 'previous_missing':
        market.data[round_['instrument_id']] = [row for row in market.data[round_['instrument_id']] if row['date'] != market.days[71]]
    elif case == 'current_missing':
        market.data[round_['instrument_id']] = [row for row in market.data[round_['instrument_id']] if row['date'] != date]
    else:
        monkeypatch.setattr(market, 'actions_for_instruments', lambda symbols: {})
    event = client.get('/api/v1/tracking-events?event_type=alert').json()['items'][0]
    assert event['today_change_pct'] is None
    assert event['valuation_date'] == date
    if case == 'current_missing':
        assert event['observed_close'] is None
    else:
        assert event['observed_close'] > 0
    assert len(calls) == 1


def test_no_events_does_not_read_market_quotes(ws, monkeypatch):
    market, client = ws
    calls = batch_reader(monkeypatch, market)
    response = client.get('/api/v1/tracking-events?event_type=alert').json()
    assert response['items'] == [] and response['total'] == 0
    assert calls == []


def test_tab_counts_are_strategy_scoped_before_status_and_trigger_filters(ws):
    _, client = ws
    first_strategy = make_strategy(client)
    _, first = add_one(client, first_strategy)
    _, second = add_one(client, make_strategy(client))
    client.post('/api/v1/tracking-rounds/'+second['round_id']+'/remove', json={})
    active = client.get('/api/v1/tracking-rounds?status=tracking&triggered_only=true').json()
    assert active['items'] == []
    assert active['counts'] == {'active': 1, 'history': 1}
    scoped = client.get('/api/v1/tracking-rounds?status=all&strategy_id='+first_strategy['strategy_id']).json()
    assert scoped['counts'] == {'active': 1, 'history': 0}
    assert scoped['items'][0]['round_id'] == first['round_id']
