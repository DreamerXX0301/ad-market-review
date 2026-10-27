"""Recent-event and independent-today conditions use only bounded local fixtures."""
from copy import deepcopy
import math

import pytest

from app.analytics.backtest_engine import availability, prepare
from app.domain.conditions import evaluate, metrics_for, validate
from app.domain.history_events import read_start
from app.services.history_events import contexts_for_batch, prepare_shared
from app.services.strategy_limits import LimitConditionEvidence
from app.services import strategy_limits
from test_history_events import days, quotes, event, check
from test_history_event_panels import fixture_market
from test_strategy_limits import pool, event as source_event, SYMBOL, OTHER
from test_workspace import ws, advance


TODAY = {'field': 'change_pct', 'op': '>', 'value': 0, 'period': 'D'}


@pytest.mark.parametrize('offset,matched', [(0, False), (1, True), (2, True), (30, True), (31, False)])
def test_recent_window_includes_yesterday_previous_day_and_30_but_not_today_or_31(offset, matched):
    calendar = days(); rows = quotes(calendar); rows[-1-offset]['volume'] = 300
    result = check(event(1, 30, then=TODAY), rows, calendar)
    assert result['result'] is matched
    if matched: assert result['selected_event']['offset'] == offset
    else: assert result['selected_event'] is None


@pytest.mark.parametrize('offset,matched', [(4, False), (5, True), (20, True), (21, False)])
def test_custom_window_includes_both_5_and_20_boundaries(offset, matched):
    calendar = days(); rows = quotes(calendar); rows[-1-offset]['volume'] = 300
    result = check(event(5, 20, then=TODAY), rows, calendar)
    assert result['result'] is matched
    if matched: assert result['selected_event']['offset'] == offset


def test_recent_event_is_not_a_price_comparison_and_today_clause_is_independent():
    calendar = days(); rows = quotes(calendar); rows[-3]['volume'] = 300
    rows[-1].update(close=9, low=8, open=9)
    node = event(1, 30, then={'field': 'change_pct', 'op': '>', 'value': -90, 'period': 'D'})
    result = check(node, rows, calendar)
    assert result['result'] is True and result['selected_event']['offset'] == 2
    node['history_event']['then']['value'] = 0
    result = check(node, rows, calendar)
    assert result['result'] is False and result['selected_event']['offset'] == 2


def test_direct_domain_context_does_not_trust_unverified_price_panel_limit_flags():
    calendar = days(); rows = quotes(calendar)
    for row in rows: row['limit_up'] = True
    result = check(limit_rule(), rows, calendar)
    assert result['result'] is None and result['selected_event'] is None
    assert '尚未核验' in result['event_checks']['reason']


@pytest.mark.parametrize('field', ['change_pct', 'macd_cross', 'kd_cross'])
def test_shared_and_scalar_explicit_today_clauses_have_identical_daily_inputs(tmp_path, field):
    calendar = days(180); rows = quotes(calendar)
    for index, row in enumerate(rows):
        close = 25 + math.sin(index * .25) * 2
        row.update(open=close, close=close, high=close + .5, low=close - .5)
    candidate = next(index for index in range(90, len(rows))
                     if metrics_for(rows, calendar[index], calendar).get(field) is True) if field.endswith('cross') else 130
    day = calendar[candidate]; rows[candidate-2]['volume'] = 300
    current = {'field': field, 'op': '==' if field.endswith('cross') else '>',
               'value': True if field.endswith('cross') else -100, 'period': 'D'}
    node = event(1, 30, then=current)
    market = fixture_market(tmp_path, rows, calendar)
    shared = prepare_shared(market, [SYMBOL], [node], calendar, day)
    compact = contexts_for_batch(market, [SYMBOL], [node], calendar, day, shared=shared)[SYMBOL]
    scalar = contexts_for_batch(market, [SYMBOL], [node], calendar, day, histories={SYMBOL: rows})[SYMBOL]
    expected = metrics_for(rows, day, calendar)[field]
    assert compact.daily[day][field] == expected and scalar.daily[day][field] == expected
    assert evaluate(node, {}, compact.at(day))['result'] is True
    assert evaluate(node, {}, scalar.at(day))['result'] is True
    # A later price/action cannot alter an already closed daily input.
    assert max(compact.daily) == day
    if field.endswith('cross'): assert read_start([node], calendar, day) == calendar[0]


def source_setup(monkeypatch, market, calendar, rows, event_offsets=(2,), *, day=None):
    day = day or calendar[-1]; position = calendar.index(day)
    selected = {calendar[position-offset] for offset in event_offsets}
    prices = {row['date']: row['close'] for row in rows}
    supplement = {'snapshot_id': market.status()['snapshot_id'], 'annual_pools': {
        observed: pool(observed, [source_event(observed, SYMBOL if observed in selected else OTHER,
                                               prices[observed] if observed in selected else 10)])
        for observed in calendar[max(0, position-31):position+1]}}
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda snapshot, **_kwargs: deepcopy(supplement))
    return supplement


def limit_rule(from_=1, to=30, match='latest'):
    return event(from_, to, match, where={'field': 'limit_up', 'op': '==', 'value': True, 'period': 'D'}, then=TODAY)


@pytest.mark.parametrize('offset,matched', [(0, False), (1, True), (2, True), (30, True), (31, False)])
def test_historical_limit_pool_is_same_exact_date_on_shared_and_scalar_paths(tmp_path, monkeypatch, offset, matched):
    calendar = days(); rows = quotes(calendar)
    for row in rows: row['limit_up'] = True  # Price-panel flags are deliberately untrusted.
    market = fixture_market(tmp_path, rows, calendar)
    source_setup(monkeypatch, market, calendar, rows, (offset,))
    node = limit_rule(); evidence = LimitConditionEvidence(market)
    shared = prepare_shared(market, [SYMBOL], [node], calendar, calendar[-1])
    compact = contexts_for_batch(market, [SYMBOL], [node], calendar, calendar[-1], shared=shared, limit_evidence=evidence)[SYMBOL]
    scalar = contexts_for_batch(market, [SYMBOL], [node], calendar, calendar[-1], histories={SYMBOL: rows}, limit_evidence=evidence)[SYMBOL]
    for context in (compact, scalar):
        result = evaluate(node, {}, context.at(calendar[-1]))
        assert result['result'] is matched
        if matched:
            assert result['selected_event']['offset'] == offset
            proof = result['event_checks']['evidence']
            assert proof['snapshot_id'] == 'history-matrix'
            assert proof['source_proofs'][0]['state'] is True
    assert len(evidence._sources) <= 2 * 31, 'source code indexes are shared by date, never rescanned per security'


@pytest.mark.parametrize('mode', ['missing_source', 'wrong_close', 'wrong_date', 'future_only', 'duplicate_quote'])
def test_historical_limit_unknown_and_conflict_are_not_skipped_or_inferred(tmp_path, monkeypatch, mode):
    calendar = days(); rows = quotes(calendar); market = fixture_market(tmp_path, rows, calendar)
    supplement = source_setup(monkeypatch, market, calendar, rows, (2,))
    yesterday = calendar[-2]
    if mode == 'missing_source': supplement['annual_pools'].pop(yesterday)
    elif mode == 'wrong_close': supplement['annual_pools'][yesterday] = pool(yesterday, [source_event(yesterday, close=999)])
    elif mode == 'wrong_date': supplement['annual_pools'][yesterday] = pool(yesterday, [source_event(calendar[-1], close=rows[-2]['close'])])
    elif mode == 'future_only': supplement['annual_pools'] = {calendar[-1]: pool(calendar[-1], [source_event(calendar[-1], close=rows[-1]['close'])])}
    else: rows.append(dict(rows[-2]))
    context = contexts_for_batch(market, [SYMBOL], [limit_rule()], calendar, calendar[-1], histories={SYMBOL: rows})[SYMBOL]
    result = evaluate(limit_rule(), {}, context.at(calendar[-1]))
    assert result['result'] is None and result['selected_event'] is None
    assert result['evidence']['blocking_offset'] == 1


@pytest.mark.parametrize('match,offset', [('latest', 5), ('earliest', 20)])
def test_custom_source_window_uses_inclusive_bounds_and_explicit_priority(tmp_path, monkeypatch, match, offset):
    calendar = days(); rows = quotes(calendar); market = fixture_market(tmp_path, rows, calendar)
    source_setup(monkeypatch, market, calendar, rows, (4, 5, 20, 21))
    node = limit_rule(5, 20, match)
    context = contexts_for_batch(market, [SYMBOL], [node], calendar, calendar[-1], histories={SYMBOL: rows})[SYMBOL]
    result = evaluate(node, {}, context.at(calendar[-1]))
    assert result['result'] is True and result['selected_event']['offset'] == offset


def test_future_source_pool_and_quote_cannot_become_an_earlier_event(tmp_path, monkeypatch):
    calendar = days(); rows = quotes(calendar); market = fixture_market(tmp_path, rows, calendar)
    observed = calendar[-10]
    supplement = source_setup(monkeypatch, market, calendar, rows, (), day=observed)
    future = calendar[-9]
    supplement['annual_pools'][future] = pool(future, [source_event(future, close=rows[-9]['close'])])
    node = limit_rule()
    shared = prepare_shared(market, [SYMBOL], [node], calendar, observed)
    context = contexts_for_batch(market, [SYMBOL], [node], calendar, observed, shared=shared)[SYMBOL]
    result = evaluate(node, {}, context.at(observed))
    assert result['result'] is False and result['selected_event'] is None
    assert max(context.quotes) == observed


def test_historical_limit_backtest_uses_identical_source_adapter_without_enabling_current_limit(monkeypatch):
    calendar = days(); rows = quotes(calendar)
    class Market:
        def calendar(self): return calendar
        def instruments(self, limit=10000): return [{'instrument_id': SYMBOL, 'name': 'fixture', 'kind': 'stock', 'list_date': '2000-01-01'}]
        def status(self): return {'symbols': 1, 'snapshot_id': 'fixture-history-limit'}
        def reference(self): return {}
        def iter_histories(self, end): yield SYMBOL, [row for row in rows if row['date'] <= end]
    market = Market(); node = limit_rule()
    source_setup(monkeypatch, market, calendar, rows, (2,))
    assert availability(market, {'period': 'D', 'definition': node})['available'] is True
    assert availability(market, {'period': 'D', 'definition': {'all': [node, {'field': 'limit_up', 'op': '==', 'value': True}]}})['available'] is False
    _, signals, _, _ = prepare(market, {'period': 'D', 'definition': node}, calendar[-6], calendar[-1])
    result = signals[calendar[-1]][0]['checks']
    assert result['selected_event']['date'] == calendar[-3]
    assert result['event_checks']['evidence']['source_proofs'][0]['state'] is True


def test_historical_source_selection_and_tracking_share_saved_date_and_proof(ws, monkeypatch):
    market, client = ws
    for values in market.data.values():
        for row in values: row['corporate_action_coverage_known'] = True
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'fixture-history-limit'}, raising=False)
    monkeypatch.setattr(market, 'reference', lambda: {}, raising=False)
    monkeypatch.setattr(market, 'history_for_instruments', lambda symbols, start, end: {
        symbol: [row for row in market.data[symbol] if start <= row['date'] <= end] for symbol in symbols}, raising=False)
    market_rows = market.data[SYMBOL]
    source_setup(monkeypatch, market, market.days, market_rows, (2,), day=market.entry)
    node = limit_rule()
    response = client.post('/api/v1/strategies', json={'name': 'history limit fixture', 'period': 'D', 'definition': node,
                          'policy': {'alerts': [node], 'exits': []}})
    assert response.status_code == 200, response.text
    strategy = response.json(); sid = strategy['strategy_id']
    run = client.post('/api/v1/strategies/' + sid + '/runs', json={})
    assert run.status_code == 200, run.text
    result = run.json(); matched = next(row for row in result['items'] if row['instrument_id'] == SYMBOL)
    assert matched['decision'] == 'matched'
    selected = matched['checks']['selected_event']['date']
    added = client.post('/api/v1/tracking-rounds', json={'run_id': result['run_id'], 'instrument_ids': [SYMBOL]})
    assert added.status_code == 200, added.text
    advance(market, 71)
    review = client.post('/api/v1/tracking-rounds/review', json={})
    assert review.status_code == 200, review.text
    assert review.json()['alerts'] == 1
    detail = client.get('/api/v1/tracking-rounds/' + added.json()['items'][0]['round_id']).json()
    alert = next(row for row in detail['events'] if row['event_type'] == 'alert')
    bound = alert['evidence']['alerts']['checks'][0]
    assert bound['selected_event']['date'] == selected
    assert bound['event_checks']['evidence']['source_proofs'][0]['state'] is True
