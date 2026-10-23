"""Manual research, disclosed dates, local monthly warm-up and scoped TTL."""
from copy import deepcopy
from datetime import datetime,timezone
import json
from urllib.parse import parse_qs
import httpx
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient

from app import core
from app.adapters.providers import ProviderError
from app.adapters.research_public import ResearchPublicClient
from app.services import research_cache,research_materials,research_service
from app.services.research_trends import build_trends,aggregate_monthly
from test_workspace import ws

SYMBOL='600001.SH'


def ms(day):return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000)


class Adapter:
    key='fixture-only'
    def request(self,path,params):
        if 'valuations' in path:return {'code':0,'data':{'timestamp':ms('2026-01-10'),'item':[{'thscode':SYMBOL,'pe_ttm':-5,'pe_mrq':None,'pb_mrq':2,'ps_ttm':3,'pcf_ttm':4}]}}
        if path.endswith('/indicators'):return {'code':0,'data':{'thscode':SYMBOL,'report':params['report'],'abilities':[{'indicators':[{'index_id':'net_profit_yoy_growth_ratio','value':'-12.50'}]}]}}
        return {'code':0,'data':{'timestamp':ms('2025-12-31'),'item':[{'thscode':SYMBOL,'currency':'CNY','period_end_ms':ms('2025-12-31'),'report_date_ms':ms('2026-01-10'),'fiscal_year':2025,'fiscal_period':'Q4','operating_income':100000000,'parent_holder_net_profit':-1000,'net_profit':None,'basic_eps':-.1,'assets_total':200000000,'total_debt':100000000,'holder_equity_total':100000000,'act_cash_flow_net':3000}]}}


class Public:
    def announcements(self,symbol,start,end):
        return {'items':[{'announcement_id':'fixture','title':'关于回购股份进展的公告','published_date':'2026-01-10','published_at_ms':ms('2026-01-10'),'source':'cninfo','url':'https://www.cninfo.com.cn/fixture','raw':{'title':'fixture'}}],'coverage':{'complete':True,'declared_total':1},'raw_pages':[{'fixture':True}]}
    def shareholders(self,symbol,as_of):return {'items':[{'holder_count':123,'report_period':'2025-12-31','published_date':'2026-01-10'}],'coverage':{'complete':True}}
    def market_cap(self,symbol,as_of):return {'total_market_cap_cny':100000000,'source_date':'2026-01-10','source':'eastmoney'}


@pytest.fixture
def research(ws,monkeypatch):
    market,_=ws
    monkeypatch.setattr(market,'instrument',lambda symbol:next(row for row in market.instruments() if row['instrument_id']==symbol),raising=False)
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'fixture-research-market'},raising=False)
    monkeypatch.setattr(market,'topics_data',market.topics,raising=False)
    monkeypatch.setattr(research_service,'get_market',lambda:market)
    monkeypatch.setattr(research_service,'client',lambda identifier:Adapter())
    monkeypatch.setattr(research_materials,'ResearchPublicClient',Public)
    from app.routers.platform import router
    app=FastAPI();app.include_router(router)
    return market,TestClient(app)


def test_only_explicit_manual_job_fetches_and_report_has_real_material_sections(research,monkeypatch):
    market,client=research;calls=[];original=Adapter.request
    def request(self,*args):calls.append(args[0]);return original(self,*args)
    monkeypatch.setattr(Adapter,'request',request)
    assert client.get('/api/v1/research/jobs').json()['items']==[] and calls==[]
    assert client.post('/api/v1/research/jobs',json={}).status_code==422 and calls==[]
    queued=client.post('/api/v1/research/jobs',json={'instrument_id':SYMBOL}).json()
    assert queued['status']=='queued' and calls==[]
    report=research_service.execute_research(queued['job_id'])
    assert len(calls)==5 and report['valuation']['pe_ttm']==-5
    assert report['valuation']['pe_mrq'] is None
    assert report['financials']['statements']['income'][0]['parent_holder_net_profit']==-1000
    assert report['financials']['metrics'][0]['value']==-12.5
    assert report['events'][0]['categories']==['回购']
    assert {trend['period'] for trend in report['trends']}=={'D','W','M'}
    monthly=next(trend for trend in report['trends'] if trend['period']=='M')
    assert monthly['observations']['macd_hist'] is None and monthly['observations']['kdj_k'] is None
    assert monthly['warnings'] and report['materials'] and report['cache']['ttl_days']==7
    assert client.get('/api/v1/tasks/'+queued['job_id']).status_code==200


def test_unavailable_materials_are_gaps_not_filled_numbers(monkeypatch):
    class FailedPublic:
        def __getattr__(self,name):
            def fail(*args):raise ProviderError('fixture unavailable')
            return fail
    result=research_materials.collect_materials(SYMBOL,None,public=FailedPublic(),as_of_ms=ms('2026-10-20'))
    assert result['valuation']==result['market_cap']==result['shareholders']=={}
    assert result['financials']['statements']=={} and result['events']==[]
    assert len(result['gaps'])==4 and result['materials']==[]


def test_future_disclosures_and_wrong_currency_are_isolated():
    class Wrong(Adapter):
        def request(self,path,params):
            result=super().request(path,params)
            if 'financials/' in path:
                result['data']['item'][0].update(currency='USD',report_date_ms=ms('2027-01-01'))
            return result
    result=research_materials.collect_materials(SYMBOL,Wrong(),public=Public(),as_of_ms=ms('2026-10-20'))
    assert result['financials']['statements']=={}
    assert sum(gap['capability'].startswith('financial_') for gap in result['gaps'])==3


class TimedValuationAdapter(Adapter):
    def __init__(self,clock,invalid=None):self.clock=clock;self.invalid=invalid
    def request(self,path,params):
        body=super().request(path,params)
        if 'valuations' in path:
            stamp=self.clock[0]
            if self.invalid=='future':stamp+=60000
            elif self.invalid=='seconds':stamp//=1000
            elif self.invalid=='fractional':stamp+=.5
            elif self.invalid=='wrong_symbol':body['data']['item'][0]['thscode']='600002.SH'
            body['data']['timestamp']=stamp
            self.clock[0]+=250
        else:self.clock[0]+=1000
        return body


def test_default_live_valuation_accepts_normal_update_during_research_and_keeps_source_time(monkeypatch):
    started=ms('2026-10-20');clock=[started]
    monkeypatch.setattr(research_materials,'now_ms',lambda:clock[0])
    result=research_materials.collect_materials(SYMBOL,TimedValuationAdapter(clock),public=Public())
    value=result['valuation'];material=next(row for row in result['materials'] if row['material_id']=='valuation')
    assert value['source_timestamp_ms']==started+3000>started
    assert value['fetched_at_ms']==value['validation_as_of_ms']==started+3250
    assert value['source_timestamp_ms']!=value['fetched_at_ms']
    assert value['validation_basis']=='latest_fetch_completed' and value['future_tolerance_ms']==0
    assert material['source_timestamp_ms']==value['source_timestamp_ms']
    assert material['fetched_at_ms']==value['fetched_at_ms']
    assert material['validation_as_of_ms']==value['validation_as_of_ms']
    assert result['material_window']['collection_started_at_ms']==started
    assert result['material_window']['collection_finished_at_ms']==clock[0]
    assert result['material_window']['as_of_mode']=='manual_latest'
    assert not any(gap['capability']=='valuation' for gap in result['gaps'])


@pytest.mark.parametrize('invalid',['future','seconds','fractional','wrong_symbol'])
def test_live_valuation_rejects_future_time_wrong_units_or_identity(monkeypatch,invalid):
    clock=[ms('2026-10-20')]
    monkeypatch.setattr(research_materials,'now_ms',lambda:clock[0])
    result=research_materials.collect_materials(SYMBOL,TimedValuationAdapter(clock,invalid),public=Public())
    assert result['valuation']=={}
    assert any(gap['capability']=='valuation' for gap in result['gaps'])
    assert all(row['material_id']!='valuation' for row in result['materials'])


def test_explicit_valuation_cutoff_stays_strict_when_collection_finishes_later(monkeypatch):
    cutoff=ms('2026-10-20');clock=[cutoff]
    monkeypatch.setattr(research_materials,'now_ms',lambda:clock[0])
    result=research_materials.collect_materials(SYMBOL,TimedValuationAdapter(clock),public=Public(),as_of_ms=cutoff)
    assert clock[0]>cutoff and result['valuation']=={}
    assert result['material_window']['research_as_of_ms']==cutoff
    assert result['material_window']['as_of_mode']=='explicit_cutoff'
    assert any(gap['capability']=='valuation' for gap in result['gaps'])


def test_access_ttl_list_does_not_renew_task_poll_does_not_renew_and_cleanup_is_scoped(research,monkeypatch):
    _,client=research;queued=client.post('/api/v1/research/jobs',json={'instrument_id':SYMBOL}).json()
    report=research_service.execute_research(queued['job_id']);identifier=report['report_id'];created=report['created_at_ms']
    with core.connect() as connection:
        connection.execute("INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms) VALUES('external','raw','manual','unrelated-research-user',?)",(created,))
    monkeypatch.setattr(research_cache,'now_ms',lambda:created+1000)
    assert client.get('/api/v1/research/jobs').status_code==200
    assert client.get('/api/v1/tasks/'+queued['job_id']).status_code==200
    with core.connect() as connection:assert connection.execute('SELECT last_accessed_at_ms FROM research_report WHERE report_id=?',(identifier,)).fetchone()[0]==created
    opened=client.get('/api/v1/research/jobs/'+identifier).json()
    assert opened['cache']['last_accessed_at_ms']==created+1000
    expiry=opened['cache']['expires_at_ms'];monkeypatch.setattr(research_cache,'now_ms',lambda:expiry)
    assert client.get('/api/v1/research/jobs').json()['items']==[]
    assert client.get('/api/v1/research/jobs/'+identifier).status_code==404
    task=client.get('/api/v1/tasks/'+queued['job_id']).json()
    assert task['report_available'] is False
    assert task['status']==('partial' if report['status']=='partial' else 'succeeded')
    cleaned=research_cache.cleanup_expired(now=expiry)
    assert cleaned['removed_reports']==1 and cleaned['released_research_pins']>0
    with core.connect() as connection:
        assert connection.execute("SELECT count(*) FROM retention_pin WHERE pin_id='external'").fetchone()[0]==1
        assert connection.execute('SELECT count(*) FROM dataset_snapshot').fetchone()[0]==3
        assert connection.execute('SELECT count(*) FROM job WHERE job_id=?',(queued['job_id'],)).fetchone()[0]==1


def test_explicit_external_report_pin_protects_json_but_not_expired_own_pins(research):
    _,client=research;job=client.post('/api/v1/research/jobs',json={'instrument_id':SYMBOL}).json();report=research_service.execute_research(job['job_id'])
    with core.connect() as connection:
        connection.execute("INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms) VALUES('external-report','raw','manual',?,?)",(report['report_id'],report['created_at_ms']))
    result=research_cache.cleanup_expired(now=report['created_at_ms']+research_cache.TTL_MS)
    assert result['removed_reports']==0 and result['protected_reports']==1
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM research_report').fetchone()[0]==1
        assert connection.execute("SELECT count(*) FROM retention_pin WHERE owner_kind='research'").fetchone()[0]==0


def test_cancellation_or_generation_change_does_not_publish_report(research,monkeypatch):
    market,client=research;job=client.post('/api/v1/research/jobs',json={'instrument_id':SYMBOL}).json()
    def progress(stage,*args):
        if stage=='留存研究':
            with core.connect() as connection:connection.execute("UPDATE job SET status='cancelled' WHERE job_id=?",(job['job_id'],))
    from app.services.task_service import TaskCancelled
    with pytest.raises(TaskCancelled):research_service.execute_research(job['job_id'],progress)
    with core.connect() as connection:assert connection.execute('SELECT count(*) FROM research_report').fetchone()[0]==0


def test_monthly_ohlc_aggregation_and_short_sample_unknown():
    calendar=['2026-01-01','2026-01-02','2026-02-02','2026-02-03','2026-03-02']
    rows=[{'date':day,'open':i+10,'high':i+12,'low':i+9,'close':i+11,'volume':100,'amount':1000} for i,day in enumerate(calendar)]
    aggregated=aggregate_monthly(rows,calendar,'2026-03-02')
    assert aggregated[0]['open']==10 and aggregated[0]['close']==12 and aggregated[0]['high']==13 and aggregated[0]['low']==9
    assert aggregated[0]['volume']==200 and aggregated[0]['complete']
    assert aggregated[-1]['complete'] is False


def test_cninfo_all_pages_validate_identity_date_and_total():
    calls=[]
    def handler(request):
        calls.append(request)
        if request.method=='GET':return httpx.Response(200,json={'stockList':[{'code':'600001','orgId':'org'}]})
        page=int(parse_qs(request.content.decode())['pageNum'][0]);size=30 if page==1 else 1
        return httpx.Response(200,json={'totalAnnouncement':31,'announcements':[{'secCode':'600001','orgId':'org','announcementId':str((page-1)*30+i),'announcementTitle':'<em>回购</em>公告','announcementTime':ms('2026-01-10'),'adjunctUrl':'finalpage/fixture.pdf'} for i in range(size)]})
    result=ResearchPublicClient(httpx.MockTransport(handler),sleep=lambda _:None).announcements(SYMBOL,'2025-10-20','2026-10-20')
    assert len(calls)==3 and len(result['items'])==31 and result['coverage']['complete']
    assert result['items'][0]['title']=='回购公告' and result['items'][0]['document_url'].startswith('https://static.cninfo.com.cn/')


def test_cninfo_page_limit_is_partial_and_wrong_source_date_not_accepted():
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json={'stockList':[{'code':'600001','orgId':'org'}]})
        return httpx.Response(200,json={'totalAnnouncement':31,'announcements':[{'secCode':'600001','orgId':'org','announcementId':'future','announcementTitle':'公告','announcementTime':ms('2027-01-01')}]})
    result=ResearchPublicClient(httpx.MockTransport(handler),sleep=lambda _:None).announcements(SYMBOL,'2025-10-20','2026-10-20',max_pages=1)
    assert not result['coverage']['complete'] and result['items']==[]


def test_public_blocked_source_does_not_bypass_or_retry_indefinitely():
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(403,text='blocked')
    with pytest.raises(ProviderError,match='拒绝访问'):
        ResearchPublicClient(httpx.MockTransport(handler),sleep=lambda _:None).shareholders(SYMBOL,'2026-10-20')
    assert len(calls)==1
