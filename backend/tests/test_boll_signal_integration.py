"""Independent BOLL integration checks: frozen inputs, caches and workflow parity."""
from copy import deepcopy
from datetime import date, timedelta
import json
from math import sin

import pytest

from app.analytics import boll_conditions
from app.analytics.backtest_engine import prepare, warmup
from app.domain.conditions import evaluate, metrics_for, specs, validate
from app.domain.history_events import HistoryContext
from app.services.strategy_periods import add_period_metrics, cached_metrics
from test_workspace import ws, make_strategy, add_one, advance
from test_backtests import creation_context


def calendar_days(count, start='2022-01-03'):
    result = []
    current = date.fromisoformat(start)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def config(**changes):
    return {**boll_conditions.DEFAULTS, 'band_period': 5, 'deviations': 1,
            'rank_window': 10, 'require_squeeze': False, **changes}


def leaf(period=None, **changes):
    result = {'field': 'boll_signal', 'op': 'matches', 'value': config(**changes)}
    if period is not None:
        result['period'] = period
    return result


def quotes(values, calendar):
    return [{'date': day, 'open': close, 'close': close, 'high': close+.2,
             'low': close-.2, 'volume': 100, 'amount': 1000,
             'corporate_action': False, 'corporate_action_coverage_known': True}
            for day, close in zip(calendar, values)]


def breakout_rows(count=90):
    calendar = calendar_days(count)
    rows = quotes([10+.1*sin(index) for index in range(count)], calendar)
    rows[-1].update(open=10, close=12, high=12.2, low=9.8, volume=400)
    return rows, calendar


def test_public_field_contract_and_no_hidden_current_theme_dependency():
    field = next(item for item in specs() if item['field'] == 'boll_signal')
    assert field['operators'] == ['matches']
    assert field['supported_periods'] == ['D', 'W', 'M']
    assert field['historical_event_supported'] is False
    original = {'field': 'boll_signal', 'op': 'matches', 'value': {}}
    normalized = validate(original)
    assert normalized['value'] == boll_conditions.DEFAULTS
    assert original['value'] == {}
    for period in ('D', 'W', 'M'):
        assert validate(leaf(period))['period'] == period


def test_same_symbol_generation_cache_isolates_configs_and_returned_mutations(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    rows, calendar = breakout_rows()
    day = calendar[-1]
    up, wide = leaf(deviations=1), leaf(deviations=3)
    args = {'instrument_id': '600001.SH', 'generation': 'fixed-generation'}
    first = cached_metrics(rows, day, calendar, 'D', boll_configs=[up['value']], **args)
    second = cached_metrics(rows, day, calendar, 'D', boll_configs=[wide['value']], **args)
    assert evaluate(up, first)['result'] is True
    assert evaluate(wide, second)['result'] is False
    assert evaluate(wide, first)['result'] is None
    first['boll_states'].clear()
    again = cached_metrics(rows, day, calendar, 'D', boll_configs=[up['value']], **args)
    assert evaluate(up, again)['result'] is True
    both = cached_metrics(rows, day, calendar, 'D', boll_configs=[wide['value'], up['value']], **args)
    assert len(both['boll_states']) == 2
    assert evaluate(up, both)['result'] is True and evaluate(wide, both)['result'] is False


def test_legacy_scalar_rules_do_not_compute_boll(monkeypatch):
    rows, calendar = breakout_rows()
    monkeypatch.setattr(boll_conditions, 'latest_states',
                        lambda *args, **kwargs: pytest.fail('legacy rule must not scan BOLL history'))
    metrics = metrics_for(rows, calendar[-1], calendar)
    assert 'boll_states' not in metrics
    assert evaluate({'field': 'close', 'op': '>', 'value': 0}, metrics)['result'] is True


@pytest.mark.parametrize('period', ['W', 'M'])
def test_mixed_daily_and_closed_period_clauses_load_own_bands(period):
    calendar = calendar_days(600)
    groups = {}
    for day in calendar:
        key = date.fromisoformat(day).isocalendar()[:2] if period == 'W' else day[:7]
        groups.setdefault(key, []).append(day)
    selected = list(groups.values())[:20]
    rows = []
    for index, members in enumerate(selected):
        close = 12 if index == 19 else 10+.1*sin(index)
        rows.extend(quotes([close]*len(members), members))
    day = selected[-1][-1]
    tree = validate({'all': [leaf(period), {'field': 'volume', 'op': '>', 'value': 0, 'period': 'D'}]})
    base = metrics_for(rows, day, calendar, 'D')
    combined = add_period_metrics(base, rows, day, calendar, tree, 'D')
    check = evaluate(tree, combined)
    assert check['result'] is True
    assert check['checks'][0]['period'] == period
    assert check['checks'][0]['observed_date'] == day
    assert 'boll_states' not in base
    early = selected[-1][0]
    current = metrics_for(rows, early, calendar, 'D')
    before = evaluate(tree, add_period_metrics(current, rows, early, calendar, tree, 'D'))
    assert before['checks'][0]['observed_date'] <= early
    assert before['checks'][0]['result'] is not True


def test_history_today_clause_uses_observation_boll_and_does_not_scan_event_boll():
    rows, calendar = breakout_rows()
    tree = validate({'history_event': {'window': {'from': 1, 'to': 20}, 'match': 'latest',
                    'where': {'field': 'volume', 'op': '>', 'value': 0}, 'then': leaf('D')}})
    day = calendar[-1]
    metrics = metrics_for(rows, day, calendar, boll_configs=[config()])
    check = evaluate(tree, metrics, HistoryContext(rows, calendar, day).at(day))
    assert check['result'] is True
    assert check['selected_event']['date'] == calendar[-2]
    assert check['checks'][0]['observed_date'] == day
    bad = deepcopy(tree)
    bad['history_event']['where'] = leaf('D')
    with pytest.raises(ValueError, match='BOLL'):
        validate(bad)


def test_real_selection_and_tracking_preserve_same_compact_parameter_proof(ws, monkeypatch):
    market, client = ws
    symbol = '600001.SH'
    rows = quotes([10+.1*sin(index) for index in range(len(market.days))], market.days)
    rows[70].update(open=10, close=12, high=12.2, low=9.8, volume=400)
    market.data[symbol] = rows
    for row in market.data['000001.SZ']:
        row['corporate_action_coverage_known'] = True
    monkeypatch.setattr(market, 'history_panel',
                        lambda *args, **kwargs: pytest.fail('whole-market history materialization forbidden'))
    definition = {'all': [leaf('D'), {'field': 'volume_ratio', 'op': '>=', 'value': 3},
                         {'field': 'macd_hist', 'op': '>', 'value': 0},
                         {'field': 'kdj_k', 'op': '>', 'right_field': 'kdj_d'}]}
    strategy = make_strategy(client, definition,
                             policy={'allow_entry_day_alerts': True, 'alerts': [leaf('D')], 'exits': []})
    run, round_ = add_one(client, strategy)
    item = next(item for item in run['items'] if item['instrument_id'] == symbol)
    assert item['decision'] == 'matched'
    proof = item['checks']['checks'][0]
    assert proof['result'] is True and proof['signal_version'] == boll_conditions.VERSION
    assert proof['value']['band_period'] == 5
    assert len(json.dumps(item['metrics']['boll_states'])) < 5000
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    detail = client.get('/api/v1/tracking-rounds/'+round_['round_id']).json()
    alert = next(item for item in detail['events'] if item['event_type'] == 'alert')
    assert alert['evidence']['alerts']['checks'][0] == proof


class Market:
    def __init__(self, rows, calendar):
        self.rows, self.days = rows, calendar

    def calendar(self):
        return self.days

    def instruments(self, limit=10000):
        return [{'instrument_id': '600001.SH', 'name': '合成测试', 'list_date': '2020-01-01'}]

    def status(self):
        return {'symbols': 1, 'snapshot_id': 'test-fixed-generation'}

    def iter_histories(self, end):
        yield '600001.SH', [dict(row) for row in self.rows if row['date'] <= end]


def test_backtest_streams_once_per_period_matches_scalar_and_is_causal(monkeypatch):
    calendar = calendar_days(170)
    values = [10+.1*sin(index) + (2 if index in (70, 110, 150) else 0) for index in range(len(calendar))]
    rows = quotes(values, calendar)
    upward, downward = leaf(), leaf(mode='breakout_down')
    definition = validate({'any': [upward, downward]})
    calls = []
    original = boll_conditions.states_for_bars

    def counted(daily_rows, days, as_of, period='D', configs=None):
        calls.append((period, len(configs or []), len(daily_rows)))
        return original(daily_rows, days, as_of, period, configs)

    monkeypatch.setattr(boll_conditions, 'states_for_bars', counted)
    _, _, signals, quality = prepare(Market(rows, calendar), {'period': 'D', 'definition': definition},
                                     calendar[40], calendar[-1])
    assert calls == [('D', 2, len(rows))]
    expected = {day for day in calendar[40:] if evaluate(definition,
                metrics_for(rows, day, calendar, boll_configs=[upward['value'], downward['value']]))['result'] is True}
    assert {signal['date'] for signal in signals} == expected
    assert quality['unknown_observations'] == 0
    assert calendar[70] in expected
    altered = deepcopy(rows)
    for row in altered:
        if row['date'] > calendar[70]:
            row.update(open=1, close=1, low=.8, high=1.2)
    _, _, changed, _ = prepare(Market(altered, calendar), {'period': 'D', 'definition': definition},
                               calendar[40], calendar[-1])
    assert next(signal['checks'] for signal in changed if signal['date'] == calendar[70]) == next(
        signal['checks'] for signal in signals if signal['date'] == calendar[70])


def test_boll_warmup_uses_own_parameters_and_disabled_setup_is_independent():
    assert warmup(leaf()) == 6
    assert warmup(leaf(mode='squeeze', band_period=20, rank_window=125)) == 145
    assert warmup(leaf(require_squeeze=True, band_period=20, rank_window=125,
                       recent_squeeze_periods=5)) == 150
    assert warmup(leaf(middle_direction='rising', slope_periods=60)) == 65
    calendar = calendar_days(10)
    rows = quotes([10]*9+[12], calendar)
    check = evaluate(leaf(rank_window=750), metrics_for(rows, calendar[-1], calendar,
                     boll_configs=[config(rank_window=750)]))
    assert check['result'] is True


def test_multiday_tracking_prepares_once_per_period_outside_sqlite_write_transaction(ws, monkeypatch):
    from app import core
    market, client = ws
    rows = quotes([10]*len(market.days), market.days)
    rows[70].update(close=12, high=12.2)
    market.data['600001.SH'] = rows
    strategy = make_strategy(client, leaf(), policy={
        'allow_entry_day_alerts': True,
        'alerts': [leaf('D'), leaf('W', band_period=2)], 'exits': []})
    add_one(client, strategy)
    advance(market, 74)
    calls = []
    original = boll_conditions.states_for_bars

    def transaction_checked(daily_rows, days, as_of, period='D', configs=None):
        # A second SQLite writer can acquire immediately only when the caller
        # has not held its own BEGIN IMMEDIATE across a full history scan.
        with core.connect() as connection:
            connection.execute('PRAGMA busy_timeout=0')
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
        calls.append((period, len(configs or []), as_of))
        return original(daily_rows, days, as_of, period, configs)

    monkeypatch.setattr(boll_conditions, 'states_for_bars', transaction_checked)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    assert sorted(calls) == [('D', 1, market.days[74]), ('W', 1, market.days[74])]
    assert response.json()['reviewed'] == 5


def test_backtest_freezes_boll_contract_and_refuses_incompatible_resume_before_market(creation_context, monkeypatch):
    from app.core import connect
    store, payload = creation_context
    market, strategy, inputs, generation = store.context(payload['strategy_id'])
    monkeypatch.setattr(store, 'context', lambda identifier: (
        market, {**strategy, 'definition': leaf()}, inputs, generation))
    with monkeypatch.context() as previous:
        previous.setattr(boll_conditions, 'VERSION', 'boll-signal-previous-fixture')
        saved = store.create_backtest(payload)
    with connect() as connection:
        row = connection.execute('SELECT * FROM backtest_run WHERE backtest_id=?',
                                 (saved['backtest_id'],)).fetchone()
        frozen = json.loads(row['strategy_snapshot_json'])
        assert frozen['condition_contracts'] == {'boll_signal': 'boll-signal-previous-fixture'}
        assert row['engine_version'] == store.VERSION
    monkeypatch.setattr(store, 'MarketData',
                        lambda *args, **kwargs: pytest.fail('mismatched contract cannot open market'))
    with pytest.raises(ValueError, match='BOLL.*版本不匹配'):
        store.execute_backtest(saved['job_id'])
