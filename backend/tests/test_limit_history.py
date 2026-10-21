"""Historical down pools: verified source evidence, unknown gaps and resumability."""
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from app.adapters.providers import ProviderError
from app.services.limit_history import fetch_fuyao_pool, annual_coverage, _midnight, NORMALIZATION_VERSION
from app.services.market_supplement import SupplementCache

A='600001.SH';B='000001.SZ'
MASTER=[{'instrument_id':A,'code':'600001','kind':'stock','name':'甲'}, {'instrument_id':B,'code':'000001','kind':'stock','name':'乙'}]


def envelope(records,total=None):
    return {'code':0,'data':{'item':records,'pagination':{'total':len(records) if total is None else total}}}


def event(symbol=A,price=10,**kwargs):
    return {'thscode':symbol,'ticker':symbol[:6] if symbol else None,'last_price':price,**kwargs}


def test_up_default_and_down_endpoint_are_explicit_and_use_raw_price_only():
    calls=[]
    class Adapter:
        def request(self,path,params):
            calls.append((path,params))
            return envelope([event(A,10,price_change_ratio_pct=-4.9)])
    adapter=Adapter()
    up,_=fetch_fuyao_pool(adapter,'2026-01-05',{A:10},MASTER)
    down,_=fetch_fuyao_pool(adapter,'2026-01-05',{A:10},MASTER,pool_kind='limitdown')
    assert calls[0][0].endswith('/limit-up-pool') and calls[1][0].endswith('/limit-down-pool')
    assert up['pool_kind']=='limitup' and down['pool_kind']=='limitdown'
    assert down['items'][0]['pool_kind']=='limitdown' and down['items'][0]['date']=='2026-01-05'
    assert down['complete'] and down['items'][0]['price_change_ratio_pct']==-4.9
    assert calls[1][1]['date_ms']==_midnight('2026-01-05')
    with pytest.raises(ValueError):fetch_fuyao_pool(adapter,'2026-01-05',{A:10},MASTER,pool_kind='guessed')
    assert len(calls)==2


def test_down_full_pagination_and_ticker_fallback_keep_retired_codes_separate():
    class Adapter:
        def request(self,path,params):
            assert path.endswith('/limit-down-pool')
            row={'thscode':None,'ticker':'600001','last_price':10} if params['page']==1 else {'thscode':None,'ticker':'300029','last_price':5}
            return envelope([row],2)
    record,pages=fetch_fuyao_pool(Adapter(),'2026-01-05',{A:10},MASTER,pool_kind='limitdown')
    assert len(pages)==2 and record['declared_total']==2 and record['pagination_complete']
    assert record['items'][0]['instrument_id']==A and record['verified_rows']==1
    assert not record['complete'] and record['raw_codes']==['300029','600001']
    assert record['rejected_codes']==['300029'] and record['ambiguous_code_rows']==0


@pytest.mark.parametrize('pool_kind',['limitup','limitdown'])
def test_explicit_source_date_conflict_is_quarantined_even_when_raw_close_matches(pool_kind):
    payload=envelope([event(A,10,date='2026-01-06'),event(B,10,date='2026-01-05')])
    original=deepcopy(payload)
    record,pages=fetch_fuyao_pool(None,'2026-01-05',{A:10,B:10},MASTER,pool_kind=pool_kind,payload_pages=[payload])
    assert not record['complete'] and record['verified_rows']==1
    assert [row['instrument_id'] for row in record['items']]==[B]
    assert record['rejected_codes']==['600001']
    assert record['quarantine']==[{'code':'600001','reason':'来源明确日期与目标交易日冲突','source_date':'2026-01-06','requested_date':'2026-01-05'}]
    assert pages==[original] and payload==original
    document={('annual_pools' if pool_kind=='limitup' else 'annual_down_pools'):{'2026-01-05':record}}
    coverage=annual_coverage(document,['2026-01-05'],'2026-01-05',MASTER,pool_kind=pool_kind)
    assert A not in coverage['counts'] and not coverage['instrument_coverage'][A]['complete']
    assert coverage['instrument_coverage'][B]['complete']


@pytest.mark.parametrize('payload,closes,match',[
    (envelope([]),{A:10},'返回空'),
    (envelope([event(A,11)]),{A:10},'价格与目标交易日'),
    (envelope([event(A,float('nan'))]),{A:10},'价格与目标交易日'),
    (envelope([event(),event()]),{A:10},'代码重复'),
    (envelope([event()],2),{A:10},'分页'),
])
def test_down_rejected_response_is_unknown_and_retains_raw_evidence(payload,closes,match):
    with pytest.raises(ProviderError,match=match) as caught:
        fetch_fuyao_pool(None,'2026-01-05',closes,MASTER,pool_kind='limitdown',payload_pages=[payload])
    assert caught.value.pool_kind=='limitdown' and caught.value.raw_pages==[payload]


def test_down_pagination_total_change_fails_with_both_observed_pages():
    class Adapter:
        def request(self,path,params):
            return envelope([event(A if params['page']==1 else B)],2 if params['page']==1 else 3)
    with pytest.raises(ProviderError,match='总量变化') as caught:
        fetch_fuyao_pool(Adapter(),'2026-01-05',{A:10,B:10},MASTER,pool_kind='limitdown')
    assert len(caught.value.raw_pages)==2


def test_down_coverage_is_independent_of_up_and_prior_year_stays_out_of_scope():
    def normalized(day,records,closes):
        return fetch_fuyao_pool(None,day,closes,MASTER,pool_kind='limitdown',payload_pages=[envelope(records)])[0]
    first=normalized('2026-01-05',[event(A),event('300029.SZ',5)],{A:10})
    second=normalized('2026-01-06',[event('300029.SZ',5)],{})
    doc={'annual_pools':{'2026-01-05':{'complete':True,'items':[{'instrument_id':B}]}},'annual_down_pools':{'2025-12-31':first,'2026-01-05':first,'2026-01-06':second}}
    calendar=['2025-12-31','2026-01-05','2026-01-06']
    up=annual_coverage(doc,calendar,'2026-01-06')
    down=annual_coverage(doc,calendar,'2026-01-06',instruments=[A,'300029.SZ'],pool_kind='limitdown')
    assert up['counts']=={B:1} and down['counts']=={A:1}
    assert down['observed_days']==down['expected_days']==2 and down['covered_days']==0
    assert down['instrument_coverage'][A]['complete']
    assert not down['instrument_coverage']['300029.SZ']['complete']
    assert down['expected_begin']=='2026-01-05' and down['continuous_begin'] is None and '上一年' in down['history_scope']
    second['ambiguous_code_rows']=1
    assert not annual_coverage(doc,calendar,'2026-01-06',[A],pool_kind='limitdown')['instrument_coverage'][A]['complete']


def test_down_cache_sha_checks_and_offline_revalidation_preserve_fetch_time(tmp_path):
    cache=SupplementCache(tmp_path);raw=[envelope([event()])]
    result,_=fetch_fuyao_pool(None,'2026-01-05',{A:10},MASTER,payload_pages=raw,pool_kind='limitdown')
    result['fetched_at']='2026-10-20T12:00:00+08:00'
    original=cache.accept('fuyao','limitdown','2026-01-05','fixture-signature',result,raw)
    reused=cache.reuse('fuyao','limitdown','2026-01-05','fixture-signature')
    assert reused['fetched_at']==original['fetched_at']
    assert cache.reuse('fuyao','limitdown','2026-01-05','changed-quote-signature') is None
    assert cache.reuse('fuyao','limitup','2026-01-05','fixture-signature') is None
    (tmp_path/reused['raw_artifact']).write_text('{}')
    with pytest.raises(ValueError,match='原始证据缓存'):
        cache.reuse('fuyao','limitdown','2026-01-05','fixture-signature')
    with pytest.raises(ValueError,match='原始证据对象已损坏'):
        cache.accept('fuyao','limitdown','2026-01-05','fixture-signature',result,raw)


@pytest.fixture
def integration(tmp_path,monkeypatch):
    from app.services import market_supplement as module, source_service
    root=tmp_path/'supplement';monkeypatch.setattr(module,'_root',lambda:root)
    calls=[];behavior={'empty_down':False}
    class Adapter:
        def request(self,path,params):
            kind='limitdown' if path.endswith('/limit-down-pool') else 'limitup'
            day=next(day for day in ('2026-01-05','2026-01-06','2026-01-07') if params['date_ms']==_midnight(day))
            calls.append((kind,day));symbol=A if kind=='limitup' else B
            return envelope([] if kind=='limitdown' and behavior['empty_down'] else [event(symbol,10)])
    monkeypatch.setattr(source_service,'client',lambda *_:Adapter())
    class Public:
        def limit_pool(self,day):raise ProviderError('fixture-public-unavailable')
    monkeypatch.setattr(module,'PublicMarketClient',Public)
    def no_funds(*args,**kwargs):raise ProviderError('fixture-funds-unavailable')
    monkeypatch.setattr(module,'fetch_history_for_market',no_funds)
    class Market:
        def __init__(self,label,days,changed=False):
            self.label=label;self.days=days;self.path=tmp_path/label;self.path.mkdir()
            pd.DataFrame([{'instrument_id':symbol,'date':day,'close':99 if changed and day==days[0] and symbol==A else 10} for day in days for symbol in (A,B)]).to_parquet(self.path/'daily.parquet')
        def status(self):return {'snapshot_id':self.label,'data_end':self.days[-1],'path':str(self.path)}
        def calendar(self):return list(self.days)
        def instruments(self,**kwargs):return deepcopy(MASTER)
    return module,root,calls,behavior,Market


def test_annual_both_pools_then_increment_only_new_day_and_quote_signature_guard(integration):
    module,root,calls,behavior,Market=integration
    first=Market('market-first',['2026-01-05','2026-01-06'])
    doc=module.refresh_for_market(first,'2026-01-06',year_history=True)
    assert set(doc['annual_pools'])==set(doc['annual_down_pools'])==set(first.days)
    assert set(calls)=={(kind,day) for kind in ('limitup','limitdown') for day in first.days} and len(calls)==4
    assert doc['annual_coverage']['counts']=={A:2} and doc['annual_down_coverage']['counts']=={B:2}
    fetched=doc['annual_down_pools']['2026-01-05']['fetched_at'];raw_sha=doc['annual_down_pools']['2026-01-05']['raw_sha256']
    second=Market('market-second',first.days+['2026-01-07'])
    doc=module.refresh_for_market(second,'2026-01-07',year_history=False)
    assert calls[-2:]==[('limitup','2026-01-07'),('limitdown','2026-01-07')] and len(calls)==6
    assert doc['annual_down_coverage']['complete'] and doc['annual_down_coverage']['counts']=={B:3}
    assert doc['annual_down_pools']['2026-01-05']['fetched_at']==fetched and doc['annual_down_pools']['2026-01-05']['raw_sha256']==raw_sha
    third=Market('market-revised',second.days,changed=True)
    doc=module.refresh_for_market(third,'2026-01-07',year_history=False)
    assert len(calls)==6 and '2026-01-05' not in doc['annual_down_pools']
    assert not doc['annual_down_coverage']['complete'] and '2026-01-05' in doc['annual_down_coverage']['missing_dates']
    assert any(r['capability']=='year_limitdown' and '签名变化' in r['reason'] for r in doc['failures'])


def test_increment_revalidates_legacy_inherited_date_conflict_offline(integration):
    module,root,calls,behavior,Market=integration
    first=Market('market-legacy',['2026-01-05','2026-01-06'])
    doc=module.refresh_for_market(first,'2026-01-06',year_history=True)
    cache=SupplementCache(root);day='2026-01-05'
    pointer=json.loads((root/'accepted'/'fuyao'/'limitdown'/(day+'.json')).read_text())
    legacy=dict(doc['annual_down_pools'][day]);fetched=legacy['fetched_at']
    legacy['normalization_version']='explicit-ticker-coverage-v3'
    conflicting=envelope([event(B,10,date='2026-01-06')]);original=deepcopy(conflicting)
    legacy=cache.accept('fuyao','limitdown',day,pointer['signature'],legacy,[conflicting])
    doc['annual_down_pools'][day]=legacy;cache.publish(doc)
    second=Market('market-revalidated',first.days+['2026-01-07'])
    refreshed=module.refresh_for_market(second,'2026-01-07',year_history=False)
    inherited=refreshed['annual_down_pools'][day]
    assert calls[-2:]==[('limitup','2026-01-07'),('limitdown','2026-01-07')] and len(calls)==6
    assert inherited['normalization_version']==NORMALIZATION_VERSION
    assert inherited['fetched_at']==fetched and inherited['raw_sha256']==legacy['raw_sha256']
    assert inherited['validated_at'] and not inherited['complete'] and inherited['items']==[]
    assert inherited['quarantine'][0]['source_date']=='2026-01-06'
    assert json.loads((root/inherited['raw_artifact']).read_text())==[original]
    assert refreshed['annual_down_coverage']['counts']=={B:2}
    assert not refreshed['annual_down_coverage']['complete']


def test_empty_down_day_keeps_unknown_failure_raw_then_manual_retry_only_missing(integration):
    module,root,calls,behavior,Market=integration;market=Market('market-empty',['2026-01-05']);behavior['empty_down']=True
    first=module.refresh_for_market(market,'2026-01-05',year_history=True)
    assert first['annual_down_pools']=={} and first['annual_down_coverage']['observed_days']==0
    assert first['annual_down_coverage']['missing_dates']==['2026-01-05']
    failure=next(r for r in first['failures'] if r['capability']=='year_limitdown')
    assert failure['raw_sha256'] and (root/failure['raw_artifact']).is_file()
    assert '返回空' in failure['reason']
    behavior['empty_down']=False
    second=module.refresh_for_market(market,'2026-01-05',year_history=True)
    assert calls==[('limitup','2026-01-05'),('limitdown','2026-01-05'),('limitdown','2026-01-05')]
    assert second['annual_down_coverage']['complete'] and second['annual_down_coverage']['counts']=={B:1}
