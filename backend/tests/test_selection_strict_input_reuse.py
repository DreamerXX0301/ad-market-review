"""Strict quote preparation preserves full proofs while avoiding repeat work."""
from copy import deepcopy

import pytest

from app.analytics import boll_conditions, higher_lows, indicators
from app.domain import conditions, history_events
from app.domain.strategy_prices import prepare_condition_prices
from test_history_materialization_tail import sample


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('fault', ['none', 'missing', 'duplicate', 'zero', 'negative', 'missing_volume',
                                  'unknown_coverage', 'action', 'invalid_ohlc', 'missing_amount', 'unexpected'])
def test_vector_preparation_matches_original_strict_full_evidence(period, fault):
    rows, calendar = sample(390)
    if fault == 'missing': rows.pop(-17)
    elif fault == 'duplicate': rows.insert(-17, dict(rows[-17]))
    elif fault == 'zero': rows[-17]['volume'] = 0
    elif fault == 'negative': rows[-17]['close'] = -1
    elif fault == 'missing_volume': rows[-17].pop('volume')
    elif fault == 'unknown_coverage': rows[-17].pop('corporate_action_coverage_known')
    elif fault == 'action': rows[-17]['corporate_action'] = True
    elif fault == 'invalid_ohlc': rows[-17]['high'] = rows[-17]['low'] - 1
    elif fault == 'missing_amount': rows[-17]['amount'] = None
    elif fault == 'unexpected':
        rows[-17]['date'] = '2024-01-06'
    prepared = conditions.normalized_metric_rows(rows, calendar[-1])
    ordinary = list(deepcopy(prepared))
    for module in (higher_lows, boll_conditions):
        assert module._period_rows(prepared, calendar, calendar[-1], period) == module._period_rows(ordinary, calendar, calendar[-1], period)
    assert higher_lows.latest(prepared, calendar, calendar[-1], period) == higher_lows.latest(ordinary, calendar, calendar[-1], period)
    configs = [{'band_period': 5, 'rank_window': 10}, {'band_period': 20, 'rank_window': 125, 'mode': 'squeeze'}]
    assert boll_conditions.latest_states(prepared, calendar, calendar[-1], period, configs) == boll_conditions.latest_states(ordinary, calendar, calendar[-1], period, configs)


def test_prepared_structure_never_repeats_scalar_numeric_validation(monkeypatch):
    rows, calendar = sample(390)
    prepared = conditions.normalized_metric_rows(rows, calendar[-1])
    def repeated(*args):
        pytest.fail('normalized structure must use one vector validation')
    monkeypatch.setattr(boll_conditions, '_valid', repeated)
    monkeypatch.setattr(higher_lows, '_valid', repeated)
    for period in ('D', 'W', 'M'):
        assert higher_lows.latest(prepared, calendar, calendar[-1], period)['observed_date']
        assert boll_conditions.latest_states(prepared, calendar, calendar[-1], period, [{}])


@pytest.mark.parametrize('fault', ['none', 'gap', 'zero', 'amount', 'coverage', 'invalid'])
def test_history_tail_reuses_only_identical_indicator_seed(monkeypatch, fault):
    rows, calendar = sample(390)
    if fault == 'gap':rows.pop(-16)
    elif fault == 'zero':rows[-16]['volume'] = 0
    elif fault == 'amount':rows[-16]['amount'] = None
    elif fault == 'coverage':rows[-16]['corporate_action_coverage_known'] = False
    elif fault == 'invalid':rows[-16]['high'] = rows[-16]['low']-1
    day = calendar[-1]
    context = prepare_condition_prices(rows, [], calendar, day, 'forward', instrument_id='600000.SH',
        actions_coverage={'known':True, 'source_token':('fixed', 'source'), 'snapshot_id':'market'})
    expected = conditions.metrics_for(rows, day, calendar, price_context=deepcopy(context))
    for lane in ('raw_rows', 'price_rows'):context[lane] = conditions.prepare_metric_bars(context[lane])
    calls = []
    original = indicators.enrich_bars
    def observed(values, *args, **kwargs):
        calls.append(len(values));return original(values, *args, **kwargs)
    monkeypatch.setattr(indicators, 'enrich_bars', observed)
    history_events.HistoryContext(rows, calendar, day, 'market', retain_start=calendar[-64], price_context=context)
    before = list(calls)
    actual = conditions.metrics_for(rows, day, calendar, price_context=context)
    assert actual == expected
    # The tiny raw-facts lane still runs. The ordinary daily price lane may
    # reuse the full replay only when the historical filter accepted it all.
    reuse = fault in ('none', 'gap')
    assert (context['price_rows']._daily_indicator_tail is not None) is reuse
    assert calls[len(before):] == ([2] if reuse or fault == 'coverage' else [2, len(rows)])


from test_selection_shared_basis import inputs, prime


@pytest.mark.parametrize('restart', [False, True])
def test_next_structure_and_boll_parameter_use_shared_strict_inputs(inputs, monkeypatch, restart):
    from app.services import strategies, strategy_periods as periods, condition_basis_cache as basis
    market, day, symbols, token, generation = inputs
    contexts = prime(inputs)
    tree = conditions.validate({'field':'higher_lows','op':'confirmed_rising_lows','value':60})
    plan = periods.compile_metric_plan(tree)
    strategies.selection_metric_batch(market, symbols, day, plan, generation, price_basis='forward', source_token=token)
    if restart:
        periods.clear_metric_cache();basis.clear_cache()
    config = {'band_period':21,'rank_window':25,'squeeze_percentile':15,'require_squeeze':False}
    tree = conditions.validate({'field':'boll_signal','op':'matches','value':config})
    plan = periods.compile_metric_plan(tree)
    expected = {symbol:{'D':conditions.metrics_for(contexts[symbol]['raw_rows'],day,market.days,
        price_context=contexts[symbol],boll_configs=plan.requests['D'].boll_configs)} for symbol in symbols}
    def never(*args, **kwargs):pytest.fail('new BOLL rule must use source-bound strict period inputs')
    monkeypatch.setattr(market, 'bars', never)
    monkeypatch.setattr(market, 'actions_for_instruments', never)
    monkeypatch.setattr(basis, 'restore_structure', never)
    monkeypatch.setattr(boll_conditions, '_period_rows', never)
    monkeypatch.setattr(higher_lows, '_period_rows', never)
    histories, ready, daily = strategies.selection_metric_batch(market, symbols, day, plan, generation,
        price_basis='forward', source_token=token)
    assert histories == daily == {}
    assert ready == expected


def test_strict_input_scope_changes_and_malformed_cache_fall_back(inputs):
    from app.services import strategy_periods as periods, condition_proof_cache as disk, strict_structure_cache as inputs_cache
    market, day, symbols, token, generation = inputs
    contexts = prime(inputs)
    key = periods._summary_key(day, 'D', symbols[0], generation, contexts[symbols[0]])
    rows = conditions.normalized_metric_rows(contexts[symbols[0]]['price_rows'], day)
    assert inputs_cache.prepare(key, rows, market.days, day, 'D')
    for position, changed in ((1, ('other',token['verified_closure_sha256'])), (2,'other.SH'), (3,'1999-01-01'), (4,'W')):
        other=list(key);other[position]=changed
        assert inputs_cache.restore(tuple(other)) is None
    assert disk.store(key,'strict-input',{'rows':[{'valid':True}]},inputs_cache.VERSION)
    assert inputs_cache.restore(key) is None


@pytest.mark.parametrize('size', [0, 1, 2, 90])
def test_history_tail_one_day_retention_keeps_current_and_predecessor(size):
    rows, calendar = sample(max(1,size))
    if size == 0:rows=[]
    day=calendar[-1]
    context=prepare_condition_prices(rows,[],calendar,day,'forward',instrument_id='600000.SH',
        actions_coverage={'known':True,'source_token':('fixed','source'),'snapshot_id':'market'})
    expected=conditions.metrics_for(rows,day,calendar,price_context=deepcopy(context))
    for lane in ('raw_rows','price_rows'):context[lane]=conditions.prepare_metric_bars(context[lane])
    history_events.HistoryContext(rows,calendar,day,'market',retain_start=day,price_context=context)
    assert conditions.metrics_for(rows,day,calendar,price_context=context)==expected
