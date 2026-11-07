"""Conservative generation/object GC for an explicit offline maintenance window.

No directory age is a deletion authorization. The frozen plan contains exact
files/SHA, roots include all persisted business references, and shared objects
survive while any available generation uses them. Ordinary application startup
and GETs never execute this operation.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
import stat

from app.analytics.market_storage import OBJECT_ROOT, safe_object
from app.infrastructure.file_operations import flush_directory, path_is_link_or_reparse
from app.core import digest, dumps, hash_file, new_id, now_ms, raw_dir, work_dir

VERSION='market-storage-gc-v1'
_GENERATION=re.compile(r'market-[0-9a-f]{24}\Z')
_IGNORED={'_schema_migration','dataset_snapshot','snapshot_artifact','snapshot_coverage','retention_pin','published_head','snapshot_gc',
          'job_event','sync_batch','market_storage_collection','market_storage_collection_file','workspace_purge','workspace_purge_file','workspace_purge_audit',
          'supplement_retention_run','supplement_retention_file'}
_NOON_CLOSE_PROVENANCE = {'market_noon_close_review','market_noon_close_item','market_noon_close_attempt'}


def _strings(value):
    if isinstance(value,str):
        yield value
        if value[:1] in ('[','{'):
            try:yield from _strings(json.loads(value))
            except ValueError:pass
    elif isinstance(value,dict):
        for item in value.values():yield from _strings(item)
    elif isinstance(value,list):
        for item in value:yield from _strings(item)


def _roots(connection,snapshots,now):
    """Schema-aware conservative roots; unknown business JSON protects, not drops."""
    identifiers={row['snapshot_id'] for row in snapshots};generations={json.loads(row['lineage_json']).get('market_snapshot_id') for row in snapshots}
    roots={row[0] for row in connection.execute('SELECT snapshot_id FROM published_head')}
    roots.update(row[0] for row in connection.execute('SELECT snapshot_id FROM retention_pin WHERE expires_at_ms IS NULL OR expires_at_ms>?',(now,)))
    # Close-review inputs are independently retained inside SQLite. Their old
    # generation identities are provenance, not a request to retain all files.
    from app.services.noon_close_integrity import validate_close_reviews
    validate_close_reviews(connection)
    # Metadata of completed sync jobs remains audit history. Only recoverable
    # task parameters/results have a live file dependency; reports and all saved
    # strategy/selection/tracking/watchlist/annotation rows are scanned below.
    for table, in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if table.startswith('sqlite_') or table in _IGNORED or table in _NOON_CLOSE_PROVENANCE:continue
        columns=[row[1] for row in connection.execute('PRAGMA table_info("'+table.replace('"','""')+'")')]
        fields=[value for value in columns if 'snapshot' in value or value.endswith('_json')]
        if not fields:continue
        quoted=','.join('"'+value.replace('"','""')+'"' for value in fields)
        where=" WHERE status IN ('queued','running','paused')" if table=='job' else ''
        for row in connection.execute('SELECT '+quoted+' FROM "'+table.replace('"','""')+'"'+where):
            for value in row:
                for item in _strings(value):
                    if item in identifiers or item in generations:roots.add(item)
    # Frozen recovery plans live outside SQLite. Read only exact paused job
    # checkpoints; missing/invalid evidence protects every generation rather
    # than assuming a paused task no longer needs its original inputs.
    for job in connection.execute("SELECT job_id,kind FROM job WHERE status='paused' AND kind IN ('initialise','increment','topic_init','topic_refresh')"):
        path=(raw_dir()/'sync'/job['job_id']/'checkpoint.json' if job['kind'] in ('initialise','increment') else work_dir()/job['job_id']/'topic-plan.json')
        try:
            if path_is_link_or_reparse(path) or path.stat().st_size>16*1024*1024:raise ValueError('unsafe recovery checkpoint')
            document=json.loads(path.read_text())
            roots.update(value for value in _strings(document.get('plan',{}) if job['kind'] in ('initialise','increment') else document) if value in identifiers or value in generations)
        except (OSError,ValueError,TypeError):roots.update(value for value in generations if isinstance(value,str))
    return roots


def _closure(snapshots,roots):
    by_id={row['snapshot_id']:row for row in snapshots};groups=defaultdict(list);lineages={}
    for row in snapshots:
        lineage=json.loads(row['lineage_json']);lineages[row['snapshot_id']]=lineage
        generation=lineage.get('market_snapshot_id')
        if isinstance(generation,str) and _GENERATION.fullmatch(generation):groups[generation].append(row['snapshot_id'])
    protected=set();pending=list(roots)
    while pending:
        key=pending.pop()
        if key in protected:continue
        protected.add(key)
        if key in groups:pending.extend(groups[key])
        if key in by_id:
            lineage=lineages[key];generation=lineage.get('market_snapshot_id')
            if generation:pending.append(generation)
            pending.extend(value for value in _strings(lineage.get('input_snapshots',{})) if value in by_id or value in groups)
    return protected


def preview(connection,published,*,now=None,keep_latest=2):
    now=now or now_ms();published=Path(published).resolve()
    snapshots=[dict(row) for row in connection.execute("SELECT s.* FROM dataset_snapshot s LEFT JOIN snapshot_gc g USING(snapshot_id) WHERE s.state='published' AND (g.state IS NULL OR g.state='available')")]
    by_id={row['snapshot_id']:row for row in snapshots};groups=defaultdict(list);lineages={}
    for row in snapshots:
        lineage=json.loads(row['lineage_json']);lineages[row['snapshot_id']]=lineage
        generation=lineage.get('market_snapshot_id')
        if isinstance(generation,str) and _GENERATION.fullmatch(generation):groups[generation].append(row['snapshot_id'])
    roots=_roots(connection,snapshots,now)
    # Preserve latest generations even without business pins, including a
    # previous published rollback view. The filesystem head protects an import
    # that completed before SQLite module registration.
    head=published/'market-head.json'
    if head.is_file():roots.add(json.loads(head.read_text()).get('snapshot_id'))
    latest=sorted(groups,key=lambda key:max(by_id[value]['published_at_ms'] or 0 for value in groups[key]),reverse=True)
    roots.update(latest[:max(1,keep_latest)])
    protected=_closure(snapshots,roots)
    candidates=sorted(key for key in groups if key not in protected)
    removed_ids={value for key in candidates for value in groups[key]}
    preserved_files=set()
    for row in snapshots:
        if row['snapshot_id'] not in removed_ids:
            preserved_files.add(row['manifest_path'])
            preserved_files.update(value[0] for value in connection.execute('SELECT object_key FROM snapshot_artifact WHERE snapshot_id=?',(row['snapshot_id'],)))
    # Generation manifests are the complete transitive CAS closure even if a
    # legacy importer did not register auxiliary module artifacts individually.
    for generation in groups:
        if generation in candidates:continue
        path=safe_object(published,generation+'/manifest.json')
        if not path.is_file():raise ValueError('保留代际缺少manifest，拒绝生成清理计划')
        for file in json.loads(path.read_text()).get('summary',{}).get('files',[]):
            key=file['path'].removeprefix('published/');preserved_files.add(key)
    files={};removed_snapshots=[]
    def add(key,expected=None,size=None):
        if key in preserved_files:return
        path=safe_object(published,key)
        if not path.is_file() or not stat.S_ISREG(path.lstat().st_mode):raise ValueError('清理候选文件缺失或类型不可核验：'+key)
        actual=hash_file(path);actual_size=path.stat().st_size
        if (expected and actual!=expected) or (size is not None and actual_size!=size):raise ValueError('清理候选文件SHA或大小不符：'+key)
        prior=files.get(key);item={'object_key':key,'sha256':actual,'byte_count':actual_size}
        if prior is not None and prior!=item:raise ValueError('共享清理对象元数据冲突')
        files[key]=item
    for generation in candidates:
        folder=safe_object(published,generation);manifest=folder/'manifest.json'
        document=json.loads(manifest.read_text())
        expected={file['path'].removeprefix('published/'):file for file in document.get('summary',{}).get('files',[])}
        actual=set()
        for path in folder.rglob('*'):
            if path_is_link_or_reparse(path):raise ValueError('候选代际包含符号链接')
            if path.is_file():actual.add(path.relative_to(published).as_posix())
        allowed={key for key in expected if Path(key).parts[0]==generation}|{generation+'/manifest.json'}
        if actual!=allowed:raise ValueError('候选代际含未登记文件，拒绝清理')
        add(generation+'/manifest.json')
        for key,item in expected.items():add(key,item['sha256'],item['bytes'])
        for sid in groups[generation]:
            row=by_id[sid];add(row['manifest_path'],row['manifest_sha256'])
            for item in connection.execute('SELECT * FROM snapshot_artifact WHERE snapshot_id=?',(sid,)):add(item['object_key'],item['sha256'],item['byte_count'])
            removed_snapshots.append(sid)
    scope={'version':VERSION,'generations':candidates,'snapshot_ids':sorted(removed_snapshots),'files':sorted(files.values(),key=lambda row:row['object_key']),
           'protected_generations':sorted(key for key in groups if key not in candidates),'keep_latest':max(1,keep_latest),'maintenance_window_required':True}
    scope['logical_bytes']=sum(row['byte_count'] for row in scope['files'])
    return {'scope':scope,'scope_sha256':digest(scope),'candidate_generations':len(candidates),'candidate_files':len(files),'logical_bytes':scope['logical_bytes'],
            'protected_generations':len(groups)-len(candidates),'note':'逐路径逻辑大小，不代表文件系统可释放物理空间；旧指标种子和全部业务引用保留'}


def collect(connection,published,plan,*,offline=False,collection_id=None):
    """Caller owns the sole worker lock AND has stopped API/worker before entry."""
    if not offline:raise ValueError('仅允许停止API和worker后的离线维护窗口执行')
    published=Path(published).resolve();scope=plan.get('scope',{})
    if digest(scope)!=plan.get('scope_sha256') or scope.get('version')!=VERSION:raise ValueError('清理计划校验和无效')
    if connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone():raise ValueError('仍有排队或运行任务，拒绝离线清理')
    if collection_id:
        existing=connection.execute('SELECT * FROM market_storage_collection WHERE collection_id=?',(collection_id,)).fetchone()
        if not existing or existing['scope_sha256']!=plan['scope_sha256']:raise ValueError('恢复清理须使用原冻结清单')
        published_snapshots=[dict(row) for row in connection.execute("SELECT * FROM dataset_snapshot WHERE state='published'")]
        roots=_closure(published_snapshots,_roots(connection,published_snapshots,now_ms()))
        if roots & (set(scope['generations'])|set(scope['snapshot_ids'])):raise ValueError('清理恢复发现新的业务引用，拒绝继续原删除范围')
        if existing['status']=='completed':return {'collection_id':collection_id,'status':'completed','already_completed':True}
    else:
        current=preview(connection,published,keep_latest=scope.get('keep_latest',2))
        if current['scope_sha256']!=plan['scope_sha256']:raise ValueError('引用或文件发生变化，须重新预览清理计划')
        collection_id=new_id('market-gc')
        connection.execute('BEGIN IMMEDIATE')
        try:
            connection.execute('INSERT INTO market_storage_collection VALUES(?,?,?,?,?,?,NULL)',(collection_id,VERSION,dumps(scope),plan['scope_sha256'],'planned',now_ms()))
            for row in scope['files']:connection.execute('INSERT INTO market_storage_collection_file VALUES(?,?,?,?,?,0)',(collection_id,row['object_key'],row['sha256'],row['byte_count'],'planned'))
            for sid in scope['snapshot_ids']:
                connection.execute("INSERT INTO snapshot_gc VALUES(?,'gc_pending',?,?) ON CONFLICT(snapshot_id) DO UPDATE SET state='gc_pending',changed_at_ms=excluded.changed_at_ms,reason=excluded.reason",(sid,now_ms(),VERSION+':'+collection_id))
            connection.execute("UPDATE market_storage_collection SET status='deleting' WHERE collection_id=?",(collection_id,));connection.commit()
        except BaseException:connection.rollback();raise
    for row in connection.execute('SELECT * FROM market_storage_collection_file WHERE collection_id=? ORDER BY object_key',(collection_id,)).fetchall():
        if row['status']!='planned':continue
        path=safe_object(published,row['object_key']);exists=path.exists()
        if exists:
            if not path.is_file() or path.stat().st_size!=row['byte_count'] or hash_file(path)!=row['sha256']:raise ValueError('删除前文件大小或SHA变化，停止且保留原清理台账')
            path.unlink()
            flush_directory(path.parent)
        connection.execute("UPDATE market_storage_collection_file SET status=?,removed_bytes=? WHERE collection_id=? AND object_key=?",('removed' if exists else 'absent',row['byte_count'] if exists else 0,collection_id,row['object_key']));connection.commit()
    connection.execute('BEGIN IMMEDIATE')
    try:
        for sid in scope['snapshot_ids']:connection.execute("UPDATE snapshot_gc SET state='purged',changed_at_ms=? WHERE snapshot_id=? AND state='gc_pending'",(now_ms(),sid))
        connection.execute("UPDATE market_storage_collection SET status='completed',completed_at_ms=? WHERE collection_id=?",(now_ms(),collection_id));connection.commit()
    except BaseException:connection.rollback();raise
    for generation in scope['generations']:
        folder=safe_object(published,generation)
        if folder.is_dir() and not any(folder.iterdir()):folder.rmdir()
    return {'collection_id':collection_id,'status':'completed','files':len(scope['files']),'logical_bytes':scope['logical_bytes']}


def authorized_removed_files(connection):
    """Exact sealed GC records accepted by the generic publication validator."""
    result={}
    try:collections=connection.execute("SELECT * FROM market_storage_collection WHERE status='completed'").fetchall()
    except __import__('sqlite3').OperationalError:return result
    snapshots=[dict(row) for row in connection.execute("SELECT * FROM dataset_snapshot WHERE state='published'")]
    live_roots=_closure(snapshots,_roots(connection,snapshots,now_ms()))
    for row in collections:
        try:
            scope=json.loads(row['scope_json'])
            if row['version']!=VERSION or digest(scope)!=row['scope_sha256'] or live_roots & (set(scope['generations'])|set(scope['snapshot_ids'])):continue
            ledger={file['object_key']:dict(file) for file in connection.execute('SELECT * FROM market_storage_collection_file WHERE collection_id=?',(row['collection_id'],))}
            expected={file['object_key']:file for file in scope['files']}
            if set(ledger)!=set(expected):continue
            if any(file['status'] not in ('removed','absent') or file['sha256']!=expected[key]['sha256'] or file['byte_count']!=expected[key]['byte_count'] or file['removed_bytes']!=(file['byte_count'] if file['status']=='removed' else 0) for key,file in ledger.items()):continue
            for sid in scope['snapshot_ids']:
                marker=connection.execute('SELECT * FROM snapshot_gc WHERE snapshot_id=?',(sid,)).fetchone()
                snapshot=connection.execute('SELECT * FROM dataset_snapshot WHERE snapshot_id=?',(sid,)).fetchone()
                if not marker or marker['state']!='purged' or marker['reason']!=VERSION+':'+row['collection_id'] or not snapshot:continue
                if connection.execute('SELECT 1 FROM published_head WHERE snapshot_id=?',(sid,)).fetchone():continue
                files={file['object_key']:(file['sha256'],file['byte_count']) for file in connection.execute('SELECT * FROM snapshot_artifact WHERE snapshot_id=?',(sid,))}
                files[snapshot['manifest_path']]=(snapshot['manifest_sha256'],None)
                for key,(sha,size) in files.items():
                    file=ledger.get(key)
                    if file and file['sha256']==sha and (size is None or file['byte_count']==size):result[(sid,key)]={'snapshot_id':sid,'object_key':key,'sha256':sha,'byte_count':file['byte_count'],'collection_id':row['collection_id'],'status':file['status']}
        except (ValueError,KeyError,TypeError):continue
    return result
