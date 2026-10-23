"""Polling task history is bounded; full selected-job audit remains compatible."""
import json
import pytest

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import core
from app.routers.platform import router
from app.services import task_service as tasks
from test_platform import isolated


def client():
    app=FastAPI();app.include_router(router)
    return TestClient(app)


def test_summary_omits_large_parameters_and_logs_but_keeps_followup_states(isolated):
    task=tasks.create_task('increment',{'source_id':'fuyao','data_chain_version':'sector-charts-increment-v1',
        'api_key':'private-fixture','large_payload':['a'*64]*10000})
    with core.connect() as connection:
        tasks.append_event(connection,task['job_id'],'chain_authorized','已授权后续数据链')
        for index in range(12):
            tasks.append_event(connection,task['job_id'],'核验来源','批次已核验',details={'source_sha':{'large': ['b'*64]*1000},'actual_completed_batches':index,'actual_total_batches':12})
    tasks.finish(task['job_id'])
    response=client().get('/api/v1/tasks?limit=200&summary=true')
    assert response.status_code==200 and len(response.content)<2500
    view=response.json();item=view['items'][0]
    assert view['summary'] and item['summary'] and item['task_id']==task['task_id']
    assert item['parameters']=={'source_id':'fuyao','data_chain_version':'sector-charts-increment-v1'}
    assert [event['stage'] for event in item['logs']]==['chain_authorized']
    assert item['status']=='succeeded' and item['progress']==100 and item['duration_ms']>=0
    assert item['row_count'] is None and item['accepted_rows'] is None
    assert all(value not in response.text for value in ('private-fixture','large_payload','source_sha','large:'))
    with core.connect() as connection:
        tasks.append_event(connection,task['job_id'],'followup_queued','后续已排队',details={'next_task_id':'child-fixture','unneeded':['c'*64]*1000})
    queued=client().get('/api/v1/tasks?summary=true').json()['items'][0]
    assert queued['logs'][-1]['details']=={'next_task_id':'child-fixture'}
    with core.connect() as connection:
        tasks.append_event(connection,task['job_id'],'chain_blocked','排队受阻',details={'reason':'真实原因','raw_payload':['d'*64]*1000})
    blocked=client().get('/api/v1/tasks?summary=true').json()['items'][0]
    assert [event['stage'] for event in blocked['logs']]==['chain_authorized','followup_queued','chain_blocked']
    assert blocked['logs'][-1]['details']=={'reason':'真实原因'}
    # Explicit legacy list and selected detail still expose the original audit.
    legacy=client().get('/api/v1/tasks').json()['items'][0]
    detail=client().get('/api/v1/tasks/'+task['job_id']).json()
    assert legacy['parameters']['large_payload']==detail['parameters']['large_payload']
    assert len(legacy['logs'])==len(detail['logs']) and len(detail['logs'])>12


def test_200_summary_receipts_stay_under_250kb_without_per_task_connections(isolated,monkeypatch):
    stamp=core.now_ms()
    with core.connect() as connection:
        for index in range(200):
            parameters={'operation':'sector_charts','mode':'increment','data_chain_version':'sector-charts-increment-v1',
                        'source_sha':['a'*64]*100 if index==0 else [],'fixture':index}
            connection.execute("""INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,
                created_at_ms,finished_at_ms) VALUES(?,?,?,'source_check',?,?,'succeeded',?,?)""",
                (f'fixture-{index}',f'key-{index}',f'logical-{index}',json.dumps(parameters),'a'*64,stamp-index-1,stamp))
    connections=[];original=tasks.connect
    def counted():connections.append(True);return original()
    monkeypatch.setattr(tasks,'connect',counted)
    body=tasks.list_tasks(200,summary=True)
    encoded=json.dumps(body,ensure_ascii=False).encode()
    assert len(body['items'])==body['total']==200
    assert len(encoded)<250000
    assert len(connections)==1,'summary polling must not reopen SQLite once per history item'
    assert body['items'][0]['parameters']['mode']=='increment'
    assert all('source_sha' not in row['parameters'] and row['logs']==[] for row in body['items'])


def test_summary_running_unknown_progress_and_parameter_instrument_allowlist(isolated):
    task=tasks.create_task('source_check',{'operation':'sector_chart','topic_code':'123456',
        'instrument':{'name':'研究行业','source_code':'123456','raw':'do-not-return'},'window':'near-five-years'})
    tasks.claim_next('fixture-worker')
    item=tasks.list_tasks(summary=True)['items'][0]
    assert item['progress'] is None
    assert item['parameters']['instrument']=={'name':'研究行业','source_code':'123456'}
    assert item['parameters']['window']=='near-five-years'
    tasks.progress(task['job_id'],'开始准备',0,'已开始实际准备',{'actual_completed_batches':0,'actual_total_batches':3})
    assert tasks.list_tasks(summary=True)['items'][0]['progress']==0


def test_offline_storage_maintenance_cannot_be_requeued(isolated):
    task=tasks.create_task('source_check',{'operation':'local_storage_conversion'})
    tasks.finish(task['job_id'])
    assert tasks.list_tasks(summary=True)['items'][0]['title']=='本地存储升级'
    for action in ('pause','resume','retry'):
        with pytest.raises(HTTPException,match='离线维护记录') as error:
            tasks.change_task(task['job_id'],action)
        assert error.value.status_code==422
    assert tasks.list_tasks()['total']==1
