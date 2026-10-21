#!/usr/bin/env python3
"""Compare real retained five/ten-year prices in unpublished local fixtures.

No credentials, network, SQLite, production writes or source jobs. Run with the
backend venv; a fresh child per workload has a hard 4GiB RSS / 240s budget.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import benchmark_ten_year as base

SOURCE = Path('/tmp/shixu-ten-year-real-20261021/published/market-benchmark-real-ten-year')
DEFAULT_OUTPUT = ROOT/'docs/validation/history-window-20261021/performance.json'


def fingerprint():
    result=base.hashes()
    for name in ('scripts/benchmark_history_windows.py','backend/app/analytics/monthly.py'):
        result[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return result


def prepare(workspace, source):
    import duckdb
    if not source.is_dir() or not (source/'manifest.json').is_file():raise ValueError('Existing real fixture required')
    original=json.loads((source/'reference.json').read_text())
    if original.get('synthetic'):raise ValueError('Synthetic cannot be used for real window comparison')
    source_file=source/'daily.parquet';end=max(original['calendar'])
    from app.services.three_year_calendar import years_before
    con=duckdb.connect(':memory:');con.execute("SET memory_limit='1GB'");con.execute('SET threads=2')
    con.execute('SET temp_directory=?',[str(workspace/'spill')])
    result=[]
    try:
        for years in (5,10):
            snapshot=f'market-benchmark-real-{years}y';folder=workspace/'published'/snapshot
            folder.mkdir(parents=True,exist_ok=True)
            output=folder/'daily.parquet';wanted=years_before(end,years)
            if output.exists():raise ValueError('Existing window fixture: reuse --operation runtime instead')
            start=time.perf_counter()
            con.execute('COPY (SELECT * FROM read_parquet($source) WHERE date BETWEEN $wanted AND $ending ORDER BY instrument_id,date) TO $destination (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 65536)',
                        {'source':str(source_file),'wanted':wanted,'ending':end,'destination':str(output)})
            rows,symbols=con.execute('SELECT count(*),count(distinct instrument_id) FROM read_parquet(?)',[str(output)]).fetchone()
            calendar=[row[0] for row in con.execute('SELECT DISTINCT date FROM read_parquet(?) ORDER BY date',[str(output)]).fetchall()]
            summary={'snapshot_id':snapshot,'data_begin':calendar[0],'data_end':end,'rows':rows,'quality':'partial','benchmark_only':True,'price_basis':'none','source':'same-already-downloaded-real-prices'}
            reference={**original,'calendar':calendar,'benchmark_only':True,'indices':{},
                       'coverage':{'initial_window':f'near-{ "five" if years==5 else "ten"}-years','calendar_provenance':{'basis':'source_dates_only_not_official','official_history':False}},
                       'benchmark_notice':'Current identity/topics are capacity proxies, not historical PIT membership; no ten-year action evidence or event pools were created.'}
            base.save(folder/'reference.json',reference);base.save(folder/'manifest.json',{'summary':summary,'benchmark_only':True,'never_published':True})
            result.append({'years':years,'snapshot_id':snapshot,'rows':rows,'symbols':symbols,'sessions':len(calendar),'wanted':wanted,'first':calendar[0],'last':calendar[-1],'parquet_bytes':output.stat().st_size,'copy_seconds':round(time.perf_counter()-start,6)})
    finally:con.close()
    return {'source_file':str(source_file),'source_sha256':hashlib.sha256(source_file.read_bytes()).hexdigest(),'datasets':result,'notice':'Both fixtures use the exact same existing normalized real-price source; source calendar completeness/actions are unproved.'}


def child(workspace,years,mode,output):
    os.environ['SHIXU_DATA_DIR']=str(workspace)
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'):os.environ.pop(key,None)
    from app.analytics.market import MarketData
    market=MarketData(workspace,snapshot_id=f'market-benchmark-real-{years}y')
    status=market.status();reference=market.reference();calendar=market.calendar();day=calendar[-1]
    report={'years':years,'mode':mode,'started_at':base.now(),'snapshot_id':status['snapshot_id'],'rows':status['rows'],'source_sha256':fingerprint(),'measurements':[]}
    def measure(label,call,observe=None):
        report['running']=label;base.save(output,report)
        cached=observe in market._panels if observe else None
        start=time.perf_counter();value=call()
        row={'label':label,'seconds':round(time.perf_counter()-start,6),'peak_rss_bytes':base.rss()}
        if observe and market._panel_inputs is not None:
            row.update(indicator_steps=0 if cached else market._panel_inputs.last_compute_steps,restored_checkpoint=None if cached else market._panel_inputs.last_checkpoint_position)
        if market._frame is not None:row['resident_frame_deep_bytes']=int(market._frame.memory_usage(index=True,deep=True).sum())
        report['measurements'].append(row);base.save(output,report);return value
    if mode=='panel':
        measure('cold_latest_panel',lambda:market.panel(day),day)
        measure('cached_latest_panel',lambda:market.panel(day),day)
        previous=calendar[-2];measure('neighbor_previous_panel',lambda:market.panel(previous),previous)
        for label,offset in (('random_early',len(calendar)//5),('random_later',len(calendar)*4//5)):
            target=calendar[offset];measure(label,lambda target=target:market.panel(target),target)
        from app.services.market_service import gainers_rows
        ranking=measure('latest_30d_ranking',lambda:gainers_rows(market,reference,day,market.panel(day))[0])
        report['ranking_rows']=len(ranking)
    else:
        from app.services.topic_member_metrics import member_metrics
        topic=next(row for row in reference['topics'] if row.get('name')=='人工智能')
        selected=sorted(set(topic['instrument_ids']));report['member_count']=len(selected)
        histories=market.history_for_instruments(selected,start=day,end=day)
        quotes={symbol:items[0] for symbol,items in histories.items() if len(items)==1}
        master={row['instrument_id']:row for row in market.instruments(limit=10000)}
        first=measure('members_cold_metrics',lambda:member_metrics(market,day,selected,master,quotes,{}))
        second=measure('members_cached_metrics',lambda:member_metrics(market,day,selected,master,quotes,{}))
        assert first==second;report['member_result_sha256']=base.digest(first)
        chart_stats=[]
        for symbol in ('600519.SH','600825.SH'):
            for period in ('D','W','M'):
                chart=measure(f'{symbol}_{period}_chart',lambda:market.chart(symbol,period,day,'none'))
                bars=chart['bars'];closed=[bar for bar in bars if period=='D' or bar.get('complete') is True]
                first_week_unproven=period=='W' and bool(calendar) and datetime.fromisoformat(calendar[0]).weekday()>0
                usable=[bar for bar in closed if not(first_week_unproven and bar is bars[0])]
                chart_stats.append({'symbol':symbol,'period':period,'bars':len(bars),'completed_periods':len(closed),
                    'conservative_complete_periods':len(usable),'first_week_boundary_unproven':first_week_unproven,
                    'forming_periods':sum(not row.get('complete',False) for row in bars) if period!='D' else 0,
                    'completed_ma60_values':sum(i>=59 and row.get('ma60') is not None for i,row in enumerate(usable)),
                    'completed_ma250_values':sum(i>=249 and row.get('ma250') is not None for i,row in enumerate(usable)),
                    'raw_numeric_ma60_values':sum(row.get('ma60') is not None for row in bars),
                    'raw_numeric_ma250_values':sum(row.get('ma250') is not None for row in bars),
                    'ma120_field_supported':any('ma120' in row for row in bars),
                    'completed_macd_values':sum(row.get('macd_dif') is not None for row in usable),
                    'latest_completed_date':usable[-1]['date'] if usable else None,
                    'latest_completed_ma60':usable[-1].get('ma60') if usable else None,
                    'latest_completed_ma250':usable[-1].get('ma250') if usable else None,
                    'action_coverage_known':all(row.get('corporate_action_coverage_known') is True for row in bars),
                    'price_basis':chart['price_basis'],'adjustment_usable':chart['adjustment'].get('usable'),
                    'notice':'Raw chart numeric availability only; missing action archive does not prove forward-adjusted or complete historical indicators.'})
        report['charts']=chart_stats
    report.pop('running',None);report.update(finished_at=base.now(),source_unchanged=report['source_sha256']==fingerprint());base.save(output,report)


def supervise(workspace,years,mode,limit,timeout):
    output=workspace/f'run-{years}-{mode}.json';log=workspace/f'run-{years}-{mode}.txt'
    command=[sys.executable,str(Path(__file__).resolve()),'--child',mode,'--years',str(years),'--workspace',str(workspace),'--output',str(output)]
    started=time.perf_counter();peak=0;reason=None
    with log.open('w') as stream:
        process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        while process.poll() is None:
            sample=subprocess.run(['ps','-o','rss=','-p',str(process.pid)],text=True,capture_output=True)
            peak=max(peak,int(sample.stdout.strip() or 0)*1024)
            if peak>limit*1024**2:reason='rss_budget_exceeded'
            if time.perf_counter()-started>timeout:reason='timeout'
            if reason:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                break
            time.sleep(.25)
    result=json.loads(output.read_text()) if output.exists() else {}
    result.update(exit_code=process.returncode,stop_reason=reason,monitored_peak_rss_bytes=peak,
                  completed=reason is None and process.returncode==0,seconds_total=round(time.perf_counter()-started,6))
    if process.returncode:result['error_tail']=log.read_text()[-2500:]
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation',choices=('prepare','runtime'),default='runtime')
    parser.add_argument('--workspace',type=Path,default=Path('/tmp/shixu-history-windows-20261021'))
    parser.add_argument('--source',type=Path,default=SOURCE);parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--child',choices=('panel','bounded'));parser.add_argument('--years',type=int,choices=(5,10))
    parser.add_argument('--max-rss-mb',type=int,default=4096);parser.add_argument('--timeout',type=int,default=240)
    args=parser.parse_args();workspace=args.workspace.resolve()
    if workspace==(ROOT/'data').resolve() or workspace.is_relative_to((ROOT/'data').resolve()) or (workspace/'business/app.sqlite3').exists():raise ValueError('Use an unpublished isolated workspace without business DB')
    if args.child:child(workspace,args.years,args.child,args.output);return
    workspace.mkdir(parents=True,exist_ok=True)
    report=json.loads(args.output.read_text()) if args.output.exists() else {}
    report.update(started_at=base.now(),source_sha256=fingerprint(),workspace=str(workspace),
                  safety={'new_source_requests':False,'credentials_read':False,'production_written':False,'published_head_changed':False},
                  environment={'rss_limit_mb':args.max_rss_mb,'timeout_seconds':args.timeout,'cold_notice':'Fresh process, OS filesystem cache not cleared; no concurrent project build/pytest/API intended.'})
    if args.operation=='prepare':report['fixtures']=prepare(workspace,args.source.resolve())
    else:
        report['runtime']={}
        for years in (5,10):
            report['runtime'][str(years)]={}
            for mode in ('panel','bounded'):
                result=supervise(workspace,years,mode,args.max_rss_mb,args.timeout)
                report['runtime'][str(years)][mode]=result;base.save(args.output,report)
                if not result['completed'] or not result.get('source_unchanged'):raise SystemExit(1)
    report['finished_at']=base.now();base.save(args.output,report)
    print(json.dumps({'output':str(args.output),'operation':args.operation,'passed':True}))


if __name__=='__main__':main()
