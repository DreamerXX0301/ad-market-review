"""Volume-change predicates require traded periods; numeric display stays intact."""
from copy import deepcopy
from datetime import date, timedelta
from math import lcm

import pandas as pd
import pytest

from app import core
from app.analytics.backtest_engine import prepare
from app.analytics.panel_inputs import PanelInputs
from app.analytics.period_dates import latest_closed_date
from app.domain.conditions import evaluate, metrics_for, metrics_from_enriched
from app.domain.sector_conditions import evaluate as evaluate_sector
from app.services.sector_strategies import _raw_metrics
from app.services import strategies
from test_workspace import ws, make_strategy, add_one, advance


FIELDS = ('volume_expand', 'volume_shrink')


def rule(field, **extra):
    return {'field': field, 'op': '==', 'value': True, **extra}


def assert_states(metrics, expected):
    for field, state in zip(FIELDS, expected):
        assert metrics[field] is state
        assert evaluate(rule(field), metrics)['result'] is state
        for op, value in [('==', False), ('!=', True), ('!=', False)]:
            result = evaluate({**rule(field), 'op': op, 'value': value}, metrics)['result']
            assert result is (None if state is None else (state == value if op == '==' else state != value))
        if state is None:
            assert evaluate(rule(field), metrics)['reason']


def market_rows():
    day = date(2026, 1, 1); days = []
    while day <= date(2026, 3, 31):
        if day.weekday() < 5: days.append(day.isoformat())
        day += timedelta(days=1)
    return days, [{'date': day, 'open': 10., 'high': 11., 'low': 9., 'close': 10.,
                   'volume': 100., 'amount': 1000., 'corporate_action': False,
                   'corporate_action_coverage_known': True} for day in days]


def comparable_periods(period):
    days, rows = market_rows(); as_of = days[-1]
    closed = latest_closed_date(days, as_of, period)
    key = lambda day: date.fromisoformat(day).isocalendar()[:2] if period == 'W' else day[:7] if period == 'M' else day
    groups = {}
    for row in rows:
        if row['date'] <= closed: groups.setdefault(key(row['date']), []).append(row)
    previous, current = list(groups.values())[-2:]
    total = lcm(len(previous), len(current))*100
    for group in (previous, current):
        for row in group: row['volume'] = total // len(group)
    return days, rows, as_of, previous, current


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('factor,expected', [(1, (False, False)), (2, (True, False)), (.5, (False, True))])
def test_comparable_complete_periods_keep_strict_increase_decrease_and_equality(period, factor, expected):
    days, rows, as_of, _, current = comparable_periods(period)
    for row in current: row['volume'] *= factor
    metrics = metrics_for(rows, as_of, days, period)
    assert metrics['observed_date'] == current[-1]['date']
    assert metrics['volume_ratio'] == factor
    assert_states(metrics, expected)
    for field, state in zip(FIELDS, expected):
        assert evaluate_sector(rule(field, period=period), {period: metrics})['result'] is state


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('side', ['current', 'previous'])
@pytest.mark.parametrize('invalid', [0, None])
def test_invalid_volume_in_either_comparable_period_stays_unknown(period, side, invalid):
    days, rows, as_of, previous, current = comparable_periods(period)
    (current if side == 'current' else previous)[-1]['volume'] = invalid
    metrics = metrics_for(rows, as_of, days, period)
    assert_states(metrics, (None, None))
    if period == 'D' and side == 'current' and invalid == 0:
        assert metrics['volume_ratio'] == 0, 'legacy numeric display ratio is unchanged'
        assert metrics['close'] == 10., 'volume guards must not hide valid price observations'


@pytest.mark.parametrize('side', ['current', 'previous'])
@pytest.mark.parametrize('invalid', [0, None])
def test_actual_panel_fast_path_uses_the_same_unknown_guard(side, invalid):
    days, rows, as_of, previous, current = comparable_periods('D')
    (current if side == 'current' else previous)[-1]['volume'] = invalid
    frame = pd.DataFrame([{**row, 'instrument_id': '600001.SH'} for row in rows])
    indicators = PanelInputs(frame, days).latest(as_of)['600001.SH']
    # Production selection merges raw current volume with the derived panel,
    # and usually does not load a previous panel for a volume-only rule.
    metrics = metrics_from_enriched({**current[-1], **indicators})
    assert_states(metrics, (None, None))
    if side == 'current' and invalid == 0:
        assert metrics['volume_ratio'] == 0


@pytest.mark.parametrize('invalid', [0, None, float('nan'), float('inf'), -1])
@pytest.mark.parametrize('side', ['current', 'previous', 'ratio'])
def test_stale_positive_ratio_cannot_override_invalid_raw_volume(side, invalid):
    current = {'date': '2026-03-31', 'volume': 100., 'volume_ratio': 2.}
    previous = {'date': '2026-03-30', 'volume': 50.}
    if side == 'ratio': current['volume_ratio'] = invalid
    else: (current if side == 'current' else previous)['volume'] = invalid
    assert_states(metrics_from_enriched(current, previous), (None, None))


def test_panel_omitting_previous_row_still_accepts_verified_positive_ratio():
    assert_states(metrics_from_enriched({'volume': 100., 'volume_ratio': 2.}), (True, False))


@pytest.mark.parametrize('invalid', [0, None])
def test_sector_adapter_does_not_turn_zero_or_missing_volume_into_a_signal(invalid):
    current = {'date': '2026-03-31', 'volume': invalid, 'volume_ratio': 0 if invalid == 0 else None}
    metrics = _raw_metrics(current, {'volume': 100.})
    for field in FIELDS:
        assert metrics[field] is None
        assert evaluate_sector(rule(field), {'D': metrics})['result'] is None


def test_scalar_gap_is_unknown_and_does_not_compare_last_visible_quote():
    days, rows, as_of, _, _ = comparable_periods('D')
    rows.pop(-2)
    assert_states(metrics_for(rows, as_of, days), (None, None))


def test_ordinary_selection_fast_path_keeps_zero_volume_candidate_unknown(ws):
    market, client = ws
    next(row for row in market.data['600001.SH'] if row['date'] == market.entry)['volume'] = 0
    strategy = make_strategy(client, definition=rule('volume_shrink'))
    response = client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs', json={})
    assert response.status_code == 200, response.text
    candidate = next(row for row in response.json()['items'] if row['instrument_id'] == '600001.SH')
    assert candidate['decision'] == 'unknown'
    assert candidate['metrics']['volume_ratio'] == 0
    assert candidate['checks']['actual'] is None


def test_only_new_manual_selection_uses_corrected_version_old_result_is_frozen(ws, monkeypatch):
    market, client = ws
    next(row for row in market.data['600001.SH'] if row['date'] == market.entry)['volume'] = 0
    adapter = strategies.metrics_from_enriched
    def legacy_metrics(row, previous=None, topics=None):
        metrics = adapter(row, previous, topics)
        if row is not None and row.get('volume_ratio') is not None:
            metrics.update(volume_expand=row['volume_ratio'] > 1, volume_shrink=row['volume_ratio'] < 1)
        return metrics
    with monkeypatch.context() as legacy:
        legacy.setattr(strategies, 'evaluator_for', lambda *args: strategies.ENGINE_VERSION)
        legacy.setattr(strategies, 'metrics_from_enriched', legacy_metrics)
        strategy = make_strategy(client, definition=rule('volume_shrink'))
        response = client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs', json={})
        assert response.status_code == 200, response.text
        old = response.json()
        assert next(row for row in old['items'] if row['instrument_id'] == '600001.SH')['decision'] == 'matched'
    response = client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs', json={})
    assert response.status_code == 200, response.text
    new = response.json()
    assert new['run_id'] != old['run_id']
    assert new['strategy_version'] == old['strategy_version'] + 1
    assert next(row for row in new['items'] if row['instrument_id'] == '600001.SH')['decision'] == 'unknown'
    assert strategies.get_run(old['run_id']) == old
    with core.connect() as connection:
        versions = connection.execute('SELECT evaluator_version FROM strategy_version WHERE strategy_id=? ORDER BY version', (strategy['strategy_id'],)).fetchall()
    assert [row['evaluator_version'] for row in versions] == [strategies.ENGINE_VERSION, 'workspace-v16-volume-comparison-v1']


def test_volume_evaluator_version_covers_compositions_and_trade_rules_without_changing_numeric_rules():
    plain = {'field': 'volume_ratio', 'op': '>=', 'value': 3}
    assert strategies.evaluator_for(plain, 'D') == strategies.ENGINE_VERSION
    for field in FIELDS:
        assert strategies.evaluator_for(rule(field), 'D') == 'workspace-v16-volume-comparison-v1'
        assert strategies.evaluator_for({'all': [rule(field), {'field': 'boll_signal'}]}, 'D') == 'workspace-v16-volume-comparison-v1'
        assert strategies.evaluator_for(plain, 'D', {'entry': None, 'exit': rule(field)}) == 'workspace-v16-volume-comparison-v1'


@pytest.mark.parametrize('invalid', [0, None])
def test_tracking_review_does_not_alert_or_end_on_invalid_volume(ws, invalid):
    market, client = ws
    strategy = make_strategy(client, policy={'alerts': [rule('volume_expand')], 'exits': [rule('volume_shrink')]})
    _, tracked = add_one(client, strategy)
    next(row for row in market.data[tracked['instrument_id']] if row['date'] == market.days[71])['volume'] = invalid
    advance(market, 71)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    detail = client.get('/api/v1/tracking-rounds/'+tracked['round_id']).json()
    assert detail['status'] == 'tracking'
    assert not [event for event in detail['events'] if event['event_type'] in ('alert', 'condition_ended')]
    review = next(event for event in detail['events'] if event['event_type'] == 'review' and event['business_date'] == market.days[71])
    assert review['outcome'] == 'unknown'
    assert review['evidence']['alerts']['checks'][0]['result'] is None
    assert review['evidence']['exits']['checks'][0]['result'] is None


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_restricted_backtest_reuses_predicates_and_keeps_no_trade_exclusion(period):
    from test_stock_strategy_periods import PeriodMarket
    market = PeriodMarket()
    strategy = {'period': period, 'definition': rule('volume_shrink')}
    _, signals, _, quality = prepare(market, strategy, '2026-08-03', '2026-10-13')
    assert quality['eligible_symbols'] == 1
    assert all(row['metrics']['volume_shrink'] is True for group in signals.values() for row in group)
    invalid = deepcopy(market)
    next(row for row in invalid.rows if row['date'] == '2026-09-01')['volume'] = 0
    _, signals, rows, quality = prepare(invalid, strategy, '2026-08-03', '2026-10-13')
    assert not signals and not rows
    assert quality['excluded_symbols'] == 1
    assert quality['exclusions']['invalid_or_no_trading'] == 1
