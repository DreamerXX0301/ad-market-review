"""Small status reads retain the full historical immutable-object validation."""
from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from app import core
from app.infrastructure.storage import atomic_json, hash_file
from app.services import market_supplement as supplement, source_service, supplement_storage as storage
from test_market_supplement_projection import index_record


FIRST, PREVIOUS, DAY = '2026-10-22', '2026-10-23', '2026-10-26'


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for name in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path/'published'/'market-supplement'
    def record(day, label):
        return {'date': day, 'complete': True, 'items': [{'instrument_id': f'{number:06d}.SZ',
            'note': label*80} for number in range(80)]}
    document = {'version': supplement.VERSION, 'snapshot_id': 'status-fixture', 'target_date': DAY,
        'quality': 'partial', 'failures': [{'reason': 'fixture gap'}],
        'annual_pools': {FIRST: record(FIRST, 'old-up'), DAY: record(DAY, 'new-up')},
        'annual_down_pools': {FIRST: record(FIRST, 'old-down')},
        'public_pools': {FIRST: record(FIRST, 'old-public'), DAY: record(DAY, 'new-public')},
        'fund_flows': {DAY: {'date': DAY, 'complete': False, 'items': [{'net_inflow': None}],
            'history': record(FIRST, 'current-unused-fund-history')['items']}},
        'sector_index_history': {PREVIOUS: index_record(PREVIOUS, FIRST), DAY: index_record(DAY, PREVIOUS)},
        'annual_coverage': {'covered_days': 2, 'expected_days': 3, 'complete': False},
        'annual_down_coverage': {'observed_days': 2, 'covered_days': 1, 'expected_days': 3, 'complete': False}}
    with storage._lock:
        storage._metadata_cache.clear(); storage._metadata_bytes = 0
    cache = supplement.SupplementCache(root); cache.publish(document)
    monkeypatch.setattr(core, 'get_market', lambda: SimpleNamespace(status=lambda: {'snapshot_id': 'status-fixture'}))
    yield root, document, cache
    with storage._lock:
        storage._metadata_cache.clear(); storage._metadata_bytes = 0


def full_status(monkeypatch):
    with monkeypatch.context() as local:
        local.setattr(supplement, 'load_supplement_status', supplement.load_supplement)
        return source_service.supplement_status()


def index(root):
    head = json.loads((root/'head.json').read_text())
    return json.loads((root/head['artifact']).read_text())


def old_payload(root):
    pointer = index(root)['annual_pools'][FIRST]
    record = json.loads((root/pointer['record_ref']).read_text())
    return root/record['__supplement_fields_v1']['items']['record_ref']


def unused_current_payload(root):
    pointer = index(root)['fund_flows'][DAY]
    record = json.loads((root/pointer['record_ref']).read_text())
    return root/record['__supplement_fields_v1']['history']['record_ref']


@pytest.mark.parametrize('legacy', [False, True])
def test_status_projection_preserves_all_public_fields_and_date_proofs(published, monkeypatch, legacy):
    root, document, _ = published
    if legacy:
        target = root/'indexes'/'legacy.json'; atomic_json(target, document)
        atomic_json(root/'head.json', {'snapshot_id': document['snapshot_id'], 'artifact': 'indexes/legacy.json',
                                     'sha256': hash_file(target)})
    expected = full_status(monkeypatch)
    actual = source_service.supplement_status()
    assert actual == expected
    assert actual['target_date'] == DAY and actual['seal_rows'] == 80
    assert actual['sector_index_rows'] == 1 and actual['sector_index_complete']
    assert actual['annual_covered_days'] == 2 and not actual['annual_complete']
    projected = supplement.load_supplement_status('status-fixture')
    assert 'annual_pools' not in projected and 'annual_down_pools' not in projected
    assert set(projected['public_pools']) == set(projected['sector_index_history']) == {DAY}


def test_warm_metadata_survives_bulk_byte_eviction_without_decoding_old_rows(published, monkeypatch):
    root, _, _ = published
    expected = source_service.supplement_status(); historical = old_payload(root)
    unused = unused_current_payload(root)
    with storage._lock:
        storage._cache.clear(); storage._cache_bytes = 0
    calls = []; original = storage._object_value
    def read(path, key): calls.append(path); return original(path, key)
    monkeypatch.setattr(storage, '_object_value', read)
    assert source_service.supplement_status() == expected
    assert historical not in calls and unused not in calls and calls


def test_rolling_coverage_counts_both_unverified_and_unreceived_dates(published, monkeypatch):
    root, document, cache = published
    earlier, before_year = '2025-10-27', '2025-10-24'
    document['annual_pools'][earlier] = {'date': earlier, 'complete': False, 'items': []}
    cache.publish(document)
    market = SimpleNamespace(status=lambda: {'snapshot_id': 'status-fixture'},
                             calendar=lambda: [before_year, earlier, FIRST, PREVIOUS, DAY])
    monkeypatch.setattr(core, 'get_market', lambda: market)
    actual = source_service.supplement_status()
    assert actual == full_status(monkeypatch)
    assert actual['annual_covered_days'] == 2
    assert actual['rolling_year'] == {
        'start': earlier, 'end': DAY,
        'up': {'expected_days': 4, 'observed_days': 3, 'covered_days': 2},
        'down': {'expected_days': 4, 'observed_days': 1, 'covered_days': 1},
    }
    assert source_service._rolling_pool_coverage({}, [FIRST, PREVIOUS, DAY], DAY) is None


@pytest.mark.parametrize('change', ['missing', 'same-size', 'symlink'])
def test_old_unselected_history_corruption_still_invalidates_status_after_warming(published, monkeypatch, tmp_path, change):
    root, _, _ = published
    source_service.supplement_status(); historical = old_payload(root)
    before = historical.stat(); content = historical.read_bytes()
    if change == 'missing': historical.unlink()
    elif change == 'same-size':
        changed = content.replace(b'old-up', b'bad-up', 1)
        assert changed != content and len(changed) == len(content)
        historical.write_bytes(changed); os.utime(historical, ns=(before.st_atime_ns, before.st_mtime_ns))
    else:
        outside = tmp_path/'outside.json'; outside.write_bytes(content)
        historical.unlink(); historical.symlink_to(outside)
    actual = source_service.supplement_status()
    assert actual == full_status(monkeypatch)
    assert actual['quality'] == 'unknown' and actual['annual_covered_days'] == 0


def test_unmaterialized_current_fund_history_keeps_its_source_validation(published, monkeypatch):
    root, _, _ = published
    source_service.supplement_status(); unused_current_payload(root).unlink()
    actual = source_service.supplement_status()
    assert actual == full_status(monkeypatch) and actual['quality'] == 'unknown'


def test_head_republication_and_caller_changes_do_not_reuse_a_stale_status(published, monkeypatch):
    _, document, cache = published
    original = source_service.supplement_status(); original['failures'].append({'caller': 'only'})
    assert source_service.supplement_status() == full_status(monkeypatch)
    changed = deepcopy(document); changed['public_pools'][DAY]['items'] = []
    changed['annual_coverage']['covered_days'] = 1; cache.publish(changed)
    assert source_service.supplement_status() == full_status(monkeypatch)
    assert source_service.supplement_status()['seal_rows'] == 0
    assert supplement.load_supplement_status('different-generation') == {}


def test_metadata_cache_is_bounded_without_changing_status(published, monkeypatch):
    expected = full_status(monkeypatch)
    monkeypatch.setattr(storage, 'MAX_METADATA_ENTRIES', 2)
    assert source_service.supplement_status() == expected
    assert len(storage._metadata_cache) <= 2 and storage._metadata_bytes <= storage.MAX_METADATA_BYTES
    monkeypatch.setattr(storage, 'MAX_METADATA_BYTES', 1)
    assert source_service.supplement_status() == expected
    assert not storage._metadata_cache and storage._metadata_bytes == 0
