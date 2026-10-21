"""Frozen event-day low termination; local immutable sources only."""
from copy import deepcopy
from contextlib import contextmanager
import pytest
from app import core
from app.domain.conditions import specs, validate, evaluate
from app.analytics.backtest_engine import availability
from app.services import entry_high, tracking
from test_workspace import ws, make_strategy, add_one, advance
from test_entry_high import snapshots, legacy_round, round_record

A = '600001.SH'
FIELD = 'close_below_entry_low'


def rule(value=True):
    return {'field': FIELD, 'op': '==', 'value': value}


def test_low_is_a_tracking_boolean_available_to_exit_and_alert_policy(ws):
    _, client = ws
    spec = next(row for row in specs() if row['field'] == FIELD)
    assert spec['type'] == 'boolean' and spec['tracking_only'] is True
    assert spec.get('alerts_only') is not True and spec['hidden_in_builder'] is True
    strategy = make_strategy(client, policy={'alerts': [rule()], 'exits': [rule()]})
    assert strategy['policy']['exits'] == [rule()]
    assert evaluate(validate(rule(False)), {FIELD: None})['result'] is None
    assert client.post('/api/v1/strategies', json={'name': '不能选股', 'definition': rule()}).status_code == 422
    assert client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={'definition': rule()}).status_code == 422
    assert availability(ws[0], {'period': 'D', 'definition': rule()})['available'] is False


def test_new_weekly_round_freezes_exact_entry_day_low_not_signal_week_or_entry_close(snapshots):
    market, client, _ = snapshots
    strategy = make_strategy(client, {'field': 'close', 'op': '>', 'value': 100})
    client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={'period': 'W'})
    strategy = client.get('/api/v1/strategies/' + strategy['strategy_id']).json()
    _, round_ = add_one(client, strategy)
    detail = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    assert detail['entry_basis']['signal_date'] == market.days[69]
    assert detail['entry_low_date'] == market.entry
    assert detail['entry_low'] == market.data[A][70]['low'] != detail['entry_price']
    assert detail['entry_basis']['entry_low'] == detail['entry_low_basis']
    assert detail['entry_low_basis']['raw_low_e8'] == round(market.data[A][70]['low'] * 100000000)


def test_equal_low_does_not_end_and_strictly_below_ends_once_in_history(snapshots):
    market, client, publish = snapshots
    low = market.data[A][70]['low']
    strategy = make_strategy(client, policy={'alerts': [], 'exits': [rule()]})
    _, round_ = add_one(client, strategy)
    market.data[A][71].update(close=low, low=low - 1)
    market.data[A][72].update(close=low - .01, low=low - 1)
    publish('market-current', 72); advance(market, 72)
    result = client.post('/api/v1/tracking-rounds/review', json={})
    assert result.status_code == 200, result.text
    assert result.json()['ended'] == 1
    detail = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    assert detail['status'] == 'condition_ended' and detail['end_business_date'] == market.days[72]
    reviews = {row['business_date']: row for row in detail['events'] if row['event_type'] == 'review'}
    assert reviews[market.days[71]]['evidence']['metrics'][FIELD] is False
    assert reviews[market.days[71]]['evidence']['metrics']['close_below_entry'] is True
    assert reviews[market.days[72]]['evidence']['metrics'][FIELD] is True
    ended = [row for row in detail['events'] if row['event_type'] == 'condition_end']
    assert len(ended) == 1
    proof = ended[0]['evidence']['exits']['checks'][0]['evidence']['entry_low']
    assert proof['raw_low'] == low and proof['market_snapshot_id'] == 'market-entry'
    assert ended[0]['evidence']['reference_entry_low_e8'] == round(low * 100000000)
    frozen = deepcopy(detail['events'])
    assert client.post('/api/v1/tracking-rounds/review', json={}).json()['reviewed'] == 0
    assert client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()['events'] == frozen
    history = client.get('/api/v1/tracking-rounds?status=condition_ended').json()
    assert round_['round_id'] in {row['round_id'] for row in history['items']}


def test_legacy_low_is_recovered_from_original_generation_without_rewriting_entry_basis(snapshots):
    market, client, publish = snapshots
    strategy = make_strategy(client, policy={'exits': [rule()]})
    old = legacy_round(client, strategy)
    before = old['entry_basis_json']; original = market.data[A][70]['low']
    market.data[A][70]['low'] = original - 50
    market.data[A][71].update(close=original - .01, low=original - 1)
    publish('market-current', 71); advance(market, 71)
    detail = client.get('/api/v1/tracking-rounds/' + old['round_id']).json()
    assert detail['entry_low'] == original
    result = client.post('/api/v1/tracking-rounds/review', json={}).json()
    assert result['ended'] == 1
    assert round_record(client, old)['entry_basis_json'] == before
    detail = client.get('/api/v1/tracking-rounds/' + old['round_id']).json()
    ended = next(row for row in detail['events'] if row['event_type'] == 'condition_end')
    assert ended['evidence']['metrics'][FIELD + '_evidence']['entry_low']['raw_low'] == original


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'wrong_close', 'corrupt', 'deleted'])
def test_legacy_unusable_source_cannot_take_a_low_from_current_generation(snapshots, mode):
    market, client, publish = snapshots
    ids, daily, _ = publish('market-invalid', 70, missing=mode == 'missing', duplicate=mode == 'duplicate', wrong_close=mode == 'wrong_close')
    old = legacy_round(client, make_strategy(client))
    if mode == 'corrupt': daily.write_bytes(b'corrupt original source')
    if mode == 'deleted': daily.unlink()
    publish('market-current', 72)
    proof = entry_high.entry_low_for(old, entry_high.load_entry_lows([old]))
    assert proof['state'] == 'unknown' and proof['raw_low'] is None
    assert proof['entry_snapshot_id'] == ids['analysis_bars']


@pytest.mark.parametrize('mode', ['actions', 'missing_actions', 'missing_quote', 'duplicate_quote', 'suspended'])
def test_unknown_price_or_action_basis_never_ends_tracking_even_when_quote_appears_below(snapshots, mode):
    market, client, publish = snapshots
    strategy = make_strategy(client, policy={'exits': [rule()]})
    _, round_ = add_one(client, strategy)
    low = market.data[A][70]['low']
    market.data[A][71].update(close=low - 1, low=low - 2)
    _, _, actions = publish('market-current', 71, actions=[market.days[71]] if mode == 'actions' else [])
    if mode == 'missing_actions': actions.unlink()
    if mode == 'missing_quote': market.data[A] = [row for row in market.data[A] if row['date'] != market.days[71]]
    if mode == 'duplicate_quote': market.data[A].append(dict(market.data[A][71]))
    if mode == 'suspended': market.data[A][71]['volume'] = 0
    advance(market, 71)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    assert response.json()['ended'] == 0
    detail = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    review = next(row for row in detail['events'] if row['event_type'] == 'review' and row['business_date'] == market.days[71])
    assert detail['status'] == 'tracking'
    assert review['evidence']['metrics'][FIELD] is None
    assert review['evidence']['metrics'][FIELD + '_reason']


def test_new_frozen_low_survives_original_artifact_loss_without_changing_high_contract(snapshots):
    _, client, _ = snapshots
    _, round_ = add_one(client, make_strategy(client))
    before = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    (core.published_dir() / 'market-entry' / 'daily.parquet').unlink()
    after = client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()
    assert after['entry_low_basis'] == before['entry_low_basis']
    assert after['entry_high_basis'] == before['entry_high_basis']
    assert after['entry_low_state'] == after['entry_high_state'] == 'ready'


@pytest.mark.parametrize('invalid_low', [0, -1, None, float('nan')])
def test_missing_or_invalid_original_low_does_not_substitute_close_or_current_low(snapshots, invalid_low):
    market, client, publish = snapshots
    original_low = market.data[A][70]['low']
    market.data[A][70]['low'] = invalid_low
    publish('market-missing-low')
    # Selection still has valid current quotes; only the retained original
    # artifact's low is absent. Do not feed invalid prices into indicators.
    market.data[A][70]['low'] = original_low
    old = legacy_round(client, make_strategy(client))
    market.data[A][70]['low'] = 119
    publish('market-current', 71)
    proof = entry_high.entry_low_for(old, entry_high.load_entry_lows([old]))
    assert proof['state'] == 'unknown' and proof['raw_low'] is None


def test_low_alert_can_notify_without_ending_when_no_exit_rule_is_configured(snapshots):
    market, client, publish = snapshots
    strategy = make_strategy(client, policy={'alerts': [rule()], 'exits': []})
    _, round_ = add_one(client, strategy)
    low = market.data[A][70]['low']
    market.data[A][71].update(close=low - .01, low=low - 1)
    publish('market-current', 71); advance(market, 71)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    assert response.json()['alerts'] == 1 and response.json()['ended'] == 0
    assert client.get('/api/v1/tracking-rounds/' + round_['round_id']).json()['status'] == 'tracking'


def test_low_source_reads_stay_outside_tracking_write_transactions(snapshots, monkeypatch):
    market, client, publish = snapshots
    connections = []; original_connect = tracking.connect; original_artifact = entry_high._artifact

    @contextmanager
    def monitored():
        with original_connect() as connection:
            connections.append(connection)
            try: yield connection
            finally: connections.remove(connection)

    def artifact(*args, **kwargs):
        assert not any(connection.in_transaction for connection in connections)
        return original_artifact(*args, **kwargs)

    monkeypatch.setattr(tracking, 'connect', monitored)
    monkeypatch.setattr(entry_high, '_artifact', artifact)
    strategy = make_strategy(client, policy={'exits': [rule()]})
    add_one(client, strategy)
    publish('market-current', 71); advance(market, 71)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
