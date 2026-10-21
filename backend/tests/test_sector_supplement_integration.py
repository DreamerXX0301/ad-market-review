"""Supplement publication and durable scope use isolated cache/SQLite fixtures.

The collector result is a controlled normalized fixture; no remote request,
credential lookup, production data or running worker is involved.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from test_platform import isolated
from test_market_repair import payload, public_row

from app import core, worker
from app.adapters.providers import ProviderError
from app.services import market_supplement as supplement
from app.services import sector_history, source_service, task_service as tasks

DAY='2026-10-20'
PREVIOUS='2026-10-19'
LATER='2026-10-21'


class LocalMarket:
    def __init__(self,path):
        self.path=path
        path.mkdir()
        pd.DataFrame([{'instrument_id':symbol,'date':day,'close':10.}
                      for day in (PREVIOUS,DAY)
                      for symbol in ('600825.SH','000001.SZ')]).to_parquet(path/'daily.parquet')

    def status(self):
        return {'ready':True,'snapshot_id':'market-fixture','data_end':DAY,'path':str(self.path)}

    def calendar(self):return [PREVIOUS,DAY]

    def instruments(self,**kwargs):
        return [{'instrument_id':symbol,'code':symbol[:6],'kind':'stock'}
                for symbol in ('600825.SH','000001.SZ')]


@pytest.fixture
def cache_workspace(tmp_path,monkeypatch):
    root=tmp_path/'supplement'
    monkeypatch.setattr(supplement,'_root',lambda:root)
    market=LocalMarket(tmp_path/'market-fixture')

    class Adapter:
        def request(self,path,params):
            assert params['date_ms']==supplement._midnight(DAY)
            symbol='600825.SH' if path.endswith('limit-up-pool') else '000001.SZ'
            return {'data':{'item':[{'thscode':symbol,'ticker':symbol[:6],'last_price':10.}],
                            'pagination':{'total':1}}}

    class Public:
        def limit_pool(self,day):
            assert day==DAY
            return payload([public_row()],qdate=20261020)

    monkeypatch.setattr(source_service,'client',lambda *_:Adapter())
    monkeypatch.setattr(supplement,'PublicMarketClient',Public)
    fund={'date':DAY,'source':'eastmoney-public','complete':True,
          'items':[{'topic_code':'BK0001','date':DAY,'net_inflow':123.}],
          'history':[{'topic_code':'BK0001','date':DAY,'net_inflow':123.}],
          'coverage':{'selected_count':1,'accepted_count':1},'failures':[],
          'raw_pages':[{'controlled_fund_fixture':True}]}
    monkeypatch.setattr(supplement,'fetch_history_for_market',lambda *a,**k:deepcopy(fund))
    return root,market


def sector_report(count=710):
    return {'date':DAY,'source':'fuyao','normalization_version':sector_history.NORMALIZATION_VERSION,
            'items':[{'topic_code':f'{880000+i:06d}.TI','date':DAY,
                      'request_signature':'controlled-fixture-'+str(i),
                      'raw_artifact':'controlled-raw-'+str(i),'raw_sha256':'controlled-sha-'+str(i)}
                     for i in range(count)],
            'coverage':{'catalog_count':count,'selected_count':count,'accepted_count':count,
                        'current_catalog':True,'historical_catalog_complete':False},
            'failures':[],'complete':True}


def test_default_refresh_preserves_legacy_scope_without_collecting_sector_history(cache_workspace,monkeypatch):
    root,market=cache_workspace
    monkeypatch.setattr(sector_history,'collect_sector_history',
                        lambda *a,**k:pytest.fail('legacy refresh must not acquire new scope'))
    result=supplement.refresh_for_market(market,DAY)
    assert result['sector_index_history']=={}
    assert not (root/'accepted/fuyao/sector-day').exists()
    assert result['annual_pools'][DAY]['items'][0]['instrument_id']=='600825.SH'
    assert result['annual_down_pools'][DAY]['items'][0]['instrument_id']=='000001.SZ'
    assert result['public_pools'][DAY]['items']
    assert result['fund_flows'][DAY]['items'][0]['net_inflow']==123.


def test_sector_table_is_an_independent_hashed_artifact_and_head_keeps_other_capabilities(cache_workspace,monkeypatch):
    root,market=cache_workspace;calls=[]
    def collect(m,target,cache,adapter,**kwargs):
        calls.append((m,target,cache.root))
        return sector_report()
    monkeypatch.setattr(sector_history,'collect_sector_history',collect)
    result=supplement.refresh_for_market(market,DAY,sector_history=True)
    assert calls==[(market,DAY,root)]
    published=supplement.load_supplement('market-fixture')
    assert published==result
    descriptor=published['sector_index_history'][DAY]
    assert descriptor['date']==DAY and descriptor['source']=='fuyao'
    assert descriptor['normalization_version']==sector_history.NORMALIZATION_VERSION
    assert descriptor['coverage']['accepted_count']==710
    assert 'items' not in descriptor and 'raw_pages' not in descriptor
    assert len(json.dumps(descriptor))<2048, 'head must not embed the whole board table'
    artifact=root/descriptor['artifact']
    assert core.hash_file(artifact)==descriptor['sha256']
    accepted=json.loads(artifact.read_text())
    assert len(accepted['items'])==710
    assert accepted['date']==DAY and accepted['source']=='fuyao'
    assert (root/'accepted/fuyao/sector-day'/f'{DAY}.json').is_file()
    for key in ('annual_pools','annual_down_pools','public_pools','fund_flows'):
        assert published[key][DAY]['items'], f'{key} was lost during sector publication'


@pytest.mark.parametrize('failure_mode',['empty','exception'])
def test_total_sector_failure_retains_previous_success_and_other_published_inputs(cache_workspace,monkeypatch,failure_mode):
    root,market=cache_workspace
    monkeypatch.setattr(sector_history,'collect_sector_history',lambda *a,**k:sector_report(2))
    first=supplement.refresh_for_market(market,DAY,sector_history=True)
    descriptor=deepcopy(first['sector_index_history'][DAY])
    original=(root/descriptor['artifact']).read_bytes()
    def fail(*args,**kwargs):
        if failure_mode=='exception':raise ProviderError('controlled sector failure')
        return {**sector_report(0),'complete':False,
                'failures':[{'reason':'controlled sector failure'}]}
    monkeypatch.setattr(sector_history,'collect_sector_history',fail)
    second=supplement.refresh_for_market(market,DAY,sector_history=True)
    assert second['sector_index_history'][DAY]==descriptor
    assert (root/descriptor['artifact']).read_bytes()==original
    assert second['quality']=='partial'
    assert any(f['capability']=='sector_index_history' for f in second['failures'])
    for key in ('annual_pools','annual_down_pools','public_pools','fund_flows'):
        assert second[key][DAY]['items']==first[key][DAY]['items']
    assert supplement.load_supplement()['sector_index_history'][DAY]==descriptor


@pytest.mark.parametrize('sector_flag,expected',[(None,False),(False,False),(True,True),('true',False),(1,False)])
def test_execute_uses_only_explicit_boolean_scope_and_the_frozen_task_date(isolated,monkeypatch,sector_flag,expected):
    parameters={'operation':'market_supplement','year_history':False,'recent_days':1}
    if sector_flag is not None:parameters['sector_history']=sector_flag
    task=tasks.create_task('source_check',parameters,target_date=DAY)
    market=SimpleNamespace(status=lambda:{'ready':True,'snapshot_id':'later-market','data_end':LATER})
    monkeypatch.setattr(supplement,'get_market',lambda:market)
    calls=[]
    def refresh(current,target,**kwargs):
        calls.append((current,target,kwargs))
        kwargs['guard']()
        return {'fixture':True}
    monkeypatch.setattr(supplement,'refresh_for_market',refresh)
    assert supplement.execute_supplement(task['job_id'])=={'fixture':True}
    assert len(calls)==1 and calls[0][:2]==(market,DAY)
    assert calls[0][2]['sector_history'] is expected
    assert calls[0][2]['year_history'] is False and calls[0][2]['recent_days']==1
    assert tasks.task_view(task['job_id'])['parameters']==parameters


@pytest.fixture
def route_client(isolated,monkeypatch):
    from app.routers import platform
    status={'ready':True,'snapshot_id':'market-fixture','data_end':DAY}
    key={'value':'controlled-local-credential'}
    monkeypatch.setattr(core,'get_market',lambda:SimpleNamespace(status=lambda:dict(status)))
    monkeypatch.setattr(source_service,'client',lambda *_:SimpleNamespace(key=key['value']))
    app=FastAPI();app.include_router(platform.router)
    with TestClient(app) as client:yield client,status,key


def test_manual_route_defaults_new_scope_and_freezes_date_before_market_advances(route_client):
    client,status,_=route_client
    response=client.post('/api/v1/sync/market-supplement',json={'request_key':'new-sector-scope'})
    assert response.status_code==200
    task=response.json()
    assert task['parameters']['sector_history'] is True
    assert task['parameters']['year_history'] is True and task['parameters']['recent_days']==1
    assert task['parameters']['source_id']=='fuyao' and task['target_date']==DAY
    status['data_end']=LATER
    assert tasks.task_view(task['job_id'])['target_date']==DAY
    assert tasks.task_view(task['job_id'])['parameters']==task['parameters']


@pytest.mark.parametrize('value',['true',1,0,None,[],{}])
def test_manual_route_rejects_non_boolean_sector_scope_without_queueing(route_client,value):
    client,_,_=route_client
    assert client.post('/api/v1/sync/market-supplement',json={'sector_history':value}).status_code==422
    assert tasks.list_tasks()['total']==0


def test_sector_scope_needs_a_key_but_explicit_public_only_scope_does_not(route_client):
    client,_,key=route_client;key['value']=None
    rejected=client.post('/api/v1/sync/market-supplement',json={'year_history':False})
    assert rejected.status_code==422 and tasks.list_tasks()['total']==0
    accepted=client.post('/api/v1/sync/market-supplement',json={'year_history':False,'sector_history':False})
    assert accepted.status_code==200
    assert accepted.json()['parameters']['source_id']=='akshare-public'
    assert accepted.json()['parameters']['sector_history'] is False


@pytest.mark.parametrize('recent_days',[1,20])
@pytest.mark.parametrize('status',['queued','paused','cancelled'])
def test_legacy_frozen_children_are_reused_without_scope_or_state_drift(isolated,recent_days,status):
    parameters={'operation':'market_supplement','year_history':True,'recent_days':recent_days,
                'source_id':'fuyao','parent_sync_job_id':'fixture-parent','previous_chain_job_id':'fixture-topic'}
    child=tasks.create_task('source_check',parameters,target_date=DAY,request_key='frozen-legacy-child')
    if status!='queued':tasks.change_task(child['job_id'],'pause' if status=='paused' else 'cancel')
    before=tasks.task_view(child['job_id'])
    result=worker._supplement_child({**parameters,'recent_days':1,'sector_history':True},DAY,'frozen-legacy-child')
    after=tasks.task_view(child['job_id'])
    assert result['job_id']==child['job_id'] and result['parameters']==parameters
    assert after['status']==status and after['parameters']==parameters
    assert after['logs']==before['logs'] and tasks.list_tasks()['total']==1


def test_new_supplement_child_freezes_sector_scope_without_replacing_legacy_tasks(isolated):
    parameters={'operation':'market_supplement','year_history':False,'recent_days':1,
                'source_id':'fuyao','sector_history':True,'parent_sync_job_id':'fixture-parent'}
    child=worker._supplement_child(parameters,DAY,'new-sector-child')
    assert child['parameters']==parameters and child['target_date']==DAY
    assert worker._supplement_child(parameters,DAY,'new-sector-child')['job_id']==child['job_id']
    assert tasks.list_tasks()['total']==1
