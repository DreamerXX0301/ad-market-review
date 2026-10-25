#!/usr/bin/env python3
"""Consistent online SQLite + pinned immutable published files + credential ciphertext.

No network, decryption, master-key reads, live DuckDB copies, or recursive data-dir copying.
"""
from __future__ import annotations
import argparse,datetime as dt,hashlib,json,os,shutil,sqlite3,stat,tempfile,time,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as reader:
        for block in iter(lambda:reader.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def finder_metadata(path):
    """Only Finder's ordinary metadata file is outside backup evidence."""
    if path.name!='.DS_Store':return False
    try:return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:return False

def safe_path(root,key):
    relative=Path(key)
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:raise ValueError('Unsafe relative path in backup metadata')
    root=Path(root).resolve();target=root/relative
    if any(p.is_symlink() for p in [target,*target.parents] if p!=root and root in [p,*p.parents]):raise ValueError('Symlinks are not accepted in backup inputs')
    if not target.resolve().is_relative_to(root):raise ValueError('Backup path escapes its root')
    return target

def connect_sqlite(path,mode='rw'):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode='+mode,uri=True,timeout=10)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA recursive_triggers=ON');c.execute('PRAGMA busy_timeout=10000')
    return c

def check_db(c):
    if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity_check failed')
    if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('SQLite foreign_key_check failed')

def retained_snapshots(c):
    return [dict(r) for r in c.execute("SELECT s.* FROM dataset_snapshot s LEFT JOIN snapshot_gc g USING(snapshot_id) WHERE s.state='published' AND (g.state IS NULL OR g.state='available')")]

def reject_incomplete_collection(c):
    """A partial unlink ledger is not a self-contained recoverable backup."""
    if c.execute("SELECT 1 FROM snapshot_gc WHERE state='gc_pending' LIMIT 1").fetchone():raise ValueError('Cannot back up incomplete GC; finish or resume its exact maintenance ledger first')
    table=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_storage_collection'").fetchone()
    if table and c.execute("SELECT 1 FROM market_storage_collection WHERE status<>'completed' LIMIT 1").fetchone():raise ValueError('Cannot back up incomplete market storage collection; finish its exact maintenance ledger first')


def make_pins(c,snapshots,owner):
    now=time.time_ns()//1000000
    c.execute('BEGIN IMMEDIATE')
    try:
        for r in snapshots:
            c.execute("INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms) VALUES(?,?,'backup',?,?) ON CONFLICT(snapshot_id,owner_kind,owner_id) DO NOTHING",('backup_pin_'+uuid.uuid4().hex,r['snapshot_id'],owner,now))
        c.commit()
    except BaseException:c.rollback();raise

def supplement_files(published):
    """Freeze and validate the optional public supplement tree before copying.

    Its head and accepted-day pointers are mutable, unlike their content objects.
    A full second inventory after copying rejects concurrent publication, cache
    replacement, missing objects, or additions instead of blessing a mixed tree.
    Only the explicit public JSON namespaces are eligible; arbitrary files and
    temporary writer files are never included.
    """
    root=safe_path(published,'market-supplement')
    if not root.exists():return {}
    if not root.is_dir():raise ValueError('Public supplement root is not a directory')
    required={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('Symlink in public supplement')
        if not path.is_file():continue
        relative=path.relative_to(root)
        if path.suffix!='.json':continue
        valid=relative.as_posix()=='head.json' or (len(relative.parts)>1 and relative.parts[0] in ('indexes','artifacts','raw','accepted'))
        if not valid:raise ValueError('Unexpected public supplement JSON namespace')
        required['market-supplement/'+relative.as_posix()]=sha(path)
    def document(path):
        key='market-supplement/'+str(path.relative_to(root))
        if key not in required or not path.is_file() or sha(path)!=required[key]:raise ValueError('Public supplement changed while taking its inventory')
        try:value=json.loads(path.read_text())
        except (OSError,ValueError) as error:raise ValueError('Invalid public supplement JSON: '+key) from error
        if sha(path)!=required[key]:raise ValueError('Public supplement changed while reading metadata')
        return value
    def reference(pointer,field,hash_field,category):
        relative=pointer.get(field);expected=pointer.get(hash_field)
        if not isinstance(relative,str) or not isinstance(expected,str) or len(expected)!=64:raise ValueError('Public supplement pointer lacks an object checksum')
        path=safe_path(root,relative)
        if Path(relative).parts[0]!=category or path.suffix!='.json':raise ValueError('Unexpected public supplement reference category')
        if required.get('market-supplement/'+relative)!=expected:raise ValueError('Public supplement referenced object is missing or has a checksum mismatch')
        return path
    def raw_reference(record):
        if not isinstance(record,dict):raise ValueError('Invalid public supplement record')
        if 'raw_artifact' in record or 'raw_sha256' in record:reference(record,'raw_artifact','raw_sha256','raw')
    for key in required:
        relative=Path(key).relative_to('market-supplement')
        if relative.parts[0]=='accepted':
            pointer=document(root/relative)
            if not isinstance(pointer,dict) or pointer.get('version')!='market-supplement-v1':raise ValueError('Invalid public supplement accepted pointer')
            raw_reference(document(reference(pointer,'artifact','sha256','artifacts')))
        elif relative.parts[0]=='indexes':
            index=document(root/relative)
            if not isinstance(index,dict) or index.get('version')!='market-supplement-v1':raise ValueError('Invalid public supplement index version')
            storage_version=index.get('storage_version')
            if storage_version not in (None,'supplement-index-v2'):raise ValueError('Invalid public supplement index storage version')
            for field in ('public_pools','annual_pools','annual_down_pools','fund_flows','sector_index_history'):
                records=index.get(field,{})
                if not isinstance(records,dict):raise ValueError('Invalid public supplement index records')
                for record in records.values():
                    if storage_version=='supplement-index-v2' and isinstance(record,dict):
                        if set(record)!={'record_ref','sha256'}:raise ValueError('Invalid compact supplement record reference')
                        record=document(reference(record,'record_ref','sha256','artifacts'))
                        payloads=record.get('__supplement_fields_v1',{})
                        if not isinstance(payloads,dict) or len(payloads)>64:raise ValueError('Invalid compact supplement field references')
                        for field_name,pointer in payloads.items():
                            if field_name in record or not isinstance(pointer,dict) or set(pointer)!={'record_ref','sha256'}:raise ValueError('Invalid compact supplement field pointer')
                            payload=document(reference(pointer,'record_ref','sha256','artifacts'))
                            if not isinstance(payload,dict) or set(payload)!={'__supplement_payload_v1'}:raise ValueError('Invalid compact supplement payload')
                    raw_reference(record)
    if 'market-supplement/head.json' in required:
        head=document(root/'head.json')
        if not isinstance(head,dict) or head.get('version')!='market-supplement-v1':raise ValueError('Invalid public supplement head')
        index=document(reference(head,'artifact','sha256','indexes'))
        if not head.get('snapshot_id') or head['snapshot_id']!=index.get('snapshot_id'):raise ValueError('Public supplement head snapshot differs from its index')
    return required

def needed_files(c,published):
    required={};snapshots=retained_snapshots(c)
    for r in snapshots:
        required[r['manifest_path']]=r['manifest_sha256']
        for item in c.execute('SELECT object_key,sha256 FROM snapshot_artifact WHERE snapshot_id=?',(r['snapshot_id'],)):
            if item['object_key'] in required and required[item['object_key']]!=item['sha256']:raise ValueError('Conflicting artifact hashes')
            required[item['object_key']]=item['sha256']
        lineage=json.loads(r['lineage_json']);generation=lineage.get('market_snapshot_id')
        if generation:
            directory=safe_path(published,generation)
            if not directory.is_dir():raise ValueError('Required immutable market generation is missing')
            for f in directory.rglob('*'):
                if f.is_symlink():raise ValueError('Symlink in immutable generation')
                if f.is_file():required.setdefault(str(f.relative_to(published)),None)
            manifest=json.loads(safe_path(directory,'manifest.json').read_text())
            for item in manifest.get('summary',{}).get('files',[]):
                key=Path(item['path'])
                if key.parts and key.parts[0]=='published':key=Path(*key.parts[1:])
                if str(key) in required and required[str(key)] not in (None,item['sha256']):raise ValueError('Conflicting market object hashes')
                required[str(key)]=item['sha256']
    for key,expected in supplement_files(published).items():
        if key in required and required[key] not in (None,expected):raise ValueError('Conflicting public supplement artifact hashes')
        required[key]=expected
    return required,snapshots

def perform_backup(data_dir,output,sqlite_path=None,published_dir=None,credential_dir=None):
    # The API and sole collector use this same lock on the shared business
    # volume. Hold a reader lock through the supplement inventory/copy so GC
    # cannot unlink an object after it has been captured by this backup.
    import fcntl
    import os
    database=Path(sqlite_path or os.environ.get('SHIXU_SQLITE_PATH') or Path(data_dir)/'business/app.sqlite3').expanduser().absolute()
    parent=database.parent
    if not database.is_file() or parent.is_symlink():raise ValueError('Source SQLite database does not exist or has an unsafe parent')
    descriptor=os.open(parent/'supplement-access.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(descriptor,fcntl.LOCK_SH)
        return _perform_backup(data_dir,output,sqlite_path,published_dir,credential_dir)
    finally:
        fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)


def _perform_backup(data_dir,output,sqlite_path=None,published_dir=None,credential_dir=None):
    data_dir=Path(data_dir).expanduser().resolve();output=Path(output).expanduser().resolve()
    if output.exists():raise ValueError('Backup output must be a new path')
    if output.is_relative_to(data_dir):raise ValueError('Backup output cannot be inside live data')
    db=Path(sqlite_path or os.environ.get('SHIXU_SQLITE_PATH') or data_dir/'business/app.sqlite3').expanduser().resolve()
    published=Path(published_dir or os.environ.get('SHIXU_PUBLISHED_DIR') or data_dir/'published').expanduser().resolve()
    credentials=Path(credential_dir or os.environ.get('SHIXU_CREDENTIAL_DIR') or data_dir/'credentials').expanduser().resolve()
    if not db.is_file():raise ValueError('Source SQLite database does not exist')
    if db.is_symlink() or published.is_symlink() or credentials.is_symlink():raise ValueError('Symlink storage roots are not accepted')
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.shixu-backup-',dir=output.parent));stage.chmod(0o700)
    owner='backup-'+uuid.uuid4().hex;source=connect_sqlite(db)
    try:
        reject_incomplete_collection(source)
        make_pins(source,retained_snapshots(source),owner)
        (stage/'business').mkdir()
        dest=sqlite3.connect(stage/'business/app.sqlite3')
        try:source.backup(dest,pages=512,sleep=.05);check_db(dest)
        finally:dest.close()
        copied=connect_sqlite(stage/'business/app.sqlite3','ro')
        try:
            reject_incomplete_collection(copied)
            required,snapshots=needed_files(copied,published)
            # A publication could have committed between initial pins and the online snapshot.
            # Pin its exact captured dependencies before copying; unavailable files make backup fail.
            make_pins(source,snapshots,owner)
            heads=[dict(r) for r in copied.execute('SELECT * FROM published_head ORDER BY module,scope_key')]
            latest=copied.execute("SELECT s.lineage_json FROM published_head h JOIN dataset_snapshot s USING(snapshot_id) WHERE h.module='daily_raw' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
            for key,expected in sorted(required.items()):
                src=safe_path(published,key)
                if not src.is_file():raise ValueError('Required published object is missing: '+key)
                before=sha(src)
                if expected and before!=expected:raise ValueError('Published object hash differs from SQLite manifest: '+key)
                dst=safe_path(stage/'published',key);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
                if sha(dst)!=before or sha(src)!=before:raise ValueError('Published object changed during backup: '+key)
            supplement={key:value for key,value in required.items() if key.startswith('market-supplement/')}
            if supplement_files(published)!=supplement:raise ValueError('Public supplement changed during backup; wait for manual supplement sync to finish and retry')
            if latest:
                generation=json.loads(latest[0]).get('market_snapshot_id')
                if generation:
                    frozen=json.loads(safe_path(stage/'published',generation+'/manifest.json').read_text())['summary']
                    (stage/'published/market-head.json').write_text(json.dumps(frozen,ensure_ascii=False))
        finally:copied.close()
        # A read-only connection to a WAL-mode backup can leave empty -wal/-shm sidecars.
        # Materialize a standalone checkpointed database; runtime re-enables WAL on start.
        standalone=sqlite3.connect(stage/'business/app.sqlite3')
        try:
            standalone.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            standalone.execute('PRAGMA journal_mode=DELETE')
        finally:standalone.close()
        # Only ciphertext .enc files are eligible. Master keys are never opened or copied.
        credential_files=[]
        if credentials.exists():
            explicit_master=os.environ.get('SHIXU_CREDENTIAL_MASTER_KEY_FILE') or os.environ.get('SHIXU_MASTER_KEY_FILE')
            master_path=Path(explicit_master).expanduser().resolve() if explicit_master else Path.home()/'.local/share/shixu/credential_master_key'
            for src in sorted(credentials.glob('*.enc')):
                if src.is_symlink() or not src.is_file():raise ValueError('Invalid credential ciphertext file')
                if src.resolve()==master_path:raise ValueError('The master key must be outside credential ciphertext files')
                expected=sha(src);dst=stage/'credentials'/src.name;dst.parent.mkdir(exist_ok=True);shutil.copyfile(src,dst);dst.chmod(0o600)
                if sha(src)!=expected or sha(dst)!=expected:raise ValueError('Credentials changed during backup; retry with credential settings idle')
                credential_files.append(src.name)
        files=[]
        for path in sorted(stage.rglob('*')):
            if path.is_symlink():raise ValueError('Symlink in backup staging directory')
            if finder_metadata(path):continue
            if path.is_file():files.append({'path':str(path.relative_to(stage)),'sha256':sha(path),'bytes':path.stat().st_size})
        manifest={'format':'shixu-backup-v1','backup_id':owner,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'sqlite_version':sqlite3.sqlite_version,'heads':heads,'retained_snapshot_count':len(snapshots),'credential_ciphertexts':credential_files,'master_key_included':False,'files':files,'notes':['Online SQLite backup; immutable file references pinned while copying','Optional public market-supplement JSON objects and mutable pointers copied only after checksum and stable-inventory verification','Bulk raw downloads and active DuckDB work files intentionally excluded','Do not change provider credentials during a backup; ciphertext storage is not cross-store versioned']}
        (stage/'backup-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        for f in stage.rglob('*'):
            if f.is_symlink():raise ValueError('Symlink in backup staging directory')
            if finder_metadata(f):continue
            if f.is_file():
                with f.open('rb') as reader:os.fsync(reader.fileno())
        os.replace(stage,output)
        return {'backup_id':owner,'output':str(output),'files':len(files),'bytes':sum(x['bytes'] for x in files),'snapshot_count':len(snapshots),'master_key_included':False}
    finally:
        try:source.execute("DELETE FROM retention_pin WHERE owner_kind='backup' AND owner_id=?",(owner,));source.commit()
        finally:source.close()
        if stage.exists():shutil.rmtree(stage)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-dir',default=os.environ.get('SHIXU_DATA_DIR',str(ROOT/'data')));parser.add_argument('--output',required=True);parser.add_argument('--sqlite-path');parser.add_argument('--published-dir');parser.add_argument('--credential-dir');args=parser.parse_args()
    print(json.dumps(perform_backup(args.data_dir,args.output,args.sqlite_path,args.published_dir,args.credential_dir),ensure_ascii=False,indent=2))
