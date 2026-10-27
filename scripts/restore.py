#!/usr/bin/env python3
"""Restore a checksummed backup into a new data root; never merge or overwrite."""
from __future__ import annotations
import argparse,json,os,shutil,sqlite3,tempfile,time,uuid
from pathlib import Path
from backup import sha,safe_path,connect_sqlite,check_db,finder_metadata,dragon_tiger_files

def verify_backup(bundle):
    bundle=Path(bundle).expanduser().resolve()
    if not bundle.is_dir() or (bundle/'backup-manifest.json').is_symlink():raise ValueError('Backup bundle is missing or invalid')
    manifest=json.loads((bundle/'backup-manifest.json').read_text())
    if manifest.get('format')!='shixu-backup-v1' or manifest.get('master_key_included') is not False:raise ValueError('Unsupported/unsafe backup format')
    expected=set()
    for item in manifest['files']:
        key=item['path']
        if Path(key).name=='.DS_Store':raise ValueError('Finder metadata is listed in the manifest; create a fresh backup')
        path=safe_path(bundle,key)
        if key in expected:raise ValueError('Duplicate manifest file')
        if not path.is_file() or path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:raise ValueError('Backup checksum mismatch: '+key)
        if key not in ('business/app.sqlite3',) and not key.startswith(('published/','credentials/')):raise ValueError('Unexpected file category')
        if key.startswith('credentials/') and not (len(Path(key).parts)==2 and key.endswith('.enc')):raise ValueError('Only ciphertext files can be restored')
        expected.add(key)
    actual=set()
    for p in bundle.rglob('*'):
        if p.is_symlink():raise ValueError('Symlink in backup bundle')
        if finder_metadata(p):continue
        if p.is_file() and p!=bundle/'backup-manifest.json':actual.add(str(p.relative_to(bundle)))
    if actual!=expected or 'business/app.sqlite3' not in expected:raise ValueError('Backup inventory differs from manifest')
    dragon_tiger=dragon_tiger_files(bundle/'published')
    declared_dragon={item['path'].removeprefix('published/'):item['sha256'] for item in manifest['files'] if item['path'].startswith('published/dragon-tiger/')}
    if dragon_tiger!=declared_dragon:raise ValueError('Dragon-tiger backup dependency closure differs from manifest')
    c=connect_sqlite(bundle/'business/app.sqlite3','ro')
    try:check_db(c)
    finally:c.close()
    return manifest

def perform_restore(bundle,target):
    bundle=Path(bundle).expanduser().resolve();target=Path(target).expanduser().resolve()
    if target.exists():raise ValueError('Restore target must not exist; choose a completely new data root')
    if target.is_relative_to(bundle) or bundle.is_relative_to(target):raise ValueError('Restore target and backup bundle must be independent')
    manifest=verify_backup(bundle);target.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.shixu-restore-',dir=target.parent));stage.chmod(0o700)
    try:
        for item in manifest['files']:
            src=safe_path(bundle,item['path']);dst=safe_path(stage,item['path']);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
            if sha(dst)!=item['sha256']:raise ValueError('Restore copy verification failed')
            if item['path'].startswith(('business/','credentials/')):dst.chmod(0o600)
        c=connect_sqlite(stage/'business/app.sqlite3')
        try:
            now=time.time_ns()//1000000;c.execute('BEGIN IMMEDIATE')
            interrupted=[r[0] for r in c.execute("SELECT job_id FROM job WHERE status IN ('running','queued')")]
            c.execute("UPDATE job SET status='paused',phase='restored_waiting_for_manual_resume',lease_owner=NULL,lease_expires_at_ms=NULL WHERE status IN ('running','queued')")
            c.execute('UPDATE job SET lease_owner=NULL,lease_expires_at_ms=NULL')
            c.execute("UPDATE sync_batch SET status='cancelled' WHERE status='running'")
            c.execute("DELETE FROM retention_pin WHERE owner_kind='request' OR (owner_kind='backup' AND owner_id=?)",(manifest['backup_id'],))
            for job_id in interrupted:
                seq=c.execute('SELECT coalesce(max(seq),0)+1 FROM job_event WHERE job_id=?',(job_id,)).fetchone()[0]
                c.execute('INSERT INTO job_event VALUES(?,?,?,?,?,?,?)',(job_id,seq,now,'warning','restored','备份恢复后等待用户手动继续；未复用旧进程租约',json.dumps({'backup_id':manifest['backup_id']})))
            c.commit();check_db(c);c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except BaseException:c.rollback();raise
        finally:c.close()
        for folder in ('published','credentials','raw','work'): (stage/folder).mkdir(exist_ok=True)
        result={'backup_id':manifest['backup_id'],'restored_at_ms':now,'paused_jobs':interrupted,'request_leases_removed':True,'master_key_restored':False,'credentials_require_original_master_key_or_reconfiguration':bool(manifest.get('credential_ciphertexts'))}
        (stage/'restore-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        os.replace(stage,target)
        return {**result,'target':str(target)}
    finally:
        if stage.exists():shutil.rmtree(stage)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('bundle');parser.add_argument('--target',required=True);args=parser.parse_args()
    print(json.dumps(perform_restore(args.bundle,args.target),ensure_ascii=False,indent=2))
