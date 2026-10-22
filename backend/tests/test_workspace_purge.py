"""Permanent cleanup runs only on synthetic temporary roots, never real user data."""
import json
from pathlib import Path
import sqlite3
import pytest
from app import core
from app.services import workspace_purge as purge, task_service as tasks, strategies, workspace_automation
from app.services import workspace_deletion as deletion
from app.analytics import backtest_store as store
from app import worker
from test_workspace_deletion import workspace, rows
from test_workspace import ws, make_strategy, add_one


def path_for(strategy):return '/api/v1/strategies/'+strategy['strategy_id']


def confirm(client,path):
    view=client.get(path+'/purge-preview');assert view.status_code==200,view.text
    preview=view.json();assert preview['can_purge'],preview['blocked_reason']
    response=client.post(path+'/purge',json={k:preview[k] for k in ('confirmation_token','confirmation_text')})
    assert response.status_code==200,response.text
    return response.json(),preview


def execute_one():
    job=tasks.claim_next('purge-fixture-worker');assert job and job['kind']=='cleanup',job
    worker.dispatch(job)
    return job


def watch(symbol,round_id=None):
    with core.connect() as c:
        original=c.execute('SELECT * FROM tracking_round WHERE round_id=?',(round_id,)).fetchone() if round_id else None
        day=c.execute('SELECT business_date FROM business_clock').fetchone()[0]
        c.execute("INSERT INTO watchlist_item(instrument_id,source_kind,source_name,added_business_date,tracking_round_id,tracking_entry_date,event_price_e8,event_basis_json,created_at_ms) VALUES(?,?,?, ?,?,?,?,'{}',?)",(symbol,'tracking' if original else 'stock','合成自选',day,round_id,original['entry_business_date'] if original else None,original['entry_price_e8'] if original else None,core.now_ms()))


def test_real_strategy_cleanup_deletes_owned_rows_and_files_not_market_or_other_strategy(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);other=make_strategy(client)
    run,tracked=add_one(client,strategy);add_one(client,other)
    created,_=factory(strategy,'purge-owned');unrelated,_=factory(other,'purge-unrelated')
    workspace_automation.configure(strategy['strategy_id'],{'refresh_after_sync':True})
    before_job=rows('job');before_snap=rows('dataset_snapshot');before_art=rows('snapshot_artifact')
    original_other={t:[r for r in rows(t) if r.get('strategy_id')==other['strategy_id']] for t in ('strategy','strategy_version','tracking_round','selection_run','backtest_run')}
    files_before={str(f):core.hash_file(f) for f in core.published_dir().rglob('*') if f.is_file()}
    path=path_for(strategy);assert client.delete(path).status_code==200
    received,preview=confirm(client,path)
    assert preview['counts']['tracking_rounds']==1 and preview['counts']['selection_candidates']==2
    assert preview['files']['exclusive_count']==8 and preview['files']['exclusive_bytes']>0
    assert not received['business_deleted'] and received['status']=='queued'
    assert client.post(path+'/restore').status_code==409
    assert client.post('/api/v1/backtests/'+created['backtest_id']+'/restore').status_code==409
    execute_one()
    status=client.get('/api/v1/workspace-purges/'+received['purge_id']).json()
    assert status['status']=='completed' and status['business_deleted']
    assert status['removed_file_count']==8 and status['removed_file_bytes']==preview['files']['exclusive_bytes']
    for table in ('strategy','strategy_version','tracking_policy_version','selection_run','tracking_round','tracking_ignore','backtest_run'):
        assert not [r for r in rows(table) if r.get('strategy_id')==strategy['strategy_id']],table
    assert not [r for r in rows('tracking_event') if r['round_id']==tracked['round_id']]
    assert not [r for r in rows('selection_candidate') if r['run_id']==run['run_id']]
    for table,original in original_other.items():assert [r for r in rows(table) if r.get('strategy_id')==other['strategy_id']]==original
    assert rows('dataset_snapshot')==before_snap and rows('snapshot_artifact')==before_art
    assert all(row in rows('job') for row in before_job)
    assert len(rows('snapshot_gc'))==1 and rows('snapshot_gc')[0]['state']=='purged'
    remaining={str(f):core.hash_file(f) for f in core.published_dir().rglob('*') if f.is_file()}
    assert len(files_before)-len(remaining)==8 and all(files_before[k]==v for k,v in remaining.items())
    assert client.get('/api/v1/backtests/'+unrelated['backtest_id']).status_code==200
    with core.connect() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_purge_preview_is_read_only_requires_trash_and_exact_confirmation(workspace):
    _,client,_=workspace;strategy=make_strategy(client);path=path_for(strategy)
    before={t:rows(t) for t in ('job','workspace_purge','workspace_purge_audit')}
    preview=client.get(path+'/purge-preview').json()
    assert not preview['can_purge'] and not preview['confirmation_token']
    client.delete(path);preview=client.get(path+'/purge-preview').json()
    assert {t:rows(t) for t in before}==before
    assert client.post(path+'/purge',json={'confirmation_token':preview['confirmation_token'],'confirmation_text':'wrong'}).status_code==422
    assert client.post(path+'/purge',json={'confirmation_token':'0.bad','confirmation_text':preview['confirmation_text']}).status_code==409
    with core.connect() as c:c.execute('UPDATE strategy SET name=? WHERE strategy_id=?',('修改后',strategy['strategy_id']))
    assert client.post(path+'/purge',json={k:preview[k] for k in ('confirmation_token','confirmation_text')}).status_code==409
    assert not rows('workspace_purge')


@pytest.mark.parametrize('historical,provenance',[(False,False),(True,False),(False,True),(True,True)])
def test_all_historical_rounds_watchlisted_even_manually_protect_entire_strategy(workspace,historical,provenance):
    _,client,_=workspace;strategy=make_strategy(client);_,tracked=add_one(client,strategy)
    if historical:assert client.post('/api/v1/tracking-rounds/'+tracked['round_id']+'/remove',json={}).status_code==200
    watch(tracked['instrument_id'],tracked['round_id'] if provenance else None)
    path=path_for(strategy);assert client.delete(path).status_code==200
    original={t:rows(t) for t in ('tracking_round','tracking_event','watchlist_item','strategy_version')}
    preview=client.get(path+'/purge-preview').json()
    assert not preview['can_purge'] and preview['protected_watchlist_count']==1 and '自选保护' in preview['blocked_reason']
    assert preview['protected_watchlist'][0]['instrument_id']==tracked['instrument_id']
    assert client.post(path+'/purge',json={'confirmation_token':'0.bad','confirmation_text':strategy['name']}).status_code==409
    assert {t:rows(t) for t in original}==original
    assert client.post(path+'/restore').status_code==200


def test_unmatched_candidate_watchlist_does_not_protect_unrelated_strategy(workspace):
    _,client,_=workspace;strategy=make_strategy(client);add_one(client,strategy)
    watch('000001.SZ') # selection candidate exists, but was never tracked by this strategy
    client.delete(path_for(strategy));_,view=confirm(client,path_for(strategy))
    assert view['protected_watchlist_count']==0
    execute_one();assert len(rows('watchlist_item'))==1


def test_watchlist_added_after_preview_or_after_submit_is_rechecked(workspace):
    _,client,_=workspace;strategy=make_strategy(client);_,tracked=add_one(client,strategy);path=path_for(strategy)
    client.delete(path);view=client.get(path+'/purge-preview').json();watch(tracked['instrument_id'])
    assert client.post(path+'/purge',json={k:view[k] for k in ('confirmation_token','confirmation_text')}).status_code==409
    with core.connect() as c:c.execute('DELETE FROM watchlist_item')
    received,_=confirm(client,path);watch(tracked['instrument_id'])
    original={t:rows(t) for t in ('strategy','tracking_round','tracking_event','selection_candidate')}
    job=tasks.claim_next('fixture')
    with pytest.raises(ValueError,match='自选保护'):purge.execute(job)
    assert purge.get_purge(received['purge_id'])['status']=='failed'
    assert not purge.get_purge(received['purge_id'])['business_deleted']
    assert {t:rows(t) for t in original}==original
    assert client.post('/api/v1/workspace-purges/'+received['purge_id']+'/retry').status_code==409


def test_single_backtest_cleanup_keeps_strategy_tracking_and_old_request_key(workspace,monkeypatch):
    _,client,factory=workspace;strategy=make_strategy(client);add_one(client,strategy)
    created,payload=factory(strategy,'old-purged-request');path='/api/v1/backtests/'+created['backtest_id']
    original={t:rows(t) for t in ('strategy','strategy_version','tracking_round','tracking_event','selection_run')}
    client.delete(path);received,_=confirm(client,path);execute_one()
    assert purge.get_purge(received['purge_id'])['status']=='completed'
    assert {t:rows(t) for t in original}==original
    monkeypatch.setattr(store,'context',lambda *_:pytest.fail('old key rejected before market reads'))
    response=client.post('/api/v1/backtests',json=payload)
    assert response.status_code==409 and '永久删除' in response.text
    with pytest.raises(Exception) as error:tasks.change_task(created['job_id'],'retry')
    assert error.value.status_code==422


def test_shared_result_pin_retains_all_files_head_and_published_metadata(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'shared-result')
    with core.connect() as c:
        sid=c.execute('SELECT result_snapshot_id FROM backtest_run WHERE backtest_id=?',(created['backtest_id'],)).fetchone()[0]
        c.execute("INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms) VALUES('external-pin',?,'manual','user',?)",(sid,core.now_ms()))
    files={str(f):core.hash_file(f) for f in core.published_dir().rglob('*') if f.is_file()}
    path=path_for(strategy);client.delete(path);_,view=confirm(client,path)
    assert view['files']=={'exclusive_count':0,'exclusive_bytes':0,'shared_count':8}
    execute_one();assert not rows('snapshot_gc')
    assert {str(f):core.hash_file(f) for f in core.published_dir().rglob('*') if f.is_file()}==files
    assert any(r['snapshot_id']==sid for r in rows('published_head'))
    assert any(r['pin_id']=='external-pin' for r in rows('retention_pin'))


def test_failed_file_cleanup_retries_original_ledger_without_expanding_scope(workspace,monkeypatch):
    _,client,factory=workspace;strategy=make_strategy(client);factory(strategy,'file-failure');path=path_for(strategy)
    client.delete(path);received,_=confirm(client,path);original=purge._verify_file;failed=[]
    def fail_once(file,*,remove=False):
        if remove and not failed:failed.append(True);raise OSError('synthetic unlink failure')
        return original(file,remove=remove)
    monkeypatch.setattr(purge,'_verify_file',fail_once)
    job=tasks.claim_next('fixture')
    with pytest.raises(OSError,match='synthetic'):purge.execute(job)
    status=purge.get_purge(received['purge_id']);assert status['status']=='failed' and status['business_deleted'] and status['remaining_file_count']==8
    assert client.get(path).status_code==404 and not rows('backtest_run')
    assert client.post('/api/v1/workspace-purges/'+received['purge_id']+'/retry').status_code==200
    execute_one();status=purge.get_purge(received['purge_id'])
    assert status['status']=='completed' and status['remaining_file_count']==0
    assert len(rows('workspace_purge'))==1 and len(rows('workspace_purge_file'))==8
    assert len([r for r in rows('job') if r['kind']=='cleanup'])==1


def test_completed_purge_before_job_finish_recovers_to_terminal(workspace,monkeypatch):
    _,client,_=workspace;strategy=make_strategy(client);path=path_for(strategy);client.delete(path)
    received,_=confirm(client,path);job=tasks.claim_next('first-worker');finish=tasks.finish
    monkeypatch.setattr(tasks,'finish',lambda *args,**kwargs:None)
    purge.execute(job);assert purge.get_purge(received['purge_id'])['status']=='completed'
    monkeypatch.setattr(tasks,'finish',finish)
    with core.connect() as c:c.execute('UPDATE job SET lease_expires_at_ms=1 WHERE job_id=?',(job['job_id'],))
    recovered=tasks.claim_next('recovered-worker');purge.execute(recovered)
    assert tasks.task_view(job['job_id'])['status']=='succeeded'
    assert tasks.task_view(job['job_id'])['lease_owner'] is None


def test_builtin_purge_does_not_bootstrap_or_replay_into_existence(workspace):
    _,client,_=workspace;client.post('/api/v1/strategies/bootstrap');sid='builtin-volume-3x';path='/api/v1/strategies/'+sid
    run=client.post(path+'/runs',json={'request_key':'builtin-old-selection'}).json()
    client.delete(path);confirm(client,path);execute_one()
    assert client.post('/api/v1/strategies/bootstrap').status_code==200
    assert sid not in {r['strategy_id'] for r in rows('strategy')}
    assert client.post(path+'/runs',json={'request_key':'builtin-old-selection'}).status_code==409
    assert client.post('/api/v1/strategy-batches',json={'strategy_ids':[sid]}).status_code in (404,409)
    with pytest.raises(Exception) as error:strategies.create_strategy({'name':'same id','definition':{'field':'close','op':'>','value':1}},strategy_id=sid)
    assert error.value.status_code==409
    assert len([r for r in rows('job') if r['request_key']=='builtin-old-selection'])==1
    assert not [r for r in rows('selection_run') if r['run_id']==run['run_id']]


def test_default_connection_delete_and_update_guards_still_fail_closed(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'immutable-purge')
    with pytest.raises(sqlite3.IntegrityError):
        with core.connect() as c:c.execute('DELETE FROM backtest_run WHERE backtest_id=?',(created['backtest_id'],))
    with pytest.raises(sqlite3.IntegrityError):
        with core.connect() as c:
            c.create_function('workspace_purge_allowed',2,lambda *_:1)
            c.execute("UPDATE backtest_run SET status='failed' WHERE backtest_id=?",(created['backtest_id'],))
    with sqlite3.connect(core.sqlite_path()) as raw:
        with pytest.raises(sqlite3.OperationalError,match='workspace_purge_allowed'):raw.execute('DELETE FROM backtest_run WHERE backtest_id=?',(created['backtest_id'],))


def test_020_changes_only_eight_existing_delete_guards_and_preserves_old_data():
    directory=Path(__file__).parents[1]/'app/migrations';c=sqlite3.connect(':memory:')
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA recursive_triggers=ON')
    for file in sorted(directory.glob('*.sql')):
        if file.name<'020':c.executescript(file.read_text())
    c.execute("INSERT INTO platform_setting VALUES('fixture','{}',1)");c.commit()
    old={r[0]:r[1:] for r in c.execute("SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
    c.executescript((directory/'020_workspace_purge.sql').read_text())
    new={r[0]:r[1:] for r in c.execute("SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
    changed={key for key in old if old[key]!=new[key]}
    assert changed=={'append_only_strategy_version_delete','append_only_tracking_policy_version_delete','append_only_tracking_policy_application_delete','append_only_tracking_event_delete','freeze_selection_run_delete','freeze_selection_candidate_delete','tracking_round_no_delete','completed_backtest_no_delete'}
    assert c.execute('SELECT * FROM platform_setting').fetchall()==[('fixture','{}',1)]
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not c.execute('PRAGMA foreign_key_check').fetchall()


def test_new_confirmation_supersedes_stale_failure_and_restore_invalidates_old_scope(workspace):
    _,client,_=workspace;strategy=make_strategy(client);path=path_for(strategy);client.delete(path)
    first,_=confirm(client,path)
    with core.connect() as c:c.execute('UPDATE strategy SET name=? WHERE strategy_id=?',('新的名称',strategy['strategy_id']))
    job=tasks.claim_next('fixture')
    with pytest.raises(ValueError,match='范围已变化'):purge.execute(job)
    old=purge.get_purge(first['purge_id']);assert not old['can_retry'] and old['can_reconfirm']
    # An explicit restore remains safe after a pre-commit failure, but cannot
    # make the old authorization valid again.
    assert client.post(path+'/restore').status_code==200
    assert client.post('/api/v1/workspace-purges/'+first['purge_id']+'/retry').status_code==409
    client.delete(path);second,_=confirm(client,path)
    old=purge.get_purge(first['purge_id']);assert old['superseded_by']==second['purge_id'] and not old['can_retry']
    assert client.post('/api/v1/workspace-purges/'+first['purge_id']+'/retry').status_code==409
    active=client.get('/api/v1/workspace-purges?status=unfinished&target_kind=strategy&limit=20&offset=0').json()
    assert active['total']==1 and active['items'][0]['purge_id']==second['purge_id']
    execute_one();assert len(rows('workspace_purge_audit'))==5
    assert any(r['stage']=='superseded' and r['purge_id']==first['purge_id'] for r in rows('workspace_purge_audit'))


def test_file_content_symlinks_unknown_files_and_missing_files_are_never_misreported(workspace,monkeypatch):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'unsafe-file')
    path=path_for(strategy);client.delete(path)
    directory=core.published_dir()/'backtests'/created['backtest_id'];report=directory/'report.json'
    original=report.read_bytes();outside=core.data_dir()/'outside.json';outside.write_bytes(original)
    report.unlink();report.symlink_to(outside)
    view=client.get(path+'/purge-preview').json()
    assert not view['can_purge'] and '符号链接' in view['blocked_reason']
    report.unlink();report.write_bytes(original)
    received,_=confirm(client,path)
    # Same-size corruption is caught by worker SHA before the SQL transaction.
    report.write_bytes(b'X'+original[1:]);job=tasks.claim_next('fixture')
    with pytest.raises(ValueError,match='校验和'):purge.execute(job)
    assert not purge.get_purge(received['purge_id'])['business_deleted'] and rows('backtest_run')
    report.write_bytes(original)
    # An unrecorded file is outside the authorization and survives cleanup.
    unknown=directory/'unregistered.txt';unknown.write_text('retain me')
    # Fingerprint is unchanged by unregistered files; retry never expands scope.
    assert client.post('/api/v1/workspace-purges/'+received['purge_id']+'/retry').status_code==200
    execute_one();assert unknown.read_text()=='retain me' and outside.read_bytes()==original


def test_crash_after_unlink_before_ledger_update_counts_absence_without_fabricated_bytes(workspace,monkeypatch):
    _,client,factory=workspace;strategy=make_strategy(client);factory(strategy,'unlink-crash');path=path_for(strategy)
    client.delete(path);received,view=confirm(client,path);original=purge._verify_file;crashed=[]
    def crash_once(file,*,remove=False):
        result=original(file,remove=remove)
        if remove and not crashed:crashed.append((file['object_key'],file['byte_count']));raise OSError('crash after unlink')
        return result
    monkeypatch.setattr(purge,'_verify_file',crash_once);job=tasks.claim_next('fixture')
    with pytest.raises(OSError,match='crash after'):purge.execute(job)
    purge.retry(received['purge_id']);execute_one();done=purge.get_purge(received['purge_id'])
    assert done['status']=='completed' and done['removed_file_count']==7
    assert done['removed_file_bytes']==view['files']['exclusive_bytes']-crashed[0][1]
    assert any(r['status']=='absent' and r['removed_bytes']==0 for r in rows('workspace_purge_file'))


def test_later_external_reference_invalidates_whole_confirmation(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'later-reference')
    path=path_for(strategy);client.delete(path);received,_=confirm(client,path)
    with core.connect() as c:
        sid=c.execute('SELECT result_snapshot_id FROM backtest_run WHERE backtest_id=?',(created['backtest_id'],)).fetchone()[0]
        c.execute("INSERT INTO research_report(report_id,job_id,instrument_id,data_cutoff,created_at_ms,report_json,snapshot_refs_json) VALUES('external-report',NULL,'600001.SH','2026-01-05',?,'{}',?)",(core.now_ms(),json.dumps([sid])))
    job=tasks.claim_next('fixture')
    with pytest.raises(ValueError,match='范围已变化'):purge.execute(job)
    assert rows('backtest_run') and not rows('snapshot_gc')
    assert not purge.get_purge(received['purge_id'])['business_deleted']


def test_shared_artifact_path_and_non_owned_head_preserve_files(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'shared-path')
    with core.connect() as c:
        sid=c.execute('SELECT result_snapshot_id FROM backtest_run WHERE backtest_id=?',(created['backtest_id'],)).fetchone()[0]
        artifact=dict(c.execute("SELECT * FROM snapshot_artifact WHERE snapshot_id=? AND role='report'",(sid,)).fetchone())
    core.publish_snapshot(module='backtest_result',job_id='fixture-job',start='2026-01-05',end='2026-01-05',artifacts=[artifact],scope='other-report',snapshot_id='shared-result-file',quality='partial')
    before={str(f):core.hash_file(f) for f in core.published_dir().rglob('*') if f.is_file()}
    path=path_for(strategy);client.delete(path);_,view=confirm(client,path)
    assert view['files']['shared_count']==8 and view['files']['exclusive_count']==0
    execute_one();assert before=={str(f):core.hash_file(f) for f in core.published_dir().rglob('*') if f.is_file()}
    assert not rows('snapshot_gc')


def test_overlap_strategy_report_cleanup_is_blocked(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'overlap')
    strategy_path=path_for(strategy);report_path='/api/v1/backtests/'+created['backtest_id']
    client.delete(strategy_path);confirm(client,report_path)
    view=client.get(strategy_path+'/purge-preview').json();assert not view['can_purge'] and '关联报告' in view['blocked_reason']


def test_watchlist_insertion_and_purge_confirmation_serialize_on_sqlite_write_lock(workspace,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    from threading import Event
    _,client,_=workspace;strategy=make_strategy(client);_,tracked=add_one(client,strategy)
    path=path_for(strategy);client.delete(path);view=client.get(path+'/purge-preview').json()
    original=purge._plan;attempted=Event();futures=[]
    with ThreadPoolExecutor(max_workers=1) as pool:
        def during_locked_plan(c,kind,identifier,**kwargs):
            prepared=original(c,kind,identifier,**kwargs)
            if c.in_transaction and not futures:
                def add_watch():attempted.set();watch(tracked['instrument_id'])
                future=pool.submit(add_watch);futures.append(future);assert attempted.wait(2)
                with pytest.raises(TimeoutError):future.result(timeout=.05)
            return prepared
        monkeypatch.setattr(purge,'_plan',during_locked_plan)
        response=client.post(path+'/purge',json={k:view[k] for k in ('confirmation_token','confirmation_text')})
        assert response.status_code==200,response.text
        futures[0].result(timeout=5)
    monkeypatch.setattr(purge,'_plan',original)
    job=tasks.claim_next('fixture')
    with pytest.raises(ValueError,match='自选保护'):purge.execute(job)
    assert rows('tracking_round') and rows('watchlist_item')


def test_old_batch_replay_and_sync_followup_never_recreate_purged_strategy(workspace,monkeypatch):
    from app.services import strategy_batches
    _,client,_=workspace;strategy=make_strategy(client);normal=make_strategy(client)
    request={'strategy_ids':[strategy['strategy_id']],'request_key':'old-complete-batch'}
    batch=strategy_batches.create_batch(request)
    for _ in range(3):worker.dispatch(tasks.claim_next('batch-fixture'))
    assert strategy_batches.get_batch(batch['batch_id'])['status']=='succeeded'
    workspace_automation.configure(strategy['strategy_id'],{'refresh_after_sync':True})
    path=path_for(strategy);deleted=client.delete(path);assert deleted.status_code==200,deleted.text
    confirm(client,path);execute_one()
    before={table:rows(table) for table in ('strategy','tracking_round','selection_run','backtest_run','job')}
    replay=client.post('/api/v1/strategy-batches',json=request)
    assert replay.status_code==200 and replay.json()['batch_id']==batch['batch_id']
    assert replay.json()['items'][0]['run_id'] is None
    assert {table:rows(table) for table in before}==before
    with pytest.raises(Exception) as error:tasks.change_task(batch['batch_id'],'retry')
    assert error.value.status_code==422
    workspace_automation.configure(normal['strategy_id'],{'refresh_after_sync':True})
    called=[]
    def execute_normal(sid,payload):
        called.append(sid)
        return {'run_id':'fixture-sync-run','matched_count':0,'unknown_count':0,'items':[]}
    monkeypatch.setattr(strategies,'run_strategy',execute_normal)
    workspace_automation.run_after_sync('fixture-job')
    assert called==[normal['strategy_id']]
    assert not any(row['key']=='strategy-execution:'+strategy['strategy_id'] for row in rows('platform_setting'))


def test_allowlist_does_not_bypass_other_target_or_any_update_guard(workspace,monkeypatch):
    _,client,factory=workspace;target=make_strategy(client);other=make_strategy(client)
    created,_=factory(target,'allowed-target');unrelated,_=factory(other,'guard-other')
    path=path_for(target);client.delete(path);confirm(client,path)
    original=purge._delete_business
    def observe(c,scope):
        # Observe the exact moment after the service installs its connection
        # function, using SQLite's trace hook to exercise another target.
        # sqlite connection methods are read-only, so validate via an extra
        # DELETE trigger which runs on the same authorized connection instead.
        c.execute("CREATE TEMP TRIGGER probe_other_target BEFORE DELETE ON backtest_run WHEN OLD.backtest_id='"+created['backtest_id']+"' BEGIN DELETE FROM backtest_run WHERE backtest_id='"+unrelated['backtest_id']+"'; END")
        with pytest.raises(sqlite3.IntegrityError,match='retain completed backtest'):original(c,scope)
        c.execute('DROP TRIGGER probe_other_target')
        original(c,scope)
        with pytest.raises(sqlite3.IntegrityError,match='retain completed backtest'):c.execute('DELETE FROM backtest_run WHERE backtest_id=?',(unrelated['backtest_id'],))
    monkeypatch.setattr(purge,'_delete_business',observe)
    execute_one()
    assert any(row['backtest_id']==unrelated['backtest_id'] for row in rows('backtest_run'))


def test_parent_restore_cannot_indirectly_restore_report_with_confirmed_purge(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'indirect-restore')
    strategy_path=path_for(strategy);client.delete(strategy_path)
    confirm(client,'/api/v1/backtests/'+created['backtest_id'])
    item=client.get('/api/v1/strategies?deleted=true').json()['items'][0]
    assert not item['can_restore'] and item['restore_blocked_reason']
    assert client.post(strategy_path+'/restore').status_code==409
    execute_one()
    assert client.post(strategy_path+'/restore').status_code==200
    assert not rows('backtest_run')


@pytest.mark.parametrize('first_kind',['strategy','backtest'])
def test_parent_and_child_confirmations_are_mutually_exclusive_from_stale_previews(workspace,first_kind):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'parent-child-'+first_kind)
    parent=path_for(strategy);child='/api/v1/backtests/'+created['backtest_id'];client.delete(parent)
    views={kind:client.get(path+'/purge-preview').json() for kind,path in [('strategy',parent),('backtest',child)]}
    assert all(view['can_purge'] for view in views.values())
    paths={'strategy':parent,'backtest':child};second_kind='backtest' if first_kind=='strategy' else 'strategy'
    first=client.post(paths[first_kind]+'/purge',json={k:views[first_kind][k] for k in ('confirmation_token','confirmation_text')})
    assert first.status_code==200,first.text
    blocked=client.get(paths[second_kind]+'/purge-preview').json()
    assert not blocked['can_purge'] and '永久清理任务' in blocked['blocked_reason']
    second=client.post(paths[second_kind]+'/purge',json={k:views[second_kind][k] for k in ('confirmation_token','confirmation_text')})
    assert second.status_code==409,second.text
    assert len(rows('workspace_purge'))==1
    execute_one();assert not any(r['status']=='failed' for r in rows('workspace_purge'))


def test_parent_and_child_parallel_submissions_accept_exactly_one_authorization(workspace):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'parallel-purges')
    paths=[path_for(strategy),'/api/v1/backtests/'+created['backtest_id']];client.delete(paths[0])
    prepared=[client.get(path+'/purge-preview').json() for path in paths]
    assert all(view['can_purge'] for view in prepared)
    barrier=Barrier(2)
    def submit(index):
        barrier.wait(timeout=5)
        return client.post(paths[index]+'/purge',json={k:prepared[index][k] for k in ('confirmation_token','confirmation_text')})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
    assert sorted(result.status_code for result in results)==[200,409]
    assert len(rows('workspace_purge'))==1 and len([r for r in rows('job') if r['kind']=='cleanup'])==1
    execute_one();assert rows('workspace_purge')[0]['status']=='completed'


def test_regular_parent_delete_cannot_mutate_markers_during_child_purge(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'normal-parent-delete')
    parent=path_for(strategy);child='/api/v1/backtests/'+created['backtest_id']
    client.delete(child);confirm(client,child)
    before={t:rows(t) for t in ('strategy','strategy_deletion','backtest_deletion','workspace_purge')}
    response=client.delete(parent)
    assert response.status_code==409 and '永久清理任务' in response.text
    item=client.get('/api/v1/strategies').json()['items'][0]
    assert not item['can_delete'] and item['delete_blocked_reason']
    assert {t:rows(t) for t in before}==before
    execute_one();assert client.delete(parent).status_code==200


def test_parent_new_confirmation_supersedes_uncommitted_failed_child_scope(workspace):
    _,client,factory=workspace;strategy=make_strategy(client);created,_=factory(strategy,'failed-child-takeover')
    parent=path_for(strategy);child='/api/v1/backtests/'+created['backtest_id'];client.delete(parent)
    original,_=confirm(client,child)
    with core.connect() as c:
        sid=c.execute('SELECT result_snapshot_id FROM backtest_run WHERE backtest_id=?',(created['backtest_id'],)).fetchone()[0]
        c.execute("INSERT INTO research_report(report_id,instrument_id,data_cutoff,created_at_ms,report_json,snapshot_refs_json) VALUES('child-shared','600001.SH','2026-01-05',?,'{}',?)",(core.now_ms(),json.dumps([sid])))
    with pytest.raises(ValueError,match='范围已变化'):purge.execute(tasks.claim_next('fixture'))
    old=next(r for r in rows('workspace_purge') if r['purge_id']==original['purge_id'])
    assert old['status']=='failed' and not old['business_deleted']
    confirmed,_=confirm(client,parent)
    after=next(r for r in rows('workspace_purge') if r['purge_id']==original['purge_id'])
    assert after['superseded_by']==confirmed['purge_id'] and after['scope_json']==old['scope_json'] and after['scope_sha256']==old['scope_sha256']
    receipt=purge.get_purge(original['purge_id']);assert not receipt['can_retry'] and not receipt['can_reconfirm']
    unfinished=client.get('/api/v1/workspace-purges?status=unfinished').json()
    assert unfinished['total']==1 and unfinished['items'][0]['purge_id']==confirmed['purge_id']
    assert client.post('/api/v1/workspace-purges/'+original['purge_id']+'/retry').status_code==409
    execute_one()
    assert purge.get_purge(confirmed['purge_id'])['status']=='completed'
    assert client.get('/api/v1/workspace-purges?status=unfinished').json()['total']==0
