"""Offline partition experiments use only synthetic, SHA-pinned temporary input."""
from datetime import date
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


PATH = Path(__file__).with_name('compare_market_partitions.py')
spec = importlib.util.spec_from_file_location('market_partitions', PATH)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def fixture(tmp_path, *, count=8, duplicate=False, invalid=False):
    source = tmp_path / 'immutable' / 'daily.parquet'
    source.parent.mkdir()
    rows = []
    for index in range(count):
        rows.append({'instrument_id': f'{index // 4:06}.SZ',
                     'date': ('2026-01-30', '2026-01-31', '2026-02-02', '2026-02-03')[index % 4],
                     'open': 1.0, 'high': 2.0, 'low': 1.0, 'close': 1.5,
                     'volume': float(index), 'amount': -0.0 if index % 2 else 0.0,
                     'source': 'synthetic', 'price_basis': 'none',
                     'extra_nullable': None if index % 2 else 'all-columns-must-survive',
                     'extra_integer': index, 'extra_decimal': Decimal('1.23'),
                     'extra_date': date(2025, 1, 1)})
    if duplicate:
        rows.append(dict(rows[0]))
    if invalid:
        rows[0]['date'] = '2026-02-30'
    # Deliberately unsorted input verifies ordering is by both business keys,
    # not assumed to match row groups, file paths or input traversal order.
    pq.write_table(pa.Table.from_pylist(list(reversed(rows))), source, compression='zstd',
                   row_group_size=3 if count < 100 else 4096)
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    return source, scratch


def run(source, scratch, **kwargs):
    return proof.compare(source, proof.sha(source), scratch, max_temp_mib=32, memory_mib=128, **kwargs)


def test_month_and_day_preserve_every_typed_column_and_row_with_cleaned_artifacts(tmp_path):
    source, scratch = fixture(tmp_path)
    original = source.read_bytes()
    (scratch / 'unrelated').write_text('keep this')
    result = run(source, scratch)
    assert result['passed'] and result['source']['unchanged']
    assert result['source']['rows'] == 8 and result['source']['symbols'] == 2
    assert result['source']['first_date'] == '2026-01-30' and result['source']['last_date'] == '2026-02-03'
    assert {item['name'] for item in result['source']['schema']} >= {'extra_nullable', 'extra_decimal', 'extra_date', 'extra_integer'}
    assert [item['file_count'] for item in result['variants']] == [2, 4]
    for candidate in result['variants']:
        assert candidate['logical_bytes'] > 0 and candidate['all_workloads_equal']
        for key, measured in candidate['reads'].items():
            original_read = result['source']['reads'][key]
            assert measured['rows'] == original_read['rows']
            assert measured['ordered_rows_sha256'] == original_read['ordered_rows_sha256']
            assert measured['fresh_engine_seconds'] >= 0 and measured['repeat_engine_seconds'] >= 0
    assert result['resources']['cpu_seconds'] > 0
    assert result['resources']['process_lifetime_peak_rss_bytes'] > 0
    assert result['temporary_artifacts_removed'] and not result['production_format_changed']
    assert source.read_bytes() == original
    assert list(scratch.iterdir()) == [scratch / 'unrelated']
    json.dumps(result, allow_nan=False)


def test_canonical_hash_is_independent_of_partition_boundaries_across_symbol_ranges(tmp_path):
    source, scratch = fixture(tmp_path, count=65_544)
    result = run(source, scratch)
    assert result['source']['rows'] == 65_544
    assert result['full_proof']['range_count'] > 1
    assert all(item['all_workloads_equal'] for item in result['variants'])
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize('change', ['missing', 'extra_below_min', 'extra_above_max', 'null_key'])
def test_frozen_128_symbol_ranges_detect_missing_and_extra_candidate_rows(tmp_path, change):
    source, scratch = fixture(tmp_path, count=520)
    table = pq.read_table(source)
    rows = table.to_pylist()
    if change == 'missing':
        rows.pop()
    else:
        extra = dict(rows[0])
        extra['instrument_id'] = {'extra_below_min': '-extra', 'extra_above_max': 'Z-extra', 'null_key': None}[change]
        rows.append(extra)
    candidate = tmp_path / 'candidate.parquet'
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), candidate)
    symbols = sorted(set(table.column('instrument_id').to_pylist()))
    ranges = proof.symbol_ranges(symbols)
    assert len(ranges) == 2 and ranges[0][0] is None and ranges[-1][1] is None
    con = proof.connection(scratch, 128, 16 * proof.MIB)
    try:
        original = proof.ranged_digest(con, [str(source)], table.column_names, ranges)
        if change == 'null_key':
            with pytest.raises(ValueError, match='NULL keys'):
                proof.ranged_digest(con, [str(candidate)], table.column_names, ranges)
        else:
            altered = proof.ranged_digest(con, [str(candidate)], table.column_names, ranges)
            assert original['rows'] != altered['rows']
            assert original['ordered_rows_sha256'] != altered['ordered_rows_sha256']
    finally:
        con.close()


@pytest.mark.parametrize('duplicate,invalid', [(True, False), (False, True)])
def test_duplicate_or_invalid_keys_fail_without_leaving_temp_artifacts(tmp_path, duplicate, invalid):
    source, scratch = fixture(tmp_path, duplicate=duplicate, invalid=invalid)
    with pytest.raises(ValueError, match='keys'):
        run(source, scratch)
    assert not list(scratch.iterdir())


def test_missing_normalized_columns_are_not_silently_dropped(tmp_path):
    source, scratch = fixture(tmp_path)
    pq.write_table(pa.table({'date': ['2026-01-01'], 'instrument_id': ['fixture']}), source)
    with pytest.raises(ValueError, match='columns'):
        run(source, scratch)
    assert not list(scratch.iterdir())


def test_extra_candidate_column_is_not_ignored_by_projection(tmp_path):
    source, scratch = fixture(tmp_path)
    table = pq.read_table(source)
    candidate = tmp_path / 'candidate.parquet'
    pq.write_table(table.append_column('unexpected', pa.array([1] * len(table))), candidate)
    with pytest.raises(ValueError, match='columns differ'):
        proof.measure([str(candidate)], table.column_names, {'full': ('', ())}, [(None, None)],
                      scratch, 128, 16 * proof.MIB)


def test_sha_mismatch_refuses_experiment_before_creating_artifacts(tmp_path):
    source, scratch = fixture(tmp_path)
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        proof.compare(source, '0' * 64, scratch)
    assert not list(scratch.iterdir())


@pytest.mark.parametrize('field,value', [('max_temp_mib', 512), ('max_temp_mib', True), ('memory_mib', 2048)])
def test_unbounded_or_invalid_resource_limits_are_refused(tmp_path, field, value):
    source, scratch = fixture(tmp_path)
    with pytest.raises(ValueError, match='budget|limit'):
        proof.compare(source, proof.sha(source), scratch, **{field: value})
    assert not list(scratch.iterdir())


def test_disk_reserve_is_required_without_cleanup_of_unrelated_data(tmp_path, monkeypatch):
    source, scratch = fixture(tmp_path)
    (scratch / 'keep').write_text('untouched')
    monkeypatch.setattr(proof.shutil, 'disk_usage', lambda *args: SimpleNamespace(free=proof.RESERVE))
    with pytest.raises(proof.BudgetExceeded, match='2 GiB'):
        run(source, scratch)
    assert (scratch / 'keep').read_text() == 'untouched' and len(list(scratch.iterdir())) == 1


def test_budgeted_writer_does_not_write_past_candidate_cap(tmp_path):
    budget = proof.FileBudget(tmp_path, 7)
    with proof.BudgetFile(tmp_path / 'new', budget) as handle:
        assert handle.write(b'1234') == 4
        with pytest.raises(proof.BudgetExceeded):
            handle.write(b'5678')
    assert budget.used == 4 and (tmp_path / 'new').read_bytes() == b'1234'


def test_mid_experiment_failure_removes_only_its_owned_workspace(tmp_path, monkeypatch):
    source, scratch = fixture(tmp_path)
    (scratch / 'keep').write_bytes(b'unrelated')

    def fail(*args, **kwargs):
        raise RuntimeError('fixture interrupted read')

    monkeypatch.setattr(proof, 'measure', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        run(source, scratch)
    assert [item.name for item in scratch.iterdir()] == ['keep']
    assert source.exists()


def test_changed_source_cannot_produce_success_receipt(tmp_path, monkeypatch):
    source, scratch = fixture(tmp_path)
    partition = proof.partition

    def touch_source(*args, **kwargs):
        files, size = partition(*args, **kwargs)
        source.touch()
        return files, size

    monkeypatch.setattr(proof, 'partition', touch_source)
    with pytest.raises(ValueError, match='source changed'):
        run(source, scratch)
    assert not list(scratch.iterdir())


def test_aliased_source_and_source_overlapping_temp_are_refused(tmp_path):
    source, scratch = fixture(tmp_path)
    alias = source.with_name('alias.parquet')
    alias.symlink_to(source)
    with pytest.raises(ValueError, match='symlinks'):
        proof.compare(alias, proof.sha(source), scratch)
    with pytest.raises(ValueError, match='separate'):
        proof.compare(source, proof.sha(source), tmp_path)


def test_cli_small_receipt_is_new_and_has_no_retained_parquet(tmp_path):
    source, scratch = fixture(tmp_path)
    output = tmp_path / 'receipt.json'
    args = [sys.executable, str(PATH), '--source', str(source), '--expected-sha256', proof.sha(source),
            '--temp-dir', str(scratch), '--output', str(output), '--memory-mib', '128', '--max-temp-mib', '32']
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['passed']
    receipt = json.loads(output.read_text())
    assert receipt['passed'] and receipt['temporary_artifacts_removed']
    assert output.stat().st_size < 20_000 and not list(scratch.iterdir())
    original_receipt = output.read_bytes()
    duplicate = subprocess.run(args, capture_output=True, text=True)
    assert duplicate.returncode != 0 and output.read_bytes() == original_receipt
