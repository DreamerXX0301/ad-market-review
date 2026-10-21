"""N-market-session high conditions keep a daily window in daily/weekly workflows."""
import pytest

from app.analytics.backtest_engine import availability, prepare, warmup
from app.analytics.indicators import aggregate_weekly
from app.analytics.rolling_high import rolling_high_state
from app.domain.conditions import evaluate, metrics_for, specs, validate
from test_workspace import FixtureMarket, ws, make_strategy

FIELD = 'close_breakout_high'
A = '600001.SH'


def rule(lookback=60): return {'field': FIELD, 'op': 'new_high', 'value': lookback}


def test_new_high_parameter_contract_and_window_validation():
    spec = next(row for row in specs() if row['field'] == FIELD)
    assert spec['type'] == 'number' and spec['operators'] == ['new_high']
    assert spec['min'] == 1 and spec['max'] == 750 and spec['integer'] is True
    assert spec['parameter_kind'] == 'lookback_sessions' and spec['constant_only'] is True
    for value in [0, -1, 751, 1.5, True, '60', None]:
        with pytest.raises(ValueError): validate(rule(value))
    for value in (1, 60, 750): assert validate(rule(value))['value'] == value
    with pytest.raises(ValueError): validate({'field': FIELD, 'op': '>=', 'value': 60})
    with pytest.raises(ValueError): validate({'field': FIELD, 'op': 'new_high', 'right_field': 'close'})
    with pytest.raises(ValueError): validate({'field': 'close', 'op': '>', 'right_field': FIELD})


@pytest.mark.parametrize('available,blocker,valid,expected', [
    (60, None, True, True), (60, 61, True, True), (60, 60, True, False),
    (60, 1, True, False), (59, None, True, None), (59, 1, True, None),
    (60, None, False, None), (True, None, True, None),
    (60, False, True, None), (60, -1, True, None),
])
def test_strict_breakout_and_unknown_window_states(available, blocker, valid, expected):
    metrics = {'breakout_previous_sessions': available, 'breakout_blocking_distance': blocker,
               'breakout_valid': valid, 'new_high_current_close': 20}
    result = evaluate(validate(rule()), metrics)
    assert result['result'] is expected and result['actual'] is expected
    assert result['lookback_sessions'] == 60 and result['current_close'] == 20
    assert result['covered_sessions'] == (available if type(available) is int else None)
    if expected is None: assert result['reason']


@pytest.mark.parametrize('price', [None, 0, -1, True, '20', float('inf'), float('nan')])
def test_invalid_current_price_cannot_be_hidden_by_a_true_proof_flag(price):
    result = evaluate(validate(rule()), {'breakout_valid': True, 'breakout_previous_sessions': 60,
                      'breakout_blocking_distance': None, 'new_high_current_close': price})
    assert result['result'] is None and result['reason']


def test_weekly_strategy_condition_still_uses_observation_day_daily_window():
    market = FixtureMarket(); rows = market.bars(A, market.entry)
    rows[-1].update(close=200, high=201)
    state = metrics_for(rows, market.entry, market.days, 'W')
    assert state['observed_date'] == market.days[69] # Other technical indicators remain weekly.
    assert state['new_high_current_close'] == 200
    assert state['breakout_previous_sessions'] == 70
    assert evaluate(validate(rule()), state)['result'] is True


@pytest.mark.parametrize('mode', ['missing_previous', 'zero_volume', 'missing_action_coverage'])
def test_daily_window_does_not_skip_suspension_gap_or_missing_actions(mode):
    market = FixtureMarket(); rows = market.bars(A, market.entry)
    rows[-1].update(close=200, high=201)
    if mode == 'missing_previous': rows = rows[:-2] + rows[-1:]
    if mode == 'zero_volume': rows[-2]['volume'] = 0
    if mode == 'missing_action_coverage': rows[-1]['corporate_action_coverage_known'] = False
    for period in ('D', 'W'):
        metrics = metrics_for(rows, market.entry, market.days, period)
        assert evaluate(validate(rule()), metrics)['result'] is None


def test_fast_strategy_uses_one_vector_read_without_history_panel(ws, monkeypatch):
    market, client = ws
    market.data[A][70].update(close=200, high=201)
    calls = []
    def vector(day):
        calls.append(day)
        return {symbol: rolling_high_state(rows, market.days, day) for symbol, rows in market.data.items()}
    monkeypatch.setattr(market, 'breakout_states', vector, raising=False)
    monkeypatch.setattr(market, 'history_panel', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('no full history materialization')))
    strategy = make_strategy(client, rule())
    response = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert calls == [market.entry]
    candidate = next(row for row in result['items'] if row['instrument_id'] == A)
    assert candidate['decision'] == 'matched'
    assert candidate['checks']['lookback_sessions'] == 60
    assert candidate['checks']['actual'] is True
    assert candidate['metrics']['breakout_previous_sessions'] == 70


def test_weekly_backtest_separates_daily_window_from_weekly_ma_warmup():
    market = FixtureMarket()
    # This fixture isolates indicator warmup; listing eligibility is tested
    # separately and requires an explicit, valid identity for backtesting.
    base_instruments = market.instruments
    market.instruments = lambda limit=10000: [{**row, 'list_date': '2020-01-01'} for row in base_instruments(limit=limit)]
    strategy = {'period': 'W', 'definition': rule()}
    bounds = availability(market, strategy)
    assert bounds['available'] is True # 130 daily sessions suffice; 60 weeks are not required.
    assert bounds['min_date'] == market.days[60]
    assert bounds['daily_lookback_sessions'] == 60 and bounds['warmup_bars'] == 2
    assert warmup(validate(rule())) == 61
    long = availability(market, {'period': 'W', 'definition': rule(750)})
    assert long['available'] is False and '750' in long['reason']
    combined = availability(market, {'period': 'W', 'definition': {'all': [rule(), {'field': 'close', 'op': '>', 'right_field': 'ma30'}]}})
    assert combined['available'] is False # Weekly MA30 still needs 30 complete weeks.


def test_weekly_backtest_signals_copy_daily_state_at_that_week_signal_date():
    market = FixtureMarket()
    market.data[A][69].update(close=200, high=201)
    base_instruments = market.instruments
    market.instruments = lambda limit=10000: [{**row, 'list_date': '2020-01-01'} for row in base_instruments(limit=limit)]
    market.status = lambda: {'symbols': len(market.data)}
    market.iter_histories = lambda end: ((symbol, [dict(row) for row in rows if row['date'] <= end]) for symbol, rows in market.data.items())
    strategy = {'period': 'W', 'definition': rule()}
    _, _, signals, quality = prepare(market, strategy, market.days[65], market.days[90])
    found = next(row for row in signals if row['instrument_id'] == A and row['date'] == market.days[69])
    assert found['metrics']['breakout_previous_sessions'] == 69
    assert found['metrics']['new_high_current_close'] == 200
    assert found['checks']['lookback_sessions'] == 60 and found['checks']['result'] is True
