"""Signed event returns and causal golden/dead/golden cycle fixtures."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
import math

import pytest

from app import core
from app.analytics.backtest_engine import prepare
from app.analytics.indicators import enrich_bars
from app.domain.conditions import evaluate, metrics_for, specs, validate
from app.domain.history_events import HistoryContext, prepared_fields, preparation_warmup
from app.domain.history_price_path import evaluate_price_path
from app.domain.tracking import condition_decisions
from app.services import strategies
from app.services.history_events import contexts_for_batch, prepare_shared
from test_history_price_path import path_fixture, rule, guarded, check
from test_history_events import days
from test_history_event_panels import fixture_market
from test_workspace import ws


def relative_rule(offset=4, ratio=3, op='<', field='close'):
    return rule(offset, then={'field': 'close', 'op': op, 'right_event_field': field,
                             'right_event_change_pct': ratio, 'period': 'D'})


@pytest.mark.parametrize('ratio,price,op,expected,threshold', [
    (3, 10.29, '<', True, '10.30'), (3, 10.30, '<', False, '10.30'),
    (3, 10.30, '<=', True, '10.30'), (3, 10.30, '==', True, '10.30'),
    (-3, 9.70, '>=', True, '9.70'), (-3, 9.69, '<', True, '9.70'),
    (0, 10, '==', True, '10'), (10000, 1010, '==', True, '1010'),
    (-18, 8.2, '==', True, '8.20'), (-5.05, 9.495, '==', True, '9.495'),
])
def test_signed_event_percent_threshold_uses_unrounded_decimal(ratio, price, op, expected, threshold):
    calendar, rows, offset = path_fixture((10, 10, price))
    result = check(relative_rule(offset, ratio, op), rows, calendar)
    proof = result['checks'][0]
    assert result['result'] is expected
    assert proof['event_price'] == 10
    assert Decimal(proof['threshold_decimal']) == Decimal(threshold)
    assert proof['event_change_pct'] == pytest.approx((price/10-1)*100)
    assert proof['comparison_model'] == 'daily-event-relative-price-v1'


@pytest.mark.parametrize('field', ['close', 'high', 'low'])
def test_offset_can_reference_each_accepted_event_price(field):
    calendar, rows, offset = path_fixture((10, 10, 10.25))
    event = rows[-offset-1]
    proof = check(relative_rule(offset, 3, '<', field), rows, calendar)['checks'][0]
    expected = Decimal(str(event[field])) * Decimal('1.03')
    assert Decimal(proof['threshold_decimal']) == expected
    assert proof['event_price'] == event[field]


@pytest.mark.parametrize('ratio', [-1e-100, 1e-100, -5e-324, 5e-324])
@pytest.mark.parametrize('op', ['>', '>=', '<', '<=', '==', '!='])
def test_tiny_finite_percentages_are_compared_without_losing_the_offset(ratio, op):
    calendar, rows, offset = path_fixture((10, 10, 10))
    actual = Fraction(10)
    threshold = Fraction(10)*(1+Fraction(Decimal(str(ratio)))/100)
    expected = {'>': actual>threshold, '>=': actual>=threshold, '<': actual<threshold,
                '<=': actual<=threshold, '==': actual==threshold, '!=': actual!=threshold}[op]
    proof = check(relative_rule(offset, ratio, op), rows, calendar)['checks'][0]
    assert proof['result'] is expected
    assert Fraction(Decimal(proof['threshold_decimal'])) == threshold


def test_zero_offset_is_explicit_new_input_but_absence_preserves_old_ast_and_proof():
    calendar, rows, offset = path_fixture()
    old = rule(offset, then={'field': 'close', 'op': '<', 'right_event_field': 'close', 'period': 'D'})
    new = relative_rule(offset, 0)
    assert validate(old) == old
    old_proof = check(old, rows, calendar)['checks'][0]
    new_proof = check(new, rows, calendar)['checks'][0]
    assert old_proof['result'] is new_proof['result']
    for field in ('event_price', 'threshold_decimal', 'comparison_model', 'event_change_pct'):
        assert field not in old_proof
    assert 'right_event_change_pct' not in old['history_event']['then']
    assert new['history_event']['then']['right_event_change_pct'] == 0


@pytest.mark.parametrize('bad', [True, None, '3', -100, -101, 10001, 10**1000,
                                float('nan'), float('inf'), float('-inf')])
def test_invalid_percent_offsets_are_rejected_as_validation_errors(bad):
    node = relative_rule()
    node['history_event']['then']['right_event_change_pct'] = bad
    with pytest.raises(ValueError): validate(node)


def test_percent_offset_must_be_scoped_to_an_event_then_reference():
    leaf = {'field': 'close', 'op': '<', 'right_event_field': 'close', 'right_event_change_pct': 3}
    with pytest.raises(ValueError): validate(leaf)
    with pytest.raises(ValueError): validate({**leaf, 'right_field': 'ma20'})
    with pytest.raises(ValueError): validate({'field': 'close', 'op': '<', 'value': 10, 'right_event_change_pct': 3})
    node = rule(4); node['history_event']['where'] = leaf
    with pytest.raises(ValueError): validate(node)
    node = relative_rule(); node['history_event']['then']['period'] = 'M'
    with pytest.raises(ValueError): validate(node)


@pytest.mark.parametrize('mode', ['gap', 'duplicate', 'zero', 'action', 'unknown_actions'])
def test_percent_comparison_retains_price_comparability_unknowns(mode):
    calendar, rows, offset = path_fixture()
    if mode == 'gap': rows.pop(-2)
    elif mode == 'duplicate': rows.append(dict(rows[-2]))
    elif mode == 'zero': rows[-2]['volume'] = 0
    elif mode == 'action': rows[-2]['corporate_action'] = True
    else: rows[-2]['corporate_action_coverage_known'] = False
    proof = check(relative_rule(offset), rows, calendar)['checks'][0]
    assert proof['result'] is None and proof['actual'] is None
    assert proof['event_price'] == 10 and Decimal(proof['threshold_decimal']) == Decimal('10.3')
    assert proof['event_change_pct'] is None


def test_extreme_decimal_threshold_does_not_emit_infinity_or_lose_small_price_comparison():
    calendar, rows, offset = path_fixture((1e308, 1e308, 1e308))
    proof = check(relative_rule(offset, 10000, '<'), rows, calendar)['checks'][0]
    assert proof['result'] is True and proof['right_actual'] is None
    json.dumps(proof, allow_nan=False)
    calendar, rows, offset = path_fixture((5e-324, 5e-324, 5e-324))
    # Keep valid tiny OHLC; the helper's +/-0.1 is deliberately replaced.
    for row in rows:
        if row['date'] >= calendar[-3]: row.update(open=5e-324, high=5e-324, low=5e-324)
    proof = check(relative_rule(offset, -99, '>'), rows, calendar)['checks'][0]
    assert proof['result'] is True and proof['right_actual'] is None
    assert Decimal(proof['threshold_decimal']) > 0
    json.dumps(proof, allow_nan=False)


def cycle_rule(indicator='macd', then=None, window=60):
    golden = 'macd_cross' if indicator == 'macd' else 'kd_cross'
    return validate({'history_event': {'window': {'from': 1, 'to': window}, 'match': 'latest',
        'where': {'field': golden, 'op': '==', 'value': True, 'period': 'D'},
        'price_path': {'cross_cycle': {'indicator': indicator, 'require_dead_cross': True}},
        'then': then or {'field': 'close', 'op': '>', 'right_event_field': 'close', 'period': 'D'}}})


def cycle_fixture(indicator='macd', event_close=60, today_close=61):
    calendar, rows, _ = path_fixture((event_close, 59, 58, 59.5, today_close))
    context = HistoryContext(rows, calendar, calendar[-1], 'cycle-fixture')
    golden, dead = ('macd_cross', 'macd_dead_cross') if indicator == 'macd' else ('kd_cross', 'kd_dead_cross')
    for metrics in context.daily.values(): metrics.update({golden: False, dead: False})
    context.daily[calendar[-5]][golden] = True
    context.daily[calendar[-3]][dead] = True
    context.daily[calendar[-1]][golden] = True
    return calendar, rows, context, golden, dead


@pytest.mark.parametrize('indicator', ['macd', 'kdj'])
def test_cycle_is_previous_golden_then_dead_then_today_golden_at_60_and_61(indicator):
    calendar, rows, context, golden, dead = cycle_fixture(indicator)
    result = check(cycle_rule(indicator), rows, calendar, context=context)
    cycle = result['price_path_checks']['cross_cycle']
    assert result['result'] is True
    assert result['selected_event']['date'] == calendar[-5]
    assert cycle['event_close'] == 60 and cycle['current_close'] == 61
    assert cycle['event_cross'] is cycle['current_cross'] is True
    assert cycle['dead_cross_result'] is True
    assert cycle['dead_cross_date'] == calendar[-3]
    assert cycle['expected_sessions'] == cycle['known_signal_sessions'] == 3
    assert result['price_path_checks']['model'] == 'daily-history-price-path-v2-cross-cycle'
    assert prepared_fields([cycle_rule(indicator)]) >= {golden, dead}
    assert preparation_warmup(cycle_rule(indicator)['history_event']) is None


def test_cycle_does_not_implicitly_force_a_higher_current_price():
    calendar, rows, context, _, _ = cycle_fixture(today_close=59)
    then = {'field': 'close', 'op': '<', 'right_event_field': 'close', 'right_event_change_pct': 3, 'period': 'D'}
    result = check(cycle_rule(then=then), rows, calendar, context=context)
    assert result['result'] is True
    assert result['price_path_checks']['cross_cycle']['close_above_event'] is False
    assert result['checks'][0]['event_change_pct'] == pytest.approx(-100/60)
    assert check(cycle_rule(), rows, calendar, context=context)['result'] is False


@pytest.mark.parametrize('mode', ['none', 'only_event', 'only_today', 'wrong_indicator'])
def test_cycle_requires_a_confirmed_same_indicator_dead_strictly_between(mode):
    calendar, rows, context, _, dead = cycle_fixture()
    context.daily[calendar[-3]][dead] = False
    if mode == 'only_event': context.daily[calendar[-5]][dead] = True
    elif mode == 'only_today': context.daily[calendar[-1]][dead] = True
    elif mode == 'wrong_indicator': context.daily[calendar[-3]]['kd_dead_cross'] = True
    result = check(cycle_rule(), rows, calendar, context=context)
    assert result['price_path_checks']['cross_cycle']['result'] is False
    assert result['price_path_checks']['cross_cycle']['dead_cross_result'] is False
    assert result['price_path_checks']['cross_cycle']['dead_cross_date'] is None


def test_yesterday_event_cannot_complete_an_intermediate_dead_cross():
    calendar, rows, context, golden, dead = cycle_fixture()
    context.daily[calendar[-2]][golden] = True
    result = check(cycle_rule(), rows, calendar, context=context)
    assert result['selected_event']['date'] == calendar[-2]
    assert result['result'] is False
    assert result['price_path_checks']['cross_cycle']['expected_sessions'] == 0


def test_cycle_does_not_skip_a_closer_golden_whose_price_condition_fails():
    calendar, rows, context, golden, dead = cycle_fixture()
    rows[-3].update(close=62, open=62, high=63, low=61)
    context = HistoryContext(rows, calendar, calendar[-1], 'cycle-fixture')
    for metrics in context.daily.values(): metrics.update({golden: False, dead: False})
    context.daily[calendar[-5]][golden] = True
    context.daily[calendar[-3]][golden] = True
    context.daily[calendar[-2]][dead] = True
    context.daily[calendar[-1]][golden] = True
    result = check(cycle_rule(), rows, calendar, context=context)
    assert result['selected_event']['date'] == calendar[-3]
    assert result['price_path_checks']['cross_cycle']['result'] is True
    assert result['checks'][0]['result'] is False and result['result'] is False


def test_priority_unknown_or_intermediate_unknown_is_not_a_false_signal():
    calendar, rows, context, golden, dead = cycle_fixture()
    context.daily[calendar[-2]][golden] = None
    result = check(cycle_rule(), rows, calendar, context=context)
    assert result['result'] is None and result['selected_event'] is None
    context.daily[calendar[-2]][golden] = False
    context.daily[calendar[-3]][dead] = None
    context.event_cache.clear()  # The test is replacing its immutable input fixture.
    result = check(cycle_rule(), rows, calendar, context=context)
    assert result['result'] is None and result['selected_event']['date'] == calendar[-5]
    assert result['price_path_checks']['cross_cycle']['dead_cross_result'] is None
    assert result['price_path_checks']['cross_cycle']['known_signal_sessions'] == 2


@pytest.mark.parametrize('mode', ['gap', 'zero', 'action', 'unknown_actions'])
def test_cycle_does_not_use_technical_signals_beyond_an_uncomparable_obstruction(mode):
    calendar, rows, _, golden, dead = cycle_fixture()
    if mode == 'gap': rows.pop(-3)
    elif mode == 'zero': rows[-3]['volume'] = 0
    elif mode == 'action': rows[-3]['corporate_action'] = True
    else: rows[-3]['corporate_action_coverage_known'] = False
    context = HistoryContext(rows, calendar, calendar[-1], 'cycle-fixture')
    for metrics in context.daily.values(): metrics.update({golden: False, dead: False})
    context.daily[calendar[-5]][golden] = True
    # This known signal after the obstruction must not count.
    context.daily[calendar[-2]][dead] = True
    context.daily[calendar[-1]][golden] = True
    result = check(cycle_rule(), rows, calendar, context=context)
    assert result['result'] is None
    if result['selected_event'] is None:
        assert result['price_path_checks'] is None
    else:
        assert result['selected_event']['date'] == calendar[-5]
        assert result['price_path_checks']['cross_cycle']['current_cross'] is None
    # Missing/unusable quotes can block the nearest-event scan itself. The
    # action fixture injects known technical values, but its price path still
    # remains unknown at the action boundary.
    # Even a defensive direct call with an externally bound E must not consume
    # any apparently confirmed signal after the raw-price obstruction.
    path = evaluate_price_path(cycle_rule()['history_event']['price_path'], context,
                               context.quotes[calendar[-5]], calendar[-1])
    cycle = path['cross_cycle']
    assert cycle['dead_cross_date'] is None and cycle['current_cross'] is None
    assert cycle['blocking_date'] == calendar[-3]


@pytest.mark.parametrize('change', [
    lambda e: e.update(match='earliest'), lambda e: e['window'].update({'from': 2}),
    lambda e: e.update(where={'field': 'macd_cross', 'op': '==', 'value': True}),
    lambda e: e.update(where={'field': 'kd_cross', 'op': '==', 'value': True, 'period': 'D'}),
    lambda e: e.update(where={'any': [e['where']]}),
    lambda e: e.update(where={'all': [e['where'], {'field': 'kdj_k', 'op': '<', 'value': 20, 'period': 'D'}]}),
    lambda e: e['price_path']['cross_cycle'].update(require_dead_cross=False),
    lambda e: e['price_path']['cross_cycle'].update(indicator='rsi'),
    lambda e: e['price_path']['cross_cycle'].update(other=1),
])
def test_cycle_gate_preserves_the_true_previous_same_indicator_cross(change):
    node = deepcopy(cycle_rule()); change(node['history_event'])
    with pytest.raises(ValueError): validate(node)


def test_cycle_accepts_a_single_all_wrapper_but_legacy_unscoped_events_stay_legal():
    node = cycle_rule(); node['history_event']['where'] = {'all': [node['history_event']['where']]}
    assert validate(node) == node
    legacy = rule(4, guarded()); legacy['history_event']['where'].pop('period')
    assert validate(legacy) == legacy


def wave_fixture(indicator='macd', count=420):
    calendar = days(count)
    rows = []
    for index, day in enumerate(calendar):
        close = 60+math.sin(index*.2)*4+index*.02
        rows.append({'instrument_id': '600001.SH', 'date': day, 'open': close,
                     'close': close, 'high': close+.3, 'low': close-.3, 'volume': 100.,
                     'amount': 10000., 'corporate_action': False, 'corporate_action_coverage_known': True})
    golden = 'macd_cross' if indicator == 'macd' else 'kd_cross'
    enriched = enrich_bars(rows, calendar)
    crosses = [index for index, row in enumerate(enriched) if row[golden] is True]
    return calendar, rows, calendar[crosses[-1]], enriched


@pytest.mark.parametrize('indicator', ['macd', 'kdj'])
def test_real_scalar_and_compact_cycle_use_the_same_recursive_seed_and_signal_dates(tmp_path, indicator):
    calendar, rows, day, _ = wave_fixture(indicator)
    node = cycle_rule(indicator)
    market = fixture_market(tmp_path, rows, calendar)
    shared = prepare_shared(market, ['600001.SH'], [node], calendar, day)
    compact = contexts_for_batch(market, ['600001.SH'], [node], calendar, day, shared=shared)['600001.SH']
    scalar = HistoryContext(rows, calendar, day, 'history-matrix')
    a = evaluate(node, {}, compact.at(day)); b = evaluate(node, {}, scalar.at(day))
    assert a['result'] is b['result'] is True
    assert a['selected_event'] == b['selected_event']
    assert a['price_path_checks'] == b['price_path_checks']
    future = deepcopy(rows)
    for row in future:
        if row['date'] > day: row.update(close=10000, high=10001, corporate_action=True)
    assert check(node, rows, calendar, day) == check(node, future, calendar, day)


@pytest.mark.parametrize('indicator', ['macd', 'kdj'])
def test_tracking_and_restricted_backtest_share_the_cycle_and_percent_comparison(indicator):
    calendar, rows, day, _ = wave_fixture(indicator)
    node = cycle_rule(indicator, then={'field': 'close', 'op': '<', 'right_event_field': 'close',
                                     'right_event_change_pct': 3, 'period': 'D'})
    context = HistoryContext(rows, calendar, calendar[-1], 'cycle-wave')
    metrics = metrics_for(rows, day, calendar)
    result = evaluate(node, metrics, context.at(day))
    policy = {'alerts': [node], 'alert_logic': 'OR', 'exits': [], 'exit_logic': 'OR',
              'allow_entry_day_alerts': False, 'allow_entry_day_exits': False}
    alert, _ = condition_decisions(policy, metrics, 1, context.at(day))
    assert alert['result'] is result['result'] is True
    assert alert['checks'][0]['price_path_checks'] == result['price_path_checks']
    class Market:
        def calendar(self): return calendar
        def instruments(self, limit=10000): return [{'instrument_id': '600001.SH', 'name': '周期fixture', 'list_date': '2000-01-01'}]
        def status(self): return {'symbols': 1, 'snapshot_id': 'cycle-wave'}
        def iter_histories(self, end): yield '600001.SH', [row for row in rows if row['date'] <= end]
    _, signals, _, _ = prepare(Market(), {'period': 'D', 'definition': node}, calendar[-90], day)
    assert signals[day][0]['checks']['price_path_checks'] == result['price_path_checks']
    assert signals[day][0]['checks']['checks'][0]['threshold_decimal'] == result['checks'][0]['threshold_decimal']


@pytest.mark.parametrize('window', [30, 60])
@pytest.mark.parametrize('event_type', ['volume', 'limit', 'change'])
def test_existing_recent_event_or_and_today_cross_conditions_are_compatible(window, event_type):
    calendar, rows, context, _, _ = cycle_fixture()
    for value in context.daily.values(): value.update(limit_up=False, macd_cross=False, kd_cross=False)
    event_day = calendar[-5]
    context.daily[event_day].update(volume_ratio=3 if event_type == 'volume' else 1,
                                   limit_up=event_type == 'limit', change_pct=5.1 if event_type == 'change' else 0)
    context.daily[calendar[-1]]['kd_cross'] = True
    node = validate({'history_event': {'window': {'from': 1, 'to': window}, 'match': 'latest',
        'where': {'any': [{'field': 'volume_ratio', 'op': '>=', 'value': 3, 'period': 'D'},
                          {'field': 'limit_up', 'op': '==', 'value': True, 'period': 'D'},
                          {'field': 'change_pct', 'op': '>', 'value': 5, 'period': 'D'}]},
        'then': {'any': [{'field': 'macd_cross', 'op': '==', 'value': True, 'period': 'D'},
                         {'field': 'kd_cross', 'op': '==', 'value': True, 'period': 'D'}]}}})
    result = check(node, rows, calendar, context=context)
    assert result['result'] is True and result['selected_event']['date'] == event_day


def test_new_version_only_applies_to_new_percent_or_cycle_inputs_and_field_metadata():
    old = rule(4); old_path = rule(4, guarded())
    assert strategies.evaluator_for(old, 'D') == 'workspace-v8-bounded-history-events'
    assert strategies.evaluator_for(old_path, 'D') == 'workspace-v10-history-price-path'
    assert strategies.evaluator_for(relative_rule(), 'D') == 'workspace-v11-event-percent-cross-cycle'
    assert strategies.evaluator_for(cycle_rule(), 'D') == 'workspace-v11-event-percent-cross-cycle'
    assert strategies.evaluator_for(old, 'D', {'entry': cycle_rule(), 'exit': None}) == 'workspace-v11-event-percent-cross-cycle'
    metadata = next(item for item in specs() if item['field'] == 'close')['event_price_comparison']
    assert metadata['change_pct'] == {'min_exclusive': -100, 'max': 10000, 'default': 0}


def test_api_persists_new_event_percent_versions_without_rewriting_old_inputs(ws, monkeypatch):
    market, client = ws
    for values in market.data.values():
        for row in values: row['corporate_action_coverage_known'] = True
    monkeypatch.setattr(market, 'history_for_instruments',
                        lambda symbols, start, end: {symbol: [row for row in market.bars(symbol, end) if row['date'] >= start]
                                                     for symbol in symbols}, raising=False)
    market.data['600001.SH'][66]['volume'] = 300
    created = client.post('/api/v1/strategies', json={'name': '事件涨幅fixture', 'definition': rule(4, guarded())})
    assert created.status_code == 200
    sid = created.json()['strategy_id']
    with core.connect() as connection:
        old = dict(connection.execute('SELECT * FROM strategy_version WHERE strategy_id=?', (sid,)).fetchone())
    changed = client.patch('/api/v1/strategies/'+sid, json={'definition': relative_rule()})
    assert changed.status_code == 200 and changed.json()['definition'] == relative_rule()
    with core.connect() as connection:
        assert dict(connection.execute('SELECT * FROM strategy_version WHERE strategy_id=? AND version=1', (sid,)).fetchone()) == old
        assert connection.execute('SELECT evaluator_version FROM strategy_version WHERE strategy_id=? AND version=2', (sid,)).fetchone()[0] == 'workspace-v11-event-percent-cross-cycle'
    executed = client.post('/api/v1/strategies/'+sid+'/runs', json={})
    assert executed.status_code == 200, executed.text
    run = executed.json()
    match = next(item for item in run['items'] if item['instrument_id'] == '600001.SH')
    assert match['decision'] == 'matched'
    assert match['checks']['checks'][0]['comparison_model'] == 'daily-event-relative-price-v1'
    assert client.get('/api/v1/strategy-runs/'+run['run_id']).json()['items'] == run['items']
    wrong = deepcopy(cycle_rule()); wrong['history_event']['window']['from'] = 2
    assert client.patch('/api/v1/strategies/'+sid, json={'definition': wrong}).status_code == 422
