#!/usr/bin/env python3
"""Preview or resume exact market GC during an explicit offline maintenance window."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',required=True);parser.add_argument('--plan',required=True)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--offline-maintenance',action='store_true');parser.add_argument('--resume-collection')
    parser.add_argument('--keep-latest',type=int,default=2)
    args=parser.parse_args();root=Path(args.data_dir).resolve();plan_path=Path(args.plan).resolve()
    # Environment points ONLY to the explicit candidate root. No inherited
    # source database/path is allowed to redirect an offline operation.
    os.environ.update(SHIXU_DATA_DIR=str(root),SHIXU_SQLITE_PATH=str(root/'business/app.sqlite3'),SHIXU_PUBLISHED_DIR=str(root/'published'),SHIXU_WORK_DIR=str(root/'work'),SHIXU_RAW_DIR=str(root/'raw'))
    if not (root/'business/app.sqlite3').is_file():raise ValueError('指定业务库不存在')
    from app.core import connect
    from app.services.market_storage_gc import preview,collect
    if args.apply:
        if not args.offline_maintenance:raise ValueError('请先停止目标API及worker并显式指定--offline-maintenance')
        plan=json.loads(plan_path.read_text());(root/'work').mkdir(exist_ok=True)
        with (root/'work/worker.lock').open('a+b') as lock:
            try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('目标worker仍在运行，拒绝清理') from None
            with connect() as connection:result=collect(connection,root/'published',plan,offline=True,collection_id=args.resume_collection)
    else:
        if plan_path.exists():raise ValueError('预览计划须写入新路径')
        with connect() as connection:result=preview(connection,root/'published',keep_latest=args.keep_latest)
        plan_path.parent.mkdir(parents=True,exist_ok=True);plan_path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    display={key:value for key,value in result.items() if key!='scope'}
    if 'scope' in result:display['plan']=str(plan_path)
    print(json.dumps(display,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
