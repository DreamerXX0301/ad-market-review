#!/usr/bin/env python3
"""Bounded offline import of an existing real price package into an isolated root.

This tests normalization/publication I/O, not remote synchronization or complete
event/calendar/action coverage. No business DB, Keys or production head changes.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


def save(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def child(args):
    from app.analytics.market import MarketData
    from app.services.sync_service import midnight_ms
    source=args.source.resolve(); workspace=args.workspace.resolve()
    if workspace.exists():raise ValueError('Use a new isolated workspace')
    if workspace.is_relative_to(ROOT/'data') or source.is_relative_to(workspace):raise ValueError('Isolated paths required')
    reference=json.loads(args.reference.read_text())
    audit=workspace/'audit';raw=audit/'raw';raw.mkdir(parents=True)
    # Link an immutable input; import_audit itself copies its raw output and the
    # timer includes that physical copy. No production object is written.
    os.link(source,audit/'daily.parquet')
    save(audit/'bundle.json',{'source':'fuyao','price_basis':'none','daily_file':'daily.parquet','collected_at':'2026-10-21','coverage':{'benchmark_only':True,'initial_window':'near-ten-years','calendar_provenance':{'basis':'benchmark_source_dates_only','official_history':False}},'topic_files':[]})
    master=[row for row in reference.get('master',{}).values() if row.get('kind')!='index']
    save(raw/'fuyao_stock_master.json',{'data':{'item':master}})
    save(raw/'fuyao_calendar_year.json',{'data':{'item':[{'date_ms':midnight_ms(day)} for day in reference['calendar']]}})
    data=workspace/'isolated-data'
    started=time.perf_counter()
    result=MarketData(data).import_audit(audit,lambda phase,pct:save(args.output,{'running':phase,'progress':pct}))
    seconds=time.perf_counter()-started
    files=[p for p in data.rglob('*') if p.is_file()]
    report={'passed':True,'seconds':round(seconds,6),'rows':result['rows'],'symbols':result['symbols'],
            'data_begin':result['data_begin'],'data_end':result['data_end'],
            'published_bytes':sum(p.stat().st_size for p in files if 'published' in p.parts),
            'spill_directories_remaining':[str(p) for p in (data/'work').iterdir()],
            'source_file_sha256':hashlib.file_digest(source.open('rb'),'sha256').hexdigest(),
            'market_source_sha256':hashlib.sha256((ROOT/'backend/app/analytics/market.py').read_bytes()).hexdigest(),
            'notice':'Real-price normalization/copy/hash publication only; source-derived dates, no complete historical actions/events/identity proof.',
            'safety':{'remote_requests':0,'credentials_read':False,'business_sqlite_created':False,'production_written':False}}
    assert not report['spill_directories_remaining']
    save(args.output,report)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--child',action='store_true');args=parser.parse_args()
    if args.child:child(args);return
    command=[sys.executable,__file__,'--source',str(args.source),'--reference',str(args.reference),'--workspace',str(args.workspace),'--output',str(args.output),'--child']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    peak=0;started=time.perf_counter();reason=None
    with args.output.with_suffix('.log').open('w') as log:
        proc=subprocess.Popen(command,stdout=log,stderr=log)
        while proc.poll() is None:
            memory=subprocess.run(['ps','-o','rss=','-p',str(proc.pid)],capture_output=True,text=True).stdout.strip()
            peak=max(peak,int(memory or 0)*1024)
            if peak>4*1024**3:reason='rss_budget_exceeded'
            if time.perf_counter()-started>240:reason='timeout'
            if reason:
                proc.terminate();proc.wait(timeout=15);break
            time.sleep(.25)
    result=json.loads(args.output.read_text()) if args.output.exists() else {}
    result.update(exit_code=proc.returncode,stop_reason=reason,monitored_peak_rss_bytes=peak,rss_limit_bytes=4*1024**3)
    result['passed']=result.get('passed') is True and proc.returncode==0 and reason is None
    save(args.output,result)
    print(json.dumps({'passed':result['passed'],'report':str(args.output),'peak_rss_bytes':peak,'seconds':result.get('seconds')}))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
