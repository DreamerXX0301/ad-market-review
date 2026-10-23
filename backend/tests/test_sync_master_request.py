"""Explicit new exchanges retain listing dates without changing old signatures."""
from copy import deepcopy
from datetime import datetime
import json

import pytest

from test_sync import transport, start_job
from app.core import digest
from app.services import sync_service as sync, task_service as tasks

PATH='/api/meta/tickers/list'
LEGACY={'asset_type':'a-share','limit':10000,'offset':0}
EXPLICIT={**LEGACY,'exchange':'SH,SZ,BJ'}


def pause_after_master(job):
    def progress(stage,percent):
        if stage.startswith('下载daily-k'):tasks.change_task(job,'pause')
    return progress


@pytest.mark.parametrize('kind',['initialise','increment'])
def test_new_sync_freezes_explicit_exchanges_before_master_and_keeps_raw_evidence(transport,monkeypatch,kind):
    if kind=='increment':
        sync.execute_sync(start_job(window='near-one-year'))
        monkeypatch.setattr(sync,'now_china',lambda:datetime(2026,10,20,18,tzinfo=sync.CHINA))
    transport.calls.clear()
    original=transport.request
    def source(path,params=None):
        result=deepcopy(original(path,params))
        if path==PATH:
            # The source's unqualified endpoint has null listing dates; the
            # explicit exchanges return the real date plus an unknown suffix.
            for index,row in enumerate(result['data']['item']):
                row['list_date']='2000-01-01' if params.get('exchange')=='SH,SZ,BJ' and index==0 else None
        return result
    monkeypatch.setattr(transport,'request',source)
    job=start_job(kind,window='near-one-year')
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,pause_after_master(job))
    collector=sync.Collector(job,None)
    assert collector.state['plan']['master_request_params']==EXPLICIT
    assert [params for path,params in transport.calls if path==PATH]==[EXPLICIT]
    accepted=json.loads((collector.root/'accepted/master.json').read_text())
    assert accepted['data']['item'][0]['list_date']=='2000-01-01'
    assert accepted['data']['item'][1]['list_date'] is None
    canonical=json.loads((collector.root/'raw/fuyao_stock_master.json').read_text())
    assert canonical['identity_lineage']['master_request_params']==EXPLICIT
    assert canonical['identity_lineage']['raw_evidence']=='accepted/master.json'
    assert collector.state['files']['master.json']['signature']==digest({'path':PATH,'params':EXPLICIT})


def test_legacy_checkpoint_reuses_original_master_signature_and_fetch_receipt(transport):
    job=start_job(window='near-one-year');collector=sync.Collector(job,transport)
    collector.state['plan']={'kind':'initialise','target':'2026-10-19','begin':'2025-10-20',
                            'wanted':'2025-10-19','window':'near-one-year','provider':'fuyao',
                            'previous_generation':None,'previous_end':None}
    sync.atomic_json(collector.state_path,collector.state)
    collector.request('master.json',PATH,LEGACY,5)
    before=deepcopy(collector.state['files']['master.json'])
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,pause_after_master(job))
    resumed=sync.Collector(job,None)
    assert 'master_request_params' not in resumed.state['plan']
    assert resumed.state['files']['master.json']==before
    assert [params for path,params in transport.calls if path==PATH]==[LEGACY]
    canonical=json.loads((resumed.root/'raw/fuyao_stock_master.json').read_text())
    assert canonical['identity_lineage']['master_request_params']==LEGACY


def test_new_paused_plan_uses_its_saved_master_params_after_default_changes(transport,monkeypatch):
    job=start_job(window='near-one-year')
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,pause_after_master(job))
    before=sync.Collector(job,None).state
    monkeypatch.setattr(sync,'MASTER_REQUEST_PARAMS',{'asset_type':'a-share','limit':50,'offset':0})
    transport.calls.clear()
    tasks.change_task(job,'resume');tasks.claim_next('fixture-worker')
    with pytest.raises(tasks.TaskPaused):sync.execute_sync(job,pause_after_master(job))
    after=sync.Collector(job,None).state
    assert after['plan']==before['plan']
    assert after['files']['master.json']==before['files']['master.json']
    assert not [path for path,_ in transport.calls if path==PATH]


@pytest.mark.parametrize('bad',[{**EXPLICIT,'exchange':'US'},{**EXPLICIT,'offset':False},{**EXPLICIT,'limit':10000.0}])
def test_invalid_frozen_master_params_are_rejected_before_credentials_or_requests(transport,monkeypatch,bad):
    job=start_job(window='near-one-year');collector=sync.Collector(job,None)
    collector.state['plan']={'kind':'initialise','target':'2026-10-19','master_request_params':bad}
    sync.atomic_json(collector.state_path,collector.state)
    monkeypatch.setattr(sync.source_service,'client',lambda *_:pytest.fail('invalid frozen plan must not access credentials'))
    with pytest.raises(ValueError,match='冻结证券目录请求参数'):sync.execute_sync(job)
    assert transport.calls==[]
