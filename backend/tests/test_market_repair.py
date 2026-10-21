"""Failure-oriented contracts for market rank and free-source completeness."""
from datetime import date, timedelta
import json
import httpx
import pytest

from app.adapters.providers import ProviderError
from app.adapters.public_market import PublicMarketClient, normalize_limit_pool
from app.services.market_supplement import SupplementCache, annual_coverage, fetch_fuyao_pool
from app.services.market_service import gainers_rows, dated_events, supplement_status


def public_row(code='600825', price=10000):
    return {'c':code,'m':1,'n':'fixture','p':price,'zbc':0,'lbc':2,'fbt':92500,'lbt':142351,'hs':2.3,'fund':12000,'amount':50000,'hybk':'出版'}


def payload(rows, qdate=20261020):return {'rc':0,'data':{'tc':len(rows),'qdate':qdate,'pool':rows}}


def test_public_pool_anchors_price_and_resolves_beijing_from_master():
    master=[{'instrument_id':'600825.SH','code':'600825'},{'instrument_id':'920006.BJ','code':'920006'}]
    result=normalize_limit_pool(payload([public_row(),public_row('920006')]),'2026-10-19',{'600825.SH':10,'920006.BJ':10},master)
    assert result['complete'] and result['verified_rows']==2
    assert result['items'][1]['instrument_id']=='920006.BJ'
    assert result['items'][0]['first_limit_time']=='09:25:00'
    assert result['items'][0]['last_limit_time']=='14:23:51'
    assert result['provider_qdate']==20261020 and result['date']=='2026-10-19'
    with pytest.raises(ProviderError,match='目标交易日'):
        normalize_limit_pool(payload([public_row(price=11000)]),'2026-10-19',{'600825.SH':10},master)


def test_public_empty_duplicate_and_truncated_are_not_complete_zero():
    for value in [payload([]), payload([public_row(),public_row()]), {'data':{'tc':2,'pool':[public_row()]}}]:
        with pytest.raises(ProviderError):normalize_limit_pool(value,'2026-10-19',{'600825.SH':10},[{'instrument_id':'600825.SH','code':'600825'}])
    result=normalize_limit_pool(payload([public_row(),public_row('600999')]),'2026-10-19',{'600825.SH':10},[{'instrument_id':'600825.SH','code':'600825'}])
    assert not result['complete'] and len(result['quarantine'])==1


def test_public_http_bounds_retry_and_fixed_hosts():
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(503 if len(calls)<3 else 200,json=payload([public_row()]))
    client=PublicMarketClient(httpx.MockTransport(handler),sleep=lambda _:None)
    assert client.limit_pool('2026-10-19')['data']['tc']==1 and len(calls)==3
    with pytest.raises(ProviderError):client.request('arbitrary','/endpoint',{})
    with pytest.raises(ProviderError):client.request('eastmoney_pool','https://other.example/',{})


def test_cache_only_reuses_complete_matching_checked_artifacts(tmp_path):
    cache=SupplementCache(tmp_path)
    row=cache.accept('eastmoney','limitup','2026-10-19','signature',{'complete':True,'items':[{'id':'fixture'}]},{'fixture':'raw'})
    assert cache.reuse('eastmoney','limitup','2026-10-19','signature')==row
    assert cache.reuse('eastmoney','limitup','2026-10-19','changed') is None
    marker=json.loads((tmp_path/'accepted/eastmoney/limitup/2026-10-19.json').read_text())
    (tmp_path/marker['artifact']).write_text('{}')
    with pytest.raises(ValueError,match='缓存损坏'):cache.reuse('eastmoney','limitup','2026-10-19','signature')
    cache.accept('fuyao','limitup','2026-10-19','partial',{'complete':False,'items':[]},{})
    assert cache.reuse('fuyao','limitup','2026-10-19','partial') is None


def test_annual_pool_validates_all_pages_and_quarantines_unidentified():
    class Adapter:
        def request(self,path,params):
            rows=[{'thscode':'600825.SH','ticker':'600825','last_price':10}] if params['page']==1 else [{'thscode':None,'ticker':None,'last_price':5}]
            return {'data':{'item':rows,'pagination':{'total':2,'page':params['page'],'pages':2}}}
    result,_=fetch_fuyao_pool(Adapter(),'2026-01-05',{'600825.SH':10},[{'instrument_id':'600825.SH'}])
    assert result['verified_rows']==1 and not result['complete'] and len(result['quarantine'])==1
    class Bad(Adapter):
        def request(self,path,params):
            return {'data':{'item':[{'thscode':'600825.SH','last_price':float('nan')}],'pagination':{'total':1}}}
    with pytest.raises(ProviderError):fetch_fuyao_pool(Bad(),'2026-01-05',{'600825.SH':10},[{'instrument_id':'600825.SH'}])


def test_partial_annual_count_keeps_confirmed_lower_bound():
    calendar=['2026-01-05','2026-01-06','2026-01-07']
    doc={'annual_pools':{'2026-01-05':{'complete':True,'items':[{'instrument_id':'600825.SH'}]},'2026-01-06':{'complete':False,'items':[{'instrument_id':'600825.SH'}]}}}
    result=annual_coverage(doc,calendar,'2026-01-07')
    assert result['counts']['600825.SH']==2
    assert not result['complete'] and result['covered_days']==1 and result['observed_days']==2 and result['expected_days']==3
    assert annual_coverage(doc,calendar,'2026-01-05')['counts']['600825.SH']==1


def test_rank_uses_market_endpoints_despite_middle_quote_gap():
    calendar=[];d=date(2026,7,1)
    while len(calendar)<40:
        if d.weekday()<5:calendar.append(d.isoformat())
        d+=timedelta(days=1)
    class Market:
        def calendar(self):return calendar
        def history_panel(self,*args,**kwargs):
            return {'600825.SH':[{'date':day,'close':10+i,'open':10+i,'high':10+i,'low':10+i} for i,day in enumerate(calendar) if i not in (15,16)]}
        def actions(self,*args):return []
        def instruments(self,**kwargs):return [{'instrument_id':'600825.SH','code':'600825','name':'新华传媒','kind':'stock'},{'instrument_id':'600999.SH','code':'600999','name':'缺数股','kind':'stock'}]
    rows,summary=gainers_rows(Market(),{},calendar[-1],[{'instrument_id':'600825.SH','code':'600825','name':'新华传媒','return_30d':None}])
    assert rows[0]['rank']==1 and rows[0]['return_30d']==pytest.approx((49/19-1)*100)
    assert rows[0]['return_30d_missing_sessions']==2 and rows[0]['return_30d_start']==calendar[9]
    assert rows[1]['rank'] is None and '未留存报价' in rows[1]['return_30d_reason']
    assert summary['unknown_count']==1 and summary['with_intermediate_gaps']==1


def test_overlay_preserves_source_pool_and_adds_only_verified_details():
    reference={'pool_trade_date':'2026-10-19','limitup':[{'thscode':'600825.SH','continue_day_cnt':5}], '_supplement':{'public_pools':{'2026-10-19':{'items':[{'instrument_id':'600825.SH','first_limit_time':'09:25:00','last_limit_time':'09:28:00','break_count':1,'turnover_rate':5,'source':'eastmoney-public'}]}}}}
    rows=dated_events(reference,'2026-10-19')['limitup']
    assert rows[0]['continue_day_cnt']==5 and rows[0]['first_limit_time']=='09:25:00'
    assert 'first_limit_time' not in reference['limitup'][0]
    assert not dated_events(reference,'2026-10-16')
    assert supplement_status({'_supplement':{'failures':[{'date':'2026-10-19','capability':'fund_flow','reason':'fixture failed'}]}},'2026-10-19','fund_flow')['state']=='failed'


def test_flow_pagination_checks_every_topic_and_timestamp():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    stamp=int(datetime(2026,10,19,15,0,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
    seen=[]
    def handler(request):
        q=request.url.params;page=int(q['pn']);kind=2 if q['fs'].endswith('2') else 3
        seen.append((kind,page));offset=(page-1)*100
        count=100 if page==1 else 1
        return httpx.Response(200,json={'rc':0,'data':{'total':101,'diff':[{'f12':f'BK{kind*1000+i:04d}','f14':'fixture','f3':-1,'f62':-500,'f184':-2,'f124':stamp} for i in range(offset,offset+count)]}})
    result=PublicMarketClient(httpx.MockTransport(handler),sleep=lambda _:None).sector_flow('2026-10-19')
    assert len(result['items'])==202 and result['complete'] and len(seen)==4
    assert result['items'][0]['net_inflow']==-500 and not result['intraday_sequence_available']
    with pytest.raises(ProviderError,match='目标盘面日'):
        PublicMarketClient(httpx.MockTransport(handler),sleep=lambda _:None).sector_flow('2026-10-16')


def test_cancel_before_supplement_publication_leaves_previous_head(tmp_path):
    cache=SupplementCache(tmp_path)
    doc={'version':'market-supplement-v1','snapshot_id':'market-fixture'}
    cache.publish(doc)
    old=(tmp_path/'head.json').read_bytes()
    def guard():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError):cache.publish({**doc,'revision':2},guard)
    assert (tmp_path/'head.json').read_bytes()==old


def test_cross_generation_increment_revalidates_cache_without_old_remote_calls(tmp_path,monkeypatch):
    import pandas as pd
    from app.services import market_supplement as module, source_service
    root=tmp_path/'supplement'
    monkeypatch.setattr(module,'_root',lambda:root)
    source_calls={"limit-up-pool":[],"limit-down-pool":[]}; public_calls=[]
    class Adapter:
        def request(self,path,params):
            day='2026-01-05' if params['date_ms']==module._midnight('2026-01-05') else '2026-01-06'
            capability=path.rsplit('/',1)[-1];source_calls[capability].append(day)
            symbol='600825.SH' if capability=='limit-up-pool' else '000001.SZ'
            return {'data':{'item':[{'thscode':symbol,'ticker':symbol[:6],'last_price':10 if day.endswith('05') else 11}],'pagination':{'total':1}}}
    monkeypatch.setattr(source_service,'client',lambda *_:Adapter())
    class Public:
        def limit_pool(self,day):
            public_calls.append(day)
            return payload([public_row(price=10000 if day.endswith('05') else 11000)])
        def sector_flow(self,day):raise ProviderError('fixture unavailable')
    monkeypatch.setattr(module,'PublicMarketClient',Public)
    def unavailable(*args,**kwargs):raise ProviderError('fixture unavailable')
    monkeypatch.setattr(module,'fetch_history_for_market',unavailable)
    class Market:
        def __init__(self,label,quotes):
            self.label=label;self.quotes=quotes;self.path=tmp_path/label;self.path.mkdir()
            pd.DataFrame([{'instrument_id':symbol,'date':d,'close':p} for d,p in quotes for symbol in ('600825.SH','000001.SZ')]).to_parquet(self.path/'daily.parquet')
        def status(self):return {'snapshot_id':self.label,'data_end':self.quotes[-1][0],'path':str(self.path)}
        def calendar(self):return [d for d,p in self.quotes]
        def instruments(self,**kwargs):return [{'instrument_id':symbol,'code':symbol[:6],'kind':'stock'} for symbol in ('600825.SH','000001.SZ')]
    first=Market('market-a',[('2026-01-05',10)])
    module.refresh_for_market(first,'2026-01-05',year_history=True,recent_days=1)
    second=Market('market-b',[('2026-01-05',10),('2026-01-06',11)])
    result=module.refresh_for_market(second,'2026-01-06',year_history=False,recent_days=1)
    assert set(result['annual_pools'])=={'2026-01-05','2026-01-06'}
    assert result['annual_coverage']['counts']['600825.SH']==2 and result['annual_coverage']['complete']
    assert source_calls=={'limit-up-pool':['2026-01-05','2026-01-06'],'limit-down-pool':['2026-01-05','2026-01-06']} and public_calls==source_calls['limit-up-pool']
    assert set(result['annual_down_pools'])=={'2026-01-05','2026-01-06'}
    third=Market('market-c',[('2026-01-05',9),('2026-01-06',11)])
    result=module.refresh_for_market(third,'2026-01-06',year_history=False,recent_days=1)
    assert set(result['annual_pools'])=={'2026-01-06'}
    assert not result['annual_coverage']['complete']
    assert source_calls=={'limit-up-pool':['2026-01-05','2026-01-06'],'limit-down-pool':['2026-01-05','2026-01-06']}
    assert any('签名变化' in r['reason'] for r in result['failures'])


def test_cached_annual_pages_can_resolve_explicit_ticker_without_network():
    class Adapter:
        def request(self,*args,**kwargs):raise AssertionError('offline revalidation must not fetch')
    raw=[{'data':{'item':[{'thscode':None,'ticker':'603261','last_price':25.36}],'pagination':{'total':1}}}]
    result,_=fetch_fuyao_pool(Adapter(),'2026-01-05',{'603261.SH':25.36},[{'instrument_id':'603261.SH','code':'603261'}],payload_pages=raw)
    assert result['complete'] and result['items'][0]['instrument_id']=='603261.SH'
    from app.services.limit_history import NORMALIZATION_VERSION
    assert result['normalization_version']==NORMALIZATION_VERSION
    with pytest.raises(ProviderError):fetch_fuyao_pool(Adapter(),'2026-01-05',{'603261.SH':25.36},[{'instrument_id':'603261.SH','code':'603261'}],payload_pages=raw+raw)


def test_other_retired_code_does_not_make_this_instrument_annual_count_unknown():
    doc={'annual_pools':{'2026-01-05':{'complete':False,'items':[{'instrument_id':'600825.SH'}],'pagination_complete':True,'raw_codes':['600825','300029'],'ambiguous_code_rows':0,'rejected_codes':['300029']},'2026-01-06':{'complete':False,'items':[],'pagination_complete':True,'raw_codes':['300029'],'ambiguous_code_rows':0,'rejected_codes':['300029']}}}
    result=annual_coverage(doc,['2026-01-05','2026-01-06'],'2026-01-06',instruments=['600825.SH','300029.SZ'])
    assert not result['complete'] and result['covered_days']==0
    assert result['counts']['600825.SH']==1
    assert result['instrument_coverage']['600825.SH']['complete']
    assert not result['instrument_coverage']['300029.SZ']['complete']
    doc['annual_pools']['2026-01-06']['ambiguous_code_rows']=1
    result=annual_coverage(doc,['2026-01-05','2026-01-06'],'2026-01-06',instruments=['600825.SH'])
    assert not result['instrument_coverage']['600825.SH']['complete']


def test_historical_flow_uses_only_explicit_daily_history():
    from app.services.market_service import fund_flow_for_date
    doc={'fund_flows':{'2026-10-19':{'items':[{'date':'2026-10-19','net_inflow':999}],'history':[{'date':'2026-10-16','net_inflow':-8,'topic_code':'BK1034'},{'date':'2026-10-19','net_inflow':999,'topic_code':'BK1034'}],'coverage':{'directory_count':1}}}}
    result=fund_flow_for_date(doc,'2026-10-16')
    assert result['items'][0]['net_inflow']==-8 and len(result['history'])==1
    assert result['date']=='2026-10-16' and result['coverage']['complete']
    assert not fund_flow_for_date(doc,'2026-10-15')
    assert doc['fund_flows']['2026-10-19']['items'][0]['net_inflow']==999


def _fund_directory_fixture(count):
    links=''.join(f'<a href="/bkzj/BK{1000+i:04d}.html">行业{i}</a>' for i in range(count))
    return '<ul class="pop_slider"><li>行业</li><li>概念</li><li>地域</li></ul><div class="pop-cont">'+links+'</div><div class="pop-cont"></div><div class="pop-cont"></div>'


def _fund_retry_calendar():
    days=[];day=date(2026,1,5)
    while len(days)<20:
        if day.weekday()<5:days.append(day.isoformat())
        day+=timedelta(days=1)
    return days


def _fund_history_fixture(code,days):
    return {'rc':0,'data':{'code':code,'market':90,'name':'fixture','klines':[','.join([day,'10','-4','-6','2','8','1','-1','-1','1','1','10','0.2']) for day in days]}}


def test_same_task_second_fund_round_reuses_success_and_recovers_failed_code(tmp_path):
    from collections import Counter
    from app.services.market_supplement import fetch_history_for_market
    days=_fund_retry_calendar();target=days[-1];calls=Counter();progress=[];saved=[]
    class Market:
        def calendar(self):return days
    def handler(request):
        if request.url.host=='data.eastmoney.com':
            calls['directory']+=1
            return httpx.Response(200,text=_fund_directory_fixture(3))
        code=request.url.params['secid'].split('.')[1];calls[code]+=1
        if code=='BK1001' and calls[code]<=3:return httpx.Response(503,json={})
        return httpx.Response(200,json=_fund_history_fixture(code,days))
    cache=SupplementCache(tmp_path);original_accept=cache.accept
    def accept(*args,**kwargs):
        value=original_accept(*args,**kwargs);saved.append((args[2],value['fetched_at']));return value
    cache.accept=accept
    client=PublicMarketClient(httpx.MockTransport(handler),sleep=lambda _:None)
    result=fetch_history_for_market(client,Market(),target,cache,progress=lambda *args:progress.append(args))
    assert result['complete'] and len(result['items'])==3
    assert result['coverage']['round_count']==2 and result['coverage']['logical_request_count']==6
    assert result['rounds'][0]['failed_count']==1 and result['rounds'][1]['failed_count']==0
    assert result['rounds'][1]['cached_count']==2
    assert calls=={'directory':2,'BK1000':1,'BK1001':4,'BK1002':1}
    assert len(saved)==3 and len({key for key,stamp in saved})==3
    assert any('第2轮补缺' in row[2] for row in progress)
    assert len(result['raw_pages'])==2 and result['raw_pages'][0]['failures'] and not result['raw_pages'][1]['failures']


def test_permanent_fund_failure_stops_at_two_rounds_and_shared_200_budget(tmp_path):
    from collections import Counter
    from app.services.market_supplement import fetch_history_for_market
    days=_fund_retry_calendar();calls=Counter()
    class Market:
        def calendar(self):return days
    def handler(request):
        if request.url.host=='data.eastmoney.com':
            calls['directory']+=1
            return httpx.Response(200,text=_fund_directory_fixture(128))
        calls['history_attempts']+=1
        return httpx.Response(503,json={})
    client=PublicMarketClient(httpx.MockTransport(handler),sleep=lambda _:None)
    result=fetch_history_for_market(client,Market(),days[-1],SupplementCache(tmp_path))
    assert not result['complete'] and result['items']==[]
    assert result['coverage']['round_count']==2 and calls['directory']==2
    assert result['coverage']['logical_request_count']==200
    assert [r['logical_request_count'] for r in result['rounds']]==[129,71]
    assert result['rounds'][1]['budget_remaining']==0
    assert calls['history_attempts']==594 and sum(calls.values())==596
    assert result['coverage']['unrequested_count']==58 and len(result['failures'])==128
    assert any(r['stage']=='request_budget' for r in result['failures'])
    assert result['coverage']['max_logical_requests']==200 and result['coverage']['max_transport_attempts']==600


def test_historical_fund_coverage_recomputes_19_of_20_for_the_view_date():
    from app.services.market_service import fund_flow_for_date
    calendar=_fund_retry_calendar()+['2026-02-02']
    latest=calendar[-1];view=calendar[-2]
    rows=[{'date':day,'topic_code':'BK1000','net_inflow':1} for day in calendar[-20:]]
    doc={'fund_flows':{latest:{'items':[rows[-1]],'history':rows,'coverage':{'directory_count':1,'accepted_count':1,'failed_count':0,'complete':True,'history_20d_complete':True,'history_complete_count':1}}}}
    current=fund_flow_for_date(doc,latest,calendar)
    assert current['coverage']['history_20d_complete'] and current['coverage']['history_complete_count']==1
    historical=fund_flow_for_date(doc,view,calendar)
    assert len(historical['history'])==19 and historical['coverage']['history_observed_days']==19
    assert historical['coverage']['history_window_expected_days']==20
    assert not historical['coverage']['history_20d_complete'] and historical['coverage']['history_complete_count']==0
    assert historical['coverage']['history_gaps'][0]['dates']==[calendar[0]]
    assert historical['coverage']['complete']  # Target-day directory still complete.
    assert doc['fund_flows'][latest]['coverage']['history_20d_complete']  # Saved evidence untouched.


def test_historical_market_events_reuse_verified_down_supplement_without_replacing_base():
    from app.services.market_service import dated_events
    down={'thscode':'603999.SH','last_price':7.54,'date':'2026-10-19'}
    reference={'_supplement':{'annual_down_pools':{'2026-10-19':{'items':[down]}}}}
    assert dated_events(reference,'2026-10-19')['limitdown']==[down]
    assert 'breakpool' not in dated_events(reference,'2026-10-19')
    reference.update(pool_trade_date='2026-10-19',limitdown=[{'thscode':'base-priority'}])
    assert dated_events(reference,'2026-10-19')['limitdown']==[{'thscode':'base-priority'}]
