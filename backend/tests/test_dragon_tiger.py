"""Synthetic fixtures exercise contracts; production never renders these values."""
from copy import deepcopy
from datetime import date,timedelta
import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient

from test_platform import isolated
from app import core
from app.adapters import dragon_tiger as adapter
from app.services import dragon_tiger as service,task_service as tasks,market_service,source_service

DAY='2026-10-27'
MASTER=[{'instrument_id':'000678.SZ','code':'000678','name':'fixture SZ','kind':'stock'},
        {'instrument_id':'600814.SH','code':'600814','name':'fixture SH','kind':'stock'},
        {'instrument_id':'002343.SZ','code':'002343','name':'fixture multi','kind':'stock'}]


def calendar(count=50):
    out=[];cursor=date.fromisoformat(DAY)
    while len(out)<count:
        if cursor.weekday()<5 and not date(2026,10,1)<=cursor<=date(2026,10,7):out.append(cursor.isoformat())
        cursor-=timedelta(days=1)
    return sorted(out)


class Market:
    def __init__(self,days=None):self.days=days or calendar()
    def status(self):return {'ready':True,'snapshot_id':'fixture-market','data_end':self.days[-1]}
    def calendar(self):return self.days
    def instruments(self,**kwargs):return deepcopy(MASTER)
    def instrument(self,symbol):
        result=next((row for row in MASTER if row['instrument_id']==symbol),None)
        if result is None:raise ValueError('无此标的')
        return dict(result)


def board(day=DAY,kind='all'):
    rows=[{'thscode':row['instrument_id'],'ticker':row['code'],'name':row['name'],'range_days':1,'change':.023,
           'buy_value':100.,'sell_value':50.,'net_value':50.,'org_net_value':None,'concept_list':[{'name':'fixture concept'}]} for row in MASTER]
    data={'trade_date':day,'board_type':kind,'count':len(rows),'stock_count':len(rows),'stock_items':rows,'hot_money_items':[]}
    if kind=='hot_money':
        data.update(count=3,stock_count=3,stock_items=[],hot_money_items=[{'name':'fixture A','rows':[dict(rows[0],hot_money_item_net_value=10.),dict(rows[1],hot_money_item_net_value=-5.)]},{'name':'fixture B','rows':[dict(rows[0],hot_money_item_net_value=20.)]}])
    return {'code':0,'data':data}


def reports(day=DAY):
    output={kind:{'date':day,'kind':kind,'rows':[],'pages':[]} for kind in ('buy','sell','disclosures')}
    for index,master in enumerate(MASTER):
        reasons=['日涨幅偏离值达到7%的前5只证券']
        if index==2:reasons.append('日换手率达到20%的前5只证券')
        for extra,reason in enumerate(reasons):
            base={'TRADE_DATE':day+' 00:00:00','SECUCODE':master['instrument_id'],'SECURITY_CODE':master['code'],'TRADE_ID':str(index*10+extra+100),
                  'CHANGE_TYPE':'fixture-type','EXPLANATION':reason}
            output['disclosures']['rows'].append({**base,'SECURITY_NAME_ABBR':master['name'],'BILLBOARD_BUY_AMT':100.,'BILLBOARD_SELL_AMT':50.,'BILLBOARD_NET_AMT':50.})
            for side in ('buy','sell'):
                for rank in range(5):
                    buy=60.-rank;sell=10.+rank
                    if index==1:
                        if side=='buy':sell=None
                        else:buy=None
                    net=(buy or 0)-(sell or 0)
                    output[side]['rows'].append({**base,'OPERATEDEPT_NAME':'机构专用' if rank<2 else 'fixture department '+str(rank),
                      'OPERATEDEPT_CODE':'0' if rank<2 else str(rank),'BUY':buy,'SELL':sell,'NET':net})
    return output


class Provider:
    key='fixture-private-key'
    def __init__(self):self.calls=[]
    def request(self,path,params):
        assert path==adapter.FUYAO_PATH
        self.calls.append(dict(params))
        return board(params['date'],params['board_type'])


class Public:
    def __init__(self):self.calls=[]
    def report(self,day,kind,**kwargs):
        self.calls.append((day,kind));return reports(day)[kind]


@pytest.fixture
def local(isolated,monkeypatch):
    market=Market();provider=Provider();public=Public()
    monkeypatch.setattr(core,'get_market',lambda:market)
    monkeypatch.setattr(service,'get_market',lambda:market)
    monkeypatch.setattr(market_service,'get_market',lambda:market)
    monkeypatch.setattr(source_service,'client',lambda *_:provider)
    return market,provider,public


def test_board_exact_date_count_fraction_identity_and_scope():
    result=adapter.normalize_board(board(),DAY,'all',MASTER)
    assert result['items'][0]['change_pct']==pytest.approx(2.3)
    assert result['items'][0]['source_concepts_basis']==adapter.CONCEPT_BASIS
    value=board();value['data']['stock_items'][0]['thscode']=None
    assert adapter.normalize_board(value,DAY,'all',MASTER)['items'][0]['instrument_id']=='000678.SZ'
    value['data']['stock_items'][0]['thscode']='000678.SH'
    assert adapter.normalize_board(value,DAY,'all',MASTER)['items'][0]['instrument_id'] is None
    for modify in [lambda v:v['data'].update(trade_date='2026-10-26'),lambda v:v['data'].update(count=99),
                   lambda v:v['data']['stock_items'][0].update(range_days=0),
                   lambda v:v['data']['stock_items'][0].update(change=float('nan'))]:
        bad=board();modify(bad)
        with pytest.raises(ProviderError):adapter.normalize_board(bad,DAY,'all',MASTER)


from app.adapters.providers import ProviderError


@pytest.mark.parametrize('declared,source_stock_count',[(52,52),(10,3),(0,0),(0,52),(999,0)])
def test_hot_metadata_is_not_expanded_label_or_distinct_stock_count(declared,source_stock_count):
    value=board(kind='hot_money');value['data'].update(count=declared,stock_count=source_stock_count)
    normalized=adapter.normalize_board(value,DAY,'hot_money',MASTER)
    assert normalized['source_count']==declared and normalized['source_stock_count']==source_stock_count
    assert normalized['observed_label_count']==2 and normalized['observed_label_row_count']==3
    assert normalized['observed_stock_count']==2 and normalized['observed_stock_range_count']==2
    assert normalized['statistics_state']=='unverified' and normalized['state']=='partial'
    assert normalized['warnings']==[adapter.HOT_COUNT_WARNING]
    assert normalized['items'][0]['rows'][0]['change_pct']==pytest.approx(2.3)
    assert normalized['items'][0]['rows'][0]['buy_value']==100.


@pytest.mark.parametrize('day,labels,length,distinct,declared,source_stock_count',[
    ('2026-09-08',15,47,31,52,52),('2026-10-27',18,71,45,71,65)])
def test_hot_multi_day_observed_shape_uses_returned_rows_only(day,labels,length,distinct,declared,source_stock_count):
    # Synthetic rows reproduce two observed response shapes, not real stocks.
    value=board(day,'hot_money');groups=[];position=0
    for index in range(labels):
        size=length//labels+int(index<length%labels);rows=[]
        for _ in range(size):
            code=f'{700000+position%distinct:06d}';position+=1
            rows.append(dict(value['data']['hot_money_items'][0]['rows'][0],ticker=code,thscode=code+'.SH',name='fixture stock'))
        groups.append({'name':'fixture label '+str(index),'rows':rows})
    value['data'].update(count=declared,stock_count=source_stock_count,hot_money_items=groups)
    result=adapter.normalize_board(value,day,'hot_money',MASTER)
    assert result['date']==day and result['source_count']==declared
    assert result['observed_label_count']==labels and result['observed_label_row_count']==length
    assert result['observed_stock_count']==result['observed_stock_range_count']==distinct
    assert sum(len(group['rows']) for group in result['items'])==length


@pytest.mark.parametrize('damage',[
    lambda data:data.update(count='52'),lambda data:data.update(count=True),lambda data:data.update(stock_count=-1),
    lambda data:data.update(trade_date='2026-09-08'),lambda data:data.update(board_type='all'),
    lambda data:data['hot_money_items'][0]['rows'][0].update(buy_value=-1.),
    lambda data:data['hot_money_items'][0]['rows'][0].update(change=float('inf')),
    lambda data:data['hot_money_items'][0]['rows'].append(dict(data['hot_money_items'][0]['rows'][0])),
    lambda data:data['hot_money_items'].append(deepcopy(data['hot_money_items'][0])),
])
def test_hot_unverified_statistics_never_relax_date_type_money_or_duplicates(damage):
    value=board(kind='hot_money');damage(value['data'])
    with pytest.raises(ProviderError):adapter.normalize_board(value,DAY,'hot_money',MASTER)


@pytest.mark.parametrize('source_period',[2,10,20,30])
def test_legal_other_source_periods_are_preserved_but_not_mixed_with_single_or_three(source_period):
    value=board();value['data']['stock_items'][0]['range_days']=source_period
    result=adapter.normalize_board(value,DAY,'all',MASTER);row=result['items'][0]
    assert row['range_days'] is None and row['source_range_days']==source_period and row['range_state']=='excluded'
    assert result['excluded_range_row_count']==1 and result['state']=='partial'
    assert result['observed_stock_range_count']==3 and result['source_count']==3
    assert row['buy_value']==100. and row['sell_value']==50. and row['net_value']==50.


@pytest.mark.parametrize('source_period',[None,0,-1,True,'10',1.5])
def test_malformed_source_periods_still_rejected(source_period):
    value=board();value['data']['stock_items'][0]['range_days']=source_period
    with pytest.raises(ProviderError):adapter.normalize_board(value,DAY,'all',MASTER)


@pytest.mark.parametrize('kind',['all','hot_money'])
def test_same_stock_other_periods_do_not_collide_after_unknown_projection(kind):
    value=board(kind=kind)
    template=value['data']['hot_money_items'][0]['rows'][0] if kind=='hot_money' else value['data']['stock_items'][0]
    rows=[dict(template,range_days=period) for period in (1,3,10,30)]
    value['data'].update(count=4,stock_count=1)
    if kind=='hot_money':value['data']['hot_money_items']=[{'name':'fixture label','rows':rows}]
    else:value['data']['stock_items']=rows
    result=adapter.normalize_board(value,DAY,kind,MASTER)
    observed=result['items'][0]['rows'] if kind=='hot_money' else result['items']
    assert [row['range_days'] for row in observed]==[1,3,None,None]
    assert [row['source_range_days'] for row in observed]==[1,3,10,30]
    assert result['observed_stock_count']==1 and result['observed_stock_range_count']==4
    assert result['excluded_range_row_count']==2


@pytest.mark.parametrize('reason',[
    '严重异常期间日收盘价格涨幅偏离值累计达到100%的证券',
    '连续十个交易日内日收盘价格涨幅偏离值累计达到100%的证券',
    '连续30个交易日内日收盘价格涨幅偏离值累计达到200%的证券',
])
def test_cumulative_seat_reason_is_unknown_not_single_day(reason):
    value=reports()
    for report in value.values():
        for row in report['rows']:
            if row['SECUCODE']=='000678.SZ':row['EXPLANATION']=reason
    stock=board();stock['data']['stock_items'][0]['range_days']=10
    result=adapter.normalize_seats(value,DAY,MASTER,adapter.normalize_board(stock,DAY,'all',MASTER))
    periods=result['instruments']['000678.SZ']['periods']
    assert not periods['1'] and not periods['3'] and len(periods['unknown'])==1
    record=periods['unknown'][0]
    assert record['reason']==reason and record['range_days'] is None and record['reconciliation']['matched'] is None
    assert record['summary']['buy_value']==100. and record['buy'][0]['buy']==60.
    assert result['coverage']['unclassified_disclosures']==1 and result['coverage']['excluded_source_period_rows']==1


def test_seat_rows_keep_anonymous_duplicates_and_unknown_opposing_amounts():
    result=adapter.normalize_seats(reports(),DAY,MASTER,adapter.normalize_board(board(),DAY,'all',MASTER))
    instrument=result['instruments']['000678.SZ'];row=instrument['periods']['1'][0]
    assert len(row['buy'])==len(row['sell'])==5
    assert [r['source_id'] for r in row['buy']].count('0')==2
    assert len(result['instruments']['002343.SZ']['periods']['1'])==2
    sh=result['instruments']['600814.SH']['periods']['1'][0]
    assert all(r['sell'] is None and r['net'] is None and r['raw_net'] is not None for r in sh['buy'])
    assert all(r['buy'] is None and r['net'] is None for r in sh['sell'])
    assert all(g['reconciliation']['matched'] for i in result['instruments'].values() for gs in i['periods'].values() for g in gs)
    bad=reports();bad['sell']['rows'][0]['NET']=999.
    conflicted=adapter.normalize_seats(bad,DAY,MASTER,adapter.normalize_board(board(),DAY,'all',MASTER))
    seat=next(row for row in conflicted['instruments']['000678.SZ']['periods']['1'][0]['sell'] if row['raw_net']==999.)
    assert seat['net'] is None and seat['amount_check']=='conflict' and not seat['amounts_complete']
    assert conflicted['coverage']['conflicting_amount_rows']==1


@pytest.mark.parametrize('kind',['all','org','hot_money'])
def test_source_stock_net_conflict_preserves_raw_and_never_repairs_or_zeroes(kind):
    value=board(kind=kind)
    raw=value['data']['hot_money_items'][0]['rows'][0] if kind=='hot_money' else value['data']['stock_items'][0]
    raw['net_value']=99.
    result=adapter.normalize_board(value,DAY,kind,MASTER)
    row=result['items'][0]['rows'][0] if kind=='hot_money' else result['items'][0]
    assert row['buy_value']==100. and row['sell_value']==50. and row['raw_net_value']==99.
    assert row['net_value'] is None and row['amount_check']=='conflict'
    assert row['amount_check_evidence']['buy_minus_sell_minus_reported_net']=='-49.0'
    assert result['amount_conflict_row_count']==1 and result['state']=='partial'
    assert any('净额' in warning and '冲突' in warning for warning in result['warnings'])


def test_public_summary_real_shape_conflict_retains_unknown_without_widening_tolerance():
    value=reports();raw=value['disclosures']['rows'][0]
    raw.update(BILLBOARD_BUY_AMT=11697284100,BILLBOARD_SELL_AMT=11697284200,BILLBOARD_NET_AMT=0)
    result=adapter.normalize_seats(value,DAY,MASTER,adapter.normalize_board(board(),DAY,'all',MASTER))
    record=result['instruments']['000678.SZ']['periods']['1'][0];summary=record['summary']
    assert summary['buy_value']==11697284100 and summary['sell_value']==11697284200
    assert summary['net_value'] is None and summary['raw_net_value']==0 and summary['amount_check']=='conflict'
    assert summary['amount_check_evidence']=={'buy_minus_sell_minus_reported_net':'-100','unit':'CNY','tolerance':'0.011'}
    assert result['coverage']['conflicting_amount_summaries']==1 and len(record['buy'])==5
    assert any('不凑平' in warning for warning in result['warnings'])


def test_single_three_and_multiple_disclosures_are_not_collapsed():
    value=reports()
    for kind in value:
        first=deepcopy(value[kind]['rows'][0]);first['TRADE_ID']='999';first['EXPLANATION']='连续三个交易日内，涨幅偏离值累计达到20%的证券'
        if kind=='disclosures':value[kind]['rows'].append(first)
        else:
            source=[deepcopy(r) for r in value[kind]['rows'][:5]]
            for row in source:row.update(TRADE_ID='999',EXPLANATION=first['EXPLANATION'])
            value[kind]['rows'].extend(source)
    result=adapter.normalize_seats(value,DAY,MASTER,adapter.normalize_board(board(),DAY,'all',MASTER))
    assert len(result['instruments']['000678.SZ']['periods']['1'])==1
    assert len(result['instruments']['000678.SZ']['periods']['3'])==1


def test_provider_paginates_with_reused_connection_and_rejects_wrong_actual_date():
    calls=[]
    def transport(request):
        calls.append(dict(request.url.params));page=int(request.url.params['pageNumber'])
        return httpx.Response(200,json={'success':True,'code':0,'result':{'count':2,'pages':2,'data':[{'TRADE_DATE':DAY+' 00:00:00','fixture':page}]}})
    with adapter.DragonTigerPublicClient(transport=httpx.MockTransport(transport),sleep=lambda _:None) as client:
        result=client.report(DAY,'buy')
        assert len(result['rows'])==2 and client.transport_attempts==2
    assert [row['pageNumber'] for row in calls]==['1','2']
    assert all(row['filter']=="(TRADE_DATE='2026-10-27')" for row in calls)
    def stale(request):return httpx.Response(200,json={'success':True,'code':0,'result':{'count':1,'pages':1,'data':[{'TRADE_DATE':'2026-10-26 00:00:00'}]}})
    with adapter.DragonTigerPublicClient(transport=httpx.MockTransport(stale),sleep=lambda _:None) as client:
        with pytest.raises(ProviderError):client.report(DAY,'buy')


def test_provider_rejection_is_not_silently_empty_and_has_bounded_retry():
    calls=[]
    def deny(request):calls.append(1);return httpx.Response(200,json={'success':False,'code':9501,'message':'fixture invalid report'})
    with adapter.DragonTigerPublicClient(transport=httpx.MockTransport(deny),sleep=lambda _:None) as client:
        with pytest.raises(ProviderError):client.report(DAY,'buy',allow_empty=True)
    assert len(calls)==1
    calls.clear()
    def retry(request):calls.append(1);return httpx.Response(503)
    with adapter.DragonTigerPublicClient(transport=httpx.MockTransport(retry),sleep=lambda _:None) as client:
        with pytest.raises(ProviderError):client.report(DAY,'buy')
    assert len(calls)==3


def test_collect_cas_publication_api_reads_do_not_network_and_reuse(local,monkeypatch):
    market,provider,public=local
    result=service.collect_for_market(market,DAY,adapter=provider,public=public)
    assert result['quality']=='complete' and result['accepted_days']==1
    assert len(provider.calls)==len(public.calls)==3
    calls=(len(provider.calls),len(public.calls))
    again=service.collect_for_market(market,DAY,adapter=provider,public=public)
    assert again['reused_days']==1 and calls==(len(provider.calls),len(public.calls))
    monkeypatch.setattr(source_service,'client',lambda *_:pytest.fail('GET must never read credentials or request providers'))
    view=service.read_view(DAY)
    assert view['availability']['available'] and view['state']=='partial'
    assert view['boards']['all']['items'][0]['instrument_id']=='000678.SZ'
    seats=service.read_seats('600814.SH',DAY)
    assert seats['records'][0]['buy'][0]['net'] is None
    assert service.Store().index()['anchor_date']==DAY
    assert 'fixture-private-key' not in '\n'.join(p.read_text() for p in service._root().rglob('*.json'))


def test_failed_day_keeps_old_anchor_and_successful_components_for_manual_retry(local):
    market,provider,public=local
    previous=market.days[-2]
    service.collect_for_market(market,DAY,dates=[previous],adapter=provider,public=public)
    old=service.Store().index();fail_public=Public()
    def fail(*args,**kwargs):raise ProviderError('fixture source failure')
    fail_public.report=fail
    result=service.collect_for_market(market,DAY,adapter=provider,public=fail_public)
    assert result['quality']=='partial'
    current=service.Store().index()
    assert current['anchor_date']==old['anchor_date']==previous and current['days']==old['days']
    count=len(provider.calls)
    repaired=service.collect_for_market(market,DAY,adapter=provider,public=public)
    assert repaired['quality']=='complete' and len(provider.calls)==count
    assert service.Store().index()['anchor_date']==DAY


def test_hot_failure_keeps_successful_components_retryable_without_false_complete(local):
    market,provider,public=local;original=provider.request
    def broken(path,params):
        result=original(path,params)
        if params['board_type']=='hot_money':result['data']['trade_date']='2026-09-08'
        return result
    provider.request=broken
    failed=service.collect_for_market(market,DAY,adapter=provider,public=public)
    assert failed['quality']=='partial' and failed['accepted_days']==0 and not public.calls
    assert not service.Store().index()['days'] and service.Store().index()['anchor_date'] is None
    assert service.read_view(DAY)['coverage']['can_backfill']
    assert (service._root()/'parts/fuyao-all'/f'{DAY}.json').is_file()
    assert (service._root()/'parts/fuyao-org'/f'{DAY}.json').is_file()
    previous=len(provider.calls);provider.request=original
    repaired=service.collect_for_market(market,DAY,adapter=provider,public=public)
    assert repaired['quality']=='complete' and repaired['cached_components']==2
    assert len(provider.calls)-previous==1 and len(public.calls)==3
    assert service.read_view(DAY)['warnings'][0]==adapter.HOT_COUNT_WARNING


def test_complete_coverage_means_retained_responses_not_verified_source_statistics(local):
    market,provider,public=local
    service.collect_for_market(market,DAY,dates=market.days[-30:],adapter=provider,public=public)
    view=service.read_view(DAY)
    assert view['coverage']['complete'] and not view['coverage']['can_backfill']
    assert '响应已留存' in view['coverage']['reason'] and '未知' in view['coverage']['reason']
    assert view['boards']['hot_money']['statistics_state']=='unverified'
    assert view['boards']['all']['state']=='ready' and service.read_seats('000678.SZ',DAY)['records']


def test_old_accepted_cas_projects_unverified_statistics_without_rewriting_or_requests(local):
    market,provider,public=local;store=service.Store()
    boards={kind:adapter.normalize_board(board(kind=kind),DAY,kind,MASTER) for kind in ('all','org','hot_money')}
    hot=boards['hot_money']
    for key in list(hot):
        if key.startswith('observed_') or key in ('statistics_state','source_count_basis','source_stock_count_basis'):del hot[key]
    hot.update(state='ready',warnings=[])
    record={'version':adapter.VERSION,'accepted':True,'date':DAY,'snapshot_id':'fixture-market','source_objects':[],
            'boards':boards,'seats':adapter.normalize_seats(reports(),DAY,MASTER,boards['all']),'warnings':[]}
    store.accept(record,market.days);pointer=store.index()['days'][DAY];path=service._root()/pointer['record_ref'];before=path.read_bytes()
    view=service.read_view(DAY)
    assert view['boards']['hot_money']['statistics_state']=='unverified' and view['boards']['hot_money']['observed_label_row_count']==3
    assert view['boards']['hot_money']['state']=='partial' and adapter.HOT_COUNT_WARNING in view['warnings']
    assert store.day(store.index(),DAY)['boards']['hot_money']['state']=='ready' and path.read_bytes()==before
    reuse=service.collect_for_market(market,DAY,adapter=provider,public=public)
    assert reuse['reused_days']==1 and not provider.calls and not public.calls
    assert store.index()['days'][DAY]==pointer and path.read_bytes()==before


def test_old_accepted_severe_reason_isolated_by_readonly_range_projection(local):
    market,provider,public=local;store=service.Store()
    service.collect_for_market(market,DAY,adapter=provider,public=public)
    record=deepcopy(store.day(store.index(),DAY))
    old=record['seats']['instruments']['000678.SZ']['periods']['1'][0]
    old['reason']='严重异常期间日收盘价格涨幅偏离值累计达到100%的证券'
    assert old['range_days']==1
    store.accept(record,market.days);pointer=store.index()['days'][DAY];path=service._root()/pointer['record_ref'];before=path.read_bytes()
    result=service.read_seats('000678.SZ',DAY,1)
    assert not result['records'] and result['unclassified_record_count']==1 and result['state']=='partial'
    assert '累计或其他区间' in result['availability']['reason'] and any('只读隔离' in v for v in result['warnings'])
    assert path.read_bytes()==before and store.day(store.index(),DAY)['seats']['instruments']['000678.SZ']['periods']['1'][0]['range_days']==1


def test_read_seats_explains_cumulative_disclosure_without_mixing_requested_range(local):
    market,provider,public=local
    def response(day,kind,**kwargs):
        report=reports(day)[kind]
        for row in report['rows']:
            if row['SECUCODE']=='000678.SZ':row['EXPLANATION']='严重异常期间日收盘价格涨幅偏离值累计达到100%的证券'
        return report
    public.report=response
    service.collect_for_market(market,DAY,adapter=provider,public=public)
    result=service.read_seats('000678.SZ',DAY,1)
    assert not result['records'] and result['state']=='partial' and result['unclassified_record_count']==1
    assert '累计或其他区间' in result['availability']['reason']


def test_30_market_session_boundary_publication_before_gc_and_failed_day_no_deletion(local):
    market,provider,public=local;store=service.Store();days=market.days[-31:]
    def record(day):return {'version':adapter.VERSION,'accepted':True,'date':day,'snapshot_id':'fixture-market','source_objects':[]}
    pointers={}
    for day in days[:-1]:
        store.accept(record(day),market.days);pointers[day]=store.index()['days'][day]
    first=service._root()/pointers[days[0]]['record_ref'];assert first.is_file()
    before=store.index();store.failure(days[-1],'fixture fail')
    assert store.index()['days']==before['days'] and store.index()['anchor_date']==days[-2] and first.exists()
    store.accept(record(days[-1]),market.days)
    assert len(store.index()['days'])==30 and days[0] not in store.index()['days'] and first.exists()
    store.cleanup(market.days,DAY)
    assert not first.exists() and len(store.index()['days'])==30
    assert service.window(market.days,DAY)==days[1:]
    assert '2026-10-01' not in service.window(market.days,DAY)


def test_pause_cancel_bypass_failure_handling_and_publish(local):
    market,provider,public=local
    def stop():raise tasks.TaskPaused()
    with pytest.raises(tasks.TaskPaused):service.collect_for_market(market,DAY,adapter=provider,public=public,guard=stop)
    assert not provider.calls and not (service._root()/'head.json').exists()


def test_job_api_frozen_scope_replay_historical_rejection_and_no_old_supplement_guard(local):
    market,provider,public=local
    from app.routers import dragon_tiger as router
    app=FastAPI();app.include_router(router.router)
    with TestClient(app) as client:
        created=client.post('/api/v1/market/dragon-tiger/jobs',json={'date':DAY,'request_key':'fixture-dragon','backfill':True})
        assert created.status_code==200,created.text
        job=created.json();assert job['parameters']['dates']==market.days[-30:]
        assert job['parameters']['dragon_tiger_policy']==adapter.VERSION and job['task_id']
        assert not provider.calls and not public.calls
        assert client.post('/api/v1/market/dragon-tiger/jobs',json={'date':DAY,'request_key':'fixture-dragon','backfill':True}).json()['task_id']==job['task_id']
        assert client.post('/api/v1/market/dragon-tiger/jobs',json={'date':market.days[-2],'request_key':'old','backfill':True}).status_code==422
        assert client.get('/api/v1/market/dragon-tiger/000678.SZ',params={'date':DAY,'range_days':2}).status_code==422
        view=client.get('/api/v1/market/dragon-tiger',params={'date':DAY}).json()
        assert view['state']=='unknown' and view['coverage']['can_backfill']
        old=client.get('/api/v1/market/dragon-tiger',params={'date':market.days[-2]}).json()
        assert not old['coverage']['can_backfill']


def test_hash_corruption_unknown_read_not_served_as_fresh(local):
    market,provider,public=local
    service.collect_for_market(market,DAY,adapter=provider,public=public)
    pointer=service.Store().index()['days'][DAY]
    path=service._root()/pointer['record_ref'];path.write_text('{"fixture":"corrupt"}')
    view=service.read_view(DAY)
    assert view['state']=='unknown' and view['boards']=={}


def test_path_escape_and_symlink_not_followed(local,tmp_path):
    with pytest.raises(ValueError):service.Store().object({'record_ref':'../outside.json','sha256':'0'*64})
    outside=tmp_path/'external';outside.write_text('retained')
    root=service._root();root.mkdir(parents=True,exist_ok=True);(root/'head.json').symlink_to(outside)
    with pytest.raises(ValueError):service.Store().index()
    assert outside.read_text()=='retained'


@pytest.mark.parametrize('mode',['initial','increment'])
@pytest.mark.parametrize('new',[True,False])
def test_real_sector_batch_factory_keeps_frozen_policy_through_complete_chain(local,monkeypatch,mode,new):
    from app import worker
    from app.services import sector_chart_batch_jobs as batch
    from app.services.supplement_policy import FUND_FLOW_RETIRED
    from test_fund_budget_chain import finish
    marker={'dragon_tiger_policy':adapter.VERSION,'dragon_tiger_backfill':mode=='initial','fund_flow_policy':FUND_FLOW_RETIRED} if new else {}
    root=tasks.create_task('initialise' if mode=='initial' else 'increment',{'data_chain_version':'sector-charts-v1' if mode=='initial' else 'sector-charts-increment-v1',**marker},DAY)
    finish(root)
    monkeypatch.setattr(batch,'require_initialization_allowed',lambda *a,**k:None)
    monkeypatch.setattr(batch,'plan_sector_chart_batch',lambda *a,**k:{'snapshot_id':'fixture-market','boards':[{'topic_code':'fixture-board'}]})
    child=worker.recover_followups()[0]
    if mode=='initial':
        assert child['kind']=='topic_refresh';finish(child);child=worker.recover_followups()[0]
    assert child['parameters']['operation']=='sector_charts'
    assert child['parameters'].get('dragon_tiger_policy')==(adapter.VERSION if new else None)
    assert child['parameters'].get('fund_flow_policy')==(FUND_FLOW_RETIRED if new else None)
    finish(child);supplement=worker.recover_followups()[0]
    assert supplement['parameters']['operation']=='market_supplement'
    assert supplement['parameters'].get('dragon_tiger_policy')==(adapter.VERSION if new else None)
    assert supplement['parameters'].get('dragon_tiger_backfill')==(mode=='initial' if new else None)
    assert supplement['parameters'].get('fund_flow_policy')==(FUND_FLOW_RETIRED if new else None)


def test_worker_dispatches_only_frozen_lhb_job_and_paused_is_not_resumed(local,monkeypatch):
    from app import worker
    market,provider,public=local
    job=service.create_job({'date':DAY,'backfill':False,'request_key':'worker-lhb'})
    claimed=tasks.claim_next('fixture-worker')
    monkeypatch.setattr(service,'DragonTigerPublicClient',lambda **kwargs:Public())
    monkeypatch.setattr(Public,'__exit__',lambda *args:None,raising=False)
    worker.dispatch(claimed)
    assert tasks.task_view(job['task_id'])['status']=='succeeded'
    assert service.Store().index()['anchor_date']==DAY
    tasks.create_task('source_check',{'operation':'dragon_tiger','market_snapshot_id':'fixture-market'},DAY,'legacy-invalid-lhb')
    with pytest.raises(ValueError,match='冻结'):service.execute_job(tasks.claim_next('fixture-worker'))


def test_empty_is_known_only_after_all_dated_empty_boards_and_explicit_source_empty():
    value=board();value['data'].update(count=0,stock_count=0,stock_items=[])
    assert adapter.normalize_board(value,DAY,'all',MASTER)['items']==[]
    value['data']['stock_count']=1
    with pytest.raises(ProviderError):adapter.normalize_board(value,DAY,'all',MASTER)
    def empty(request):return httpx.Response(200,json={'success':False,'code':9201,'message':'empty scoped query'})
    with adapter.DragonTigerPublicClient(transport=httpx.MockTransport(empty),sleep=lambda _:None) as client:
        with pytest.raises(ProviderError):client.report(DAY,'buy')
        assert client.report(DAY,'buy',allow_empty=True)['rows']==[]


def test_complete_source_pages_preserve_fewer_than_five_and_missing_direction_as_unknown():
    value=reports();value['buy']['rows']=[r for i,r in enumerate(value['buy']['rows']) if i not in (3,4)]
    normalized=adapter.normalize_seats(value,DAY,MASTER,adapter.normalize_board(board(),DAY,'all',MASTER))
    record=normalized['instruments']['000678.SZ']['periods']['1'][0]
    assert len(record['buy'])==3 and len(record['sell'])==5 and record['coverage']['buy_rows']==3
    value['buy']['rows']=[r for r in value['buy']['rows'] if r['SECUCODE']!='000678.SZ']
    normalized=adapter.normalize_seats(value,DAY,MASTER,adapter.normalize_board(board(),DAY,'all',MASTER))
    assert normalized['instruments']['000678.SZ']['periods']['1'][0]['buy']==[]
    assert any('缺行' in warning for warning in normalized['warnings'])


def test_unknown_instrument_fallback_never_claimed_as_verified_stock(local,monkeypatch):
    market,provider,public=local
    monkeypatch.setattr(market,'instrument',lambda symbol:{'instrument_id':symbol,'kind':'stock','name':symbol})
    with pytest.raises(HTTPException) as exc:service.read_seats('999999.SZ',DAY)
    assert exc.value.status_code==422


@pytest.mark.parametrize('new',[False,True])
def test_market_supplement_collects_lhb_only_for_explicit_new_marker(isolated,cache_workspace,monkeypatch,new):
    from app.services import market_supplement as supplement
    root,market=cache_workspace;calls=[]
    def collect(m,target,**kwargs):
        calls.append((m,target,kwargs))
        return {'quality':'complete','accepted_days':1,'failures':[],'message':'fixture lhb accepted'}
    monkeypatch.setattr(service,'collect_for_market',collect)
    result=supplement.refresh_for_market(market,market.status()['data_end'],**({'dragon_tiger_policy':adapter.VERSION,'dragon_tiger_backfill':True} if new else {}))
    assert len(calls)==int(new)
    if new:
        assert calls[0][2]['backfill'] is True and result['dragon_tiger']['version']==adapter.VERSION
    else:assert 'dragon_tiger' not in result


from test_sector_supplement_integration import cache_workspace


def test_latest_failure_does_not_advance_component_gc_boundary(local):
    market,provider,public=local;store=service.Store();anchor=market.days[-2]
    boundary=service.window(market.days,anchor)[0]
    raw,pointer,_=store.part('fuyao-all',boundary,lambda:{'fixture':'old anchored resumable evidence'},lambda _:None)
    old_part=service._root()/'parts/fuyao-all'/f'{boundary}.json'
    store.accept({'version':adapter.VERSION,'accepted':True,'date':anchor,'snapshot_id':'fixture-market','source_objects':[]},market.days)
    def failed(*args,**kwargs):raise ProviderError('fixture latest source failed')
    public.report=failed
    result=service.collect_for_market(market,DAY,dates=[anchor,DAY],adapter=provider,public=public)
    assert result['quality']=='partial' and result['reused_days']==1
    assert store.index()['anchor_date']==anchor
    assert old_part.exists() and (service._root()/pointer['record_ref']).exists()
    assert (service._root()/'parts/fuyao-all'/f'{DAY}.json').exists()
