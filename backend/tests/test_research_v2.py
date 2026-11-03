"""Evidence research uses tiny synthetic fixtures and bounded mocked public HTTP."""
from copy import deepcopy
from datetime import date
import json
import httpx
import pytest
from app import core
from app.adapters.providers import ProviderError
from app.adapters.research_budget import ResearchPublicBudget
from app.adapters.research_public import ResearchPublicClient
from app.services import research_materials, research_service, task_service
from app.services.research_evidence import build_evidence_report, freeze_plan, fingerprint
from test_research import research, Public, SYMBOL, ms
from test_workspace import ws


def business_row(**patch):
    return {'SECUCODE': SYMBOL, 'SECURITY_CODE': SYMBOL[:6], 'REPORT_DATE': '2025-12-31 00:00:00',
            'MAINOP_TYPE': '2', 'ITEM_NAME': '合成测试产品', 'MAIN_BUSINESS_INCOME': '120000000',
            'MBI_RATIO': .6, 'MAIN_BUSINESS_COST': 60000000, 'MAIN_BUSINESS_RPOFIT': 60000000,
            'GROSS_RPOFIT_RATIO': .5, **patch}


def profile_from(rows, symbol=SYMBOL, as_of='2026-01-10'):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200,json={'zygcfx': rows})
    result=ResearchPublicClient(httpx.MockTransport(handler)).company_profile(symbol,as_of)
    return result,calls


def test_business_profile_verifies_full_market_identity_periods_and_categories():
    result,calls=profile_from([
        business_row(),business_row(MAINOP_TYPE='3',ITEM_NAME='测试地区',MBI_RATIO=.4),
        business_row(REPORT_DATE='2024-12-31',ITEM_NAME='旧产品'),
        business_row(SECUCODE='600001.SZ',ITEM_NAME='错误市场'),
        business_row(SECURITY_CODE='600002',ITEM_NAME='错误代码'),
        business_row(REPORT_DATE='2027-01-01',ITEM_NAME='未来期'),
        business_row(MAINOP_TYPE='7',ITEM_NAME='未知分类'),
        business_row(ITEM_NAME='<b></b>'),
    ])
    assert len(calls)==1 and 'code=SH600001' in calls[0]
    assert result['report_period']=='2025-12-31'
    assert [row['name'] for row in result['items']]==['合成测试产品','测试地区']
    assert result['items'][0]['revenue_cny']==120000000
    assert result['items'][0]['revenue_ratio']==.6
    assert result['published_at_ms'] is None and result['historical_usable'] is False
    assert result['coverage']['complete'] is False and result['coverage']['isolated_count']==5


def test_business_profile_duplicate_rows_are_not_added_and_conflicts_are_rejected():
    result,_=profile_from([business_row(),business_row()])
    assert len(result['items'])==1
    with pytest.raises(ProviderError,match='冲突'):
        profile_from([business_row(),business_row(MBI_RATIO=.7)])
    with pytest.raises(ProviderError,match='没有取得'):
        profile_from([business_row(SECUCODE='600001.SZ')])
    with pytest.raises(ProviderError,match='北交所'):
        profile_from([],symbol='920001.BJ')


def test_business_profile_limits_rows_without_claiming_complete_supply_chain():
    rows=[business_row(ITEM_NAME='测试产品'+str(i)) for i in range(205)]
    result,_=profile_from(rows)
    assert len(result['items'])==200 and result['coverage']['truncated'] is True
    assert '供应链' in result['coverage']['reason']


def limits():return freeze_plan(SYMBOL,'fixture','2026-01-10')['source_budget']


def test_frozen_plan_windows_count_calendar_dates_inclusively():
    plan=freeze_plan(SYMBOL,'fixture','2026-01-10')
    for key,count in [('news',30),('announcements',365),('institution_reports',180)]:
        window=plan['windows'][key]
        assert (date.fromisoformat(window['end'])-date.fromisoformat(window['start'])).days+1==count


@pytest.mark.parametrize('status',[401,403,429])
def test_shared_public_budget_stops_rejected_host_without_retries(status):
    calls=[]
    budget=ResearchPublicBudget(limits())
    def handler(request):calls.append(str(request.url));return httpx.Response(status,json={})
    client=ResearchPublicClient(httpx.MockTransport(handler),sleep=lambda _:None,budget=budget)
    with pytest.raises(ProviderError):client.request('business','/one')
    with pytest.raises(ProviderError,match='本任务不再请求'):client.request('business','/two')
    assert len(calls)==1 and budget.view()['http_attempts']==1


def test_public_attempt_deadline_budget_and_cancellation_apply_before_next_request():
    clock=[100.0];calls=[];config={**limits(),'public_max_http_attempts':1}
    budget=ResearchPublicBudget(config,clock=lambda:clock[0])
    def handler(request):calls.append(1);return httpx.Response(500,json={})
    client=ResearchPublicClient(httpx.MockTransport(handler),sleep=lambda _:None,budget=budget)
    with pytest.raises(ProviderError,match='请求预算'):client.request('business','/one')
    assert calls==[1]
    budget=ResearchPublicBudget(limits(),clock=lambda:clock[0]);clock[0]+=91
    client.budget=budget
    with pytest.raises(ProviderError,match='总时间预算'):client.request('business','/one')
    assert calls==[1]
    def cancelled():raise task_service.TaskCancelled()
    client.budget=ResearchPublicBudget(limits(),checkpoint=cancelled)
    with pytest.raises(task_service.TaskCancelled):client.request('business','/one')
    assert calls==[1]


def test_public_adapter_does_not_redirect_to_community_or_forward_credentials():
    calls=[]
    def handler(request):
        calls.append(request)
        assert 'x-api-key' not in request.headers and 'cookie' not in request.headers
        return httpx.Response(302,headers={'location':'https://xueqiu.com/protected'})
    client=ResearchPublicClient(httpx.MockTransport(handler),sleep=lambda _:None)
    with pytest.raises(ProviderError):client.company_profile(SYMBOL,'2026-01-10')
    assert len(calls)==2 and all(request.url.host=='emweb.securities.eastmoney.com' for request in calls)
    with pytest.raises(ProviderError):client.request('xueqiu','/api/private')
    assert len(calls)==2


@pytest.mark.parametrize('stop',['bytes','deadline','cancel'])
def test_streaming_response_stops_before_consuming_more_chunks(stop):
    clock=[100.0];consumed=[];checks=[];closed=[]
    config={**limits(),'public_max_response_bytes':8 if stop=='bytes' else 1024}
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            consumed.append('first')
            if stop=='deadline':clock[0]+=91
            yield b'123456789'
            consumed.append('second')
            yield b'never-read'
        def close(self):closed.append(True)
    def checkpoint():
        checks.append(1)
        if stop=='cancel' and len(checks)==2:raise task_service.TaskCancelled()
    budget=ResearchPublicBudget(config,clock=lambda:clock[0],checkpoint=checkpoint)
    calls=[]
    def handler(request):calls.append(1);return httpx.Response(200,stream=Stream())
    client=ResearchPublicClient(httpx.MockTransport(handler),sleep=lambda _:None,budget=budget)
    with pytest.raises(task_service.TaskCancelled if stop=='cancel' else ProviderError):
        client.request('business','/bounded')
    assert consumed==['first'] and closed==[True] and calls==[1]


def base_materials():
    profile,_=profile_from([business_row()])
    announcement={'instrument_id':SYMBOL,'announcement_id':'one','title':'拟签订测试合同的进展公告',
                  'published_date':'2026-01-09','published_at_ms':ms('2026-01-09'),'source':'cninfo',
                  'url':'https://www.cninfo.com.cn/new/disclosure/detail?announcementId=one','raw':{'not':'needed'}}
    return {'financials':{'statements':{}},'valuation':{'pe_ttm':-5},'shareholders':{},'market_cap':{},
            'company_profile':profile,'announcements':{'items':[announcement,deepcopy(announcement)],'coverage':{'complete':False}},
            'gaps':[],'material_window':{'collection_finished_at_ms':ms('2026-01-10')}}


def test_evidence_graph_has_real_references_deduplicated_events_and_neutral_title_clues():
    sources=base_materials();before=deepcopy(sources)
    report=build_evidence_report(SYMBOL,{'name':'合成测试公司'},sources,freeze_plan(SYMBOL,'snapshot','2026-01-10'))
    assert sources==before,'pure graph construction must not mutate accepted materials'
    assert report['schema_version']==2 and len(report['report_sections'])==6
    assert len(report['news']['items'])==1
    event=report['news']['items'][0]
    assert event['impact']=='unknown' and event['positive_evidence_ids']==event['negative_evidence_ids']==[]
    assert '标题线索' in event['summary'] and '不确认' in event['summary']
    assert report['company_profile']['main_business']=='来源主营构成中的产品项目：合成测试产品'
    assert report['company_profile']['business_scope'] is None
    assert [row['relation_type'] for row in report['industry_chain']['items']]==['product']
    identifiers={row['evidence_id'] for row in report['evidence']}
    for row in report['claims']+report['industry_chain']['items']+report['news']['items']:
        assert row['evidence_ids'] and set(row['evidence_ids'])<=identifiers
    assert all(len(row['content_hash'])==64 for row in report['evidence'])
    assert all(row['historical_usable'] is False for row in report['evidence'])
    announcement=next(row for row in report['evidence'] if row['kind']=='announcement')
    assert announcement['publication_date_known'] is True
    assert report['institution_views']['items']==[] and report['institution_views']['state']=='requires_license'
    assert report['overview']['model_used'] is False
    assert any(row['source_id']=='xueqiu' and row['url'].endswith('SH600001') for row in report['external_links'])


def test_news_window_does_not_merge_similar_titles_or_include_future_dates():
    sources=base_materials();template=sources['announcements']['items'][0]
    sources['announcements']['items'].extend([
        {**template,'announcement_id':'two','title':'测试合同的更正公告','url':'https://www.cninfo.com.cn/two'},
        {**template,'announcement_id':'future','published_date':'2026-01-11','published_at_ms':ms('2026-01-11')},
        {**template,'announcement_id':'old','published_date':'2025-01-01','published_at_ms':ms('2025-01-01')},
    ])
    report=build_evidence_report(SYMBOL,{},sources,freeze_plan(SYMBOL,'snapshot','2026-01-10'))
    assert len(report['news']['items'])==2
    assert all(row['published_date']=='2026-01-09' for row in report['news']['items'])
    assert {row['status'] for row in report['news']['items']}=={'进展线索','更正或澄清线索'}


def test_absent_profile_never_promotes_titles_or_topics_to_business_facts():
    sources=base_materials();sources['company_profile']={}
    sources['valuation']['invalid']=float('nan')
    report=build_evidence_report(SYMBOL,{},sources,freeze_plan(SYMBOL,'snapshot','2026-01-10'))
    assert report['company_profile']['main_business'] is None and report['industry_chain']['items']==[]
    assert all(row['kind']=='title_clue' for row in report['claims'])
    assert fingerprint({'b':1,'a':float('nan')})==fingerprint({'a':None,'b':1})


class EnhancedPublic(Public):
    profile_calls=0
    def company_profile(self,symbol,as_of,*,max_rows=200):
        type(self).profile_calls+=1
        return profile_from([business_row()])[0]


def test_new_jobs_freeze_v2_and_reading_reports_never_recollects(research,monkeypatch):
    _,client=research;EnhancedPublic.profile_calls=0
    monkeypatch.setattr(research_materials,'ResearchPublicClient',EnhancedPublic)
    job=client.post('/api/v1/research/jobs',json={'instrument_id':SYMBOL}).json()
    plan=job['parameters']['research_plan']
    assert plan['version']=='evidence-research-v2' and plan['market_snapshot_id']=='fixture-research-market'
    assert plan['external_only']==['institution-reports','institution-forecasts','xueqiu','jiuyangongshe']
    assert plan['model_calls'] is False and plan['source_budget']['public_max_http_attempts']==32
    report=research_service.execute_research(job['job_id'])
    assert report['schema_version']==2 and report['frozen_plan']==plan
    assert report['technical_assessment']['price_basis']=='none'
    assert report['financials']['statements'] and report['evidence']
    assert report['status']=='partial' and EnhancedPublic.profile_calls==1
    assert client.get('/api/v1/research/jobs').status_code==200
    assert client.get('/api/v1/research/jobs/'+report['report_id']).json()['schema_version']==2
    assert client.get('/api/v1/tasks/'+job['job_id']).status_code==200
    assert EnhancedPublic.profile_calls==1


def test_existing_unversioned_task_keeps_v1_sources_and_report(research,monkeypatch):
    _,_=research
    class OldPublic(Public):
        def company_profile(self,*args,**kwargs):raise AssertionError('old task cannot fetch new source')
    monkeypatch.setattr(research_materials,'ResearchPublicClient',OldPublic)
    job=task_service.create_task('research',{'instrument_id':SYMBOL,'market_snapshot_id':'fixture-research-market'},core.business_date())
    report=research_service.execute_research(job['job_id'])
    assert 'schema_version' not in report and 'evidence' not in report
    assert report['valuation']['pe_ttm']==-5


def test_tampered_frozen_plan_is_rejected_before_any_collection(research,monkeypatch):
    _,client=research
    job=client.post('/api/v1/research/jobs',json={'instrument_id':SYMBOL}).json()
    parameters=job['parameters'];parameters['research_plan']['source_budget']['public_max_http_attempts']=1000
    with core.connect() as connection:
        connection.execute('UPDATE job SET parameters_json=? WHERE job_id=?',(json.dumps(parameters),job['job_id']))
    monkeypatch.setattr(research_service,'collect_materials',lambda *args,**kwargs:pytest.fail('must reject before collection'))
    with pytest.raises(ValueError,match='冻结校验'):research_service.execute_research(job['job_id'])


@pytest.mark.parametrize('state,exception',[('cancelled',task_service.TaskCancelled),('paused',task_service.TaskPaused)])
def test_pause_cancel_during_new_public_step_cannot_publish(research,monkeypatch,state,exception):
    _,client=research
    job=client.post('/api/v1/research/jobs',json={'instrument_id':SYMBOL}).json()
    class StopPublic(EnhancedPublic):
        def company_profile(self,*args,**kwargs):
            with core.connect() as connection:connection.execute('UPDATE job SET status=? WHERE job_id=?',(state,job['job_id']))
            return super().company_profile(*args,**kwargs)
    monkeypatch.setattr(research_materials,'ResearchPublicClient',StopPublic)
    with pytest.raises(exception):research_service.execute_research(job['job_id'])
    with core.connect() as connection:assert connection.execute('SELECT count(*) FROM research_report').fetchone()[0]==0


@pytest.mark.parametrize('invalid', [None, 'identity', 'currency', 'future', 'swapped'])
def test_tencent_market_cap_accepts_only_dated_same_stock_cny_snapshot(invalid):
    fields = [''] * 83
    fields[0], fields[2], fields[30], fields[44], fields[45], fields[82] = (
        '51', '000001', '20260109150100', '8.125', '10.25', 'CNY')
    if invalid == 'identity': fields[2] = '000002'
    if invalid == 'currency': fields[82] = 'HKD'
    if invalid == 'future': fields[30] = '20260111093000'
    if invalid == 'swapped': fields[44] = '20'
    body = ('v_sz000001="' + '~'.join(fields) + '";').encode()
    client = ResearchPublicClient(httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
    if invalid:
        with pytest.raises(ProviderError):
            client.tencent_market_cap('000001.SZ', as_of_ms=ms('2026-01-10'))
        return
    result = client.tencent_market_cap('000001.SZ', as_of_ms=ms('2026-01-10'))
    assert result['total_market_cap_cny'] == 1_025_000_000
    assert result['float_market_cap_cny'] == 812_500_000
    assert result['source_date'] == '2026-01-09' and result['source'] == 'tencent'


def test_market_cap_fallback_does_not_expand_old_frozen_plan():
    class PublicWithFallback(Public):
        calls = 0

        def tencent_market_cap(self, symbol, *, as_of_ms=None):
            self.calls += 1
            return {'total_market_cap_cny': 1000, 'float_market_cap_cny': 500, 'source': 'tencent',
                    'source_timestamp_ms': ms('2026-01-09'), 'coverage': {'complete': True}}

    public = PublicWithFallback()
    for enabled in (False, True):
        plan = freeze_plan(SYMBOL, 'fixture', '2026-01-10', market_cap_fallback=enabled)
        report = research_materials.collect_materials(SYMBOL, public=public, as_of_ms=ms('2026-01-10'),
                                                      enhanced=True, plan=plan)
        assert public.calls == int(enabled)
        assert report['market_cap']['source'] == ('tencent' if enabled else 'eastmoney')
        if enabled:
            assert [row['source'] for row in report['materials'] if row['kind'] == 'market_cap'] == ['tencent']
