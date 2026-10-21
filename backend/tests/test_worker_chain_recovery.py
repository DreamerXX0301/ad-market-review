"""Crash-window recovery uses isolated SQLite only; no sources or production tasks."""
import json

import pytest

from test_platform import isolated
from app import core,worker
from app.services import task_service as tasks


def completed(kind='initialise',parameters=None,target='2026-10-19',authorized=True):
    parameters=parameters if parameters is not None else {'window':'near-three-years','source_id':'fuyao'}
    task=tasks.create_task(kind,parameters,target)
    job=tasks.claim_next('fixture-worker')
    assert job['job_id']==task['job_id']
    if authorized:worker._authorize_chain(job)
    tasks.finish(job['job_id'],'partial')
    return job


def children():return [job for job in tasks.list_tasks()['items'] if job['kind'] in ('topic_refresh','source_check')]


def test_restart_after_parent_commit_repairs_once_with_frozen_date(isolated,monkeypatch):
    job=completed()
    monkeypatch.setattr(core,'get_market',lambda:(_ for _ in ()).throw(AssertionError('global date must not be consulted')))
    result=worker.recover_followups()
    assert len(result)==1 and result[0]['target_date']=='2026-10-19'
    assert result[0]['parameters']['initialization_chain'] is True
    assert worker.recover_followups()==[] and len(children())==1
    assert tasks.task_view(job['job_id'])['logs'][-1]['stage']=='followup_queued'


def test_restart_between_child_creation_and_parent_event_reuses_child(isolated,monkeypatch):
    job=completed();original=tasks.append_event
    def crash(connection,identifier,stage,*args,**kwargs):
        if stage=='followup_queued':raise SystemExit('controlled commit-to-child crash')
        return original(connection,identifier,stage,*args,**kwargs)
    monkeypatch.setattr(tasks,'append_event',crash)
    with pytest.raises(SystemExit):worker.recover_followups()
    assert len(children())==1
    child=children()[0]['job_id']
    assert not any(event['stage']=='chain_blocked' for event in tasks.task_view(job['job_id'])['logs'])
    monkeypatch.setattr(tasks,'append_event',original)
    repaired=worker.recover_followups()
    assert repaired[0]['job_id']==child and len(children())==1
    assert worker.recover_followups()==[]


@pytest.mark.parametrize('stopped',['cancel','pause'])
def test_existing_stopped_child_is_not_restarted(isolated,stopped):
    job=completed()
    child=tasks.create_task('topic_refresh',{'source_id':'fuyao','scope':'current-full-catalog','initialization_chain':True,'parent_sync_job_id':job['job_id']},target_date='2026-10-19',request_key='topics-after-initialise:'+job['job_id'])
    tasks.change_task(child['job_id'],stopped)
    result=worker.recover_followups()
    assert result[0]['job_id']==child['job_id']
    assert tasks.task_view(child['job_id'])['status']==('cancelled' if stopped=='cancel' else 'paused')
    assert worker.recover_followups()==[]


@pytest.mark.parametrize('status',['paused','cancelled','failed'])
def test_parent_and_ancestor_stop_prevents_new_chain(isolated,status):
    parent=completed()
    with core.connect() as connection:connection.execute('UPDATE job SET status=? WHERE job_id=?',(status,parent['job_id']))
    assert worker.recover_followups()==[] and children()==[]
    topic=tasks.create_task('topic_refresh',{'initialization_chain':True,'parent_sync_job_id':parent['job_id']},'2026-10-19')
    with core.connect() as connection:connection.execute("UPDATE job SET status='partial' WHERE job_id=?",(topic['job_id'],))
    assert worker.recover_followups()==[]
    assert not [job for job in children() if job['kind']=='source_check']


def test_legacy_initial_without_chain_authority_is_not_scheduled(isolated):
    completed(parameters={'window':'near-one-year'},authorized=False)
    completed(parameters={},authorized=False)
    completed(parameters={'window':'near-three-years'},authorized=False)
    assert worker.recover_followups()==[] and children()==[]


def test_authorized_initial_terminal_commit_recovers_from_checkpoint(isolated,monkeypatch):
    job=completed(target=None)
    path=core.raw_dir()/'sync'/job['job_id']/'checkpoint.json'
    path.parent.mkdir(parents=True)
    core.atomic_json(path,{'plan':{'kind':'initialise','target':'2026-10-19'}})
    monkeypatch.setattr(core,'get_market',lambda:(_ for _ in ()).throw(AssertionError('no global date fallback on recovery')))
    assert worker.recover_followups()[0]['target_date']=='2026-10-19'


def test_recovery_after_grouped_publication_uses_its_date_not_current_clock(isolated,monkeypatch):
    job=completed(target=None)
    artifact=core.published_dir()/'fixture.json';core.atomic_json(artifact,{'fixture':True})
    core.publish_snapshot(module='daily_raw',job_id=job['job_id'],start='2023-10-20',end='2026-10-19',artifacts=[{'object_key':'fixture.json','sha256':core.hash_file(artifact),'byte_count':artifact.stat().st_size,'row_count':1}],activate=False)
    monkeypatch.setattr(core,'get_market',lambda:(_ for _ in ()).throw(AssertionError('no global date fallback on recovery')))
    assert worker.recover_followups()[0]['target_date']=='2026-10-19'


def test_new_increment_no_change_recovers_light_child_only(isolated):
    job=completed('increment',{'source_id':'fuyao'})
    result=worker.recover_followups()
    assert len(result)==1 and result[0]['kind']=='source_check'
    assert result[0]['parameters']['year_history'] is False and result[0]['parameters']['recent_days']==1
    assert result[0]['parameters']['parent_sync_job_id']==job['job_id']


def test_completed_topic_repairs_annual_once_and_terminal_dispatch_does_not_rerun(isolated,monkeypatch):
    parent=completed()
    topic=worker.recover_followups()[0]
    job=tasks.claim_next('fixture-worker')
    assert job['job_id']==topic['job_id']
    worker._authorize_chain(job)
    tasks.finish(job['job_id'],'partial')
    from app.services import topic_collection
    monkeypatch.setattr(topic_collection,'execute_topic_collection',lambda *args:(_ for _ in ()).throw(AssertionError('must not rerun completed source work')))
    worker.dispatch(job)
    annual=[child for child in children() if child['kind']=='source_check']
    assert len(annual)==1 and annual[0]['parameters']['year_history'] is True
    assert annual[0]['parameters']['recent_days']==1 and annual[0]['target_date']=='2026-10-19'
    assert annual[0]['parameters']['parent_sync_job_id']==parent['job_id']
    assert worker.recover_followups()==[]


@pytest.mark.parametrize('status',['queued','paused','cancelled'])
def test_upgraded_initial_chain_reuses_legacy_recent20_child_without_restarting_it(isolated,status):
    parent=completed()
    topic=worker.recover_followups()[0]
    job=tasks.claim_next('fixture-worker');worker._authorize_chain(job)
    tasks.finish(job['job_id'],'partial')
    child=tasks.create_task('source_check',{'operation':'market_supplement','year_history':True,'recent_days':20,'source_id':'fuyao','parent_sync_job_id':parent['job_id'],'previous_chain_job_id':job['job_id']},target_date='2026-10-19',request_key='full-supplement-after-initialise:'+parent['job_id'])
    if status=='paused':tasks.change_task(child['job_id'],'pause')
    if status=='cancelled':tasks.change_task(child['job_id'],'cancel')
    repaired=worker.recover_followups()
    assert len(repaired)==1 and repaired[0]['job_id']==child['job_id']
    assert repaired[0]['parameters']['recent_days']==20
    assert tasks.task_view(child['job_id'])['status']==status
    assert worker.recover_followups()==[]


def test_missing_frozen_target_is_explicitly_blocked_not_guessed(isolated,monkeypatch):
    job=completed(target=None)
    monkeypatch.setattr(core,'get_market',lambda:(_ for _ in ()).throw(AssertionError('never use current date')))
    assert worker.recover_followups()==[]
    assert children()==[]
    events=tasks.task_view(job['job_id'])['logs']
    assert events[-1]['stage']=='chain_blocked' and '冻结日期' in events[-1]['details']['reason']


def test_broken_parent_is_isolated_and_does_not_block_valid_or_ordinary_work(isolated):
    bad=completed(target=None)
    path=core.raw_dir()/'sync'/bad['job_id']/'checkpoint.json';path.parent.mkdir(parents=True)
    path.write_text('{broken fixture')
    good=completed(target='2026-10-20')
    ordinary=tasks.create_task('source_check',{'fixture':'ordinary'},'2026-10-20')
    repaired=worker.recover_followups()
    assert len(repaired)==1 and repaired[0]['parameters']['parent_sync_job_id']==good['job_id']
    events=tasks.task_view(bad['job_id'])['logs']
    assert events[-1]['stage']=='chain_blocked'
    assert worker.recover_followups()==[]
    assert len(tasks.task_view(bad['job_id'])['logs'])==len(events)
    assert tasks.claim_next('ordinary-worker')['job_id']==ordinary['job_id']
