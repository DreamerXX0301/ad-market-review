"""Existential event matching keeps each candidate's complete proof isolated."""
from copy import deepcopy
import json

import pytest

from app import core
from app.analytics.backtest_engine import prepare
from app.domain.conditions import evaluate, metrics_for, validate
from app.domain.history_events import HistoryContext
from app.routers.platform import router
from app.services import chart_tracking, strategies
from app.services.history_events import contexts_for_batch, prepare_shared
from test_history_events import days, quotes, event, check
from test_history_event_panels import fixture_market
from test_workspace import ws, make_strategy, add_one


def rule(from_=1, to=6, *, where=None, then=None, path=None):
    node = event(from_, to, 'any', where or {'field': 'amount', 'op': '>', 'value': 4000}, then)
    if path:
        node['history_event']['price_path'] = path
    return validate(node)


def price(row, close):
    row.update(open=close, high=close+1, low=close-1, close=close)


def two_events():
    calendar = days(); rows = quotes(calendar)
    rows[-3]['amount'] = rows[-5]['amount'] = 5000
    price(rows[-3], 30); price(rows[-5], 10); price(rows[-1], 20)
    return calendar, rows


def test_any_rejects_the_latest_complete_candidate_then_binds_the_first_whole_success():
    calendar, rows = two_events()
    node = rule(); original = deepcopy(node)
    result = check(node, rows, calendar)
    assert result['result'] is True and result['selected_event']['offset'] == 4
    assert result['selected_event']['close'] == 10
    assert result['checks'][0]['right_actual'] == 10
    assert result['checks'][0]['event_date'] == result['selected_event']['date']
    assert result['evidence']['model'] == 'daily-history-event-v2-any'
    assert result['search_summary']['where_matches'] == 2
    assert result['search_summary']['rejected_candidates'] == 1
    assert result['search_summary']['examined_sessions'] == 4
    assert result['search_summary']['complete_window_scan'] is False
    assert result['search_summary']['stopped_after_match'] is True
    latest = deepcopy(node); latest['history_event']['match'] = 'latest'
    old = check(latest, rows, calendar)
    assert old['result'] is False and old['selected_event']['offset'] == 2
    assert old['evidence']['model'] == 'daily-history-event-v1' and 'search_summary' not in old
    assert node == original


def test_multiple_true_events_choose_nearest_confirmed_and_do_not_enumerate_remaining_matches():
    calendar, rows = two_events(); price(rows[-3], 12)
    result = check(rule(), rows, calendar)
    assert result['selected_event']['offset'] == 2
    assert result['search_summary']['where_matches'] == 1
    assert result['search_summary']['examined_sessions'] == 2
    assert result['search_summary']['complete_window_scan'] is False
    assert len(result['checks']) == 1 and len(json.dumps(result)) < 7000


@pytest.mark.parametrize('grouped', [False, True])
def test_unknown_more_recent_where_does_not_prevent_an_earlier_complete_success(grouped):
    calendar, rows = two_events(); price(rows[-2], 12)
    context = HistoryContext(rows, calendar, calendar[-1])
    context.daily[calendar[-2]]['amount'] = None
    where = {'all': [{'field': 'amount', 'op': '>', 'value': 4000}, {'field': 'close', 'op': '>', 'value': 0}]} if grouped else None
    result = check(rule(where=where), rows, calendar, context=context)
    assert result['result'] is True and result['selected_event']['offset'] == 4
    assert result['search_summary']['unknown_candidates'] == 1
    assert result['search_summary']['first_unknown']['offset'] == 1
    assert isinstance(result['search_summary']['first_unknown']['reason'], str)


def test_no_success_with_a_whole_unknown_candidate_retains_no_selected_event():
    calendar, rows = two_events(); context = HistoryContext(rows, calendar, calendar[-1])
    context.daily[calendar[-1]]['macd_dif'] = None
    result = check(rule(then={'field': 'macd_dif', 'op': '>', 'value': 0, 'period': 'D'}), rows, calendar, context=context)
    assert result['result'] is None and result['selected_event'] is None
    assert result['event_checks'] is None and result['checks'] == []
    assert result['search_summary']['unknown_candidates'] == 2
    assert result['search_summary']['complete_window_scan'] is True


@pytest.mark.parametrize('missing', [False, True])
def test_today_false_disproves_even_unconfirmed_or_missing_event_where(missing):
    calendar, rows = two_events()
    if missing:
        rows = [row for row in rows if row['date'] not in calendar[-7:-1]]
    context = HistoryContext(rows, calendar, calendar[-1])
    for metrics in context.daily.values():
        metrics['limit_up'] = None
    result = check(rule(where={'field': 'limit_up', 'op': '==', 'value': True},
                        then={'field': 'close', 'op': '<', 'value': 0, 'period': 'D'}), rows, calendar, context=context)
    assert result['result'] is False and result['selected_event'] is None
    assert result['search_summary']['unknown_event_sessions'] == 6
    assert result['search_summary']['unknown_candidates'] == 0
    assert result['search_summary']['rejected_candidates'] == 6


def test_missing_calendar_or_context_only_uses_independent_then_false():
    calendar = days(4); rows = quotes(calendar)
    false_then = {'field': 'close', 'op': '<', 'value': 0, 'period': 'D'}
    assert check(rule(4, 6, then=false_then), rows, calendar)['result'] is False
    assert check(rule(4, 6), rows, calendar)['result'] is None
    metrics = {'close': 10, 'indicator_period': 'D'}
    result = evaluate(rule(then=false_then), metrics)
    assert result['result'] is False and result['selected_event'] is None
    assert result['search_summary']['context_unavailable'] is True
    assert result['search_summary']['examined_sessions'] == 0
    assert evaluate(rule(), metrics)['result'] is None


def test_known_then_false_short_circuits_an_unknown_price_path(monkeypatch):
    calendar, rows = two_events(); rows[-2]['corporate_action'] = True
    from app.domain import history_price_path
    monkeypatch.setattr(history_price_path, 'evaluate_price_path', lambda *a, **k: pytest.fail('False then already disproves this candidate'))
    result = check(rule(then={'field': 'close', 'op': '<', 'value': 0}, path={'guard': {'max_drawdown_pct': 5}}), rows, calendar)
    assert result['result'] is False and result['selected_event'] is None
    assert result['search_summary']['rejected_candidates'] == 2


def test_known_false_path_disproves_an_unknown_then():
    calendar, rows = two_events(); context = HistoryContext(rows, calendar, calendar[-1])
    context.daily[calendar[-1]]['macd_dif'] = None
    # The only event closes at30, while today's20 proves the zero-drawdown guard false.
    node = rule(2, 2, then={'field': 'macd_dif', 'op': '>', 'value': 0, 'period': 'D'},
                path={'guard': {'max_drawdown_pct': 0}})
    result = check(node, rows, calendar, context=context)
    assert result['result'] is False and result['search_summary']['unknown_candidates'] == 0


def test_then_and_price_path_cannot_be_satisfied_by_different_events():
    calendar = days(); rows = quotes(calendar)
    rows[-4]['amount'] = rows[-6]['amount'] = 5000
    price(rows[-6], 10); price(rows[-4], 20); price(rows[-2], 14); price(rows[-1], 15)
    node = rule(then={'field': 'close', 'op': '<', 'right_event_field': 'close'},
                path={'guard': {'max_drawdown_pct': 25}})
    result = check(node, rows, calendar)
    assert result['result'] is False and result['selected_event'] is None
    assert result['price_path_checks'] is None
    assert result['search_summary']['where_matches'] == result['search_summary']['rejected_candidates'] == 2
    # Newer event passes then but fails guard; older event passes guard but fails then.
    for match, then_state, path_state in [('latest', True, False), ('earliest', False, True)]:
        strict = deepcopy(node); strict['history_event']['match'] = match
        proof = check(strict, rows, calendar)
        assert proof['checks'][0]['result'] is then_state and proof['price_path_checks']['result'] is path_state


@pytest.mark.parametrize('outer,extra,expected', [('all', False, False), ('all', None, None), ('any', False, True)])
def test_any_event_obeys_outer_three_valued_groups(outer, extra, expected):
    calendar, rows = two_events()
    leaf = {'field': 'turnover_ratio_pct', 'op': '>', 'value': 0} if extra is None else {'field': 'close', 'op': '<', 'value': 0}
    result = check({outer: [rule(), leaf]}, rows, calendar)
    assert result['result'] is expected
    assert result['checks'][0]['selected_event']['offset'] == 4


def test_one_and_750_session_boundaries_exclude_today_and_dates_outside_window():
    calendar = days(752); rows = quotes(calendar); rows[-751]['amount'] = 5000
    node = rule(1, 750)
    result = check(node, rows, calendar)
    assert result['result'] is True and result['selected_event']['offset'] == 750
    assert result['search_summary']['examined_sessions'] == 750
    assert result['search_summary']['complete_window_scan'] is True
    rows[-751]['amount'] = 1000; rows[-752]['amount'] = rows[-1]['amount'] = 5000
    assert check(node, rows, calendar)['result'] is False
    assert check(rule(1, 1), rows, calendar)['result'] is False
    rows[-2]['amount'] = 5000
    assert check(rule(1, 1), rows, calendar)['selected_event']['offset'] == 1
    for window in ({'from': 0, 'to': 1}, {'from': 1, 'to': 751}):
        invalid = deepcopy(node); invalid['history_event']['window'] = window
        with pytest.raises(ValueError):validate(invalid)


@pytest.mark.parametrize('mode', ['complete', 'gap', 'where_unknown_false'])
def test_scalar_and_compact_batch_keep_any_selection_and_three_values(tmp_path, mode):
    calendar, rows = two_events(); node = rule()
    if mode == 'gap':rows.pop(-2)
    elif mode == 'where_unknown_false':
        node = rule(where={'field': 'limit_up', 'op': '==', 'value': True}, then={'field': 'close', 'op': '<', 'value': 0, 'period': 'D'})
    market = fixture_market(tmp_path, rows, calendar)
    shared = prepare_shared(market, ['600001.SH'], [node], calendar, calendar[-1])
    compact = contexts_for_batch(market, ['600001.SH'], [node], calendar, calendar[-1], shared=shared)['600001.SH']
    scalar = HistoryContext(rows, calendar, calendar[-1], 'history-matrix')
    left = evaluate(node, {}, compact.at(calendar[-1])); right = evaluate(node, {}, scalar.at(calendar[-1]))
    assert left['result'] is right['result']
    assert left['selected_event'] == right['selected_event']
    assert left['search_summary'] == right['search_summary']


def test_selection_tracking_and_chart_freeze_the_any_winner_across_later_strategy_edits(ws, monkeypatch):
    market, client = ws; client.app.include_router(router)
    for values in market.data.values():
        for row in values:row['corporate_action_coverage_known'] = True
    market.data['600001.SH'][40]['volume'] = market.data['600001.SH'][60]['volume'] = 300
    price(market.data['600001.SH'][60], 200)
    monkeypatch.setattr(market, 'history_for_instruments', lambda symbols, start=None, end=None:
        {symbol: [row for row in market.bars(symbol, end) if start is None or row['date'] >= start] for symbol in symbols}, raising=False)
    node = rule(1, 60, where={'field': 'volume_ratio', 'op': '>=', 'value': 3})
    strategy = make_strategy(client, node); run, tracked = add_one(client, strategy)
    matched = next(row for row in run['items'] if row['decision'] == 'matched')
    assert matched['checks']['selected_event']['date'] == market.days[40]
    with core.connect() as c:
        assert c.execute('SELECT evaluator_version FROM strategy_version WHERE strategy_id=?', (strategy['strategy_id'],)).fetchone()[0] == 'workspace-v12-history-any'
    latest = deepcopy(node); latest['history_event']['match'] = 'latest'
    assert client.patch('/api/v1/strategies/' + strategy['strategy_id'], json={'definition': latest}).status_code == 200
    saved = client.get('/api/v1/strategy-runs/' + run['run_id']).json()
    assert next(row for row in saved['items'] if row['decision'] == 'matched')['checks'] == matched['checks']
    baselines = client.get('/api/v1/instruments/600001.SH/tracking-baselines').json()
    frozen = next(row for row in baselines['items'] if row['round_id'] == tracked['round_id'])['historical_event_checks'][0]
    assert frozen['history_event']['match'] == 'any' and frozen['selected_event']['date'] == market.days[40]
    assert strategies.evaluator_for(latest, 'D') == 'workspace-v8-bounded-history-events'
    assert strategies.evaluator_for({'field': 'close', 'op': '>', 'value': 0}, 'D', {'entry': node}) == 'workspace-v12-history-any'


def test_backtest_replays_any_on_its_own_date_and_freezes_the_same_winner():
    calendar, rows = two_events(); node = rule()
    class Market:
        def calendar(self):return calendar
        def instruments(self, limit=10000):return [{'instrument_id': '600001.SH', 'name': '任意事件合成fixture', 'kind': 'stock', 'list_date': '2000-01-01'}]
        def status(self):return {'symbols': 1, 'snapshot_id': 'any-backtest-fixture'}
        def iter_histories(self, end):yield '600001.SH', [row for row in rows if row['date'] <= end]
    _, signals, _, _ = prepare(Market(), {'period': 'D', 'definition': node}, calendar[-20], calendar[-1])
    proof = signals[calendar[-1]][0]['checks']
    expected = check(node, rows, calendar)
    assert proof['selected_event'] == expected['selected_event']
    assert proof['search_summary'] == expected['search_summary']
    before = deepcopy(rows)
    for row in rows[-2:]:price(row, 1000)
    day = calendar[-3]
    assert check(node, rows, calendar, as_of=day)['selected_event'] == check(node, before, calendar, as_of=day)['selected_event']


@pytest.mark.parametrize('state', [False, None])
def test_compact_chart_never_projects_an_any_fallback_event(state):
    from test_chart_tracking import frozen_event
    node = frozen_event(); node['history_event']['match'] = 'any'; node['result'] = state
    assert chart_tracking._historical_event_checks(node, '2026-01-08') == ([], False)


def test_cross_cycle_remains_latest_only_and_unknown_match_values_are_rejected():
    for match in ('any', 'earliest', 'invalid'):
        node = {'history_event': {'window': {'from': 1, 'to': 30}, 'match': match,
            'where': {'field': 'macd_cross', 'op': '==', 'value': True, 'period': 'D'},
            'then': {'field': 'close', 'op': '>', 'value': 0},
            'price_path': {'cross_cycle': {'indicator': 'macd', 'require_dead_cross': True}}}}
        with pytest.raises(ValueError):validate(node)
