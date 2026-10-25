"""Historical price-path semantics on bounded synthetic local fixtures only."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from app import core
from app.analytics.backtest_engine import prepare, availability
from app.domain.conditions import evaluate, metrics_for, validate
from app.domain.history_events import HistoryContext
from app.domain.tracking import condition_decisions
from app.services import strategies
from app.services.history_events import contexts_for_batch, prepare_shared
from test_history_events import days
from test_history_event_panels import fixture_market
from test_workspace import ws


def path_fixture(closes=(10, 9.8, 9.6, 9.55, 9.75), count=80):
    calendar = days(count)
    values = [10.] * (count-len(closes)) + list(closes)
    rows = [{'instrument_id': '600001.SH', 'date': day, 'open': close,
             'high': close+.1, 'low': close-.1, 'close': close,
             'volume': 100., 'amount': 1000., 'corporate_action': False,
             'corporate_action_coverage_known': True}
            for day, close in zip(calendar, values)]
    offset = len(closes)-1
    rows[-offset-1]['volume'] = 300.
    return calendar, rows, offset


def rule(offset, price_path=None, then=None, window=None):
    event = {'window': window or {'from': offset, 'to': offset}, 'match': 'latest',
             'where': {'field': 'volume_ratio', 'op': '>=', 'value': 3, 'period': 'D'},
             'then': then or {'field': 'close', 'op': '>', 'value': 0, 'period': 'D'}}
    if price_path is not None:
        event['price_path'] = price_path
    return validate({'history_event': event})


def check(node, rows, calendar, day=None, context=None, period='D'):
    day = day or calendar[-1]
    context = context or HistoryContext(rows, calendar, calendar[-1], 'price-path-fixture')
    return evaluate(node, metrics_for(rows, day, calendar, period), context.at(day))


def guarded(ratio=5, pullback=False, near=None):
    guard = {'max_drawdown_pct': ratio, 'require_pullback': pullback}
    if near is not None:
        guard['near_floor'] = near
    return {'guard': guard}


def reclaim(mode='close_reclaim', field='close'):
    return {'reclaim': {'mode': mode, 'event_field': field}}


@pytest.mark.parametrize('price_path', [guarded(),guarded(pullback=True,near={'sessions':3,'band_pct':2})]+[
    reclaim(mode,field) for mode in ('close_at_or_above','close_reclaim','first_close_reclaim','high_at_or_above','touch')
    for field in ('close','high','low')])
def test_unavailable_forward_event_price_is_unknown_without_raw_fallback(price_path):
    from app.domain.strategy_prices import prepare_condition_prices
    calendar,rows,offset=path_fixture()
    rows[-1]['corporate_action_coverage_known']=False
    context=prepare_condition_prices(rows,[],calendar,calendar[-1],'forward',actions_coverage={'known':False})
    history=HistoryContext(rows,calendar,calendar[-1],price_context=context)
    node=rule(offset,price_path,then={'field':'volume','op':'>','value':0,'period':'D'})
    metrics=metrics_for(rows,calendar[-1],calendar,price_context=context)
    result=evaluate(node,metrics,history.at(calendar[-1]))
    assert result['selected_event']['raw_close']>0 and result['selected_event']['close'] is None
    assert result['result'] is None and result['price_path_checks']['result'] is None
    path=result['price_path_checks']
    assert path['reason'] and path['price_basis']=='unavailable'
    if 'guard' in path:
        assert path['guard']['floor'] is None and path['guard']['event_close'] is None
        assert path['guard']['reason']
    if 'reclaim' in path:
        assert path['reclaim']['reference_price'] is None and path['reclaim']['reason']


@pytest.mark.parametrize('close,expected', [(9.5, True), (9.49, False), (9.51, True)])
def test_guard_uses_event_close_percentage_and_includes_equal(close, expected):
    calendar, rows, offset = path_fixture((10, 9.8, close, 9.9))
    result = check(rule(offset, guarded()), rows, calendar)
    guard = result['price_path_checks']['guard']
    assert result['result'] is expected
    assert guard['floor'] == 9.5 and guard['event_close'] == 10
    assert guard['min_close'] == close
    assert guard['start'] == calendar[-3] and guard['end'] == calendar[-1]
    assert guard['comparable_sessions'] == guard['expected_sessions'] == offset
    if not expected:
        assert guard['first_breach_date'] == calendar[-2]


def test_guard_does_not_round_the_floor_before_comparison():
    calendar, rows, offset = path_fixture((10.01, 9.509, 10))
    guard = check(rule(offset, guarded()), rows, calendar)['price_path_checks']['guard']
    assert guard['floor'] == pytest.approx(9.5095)
    assert guard['result'] is False


def test_decimal_floor_equality_is_not_a_binary_float_breach():
    calendar, rows, offset = path_fixture((10, 8.2, 8.4))
    guard = check(rule(offset, guarded(18)), rows, calendar)['price_path_checks']['guard']
    assert guard['result'] is True and guard['floor'] == 8.2
    assert Decimal(guard['floor_decimal']) == Decimal('8.20')


@pytest.mark.parametrize('close,expected', [(9.49, False), (9.50, True)])
def test_decimal_half_cent_floor_is_never_rounded_to_display_price(close, expected):
    calendar, rows, offset = path_fixture((10, close, 9.8))
    guard = check(rule(offset, guarded(5.05)), rows, calendar)['price_path_checks']['guard']
    assert guard['result'] is expected
    assert Decimal(guard['floor_decimal']) == Decimal('9.495')


@pytest.mark.parametrize('ratio,band,close', [(18, 5, 8.61), (5, 1, 9.595)])
def test_decimal_near_floor_upper_equality_is_inclusive(ratio, band, close):
    calendar, rows, offset = path_fixture((10, close, 10.1))
    guard = check(rule(offset, guarded(ratio, near={'sessions': 1, 'band_pct': band})), rows, calendar)['price_path_checks']['guard']
    assert guard['result'] is True
    assert guard['near_floor']['upper'] == close


def test_guard_only_checks_closes_not_intraday_lows_or_the_event_day():
    calendar, rows, offset = path_fixture((10, 9.5, 9.6))
    rows[-2]['low'] = 8
    result = check(rule(offset, guarded()), rows, calendar)
    assert result['result'] is True
    assert result['price_path_checks']['guard']['min_close'] == 9.5


def test_rebound_template_has_actual_pullback_yesterday_near_floor_and_today_gain():
    calendar, rows, offset = path_fixture()
    node = rule(offset, guarded(pullback=True, near={'sessions': 1, 'band_pct': 1}),
                then={'field': 'change_pct', 'op': '>=', 'value': 2, 'period': 'D'})
    result = check(node, rows, calendar)
    guard = result['price_path_checks']['guard']
    assert result['result'] is True
    assert guard['pullback']['result'] is True
    assert guard['near_floor']['date'] == calendar[-2]
    assert guard['near_floor']['close'] == 9.55
    assert guard['near_floor']['upper'] == pytest.approx(9.595)
    assert result['checks'][0]['actual'] == pytest.approx((9.75/9.55-1)*100)
    assert rows[-1]['close'] < rows[-offset-1]['close'], 'rebound need not reclaim the event close'


@pytest.mark.parametrize('closes', [(10, 10.1, 10.2), (10, 10)])
def test_pullback_must_exist_after_event_and_before_today(closes):
    calendar, rows, offset = path_fixture(closes)
    guard = check(rule(offset, guarded(pullback=True)), rows, calendar)['price_path_checks']['guard']
    assert guard['result'] is False and guard['pullback']['result'] is False


def test_near_floor_recent_window_excludes_event_and_today():
    calendar, rows, offset = path_fixture((10, 9.5, 9.8, 9.75))
    assert check(rule(offset, guarded(near={'sessions': 1, 'band_pct': 0})), rows, calendar)['result'] is False
    near = check(rule(offset, guarded(near={'sessions': 2, 'band_pct': 0})), rows, calendar)['price_path_checks']['guard']['near_floor']
    assert near['result'] is True and near['date'] == calendar[-3]
    short = check(rule(offset, guarded(near={'sessions': 750, 'band_pct': 0})), rows, calendar)
    assert short['price_path_checks']['guard']['near_floor']['expected_sessions'] == offset-1
    # A near-price observation only today is not a past pullback.
    calendar, rows, offset = path_fixture((10, 10, 9.5))
    assert check(rule(offset, guarded(near={'sessions': 2, 'band_pct': 0})), rows, calendar)['result'] is False


@pytest.mark.parametrize('mode,expected', [('close_at_or_above', False), ('close_reclaim', False),
    ('first_close_reclaim', False), ('high_at_or_above', True), ('touch', True)])
def test_high_reaches_price_without_a_closing_reclaim(mode, expected):
    calendar, rows, offset = path_fixture((10, 9.8, 9.9))
    rows[-1]['high'] = 10.03
    result = check(rule(offset, reclaim(mode)), rows, calendar)
    assert result['result'] is expected
    assert result['price_path_checks']['reclaim']['reference_price'] == 10


@pytest.mark.parametrize('mode,expected', [('high_at_or_above', True), ('touch', False), ('close_reclaim', True)])
def test_gap_above_reaches_but_does_not_touch(mode, expected):
    calendar, rows, offset = path_fixture((10, 9.9, 10.2))
    assert check(rule(offset, reclaim(mode)), rows, calendar)['result'] is expected


@pytest.mark.parametrize('mode,expected', [('close_at_or_above', True), ('close_reclaim', False), ('first_close_reclaim', False)])
def test_staying_above_is_not_a_new_reclaim(mode, expected):
    calendar, rows, offset = path_fixture((10, 10.2, 10.3))
    assert check(rule(offset, reclaim(mode)), rows, calendar)['result'] is expected


def test_first_reclaim_counts_price_events_even_if_old_technical_confirmation_failed():
    calendar, rows, offset = path_fixture((10, 9.9, 10.05, 9.9, 10.2))
    # The first +1.52% recovery fails a +3% technical/current filter,
    # but still consumes the first price event.
    node = rule(offset, reclaim('first_close_reclaim'),
                then={'field': 'change_pct', 'op': '>=', 'value': 3, 'period': 'D'})
    result = check(node, rows, calendar)
    assert result['checks'][0]['result'] is True
    assert result['result'] is False
    assert result['price_path_checks']['reclaim']['first_prior_reclaim_date'] == calendar[-3]
    assert check(rule(offset, reclaim('close_reclaim')), rows, calendar)['result'] is True


def test_first_price_cross_may_be_event_to_next_day_when_reference_is_event_high():
    calendar, rows, offset = path_fixture((10, 10.2, 9.9, 10.3))
    event = rows[-offset-1]; event['high'] = 10.1
    ordinary = check(rule(offset, reclaim('close_reclaim', 'high')), rows, calendar)
    first = check(rule(offset, reclaim('first_close_reclaim', 'high')), rows, calendar)
    assert ordinary['result'] is True and first['result'] is False
    assert first['price_path_checks']['reclaim']['first_prior_reclaim_date'] == calendar[-3]
    # Current reclaim additionally requires a post-event previous day.
    early = check(rule(1, reclaim('close_reclaim', 'high')), rows, calendar, calendar[-3])
    assert early['selected_event']['date'] == event['date']
    assert early['result'] is False


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'zero', 'unknown_actions', 'action'])
def test_gap_or_action_prevents_fabricating_a_guard_success_or_later_breach(mode):
    calendar, rows, offset = path_fixture((10, 9.8, 9.7, 9.4, 10.2))
    blocked = calendar[-3]
    if mode == 'missing': rows.pop(-3)
    elif mode == 'duplicate': rows.append(dict(rows[-3]))
    elif mode == 'zero': rows[-3]['volume'] = 0
    elif mode == 'unknown_actions': rows[-3]['corporate_action_coverage_known'] = False
    else: rows[-3]['corporate_action'] = True
    result = check(rule(offset, guarded()), rows, calendar)
    guard = result['price_path_checks']['guard']
    assert result['result'] is None
    assert guard['blocking_date'] == blocked
    assert guard['first_breach_date'] is None, 'raw lower close after an obstruction is not a proven comparable breach'
    assert guard['min_close'] == 9.8 and guard['comparable_sessions'] == 1
    assert check(rule(offset, reclaim('close_reclaim')), rows, calendar)['result'] is None


def test_a_proven_early_breach_remains_false_with_later_missing_evidence():
    calendar, rows, offset = path_fixture((10, 9.49, 9.8, 9.9, 10.1))
    blocked = rows.pop(-2)['date']
    result = check(rule(offset, guarded()), rows, calendar)
    guard = result['price_path_checks']['guard']
    assert result['result'] is False
    assert guard['first_breach_date'] == calendar[-4] and guard['blocking_date'] == blocked


def test_prior_reclaim_can_prove_not_first_even_if_later_prices_are_unknown():
    calendar, rows, offset = path_fixture((10, 9.8, 10.1, 9.9, 10.2))
    rows.pop(-2)
    result = check(rule(offset, reclaim('first_close_reclaim')), rows, calendar)
    proof = result['price_path_checks']['reclaim']
    assert result['result'] is False and proof['blocking_date'] == calendar[-2]
    assert proof['first_prior_reclaim_date'] == calendar[-3]


def test_guard_and_reclaim_and_today_conditions_remain_independent_and_event_is_not_reselected():
    calendar, rows, offset = path_fixture((10, 9.49, 9.8, 9.9, 10.2))
    path = {**guarded(), **reclaim()}
    result = check(rule(offset, path), rows, calendar)
    assert result['result'] is False
    assert result['price_path_checks']['guard']['result'] is False
    assert result['price_path_checks']['reclaim']['result'] is True
    assert result['checks'][0]['result'] is True
    rows[-2]['volume'] = 900
    # latest matching event is yesterday and cannot satisfy a genuine pullback;
    # do not silently switch to the older event which could otherwise pass.
    result = check(rule(offset, guarded(pullback=True), window={'from': 1, 'to': offset}), rows, calendar)
    assert result['selected_event']['date'] == calendar[-2] and result['result'] is False


def test_path_remains_daily_under_monthly_outer_metrics_and_does_not_read_future():
    calendar, rows, offset = path_fixture(count=100)
    node = rule(offset, guarded(pullback=True, near={'sessions': 1, 'band_pct': 1}))
    context = HistoryContext(rows, calendar, calendar[-1], 'price-path-fixture')
    current = {'close': 500, 'observed_date': calendar[-20], 'indicator_period': 'M'}
    result = evaluate(node, current, context.at(calendar[-1]))
    assert result['result'] is True
    assert result['price_path_checks']['observation_date'] == calendar[-1]
    # Reuse a full replay context while evaluating an earlier observation.
    past_day = calendar[-2]
    old = check(rule(offset-1, guarded()), rows, calendar, past_day, context)
    future = deepcopy(rows); future[-1].update(corporate_action=True, close=1, low=.9)
    changed = check(rule(offset-1, guarded()), future, calendar, past_day)
    assert old == changed


def test_no_price_path_preserves_the_legacy_ast_and_event_check_shape():
    calendar, rows, offset = path_fixture()
    node = rule(offset)
    assert 'price_path' not in validate(node)['history_event']
    result = check(node, rows, calendar)
    assert 'price_path_checks' not in result
    assert len(result['checks']) == 1 and result['result'] is True
    node['history_event']['window'] = {'from': 1, 'to': 1}
    node['history_event']['price_path'] = guarded()
    result = check(node, rows, calendar)
    assert result['result'] is False and result['price_path_checks'] is None


@pytest.mark.parametrize('invalid', [None, {}, [], {'other': {}}, {'guard': {}}, {'guard': None},
    {'guard': {'max_drawdown_pct': True}}, {'guard': {'max_drawdown_pct': -1}},
    {'guard': {'max_drawdown_pct': 100}}, {'guard': {'max_drawdown_pct': 10**1000}},
    {'guard': {'max_drawdown_pct': float('nan')}},
    {'guard': {'max_drawdown_pct': float('inf')}}, {'guard': {'max_drawdown_pct': 5, 'require_pullback': 1}},
    {'guard': {'max_drawdown_pct': 5, 'other': 1}}, {'guard': {'max_drawdown_pct': 5, 'near_floor': {}}},
    {'guard': {'max_drawdown_pct': 5, 'near_floor': {'sessions': True, 'band_pct': 1}}},
    {'guard': {'max_drawdown_pct': 5, 'near_floor': {'sessions': 0, 'band_pct': 1}}},
    {'guard': {'max_drawdown_pct': 5, 'near_floor': {'sessions': 751, 'band_pct': 1}}},
    {'guard': {'max_drawdown_pct': 5, 'near_floor': {'sessions': 1, 'band_pct': -1}}},
    {'guard': {'max_drawdown_pct': 5, 'near_floor': {'sessions': 1, 'band_pct': float('inf')}}},
    {'guard': {'max_drawdown_pct': 5, 'near_floor': {'sessions': 1, 'band_pct': 10**1000}}},
    {'reclaim': {}}, {'reclaim': {'event_field': 'open', 'mode': 'touch'}},
    {'reclaim': {'event_field': 'close', 'mode': 'code'}},
    {'reclaim': {'event_field': 'close', 'mode': 'touch', 'period': 'M'}}])
def test_bad_price_path_configs_are_rejected(invalid):
    node = rule(4)
    node['history_event']['price_path'] = invalid
    with pytest.raises(ValueError): validate(node)


def test_price_path_config_cannot_be_a_free_leaf_or_history_where_field():
    with pytest.raises(ValueError): validate({'price_path': guarded()})
    with pytest.raises(ValueError): validate({'field': 'price_path', 'op': '==', 'value': True})


@pytest.mark.parametrize('mode', ['complete', 'breach', 'gap', 'action'])
def test_scalar_and_real_compact_preparation_have_identical_path_evidence(tmp_path, mode):
    calendar, rows, offset = path_fixture()
    if mode == 'breach': rows[-3].update(close=9.4, open=9.4, high=9.5, low=9.3)
    elif mode == 'gap': rows.pop(-3)
    elif mode == 'action': rows[-3]['corporate_action'] = True
    node = rule(offset, {**guarded(pullback=True), **reclaim('high_at_or_above')})
    market = fixture_market(tmp_path, rows, calendar)
    shared = prepare_shared(market, ['600001.SH'], [node], calendar, calendar[-1])
    compact = contexts_for_batch(market, ['600001.SH'], [node], calendar, calendar[-1], shared=shared)['600001.SH']
    scalar = HistoryContext(rows, calendar, calendar[-1], 'history-matrix')
    left = evaluate(node, {}, compact.at(calendar[-1]))
    right = evaluate(node, {}, scalar.at(calendar[-1]))
    assert left['result'] is right['result']
    assert left['selected_event'] == right['selected_event']
    assert left['price_path_checks'] == right['price_path_checks']


def test_tracking_uses_the_identical_price_path_result():
    calendar, rows, offset = path_fixture()
    node = rule(offset, guarded(pullback=True, near={'sessions': 1, 'band_pct': 1}))
    context = HistoryContext(rows, calendar, calendar[-1], 'price-path-fixture')
    metrics = metrics_for(rows, calendar[-1], calendar)
    policy = {'alerts': [node], 'alert_logic': 'OR', 'exits': [], 'exit_logic': 'OR',
              'allow_entry_day_alerts': False, 'allow_entry_day_exits': False}
    alert, exit_ = condition_decisions(policy, metrics, 1, context.at(calendar[-1]))
    assert alert['result'] is True and exit_['result'] is False
    proof = alert['checks'][0]['price_path_checks']
    assert proof == check(node, rows, calendar)['price_path_checks']
    assert 'context' not in json.dumps(alert)


def test_backtest_evaluates_the_same_guard_recovery_on_daily_prices():
    calendar, rows, offset = path_fixture(count=125)
    node = rule(offset, guarded(pullback=True, near={'sessions': 1, 'band_pct': 1}),
                then={'field': 'change_pct', 'op': '>=', 'value': 2, 'period': 'D'})
    class Market:
        def calendar(self): return calendar
        def instruments(self, limit=10000): return [{'instrument_id': '600001.SH', 'name': '路径测试', 'kind': 'stock', 'list_date': '2000-01-01'}]
        def status(self): return {'symbols': 1, 'snapshot_id': 'price-path-fixture'}
        def iter_histories(self, end): yield '600001.SH', [row for row in rows if row['date'] <= end]
    strategy = {'definition': node, 'period': 'D'}
    assert availability(Market(), strategy)['available'] is True
    _, signals, _, _ = prepare(Market(), strategy, calendar[-20], calendar[-1])
    assert list(signals) == [calendar[-1]]
    assert signals[calendar[-1]][0]['checks']['price_path_checks'] == check(node, rows, calendar)['price_path_checks']


def test_strategy_storage_freezes_price_path_and_uses_a_distinct_evaluator(ws):
    market, client = ws
    node = rule(4, guarded(pullback=True))
    created = client.post('/api/v1/strategies', json={'name': '路径fixture', 'period': 'D', 'definition': node})
    assert created.status_code == 200, created.text
    strategy = created.json(); sid = strategy['strategy_id']
    assert strategy['definition'] == node
    with core.connect() as connection:
        before = dict(connection.execute('SELECT * FROM strategy_version WHERE strategy_id=?', (sid,)).fetchone())
        assert before['evaluator_version'] == 'workspace-v10-history-price-path'
    changed = client.patch('/api/v1/strategies/'+sid, json={'definition': rule(4)})
    assert changed.status_code == 200 and 'price_path' not in changed.json()['definition']['history_event']
    with core.connect() as connection:
        old = dict(connection.execute('SELECT * FROM strategy_version WHERE strategy_id=? AND version=1', (sid,)).fetchone())
        assert old == before
    # Old rules retain their previous evaluator identities. A path in a buy/sell
    # tree also selects the new evaluator despite a simple selection definition.
    plain = {'field': 'close', 'op': '>', 'value': 0}
    assert strategies.evaluator_for(plain, 'D') == 'workspace-v8-bounded-history-events'
    assert strategies.evaluator_for(plain, 'M') == 'workspace-v9-multi-period'
    assert strategies.evaluator_for(plain, 'D', {'entry': node, 'exit': None}) == 'workspace-v10-history-price-path'


def test_persisted_selection_tracking_and_old_saved_result_share_price_path(ws, monkeypatch):
    from test_workspace import advance
    from app.services.tracking import review_tracking
    market, client = ws
    for values in market.data.values():
        for row in values:
            row['corporate_action_coverage_known'] = True
    for index, close in enumerate((10, 9.8, 9.7, 9.6, 9.6, 9.55, 9.75, 9.9), 64):
        market.data['600001.SH'][index].update(open=close, high=close+.1, low=close-.1,
                                               close=close, volume=300 if index == 64 else 100)
    reads = []
    def bounded(symbols, start=None, end=None):
        reads.append((symbols, start, end))
        return {symbol: [row for row in market.bars(symbol, end) if row['date'] >= start] for symbol in symbols}
    monkeypatch.setattr(market, 'history_for_instruments', bounded, raising=False)
    monkeypatch.setattr(market, 'history_panel', lambda **_: pytest.fail('daily path must use the bounded history adapter'))
    definition = rule(6, guarded(pullback=True, near={'sessions': 1, 'band_pct': 1}),
                      then={'field': 'change_pct', 'op': '>=', 'value': 2, 'period': 'D'})
    alert = rule(6, guarded(pullback=True), window={'from': 6, 'to': 7})
    response = client.post('/api/v1/strategies', json={'name': '路径选股跟踪fixture', 'period': 'D',
                          'definition': definition, 'policy': {'alerts': [alert], 'exits': []}})
    assert response.status_code == 200, response.text
    sid = response.json()['strategy_id']
    response = client.post('/api/v1/strategies/'+sid+'/runs', json={})
    assert response.status_code == 200, response.text
    saved = response.json()
    matches = [item for item in saved['items'] if item['decision'] == 'matched']
    assert [item['instrument_id'] for item in matches] == ['600001.SH']
    proof = matches[0]['checks']
    assert proof['price_path_checks']['guard']['floor'] == 9.5
    assert proof['price_path_checks']['guard']['near_floor']['date'] == market.days[69]
    with core.connect() as connection:
        versions = connection.execute('SELECT evaluator_version FROM strategy_version WHERE strategy_id=?', (sid,)).fetchall()
        assert len(versions) == 1 and versions[0][0] == 'workspace-v10-history-price-path'
    assert len(reads) == 1 and reads[0][1] == market.days[63]
    added = client.post('/api/v1/tracking-rounds', json={'run_id': saved['run_id'], 'instrument_ids': ['600001.SH']})
    assert added.status_code == 200, added.text
    advance(market, 71)
    assert review_tracking({'strategy_id': sid})['alerts'] == 1
    assert review_tracking({'strategy_id': sid})['alerts'] == 0
    changed = client.patch('/api/v1/strategies/'+sid, json={'definition': rule(1)})
    assert changed.status_code == 200
    original = client.get('/api/v1/strategy-runs/'+saved['run_id']).json()
    assert next(item for item in original['items'] if item['decision'] == 'matched')['checks'] == proof


def test_huge_guard_parameter_is_http_validation_error_not_internal_server_error(ws):
    _, client = ws
    definition = rule(4)
    definition['history_event']['price_path'] = {'guard': {'max_drawdown_pct': 10**1000}}
    response = client.post('/api/v1/strategies', json={'name': '参数验证fixture', 'definition': definition})
    assert response.status_code == 422, response.text
