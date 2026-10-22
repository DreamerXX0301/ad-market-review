"""Infrastructure contracts: credentials, task state, atomic grouped publication, HTTP scope."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    from datetime import datetime
    from app.services import sync_clock
    monkeypatch.setattr(sync_clock,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync_clock.CHINA))
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('SHIXU_CREDENTIAL_MASTER_KEY_FILE',str(tmp_path/'master'))
    from cryptography.fernet import Fernet
    (tmp_path/'master').write_bytes(Fernet.generate_key())
    from app.core import ensure_ready,clear_market_cache
    clear_market_cache();ensure_ready()
    yield tmp_path
    clear_market_cache()


def test_credentials_encrypted_and_safe_identifier(isolated):
    from app.infrastructure.credentials import CredentialStore
    vault=CredentialStore();vault.save('fuyao','fixture-credential')
    assert vault.get('fuyao')=='fixture-credential'
    assert b'fixture-credential' not in (vault.directory/'fuyao.enc').read_bytes()
    assert (vault.directory/'fuyao.enc').stat().st_mode&0o777==0o600
    with pytest.raises(ValueError):vault.save('../escape','fixture')
    with pytest.raises(ValueError):vault.save('fuyao','bad\nvalue')


def test_source_public_status_never_exposes_key(isolated):
    from app.services.source_service import configure,list_sources
    configure('fuyao',{'api_key':'fixture-private-credential'})
    result=list_sources()
    assert 'fixture-private' not in json.dumps(result)
    source=next(x for x in result['items'] if x['source_id']=='fuyao')
    assert source['configured']
    assert all(x['status']!='available' for x in source['capabilities'])


def test_task_dedup_pause_cancel_and_expired_lease(isolated):
    from app.services import task_service as t
    from app.core import connect,now_ms
    task=t.create_task('increment',{'source_id':'fuyao'},request_key='one')
    duplicate=t.create_task('increment',{'source_id':'fuyao'},request_key='two')
    assert duplicate['task_id']==task['task_id']
    jid=task['task_id'];claimed=t.claim_next('one-worker');assert claimed['job_id']==jid
    assert t.claim_next('other-worker') is None
    with connect() as c:c.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?',(now_ms()-1,jid))
    assert t.claim_next('recovery-worker')['recovery_count']==1
    t.progress(jid,'download',29,'下载已校验批次')
    assert t.task_view(jid)['progress']==29
    t.change_task(jid,'pause')
    with pytest.raises(t.TaskPaused):t.progress(jid,'parse',40)
    assert t.claim_next('worker') is None
    t.change_task(jid,'resume');assert t.claim_next('worker')['job_id']==jid
    t.change_task(jid,'cancel');t.finish(jid)
    assert t.task_view(jid)['status']=='cancelled'
    with pytest.raises(t.TaskCancelled):t.progress(jid,'publish',99)
    assert t.create_task('increment',{'source_id':'fuyao'},request_key='one')['status']=='cancelled'
    retry=t.change_task(jid,'retry');assert retry['retry_of_job_id']==jid
    from fastapi import HTTPException
    with pytest.raises(HTTPException):t.create_task('increment',{'source_id':'different'},request_key='one')


def test_new_credential_invalidates_previous_permissions(isolated,monkeypatch):
    from app.services import source_service as s
    monkeypatch.setattr(s.ProviderClient,'request',lambda *args,**kwargs:{'data':{'item':[{'fixture':True}]}})
    s.configure('fuyao',{'api_key':'fixture-first'})
    verified=s.verify('fuyao')
    assert next(c for c in verified['capabilities'] if c['key']=='daily')['status']=='available'
    changed=s.configure('fuyao',{'api_key':'fixture-second'})
    assert changed['status']=='unverified'
    assert all(c['status']!='available' for c in changed['capabilities'])
    assert next(c for c in s.verify('fuyao')['capabilities'] if c['key']=='daily')['status']=='available'


def test_partial_complete_and_backtest_retry_guard(isolated):
    from app.services import task_service as t
    from fastapi import HTTPException
    task=t.create_task('initialise',{});t.finish(task['task_id'],'partial','部分字段缺失')
    assert t.task_view(task['task_id'])['progress']==100
    task=t.create_task('backtest',{'backtest_id':'x'});t.finish(task['task_id'],'failed')
    with pytest.raises(HTTPException):t.change_task(task['task_id'],'retry')


def test_manual_sync_queues_one_durable_supplement_child(isolated,monkeypatch):
    from app import core,worker
    from app.services import sync_service,workspace_automation,task_service as tasks
    class Market:
        def status(self):return {'data_end':'2026-10-19'}
    monkeypatch.setattr(core,'get_market',lambda:Market())
    monkeypatch.setattr(sync_service,'execute_sync',lambda *args:{'quality':'partial'})
    monkeypatch.setattr(workspace_automation,'run_after_sync',lambda *args:None)
    parent=tasks.create_task('increment',{})
    job=tasks.claim_next('fixture-worker')
    worker.dispatch(job)
    children=[j for j in tasks.list_tasks()['items'] if j['kind']=='source_check']
    assert len(children)==1 and children[0]['status']=='queued'
    assert children[0]['parameters']['operation']=='market_supplement'
    assert children[0]['parameters']['year_history'] is False and children[0]['target_date']=='2026-10-19'
    worker.dispatch(job)
    assert len([j for j in tasks.list_tasks()['items'] if j['kind']=='source_check'])==1
    assert tasks.task_view(parent['job_id'])['status']=='partial'


def test_initialization_continues_full_topics_then_annual_supplement_once(isolated,monkeypatch):
    from app import core,worker
    from app.services import sync_service,topic_collection,workspace_automation,task_service as tasks
    class Market:
        def status(self):return {'data_end':'2026-10-20'}
    monkeypatch.setattr(core,'get_market',lambda:Market())
    monkeypatch.setattr(sync_service,'execute_sync',lambda *args:{'quality':'partial'})
    monkeypatch.setattr(topic_collection,'execute_topic_collection',lambda *args:{})
    monkeypatch.setattr(workspace_automation,'run_after_sync',lambda *args:None)
    parent=tasks.create_task('initialise',{'window':'near-three-years'})
    initial=tasks.claim_next('fixture-worker');worker.dispatch(initial)
    next_task=tasks.claim_next('fixture-worker')
    assert next_task['kind']=='topic_refresh'
    assert json.loads(next_task['parameters_json'])['parent_sync_job_id']==parent['job_id']
    assert not [job for job in tasks.list_tasks()['items'] if job['kind']=='source_check']
    worker.dispatch(next_task);worker.dispatch(next_task)
    followups=[job for job in tasks.list_tasks()['items'] if job['kind']=='source_check']
    assert len(followups)==1
    assert followups[0]['parameters']['year_history'] is True
    assert followups[0]['parameters']['recent_days']==1
    assert followups[0]['parameters']['parent_sync_job_id']==parent['job_id']


def test_worker_preserves_no_change_sync_status(isolated,monkeypatch):
    from app import core,worker
    from app.services import sync_service,workspace_automation,task_service as tasks
    class Market:
        def status(self):return {'data_end':'2026-10-20'}
    monkeypatch.setattr(core,'get_market',lambda:Market())
    monkeypatch.setattr(sync_service,'execute_sync',lambda *args:{'quality':'partial','status':'no_change'})
    monkeypatch.setattr(workspace_automation,'run_after_sync',lambda *args:None)
    parent=tasks.create_task('increment',{})
    worker.dispatch(tasks.claim_next('fixture-worker'))
    assert tasks.task_view(parent['job_id'])['status']=='no_change'


def test_supplement_http_task_uses_existing_schema_and_validates_scope(isolated,monkeypatch):
    from app import core
    from app.main import app
    class Market:
        def status(self):return {'ready':True,'data_end':'2026-10-19'}
    monkeypatch.setattr(core,'get_market',lambda:Market())
    with TestClient(app) as client:
        # This fixture has no provider Key and explicitly exercises only the
        # public supplement scope, excluding both Fuyao history capabilities.
        result=client.post('/api/v1/sync/market-supplement',json={'year_history':False,'sector_history':False,'recent_days':1})
        assert result.status_code==200 and result.json()['kind']=='source_check'
        assert result.json()['parameters']['operation']=='market_supplement'
        for value in (0,31,True,'20'):
            assert client.post('/api/v1/sync/market-supplement',json={'year_history':False,'sector_history':False,'recent_days':value}).status_code==422


def test_publication_cancelled_job_and_staged_heads(isolated):
    from app.core import atomic_json,published_dir,hash_file,publish_snapshot,connect
    from app.services import task_service as t
    p=published_dir()/'fixture/data.json';atomic_json(p,{'fixture':True})
    artifact={'object_key':'fixture/data.json','sha256':hash_file(p),'byte_count':p.stat().st_size,'row_count':1}
    task=t.create_task('initialise',{});jid=task['task_id']
    sid=publish_snapshot(module='calendar',job_id=jid,start='2026-01-01',end='2026-01-01',artifacts=[artifact],activate=False)
    with connect() as c:
        assert c.execute('SELECT count(*) FROM published_head').fetchone()[0]==0
        assert c.execute('SELECT state FROM dataset_snapshot WHERE snapshot_id=?',(sid,)).fetchone()[0]=='published'
    t.change_task(jid,'cancel')
    with pytest.raises(ValueError):publish_snapshot(module='daily_raw',job_id=jid,start='2026-01-01',end='2026-01-01',artifacts=[artifact])
    with connect() as c:assert c.execute('SELECT count(*) FROM published_head').fetchone()[0]==0


def test_atomic_group_registration_refuses_cancelled_before_switch(isolated):
    from app.core import published_dir,atomic_json,hash_file,connect,business_date
    from app.services import bootstrap,task_service as t
    p=published_dir()/'market-fixture/reference.json'
    atomic_json(p,{'calendar':['2026-01-02'],'master':{},'topics':[]})
    job=t.create_task('initialise',{});jid=job['task_id']
    status={'data_begin':'2026-01-02','data_end':'2026-01-02','snapshot_id':'market-fixture','rows':1,'files':[{'path':'published/market-fixture/reference.json','sha256':hash_file(p),'bytes':p.stat().st_size,'role':'reference.json'}]}
    def cancel_after_stage(stage,percent):
        if percent>82:t.change_task(jid,'cancel')
    with pytest.raises(ValueError):bootstrap.register_market(status,jid,cancel_after_stage)
    assert business_date() is None
    with connect() as c:assert c.execute('SELECT count(*) FROM published_head').fetchone()[0]==0


def test_http_without_data_and_cross_site_write(isolated):
    from app.main import app
    with TestClient(app) as client:
        assert client.get('/health').status_code==200
        assert client.get('/api/v1/bootstrap').json()['data_date'] is None
        assert client.get('/api/v1/overview').status_code==409
        assert client.post('/api/v1/sync/tasks',json={'mode':'increment'}).status_code==422
        assert client.post('/api/v1/research/jobs',json={}).status_code==422
        assert client.post('/api/v1/sources/fuyao/configure',json={'api_key':'fixture'},headers={'Origin':'https://unrelated.example'}).status_code==403
        assert client.get('/api/v1/missing').status_code==404


def test_opt_in_workspace_followups_and_validation(isolated,monkeypatch):
    from app.services import workspace_automation as a,task_service as t
    from fastapi import HTTPException
    a.strategies.create_strategy({'name':'测试策略','definition':{'field':'close','op':'>','value':0}},strategy_id='fixture')
    calls=[]
    monkeypatch.setattr(a.strategies,'run_strategy',lambda sid,payload:calls.append(('selection',payload['request_key'])) or {'run_id':'run-fixture','matched_count':1,'unknown_count':2,'items':[{'instrument_id':'600000.SH','decision':'matched'}]})
    monkeypatch.setattr(a.tracking,'add_tracking',lambda payload:calls.append(('join',payload)) or {'added_count':1})
    monkeypatch.setattr(a.tracking,'review_tracking',lambda payload:calls.append(('review',payload['request_key'])) or {'reviewed':1})
    job=t.create_task('increment',{});t.finish(job['task_id'],'partial')
    assert a.get_settings('fixture')['refresh_after_sync'] is False
    assert a.run_after_sync(job['task_id'])==[] and calls==[]
    with pytest.raises(HTTPException) as error:a.configure('fixture',{'auto_track':True})
    assert error.value.status_code==422
    with pytest.raises(HTTPException) as error:a.configure('fixture',{'refresh_after_sync':'true'})
    assert error.value.status_code==422
    a.configure('fixture',{'refresh_after_sync':True,'auto_track':True})
    result=a.run_after_sync(job['task_id'])
    assert result[0]['added_count']==1
    assert [c[0] for c in calls]==['selection','join','review']
    assert calls[0][1].startswith(job['task_id'])


def test_expired_api_calculation_does_not_block_worker(isolated):
    from app.services import task_service as t
    from app.core import connect,now_ms
    api=t.create_task('selection',{'fixture':'api'})
    with connect() as c:c.execute("UPDATE job SET status='running',lease_owner='api:dead',lease_expires_at_ms=? WHERE job_id=?",(now_ms()-1,api['task_id']))
    queued=t.create_task('increment',{'fixture':'sync'})
    assert t.claim_next('worker')['job_id']==queued['task_id']
    assert t.task_view(api['task_id'])['status']=='failed'
