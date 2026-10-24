import importlib.util
import json
from pathlib import Path

import pytest

from app.infrastructure.storage import atomic_json, digest, hash_file
from app.services.market_supplement import SupplementCache
from app.services.supplement_storage import compact_document, expand_document


def sample():
    record = {'date': '2026-10-22', 'complete': True, 'items': [{'instrument_id': '000001.SZ', 'close': 12.5}]}
    return {'version': 'market-supplement-v1', 'snapshot_id': 'market-old',
            'annual_pools': {'2026-10-22': record}, 'public_pools': {'2026-10-22': record},
            'sector_index_history': {}, 'quality': 'partial'}


def test_roundtrip_reuses_unchanged_date_objects_across_versions(tmp_path):
    original = sample()
    first = compact_document(tmp_path, original)
    assert expand_document(tmp_path, first) == original
    paths = list((tmp_path/'artifacts').iterdir())
    assert len(paths) == 1
    stamp = paths[0].stat().st_mtime_ns
    changed = {**original, 'snapshot_id': 'market-new'}
    second = compact_document(tmp_path, changed)
    assert second['annual_pools'] == first['annual_pools']
    assert list((tmp_path/'artifacts').iterdir()) == paths
    assert paths[0].stat().st_mtime_ns == stamp
    assert expand_document(tmp_path, first) == original
    assert expand_document(tmp_path, second) == changed
    loaded = expand_document(tmp_path, first)
    loaded['annual_pools']['2026-10-22']['items'][0]['close'] = 99
    assert expand_document(tmp_path, first)['annual_pools']['2026-10-22']['items'][0]['close'] == 12.5


def test_revision_keeps_original_evidence_and_old_index(tmp_path):
    original = sample()
    first = compact_document(tmp_path, original)
    newer = sample(); newer['annual_pools']['2026-10-22']['complete'] = False
    second = compact_document(tmp_path, newer)
    assert first['annual_pools'] != second['annual_pools']
    assert expand_document(tmp_path, first) == original
    assert len(list((tmp_path/'artifacts').iterdir())) == 2


def test_missing_corrupt_and_escape_objects_fail_even_after_cache_hit(tmp_path):
    compact = compact_document(tmp_path, sample())
    pointer = compact['annual_pools']['2026-10-22']
    path = tmp_path/pointer['record_ref']
    path.write_text('{}')
    with pytest.raises(ValueError, match='SHA'):
        expand_document(tmp_path, compact)
    path.unlink()
    with pytest.raises(ValueError, match='缺失'):
        expand_document(tmp_path, compact)
    compact['annual_pools']['2026-10-22']['record_ref'] = '../outside.json'
    with pytest.raises(ValueError, match='越出'):
        expand_document(tmp_path, compact, fields=['annual_pools'])


def test_cached_bulk_payloads_are_independent_and_same_size_tampering_is_rechecked(tmp_path):
    import os
    original=sample()
    original['annual_pools']['2026-10-22']['items']=[
        {'instrument_id':f'{index:06d}.SZ','nested':{'labels':['leaf',{'price':12.5}]}}
        for index in range(100)]
    compact=compact_document(tmp_path,original)  # Stores and warms the object cache.
    first=expand_document(tmp_path,compact)
    first['annual_pools']['2026-10-22']['items'][0]['nested']['labels'][1]['price']=999
    first['annual_pools']['2026-10-22']['items'].append({'caller':'only'})
    assert expand_document(tmp_path,compact)==original
    pointer=compact['annual_pools']['2026-10-22']
    record=json.loads((tmp_path/pointer['record_ref']).read_text())
    path=tmp_path/record['__supplement_fields_v1']['items']['record_ref']
    before=path.stat();content=path.read_bytes();tampered=content.replace(b'"leaf"',b'"fake"',1)
    assert tampered!=content and len(tampered)==len(content)
    path.write_bytes(tampered)
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    with pytest.raises(ValueError,match='SHA'):
        expand_document(tmp_path,compact)


def test_selected_dates_do_not_open_unrelated_objects(tmp_path):
    document = sample()
    document['annual_pools']['2026-10-21'] = {'date': '2026-10-21', 'items': []}
    compact = compact_document(tmp_path, document)
    (tmp_path/compact['annual_pools']['2026-10-21']['record_ref']).unlink()
    result = expand_document(tmp_path, compact, dates=['2026-10-22'], fields=['annual_pools'])
    assert set(result['annual_pools']) == {'2026-10-22'}
    assert 'public_pools' not in result
    assert expand_document(tmp_path, document, dates=['2026-10-22'], fields=['annual_pools']) == result


def test_revalidation_context_changes_share_bulk_rows_and_corruption_is_detected(tmp_path):
    original = sample()
    original['annual_pools']['2026-10-22']['items'] = [{'instrument_id': f'{index:06d}.SZ', 'name': '测试字段'*8} for index in range(500)]
    first = compact_document(tmp_path, original)
    before = {path.name: path.stat().st_size for path in (tmp_path/'artifacts').iterdir()}
    newer = sample()
    newer['annual_pools']['2026-10-22']['items'] = original['annual_pools']['2026-10-22']['items']
    newer['annual_pools']['2026-10-22']['validated_at'] = '2026-10-23'
    second = compact_document(tmp_path, newer)
    after = {path.name: path.stat().st_size for path in (tmp_path/'artifacts').iterdir()}
    assert sum(after[name] for name in after.keys()-before.keys()) < 2048
    assert expand_document(tmp_path, first) == original
    assert expand_document(tmp_path, second) == newer
    stored = json.loads((tmp_path/second['annual_pools']['2026-10-22']['record_ref']).read_text())
    payload = tmp_path/stored['__supplement_fields_v1']['items']['record_ref']
    payload.write_text('{}')
    with pytest.raises(ValueError, match='SHA'):
        expand_document(tmp_path, second)


def test_legacy_index_stays_compatible_and_failed_guard_does_not_activate(tmp_path):
    original = sample()
    assert expand_document(tmp_path, original) == original
    cache = SupplementCache(tmp_path)
    cache.publish(original)
    head = (tmp_path/'head.json').read_bytes()
    def interrupted():
        raise RuntimeError('isolated interruption')
    with pytest.raises(RuntimeError):
        cache.publish({**original, 'snapshot_id': 'market-next'}, interrupted)
    assert (tmp_path/'head.json').read_bytes() == head


def test_backup_validates_compact_reference_closure_and_detects_corruption(tmp_path):
    spec = importlib.util.spec_from_file_location('isolated_backup_compact', Path(__file__).resolve().parents[2]/'scripts/backup.py')
    backup = importlib.util.module_from_spec(spec); spec.loader.exec_module(backup)
    root = tmp_path/'market-supplement'
    SupplementCache(root).publish(sample())
    inventory = backup.supplement_files(tmp_path)
    assert len(inventory) == 3
    for key, sha in inventory.items():
        assert hash_file(tmp_path/key) == sha
    pointer = json.loads((root/'head.json').read_text())
    index = json.loads((root/pointer['artifact']).read_text())
    record = root/index['annual_pools']['2026-10-22']['record_ref']
    record.write_text('{}')
    with pytest.raises(ValueError, match='checksum'):
        backup.supplement_files(tmp_path)
