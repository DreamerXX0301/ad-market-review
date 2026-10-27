"""Repeated rules may reuse count evidence only after actual source checks."""
from copy import deepcopy
import json
import zlib

import pytest

from app.services import strategy_limits
from app.services.strategy_limits import COUNT_FIELD, LimitConditionEvidence
from test_strategy_limit_counts import DAY, SYMBOL, OTHER, CountMarket, fixture_evidence


@pytest.fixture
def verified(monkeypatch):
    with strategy_limits._count_cache_lock:
        strategy_limits._count_cache.clear()
        monkeypatch.setattr(strategy_limits, '_count_cache_size', 0)
    market, document = fixture_evidence(monkeypatch)
    state = {'revision': 1, 'root': 'fixture-one', 'checks': 0, 'failure': False}

    def verify():
        state['checks'] += 1
        if state['failure']:
            raise ValueError('immutable source checksum failed')
        return ('verified-condition-sources-v1', state['root'], 'count-fixture',
                ('price', state['revision']), ('reference', state['revision']))

    market.verify_condition_sources = verify
    return market, document, state


def counts(market, **kwargs):
    return LimitConditionEvidence(market).batch_count_metrics(market, [SYMBOL], DAY, market.days, **kwargs)


def test_repeat_verifies_sources_without_reading_quotes_again_and_returns_private_evidence(verified):
    market, _, state = verified
    first = counts(market, include_daily=True)
    expected = deepcopy(first)
    first[SYMBOL][COUNT_FIELD + '_coverage']['confirmed_dates'].clear()
    first[SYMBOL]['limit_up_evidence']['source_proofs'].clear()
    repeated = counts(market, include_daily=True)
    assert repeated == expected
    assert len(market.calls) == 1 and state['checks'] == 2


@pytest.mark.parametrize('change', ['price', 'calendar', 'supplement', 'reference', 'workspace'])
def test_changed_dependency_recomputes_even_with_same_snapshot_and_job(verified, change):
    market, document, state = verified
    before = counts(market)
    if change == 'price':
        market.rows['2025-06-02']['close'] = 12
        state['revision'] += 1
    elif change == 'calendar':
        market.days.remove('2025-06-02')
    elif change == 'supplement':
        document['annual_pools']['2025-06-02']['items'][0]['last_price'] = 12
    elif change == 'reference':
        market.ref['pool_history'] = {'2025-06-02': {'limitdown': [
            {'thscode': SYMBOL, 'last_price': 10, 'date': '2025-06-02'}]}}
    else:
        state['root'] = 'fixture-two'
    after = counts(market)
    assert len(market.calls) == 2
    if change != 'workspace':
        assert after != before


def test_corrupt_actual_market_source_cannot_hit_prior_summary(verified):
    market, _, state = verified
    counts(market)
    state['failure'] = True
    with pytest.raises(ValueError, match='checksum'):
        counts(market)


def test_missing_or_failed_supplement_does_not_reuse_good_counts(verified):
    market, document, _ = verified
    before = counts(market)
    document.clear()
    document['failure_reason'] = '补充来源完整性未通过核验'
    failed = counts(market)
    assert len(market.calls) == 2
    assert failed != before
    assert failed[SYMBOL][COUNT_FIELD] is None
    assert '完整性' in failed[SYMBOL][COUNT_FIELD + '_reason']


def test_unknown_result_keeps_bounds_and_invalidates_when_sources_become_available(verified):
    market, document, _ = verified
    record = document['annual_pools'].pop('2025-06-02')
    unknown = counts(market)
    assert unknown[SYMBOL][COUNT_FIELD] is None
    assert counts(market) == unknown
    assert len(market.calls) == 1
    document['annual_pools']['2025-06-02'] = record
    restored = counts(market)
    assert restored[SYMBOL][COUNT_FIELD] == 3
    assert len(market.calls) == 2


def test_caller_histories_and_unverified_adapters_always_recompute(verified):
    market, _, _ = verified
    original = counts(market)
    supplied = counts(market, histories={SYMBOL: []})
    assert supplied != original and supplied[SYMBOL][COUNT_FIELD] is None
    assert counts(market) == original and len(market.calls) == 1
    market.verify_condition_sources = lambda: None
    counts(market)
    counts(market)
    assert len(market.calls) == 3


def test_daily_evidence_has_separate_scope_and_count_cache_is_bounded(verified, monkeypatch):
    market, _, _ = verified
    monkeypatch.setattr(strategy_limits, '_COUNT_CACHE_ENTRIES', 1)
    counts(market)
    daily = counts(market, include_daily=True)
    assert 'limit_up' in daily[SYMBOL] and len(market.calls) == 2
    counts(market)
    assert len(market.calls) == 3
    assert len(strategy_limits._count_cache) == 1
    assert strategy_limits._count_cache_size <= strategy_limits._COUNT_CACHE_BYTES


def test_symbol_subset_reuses_only_verified_members(verified):
    market, _, _ = verified
    before = counts(market)
    result = LimitConditionEvidence(market).batch_count_metrics(market, [OTHER, SYMBOL], DAY, market.days)
    assert list(result) == sorted([OTHER, SYMBOL])
    assert result[SYMBOL] == before[SYMBOL]
    assert market.calls[-1][0] == [OTHER]


def test_daily_proof_compression_keeps_whole_scope_within_original_budget(verified, monkeypatch):
    market, _, _ = verified
    item = counts(market, include_daily=True)[SYMBOL]
    # Daily pool proofs repeat substantial source metadata; retain all evidence,
    # not just the Boolean, without increasing the existing byte budget.
    item['limit_up_evidence']['source_detail'] = '来源证据保持完整' * 600
    plain_size = len(json.dumps(item, ensure_ascii=False, separators=(',', ':')).encode())
    monkeypatch.setattr(strategy_limits, '_COUNT_CACHE_BYTES', plain_size)
    for n in range(8):
        strategy_limits._count_cached(('compressed-proof', n), item)
    assert all(strategy_limits._count_cached(('compressed-proof', n)) == item for n in range(8))
    assert strategy_limits._count_cache_size <= plain_size


@pytest.mark.parametrize('corruption', ['invalid', 'oversize', 'trailing'])
def test_invalid_cached_encoding_recomputes_actual_evidence(verified, corruption):
    market, _, _ = verified
    expected = counts(market, include_daily=True)
    key = next(iter(strategy_limits._count_cache))
    old = strategy_limits._count_cache[key]
    bad = {'invalid': b'invalid compressed cache',
           'oversize': zlib.compress(b' ' * (64 * 1024 + 1)),
           'trailing': old + b'trailing'}[corruption]
    with strategy_limits._count_cache_lock:
        strategy_limits._count_cache[key] = bad
        strategy_limits._count_cache_size += len(bad) - len(old)
    assert counts(market, include_daily=True) == expected
    assert len(market.calls) == 2
