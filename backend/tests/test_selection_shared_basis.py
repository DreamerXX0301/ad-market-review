"""Rule edits reuse verified inputs without reusing a selection decision."""
from copy import deepcopy

import pytest

from app.domain.conditions import metrics_for, validate
from app.domain.strategy_prices import compact_source_token
from app.services import condition_basis_cache as basis
from app.services import strategies, strategy_periods as periods
from test_forward_tracking_backtest import ForwardMarket


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('SHIXU_WORK_DIR', str(tmp_path / 'work'))
    periods.clear_metric_cache(); basis.clear_cache()
    market = ForwardMarket(220, 100, 150)
    market.bars = lambda symbol, as_of=None: deepcopy([row for row in market.data[symbol] if not as_of or row['date'] <= as_of])
    market.actions_for_instruments = lambda symbols: {symbol: deepcopy(market.events[symbol]) for symbol in symbols}
    token = compact_source_token(market.verify_condition_sources())
    generation = (market.status()['snapshot_id'], token['verified_closure_sha256'])
    yield market, market.days[-1], list(market.data), token, generation
    periods.clear_metric_cache(); basis.clear_cache()


def prime(inputs):
    market, day, symbols, token, generation = inputs
    plan = periods.compile_metric_plan([validate({'all': [
        {'field': 'close', 'op': '>', 'value': 0},
        {'field': 'macd_dif', 'op': '>=', 'right_field': 'macd_dea', 'period': 'W'}]})])
    contexts = {}
    histories, ready, _ = strategies.selection_metric_batch(market, symbols, day, plan, generation,
        price_basis='forward', source_token=token, price_contexts=contexts)
    for symbol in symbols:
        for period in plan.periods:
            periods.cached_metrics(histories[symbol], day, market.days, period,
                instrument_id=symbol, generation=generation, request=plan.requests[period], price_context=contexts[symbol])
    return contexts


@pytest.mark.parametrize('restart', [False, True])
def test_new_structure_parameters_reuse_projection_without_quotes_or_actions(inputs, monkeypatch, restart):
    market, day, symbols, token, generation = inputs
    contexts = prime(inputs)
    if restart:
        periods.clear_metric_cache(); basis.clear_cache()
    config = {'band_period': 21, 'rank_window': 25, 'require_squeeze': False}
    tree = validate({'all': [
        {'field': 'higher_lows', 'op': 'confirmed_rising_lows', 'value': 60},
        {'field': 'boll_signal', 'op': 'matches', 'value': config},
        {'field': 'macd_dif', 'op': '>=', 'right_field': 'macd_dea', 'period': 'W'}]})
    plan = periods.compile_metric_plan([tree]); expected = {}
    for symbol in symbols:
        expected[symbol] = {period: metrics_for(contexts[symbol]['raw_rows'], day, market.days, period,
            include_higher_lows=plan.requests[period].include_higher_lows,
            boll_configs=plan.requests[period].boll_configs, price_context=contexts[symbol]) for period in plan.periods}
    def never(*args, **kwargs):
        pytest.fail('new structure must use verified full-seed projection, not reread source')
    monkeypatch.setattr(market, 'bars', never)
    monkeypatch.setattr(market, 'actions_for_instruments', never)
    actual_contexts = {}
    histories, ready, daily = strategies.selection_metric_batch(market, symbols, day, plan, generation,
        price_basis='forward', source_token=token, price_contexts=actual_contexts)
    assert histories == {} and daily == {}
    assert ready == expected
    assert basis.cache_stats()['structure_hits'] == len(symbols)
    assert all('raw_rows' not in value for value in actual_contexts.values())


def test_new_history_window_restores_full_seed_and_unknown_source_never_probes_cache(inputs, monkeypatch):
    market, day, symbols, token, generation = inputs
    contexts = prime(inputs)
    plan = periods.compile_metric_plan([validate({'field': 'close', 'op': '>', 'value': 0})])
    calls = []
    original = market.bars
    monkeypatch.setattr(market, 'bars', lambda *args, **kwargs: (calls.append(args[0]), original(*args, **kwargs))[1])
    restored = {}
    histories, _, _ = strategies.selection_metric_batch(market, symbols, day, plan, generation,
        price_basis='forward', source_token=token, price_contexts=restored, require_history=True)
    assert calls == []
    assert all(histories[symbol] == contexts[symbol]['raw_rows'] for symbol in symbols)
    assert all(restored[symbol]['price_rows'] == contexts[symbol]['price_rows'] for symbol in symbols)
    # A source proof is mandatory even if summaries and full seeds exist.
    missing = {}
    periods.clear_metric_cache()
    strategies.selection_metric_batch(market, symbols, day, plan, ('other-generation', 'b' * 64),
        price_basis='forward', source_token=None, price_contexts=missing, require_history=True)
    assert calls == symbols
    assert all(value['price_basis'] == 'unavailable' for value in missing.values())
