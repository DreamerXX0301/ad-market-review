#!/usr/bin/env python3
"""Offline format feasibility experiment; never imports or changes the application.

Run with backend/.venv/bin/python. Input must be one explicitly SHA-pinned,
immutable normalized daily Parquet file. Output is a small local JSON receipt;
all generated Parquet and DuckDB spill files are removed on success or failure.
This does not enable a new publisher, reader, retention policy or backup format.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import stat
import sys
import tempfile
import time

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


MIB = 1024 ** 2
RESERVE = 2 * 1024 ** 3
BATCH = 65_536
REQUIRED = {'instrument_id', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'source', 'price_basis'}


class BudgetExceeded(ValueError):
    pass


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(MIB), b''):
            result.update(chunk)
    return result.hexdigest()


def explicit_path(value, *, directory=False):
    path = Path(value).expanduser().absolute()
    if path != path.resolve(strict=True) or path.is_symlink():
        raise ValueError('paths must not contain symlinks or aliases')
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('expected ordinary file or directory')
    return path


def identity(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


class HashSink(io.RawIOBase):
    def __init__(self):
        super().__init__()
        self.digest = hashlib.sha256()

    def writable(self):
        return True

    def write(self, value):
        self.digest.update(value)
        return len(value)


class FileBudget:
    """Half the total cap is reserved separately for DuckDB's bounded spill."""
    def __init__(self, root, limit):
        self.root, self.limit, self.used = root, limit, 0

    def charge(self, count):
        if self.used + count > self.limit:
            raise BudgetExceeded('candidate Parquet exceeds its temporary byte budget')
        if shutil.disk_usage(self.root).free < RESERVE + count:
            raise BudgetExceeded('2 GiB free-space reserve reached')
        self.used += count


class BudgetFile(io.RawIOBase):
    def __init__(self, path, budget):
        super().__init__()
        self.handle, self.budget = path.open('xb', buffering=0), budget

    def writable(self):
        return True

    def tell(self):
        return self.handle.tell()

    def write(self, value):
        self.budget.charge(len(value))
        if self.handle.write(value) != len(value):
            raise OSError('partial temporary Parquet write')
        return len(value)

    def close(self):
        if not self.closed:
            self.handle.close()
        super().close()


def connection(spill, memory_mib, spill_bytes):
    con = duckdb.connect(':memory:')
    try:
        con.execute('SET threads=1')
        con.execute('SET memory_limit=?', [f'{memory_mib}MiB'])
        con.execute('SET temp_directory=?', [str(spill)])
        con.execute('SET max_temp_directory_size=?', [f'{spill_bytes}B'])
        con.execute('SET autoinstall_known_extensions=false')
        con.execute('SET autoload_known_extensions=false')
        return con
    except BaseException:
        con.close()
        raise


def ordered_digest(con, files, columns, *, where='', parameters=()):
    """All original columns, fixed Arrow schema/batches, sorted unique keys.

    This hashes decoded typed rows, not Parquet physical encoding or metadata.
    Version information is recorded; compare hashes within the same experiment.
    """
    sql = 'SELECT ' + ','.join(quoted(name) for name in columns)
    sql += ' FROM read_parquet(?,hive_partitioning=false)' + where
    sql += ' ORDER BY instrument_id,date'
    reader = con.execute(sql, [files, *parameters]).to_arrow_reader(BATCH)
    sink = HashSink()
    count = 0
    with pa.ipc.new_stream(sink, reader.schema) as writer:
        for batch in reader:
            writer.write_batch(batch)
            count += batch.num_rows
    return {'rows': count, 'ordered_rows_sha256': sink.digest.hexdigest(),
            'schema_sha256': hashlib.sha256(reader.schema.serialize()).hexdigest()}


def symbol_ranges(symbols):
    """Frozen source boundaries cover the complete key domain, including gaps.

    Open first/last bounds also catch extra candidate symbols outside the source
    minimum/maximum; ranges never filter by membership in the original ID list.
    """
    starts = symbols[::128]
    return [(starts[index] if index else None,
             starts[index + 1] if index + 1 < len(starts) else None)
            for index in range(len(starts))]


def ranged_digest(con, files, columns, ranges):
    digest = hashlib.sha256()
    rows = 0
    schema = None
    for lower, upper in ranges:
        filters, parameters = [], []
        for bound, op in ((lower, '>='), (upper, '<')):
            if bound is not None:
                filters.append('instrument_id' + op + '?')
                parameters.append(bound)
        item = ordered_digest(con, files, columns,
                              where=' WHERE ' + ' AND '.join(filters) if filters else '',
                              parameters=parameters)
        if schema is not None and schema != item['schema_sha256']:
            raise ValueError('schema changed between frozen symbol ranges')
        schema = item['schema_sha256']
        rows += item['rows']
        digest.update(json.dumps({'lower_inclusive': lower, 'upper_exclusive': upper,
                                  **item}, sort_keys=True, separators=(',', ':')).encode())
        digest.update(b'\n')
    # A NULL candidate key must not disappear between the half-open ranges.
    if rows != con.execute('SELECT count(*) FROM read_parquet(?,hive_partitioning=false)', [files]).fetchone()[0]:
        raise ValueError('candidate key domain is incomplete or contains NULL keys')
    return {'rows': rows, 'ordered_rows_sha256': digest.hexdigest(),
            'schema_sha256': schema, 'range_count': len(ranges)}


def measure(files, columns, workloads, ranges, spill, memory_mib, spill_bytes):
    if any(pq.ParquetFile(path).schema_arrow.names != columns for path in files):
        raise ValueError('candidate columns differ from the frozen source schema')
    result = {}
    for key, (where, parameters) in workloads.items():
        con = connection(spill, memory_mib, spill_bytes)
        try:
            begin = time.perf_counter()
            first = (ranged_digest(con, files, columns, ranges) if key == 'full' else
                     ordered_digest(con, files, columns, where=where, parameters=parameters))
            first_seconds = time.perf_counter() - begin
            begin = time.perf_counter()
            repeated = (ranged_digest(con, files, columns, ranges) if key == 'full' else
                        ordered_digest(con, files, columns, where=where, parameters=parameters))
            repeat_seconds = time.perf_counter() - begin
            if first != repeated:
                raise ValueError('repeated read changed decoded rows')
            result[key] = {**first, 'fresh_engine_seconds': first_seconds,
                           'repeat_engine_seconds': repeat_seconds}
        finally:
            con.close()
    return result


def partition(source, folder, grain, columns, spill, memory_mib, spill_bytes, candidate_cap):
    """Bound sorting to one source month; stream days without per-day rescans.

    This feasibility writer may reread the same source row groups each month.
    It is intentionally not a proposed production incremental publisher.
    """
    con = connection(spill, memory_mib, spill_bytes)
    budget = FileBudget(folder, candidate_cap)
    writer = handle = None
    active = None
    files = []
    try:
        months = con.execute("SELECT DISTINCT substr(date,1,7) FROM read_parquet(?) ORDER BY 1", [str(source)]).fetchall()
        for (month,) in months:
            reader = con.execute('SELECT ' + ','.join(quoted(name) for name in columns) +
                                 ' FROM read_parquet(?) WHERE date>=? AND date<=? ORDER BY date,instrument_id',
                                 [str(source), month + '-01', month + '-31']).to_arrow_reader(BATCH)
            date_index = reader.schema.get_field_index('date')
            for batch in reader:
                dates = batch.column(date_index).to_pylist()
                start = 0
                while start < len(dates):
                    key = dates[start][:7] if grain == 'month' else dates[start]
                    end = start + 1
                    while end < len(dates) and (dates[end][:7] if grain == 'month' else dates[end]) == key:
                        end += 1
                    if key != active:
                        if writer:
                            writer.close()
                            handle.close()
                        path = folder / (key + '.parquet')
                        handle = BudgetFile(path, budget)
                        writer = pq.ParquetWriter(handle, reader.schema, compression='zstd')
                        files.append(str(path))
                        active = key
                    writer.write_batch(batch.slice(start, end - start))
                    start = end
        if writer:
            writer.close()
            writer = None
            handle.close()
            handle = None
        return files, budget.used
    finally:
        try:
            if writer:
                writer.close()
        finally:
            if handle:
                handle.close()
            con.close()


def compare(source, expected_sha256, temp_dir, *, max_temp_mib=511, memory_mib=512):
    if type(max_temp_mib) is not int or not 32 <= max_temp_mib <= 511:
        raise ValueError('temporary budget must be 32..511 MiB')
    if type(memory_mib) is not int or not 128 <= memory_mib <= 1024:
        raise ValueError('DuckDB memory limit must be 128..1024 MiB')
    if not re.fullmatch('[0-9a-f]{64}', expected_sha256 or ''):
        raise ValueError('explicit expected SHA256 is required')
    source = explicit_path(source)
    temp_dir = explicit_path(temp_dir, directory=True)
    if source.is_relative_to(temp_dir) or temp_dir.is_relative_to(source.parent):
        raise ValueError('temporary directory must be separate from the immutable source')
    cap = max_temp_mib * MIB
    free_before = shutil.disk_usage(temp_dir).free
    if free_before < RESERVE + cap:
        raise BudgetExceeded('need full temporary budget plus a 2 GiB free-space reserve')
    before = identity(source)
    if sha(source) != expected_sha256:
        raise ValueError('source SHA256 mismatch')
    cpu_begin = time.process_time()
    wall_begin = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='market-partition-proof-', dir=temp_dir) as working:
        workspace = Path(working)
        spill = workspace / 'spill'
        spill.mkdir()
        con = connection(spill, memory_mib, cap // 2)
        try:
            schema = con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [str(source)]).fetchall()
            columns = [row[0] for row in schema]
            original_names = pq.ParquetFile(source).schema_arrow.names
            if original_names != columns or len(set(original_names)) != len(original_names) or not REQUIRED <= set(columns):
                raise ValueError('input must contain all normalized daily columns without duplicates')
            if dict((row[0], row[1]) for row in schema).get('date') != 'VARCHAR':
                raise ValueError('normalized date must be an ISO date string')
            rows, begin, end, symbols, bad, duplicates = con.execute("""
                SELECT count(*),min(date),max(date),count(distinct instrument_id),
                count(*) FILTER(WHERE instrument_id IS NULL OR date IS NULL OR
                  try_cast(date AS DATE) IS NULL OR
                  strftime(try_cast(date AS DATE),'%Y-%m-%d') IS DISTINCT FROM date),
                count(*)-count(distinct (instrument_id,date)) FROM read_parquet(?)
            """, [str(source)]).fetchone()
            if not rows or bad or duplicates:
                raise ValueError('empty, invalid or duplicate normalized keys')
            symbol_ids = [row[0] for row in con.execute('SELECT DISTINCT instrument_id FROM read_parquet(?) ORDER BY instrument_id', [str(source)]).fetchall()]
            symbol = symbol_ids[0]
            ranges = symbol_ranges(symbol_ids)
            recent = con.execute('SELECT DISTINCT date FROM read_parquet(?) ORDER BY date DESC LIMIT 20', [str(source)]).fetchall()[-1][0]
        finally:
            con.close()
        workloads = {'full': ('', ()), 'single_symbol': (' WHERE instrument_id=?', (symbol,)),
                     'recent_20_retained_sessions': (' WHERE date>=?', (recent,))}
        original = measure([str(source)], columns, workloads, ranges, spill, memory_mib, cap // 2)
        variants = []
        for grain in ('month', 'day'):
            folder = workspace / grain
            folder.mkdir()
            started = time.perf_counter()
            files, generated_bytes = partition(source, folder, grain, columns, spill,
                                               memory_mib, cap // 2, cap - cap // 2)
            write_seconds = time.perf_counter() - started
            measured = measure(files, columns, workloads, ranges, spill, memory_mib, cap // 2)
            for workload, result in measured.items():
                if any(result[key] != original[workload][key] for key in ('rows', 'ordered_rows_sha256', 'schema_sha256')):
                    raise ValueError(f'{grain} decoded rows differ for {workload}')
            variants.append({'grain': grain, 'logical_bytes': generated_bytes,
                             'file_count': len(files), 'write_seconds': write_seconds,
                             'all_workloads_equal': True, 'reads': measured})
            shutil.rmtree(folder)  # Only this experiment's newly created tree.
    if identity(source) != before or sha(source) != expected_sha256:
        raise ValueError('source changed during experiment; discard all measurements')
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        'model': 'offline-market-partition-proof-v1', 'passed': True,
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'source': {'sha256': expected_sha256, 'logical_bytes': before[2], 'file_count': 1,
                   'rows': rows, 'symbols': symbols, 'first_date': begin, 'last_date': end,
                   'schema': [{'name': row[0], 'type': row[1]} for row in schema],
                   'unchanged': True, 'reads': original},
        'variants': variants, 'query_scope': {'single_symbol': symbol, 'recent_begin': recent},
        'full_proof': {'model': 'sorted-128-symbol-ranges-arrow-ipc-v1', 'range_count': len(ranges),
                       'source_symbol_keys_sha256': hashlib.sha256(json.dumps(symbol_ids, separators=(',', ':')).encode()).hexdigest(),
                       'bounds': [{'lower_inclusive': lower, 'upper_exclusive': upper} for lower, upper in ranges]},
        'limits': {'total_temp_logical_cap_bytes': cap, 'duckdb_spill_cap_bytes': cap // 2,
                   'candidate_cap_bytes': cap - cap // 2, 'free_space_reserve_bytes': RESERVE,
                   'duckdb_memory_limit_mib': memory_mib, 'duckdb_threads': 1},
        'resources': {'wall_seconds': time.perf_counter() - wall_begin,
                      'cpu_seconds': time.process_time() - cpu_begin,
                      'process_lifetime_peak_rss_bytes': peak if sys.platform == 'darwin' else peak * 1024,
                      'free_before_bytes': free_before, 'free_after_bytes': shutil.disk_usage(temp_dir).free},
        'versions': {'python': platform.python_version(), 'duckdb': duckdb.__version__, 'pyarrow': pa.__version__},
        'temporary_artifacts_removed': True, 'production_format_changed': False,
        'limitations': [
            'Fresh-engine reads use a new in-memory DuckDB connection; OS cache is uncontrolled and input SHA verification warms the source. These are not cold-disk timings.',
            'Full-read timings are bounded validation workloads over fixed 128-symbol ranges, not a production full-scan benchmark. Partition writing repeatedly filters one source month before sorting.',
            'Canonical SHA compares all decoded columns and ordered rows within this recorded Arrow/DuckDB version; Parquet encoding, metadata and physical file SHA intentionally differ.',
            'Candidates use PyArrow ZSTD and date-sorted streams; the original may have a different writer, row grouping and sort order. Size/timing differences do not isolate partition granularity alone.',
            'Process peak RSS includes prior allocations and Arrow/Python outside the configured DuckDB memory limit; it is not a hard process-memory cap.',
            'No publisher, application reader, backup/restore, rolling retention, reference closure, GC or concurrent production workload is exercised.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--temp-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-temp-mib', type=int, default=511)
    parser.add_argument('--memory-mib', type=int, default=512)
    args = parser.parse_args()
    output = args.output.expanduser().absolute()
    parent = explicit_path(output.parent, directory=True)
    source = explicit_path(args.source)
    temp_dir = explicit_path(args.temp_dir, directory=True)
    if output.exists() or output.is_symlink() or parent.is_relative_to(source.parent) or output.is_relative_to(temp_dir):
        parser.error('output must be new and separate from source and temporary directories')
    result = compare(source, args.expected_sha256, temp_dir,
                     max_temp_mib=args.max_temp_mib, memory_mib=args.memory_mib)
    with output.open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'passed': result['passed'], 'rows': result['source']['rows'],
                      'output': str(output), 'temporary_artifacts_removed': True}))


if __name__ == '__main__':
    main()
