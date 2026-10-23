"""Source absence, durable receipts and task fencing remain explicit facts."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_local_coverage_check import local_market, isolated, A, DAYS
from app import core, worker
from app.services import local_coverage_check as local, quote_gap_source_check as source, task_service as tasks


@pytest.fixture
def gap_plan(local_market,monkeypatch):
    market,_=local_market
    monkeypatch.setattr(source,'get_market',lambda:market)
    report=local.verify(market)
    artifact=local._root()/'reports'/'fixture.json';core.atomic_json(artifact,report)
    core.atomic_json(local._root()/'latest.json',{'artifact':'reports/fixture.json','sha256':core.hash_file(artifact)})
    calls=[]
    adapter=SimpleNamespace(key='fixture-key',request=lambda path,params:(calls.append(params) or {'data':{'item':[],'thscode':A,'interval':'1d','adjust':'none'}}))
    monkeypatch.setattr(source.source_service,'client',lambda *args:adapter)
    return market,report,adapter,calls


def enqueue():
    return source.create_check({'plan_id':source.read_view()['plan_id'],'request_key':'gap-source-fixture'})


def test_no_remote_on_get_and_empty_source_response_never_proves_suspension(gap_plan,monkeypatch):
    _,report,_,calls=gap_plan
    assert source.read_view()['evidence'] is None and calls==[]
    task=enqueue();worker.dispatch(tasks.claim_next('source-worker'))
    assert tasks.task_view(task['job_id'])['status']=='succeeded'
    view=source.read_view(full=True);proof=view['evidence']
    assert proof['checked_sessions']==1 and proof['source_absent_sessions']==1
    assert proof['fillable_sessions']==0 and proof['publication_changed'] is False
    item=next(iter(proof['gaps'].values()))
    assert item['status']=='source_no_quote' and item['confirmed_suspension'] is None
    assert len(calls)==1 and calls[0]['thscode']==A
    assert tasks.task_activity()['items'][0]['title']=='报价缺口来源核验'
    original=local._read_report(local._context(gap_plan[0]))[0]
    assert original['daily']['gap_diagnosis']['stocks'][0]['episodes'][0]['source_verification'] is None
    assert local.read_view(full=True)['report']['daily']['gap_diagnosis']['stocks'][0]['episodes'][0]['source_verification']['status']=='source_no_quote'
    monkeypatch.setattr(source.duckdb,'connect',lambda *a,**k:pytest.fail('GET cannot scan history'))
    assert source.read_view()['evidence']['checked_sessions']==1 and len(calls)==1


def test_real_returned_missing_quote_is_repairable_but_does_not_mutate_publication(gap_plan):
    market,_,adapter,_=gap_plan
    row={'date_ms':source.midnight_ms(DAYS[1]),'open_price':10.,'high_price':11.,'low_price':9.,'close_price':10.,'volume':100.,'turnover':1000.}
    adapter.request=lambda *args:{'data':{'item':[row],'thscode':A,'interval':'1d','adjust':'none'}}
    before={path['path']:path['sha256'] for path in market.status()['files']}
    enqueue();worker.dispatch(tasks.claim_next('source-worker'))
    proof=source.read_view(full=True)['evidence']
    assert proof['fillable_sessions']==1 and proof['source_absent_sessions']==0
    assert next(iter(proof['gaps'].values()))['status']=='local_missing'
    assert before=={path['path']:path['sha256'] for path in market.status()['files']}


@pytest.mark.parametrize('payload',[
    {'data':{}}, {'data':{'item':[],'thscode':'000001.SZ'}},
    {'data':{'item':[],'truncated':True}}, {'data':{'item':[{'date_ms':False}]}},
    {'data':{'item':[{'date_ms':source.midnight_ms(DAYS[1]),'open_price':0.}]}},
])
def test_invalid_or_wrong_source_response_is_not_confirmed_empty(gap_plan,payload):
    _,_,adapter,_=gap_plan;adapter.request=lambda *args:payload
    task=enqueue();worker.dispatch(tasks.claim_next('source-worker'))
    proof=source.read_view(full=True)['evidence']
    assert proof['failed_stocks']==1 and proof['source_absent_sessions']==0 and proof['checked_sessions']==0
    assert tasks.task_view(task['job_id'])['status']=='partial'
    assert next(iter(proof['gaps'].values()))['status']=='failed'


def test_pause_during_remote_request_cannot_accept_or_publish(gap_plan):
    _,_,adapter,_=gap_plan;task=enqueue()
    def pause(*args):
        tasks.change_task(task['job_id'],'pause')
        return {'data':{'item':[]}}
    adapter.request=pause
    with pytest.raises(tasks.TaskPaused):source.execute_check(tasks.claim_next('source-worker'))
    assert source.read_view()['evidence'] is None
    with core.connect() as db:assert db.execute('SELECT count(*) FROM sync_batch WHERE job_id=?',(task['job_id'],)).fetchone()[0]==0


def test_recovery_reuses_sha_receipt_and_new_attempt_fences_old_worker(gap_plan,monkeypatch):
    _,_,_,calls=gap_plan;task=enqueue();job=tasks.claim_next('same-worker')
    original=source._publish
    def interrupt(*args):raise RuntimeError('fixture process interrupted after accepted receipt')
    monkeypatch.setattr(source,'_publish',interrupt)
    with pytest.raises(RuntimeError):source.execute_check(job)
    assert len(calls)==1
    with core.connect() as db:db.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?',(core.now_ms()-1,task['job_id']))
    recovered=tasks.claim_next('same-worker')
    assert recovered['lease_owner']!=job['lease_owner']
    monkeypatch.setattr(source,'_publish',original)
    with pytest.raises(tasks.TaskCancelled):source.execute_check(job)
    source.execute_check(recovered)
    assert len(calls)==1 and source.read_view()['evidence']['checked_stocks']==1


def test_stale_context_rejects_creation_and_hides_old_evidence(gap_plan):
    market,_,_,_=gap_plan;old=source.read_view()['plan_id']
    enqueue();worker.dispatch(tasks.claim_next('source-worker'))
    core.set_setting('source-credential-revision:fuyao','new-credential')
    # Credential changes affect future requests, not historical point-in-time evidence.
    assert source.read_view()['is_current']
    with core.connect() as db:db.execute('INSERT INTO listing_status_revision VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET revision=revision+1')
    assert source.read_view()['available'] is False
    with pytest.raises(HTTPException):source.create_check({'plan_id':old})


def test_request_scope_and_identity_are_validated(gap_plan):
    plan=source.read_view()['plan_id']
    for payload in ({'plan_id':plan,'instrument_ids':[]},{'plan_id':plan,'instrument_ids':['000000.SZ']},{'plan_id':plan,'extra':True},{'plan_id':plan,'request_key':False}):
        with pytest.raises(HTTPException):source.create_check(payload)


def test_short_gap_windows_do_not_download_years_between_isolated_gaps():
    dates=['2021-01-04','2021-05-04','2026-10-21']
    windows=source.request_windows(dates,dates)
    assert len(windows)==3
    assert all((source.date.fromisoformat(end)-source.date.fromisoformat(start)).days<=95 for start,end in windows)


def test_row_identity_cannot_override_top_level_identity():
    with pytest.raises(ValueError,match='逐行股票身份'):
        source._quotes({'data':{'item':[{'thscode':'000001.SZ'}]}},A,DAYS[0],DAYS[-1])


@pytest.mark.parametrize('field,value',[('currency','USD'),('interval','1w'),('adjusted',True),('adjust','qfq')])
def test_explicit_conflicting_row_price_metadata_is_rejected(field,value):
    with pytest.raises(ValueError,match='逐行价格口径'):
        source._quotes({'data':{'item':[{field:value}]}},A,DAYS[0],DAYS[-1])
