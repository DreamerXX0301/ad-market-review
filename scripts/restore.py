#!/usr/bin/env python3
"""Restore a checksummed backup into a new data root; never merge or overwrite."""
from __future__ import annotations
import argparse,json,os,re,shutil,sqlite3,tempfile,time,uuid
from pathlib import Path
from backup import sha,safe_path,connect_sqlite,check_db,finder_metadata,dragon_tiger_files

def disable_automatic_review(connection, now):
    """A restored copy must not inherit permission to create new daily jobs.

    Keep the original times, receipts and calendar evidence. Advancing only the
    configuration revision also invalidates any pre-restore submission claim.
    This standalone archive tool must not import/initialize the application.
    """
    key='automatic-review-config-v1'
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_setting'").fetchone():return False
    row=connection.execute('SELECT value_json FROM platform_setting WHERE key=?',(key,)).fetchone()
    if row is None:return False
    try:config=json.loads(row[0])
    except (TypeError,ValueError) as error:raise ValueError('Invalid automatic review configuration in backup') from error
    if not isinstance(config,dict) or type(config.get('revision')) is not int or config['revision']<0:
        raise ValueError('Invalid automatic review revision in backup')
    for kind in ('noon','after_close'):
        value=config.get(kind)
        if not isinstance(value,dict) or set(value)!={'enabled','time'} or type(value.get('enabled')) is not bool:
            raise ValueError('Invalid automatic review switches in backup')
        clock=value['time']
        if (not isinstance(clock,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',clock)
                or kind=='noon' and not '11:31'<=clock<'13:00' or kind=='after_close' and clock<'17:00'):
            raise ValueError('Invalid automatic review time in backup')
        value['enabled']=False
    config['revision']+=1
    connection.execute('UPDATE platform_setting SET value_json=?,updated_at_ms=? WHERE key=?',
                       (json.dumps(config,ensure_ascii=False,sort_keys=True,separators=(',',':')),now,key))
    return True

def disable_ai(connection, now):
    """Restore local history, never permission to send another model request.

    This archive tool deliberately does not import the application or decrypt
    credentials. Actual usage and conservative uncertain-charge reservations
    survive restore; neither pending nor previously sent work can be replayed.
    """
    tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result={'disabled':False,'interrupted_tasks':[],'cancelled_tasks':[]}
    if 'ai_provider_config' not in tables:
        return result
    for row in connection.execute('SELECT provider_id,verification_json FROM ai_provider_config'):
        try:verification=json.loads(row['verification_json'])
        except (TypeError,ValueError) as error:raise ValueError('Invalid AI verification in backup') from error
        if not isinstance(verification,dict):raise ValueError('Invalid AI verification in backup')
        verification.update(state='stale',reason_code='restored_requires_verification')
        connection.execute('UPDATE ai_provider_config SET enabled=0,config_revision=config_revision+1,verification_json=?,updated_at_ms=? WHERE provider_id=?',
                           (json.dumps(verification,ensure_ascii=False,sort_keys=True,separators=(',',':')),now,row['provider_id']))
    result['disabled']=True
    if 'ai_task' not in tables:
        return result
    result['interrupted_tasks']=[row[0] for row in connection.execute("SELECT task_id FROM ai_task WHERE state IN ('queued','running') AND sent_at_ms IS NOT NULL")]
    result['cancelled_tasks']=[row[0] for row in connection.execute("SELECT task_id FROM ai_task WHERE state IN ('queued','running') AND sent_at_ms IS NULL")]
    if {'ai_task_event','ai_message'} <= tables:
        # Archive real received text before removing ephemeral stream events.
        # No application imports, provider calls or master-key reads occur.
        for task in connection.execute("SELECT * FROM ai_task WHERE state IN ('queued','running') AND sent_at_ms IS NOT NULL").fetchall():
            metadata=json.loads(task['result_json'] or '{}')
            if not metadata.get('agent') or not task['session_id']:continue
            if connection.execute("SELECT 1 FROM ai_message WHERE task_id=? AND role='assistant'",(task['task_id'],)).fetchone():continue
            request=json.loads(task['request_json'] or '{}');state=request.get('agent_state') or {}
            text=''.join(json.loads(row[0]).get('text','') for row in connection.execute("SELECT data_json FROM ai_task_event WHERE task_id=? AND kind='delta' ORDER BY seq",(task['task_id'],)))
            envelope={'response_type':'agent_answer','text':text or '请求已中断，请核对费用后手动继续提问','partial':True,
                      'source_context':{'page_anchor':request.get('page_anchor',{}),'request_time':request.get('request_time',{}),'tool_results':state.get('tool_results',[])},
                      'tool_traces':state.get('tool_traces',[]),'citations':[],'sources':[],'round_protocol':request.get('round_protocol',{})}
            message_id='ai-message_'+uuid.uuid4().hex
            connection.execute('INSERT INTO ai_message VALUES(?,?,?,?,?,?,?,?)',(message_id,task['session_id'],task['task_id'],'assistant',envelope['text'],json.dumps(envelope,ensure_ascii=False),'interrupted_manual_retry',now))
            metadata['message_id']=message_id
            connection.execute('UPDATE ai_task SET result_json=? WHERE task_id=?',(json.dumps(metadata,ensure_ascii=False),task['task_id']))
        connection.execute('DELETE FROM ai_task_event')
    if 'ai_request_receipt' in tables:
        connection.execute("UPDATE ai_request_receipt SET state='interrupted',finished_at_ms=?,reason_code='interrupted_manual_retry' WHERE state='sent'",(now,))
    connection.execute("""UPDATE ai_task SET state='interrupted_manual_retry',reason_code='interrupted_manual_retry',
        finished_at_ms=?,cancel_requested=1,owner=NULL,request_json='{}',credential_ref='',
        cost_state=CASE WHEN cost_state='estimated' AND cost_nusd IS NOT NULL THEN 'estimated' ELSE 'pending' END
        WHERE state IN ('queued','running') AND sent_at_ms IS NOT NULL""",(now,))
    connection.execute("""UPDATE ai_task SET state='cancelled',reason_code='cancelled',finished_at_ms=?,
        cancel_requested=1,owner=NULL,request_json='{}',credential_ref='',reservation_nusd=0,cost_state='not_sent'
        WHERE state IN ('queued','running') AND sent_at_ms IS NULL""",(now,))
    return result

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
        if key not in ('business/app.sqlite3',) and not key.startswith(('published/','credentials/','raw/noon/')):raise ValueError('Unexpected file category')
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
    try:
        check_db(c)
        from noon_archive import inventory as noon_inventory
        noon=noon_inventory(c,bundle/'raw')
        declared_noon={item['path'].removeprefix('raw/'):item['sha256'] for item in manifest['files'] if item['path'].startswith('raw/noon/')}
        if {key:value['stored_sha256'] for key,value in noon.items()}!=declared_noon:raise ValueError('Noon backup dependency closure differs from manifest')
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
            if item['path'].startswith(('business/','credentials/','raw/noon/')):dst.chmod(0o600)
        c=connect_sqlite(stage/'business/app.sqlite3')
        try:
            now=time.time_ns()//1000000;c.execute('BEGIN IMMEDIATE')
            interrupted=[r[0] for r in c.execute("SELECT job_id FROM job WHERE status IN ('running','queued')")]
            c.execute("UPDATE job SET status='paused',phase='restored_waiting_for_manual_resume',lease_owner=NULL,lease_expires_at_ms=NULL WHERE status IN ('running','queued')")
            c.execute('UPDATE job SET lease_owner=NULL,lease_expires_at_ms=NULL')
            c.execute("UPDATE sync_batch SET status='cancelled' WHERE status='running'")
            c.execute("DELETE FROM retention_pin WHERE owner_kind='request' OR (owner_kind='backup' AND owner_id=?)",(manifest['backup_id'],))
            automatic_review_disabled=disable_automatic_review(c,now)
            ai=disable_ai(c,now)
            automation_disabled=False
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_auction_automation'").fetchone():
                c.execute("UPDATE market_auction_automation SET mode='off',trade_date=NULL,revision=revision+1,updated_at_ms=? WHERE singleton=1",(now,))
                c.execute("""UPDATE market_auction_automation_run SET automatic_control=0,retry_after_at_ms=NULL,
                    state=CASE WHEN state IN ('queued','running','waiting_retry') THEN 'paused' ELSE state END,
                    reason='恢复副本已关闭自动竞价，原日台账保留；需在此副本明确重新开启',updated_at_ms=?""",(now,))
                automation_disabled=True
            for job_id in interrupted:
                seq=c.execute('SELECT coalesce(max(seq),0)+1 FROM job_event WHERE job_id=?',(job_id,)).fetchone()[0]
                c.execute('INSERT INTO job_event VALUES(?,?,?,?,?,?,?)',(job_id,seq,now,'warning','restored','备份恢复后等待用户手动继续；未复用旧进程租约',json.dumps({'backup_id':manifest['backup_id']})))
            c.commit();check_db(c);c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except BaseException:c.rollback();raise
        finally:c.close()
        for folder in ('published','credentials','raw','work'): (stage/folder).mkdir(exist_ok=True)
        result={'backup_id':manifest['backup_id'],'restored_at_ms':now,'paused_jobs':interrupted,'request_leases_removed':True,'master_key_restored':False,'credentials_require_original_master_key_or_reconfiguration':bool(manifest.get('credential_ciphertexts')),'auction_automation_disabled':automation_disabled,'automatic_review_disabled':automatic_review_disabled,'ai_disabled':ai['disabled'],'ai_interrupted_tasks':ai['interrupted_tasks'],'ai_cancelled_tasks':ai['cancelled_tasks']}
        (stage/'restore-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        os.replace(stage,target)
        return {**result,'target':str(target)}
    finally:
        if stage.exists():shutil.rmtree(stage)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('bundle');parser.add_argument('--target',required=True);args=parser.parse_args()
    print(json.dumps(perform_restore(args.bundle,args.target),ensure_ascii=False,indent=2))
