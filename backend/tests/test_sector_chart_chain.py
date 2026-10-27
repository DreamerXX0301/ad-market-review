"""Frozen initialization scope and sector-batch guards in isolated SQLite.

No test contacts a source, runs a live worker or changes the user's workspace.
The collector is replaced only at the I/O boundary; queue and chain transitions
use the actual task service and worker functions.
"""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from test_platform import isolated
from test_sector_charts import Adapter, DAY, Market
from app import core, worker
from app.services import market_service, source_service, task_service as tasks

CHAIN='sector-charts-v1'


@pytest.fixture
def local(isolated,monkeypatch):
    from app.services import sector_chart_batch_jobs as jobs
    market=Market(start='2026-10-15');market.state['ready']=True
    adapter=Adapter(market);adapter.key='isolated-fixture-only';adapter.source_id='fuyao'
    context=SimpleNamespace(market=market,current=market,adapter=adapter,client_calls=[],jobs=jobs,
                            root=isolated/'data')
    monkeypatch.setattr(core,'get_market',lambda:context.current)
    monkeypatch.setattr(market_service,'get_market',lambda:context.current)
    monkeypatch.setattr(jobs,'get_market',lambda:context.current)
    def client(*args,**kwargs):
        context.client_calls.append('fuyao');return adapter
    monkeypatch.setattr(source_service,'client',client)
    monkeypatch.setattr(source_service.ProviderClient,'request',
                        lambda *a,**k:pytest.fail('real source access is forbidden'))
    return context


@pytest.fixture
def http(local):
    from app.main import app
    with TestClient(app) as client:yield client


def finish_claimed(task,status='partial'):
    job=tasks.claim_next('sector-chain-test-worker')
    assert job['job_id']==task['job_id']
    worker._authorize_chain(job)
    tasks.finish(job['job_id'],status)
    return job


def initial(local,new=True):
    parameters={'source_id':'fuyao','window':'near-five-years'}
    if new:parameters['data_chain_version']=CHAIN
    task=tasks.create_task('initialise',parameters,target_date=DAY)
    parent=finish_claimed(task)
    children=worker.recover_followups()
    assert len(children)==1 and children[0]['kind']=='topic_refresh'
    return parent,children[0]


def batch_after_topics(local):
    parent,topic=initial(local)
    topic_job=finish_claimed(topic)
    children=worker.recover_followups()
    assert len(children)==1
    batch=children[0]
    assert batch['kind']=='source_check' and batch['parameters']['operation']=='sector_charts'
    return parent,topic_job,batch


def supplement_jobs():
    return [job for job in tasks.list_tasks()['items']
            if job['parameters'].get('operation')=='market_supplement']


def test_new_initial_http_freezes_new_chain_version_without_network(http,local):
    response=http.post('/api/v1/sync/tasks',json={'mode':'initialise','request_key':'new-initial'})
    assert response.status_code==200
    task=response.json()
    assert task['kind']=='initialise'
    assert task['parameters']['data_chain_version']==CHAIN
    assert 'sector_tail_request_policy_version' not in task['parameters']
    assert local.adapter.calls==[]


def test_new_initial_topics_then_batch_then_supplement_once_and_same_frozen_date(local,monkeypatch):
    parent,topic_job,batch=batch_after_topics(local)
    assert tasks.task_view(topic_job['job_id'])['parameters']['data_chain_version']==CHAIN
    assert batch['parameters']['data_chain_version']==CHAIN
    assert batch['parameters']['mode']=='initial'
    assert batch['parameters']['initialization_chain'] is True
    assert batch['parameters']['parent_sync_job_id']==parent['job_id']
    assert batch['parameters']['previous_chain_job_id']==topic_job['job_id']
    assert batch['target_date']==DAY and supplement_jobs()==[]
    assert worker.recover_followups()==[]
    monkeypatch.setattr(local.jobs,'execute_batch_job',lambda *a,**k:{'quality':'partial','message':'isolated partial cache'})
    running=tasks.claim_next('sector-chain-test-worker')
    assert running['job_id']==batch['job_id']
    worker.dispatch(running)
    assert tasks.task_view(batch['job_id'])['status']=='partial'
    supplements=supplement_jobs();assert len(supplements)==1
    assert supplements[0]['target_date']==DAY
    assert supplements[0]['request_key']=='full-supplement-after-initialise:'+parent['job_id']
    assert supplements[0]['parameters']['year_history'] is True
    assert supplements[0]['parameters']['parent_sync_job_id']==parent['job_id']
    assert local.adapter.calls==[]
    worker.dispatch(running)
    assert worker.recover_followups()==[] and len(supplement_jobs())==1


def test_legacy_initial_and_topic_do_not_gain_new_batch_scope(local):
    parent,topic=initial(local,new=False)
    assert 'data_chain_version' not in topic['parameters']
    finish_claimed(topic)
    child=worker.recover_followups()
    assert len(child)==1 and child[0]['parameters']['operation']=='market_supplement'
    assert child[0]['request_key']=='full-supplement-after-initialise:'+parent['job_id']
    assert not any(job['parameters'].get('operation')=='sector_charts' for job in tasks.list_tasks()['items'])
    assert local.adapter.calls==[]


def test_already_followed_old_chain_is_not_expanded_after_upgrade(local):
    _,topic=initial(local,new=False)
    finish_claimed(topic);old_child=worker.recover_followups()[0]
    before=deepcopy(tasks.task_view(old_child['job_id']))
    assert worker.recover_followups()==[]
    after=tasks.task_view(old_child['job_id'])
    assert after['parameters']==before['parameters'] and after['logs']==before['logs']
    assert tasks.list_tasks()['total']==3
    assert all(job['parameters'].get('operation')!='sector_charts' for job in tasks.list_tasks()['items'])


@pytest.mark.parametrize('action,status',[('pause','paused'),('cancel','cancelled')])
def test_batch_stopped_by_user_is_never_resumed_or_followed(local,action,status):
    _,_,batch=batch_after_topics(local)
    tasks.change_task(batch['job_id'],action)
    before=deepcopy(tasks.task_view(batch['job_id']))
    assert worker.recover_followups()==[] and supplement_jobs()==[]
    after=tasks.task_view(batch['job_id'])
    assert after['status']==status and after['logs']==before['logs']
    assert tasks.claim_next('sector-chain-test-worker') is None


@pytest.mark.parametrize('parent_status',['paused','cancelled','failed'])
def test_stopped_ancestor_blocks_completed_batch_followup(local,parent_status):
    parent,_,batch=batch_after_topics(local)
    finish_claimed(batch)
    with core.connect() as connection:
        connection.execute('UPDATE job SET status=? WHERE job_id=?',(parent_status,parent['job_id']))
    assert worker.recover_followups()==[] and supplement_jobs()==[]


def test_failed_batch_is_not_misreported_as_success_or_followed(local):
    _,_,batch=batch_after_topics(local)
    finish_claimed(batch,'failed')
    assert worker.recover_followups()==[] and supplement_jobs()==[]
    assert tasks.task_view(batch['job_id'])['status']=='failed'


def test_manual_batch_route_freezes_current_date_and_is_idempotent(http,local):
    response=http.post('/api/v1/sync/sector-charts',json={'request_key':'manual-full-catalog'})
    assert response.status_code==200
    task=response.json()
    assert task['kind']=='source_check' and task['parameters']['operation']=='sector_charts'
    assert task['parameters']['snapshot_id']==local.market.state['snapshot_id']
    assert task['target_date']==task['parameters']['as_of']==DAY
    again=http.post('/api/v1/sync/sector-charts',json={'request_key':'manual-full-catalog'})
    assert again.status_code==200 and again.json()['job_id']==task['job_id']
    assert tasks.list_tasks()['total']==1 and local.adapter.calls==[]
    assert local.jobs.batch_status(local.market)['last_job']['job_id']==task['job_id']


def test_manual_batch_no_key_refuses_before_queue_or_provider(http,local):
    local.adapter.key=None
    response=http.post('/api/v1/sync/sector-charts',json={})
    assert response.status_code==422
    assert tasks.list_tasks()['total']==0 and local.adapter.calls==[]


@pytest.mark.parametrize('payload',[{'request_key':1},{'request_key':[]},{'request_key':{}},
                                    {'request_key':''},{'request_key':'x'*201},
                                    {'date':'2026-10-19'},{'snapshot_id':'forged'}])
def test_manual_batch_rejects_unfrozen_scope_and_invalid_request_keys(http,local,payload):
    assert http.post('/api/v1/sync/sector-charts',json=payload).status_code==422
    assert tasks.list_tasks()['total']==0 and local.adapter.calls==[]


def test_same_request_key_cannot_be_rebound_to_another_snapshot(local):
    local.jobs.create_batch_job(local.market,DAY,request_key='frozen-catalog')
    later=deepcopy(local.market);later.state['snapshot_id']='market-next'
    with pytest.raises(HTTPException) as caught:
        local.jobs.create_batch_job(later,DAY,request_key='frozen-catalog')
    assert caught.value.status_code==409 and tasks.list_tasks()['total']==1


@pytest.mark.parametrize('action,exception',[('pause',tasks.TaskPaused),('cancel',tasks.TaskCancelled)])
def test_execute_stopped_batch_rejects_before_constructing_source(local,action,exception):
    queued=local.jobs.create_batch_job(local.market,DAY)
    job=tasks.claim_next('sector-chain-test-worker')
    assert job['job_id']==queued['job_id']
    tasks.change_task(job['job_id'],action);local.client_calls.clear()
    with pytest.raises(exception):local.jobs.execute_batch_job(job['job_id'])
    assert local.client_calls==[] and local.adapter.calls==[]


def test_execute_queued_batch_cannot_bypass_single_writer_claim(local):
    task=local.jobs.create_batch_job(local.market,DAY);local.client_calls.clear()
    with pytest.raises(ValueError):local.jobs.execute_batch_job(task['job_id'])
    assert local.client_calls==[] and local.adapter.calls==[]


@pytest.mark.parametrize('change',['generation','pause','cancel'])
def test_guard_during_collection_prevents_summary_publication(local,monkeypatch,change):
    task=local.jobs.create_batch_job(local.market,DAY)
    job=tasks.claim_next('sector-chain-test-worker');assert job['job_id']==task['job_id']
    called=[]
    def interrupted(market,plan,adapter,guard=None,progress=None):
        called.append(True)
        assert guard is not None
        guard()
        if change=='generation':
            local.current=deepcopy(local.market);local.current.state['snapshot_id']='market-replaced'
        else:tasks.change_task(job['job_id'],change)
        guard()
        raise AssertionError('changed generation or stopped task must interrupt')
    monkeypatch.setattr(local.jobs,'collect_sector_chart_batch',interrupted)
    expected=ValueError if change=='generation' else tasks.TaskPaused if change=='pause' else tasks.TaskCancelled
    with pytest.raises(expected):local.jobs.execute_batch_job(job['job_id'])
    assert called==[True] and local.adapter.calls==[]
    assert not list((local.root/'published/market-supplement/accepted').rglob('*.json'))
    assert supplement_jobs()==[]
