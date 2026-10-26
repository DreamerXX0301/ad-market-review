"""Shared forward tails are verified technical inputs, never cached decisions.

Synthetic local quotations exercise the full cold HistoryContext proof. The
isolated byte store intentionally tests the shared record protocol separately
from the already covered condition-basis SQLite transport.
"""
from copy import deepcopy
from base64 import b64encode
from hashlib import sha256
import json
from types import SimpleNamespace
import zlib

import pytest

from app.domain.conditions import evaluate, metrics_for, validate
from app.domain.history_events import HistoryContext
from app.domain.strategy_prices import compact_source_token, prepare_condition_prices
from app.services import condition_basis_cache as disk
from app.services import forward_history_cache as cache
from app.services.history_events import cached_forward_contexts, contexts_for_batch
from test_event_percent_cross_cycle import cycle_rule
from test_forward_tracking_backtest import ForwardMarket


def event(field='volume_ratio', value=1, to=28, *, start=1, then=None, price_path=None):
    node = {'window': {'from': start, 'to': to}, 'match': 'latest',
            'where': {'field': field, 'op': ('new_high' if field == 'close_breakout_high'
                                           else '==' if isinstance(value, bool) else '>'),
                      'value': value, 'period': 'D'},
            'then': then or {'field': 'close', 'op': '>',
                             'right_event_field': 'close', 'period': 'D'}}
    if price_path is not None:
        node['price_path'] = price_path
    return validate({'history_event': node})


def context_for(sample, tree, *, rows=None, prices=None):
    scope = cache.window_scope([tree], sample.calendar, sample.day)
    return HistoryContext(rows if rows is not None else sample.rows,
                          sample.calendar, sample.day, sample.snapshot,
                          retain_start=scope['first'],
                          price_context=prices or sample.prices)


def shared_key(sample, tree):
    scope = cache.window_scope([tree], sample.calendar, sample.day)
    wider = cache.shared_window_scope(sample.calendar, sample.day, scope)
    key = cache.key_for(sample.symbol, sample.day, sample.snapshot,
                        sample.prices['cache_key'], wider) if wider else None
    return scope, wider, key


def remember_wide(sample):
    assert cache.remember_shared(sample.key, sample.history, sample.wider)


def restored(sample, tree):
    scope, wider, key = shared_key(sample, tree)
    assert wider == sample.wider
    result = cache.restore_shared(key, scope, sample.calendar, sample.positions)
    assert result is not None
    return result


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    cache.clear_cache()
    records = {}
    writes = []

    def put(identity, record):
        writes.append(identity)
        records[identity] = record

    monkeypatch.setattr(disk, '_put_disk', put)
    monkeypatch.setattr(disk, '_get_disk', records.get)
    # The price-path event at distance 60 precedes this action; both adjusted
    # event prices and their original raw quotes must survive a shared hit.
    market = ForwardMarket(320, 280, 200)
    symbol = '600000.SH'
    calendar = list(market.days)
    day = calendar[-1]
    token = compact_source_token(market.verify_condition_sources())
    rows = market.data[symbol]
    prices = prepare_condition_prices(rows, market.events[symbol], calendar, day, 'forward',
        instrument_id=symbol,
        actions_coverage={'known': True, 'source_token': token, 'snapshot_id': 'forward-fixture'})
    tree = event()
    scope = cache.window_scope([tree], calendar, day)
    wider = cache.shared_window_scope(calendar, day, scope)
    history = HistoryContext(rows, calendar, day, 'forward-fixture',
                             retain_start=wider['first'], price_context=prices)
    value = SimpleNamespace(market=market, symbol=symbol, calendar=calendar, day=day,
        positions={observed: index for index, observed in enumerate(calendar)},
        snapshot='forward-fixture', token=token, rows=rows, prices=prices, tree=tree,
        scope=scope, wider=wider, history=history, records=records, writes=writes)
    value.key = cache.key_for(symbol, day, value.snapshot, prices['cache_key'], wider)
    value.metrics = metrics_for(rows, day, calendar, price_context=prices)
    yield value
    cache.clear_cache()


def test_shared_tail_includes_today_and_covers_63_but_not_64_previous_sessions(sample):
    assert cache.SHARED_WINDOW_SESSIONS == 64
    assert sample.wider['first'] == sample.calendar[-64]
    requested63, wider63, key63 = shared_key(sample, event(to=63))
    assert requested63['first'] == wider63['first']
    assert wider63 == sample.wider and key63 == sample.key
    requested64, wider64, key64 = shared_key(sample, event(to=64))
    assert requested64['first'] == sample.calendar[-65]
    assert wider64 is key64 is None
    remember_wide(sample)
    assert cached_forward_contexts([sample.symbol], [event(to=63)], sample.calendar,
        sample.day, sample.snapshot, sample.token)
    assert not cached_forward_contexts([sample.symbol], [event(to=64)], sample.calendar,
        sample.day, sample.snapshot, sample.token)
    assert cache.shared_window_scope(sample.calendar, '2099-01-01', sample.scope) is None


@pytest.mark.parametrize('sessions', [64, 127, 128])
def test_longer_window_falls_back_to_cold_exact_without_enlarging_shared_record(sample, sessions):
    remember_wide(sample)
    writes = len(sample.writes)
    tree = event(to=sessions)
    expected = context_for(sample, tree)
    actual = contexts_for_batch(sample.market, [sample.symbol], [tree], sample.calendar,
        sample.day, histories={sample.symbol: sample.rows}, price_basis='forward',
        price_contexts={sample.symbol: sample.prices})[sample.symbol]
    assert len(sample.writes) == writes
    assert min(actual.raw_quotes) == sample.calendar[-sessions - 1]
    assert evaluate(tree, sample.metrics, actual.at(sample.day)) == evaluate(
        tree, sample.metrics, expected.at(sample.day))


@pytest.mark.parametrize('tree', [
    event('volume_ratio', .5, 7), event('volume', 0, 63),
    event('ma60', 0, 63), event('macd_dif', -100, 60),
    event('kdj_k', 0, 45), event('macd_dead_cross', False, 60),
    event('close_breakout_high', 20, 35),
    event('volume', 0, 60, then={'field': 'macd_dif', 'op': '>',
                               'right_field': 'macd_dea', 'period': 'D'}),
    event('volume', 0, 60, then={'field': 'close', 'op': '<',
        'right_event_field': 'high', 'right_event_change_pct': 3, 'period': 'D'}),
])
def test_new_fields_windows_and_thresholds_reuse_tail_without_indicator_replay(sample, monkeypatch, tree):
    expected_context = context_for(sample, tree)
    expected = evaluate(tree, sample.metrics, expected_context.at(sample.day))
    remember_wide(sample)

    def never(*args, **kwargs):
        pytest.fail('a shared-tail hit must not rebuild quotes, adjustment or indicator recurrences')

    monkeypatch.setattr('app.domain.history_events.retained_indicator_rows', never)
    monkeypatch.setattr(sample.market, 'actions_for_instruments', never)
    prepared = cached_forward_contexts([sample.symbol], [tree], sample.calendar,
        sample.day, sample.snapshot, sample.token)
    assert list(prepared) == [sample.symbol]

    class Offline:
        def __getattr__(self, name):
            raise AssertionError('shared hit must not read market.' + name)

    actual = contexts_for_batch(Offline(), [sample.symbol], [tree], sample.calendar,
        sample.day, price_basis='forward', prepared_contexts=prepared)[sample.symbol]
    assert evaluate(tree, sample.metrics, actual.at(sample.day)) == expected
    exact_scope = cache.window_scope([tree], sample.calendar, sample.day)
    assert min(actual.raw_quotes) == exact_scope['first']
    assert actual.event_cache != {'stale': 'decision'}


@pytest.mark.parametrize('path', [
    {'guard': {'max_drawdown_pct': 5, 'require_pullback': False}},
    {'guard': {'max_drawdown_pct': 5, 'require_pullback': True,
               'near_floor': {'sessions': 3, 'band_pct': 2}}},
] + [{'reclaim': {'mode': mode, 'event_field': 'close'}} for mode in (
    'close_at_or_above', 'close_reclaim', 'first_close_reclaim', 'high_at_or_above', 'touch')])
def test_price_path_full_evidence_equals_original_exact_context(sample, path):
    tree = event('volume', 0, 60, start=60, price_path=path)
    expected = evaluate(tree, sample.metrics, context_for(sample, tree).at(sample.day))
    remember_wide(sample)
    actual = evaluate(tree, sample.metrics, restored(sample, tree).at(sample.day))
    assert actual == expected
    assert actual['selected_event']['date'] == sample.calendar[-61]
    assert actual['price_path_checks'] is not None
    assert actual['selected_event']['raw_close'] != actual['selected_event']['close']


@pytest.mark.parametrize('indicator', ['macd', 'kdj'])
def test_cross_cycle_retains_both_cross_lanes_and_causal_full_evidence(sample, indicator):
    tree = cycle_rule(indicator, window=63)
    expected_context = context_for(sample, tree)
    golden, dead = ('macd_cross', 'macd_dead_cross') if indicator == 'macd' else ('kd_cross', 'kd_dead_cross')
    for context in (sample.history, expected_context):
        for values in context.daily.values():
            values.update({golden: False, dead: False})
        context.daily[sample.calendar[-5]][golden] = True
        context.daily[sample.calendar[-3]][dead] = True
        context.daily[sample.day][golden] = True
    expected = evaluate(tree, sample.metrics, expected_context.at(sample.day))
    remember_wide(sample)
    actual = evaluate(tree, sample.metrics, restored(sample, tree).at(sample.day))
    assert actual == expected
    assert actual['result'] is True
    cycle = actual['price_path_checks']['cross_cycle']
    assert cycle['dead_cross_date'] == sample.calendar[-3]
    assert cycle['event_cross'] is cycle['current_cross'] is True


@pytest.mark.parametrize('mode', ['gap', 'zero', 'duplicate', 'unknown_actions'])
def test_quote_obstructions_stay_unknown_and_unusable_forward_context_is_not_admitted(sample, mode):
    rows = deepcopy(sample.rows)
    if mode == 'gap':
        rows.pop(-3)
    elif mode == 'zero':
        rows[-3]['volume'] = 0
    elif mode == 'duplicate':
        rows.append(deepcopy(rows[-3]))
    else:
        rows[-3]['corporate_action_coverage_known'] = False
    prices = prepare_condition_prices(rows, sample.market.events[sample.symbol],
        sample.calendar, sample.day, 'forward', instrument_id=sample.symbol,
        actions_coverage={'known': True, 'source_token': sample.token,
                          'snapshot_id': sample.snapshot})
    tree = event('volume', 0, 4, start=4,
        price_path={'reclaim': {'mode': 'close_at_or_above', 'event_field': 'close'}})
    original = context_for(sample, tree, rows=rows, prices=prices)
    history = HistoryContext(rows, sample.calendar, sample.day, sample.snapshot,
                             retain_start=sample.wider['first'], price_context=prices)
    admitted = cache.remember_shared(sample.key, history, sample.wider)
    if prices['price_basis'] != 'forward_event_affine':
        assert admitted is False
        assert cache.restore_shared(sample.key, sample.scope,
                                    sample.calendar, sample.positions) is None
        # The service must retain the original unavailable context. Projection
        # is only legal for admitted, verified forward inputs.
        actual = contexts_for_batch(sample.market, [sample.symbol], [tree],
            sample.calendar, sample.day, histories={sample.symbol: rows},
            price_basis='forward', price_contexts={sample.symbol: prices})[sample.symbol]
    else:
        assert admitted is True
        actual = restored(sample, tree)
    expected = evaluate(tree, sample.metrics, original.at(sample.day))
    assert evaluate(tree, sample.metrics, actual.at(sample.day)) == expected
    assert expected['result'] is None
    assert sample.calendar[-3] in actual.invalid_dates


def test_updated_closing_pool_is_reattached_after_shared_restore_without_stale_proof(sample):
    tree = event('limit_up', True)
    for values in sample.history.daily.values():
        values.update(limit_up=True, limit_up_evidence={'stale_pool': True},
                      limit_up_reason='old pool', limit_up_status='old')
    remember_wide(sample)
    prepared = cached_forward_contexts([sample.symbol], [tree], sample.calendar,
        sample.day, sample.snapshot, sample.token)
    assert prepared
    assert all(values['limit_up'] is None and 'limit_up_evidence' not in values
               for values in prepared[sample.symbol].daily.values())
    calls = []

    class Evidence:
        def historical_metrics(self, symbol, observed, quote):
            calls.append((symbol, observed, quote['close']))
            return {'limit_up': False, 'limit_up_evidence': {'pool_sha': 'new'},
                    'limit_up_reason': 'fresh source pool'}

    class Offline:
        def __getattr__(self, name):
            raise AssertionError('pool refresh must not read market.' + name)

    actual = contexts_for_batch(Offline(), [sample.symbol], [tree], sample.calendar,
        sample.day, price_basis='forward', prepared_contexts=prepared,
        limit_evidence=Evidence())[sample.symbol]
    assert len(calls) == 29
    assert calls[-1][2] == sample.rows[-1]['close']
    assert all(values['limit_up'] is False and values['limit_up_evidence'] == {'pool_sha': 'new'}
               for values in actual.daily.values())
    assert evaluate(tree, sample.metrics, actual.at(sample.day))['result'] is False
    again = restored(sample, tree)
    assert all(values['limit_up'] is None and 'limit_up_evidence' not in values
               for values in again.daily.values())


def test_exact_legacy_narrow_scope_cannot_be_relabelled_as_a_shared_tail(sample):
    narrow = context_for(sample, sample.tree)
    exact_key = cache.key_for(sample.symbol, sample.day, sample.snapshot,
                             sample.prices['cache_key'], sample.scope)
    cache.remember(exact_key, narrow, sample.scope)
    assert cached_forward_contexts([sample.symbol], [sample.tree], sample.calendar,
        sample.day, sample.snapshot, sample.token)
    assert not cached_forward_contexts([sample.symbol], [event('ma60', 0, 90)],
        sample.calendar, sample.day, sample.snapshot, sample.token)
    assert cache.remember_shared(sample.key, narrow, sample.wider) is False
    # Even an exact record spanning the shared tail only has requested fields.
    wide_tree = event(to=63)
    wide_scope = cache.window_scope([wide_tree], sample.calendar, sample.day)
    exact = cache.key_for(sample.symbol, sample.day, sample.snapshot,
                         sample.prices['cache_key'], wide_scope)
    cache.remember(exact, context_for(sample, wide_tree), wide_scope)
    thin = cache.restore(exact, sample.calendar, sample.positions)
    assert cache.remember_shared(sample.key, thin, sample.wider) is False
    assert not sample.records


def test_each_restore_and_direct_projection_owns_nested_evidence_and_adjustment(sample):
    sample.history.daily[sample.day]['volume_evidence'] = {'source': {'sha': ['original']}}
    sample.history.price_context['adjustment']['quarantined'] = [{'reason': ['original']}]
    sample.history.event_cache['stale'] = {'decision': True}
    before = deepcopy(sample.history.daily)
    projection_scope = cache.window_scope([event('volume', 0)], sample.calendar, sample.day)
    projected = cache.project_context(sample.history, projection_scope,
                                      sample.calendar, sample.positions)
    projected.daily[sample.day]['volume_evidence']['source']['sha'].append('projection')
    projected.price_context['adjustment']['quarantined'][0]['reason'].append('projection')
    assert sample.history.daily == before
    assert sample.history.price_context['adjustment']['quarantined'] == [{'reason': ['original']}]
    assert projected.event_cache == {}
    remember_wide(sample)
    first = restored(sample, event('volume', 0))
    first.daily[sample.day]['volume_evidence']['source']['sha'].append('first')
    first.price_context['adjustment']['quarantined'][0]['reason'].append('first')
    first.quotes[sample.day]['close'] = -1
    first.calendar.append('2099-01-01')
    first.positions[sample.day] = -1
    first.event_cache['decision'] = False
    second = restored(sample, event('volume', 0))
    assert second.daily[sample.day]['volume_evidence']['source']['sha'] == ['original']
    assert second.price_context['adjustment']['quarantined'] == [{'reason': ['original']}]
    assert second.quotes[sample.day]['close'] > 0 and second.event_cache == {}
    assert second.calendar == sample.calendar and second.positions == sample.positions
    assert not {'raw_rows', 'price_rows', 'decision', 'topic_ids', 'policy'} & second.price_context.keys()


@pytest.mark.parametrize('boundary', ['root', 'generation', 'source', 'anchor', 'calendar',
                                      'indicator', 'price_algorithm', 'history_algorithm', 'shared_algorithm'])
def test_shared_namespace_never_crosses_verified_source_or_algorithm_boundary(sample, monkeypatch, tmp_path, boundary):
    remember_wide(sample)
    kwargs = dict(symbols=[sample.symbol], trees=[event('ma60', 0, 45)],
                  calendar=sample.calendar, day=sample.day,
                  snapshot=sample.snapshot, source_token=sample.token)
    if boundary == 'root':
        monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'other'))
    elif boundary == 'generation':
        kwargs['snapshot'] = 'another-generation'
    elif boundary == 'source':
        kwargs['source_token'] = {'verified_closure_sha256': 'f' * 64}
    elif boundary == 'anchor':
        kwargs['day'] = sample.calendar[-2]
    elif boundary == 'calendar':
        kwargs['calendar'] = sample.calendar[:5] + sample.calendar[6:]
    else:
        name = {'indicator': 'INDICATOR_VERSION', 'price_algorithm': 'PRICE_VERSION',
                'history_algorithm': 'HISTORY_VERSION', 'shared_algorithm': 'SHARED_WINDOW_VERSION'}[boundary]
        monkeypatch.setattr(cache, name, 'changed-version')
    assert not cached_forward_contexts(**kwargs)


def test_source_verification_is_required_even_when_disk_contains_a_valid_tail(sample):
    remember_wide(sample)
    assert not cached_forward_contexts([sample.symbol], [sample.tree], sample.calendar,
        sample.day, sample.snapshot, None)


@pytest.mark.parametrize('corruption', ['compressed', 'checksum', 'size', 'trailing',
    'identity', 'scope', 'missing_day', 'missing_field', 'wrong_anchor',
    'wrong_price_key', 'wrong_basis', 'unusable', 'legacy_wrapper'])
def test_corrupt_or_incomplete_shared_record_is_a_miss_and_cold_recovery_is_identical(sample, corruption):
    remember_wide(sample)
    identity = cache._shared_identity(sample.key)
    compressed, checksum, size = sample.records[identity]
    wrapper = json.loads(zlib.decompress(compressed))
    decoded = cache._decode_shared_input(wrapper['input'])
    if corruption == 'compressed':
        compressed = b'broken zlib'
    elif corruption == 'checksum':
        checksum = '0' * 64
    elif corruption == 'size':
        size += 1
    elif corruption == 'trailing':
        compressed += b'extra'
    else:
        if corruption == 'identity':
            wrapper['identity'][2] = '600999.SH'
        elif corruption == 'scope':
            wrapper['scope']['first'] = sample.calendar[-10]
        elif corruption == 'missing_day':
            decoded['raw_quotes'].pop(sample.wider['first'])
        elif corruption == 'missing_field':
            decoded['daily'][sample.day].pop('macd_dea')
        elif corruption == 'wrong_anchor':
            decoded['price_context']['anchor_date'] = sample.calendar[-2]
        elif corruption == 'wrong_price_key':
            decoded['price_context']['cache_key'] = 'another-source'
        elif corruption == 'wrong_basis':
            decoded['price_context']['requested_price_basis'] = 'none'
        elif corruption == 'unusable':
            decoded['price_context']['adjustment']['usable'] = False
        else:
            wrapper.pop('scope')
        wrapper['input'] = cache._encode_shared_input(decoded)
        plain = json.dumps(wrapper, ensure_ascii=False, separators=(',', ':')).encode()
        compressed, checksum, size = zlib.compress(plain), sha256(plain).hexdigest(), len(plain)
    sample.records[identity] = compressed, checksum, size
    assert cache.restore_shared(sample.key, sample.scope, sample.calendar, sample.positions) is None
    assert not cached_forward_contexts([sample.symbol], [sample.tree], sample.calendar,
        sample.day, sample.snapshot, sample.token)
    expected = evaluate(sample.tree, sample.metrics, context_for(sample, sample.tree).at(sample.day))
    actual = contexts_for_batch(sample.market, [sample.symbol], [sample.tree], sample.calendar,
        sample.day, histories={sample.symbol: sample.rows}, price_basis='forward',
        price_contexts={sample.symbol: sample.prices})[sample.symbol]
    assert evaluate(sample.tree, sample.metrics, actual.at(sample.day)) == expected


@pytest.mark.parametrize('corruption', ['encoding', 'duplicate_block', 'block_count',
                                       'column_reference', 'column_bytes', 'daily_dates'])
def test_typed_column_damage_or_misalignment_is_a_safe_miss(sample, corruption):
    remember_wide(sample)
    identity = cache._shared_identity(sample.key)
    wrapper = json.loads(zlib.decompress(sample.records[identity][0]))
    encoded = wrapper['input']
    if corruption == 'encoding':
        encoded['encoding'] = 'unknown-encoding'
    elif corruption == 'duplicate_block':
        encoded['daily_blocks'].append(deepcopy(encoded['daily_blocks'][0]))
    elif corruption == 'block_count':
        encoded['daily_blocks'][0]['count'] -= 1
    elif corruption == 'column_reference':
        encoded['daily_blocks'][0]['columns'][0] = len(encoded['columns'])
    elif corruption == 'column_bytes':
        encoded['columns'][0]['data'] = 'invalid base64!'
    else:
        if len(encoded['daily_blocks']) == 1:
            encoded['daily_blocks'].append({'fields': ['_cache_day'], 'columns': [0],
                                            'count': len(sample.history.daily)})
        block = encoded['daily_blocks'][1]
        dates = list(sorted(sample.history.daily))
        dates[-1] = '2099-01-01'
        block['columns'][block['fields'].index('_cache_day')] = len(encoded['columns'])
        encoded['columns'].append({'kind': 'json', 'count': len(dates),
            'data': b64encode(json.dumps(dates).encode()).decode(), 'missing': [], 'null': []})
    plain = json.dumps(wrapper, ensure_ascii=False, separators=(',', ':')).encode()
    sample.records[identity] = zlib.compress(plain), sha256(plain).hexdigest(), len(plain)
    assert cache.restore_shared(sample.key, sample.scope, sample.calendar, sample.positions) is None
    assert not cached_forward_contexts([sample.symbol], [sample.tree], sample.calendar,
        sample.day, sample.snapshot, sample.token)


def test_optional_disk_failure_and_decode_budget_cannot_poison_original_context(sample, monkeypatch):
    before = deepcopy(sample.history.daily)

    def unavailable(*args, **kwargs):
        raise OSError('read-only disposable cache')

    monkeypatch.setattr(disk, '_put_disk', unavailable)
    assert cache.remember_shared(sample.key, sample.history, sample.wider) is False
    assert sample.history.daily == before
    monkeypatch.setattr(disk, '_get_disk', unavailable)
    assert cache.restore_shared(sample.key, sample.scope, sample.calendar, sample.positions) is None
    monkeypatch.setattr(disk, '_put_disk', lambda identity, record: sample.records.__setitem__(identity, record))
    monkeypatch.setattr(disk, '_get_disk', sample.records.get)
    remember_wide(sample)
    monkeypatch.setattr(cache, 'MAX_DECOMPRESSED', 10)
    assert cache.restore_shared(sample.key, sample.scope, sample.calendar, sample.positions) is None


def test_shared_record_rejects_raw_or_unverified_price_lanes(sample):
    original = sample.history.price_basis
    sample.history.price_basis = 'none'
    assert cache.remember_shared(sample.key, sample.history, sample.wider) is False
    sample.history.price_basis = original
    assert cache.remember_shared(None, sample.history, sample.wider) is False
    wrong = list(sample.key)
    wrong[4] = 'wrong-source-price-key'
    assert cache.remember_shared(tuple(wrong), sample.history, sample.wider) is False
    assert not sample.records
