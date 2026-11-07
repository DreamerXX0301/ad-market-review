"""Local-only evidence recovery; every file and task uses an isolated fixture."""
from datetime import date, timedelta
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from test_platform import isolated
from app import core
from app.adapters.providers import ProviderError
from app.services import market_supplement as supplement, source_service, task_service as tasks

FIRST='2026-01-05'
SECOND='2026-01-06'
UP='600825.SH'
DOWN='000001.SZ'


class LocalMarket:
    def __init__(self,path,quotes,*,extra_master=False):
        self.path=path;path.mkdir()
        self.quotes=quotes;self.extra_master=extra_master
        pd.DataFrame([{'date':day,'instrument_id':symbol,'close':price}
                      for day,prices in quotes.items() for symbol,price in prices.items()]).to_parquet(path/'daily.parquet')

    def status(self):return {'ready':True,'snapshot_id':self.path.name,'data_end':max(self.quotes),'path':str(self.path)}
    def calendar(self):return sorted(self.quotes)
    def instruments(self,**kwargs):
        symbols=[UP,DOWN]+(['688827.SH'] if self.extra_master else [])
        return [{'instrument_id':symbol,'code':symbol[:6],'kind':'stock'} for symbol in symbols]


@pytest.fixture
def offline(tmp_path,monkeypatch):
    root=tmp_path/'supplement'
    monkeypatch.setattr(supplement,'_root',lambda:root)
    def forbidden(*args,**kwargs):pytest.fail('offline repair must not read credentials, construct a provider or fetch')
    monkeypatch.setattr(source_service,'client',forbidden)
    monkeypatch.setattr(supplement,'PublicMarketClient',forbidden)
    monkeypatch.setattr(supplement,'fetch_history_for_market',forbidden)
    return supplement.SupplementCache(root)


def seed(cache,market,day,kind,*,rows=None,signature=None):
    symbol=UP if kind=='limitup' else DOWN
    raw=[{'data':{'item':rows if rows is not None else [{'ticker':symbol[:6],'thscode':symbol,'last_price':10.,'date':day}],
                  'pagination':{'total':len(rows) if rows is not None else 1}}}]
    prices=market.quotes[day]
    result,_=supplement.fetch_fuyao_pool(None,day,prices,market.instruments(),payload_pages=raw,pool_kind=kind)
    identity=core.digest({row['code']:row['instrument_id'] for row in market.instruments()})
    value=signature or supplement._pool_signature(day,'fuyao',prices,identity,kind)
    return cache.accept('fuyao',kind,day,value,result,raw)


def publish(cache,market,up=None,down=None):
    value={'version':supplement.VERSION,'snapshot_id':market.status()['snapshot_id'],
           'annual_pools':up or {},'annual_down_pools':down or {},'public_pools':{},
           'fund_flows':{},'sector_index_history':{},'failures':[]}
    cache.publish(value)


def test_offline_recovers_orphan_evidence_after_unrelated_ipo_and_is_idempotent(offline,tmp_path):
    previous=LocalMarket(tmp_path/'old',{FIRST:{UP:10.,DOWN:10.},SECOND:{UP:10.,DOWN:10.}})
    for day in previous.calendar():
        for kind in ('limitup','limitdown'):seed(offline,previous,day,kind)
    # The active index lost the first day, but its accepted raw evidence survived.
    publish(offline,previous,{SECOND:seed(offline,previous,SECOND,'limitup')},
            {SECOND:seed(offline,previous,SECOND,'limitdown')})
    current=LocalMarket(tmp_path/'current',previous.quotes,extra_master=True)
    progress=[]
    recovered=supplement.revalidate_for_market(current,SECOND,progress=lambda *args:progress.append(args))
    assert set(recovered['annual_pools'])=={FIRST,SECOND}
    assert set(recovered['annual_down_pools'])=={FIRST,SECOND}
    assert recovered['annual_coverage']['counts']=={UP:2}
    assert recovered['annual_down_coverage']['counts']=={DOWN:2}
    assert recovered['annual_coverage']['complete'] and recovered['annual_down_coverage']['complete']
    assert recovered['revalidation']=={'planned_batches':4,'completed_batches':4,'revalidated_batches':4,'missing_batches':0,'failed_batches':0}
    assert [row[3]['actual_completed_batches'] for row in progress]==[0,1,2,3,4,4]
    assert all(row[3]['actual_total_batches']==4 for row in progress)
    assert supplement.load_supplement(current.status()['snapshot_id'])==recovered
    pointer=offline.root/'accepted/fuyao/limitup'/f'{FIRST}.json'
    first_receipt=pointer.read_bytes()
    again=supplement.revalidate_for_market(current,SECOND)
    assert pointer.read_bytes()==first_receipt
    assert again['annual_pools']==recovered['annual_pools'] and again['annual_down_pools']==recovered['annual_down_pools']


def test_offline_rebuilds_items_from_raw_instead_of_copying_previous_normalization(offline,tmp_path):
    market=LocalMarket(tmp_path/'current',{FIRST:{UP:10.,DOWN:10.}},extra_master=True)
    accepted=seed(offline,market,FIRST,'limitup')
    raw=json.loads((offline.root/accepted['raw_artifact']).read_text())
    offline.accept('fuyao','limitup',FIRST,'different-context',
                   {**accepted,'items':[{'instrument_id':'688827.SH','last_price':999}]},raw)
    result=supplement.revalidate_for_market(market,FIRST)
    assert [row['instrument_id'] for row in result['annual_pools'][FIRST]['items']]==[UP]
    assert result['annual_coverage']['counts']=={UP:1}


@pytest.mark.parametrize('problem',['missing_quote','revised_price','raw_sha','artifact_sha','missing_raw','empty'])
def test_unverifiable_evidence_never_becomes_complete_zero(offline,tmp_path,problem):
    previous=LocalMarket(tmp_path/'old',{FIRST:{UP:10.,DOWN:10.}})
    accepted=seed(offline,previous,FIRST,'limitup')
    if problem=='raw_sha':(offline.root/accepted['raw_artifact']).write_text('{}')
    elif problem=='missing_raw':(offline.root/accepted['raw_artifact']).unlink()
    elif problem=='artifact_sha':
        pointer=json.loads((offline.root/'accepted/fuyao/limitup'/f'{FIRST}.json').read_text())
        (offline.root/pointer['artifact']).write_text('{}')
    elif problem=='empty':
        # A hash-valid old response that declares zero is still not proof of no events.
        offline.accept('fuyao','limitup',FIRST,'old',accepted,[{'data':{'item':[],'pagination':{'total':0}}}])
    current_prices={UP:9. if problem=='revised_price' else 10.,DOWN:10.}
    if problem=='missing_quote':del current_prices[UP]
    current=LocalMarket(tmp_path/'current',{FIRST:current_prices},extra_master=True)
    result=supplement.revalidate_for_market(current,FIRST)
    assert result['quality']=='partial'
    assert not result['annual_coverage']['complete'] and result['annual_coverage']['counts']=={}
    assert result['annual_coverage']['covered_days']==0
    assert result['annual_down_coverage']['covered_days']==0
    assert result['revalidation']['completed_batches']==2
    if problem=='missing_quote':
        record=result['annual_pools'][FIRST]
        assert record['items']==[] and not record['complete'] and record['quarantine']
    else:
        assert FIRST not in result['annual_pools']
        assert result['revalidation']['failed_batches']==1
    assert any('未留存' in failure['reason'] for failure in result['failures'])


@pytest.mark.parametrize('change',['date','pool_kind','source','path','pages'])
def test_raw_loader_checks_context_and_bounded_envelopes(offline,tmp_path,change):
    market=LocalMarket(tmp_path/'old',{FIRST:{UP:10.,DOWN:10.}})
    accepted=seed(offline,market,FIRST,'limitup')
    if change=='path':
        pointer_path=offline.root/'accepted/fuyao/limitup'/f'{FIRST}.json'
        pointer=json.loads(pointer_path.read_text());pointer['artifact']='../outside.json'
        core.atomic_json(pointer_path,pointer)
    elif change=='pages':offline.accept('fuyao','limitup',FIRST,'old',accepted,[{}]*101)
    else:
        record={**accepted,change:{'date':SECOND,'pool_kind':'limitdown','source':'other'}[change]}
        offline.accept('fuyao','limitup',FIRST,'old',record,json.loads((offline.root/accepted['raw_artifact']).read_text()))
    with pytest.raises(ValueError):offline.pool_raw_evidence(FIRST,'limitup')


def test_offline_probes_exactly_two_batches_per_current_year_calendar_date(offline,tmp_path,monkeypatch):
    dates=[];day=date(2026,1,2)
    while len(dates)<191:
        if day.weekday()<5:dates.append(day.isoformat())
        day+=timedelta(days=1)
    quotes={day:{UP:10.,DOWN:10.} for day in ['2025-12-31',*dates]}
    market=LocalMarket(tmp_path/'current',quotes)
    seed(offline,market,dates[0],'limitup',signature='old-context')
    # The accepted namespace may have other years; never enumerate them.
    seed(offline,market,'2025-12-31','limitup',signature='old-context')
    seen=[];original=supplement.SupplementCache.pool_raw_evidence
    def counted(cache,day,kind):seen.append((day,kind));return original(cache,day,kind)
    monkeypatch.setattr(supplement.SupplementCache,'pool_raw_evidence',counted)
    result=supplement.revalidate_for_market(market,dates[-1])
    assert len(seen)==382 and len(set(seen))==382
    assert {day for day,kind in seen}==set(dates)
    assert result['revalidation']['planned_batches']==382 and result['revalidation']['completed_batches']==382
    assert result['revalidation']['revalidated_batches']==1 and result['revalidation']['missing_batches']==381
    assert result['annual_coverage']['counts']=={UP:1} and result['annual_coverage']['expected_days']==191


def test_generation_guard_aborts_atomic_index_publication(offline,tmp_path):
    market=LocalMarket(tmp_path/'current',{FIRST:{UP:10.,DOWN:10.}})
    seed(offline,market,FIRST,'limitup');publish(offline,market)
    head=(offline.root/'head.json').read_bytes()
    def guard():
        # Revalidation may retain accepted chunks, but must not activate a head
        # when a cancellation/generation change happens just before publication.
        if (offline.root/'indexes').is_dir() and len(list((offline.root/'indexes').iterdir()))>1:
            raise RuntimeError('changed before atomic head switch')
    with pytest.raises(RuntimeError):supplement.revalidate_for_market(market,FIRST,guard=guard)
    assert (offline.root/'head.json').read_bytes()==head


def test_daily_increment_recovers_orphan_raw_after_master_change(offline,tmp_path,monkeypatch):
    old=LocalMarket(tmp_path/'old',{FIRST:{UP:10.,DOWN:10.}})
    seed(offline,old,FIRST,'limitup');seed(offline,old,FIRST,'limitdown');publish(offline,old)
    current=LocalMarket(tmp_path/'current',{FIRST:{UP:10.,DOWN:10.},SECOND:{UP:10.,DOWN:10.}},extra_master=True)
    calls=[]
    class Adapter:
        def request(self,path,params):
            assert params['date_ms']==supplement._midnight(SECOND), 'historical repair must stay offline'
            kind='limitup' if path.endswith('limit-up-pool') else 'limitdown';symbol=UP if kind=='limitup' else DOWN
            calls.append(kind)
            return {'data':{'item':[{'ticker':symbol[:6],'thscode':symbol,'last_price':10.}], 'pagination':{'total':1}}}
    class Public:
        def limit_pool(self,day):raise ProviderError('fixture public unavailable')
    monkeypatch.setattr(source_service,'client',lambda *_:Adapter())
    monkeypatch.setattr(supplement,'PublicMarketClient',Public)
    def no_fund(*args,**kwargs):raise ProviderError('fixture fund unavailable')
    monkeypatch.setattr(supplement,'fetch_history_for_market',no_fund)
    result=supplement.refresh_for_market(current,SECOND,year_history=False)
    assert set(result['annual_pools'])=={FIRST,SECOND} and set(result['annual_down_pools'])=={FIRST,SECOND}
    assert result['annual_coverage']['counts']=={UP:2} and calls==['limitup','limitdown']


def test_increment_preserves_in_window_pool_evidence_across_year_boundary(offline,tmp_path,monkeypatch):
    from app.services.retention_policy import VERSION
    from app.services.supplement_policy import FUND_FLOW_RETIRED

    older='2024-12-31';prior='2025-12-31'
    days=pd.bdate_range(older,prior).strftime('%Y-%m-%d').tolist()
    previous=LocalMarket(tmp_path/'previous',{day:{UP:10.,DOWN:10.} for day in days})
    up=seed(offline,previous,prior,'limitup')
    down=seed(offline,previous,prior,'limitdown')
    seed(offline,previous,older,'limitup')
    publish(offline,previous,{prior:up})  # The prior-year down pool exists only in accepted.
    current=LocalMarket(tmp_path/'current',{**previous.quotes,FIRST:{UP:10.,DOWN:10.}},extra_master=True)
    calls=[]

    class Adapter:
        def request(self,path,params):
            assert params['date_ms']==supplement._midnight(FIRST)
            kind='limitdown' if path.endswith('/limit-down-pool') else 'limitup'
            calls.append(kind)
            symbol=DOWN if kind=='limitdown' else UP
            return {'data':{'item':[{'ticker':symbol[:6],'thscode':symbol,'last_price':10.}],
                            'pagination':{'total':1}}}

    class Public:
        def limit_pool(self,day):
            assert day==FIRST
            calls.append('seal_details')
            raise ProviderError('fixture public unavailable')

    with monkeypatch.context() as local:
        local.setattr(source_service,'client',lambda *_:Adapter())
        local.setattr(supplement,'PublicMarketClient',Public)
        result=supplement.refresh_for_market(current,FIRST,year_history=False,
            retention_policy_version=VERSION,fund_flow_policy=FUND_FLOW_RETIRED)
    assert calls==['limitup','limitdown','seal_details']
    assert result['retention']['cutoff_date']>older
    assert result['annual_coverage']['counts']=={UP:1}
    assert result['annual_down_coverage']['counts']=={DOWN:1}
    for field,original in (('annual_pools',up),('annual_down_pools',down)):
        assert set(result[field])=={prior,FIRST}
        for key in ('raw_sha256','fetched_at'):
            assert result[field][prior][key]==original[key]
    # A subsequent same-generation offline repair still processes January only.
    repaired=supplement.revalidate_for_market(current,FIRST)
    assert repaired['revalidation']['planned_batches']==2
    assert repaired['annual_pools'][prior]==result['annual_pools'][prior]
    assert repaired['annual_down_pools'][prior]==result['annual_down_pools'][prior]
    status=supplement.load_supplement_status(current.status()['snapshot_id'])
    rolling=source_service._rolling_pool_coverage(status,current.calendar(),FIRST)
    assert rolling['up']['covered_days']==rolling['down']['covered_days']==2


def test_revalidate_route_never_reads_provider_credentials_and_worker_uses_frozen_scope(isolated,monkeypatch):
    from app.routers import platform
    status={'ready':True,'snapshot_id':'market-fixture','data_end':SECOND}
    market=SimpleNamespace(status=lambda:dict(status))
    monkeypatch.setattr(core,'get_market',lambda:market)
    monkeypatch.setattr(supplement,'get_market',lambda:market)
    monkeypatch.setattr(source_service,'client',lambda *_:pytest.fail('offline API must not inspect credentials'))
    app=FastAPI();app.include_router(platform.router)
    with TestClient(app) as client:
        for invalid in ('true',1,None):
            assert client.post('/api/v1/sync/market-supplement',json={'revalidate_only':invalid}).status_code==422
        response=client.post('/api/v1/sync/market-supplement',json={'revalidate_only':True,'request_key':'offline-fixture'})
    assert response.status_code==200
    task=response.json()
    assert task['parameters']=={'operation':'market_supplement','revalidate_only':True,'year_history':True,'sector_history':False,'recent_days':1,'source_id':'local-cache'}
    assert task['target_date']==SECOND and task['kind']=='source_check'
    monkeypatch.setattr(supplement,'refresh_for_market',lambda *a,**k:pytest.fail('offline job must not call network refresh'))
    calls=[]
    def revalidate(current,target,**kwargs):
        calls.append((current,target));kwargs['guard']()
        # The same mutable market object changing generation must be detected.
        status['snapshot_id']='changed-generation'
        with pytest.raises(ValueError,match='代际'):kwargs['guard']()
        return {'quality':'partial'}
    monkeypatch.setattr(supplement,'revalidate_for_market',revalidate)
    assert supplement.execute_supplement(task['job_id'])=={'quality':'partial'}
    assert calls==[(market,SECOND)]
