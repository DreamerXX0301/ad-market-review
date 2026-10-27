"""Parent structure replay preserves full results across pool failure boundaries."""
from copy import deepcopy

import pytest

from app.domain.conditions import metrics_for, normalized_metric_rows, validate
from app.domain.strategy_prices import prepare_condition_prices
from app.services import selection_compute as compute, selection_compute_pool as pool
from app.services import strategies, strategy_periods as periods, strict_structure_cache as strict
from app.services.task_service import TaskCancelled
from test_selection_shared_basis import inputs


@pytest.fixture
def warm_structures(inputs, monkeypatch):
    market, day, _, token, generation = inputs
    plan = periods.compile_metric_plan(validate({'all': [
        {'field': 'higher_lows', 'op': 'confirmed_rising_lows', 'value': 60},
        {'field': 'boll_signal', 'op': 'matches', 'value': {'band_period': 22, 'rank_window': 25}},
        {'field': 'boll_signal', 'op': 'matches', 'period': 'W',
         'value': {'band_period': 20, 'rank_window': 25}},
    ]}))
    contexts = {}; expected = {}; symbols = []
    template_rows = market.data['600000.SH']; template_actions = market.events['600000.SH']
    for number in range(8):
        symbol = f'{600010+number:06}.SH'; symbols.append(symbol)
        rows = deepcopy(template_rows); actions = deepcopy(template_actions)
        for row in rows: row['instrument_id'] = symbol
        for action in actions: action['thscode'] = symbol
        context = prepare_condition_prices(rows, actions, market.days, day, 'forward', instrument_id=symbol,
            actions_coverage={'known': True, 'source_token': token, 'snapshot_id': generation[0], 'reason': None})
        contexts[symbol] = context; expected[symbol] = {}
        for period in plan.periods:
            periods.cached_metrics(rows, day, market.days, period, instrument_id=symbol,
                generation=generation, request=periods.metric_request(), price_context=context)
            key = periods._summary_key(day, period, symbol, generation, context)
            assert strict.prepare(key, normalized_metric_rows(context['price_rows'], day),
                                  market.days, day, period) is not None
            requested = plan.requests[period]
            # Original full metric calculation is the reference, including all
            # proofs/unknowns, rather than a second call to the new pool helper.
            expected[symbol][period] = metrics_for(rows, day, market.days, period,
                include_higher_lows=requested.include_higher_lows,
                boll_configs=requested.boll_configs, price_context=deepcopy(context))
    def no_source(*_args, **_kwargs):
        pytest.fail('a structure replay must not reopen a verified source or rebuild a quotation seed')
    monkeypatch.setattr(market, 'bars', no_source)
    monkeypatch.setattr(market, 'actions_for_instruments', no_source)
    monkeypatch.setattr(strict, 'build', no_source)
    return market, day, symbols, token, generation, contexts, plan, expected


class ControlledSession:
    def __init__(self, operation):
        self.operation = operation; self.stats = {}; self.calls = 0; self.closed = False

    def map(self, function, requests):
        self.calls += 1
        assert function is compute.prepare_job and len(requests) == 8
        assert all(request['kind'] == 'strict-structures' for request in requests)
        return self.operation(function, requests)


def test_pool_unavailable_completes_the_original_serial_result_without_partial_publication(warm_structures, monkeypatch):
    market, day, symbols, token, generation, contexts, plan, expected = warm_structures
    before_higher = deepcopy(periods._higher_lows_cache); before_bands = deepcopy(periods._boll_cache)
    def unavailable(function, requests):
        # A child may have completed a valid result before another child's IPC
        # fails. That unaccepted partial batch must never enter the parent cache.
        function(requests[0])
        assert periods._higher_lows_cache == before_higher and periods._boll_cache == before_bands
        session.closed = True
        raise pool.PoolUnavailable('bounded transport fixture')
    session = ControlledSession(unavailable)
    monkeypatch.setattr(pool, 'current_session', lambda: None if session.closed else session)
    histories, ready, daily = strategies.selection_metric_batch(market, symbols, day, plan, generation,
        price_basis='forward', source_token=token)
    assert histories == daily == {} and ready == expected
    assert session.calls == 1 and session.closed and session.stats['fallbacks'] == 1


@pytest.mark.parametrize('failure', ['identity', 'cancel', 'calculation'])
def test_identity_and_control_errors_never_publish_or_fall_back(warm_structures, monkeypatch, failure):
    _, day, symbols, _, generation, contexts, plan, _ = warm_structures
    cancelled = TaskCancelled('structure fixture cancelled')
    calculation = pool.CalculationFailed('FixtureCalculationError', 0)
    def rejected(function, requests):
        if failure == 'cancel': raise cancelled
        if failure == 'calculation': raise calculation
        values = [function(request) for request in requests]
        values[-1]['identity'] = ('wrong-symbol', *values[-1]['identity'][1:])
        return values
    session = ControlledSession(rejected)
    monkeypatch.setattr(pool, 'current_session', lambda: session)
    def no_publish(*_args, **_kwargs): pytest.fail('unaccepted results must never be published')
    monkeypatch.setattr(periods, '_put', no_publish)
    expected_error = {'identity': ValueError, 'cancel': TaskCancelled, 'calculation': pool.CalculationFailed}[failure]
    with pytest.raises(expected_error) as caught:
        periods.prepare_saved_structures(symbols, day, plan.periods, plan, generation, contexts)
    if failure == 'cancel': assert caught.value is cancelled
    elif failure == 'calculation': assert caught.value is calculation
    assert session.calls == 1 and session.stats.get('fallbacks', 0) == 0


def test_zero_operator_budget_bypasses_both_parent_pool_entries_and_keeps_serial_results(warm_structures, monkeypatch):
    from app.services import condition_basis_cache as basis
    market, day, symbols, token, generation, contexts, plan, expected = warm_structures
    monkeypatch.setenv(pool.WORKER_BUDGET_ENV, '0')
    with pool.compute_session() as session:
        def no_pool(*_args, **_kwargs): pytest.fail('an explicitly disabled pool must never be called')
        monkeypatch.setattr(session, 'map', no_pool)
        histories, ready, daily = strategies.selection_metric_batch(market, symbols, day, plan, generation,
            price_basis='forward', source_token=token)
        assert histories == daily == {} and ready == expected
        actions = {symbol: [{**event, 'thscode': symbol} for event in market.events['600000.SH']]
                   for symbol in symbols}
        keys = {symbol: basis.key_for(symbol, day, generation, token) for symbol in symbols}
        assert compute.prepare_cold(market, symbols,
            {symbol: context['raw_rows'] for symbol, context in contexts.items()}, actions,
            day, market.days, plan, plan.periods, generation, token, keys, snapshot=generation[0]) is None
        assert session.stats['worker_pids'] == [] and session.stats.get('fallbacks', 0) == 0
    assert session.stats['remaining_pids'] == []
