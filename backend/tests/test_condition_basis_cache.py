"""A rule-independent cache preserves full verified technical seeds exactly."""
from copy import deepcopy
from datetime import date, timedelta
import json
from math import isnan
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import zlib

import pytest

from app.domain.conditions import prepare_metric_bars, metrics_for
from app.domain.strategy_prices import prepare_condition_prices, price_cache_key
from app.services import condition_basis_cache as cache


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('SHIXU_WORK_DIR', str(tmp_path / 'work'))
    cache.clear_cache()
    calendar = [(date(2024, 1, 1) + timedelta(days=index)).isoformat() for index in range(90)]
    symbol = '600001.SH'; day = calendar[-1]; source = {'verified_closure_sha256': 'a' * 64}
    rows = [{'date': observed, 'instrument_id': symbol, 'open': 10.0 + index / 100,
             'high': 11.0 + index / 100, 'low': 9.0 + index / 100,
             'close': 10.5 + index / 100, 'volume': 10000 + index * 10, 'amount': 100000.0,
             'corporate_action': False, 'corporate_action_coverage_known': True,
             'limit_up': None, 'turnover_ratio_pct': 1.2}
            for index, observed in enumerate(calendar)]
    context = prepare_condition_prices(rows, [], calendar, day, 'forward', instrument_id=symbol,
        actions_coverage={'known': True, 'source_token': source, 'snapshot_id': 'fixed-market'})
    context['raw_rows'] = prepare_metric_bars(context['raw_rows'])
    context['price_rows'] = prepare_metric_bars(context['price_rows'])
    key = cache.key_for(symbol, day, 'fixed-market', source)
    yield key, context, rows, calendar, source
    cache.clear_cache()


def test_private_prepared_lanes_keep_all_fields_absence_and_normalized_semantics(sample):
    key, context, rows, calendar, _ = sample
    context['raw_rows'][2]['custom_source_field'] = 7
    context['raw_rows'][3]['custom_source_field'] = None
    context['raw_rows'][4]['limit_up'] = True
    context['decision'] = 'never saved'; context['topics'] = ['never saved']; context['policy'] = {'bad': True}
    assert cache.remember(key, context)
    actual = cache.restore(key)
    assert actual is not context and actual['raw_rows'] == context['raw_rows']
    assert actual['price_rows'] == context['price_rows']
    assert actual['adjustment'] == context['adjustment']
    assert actual['raw_rows'].normalized() == context['raw_rows'].normalized()
    assert actual['price_rows'].normalized() == context['price_rows'].normalized()
    assert 'custom_source_field' not in actual['raw_rows'][0]
    assert actual['raw_rows'][3]['custom_source_field'] is None
    assert actual['raw_rows'][4]['limit_up'] is True
    assert not {'decision', 'topics', 'policy'} & set(actual)
    actual['raw_rows'][0]['close'] = -1
    actual['raw_rows']._normalized[0]['close'] = -2
    actual['price_rows'][0]['open'] = -3
    actual['adjustment']['events'].append({'bad': True})
    again = cache.restore(key)
    assert again['raw_rows'][0]['close'] == context['raw_rows'][0]['close']
    assert again['raw_rows'].normalized() == context['raw_rows'].normalized()
    assert again['price_rows'] == context['price_rows'] and not again['adjustment']['events']


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
def test_structure_projection_is_exact_private_and_cannot_replace_full_basis(sample, period):
    from app.analytics.boll_conditions import latest_states
    from app.analytics.higher_lows import latest
    from app.domain.conditions import normalized_metric_rows
    key, context, _, calendar, _ = sample
    assert cache.remember(key, context)
    original = normalized_metric_rows(context['price_rows'], key[3])
    projected = cache.restore_structure(key)
    assert projected['projection'] == 'structure-only'
    assert projected['raw_rows'] == [] and projected['price_rows'] == []
    actual = normalized_metric_rows(projected['price_rows'], key[3])
    assert actual == original
    assert latest(actual, calendar, key[3], period) == latest(original, calendar, key[3], period)
    configs = [{'band_period': 5, 'rank_window': 10}, {'band_period': 7, 'rank_window': 12, 'mode': 'squeeze'}]
    assert latest_states(actual, calendar, key[3], period, configs) == latest_states(original, calendar, key[3], period, configs)
    assert not cache.remember(key, projected)
    projected['price_rows']._normalized[-1]['low'] = -100
    projected['adjustment']['usable'] = False
    assert cache.restore_structure(key)['price_rows'].normalized() == tuple(original)
    assert cache.restore(key)['price_rows'] == context['price_rows']
    assert cache.cache_stats()['structure_hits'] == 2


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('fault', ['none', 'missing', 'duplicate', 'zero', 'missing_volume', 'unknown_coverage'])
def test_all_strict_metrics_and_unknown_reasons_equal_after_restore(sample, period, fault):
    key, context, _, calendar, _ = sample
    for lane in ('raw_rows', 'price_rows'):
        values = context[lane]
        if fault == 'missing': del values[-8]
        elif fault == 'duplicate': values.append(dict(values[-8]))
        elif fault == 'zero': values[-8]['volume'] = 0
        elif fault == 'missing_volume': values[-8].pop('volume')
        elif fault == 'unknown_coverage': values[-8].pop('corporate_action_coverage_known')
    options = {'include_higher_lows': True, 'boll_configs': [{'band_period': 5, 'rank_window': 10}]}
    expected = metrics_for(context['raw_rows'], key[3], calendar, period, price_context=context, **options)
    assert cache.remember(key, context)
    actual = cache.restore(key)
    assert metrics_for(actual['raw_rows'], key[3], calendar, period, price_context=actual, **options) == expected


def test_nonfinite_source_cells_remain_nonfinite_but_normalized_unknown(sample):
    key, context, _, _, _ = sample
    context['raw_rows'][0]['optional_source'] = float('nan')
    context['raw_rows'][1]['optional_source'] = float('inf')
    assert cache.remember(key, context)
    restored = cache.restore(key)
    assert isnan(restored['raw_rows'][0]['optional_source'])
    assert restored['raw_rows'][1]['optional_source'] == float('inf')
    assert 'optional_source' not in restored['raw_rows'][2]


def test_shared_columns_keep_integer_float_boolean_null_and_signed_zero_distinct(sample):
    from math import copysign
    key, context, _, _, _ = sample
    for index, value in enumerate((1, 1.0, True, None, -0.0, 0.0, 2 ** 80)):
        context['raw_rows'][index]['typed_extra'] = value
    assert cache.remember(key, context)
    restored = cache.restore(key)
    expected = context['raw_rows']
    assert all(type(restored['raw_rows'][index]['typed_extra']) is type(expected[index]['typed_extra']) for index in range(7))
    assert copysign(1, restored['raw_rows'][4]['typed_extra']) == -1
    assert copysign(1, restored['raw_rows'][5]['typed_extra']) == 1
    assert restored['raw_rows'][6]['typed_extra'] == 2 ** 80
    assert 'typed_extra' not in restored['raw_rows'][7]


def test_identity_boundaries_and_no_rule_key(sample, monkeypatch, tmp_path):
    key, context, _, _, source = sample
    assert cache.remember(key, context)
    assert cache.key_for(key[2], key[3], 'fixed-market', source) == key
    for index, value in ((1, '"other-market"'), (2, '600002.SH'), (3, '2024-01-01'), (4, 'b' * 64), (5, 'new-price'), (6, 'new-normalization')):
        other = list(key); other[index] = value
        assert cache.restore(tuple(other)) is None
    assert cache.key_for(key[2], key[3], 'fixed-market', None) is None
    assert cache.key_for(key[2], key[3], None, source) is None
    assert cache.key_for(key[2], key[3], 'fixed-market', {'verified_closure_sha256': 'bad'}) is None
    assert cache.key_for(key[2], key[3], ('fixed-market', 'closure'), source) is None
    assert cache.key_for(key[2], key[3], ('fixed-market', 'b' * 64), source) is None
    assert cache.key_for(key[2], key[3], ('fixed-market', source['verified_closure_sha256']), source) is not None
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'other-data'))
    assert cache.restore(key) is None


def test_disk_survives_memory_clear_and_actual_process_restart(sample):
    key, context, _, _, _ = sample
    assert cache.remember(key, context)
    cache.clear_cache()
    assert cache.restore(key)['price_rows'] == context['price_rows']
    assert cache.cache_stats()['disk_hits'] == 1
    script = '''import json,sys
from app.services.condition_basis_cache import restore,cache_stats
value=restore(tuple(json.loads(sys.argv[1])))
print(json.dumps({'ready':value is not None,'rows':len(value['raw_rows']) if value else 0,'disk':cache_stats()['disk_hits']}))
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    result = subprocess.run([sys.executable, '-c', script, json.dumps(key)], check=True, capture_output=True, text=True, env=env)
    assert json.loads(result.stdout) == {'ready': True, 'rows': len(context['raw_rows']), 'disk': 1}


def test_memory_quota_and_disk_lru_eviction_are_independent(sample, monkeypatch):
    key, context, _, _, _ = sample
    assert cache.remember(key, context)
    unit = cache.cache_stats()['compressed_bytes']
    monkeypatch.setattr(cache, 'MAX_BYTES', unit * 2)
    monkeypatch.setattr(cache, 'MAX_ENTRIES', 2)
    monkeypatch.setattr(cache, 'MAX_DISK_BYTES', unit * 4)
    keys = []
    for index in range(4):
        other = list(key); other[2] = f'{index:06d}.SH'; other = tuple(other)
        value = deepcopy(context)
        value['cache_key'] = price_cache_key(other[2], other[3], 'forward', {'verified_closure_sha256': other[4]})
        assert cache.remember(other, value)
        keys.append(other)
    stats = cache.cache_stats()
    assert stats['entries'] <= 2 and stats['compressed_bytes'] <= unit * 2
    cache.clear_cache()
    # A very small SQLite physical quota may safely reject writes; every
    # surviving record must remain exact rather than bypassing the disk limit.
    surviving = [cache.restore(item) for item in keys]
    assert all(value is None or len(value['raw_rows']) == len(context['raw_rows']) for value in surviving)


def test_disk_budget_evicts_oldest_and_keeps_quota_consistent(sample, monkeypatch):
    key, context, _, _, _ = sample
    generator = random.Random(117)
    for row in context['raw_rows']:
        row['source_extra'] = generator.randbytes(500).hex()
    monkeypatch.setattr(cache, 'MAX_DISK_BYTES', 256 * 1024)
    monkeypatch.setattr(cache, 'MAX_BYTES', 0)
    keys = []
    for index in range(8):
        other = list(key); other[2] = f'{index:06d}.SH'; other = tuple(other)
        value = dict(context)
        value['cache_key'] = price_cache_key(other[2], other[3], 'forward', {'verified_closure_sha256': other[4]})
        assert cache.remember(other, value)
        keys.append(other)
    assert cache.cache_stats()['disk_errors'] == 0
    assert cache.restore(keys[0]) is None
    assert cache.restore(keys[-1])['raw_rows'] == context['raw_rows']
    path = Path(os.environ['SHIXU_WORK_DIR']) / cache._DIRECTORY / cache._FILENAME
    with sqlite3.connect(path) as connection:
        actual = connection.execute('SELECT coalesce(sum(compressed_bytes),0) FROM seed').fetchone()[0]
        assert connection.execute('SELECT bytes FROM quota').fetchone()[0] == actual
        assert actual <= cache.MAX_DISK_BYTES * 7 // 8
    assert path.stat().st_size <= cache.MAX_DISK_BYTES


def test_independent_process_writers_keep_atomic_records_and_quota(sample):
    key, context, _, _, _ = sample
    assert cache.remember(key, context)
    script = '''import json,sys
from app.services import condition_basis_cache as cache
from app.domain.strategy_prices import price_cache_key
key=tuple(json.loads(sys.argv[1])); context=cache.restore(key)
assert context is not None
for index in range(12):
 values=list(key); values[2]=sys.argv[2]+str(index); other=tuple(values)
 context['cache_key']=price_cache_key(other[2],other[3],'forward',{'verified_closure_sha256':other[4]})
 assert cache.remember(other,context)
print('ok')
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    processes = [subprocess.Popen([sys.executable, '-c', script, json.dumps(key), prefix],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for prefix in ('one', 'two')]
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert stdout.strip() == 'ok'
    cache.clear_cache()
    assert cache.restore(key)['raw_rows'] == context['raw_rows']
    path = Path(os.environ['SHIXU_WORK_DIR']) / cache._DIRECTORY / cache._FILENAME
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('SELECT bytes FROM quota').fetchone()[0] == connection.execute('SELECT sum(compressed_bytes) FROM seed').fetchone()[0]
        assert connection.execute('SELECT count(*) FROM seed').fetchone()[0] == 25


def test_corruption_and_decompression_limits_are_optional_misses(sample, monkeypatch):
    key, context, _, _, _ = sample
    assert cache.remember(key, context)
    record = cache._cache[key]
    cache._cache[key] = (b'bad', record[1], record[2])
    assert cache.restore(key) is None
    cache.clear_cache()
    path = Path(os.environ['SHIXU_WORK_DIR']) / cache._DIRECTORY / cache._FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE seed SET payload=?', (b'broken',))
    assert cache.restore(key) is None
    assert cache.remember(key, context)
    monkeypatch.setattr(cache, 'MAX_DECOMPRESSED', 10)
    assert cache.restore(key) is None
    assert not cache.remember(key, context)


def test_unusable_or_mismatched_context_never_enters_cache(sample):
    key, context, _, _, _ = sample
    for field, value in (('price_basis', 'unavailable'), ('anchor_date', '2099-01-01'), ('cache_key', 'wrong')):
        changed = dict(context); changed[field] = value
        assert not cache.remember(key, changed)
    assert not cache.remember(None, context)
    assert cache.restore(None) is None and cache.restore(['bad']) is None
    assert cache.cache_stats()['entries'] == 0


@pytest.mark.parametrize('target', ['directory', 'database', 'journal', 'work'])
def test_symlinks_never_write_outside_private_cache(sample, tmp_path, target):
    key, context, _, _, _ = sample
    work = Path(os.environ['SHIXU_WORK_DIR']); work.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    directory = work / cache._DIRECTORY
    if target == 'directory': directory.symlink_to(outside, target_is_directory=True)
    elif target == 'work':
        work.rmdir(); work.symlink_to(outside, target_is_directory=True)
    else:
        directory.mkdir(mode=0o700)
        name = cache._FILENAME + ('-journal' if target == 'journal' else '')
        (directory / name).symlink_to(outside / 'must-not-create')
    assert cache.remember(key, context)  # safe memory path still works
    assert not list(outside.iterdir())
    assert cache.cache_stats()['disk_errors'] == 1
    cache.clear_cache()
    assert cache.restore(key) is None


def test_disk_unavailable_keeps_correct_memory_and_clear_is_namespaced(sample, monkeypatch):
    key, context, _, _, _ = sample
    monkeypatch.setattr(cache, '_put_disk', lambda *args: (_ for _ in ()).throw(PermissionError('readonly work')))
    assert cache.remember(key, context)
    assert cache.restore(key)['raw_rows'] == context['raw_rows']
    assert cache.cache_stats()['disk_errors'] == 1


def test_clear_disk_leaves_unrelated_work_files(sample):
    key, context, _, _, _ = sample
    assert cache.remember(key, context)
    root = Path(os.environ['SHIXU_WORK_DIR']); keep = root / 'unrelated.json'; keep.write_text('keep')
    cache.clear_cache(disk=True)
    assert cache.restore(key) is None
    assert keep.read_text() == 'keep'


def test_compact_encoding_checks_quotas_before_cache_admission(sample, monkeypatch):
    key, context, _, _, _ = sample
    monkeypatch.setattr(cache, 'MAX_COMPRESSED', 10)
    assert not cache.remember(key, context)
    assert cache.cache_stats()['entries'] == 0
