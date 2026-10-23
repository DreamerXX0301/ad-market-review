#!/usr/bin/env python3
"""Build an independent, verified partitioned generation from immutable local input.

The default conversion never opens a business database, worker DuckDB or
credentials, and never changes its source. --register is an explicit offline
maintenance operation against an independently restored candidate SQLite only.
It does not access network sources or replay strategies/alerts.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
import fcntl
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))

import duckdb
from app.analytics.market import MarketData, fsync_directory, fsync_file, hash_file
from app.analytics.market_storage import VERSION, REFERENCE_VERSION, ENCODER_VERSION, activity_window, parquet_input, safe_object, write_partitions, load_reference, write_reference


def compact_generation(source_data, generation, output_data, *, verify=True, publish_head=True):
    source_data=Path(source_data).resolve();output_data=Path(output_data).resolve()
    if output_data==source_data or output_data.is_relative_to(source_data) or source_data.is_relative_to(output_data):raise ValueError('转换来源和目标必须是分离的数据根')
    if not isinstance(generation,str) or not re.fullmatch(r'market-[a-f0-9]{24}',generation):raise ValueError('转换来源必须是明确完整的主行情代际ID')
    market=MarketData(source_data,generation);old=market.status()
    if not old.get('ready') or old.get('snapshot_id')!=generation:raise ValueError('来源行情代际不可用或manifest身份不符')
    source=source_data/'published'/generation;manifest=source/'manifest.json'
    source_sha=hash_file(manifest)
    source_files=[]
    for item in old.get('files',[]):
        key=Path(item['path']);key=Path(*key.parts[1:]) if key.parts[0]=='published' else key
        path=safe_object(source_data/'published',key)
        if not path.is_file() or path.stat().st_size!=item['bytes'] or hash_file(path)!=item['sha256']:raise ValueError('来源行情文件核验失败')
        source_files.append((path,item['sha256']))
    snapshot='market-'+sha256((generation+source_sha+VERSION+REFERENCE_VERSION+ENCODER_VERSION).encode()).hexdigest()[:24]
    published=output_data/'published';final=published/snapshot
    if final.exists():
        status=MarketData(output_data,snapshot).status();existing=status.get('manifest',{}).get('storage_conversion',{})
        if existing.get('source_manifest_sha256')!=source_sha or existing.get('source_generation')!=generation:raise ValueError('目标已存在但转换来源不符')
        if verify and not all(existing.get('exact_checks',{}).get(role,{}).get('exact_rows_verified') is True and existing['exact_checks'][role].get('differences')==0 for role in ('raw-daily.parquet','daily.parquet')):raise ValueError('已有转换未完成所需逐行对账，不能称为已验证')
        for file in status.get('files',[]):
            target=safe_object(output_data/'published',file['path'].removeprefix('published/'))
            if not target.is_file() or target.stat().st_size!=file['bytes'] or hash_file(target)!=file['sha256']:raise ValueError('已有转换对象核验失败')
        if publish_head:MarketData._publish_head(published,{key:value for key,value in status.items() if key not in ('ready','path','manifest')})
        return {'generation':snapshot,'source_generation':generation,'already_exists':True,'checks':existing['exact_checks'],'storage_write':status['storage_write'],'status':status}
    published.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(published).free<1024**3:raise ValueError('独立转换目标剩余空间不足1GiB，拒绝开始')
    stage=published/('.staging-'+snapshot);stage.mkdir()
    started=time.monotonic();files=[];stats={};checks={}
    with tempfile.TemporaryDirectory(prefix='market-compaction-',dir=output_data) as temporary:
        con=duckdb.connect(':memory:')
        try:
            con.execute("SET TimeZone='Asia/Shanghai'");con.execute("SET memory_limit='512MB'");con.execute('SET threads=2');con.execute('SET temp_directory=?',[temporary])
            for role in ('raw-daily.parquet','daily.parquet'):
                inputs=market.parquet_input(role)
                con.read_parquet(inputs).create_view('partition_source',replace=True)
                artifacts,result=write_partitions(con,'partition_source',role,published,stage,snapshot)
                files.extend(artifacts);stats[role]=result
                # The stage manifest resolves siblings only after it sits in the
                # generation folder, so compare directly against fixed objects.
                paths=[str(output_data/item['path']) for item in artifacts[1:]]
                con.read_parquet(paths).create_view('partition_result',replace=True)
                if verify:
                    differences=con.execute('SELECT count(*) FROM ((SELECT * FROM partition_source EXCEPT ALL SELECT * FROM partition_result) UNION ALL (SELECT * FROM partition_result EXCEPT ALL SELECT * FROM partition_source))').fetchone()[0]
                    if differences:raise ValueError('转换后逐行对账不一致')
                checks[role]={'rows':con.execute('SELECT count(*) FROM partition_result').fetchone()[0],'exact_rows_verified':bool(verify),'differences':0 if verify else None}
            reference_files,reference_stats=write_reference(load_reference(source/'reference.json'),published,stage,snapshot)
            files.extend(reference_files);stats['reference.json']=reference_stats
            for name in ('reference.json','corporate-actions.parquet'):
                original=source/name
                if original.is_file():
                    if name!='reference.json':shutil.copyfile(original,stage/name)
                    files.append({'role':name,'path':f'published/{snapshot}/{name}','sha256':hash_file(stage/name),'bytes':(stage/name).stat().st_size})
            head={**deepcopy(old),'snapshot_id':snapshot,'files':files,'manifest_path':f'published/{snapshot}/manifest.json',
                  'storage_layout':VERSION,'storage_encoder':ENCODER_VERSION,'storage_write':stats,'active_window':activity_window(old['data_end'],old['data_begin'])}
            for key in ('ready','path','manifest'):head.pop(key,None)
            converted={'summary':head,'source_sha256':old['manifest'].get('source_sha256'),'supplemental_hashes':old['manifest'].get('supplemental_hashes',{}),
                       'storage_conversion':{'version':VERSION,'source_generation':generation,'source_manifest_sha256':source_sha,'exact_checks':checks},
                       'quality':head.get('warnings',[]),'published_at':datetime.now(timezone.utc).isoformat()}
            (stage/'manifest.json').write_text(json.dumps(converted,ensure_ascii=False,indent=2))
            if hash_file(manifest)!=source_sha or any(hash_file(path)!=digest for path,digest in source_files):raise ValueError('转换期间不可变来源发生变化')
            for path in stage.iterdir():fsync_file(path)
            fsync_directory(stage);stage.rename(final);fsync_directory(published)
            if publish_head:MarketData._publish_head(published,head)
        finally:con.close()
    return {'generation':snapshot,'source_generation':generation,'elapsed_seconds':round(time.monotonic()-started,3),'checks':checks,'storage_write':stats,'status':MarketData(output_data,snapshot).status()}


def register_conversion(output_data,status):
    """Explicit local maintenance task with process environment restored on exit."""
    output_data=Path(output_data).resolve()
    checks=status.get('manifest',{}).get('storage_conversion',{}).get('exact_checks',{})
    if not all(checks.get(role,{}).get('exact_rows_verified') is True and checks[role].get('differences')==0 and checks[role].get('rows')==status.get('rows') for role in ('raw-daily.parquet','daily.parquet')):raise ValueError('登记转换前必须完成原始与规范化行情的逐行对账')
    if not (output_data/'business/app.sqlite3').is_file():raise ValueError('登记转换须在已恢复的独立候选业务库中执行')
    overrides={'SHIXU_DATA_DIR':str(output_data),'SHIXU_SQLITE_PATH':str(output_data/'business/app.sqlite3'),'SHIXU_PUBLISHED_DIR':str(output_data/'published'),'SHIXU_WORK_DIR':str(output_data/'work'),'SHIXU_RAW_DIR':str(output_data/'raw')}
    previous={key:os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        return _register_candidate(status)
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def _register_candidate(status):
    from app.core import connect,now_ms
    from app.services import task_service as tasks
    from app.services.bootstrap import register_market
    snapshot='daily_raw-'+status['snapshot_id'].removeprefix('market-')
    with connect() as connection:
        old=connection.execute('SELECT snapshot_id FROM dataset_snapshot WHERE snapshot_id=?',(snapshot,)).fetchone()
        if old:return {'already_registered':True,'generation':status['snapshot_id']}
        if connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone():raise ValueError('目标仍有排队或运行任务，拒绝维护登记')
    job=tasks.create_task('source_check',{'operation':'local_storage_conversion','source':'verified-local-storage-conversion','storage_layout':VERSION,'source_generation':status['manifest']['storage_conversion']['source_generation']})
    with connect() as connection:connection.execute("UPDATE job SET status='running',phase='storage_conversion',lease_owner='offline-storage-conversion',lease_expires_at_ms=? WHERE job_id=?",(now_ms()+300_000,job['job_id']))
    try:
        result=register_market(status,job['job_id']);tasks.finish(job['job_id'],'partial','本地存储转换与逐行对账已完成；未联网、未重放历史策略或提醒')
    except BaseException:
        tasks.finish(job['job_id'],'failed','本地存储转换登记未完成，旧SQLite发布水位保留');raise
    return {**result,'task_id':job['job_id']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-data',required=True);parser.add_argument('--generation',required=True);parser.add_argument('--output-data',required=True);parser.add_argument('--receipt',required=True)
    parser.add_argument('--register',action='store_true');parser.add_argument('--offline-maintenance',action='store_true')
    args=parser.parse_args();receipt=Path(args.receipt).resolve()
    if receipt.exists():raise ValueError('验收回执必须使用新文件')
    def execute():
        result=compact_generation(args.source_data,args.generation,args.output_data)
        if args.register:result['registration']=register_conversion(args.output_data,result['status'])
        result.pop('status',None);receipt.parent.mkdir(parents=True,exist_ok=True);receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.register:
        if not args.offline_maintenance:raise ValueError('先停止目标API/worker并指定--offline-maintenance')
        output=Path(args.output_data).resolve();(output/'work').mkdir(parents=True,exist_ok=True)
        with (output/'work/worker.lock').open('a+b') as lock:
            try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('目标worker仍在运行，拒绝转换登记') from None
            execute()
    else:execute()
