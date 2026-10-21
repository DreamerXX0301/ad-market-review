#!/usr/bin/env python3
"""Read-only real timings and isolated SYNTHETIC ten-year capacity checks.

No credentials, source requests, business writes or production initialization.
The synthetic weekday subset is NOT an exchange calendar or historical quotes.
Run with backend/.venv/bin/python. Each runtime child is bounded by RSS/time.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
DEFAULT_OUTPUT = ROOT / 'docs/validation/ladder-trading-ui-20261020/performance-ten-year.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def hashes():
    paths = ['backend/app/analytics/market.py', 'backend/app/analytics/panel_inputs.py',
             'backend/app/analytics/indicators.py', 'backend/app/services/topic_member_metrics.py',
             'backend/app/domain/conditions.py', 'backend/app/analytics/backtest_engine.py',
             'scripts/benchmark_ten_year.py']
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}


def rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def retained(data):
    database = data / 'business/app.sqlite3'
    connection = sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)
    try:
        row = connection.execute("SELECT s.lineage_json FROM published_head h JOIN dataset_snapshot s USING(snapshot_id) WHERE h.module='daily_raw' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    finally:
        connection.close()
    if not row: raise ValueError('No published real market generation')
    return json.loads(row[0])['market_snapshot_id']


def synthetic_calendar(count):
    start = date(2016, 10, 20); end = date(2026, 10, 20)
    candidates = []; cursor = start
    while cursor <= end:
        if cursor.weekday() < 5: candidates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    if not 31 <= count <= len(candidates): raise ValueError('Invalid synthetic session count')
    return [candidates[index * (len(candidates) - 1) // (count - 1)] for index in range(count)]


def generate(workspace, symbols, sessions):
    import duckdb
    directory = workspace / 'published/market-synthetic-ten-year-capacity'
    directory.mkdir(parents=True, exist_ok=True)
    daily = directory / 'daily.parquet'
    if daily.exists(): raise ValueError('Synthetic file already exists; reuse with --operation sql/runtime')
    calendar = synthetic_calendar(sessions)
    con = duckdb.connect(':memory:')
    con.execute("SET memory_limit='1GB'")
    con.execute('SET threads=2')
    con.execute('SET temp_directory=?', [str(workspace / 'duckdb-spill')])
    con.execute('CREATE TABLE calendar(day_index INTEGER,date VARCHAR)')
    con.executemany('INSERT INTO calendar VALUES (?,?)', list(enumerate(calendar)))
    start = time.perf_counter()
    try:
        con.execute("""COPY (
            WITH prices AS (
                SELECT CASE WHEN s%2=0 THEN printf('%06d.SH',600000+s//2)
                       ELSE printf('%06d.SZ',300000+s//2) END instrument_id,
                    c.date,20.0+(s%1000)*.05+c.day_index*.001+sin(c.day_index*.13+s*.01)*2 AS price,
                    (1000000+(c.day_index%20)*1000+(s%100)*200)::DOUBLE AS volume
                FROM range($symbol_count) symbols(s) CROSS JOIN calendar c
            )
            SELECT instrument_id,date,price+.1 AS open,price+.5 AS high,
                   price-.5 AS low,price AS close,volume,price*volume AS amount
            FROM prices ORDER BY instrument_id,date
        ) TO $destination (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 65536)""", {'symbol_count': symbols, 'destination': str(daily)})
        summary = con.execute('SELECT count(*),count(distinct instrument_id),count(distinct date),min(date),max(date) FROM read_parquet(?)', [str(daily)]).fetchone()
        identifiers = [row[0] for row in con.execute('SELECT DISTINCT instrument_id FROM read_parquet(?) ORDER BY 1', [str(daily)]).fetchall()]
        actions = directory / 'corporate-actions.parquet'
        con.execute('COPY (SELECT NULL::VARCHAR thscode,NULL::BIGINT ex_date_ms WHERE false) TO ? (FORMAT PARQUET)', [str(actions)])
    finally:
        con.close()
    summary_doc = {'snapshot_id': directory.name, 'data_begin': calendar[0], 'data_end': calendar[-1],
                   'rows': summary[0], 'symbols': summary[1], 'source': 'SYNTHETIC-CAPACITY-ONLY',
                   'price_basis': 'none', 'quality': 'synthetic-capacity-only'}
    reference = {'calendar': calendar, 'master': {symbol: {'instrument_id': symbol, 'name': 'SYNTHETIC ' + symbol,
                 'kind': 'stock', 'list_date': calendar[0]} for symbol in identifiers},
                 'topics': [{'topic_id': 'synthetic-capacity-1085', 'name': 'SYNTHETIC capacity members',
                             'kind': 'concept', 'instrument_ids': identifiers[:1085],
                             'observed_date': calendar[-1], 'members_complete': True}],
                 'indices': {}, 'synthetic': True}
    save(directory / 'reference.json', reference)
    save(directory / 'manifest.json', {'summary': summary_doc, 'synthetic': True})
    save(workspace / 'published/market-head.json', summary_doc)
    return {'synthetic': True, 'calendar_notice': 'Artificial subset of weekdays, NOT an exchange calendar',
            'price_notice': 'Deterministic invented OHLCV, NOT real history or limit events',
            'generation': directory.name, 'workspace': str(workspace), 'rows': summary[0], 'symbols': summary[1],
            'sessions': summary[2], 'first': summary[3], 'last': summary[4], 'parquet_bytes': daily.stat().st_size,
            'generation_seconds': round(time.perf_counter() - start, 6), 'peak_rss_bytes': rss(),
            'duckdb_memory_limit': '1GB', 'threads': 2, 'row_group_size': 65536}


def sql_capacity(workspace):
    import duckdb
    directory = workspace / 'published/market-synthetic-ten-year-capacity'
    reference = json.loads((directory / 'reference.json').read_text())
    calendar = reference['calendar']; symbols = sorted(reference['master'])
    daily = str(directory / 'daily.parquet')
    con = duckdb.connect(':memory:')
    con.execute("SET memory_limit='1GB'"); con.execute('SET threads=2')
    con.execute('SET temp_directory=?', [str(workspace / 'duckdb-spill')])
    report = {'synthetic': True, 'scope': 'Direct bounded DuckDB SQL, not app UI timings', 'measurements': []}
    def measure(label, query, params):
        times = []; signature = None; count = None
        for _ in range(2):
            start = time.perf_counter()
            result = con.execute(query, params).fetchall()
            times.append(round(time.perf_counter() - start, 6))
            current = digest(result)
            if signature is not None and signature != current: raise AssertionError('Repeated result changed')
            signature = current; count = len(result)
        report['measurements'].append({'label': label, 'first_seconds': times[0], 'repeat_seconds': times[1],
                                       'rows_returned': count, 'result_sha256': signature, 'peak_rss_bytes': rss()})
    try:
        measure('selected_day_all_symbols', 'SELECT * FROM read_parquet(?) WHERE date=? ORDER BY instrument_id', [daily, calendar[-1]])
        measure('previous_day_all_symbols', 'SELECT * FROM read_parquet(?) WHERE date=? ORDER BY instrument_id', [daily, calendar[-2]])
        measure('single_stock_ten_year_daily', 'SELECT * FROM read_parquet(?) WHERE instrument_id=? ORDER BY date', [daily, symbols[0]])
        measure('top1000_exact_T_Tminus30', """WITH selected AS (
            SELECT instrument_id,max(CASE WHEN date=? THEN close END) end_close,
                max(CASE WHEN date=? THEN close END) baseline
            FROM read_parquet(?) WHERE date IN (?,?) GROUP BY instrument_id
        ) SELECT instrument_id,(end_close/baseline-1)*100 change_30d_pct FROM selected
          WHERE end_close IS NOT NULL AND baseline>0 ORDER BY change_30d_pct DESC,instrument_id LIMIT 1000""",
                [calendar[-1], calendar[-31], daily, calendar[-1], calendar[-31]])
        measure('1085_member_current_day', 'SELECT * FROM read_parquet(?) WHERE instrument_id IN (SELECT unnest(?)) AND date=? ORDER BY close DESC,instrument_id',
                [daily, symbols[:1085], calendar[-1]])
        # Stream ten-year member history without millions of Python dictionaries.
        start = time.perf_counter(); count = 0; checksum = hashlib.sha256()
        reader = con.execute('SELECT instrument_id,date,close FROM read_parquet(?) WHERE instrument_id IN (SELECT unnest(?)) ORDER BY instrument_id,date',
                             [daily, symbols[:1085]]).fetch_record_batch(rows_per_batch=10000)
        for batch in reader:
            count += batch.num_rows
            checksum.update(batch.serialize().to_pybytes())
        report['measurements'].append({'label': '1085_members_ten_year_history_arrow_batches', 'seconds': round(time.perf_counter() - start, 6),
                                       'rows_returned': count, 'batch_rows_max': 10000, 'result_sha256': checksum.hexdigest(), 'peak_rss_bytes': rss()})
        assert count == len(calendar) * min(1085, len(symbols))
    finally:
        con.close()
    return report


def normalize_real_package(workspace, source, reference_data):
    """Normalize an ALREADY downloaded real source into an unpublished fixture.

    No business catalog, active head, credentials or source APIs are touched.
    Earlier source dates do not become an official calendar or identity archive.
    The missing ten-year action/pool history deliberately remains missing.
    """
    import duckdb
    source = source.resolve()
    directory = workspace / 'published/market-benchmark-real-ten-year'
    directory.mkdir(parents=True, exist_ok=True)
    daily = directory / 'daily.parquet'
    if daily.exists(): raise ValueError('Real fixture already exists; use --operation runtime with --snapshot')
    current = retained(reference_data)
    existing = json.loads((reference_data / 'published' / current / 'reference.json').read_text())
    con = duckdb.connect(':memory:'); con.execute("SET memory_limit='1GB'"); con.execute('SET threads=2')
    con.execute('SET temp_directory=?', [str(workspace / 'duckdb-spill')])
    start = time.perf_counter()
    try:
        con.read_parquet(str(source)).create_view('package')
        invalid_basis = con.execute("SELECT count(*) FROM package WHERE adjusted IS DISTINCT FROM 'none' OR interval IS DISTINCT FROM '1d' OR currency IS DISTINCT FROM 'CNY'").fetchone()[0]
        if invalid_basis: raise ValueError('Real package has mixed price basis, currency or interval')
        con.execute("""CREATE VIEW normalized AS SELECT upper(thscode) instrument_id,
            strftime(to_timestamp(date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d') date,
            open_price AS open,high_price AS high,low_price AS low,close_price AS close,
            volume,turnover AS amount,'fuyao' AS source,'none' AS price_basis FROM package""")
        bad = con.execute("""SELECT count(*) FROM normalized WHERE instrument_id IS NULL OR date IS NULL
            OR NOT regexp_full_match(instrument_id,'[0-9]{6}\\.(SH|SZ|BJ)')
            OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL OR amount IS NULL
            OR NOT isfinite(open) OR NOT isfinite(high) OR NOT isfinite(low) OR NOT isfinite(close)
            OR NOT isfinite(volume) OR NOT isfinite(amount) OR least(open,high,low,close)<=0 OR volume<0 OR amount<0
            OR high<greatest(open,close,low) OR low>least(open,close,high)""").fetchone()[0]
        duplicate = con.execute('SELECT count(*)-count(distinct(instrument_id,date)) FROM normalized').fetchone()[0]
        if bad or duplicate: raise ValueError(f'Real package contains {bad} invalid OHLC rows or {duplicate} duplicate pairs; fixture not published')
        con.execute('COPY (SELECT * FROM normalized ORDER BY instrument_id,date) TO ? (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 65536)', [str(daily)])
        calendar = [row[0] for row in con.execute('SELECT DISTINCT date FROM normalized ORDER BY date').fetchall()]
        rows, symbols = con.execute('SELECT count(*),count(distinct instrument_id) FROM normalized').fetchone()
    finally:
        con.close()
    summary = {'snapshot_id': directory.name, 'data_begin': calendar[0], 'data_end': calendar[-1],
               'rows': rows, 'symbols': symbols, 'source': 'fuyao-real-package-benchmark-only',
               'price_basis': 'none', 'quality': 'partial', 'benchmark_only': True}
    reference = {'calendar': calendar, 'master': existing.get('master', {}), 'topics': existing.get('topics', []),
                 'indices': {}, 'benchmark_only': True, 'synthetic': False,
                 'calendar_provenance': {'basis': 'source_dates_only_benchmark', 'not_official_calendar': True},
                 'benchmark_notice': '真实源包规范化副本；当前master/topics只作性能输入，非十年历史身份或成分证明；历史actions/pools未回填'}
    save(directory / 'reference.json', reference)
    save(directory / 'manifest.json', {'summary': summary, 'benchmark_only': True, 'never_published': True})
    # Fixed snapshot reads need no market-head.json and no SQLite publication.
    return {'benchmark_only': True, 'synthetic': False, 'never_published': True,
            'source_path': str(source), 'source_bytes': source.stat().st_size,
            'generation': directory.name, 'workspace': str(workspace), 'rows': rows, 'symbols': symbols,
            'sessions': len(calendar), 'first': calendar[0], 'last': calendar[-1],
            'bad_ohlc_rows': bad, 'duplicate_symbol_date_pairs': duplicate,
            'normalized_parquet_bytes': daily.stat().st_size, 'seconds': round(time.perf_counter()-start, 6),
            'peak_rss_bytes': rss(), 'duckdb_memory_limit': '1GB',
            'current_identity_reference_snapshot': current,
            'coverage_notice': '只有来源日期集合，非交易所完整历史日历；未核验缺日原因、历史退市身份、十年公司行动/涨跌停/题材/公告。性能结果不代表这些功能历史覆盖。'}


def runtime_child(data, snapshot, mode, output):
    os.environ['SHIXU_DATA_DIR'] = str(data)
    for name in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        os.environ.pop(name, None)
    from app.analytics.market import MarketData
    from app.services.topic_member_metrics import member_metrics
    market = MarketData(data, snapshot_id=snapshot)
    status = market.status(); reference = market.reference(); calendar = market.calendar(); day = calendar[-1]
    report = {'data_dir': str(data), 'snapshot_id': snapshot, 'source_sha256': hashes(), 'started_at': now(),
              'synthetic': bool(reference.get('synthetic')), 'rows': status.get('rows'),
              'sessions': len(calendar), 'mode': mode, 'measurements': []}
    def measure(label, func, panel_date=None):
        report['running'] = label; save(output, report)
        panel_cached = panel_date in market._panels if panel_date else None
        start = time.perf_counter(); value = func()
        count = len(value) if hasattr(value, '__len__') else None
        item = {'label': label, 'seconds': round(time.perf_counter() - start, 6),
                'returned_items': count, 'peak_rss_bytes': rss()}
        if panel_date and market._panel_inputs is not None:
            item.update(panel_cached_before=panel_cached,
                        indicator_steps_for_request=0 if panel_cached else getattr(market._panel_inputs, 'last_compute_steps', None),
                        restored_checkpoint_position=None if panel_cached else getattr(market._panel_inputs, 'last_checkpoint_position', None))
        if market._frame is not None:
            item['resident_frame_deep_bytes'] = int(market._frame.memory_usage(index=True, deep=True).sum())
            item['frame_dtypes'] = {str(key): str(dtype) for key, dtype in market._frame.dtypes.items()}
        report['measurements'].append(item)
        save(output, report)
        return value
    if mode == 'profile':
        from app.analytics.panel_inputs import PanelInputs
        frame = measure('profile_daily_frame_cold_process', lambda: market._daily_frame())
        market._panel_inputs = measure('profile_panel_inputs_initialization', lambda: PanelInputs(frame, calendar))
        measure('profile_indicator_latest', lambda: market._panel_inputs.latest(day), day)
        measure('profile_indicator_previous_day', lambda: market._panel_inputs.latest(calendar[-2]), calendar[-2])
        report['profile_notice'] = 'Independent process; OS filesystem caches not cleared; phases separate frame load, dense input build, indicator compute'
    elif mode == 'panel':
        measure('cold_process_latest_panel', lambda: market.panel(day), day)
        measure('same_day_cached_panel', lambda: market.panel(day), day)
        measure('previous_day_first_panel', lambda: market.panel(calendar[-2]), calendar[-2])
        measure('latest_revisited_panel', lambda: market.panel(day), day)
        from app.services.market_service import gainers_rows
        measure('application_exact_30d_ranking', lambda: gainers_rows(market, reference, day, market.panel(day))[0])
    else:
        symbols = sorted(reference.get('master', {}))
        topic = next((item for item in reference.get('topics', []) if item.get('name') == '人工智能'), None)
        selected = sorted(set(topic['instrument_ids'])) if topic else symbols[:min(1085, len(symbols))]
        report['member_selection'] = {'topic': topic.get('name') if topic else 'first 1085 retained symbols', 'count': len(selected)}
        single_symbol = '600825.SH' if '600825.SH' in symbols else symbols[0]
        single = measure('cold_single_stock_bars_application', lambda: market.bars(single_symbol, as_of=day))
        if not single: raise AssertionError('No single-stock rows')
        measure('single_stock_enrichment', lambda: __import__('app.analytics.indicators', fromlist=['enrich_bars']).enrich_bars(single, calendar))
        recent = market.history_for_instruments(selected, start=day, end=day)
        quotes = {symbol: rows[0] for symbol, rows in recent.items() if len(rows) == 1}
        master = {row['instrument_id']: row for row in market.instruments(limit=10000)}
        from app.services.market_supplement import load_supplement
        supplement = load_supplement(snapshot) if not reference.get('benchmark_only') and not reference.get('synthetic') else {}
        report['event_pool_scope'] = 'current fixed-generation retained supplement' if supplement else 'no historical event pools in this capacity fixture; missing evidence stays unknown'
        first = measure('1085_members_first_application_metrics', lambda: member_metrics(market, day, selected, master, quotes, supplement))
        second = measure('1085_members_cached_application_metrics', lambda: member_metrics(market, day, selected, master, quotes, supplement))
        report['member_results_equal'] = first == second
        report['member_result_sha256'] = digest(first)
    report.pop('running', None); report['finished_at'] = now()
    report['source_unchanged'] = report['source_sha256'] == hashes()
    save(output, report)


def supervise(data, snapshot, mode, workspace, max_rss_mb, timeout):
    output = workspace / ('runtime-' + mode + '.json'); log = workspace / ('runtime-' + mode + '.txt')
    command = [sys.executable, str(Path(__file__).resolve()), '--_child', mode, '--real-data', str(data),
               '--snapshot', snapshot, '--output', str(output)]
    start = time.perf_counter(); peak = 0; stopped = None
    with log.open('w') as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        while process.poll() is None:
            try:
                sample = subprocess.run(['ps', '-o', 'rss=', '-p', str(process.pid)], capture_output=True, text=True, timeout=2)
                resident = int(sample.stdout.strip() or 0) * 1024
                peak = max(peak, resident)
            except (ValueError, subprocess.TimeoutExpired): pass
            if peak > max_rss_mb * 1024 ** 2: stopped = 'rss_budget_exceeded'
            elif time.perf_counter() - start > timeout: stopped = 'time_budget_exceeded'
            if stopped:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
                break
            time.sleep(.25)
    result = json.loads(output.read_text()) if output.exists() else {}
    result.update(exit_code=process.returncode, stop_reason=stopped, monitored_peak_rss_bytes=peak,
                  seconds_total=round(time.perf_counter()-start, 6), rss_budget_mb=max_rss_mb, timeout_seconds=timeout,
                  completed=process.returncode == 0 and stopped is None)
    if process.returncode and not stopped: result['error_tail'] = log.read_text()[-3000:]
    return result


def http_checks(base):
    import httpx
    if urlparse(base).hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('HTTP benchmark accepts an explicitly local application API only')
    result = {'scope': 'Read-only existing API; first observed is NOT proven cold; production caches never cleared',
              'measurements': [], 'base': base,
              'loaded_server_source_hash': 'not introspected; disk source hashes do not prove running API code revision'}
    with httpx.Client(base_url=base.rstrip('/'), timeout=120) as client:
        boot = client.get('/bootstrap'); boot.raise_for_status(); dates = boot.json()['market_dates']; day = dates[-1]
        paths = [f'/overview?date={day}', f'/overview?date={day}', f'/overview?date={dates[-2]}', f'/overview?date={dates[-2]}',
                 f'/instruments/000001.SH/chart?as_of={day}&period=M',
                 f'/market/gainers?date={day}&top_n=1000&page_size=25',
                 f'/market/ladder?date={day}']
        for path in paths:
            start = time.perf_counter(); response = client.get(path); response.raise_for_status()
            document = response.json()
            result['measurements'].append({'path': path, 'seconds': round(time.perf_counter()-start, 6),
                'decoded_bytes': len(response.content), 'date': document.get('date', document.get('as_of')),
                'status': response.status_code})
        result['actual_retained_sessions'] = len(dates); result['actual_retained_start'] = dates[0]; result['actual_retained_end'] = day
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--operation', choices=['generate', 'normalize-real', 'sql', 'runtime', 'profile', 'http', 'all'], default='all')
    parser.add_argument('--symbols', type=int, default=5500); parser.add_argument('--sessions', type=int, default=2500)
    parser.add_argument('--real-data', type=Path, default=ROOT/'data')
    parser.add_argument('--base', default='http://127.0.0.1:8788/api/v1')
    parser.add_argument('--snapshot'); parser.add_argument('--_child', choices=['panel', 'bounded', 'profile'])
    parser.add_argument('--source-parquet', type=Path)
    parser.add_argument('--max-rss-mb', type=int, default=4096); parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    if args._child:
        runtime_child(args.real_data.resolve(), args.snapshot, args._child, args.output); return
    workspace = (args.workspace or Path(tempfile.mkdtemp(prefix='shixu-ten-year-capacity-'))).resolve()
    production = (ROOT/'data').resolve()
    if workspace == production or workspace.is_relative_to(production): raise ValueError('Production data cannot be a capacity workspace')
    if (workspace / 'business/app.sqlite3').exists(): raise ValueError('A business database exists in this workspace; use a new isolated directory')
    workspace.mkdir(parents=True, exist_ok=True)
    report = json.loads(args.output.read_text()) if args.output.exists() else {}
    report.update(script='scripts/benchmark_ten_year.py', started_at=now(), workspace=str(workspace),
                  last_command_arguments=sys.argv[1:],
                  environment={'platform': platform.platform(), 'python': platform.python_version(), 'memory_budget_mb': args.max_rss_mb},
                  safety={'source_requests': False, 'credentials_read': False, 'production_data_written': False,
                          'synthetic_is_not_real_coverage': True}, source_sha256=hashes())
    if args.operation in ('generate', 'all'):
        report['synthetic_dataset'] = generate(workspace, args.symbols, args.sessions); save(args.output, report)
    if args.operation == 'normalize-real':
        if args.source_parquet is None: raise ValueError('--source-parquet is required')
        report['real_dataset'] = normalize_real_package(workspace, args.source_parquet, args.real_data.resolve())
        save(args.output, report)
    if args.operation in ('sql', 'all'):
        report['synthetic_sql'] = sql_capacity(workspace); save(args.output, report)
    if args.operation in ('runtime', 'profile', 'all'):
        data = workspace if not args.snapshot else args.real_data.resolve()
        snapshot = args.snapshot or 'market-synthetic-ten-year-capacity'
        modes = ('profile',) if args.operation == 'profile' else ('panel', 'bounded')
        report['synthetic_runtime' if not args.snapshot else 'real_runtime'] = {
            mode: supervise(data, snapshot, mode, workspace, args.max_rss_mb, args.timeout) for mode in modes}
        save(args.output, report)
        runtime_results = report['synthetic_runtime' if not args.snapshot else 'real_runtime']
        if not all(value.get('completed') is True for value in runtime_results.values()):
            report['finished_at'] = now(); save(args.output, report)
            print(json.dumps({'output': str(args.output), 'passed': False, 'reason': 'A runtime child failed or exceeded the explicit resource budget'}, ensure_ascii=False))
            raise SystemExit(1)
    if args.operation in ('http', 'all'):
        report['real_http'] = http_checks(args.base)
    report['finished_at'] = now(); save(args.output, report)
    print(json.dumps({'output': str(args.output), 'workspace': str(workspace), 'operations': args.operation}, ensure_ascii=False))


if __name__ == '__main__':
    main()
