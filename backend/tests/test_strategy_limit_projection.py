"""Closing evidence reads only its own CAS capabilities, with all dates intact.

All records below are synthetic test fixtures stored under pytest's temp root.
No production database, provider client or worker is used.
"""
from datetime import date, timedelta
import json
import os

import pytest

from app.services import market_supplement, strategy_limits
from app.services.market_supplement import SupplementCache
from app.services.strategy_limits import COUNT_FIELD, LimitConditionEvidence


SNAPSHOT = 'fixture-limit-projection'
SYMBOL = '600001.SH'
OTHER = '000001.SZ'
TODAY = '2026-01-13'
PRIOR = '2026-01-12'
POOL_FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools')


def _pool(day, symbol=SYMBOL, *, source='fixture-verified-pool', kind='limitup'):
    return {'date': day, 'source': source, 'pool_kind': kind, 'complete': True,
            'pagination_complete': True, 'ambiguous_code_rows': 0,
            'raw_codes': [symbol[:6]], 'rejected_codes': [], 'quarantine': [],
            'raw_sha256': 'a' * 64, 'raw_artifact': 'test-only-source.json',
            'fetched_at': day + 'T18:00:00+08:00',
            'items': [{'instrument_id': symbol, 'ticker': symbol[:6],
                       'date': day, 'last_price': 10,
                       'test_only_notes': ['CAS-proof'] * 300}]}


def _document(days):
    return {'version': market_supplement.VERSION, 'snapshot_id': SNAPSHOT,
            'job_id': 'fixture-projection-source', 'target_date': days[-1],
            'quality': 'partial', 'failures': [],
            'annual_pools': {day: _pool(day) for day in days},
            'annual_down_pools': {day: _pool(day, OTHER, kind='limitdown') for day in days},
            'public_pools': {day: _pool(day, source='fixture-public-pool') for day in days},
            'fund_flows': {days[-1]: {'date': days[-1], 'items': [
                {'test_only_fund': ['unrelated-fund-payload'] * 300}]}},
            'sector_index_history': {days[-1]: {'date': days[-1], 'items': [
                {'test_only_sector': ['unrelated-sector-payload'] * 300}]}}}


class _Market:
    def reference(self): return {}
    def status(self): return {'snapshot_id': SNAPSHOT}


@pytest.fixture
def local_cas(tmp_path, monkeypatch):
    # The source reader also takes a filesystem lock beside SQLite. Point that
    # lock and every configured storage root at this test's disposable tree.
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR',
                'SHIXU_WORK_DIR', 'SHIXU_CREDENTIAL_DIR'):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / 'published' / 'market-supplement'
    monkeypatch.setattr(market_supplement, '_root', lambda: root)
    cache = SupplementCache(root)
    cache.publish(_document([PRIOR, TODAY]))
    return root, cache


def _legacy_full_evidence():
    # Reproduce the former constructor's full-capability load using the real
    # reader. This is deliberately not a mock of the new fields argument.
    class RetainedInputs:
        def reference(self): return {}
    proof = LimitConditionEvidence(RetainedInputs())
    proof.snapshot_id = SNAPSHOT
    proof.supplement = market_supplement.load_supplement(SNAPSHOT)
    return proof


def _proofs(proof, calendar, quotes, symbol=SYMBOL):
    return {'daily': proof.metrics(symbol, TODAY, calendar, quotes),
            'historical': {day: proof.historical_metrics(symbol, day, quotes.get(day))
                           for day in calendar},
            'counts': proof.count_series(symbol, [PRIOR, TODAY], calendar, quotes)}


def _pointer(root, field, day=TODAY, *, payload=False):
    head = json.loads((root / 'head.json').read_text())
    index = json.loads((root / head['artifact']).read_text())
    path = root / index[field][day]['record_ref']
    if payload:
        record = json.loads(path.read_text())
        path = root / record['__supplement_fields_v1']['items']['record_ref']
    return path


def _damage(root, field, mode):
    path = _pointer(root, field, payload=mode in ('payload', 'same_size'))
    if mode == 'missing':
        path.unlink()
    elif mode == 'symlink':
        outside = root.parent / ('outside-' + field + '.json')
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif mode == 'same_size':
        before = path.stat()
        content = path.read_bytes()
        changed = content.replace(b'CAS-proof', b'BAD-proof', 1)
        assert changed != content and len(changed) == len(content)
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = path.stat()
        assert after.st_ino == before.st_ino and after.st_size == before.st_size
        assert after.st_mtime_ns == before.st_mtime_ns
    else:
        path.write_text('{}')


def test_projected_constructor_preserves_entire_cross_year_evidence_and_counts(local_cas, monkeypatch):
    root, cache = local_cas
    days = []
    cursor = date(2024, 12, 30)
    while cursor <= date.fromisoformat(TODAY):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    document = _document(days)
    positives = {'2025-01-13', '2025-06-02', PRIOR, TODAY}
    document['annual_pools'] = {
        day: _pool(day, SYMBOL if day in positives else OTHER) for day in days}
    document['public_pools'] = {
        day: _pool(day, source='fixture-public-pool') for day in positives}
    document['annual_down_pools'] = {}
    cache.publish(document)
    full = _legacy_full_evidence()
    calls = []
    actual_load = strategy_limits.load_supplement
    def record_load(snapshot, **kwargs):
        calls.append((snapshot, kwargs))
        return actual_load(snapshot, **kwargs)
    monkeypatch.setattr(strategy_limits, 'load_supplement', record_load)
    projected = LimitConditionEvidence(_Market())
    assert calls == [(SNAPSHOT, {'fields': POOL_FIELDS})]
    assert set(projected.supplement['annual_pools']) == set(days)
    assert 'fund_flows' not in projected.supplement
    assert 'sector_index_history' not in projected.supplement
    quotes = {day: {'date': day, 'close': 10, 'volume': 100} for day in days}
    assert _proofs(projected, days, quotes) == _proofs(full, days, quotes)
    count = projected.count_series(SYMBOL, [TODAY], days, quotes)[TODAY]
    assert count[COUNT_FIELD] == 3
    assert count[COUNT_FIELD + '_coverage']['confirmed_dates'] == [
        '2025-01-13', '2025-06-02', PRIOR]
    assert count[COUNT_FIELD + '_coverage']['today_excluded'] is True


@pytest.mark.parametrize('field', ['fund_flows', 'sector_index_history'])
@pytest.mark.parametrize('mode', ['record', 'payload', 'missing', 'symlink'])
def test_unrelated_capability_failure_does_not_poison_valid_closing_pools(local_cas, field, mode):
    root, _ = local_cas
    calendar = [PRIOR, TODAY]
    quotes = {day: {'date': day, 'close': 10, 'volume': 100} for day in calendar}
    before = _proofs(_legacy_full_evidence(), calendar, quotes)
    assert before['daily']['limit_up'] is True
    _damage(root, field, mode)
    # This is the intentional capability-isolation change: the old full read
    # is unknown, while all required source proofs still produce identical
    # values, reasons, coverage metadata and source provenance.
    legacy = _legacy_full_evidence()
    assert legacy.supplement['quality'] == 'unknown'
    assert legacy.metrics(SYMBOL, TODAY, calendar, quotes)['limit_up'] is None
    assert _proofs(LimitConditionEvidence(_Market()), calendar, quotes) == before


@pytest.mark.parametrize('field', POOL_FIELDS)
@pytest.mark.parametrize('mode', ['record', 'payload', 'missing', 'symlink', 'same_size'])
def test_selected_pool_failure_after_warm_read_is_unknown(local_cas, field, mode):
    root, _ = local_cas
    quotes = {TODAY: {'date': TODAY, 'close': 10, 'volume': 100}}
    assert LimitConditionEvidence(_Market()).historical_metrics(
        SYMBOL, TODAY, quotes[TODAY])['limit_up'] is True
    _damage(root, field, mode)
    proof = LimitConditionEvidence(_Market())
    assert proof.supplement['quality'] == 'unknown'
    result = proof.historical_metrics(SYMBOL, TODAY, quotes[TODAY])
    assert result['limit_up'] is None and result['limit_up_status'] == 'unknown'
    assert '完整性' in result['limit_up_reason']
    assert result['limit_up_evidence']['source_proofs'] == []


@pytest.mark.parametrize('mode', ['corrupt', 'missing'])
def test_shared_index_failure_always_blocks_projected_evidence(local_cas, mode):
    root, _ = local_cas
    proof = LimitConditionEvidence(_Market())
    assert proof.supplement.get('snapshot_id') == SNAPSHOT
    head = json.loads((root / 'head.json').read_text())
    path = root / head['artifact']
    if mode == 'missing': path.unlink()
    else: path.write_text('{}')
    proof = LimitConditionEvidence(_Market())
    assert proof.supplement['quality'] == 'unknown'
    result = proof.historical_metrics(SYMBOL, TODAY, {'date': TODAY, 'close': 10, 'volume': 100})
    assert result['limit_up'] is None and result['limit_up_status'] == 'unknown'


@pytest.mark.parametrize('mode', ['zero_volume', 'opposite_pool'])
def test_projection_preserves_traded_quote_and_opposite_direction_checks(local_cas, mode):
    _, cache = local_cas
    document = _document([PRIOR, TODAY])
    quote = {'date': TODAY, 'close': 10, 'volume': 100}
    if mode == 'zero_volume': quote['volume'] = 0
    else: document['annual_down_pools'][TODAY] = _pool(TODAY, kind='limitdown')
    cache.publish(document)
    full = _legacy_full_evidence().historical_metrics(SYMBOL, TODAY, quote)
    actual = LimitConditionEvidence(_Market()).historical_metrics(SYMBOL, TODAY, quote)
    assert actual == full
    assert actual['limit_up'] is None
    assert actual['limit_up_status'] == ('unknown' if mode == 'zero_volume' else 'conflict')
