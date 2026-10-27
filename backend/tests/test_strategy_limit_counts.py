"""Rolling natural-year counts use dated closing evidence, never year-to-date counts."""
from copy import deepcopy
from datetime import date, timedelta
from math import ceil

import pytest

from app.analytics.backtest_engine import availability
from app.domain.conditions import evaluate, specs, validate
from app.services import strategies, strategy_limits
from app.services.strategy_limits import COUNT_FIELD, BATCH_SIZE, LimitConditionEvidence, count_window, quote_index
from test_workspace import ws, make_strategy, add_one, advance


SYMBOL = '600001.SH'
OTHER = '000001.SZ'
DAY = '2026-01-13'
EVENTS = ['2025-01-10', '2025-01-13', '2025-06-02', '2026-01-12', DAY]


def calendar_between(start='2024-12-30', end=DAY):
    values = []; cursor = date.fromisoformat(start); until = date.fromisoformat(end)
    while cursor <= until:
        if cursor.weekday() < 5: values.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return values


def quote(day, close=10, **extra):
    return {'date': day, 'close': close, 'volume': 100, **extra}


def event(day, symbol=SYMBOL, close=10, **extra):
    return {'date': day, 'instrument_id': symbol, 'ticker': symbol[:6], 'last_price': close, **extra}


def pool(day, items=None, **extra):
    items = [event(day, OTHER, 20)] if items is None else items
    return {'date': day, 'items': items, 'source': 'verified-fixture', 'pool_kind': 'limitup',
            'complete': True, 'pagination_complete': True, 'ambiguous_code_rows': 0,
            'raw_codes': [item['ticker'] for item in items], 'rejected_codes': [], **extra}


class CountMarket:
    def __init__(self, calendar=None):
        self.days = calendar or calendar_between()
        self.ref = {}; self.calls = []
        self.rows = {day: quote(day) for day in self.days}

    def reference(self): return self.ref
    def status(self): return {'snapshot_id': 'count-fixture'}
    def calendar(self): return self.days

    def history_for_instruments(self, symbols, start=None, end=None):
        self.calls.append((list(symbols), start, end))
        return {symbol: [dict(row) for day, row in self.rows.items() if start <= day <= end] for symbol in symbols}

    def history_panel(self, *args, **kwargs): raise AssertionError('must not materialize full market history')
    def bars(self, *args, **kwargs): raise AssertionError('must not scan each stock history')


def fixture_evidence(monkeypatch, market=None):
    market = market or CountMarket()
    document = {'snapshot_id': 'count-fixture', 'job_id': 'source-evidence-job',
                'annual_pools': {day: pool(day, [event(day)] if day in EVENTS else None) for day in market.days}}
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda snapshot, **_kwargs: deepcopy(document))
    return market, document


def computed(monkeypatch, mutate=None, calendar=None):
    market, document = fixture_evidence(monkeypatch, CountMarket(calendar))
    if mutate: mutate(market, document)
    evidence = LimitConditionEvidence(market)
    return evidence.count_series(SYMBOL, [DAY], market.days, market.rows)[DAY]


def rule(op='>=', value=3): return validate({'field': COUNT_FIELD, 'op': op, 'value': value})


def test_count_field_spec_and_constant_nonnegative_integer_validation():
    field = next(row for row in specs() if row['field'] == COUNT_FIELD)
    assert field['label'] == '截至昨日近一年涨停次数'
    assert field['type'] == 'number' and field['integer'] is True and field['constant_only'] is True
    assert field['min'] == 0 and field['step'] == 1
    assert field['operators'] == ['>=', '>', '==', '<=', '<', '!=']
    for value in [False, True, -1, 1.5, 2.0, '2', None]:
        with pytest.raises(ValueError): rule(value=value)
    for node in [{'field': COUNT_FIELD, 'op': '>', 'right_field': 'close'},
                 {'field': 'close', 'op': '>', 'right_field': COUNT_FIELD}]:
        with pytest.raises(ValueError): validate(node)
    assert rule(value=0)['value'] == 0


@pytest.mark.parametrize('observed,start,end', [
    ('2024-02-29', '2023-02-28', '2024-02-28'),
    ('2025-02-28', '2024-02-28', '2025-02-27'),
    ('2026-01-13', '2025-01-13', '2026-01-12'),
])
def test_natural_year_window_with_leap_day(observed, start, end):
    assert count_window(observed) == (start, end)


def test_exact_full_window_includes_previous_year_boundary_but_not_today(monkeypatch):
    result = computed(monkeypatch)
    assert result[COUNT_FIELD] == 3
    assert result[COUNT_FIELD + '_confirmed_count'] == 3
    assert result[COUNT_FIELD + '_lower_bound'] == result[COUNT_FIELD + '_upper_bound'] == 3
    coverage = result[COUNT_FIELD + '_coverage']
    assert coverage['complete'] is True and coverage['today_excluded'] is True
    assert coverage['confirmed_dates'] == ['2025-01-13', '2025-06-02', '2026-01-12']
    assert coverage['window_start'] == '2025-01-13' and coverage['window_end'] == '2026-01-12'
    assert coverage['covered_days'] == coverage['expected_days']
    assert evaluate(rule('=='), result)['value_state'] == 'exact'


def test_source_duplicates_and_multiple_providers_count_one_closing_day(monkeypatch):
    def mutate(market, doc):
        existing = doc['annual_pools']['2025-06-02']['items'][0]
        doc['annual_pools']['2025-06-02']['items'].append(dict(existing))
        doc['public_pools'] = {'2025-06-02': pool('2025-06-02', [event('2025-06-02')])}
    assert computed(monkeypatch, mutate)[COUNT_FIELD] == 3


@pytest.mark.parametrize('mode', ['no_quote', 'suspended', 'duplicate_quote', 'empty_pool', 'quarantine', 'wrong_price', 'future_event_date', 'identity_conflict', 'opposite_direction'])
def test_unverified_days_remain_unknown_with_honest_bounds(monkeypatch, mode):
    def mutate(market, doc):
        day = '2025-06-02'
        if mode == 'no_quote': market.rows.pop(day)
        if mode == 'suspended': market.rows[day]['volume'] = 0
        if mode == 'duplicate_quote':
            market.rows = quote_index([*market.rows.values(), quote(day)])
        if mode == 'empty_pool': doc['annual_pools'][day] = pool(day, [])
        if mode == 'quarantine': doc['annual_pools'][day] = pool(day, rejected_codes=[SYMBOL[:6]], raw_codes=[OTHER[:6], SYMBOL[:6]])
        if mode == 'wrong_price': doc['annual_pools'][day]['items'][0]['last_price'] = 11
        if mode == 'future_event_date': doc['annual_pools'][day]['items'][0]['date'] = DAY
        if mode == 'identity_conflict': doc['annual_pools'][day]['items'][0]['instrument_id'] = '600001.SZ'
        if mode == 'opposite_direction': doc['annual_down_pools'] = {day: pool(day, [event(day)], pool_kind='limitdown')}
    result = computed(monkeypatch, mutate)
    assert result[COUNT_FIELD] is None
    assert result[COUNT_FIELD + '_confirmed_count'] == 2
    assert result[COUNT_FIELD + '_lower_bound'] == 2 and result[COUNT_FIELD + '_upper_bound'] == 3
    coverage = result[COUNT_FIELD + '_coverage']
    assert coverage['complete'] is False and coverage['unknown_days'] == 1
    assert coverage['unknown_dates_sample'] == ['2025-06-02']
    assert result[COUNT_FIELD + '_reason']


def test_cross_previous_year_missing_pools_does_not_become_year_to_date(monkeypatch):
    def mutate(market, doc):
        doc['annual_pools'] = {day: record for day, record in doc['annual_pools'].items() if day.startswith('2026')}
    result = computed(monkeypatch, mutate)
    assert result[COUNT_FIELD] is None and result[COUNT_FIELD + '_confirmed_count'] == 1
    assert result[COUNT_FIELD + '_coverage']['window_start'] == '2025-01-13'
    assert result[COUNT_FIELD + '_coverage']['unknown_days'] > 200
    assert evaluate(rule('>=', 1), result)['result'] is True
    assert evaluate(rule('==', 1), result)['result'] is None


def test_truncated_calendar_cannot_supply_false_upper_bound_or_exact_zero(monkeypatch):
    result = computed(monkeypatch, calendar=calendar_between('2026-01-01'))
    assert result[COUNT_FIELD] is None and result[COUNT_FIELD + '_lower_bound'] == 1
    assert result[COUNT_FIELD + '_upper_bound'] is None
    coverage = result[COUNT_FIELD + '_coverage']
    assert coverage['calendar_complete'] is False and coverage['expected_days'] is None
    assert evaluate(rule('<=', 5), result)['result'] is None


@pytest.mark.parametrize('op,threshold,expected', [
    ('>=', 3, True), ('>', 2, True), ('<', 3, False), ('<=', 2, False),
    ('==', 3, None), ('!=', 3, None), ('==', 2, False), ('!=', 2, True),
    ('>', 3, None), ('<=', 3, None), ('>=', 4, None), ('<', 4, None),
])
def test_partial_lower_bound_only_proves_safe_comparisons(op, threshold, expected):
    metrics = {COUNT_FIELD: None, COUNT_FIELD + '_lower_bound': 3,
               COUNT_FIELD + '_upper_bound': None, COUNT_FIELD + '_confirmed_count': 3,
               COUNT_FIELD + '_coverage': {'complete': False, 'window_start': '2025-01-13', 'window_end': '2026-01-12', 'covered_days': 190, 'expected_days': 261}}
    result = evaluate(rule(op, threshold), metrics)
    assert result['result'] is expected and result['actual'] is None
    assert result['value_state'] == 'partial_bound'
    assert result['actual_lower_bound'] == 3 and result['actual_upper_bound'] is None
    assert result['confirmed_count'] == 3 and result['covered_days'] == 190
    assert result['reason']


@pytest.mark.parametrize('op,threshold,expected', [('>=', 5, False), ('>', 4, False), ('<=', 4, True), ('<', 5, True), ('==', 5, False), ('!=', 5, True)])
def test_reliable_upper_bound_can_also_prove_comparisons(op, threshold, expected):
    metrics = {COUNT_FIELD: None, COUNT_FIELD + '_lower_bound': 3, COUNT_FIELD + '_upper_bound': 4}
    result = evaluate(rule(op, threshold), metrics)
    assert result['result'] is expected and result['actual'] is None


def test_tracking_many_observations_reuses_prefix_states_and_excludes_future(monkeypatch):
    market, document = fixture_evidence(monkeypatch)
    evidence = LimitConditionEvidence(market)
    original = evidence._count_state; seen = []
    def state(symbol, day, row): seen.append(day); return original(symbol, day, row)
    monkeypatch.setattr(evidence, '_count_state', state)
    observations = market.days[-10:]
    result = evidence.count_series(SYMBOL, observations, market.days, market.rows)
    assert len(seen) == len(set(seen))
    assert max(seen) < DAY
    assert result['2026-01-12'][COUNT_FIELD + '_confirmed_count'] == 2
    assert result[DAY][COUNT_FIELD + '_confirmed_count'] == 3


def test_large_universe_uses_bounded_batches_and_one_source_index_per_day(monkeypatch):
    market, document = fixture_evidence(monkeypatch)
    evidence = LimitConditionEvidence(market)
    symbols = [str(100000 + index) + '.SH' for index in range(513)]
    proof_calls = []; original = strategy_limits._evidence
    def proof(*args, **kwargs): proof_calls.append(args[2]); return original(*args, **kwargs)
    monkeypatch.setattr(strategy_limits, '_evidence', proof)
    result = evidence.batch_count_metrics(market, symbols, DAY, market.days)
    expected = [day for day in market.days if '2025-01-13' <= day < DAY]
    assert len(market.calls) == ceil(len(symbols) / BATCH_SIZE)
    assert all(len(batch) <= BATCH_SIZE and start == '2025-01-13' and end == DAY for batch, start, end in market.calls)
    assert len(evidence._count_days) == len(expected)
    # All ordinary codes reuse a date's compiled absence proof rather than
    # running raw-code regex/uniqueness validation 513 times for that date.
    assert len(proof_calls) == len(expected)
    assert len(result) == 513 and all(row[COUNT_FIELD] == 0 for row in result.values())


def test_no_batch_capability_never_falls_back_to_per_stock_scan(monkeypatch):
    market, document = fixture_evidence(monkeypatch)
    evidence = LimitConditionEvidence(market)
    result = evidence.batch_count_metrics(object(), [SYMBOL], DAY, market.days)[SYMBOL]
    assert result[COUNT_FIELD] is None and result[COUNT_FIELD + '_confirmed_count'] == 0
    assert not market.calls


def test_strategy_combines_count_with_today_yesterday_and_freezes_partial_evidence(ws, monkeypatch):
    market, client = ws; day = market.entry; prior = market.days[69]
    document = {'annual_pools': {market.days[index]: pool(market.days[index], [event(market.days[index], close=market.data[SYMBOL][index]['close'])]) for index in (68, 69)}}
    document['annual_pools'][day] = pool(day, [event(day, OTHER, market.data[OTHER][70]['close'])])
    monkeypatch.setattr(market, 'reference', lambda: {}, raising=False)
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'count-fixture'}, raising=False)
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda snapshot, **_kwargs: deepcopy(document))
    calls = []
    def batch(symbols, start=None, end=None):
        calls.append((start, end))
        return {symbol: [row for row in market.data[symbol] if start <= row['date'] <= end] for symbol in symbols}
    monkeypatch.setattr(market, 'history_for_instruments', batch, raising=False)
    definition = {'all': [rule('>=', 2), {'field': 'limit_up', 'op': '==', 'value': False}, {'field': 'prev_limit_up', 'op': '==', 'value': True}]}
    strategy = make_strategy(client, definition)
    response = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={})
    assert response.status_code == 200, response.text
    run = response.json()
    member = next(row for row in run['items'] if row['instrument_id'] == SYMBOL)
    assert member['decision'] == 'matched' and member['metrics'][COUNT_FIELD] is None
    assert member['metrics'][COUNT_FIELD + '_confirmed_count'] == 2
    check = member['checks']['checks'][0]
    assert check['actual'] is None and check['actual_lower_bound'] == 2 and check['result'] is True
    assert check['window_end'] < day and check['value_state'] == 'partial_bound'
    assert len(calls) == 1 # Count and today/yesterday share the same bounded read.
    document.clear()
    later = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={}).json()
    candidate = next(row for row in later['items'] if row['instrument_id'] == SYMBOL)
    assert candidate['decision'] == 'unknown'
    assert candidate['metrics'][COUNT_FIELD + '_confirmed_count'] == 0
    assert candidate['metrics'][COUNT_FIELD + '_coverage']['complete'] is False
    assert client.get('/api/v1/strategy-runs/' + run['run_id']).json()['items'] == run['items']


def test_tracking_count_alert_has_same_window_without_turning_partial_equality_into_exit(ws, monkeypatch):
    market, client = ws; day = market.entry
    document = {'annual_pools': {day: pool(day, [event(day, close=market.data[SYMBOL][70]['close'])])}}
    monkeypatch.setattr(market, 'reference', lambda: {}, raising=False)
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'count-fixture'}, raising=False)
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda snapshot, **_kwargs: deepcopy(document))
    strategy = make_strategy(client, policy={'alerts': [rule('>=', 1)], 'exits': [rule('==', 1)]})
    _, tracked = add_one(client, strategy)
    advance(market, 71)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    assert response.json()['alerts'] == 1 and response.json()['ended'] == 0
    detail = client.get('/api/v1/tracking-rounds/' + tracked['round_id']).json()
    review = next(item for item in detail['events'] if item['event_type'] == 'review' and item['business_date'] == market.days[71])
    assert detail['status'] == 'tracking'
    assert review['evidence']['metrics'][COUNT_FIELD] is None
    assert review['evidence']['alerts']['checks'][0]['actual_lower_bound'] == 1
    assert review['evidence']['exits']['result'] is None
    before = deepcopy(detail['events'])
    document.clear()
    assert client.post('/api/v1/tracking-rounds/review', json={}).json()['reviewed'] == 0
    assert client.get('/api/v1/tracking-rounds/' + tracked['round_id']).json()['events'] == before


def test_new_count_policy_added_during_review_uses_frozen_local_inputs(ws, monkeypatch):
    market, client = ws; day = market.entry
    document = {'annual_pools': {day: pool(day, [event(day, close=market.data[SYMBOL][70]['close'])])}}
    monkeypatch.setattr(market, 'reference', lambda: {}, raising=False)
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'count-fixture'}, raising=False)
    monkeypatch.setattr(strategy_limits, 'load_supplement', lambda snapshot, **_kwargs: deepcopy(document))
    _, tracked = add_one(client, make_strategy(client))
    advance(market, 71)
    original = market.bars; added = []
    def bars_with_policy(symbol, as_of=None):
        result = original(symbol, as_of)
        if not added:
            added.append(True)
            from app.services import tracking
            tracking.change_tracking_policy(tracked['round_id'], {'policy': {'alerts': [rule('>=', 1)], 'exits': []}})
        return result
    monkeypatch.setattr(market, 'bars', bars_with_policy)
    response = client.post('/api/v1/tracking-rounds/review', json={})
    assert response.status_code == 200, response.text
    assert response.json()['alerts'] == 1


def test_evaluator_upgrade_preserves_old_saved_runs(ws, monkeypatch):
    market, client = ws
    current_version = strategies.ENGINE_VERSION
    monkeypatch.setattr(strategies, 'ENGINE_VERSION', 'workspace-v3-dated-limit-conditions')
    strategy = make_strategy(client)
    original = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={}).json()
    monkeypatch.setattr(strategies, 'ENGINE_VERSION', current_version)
    later = client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={}).json()
    assert later['strategy_version'] == original['strategy_version'] + 1
    saved = client.get('/api/v1/strategy-runs/' + original['run_id']).json()
    assert saved['strategy_version'] == original['strategy_version'] and saved['items'] == original['items']


@pytest.mark.parametrize('lower,upper', [(-1, 5), (True, 5), (2, False), (3, 2), (2, -1), (2, 2.5)])
def test_invalid_or_contradictory_bounds_are_entirely_unknown(lower, upper):
    result = evaluate(rule('>=', 0), {COUNT_FIELD: None, COUNT_FIELD + '_lower_bound': lower, COUNT_FIELD + '_upper_bound': upper})
    assert result['result'] is None and result['value_state'] == 'unknown'
    assert result['actual_lower_bound'] is None and result['actual_upper_bound'] is None
    assert '矛盾' in result['reason']


def test_backtest_count_is_explicitly_unsupported():
    result = availability(CountMarket(), {'period': 'D', 'definition': rule()})
    assert result['available'] is False
    assert '完整滚动历史来源证据' in result['reason'] and '年内次数' in result['reason']
