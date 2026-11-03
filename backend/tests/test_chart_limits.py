"""Verified close-limit overlays: controlled source records, no live collection."""
from copy import deepcopy
from datetime import date,timedelta

import pytest

from app.analytics.indicators import aggregate_weekly
from app.services.chart_limits import enrich_chart_limits

SYMBOL='600001.SH';OTHER='600002.SH'


class LimitMarket:
    def __init__(self):
        start=date(2026,1,5);self.days=[]
        while len(self.days)<11:
            if start.weekday()<5:self.days.append(start.isoformat())
            start+=timedelta(days=1)
        self.rows=[{'date':day,'open':10+i,'close':10+i,'high':11+i,'low':9+i,'volume':100,'amount':1000,'change_pct':20,'corporate_action':False} for i,day in enumerate(self.days)]
        self.ref={};self.raw_calls=0
    def bars(self,symbol,as_of=None):
        assert symbol==SYMBOL;self.raw_calls+=1
        return [dict(row) for row in self.rows if not as_of or row['date']<=as_of]
    def calendar(self):return self.days
    def reference(self):return self.ref
    def chart(self,symbol=SYMBOL,period='D',as_of=None,price_basis='none',indicator_continuity='market_sessions'):
        cutoff=as_of or self.days[-1];display=[dict(row) for row in self.rows if row['date']<=cutoff]
        if price_basis=='forward':
            for row in display:
                for field in ('open','high','low','close'):row[field]*=.5
        if period=='W':display=aggregate_weekly(display,self.days,cutoff)
        return {'instrument':{'instrument_id':symbol,'name':'*ST当前名称不回推历史','kind':'stock','list_date':getattr(self,'list_date',None)},'period':period,'as_of':cutoff,'bars':display,'snapshot_id':'fixture-generation','price_basis':'forward_event_affine' if price_basis=='forward' else 'none','warnings':[]}


def entry(market,index,symbol=SYMBOL,**extra):
    return {'instrument_id':symbol,'thscode':symbol,'ticker':symbol[:6],'last_price':market.rows[index]['close'],'date':market.days[index],**extra}


def annual(market,index,kind='limitup',members=None,**extra):
    items=members if members is not None else [entry(market,index)]
    return {'items':items,'date':market.days[index],'source':'fuyao','pool_kind':kind,'complete':True,'pagination_complete':True,'raw_codes':[row['ticker'] for row in items],'ambiguous_code_rows':0,'rejected_codes':[],**extra}


def test_exact_dated_up_down_markers_and_per_security_counts():
    market=LimitMarket();up=market.days[0];down=market.days[1]
    market.ref={'pool_history':{up:{'limitup':[entry(market,0)],'coverage':{'limitup':{'complete':True}}}},'pool_trade_date':down,'limitdown':[entry(market,1)],'pool_coverage':{'limitdown':{'complete':True}}}
    original=market.chart();before=deepcopy(original)
    result=enrich_chart_limits(market,original,{})
    assert original==before and market.raw_calls==1
    assert [(event['date'],event['kind']) for event in result['limit_events']]==[(up,'limit_up'),(down,'limit_down')]
    assert result['bars'][0]['limit_up_state'] is True and result['bars'][1]['limit_down_state'] is True
    assert result['bars'][2]['limit_up_state'] is None # +20% is not itself evidence.
    assert result['limit_events'][0]['chart_price']==10 and result['limit_events'][0]['raw_close']==10
    coverage=result['limit_event_coverage']
    assert coverage['up']['confirmed_days']==1 and coverage['down']['confirmed_days']==1
    for kind in ('up','down'):
        assert sum(coverage[kind][name+'_days'] for name in ('confirmed','source_absent','unknown','conflict'))==11
    assert '来源' in coverage['notice']


def test_annual_up_and_down_support_explicit_per_security_source_absence():
    market=LimitMarket();day=market.days[2]
    supplement={'annual_pools':{day:annual(market,2)},'annual_down_pools':{day:annual(market,2,'limitdown',[entry(market,2,OTHER)],complete=False,quarantine=[{'code':'600003'}])}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    row=result['bars'][2]
    assert row['limit_up_state'] is True and row['limit_down_state'] is False
    assert row['limit_event_status']=={'up':'confirmed','down':'source_absent'}
    assert result['limit_event_coverage']['down']['source_absent_days']==1
    assert [event['kind'] for event in row['limit_events']]==['limit_up']


def test_empty_complete_pool_is_unknown_not_a_false_normal_day():
    market=LimitMarket();day=market.days[0]
    market.ref={'pool_trade_date':day,'limitup':[],'limitdown':[],'pool_coverage':{'limitup':{'complete':True},'limitdown':{'complete':True}}}
    supplement={'annual_pools':{day:annual(market,0,members=[])},'annual_down_pools':{day:annual(market,0,'limitdown',[])}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert result['bars'][0]['limit_up_state'] is None and result['bars'][0]['limit_down_state'] is None
    assert not result['limit_events'] and result['limit_event_coverage']['up']['source_absent_days']==0


def test_public_pool_positive_verifies_price_but_absence_never_proves_negative():
    market=LimitMarket();first=market.days[0];second=market.days[1]
    supplement={'public_pools':{first:{'date':first,'items':[{'instrument_id':SYMBOL,'date':first,'close':10}],'complete':True,'source':'eastmoney-public'},second:{'date':second,'items':[{'instrument_id':OTHER,'date':second,'close':11}],'complete':True}}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert result['bars'][0]['limit_up_state'] is True
    assert result['bars'][1]['limit_up_state'] is None
    assert result['limit_events'][0]['source']=='eastmoney-public'


@pytest.mark.parametrize('bad',[
    {'last_price':99},
    {'last_price':True},
    {'date':'2026-01-09'},
    {'thscode':'600001.SZ','instrument_id':'600001.SZ'},
])
def test_bad_target_event_cannot_override_a_conflicting_source(bad):
    market=LimitMarket();day=market.days[0]
    market.ref={'pool_trade_date':day,'limitup':[entry(market,0,**bad)]}
    supplement={'public_pools':{day:{'date':day,'source':'eastmoney-public','items':[{'instrument_id':SYMBOL,'date':day,'close':10}]}}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    if bad.get('last_price') is True:
        # A missing/non-numeric evidence value is unknown, while independent
        # matching positive evidence can still establish the closing event.
        assert result['bars'][0]['limit_up_state'] is True
    else:
        assert result['bars'][0]['limit_up_state'] is None
        assert result['bars'][0]['limit_event_status']['up']=='conflict'
        assert not result['limit_events']


def test_mutually_exclusive_closing_directions_are_both_unknown():
    market=LimitMarket();day=market.days[0]
    supplement={'annual_pools':{day:annual(market,0)},'annual_down_pools':{day:annual(market,0,'limitdown')}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert result['bars'][0]['limit_up_state'] is None and result['bars'][0]['limit_down_state'] is None
    assert result['bars'][0]['limit_event_status']=={'up':'conflict','down':'conflict'}
    assert not result['limit_events']


def test_forward_changes_only_display_anchor_not_dated_raw_evidence():
    market=LimitMarket();day=market.days[3];supplement={'annual_pools':{day:annual(market,3)}}
    raw=enrich_chart_limits(market,market.chart(),supplement)
    forward=enrich_chart_limits(market,market.chart(price_basis='forward'),supplement)
    assert [(event['date'],event['kind'],event['raw_close']) for event in raw['limit_events']]==[(event['date'],event['kind'],event['raw_close']) for event in forward['limit_events']]
    assert forward['limit_events'][0]['chart_price']==raw['limit_events'][0]['chart_price']/2


def test_weekly_aggregates_exact_daily_events_without_weekly_limit_flags():
    market=LimitMarket();supplement={'annual_pools':{market.days[0]:annual(market,0)},'annual_down_pools':{market.days[2]:annual(market,2,'limitdown')}}
    result=enrich_chart_limits(market,market.chart(period='W'),supplement)
    first=result['bars'][0]
    assert first['limit_up_state'] is None and first['limit_down_state'] is None
    assert first['limit_event_summary']['up_count']==first['limit_event_summary']['down_count']==1
    assert {event['date'] for event in first['limit_events']}=={market.days[0],market.days[2]}
    assert all(event['bar_date']==first['date'] and event['chart_price'] is None for event in first['limit_events'])
    assert first['limit_event_summary']['unknown_up_days']==4


def test_as_of_never_reads_future_events_and_missing_raw_quote_stays_unknown():
    market=LimitMarket();cutoff=market.days[4]
    supplement={'annual_pools':{market.days[2]:annual(market,2),market.days[8]:annual(market,8)}}
    market.rows=[row for row in market.rows if row['date']!=market.days[2]]
    result=enrich_chart_limits(market,market.chart(period='W',as_of=cutoff),supplement)
    assert not result['limit_events']
    assert result['limit_event_coverage']['until']==cutoff
    assert result['limit_event_coverage']['expected_trade_days']==5
    assert result['limit_event_coverage']['raw_quote_days']==4
    assert result['bars'][0]['limit_event_summary']['unknown_up_days']==5


def test_breakpool_touch_and_current_st_name_do_not_create_closing_events():
    market=LimitMarket();day=market.days[0]
    market.ref={'pool_trade_date':day,'breakpool':[entry(market,0)],'pool_coverage':{'breakpool':{'complete':True}}}
    result=enrich_chart_limits(market,market.chart(),{})
    assert not result['limit_events']
    assert all(row['limit_up_state'] is None and row['limit_down_state'] is None for row in result['bars'])


def test_index_does_not_read_stock_pools_or_raw_quotes():
    market=LimitMarket();chart=market.chart();chart['instrument']={'kind':'index','instrument_id':'000001.SH'}
    result=enrich_chart_limits(market,chart)
    assert result['limit_event_coverage']['applicable'] is False and not result['limit_events']
    assert market.raw_calls==0


def test_failed_supplement_integrity_is_reported_without_discarding_base_events(monkeypatch):
    from app.services import chart_limits
    market=LimitMarket();day=market.days[0]
    market.ref={'pool_trade_date':day,'limitup':[entry(market,0)]}
    monkeypatch.setattr(chart_limits,'load_supplement',lambda snapshot,**kw:{'quality':'unknown','failure_reason':'补充数据索引缺失或校验失败'})
    result=enrich_chart_limits(market,market.chart())
    assert result['bars'][0]['limit_up_state'] is True
    assert result['bars'][1]['limit_up_state'] is None
    assert any('补充数据索引缺失或校验失败' in warning for warning in result['warnings'])


@pytest.mark.parametrize('focused', [False, True])
def test_overlay_projects_only_required_close_pools_and_dates(monkeypatch, focused):
    from app.services import chart_limits
    from app.services.supplement_storage import expand_document
    market=LimitMarket();day=market.days[-2]
    document={'snapshot_id':'fixture-generation','job_id':'supplement-1',
        'annual_pools':{observed:annual(market,index) for index,observed in enumerate(market.days)},
        'annual_down_pools':{},'public_pools':{},
        'fund_flows':{day:{'unrelated':'never needed by a closing badge'}},
        'sector_index_history':{day:{'unrelated':'never needed by a stock candle'}}}
    chart=market.chart(as_of=day)
    if focused:chart['bars']=[chart['bars'][-1]]
    expected=enrich_chart_limits(market,chart,document,focus_day=day if focused else None)
    calls=[]
    def projected(snapshot, *, dates, fields):
        calls.append((snapshot,dates,fields))
        assert set(fields)=={'public_pools','annual_pools','annual_down_pools'}
        assert dates==([day] if focused else market.days[:-1])
        return expand_document(None,document,dates=dates,fields=fields)
    monkeypatch.setattr(chart_limits,'load_supplement',projected)
    actual=enrich_chart_limits(market,chart,focus_day=day if focused else None)
    assert actual==expected and len(calls)==1


def test_router_chart_response_contains_overlay_without_analytics_coupling(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.platform import router
    from app.services import market_service,chart_limits
    market=LimitMarket();day=market.days[0]
    market.ref={'pool_trade_date':day,'limitup':[entry(market,0)]}
    monkeypatch.setattr(market_service,'context',lambda as_of:(market,{},as_of or market.days[-1]))
    monkeypatch.setattr(chart_limits,'load_supplement',lambda snapshot,**kw:{})
    app=FastAPI();app.include_router(router)
    response=TestClient(app).get('/api/v1/instruments/'+SYMBOL+'/chart')
    assert response.status_code==200,response.text
    assert response.json()['bars'][0]['limit_up_state'] is True
    assert response.json()['limit_events'][0]['basis']=='verified-source-close-pool'
    assert response.json()['automatic_trendlines']['snapshot_id']=='fixture-generation'
    assert response.json()['automatic_trendlines']['instrument_id']==SYMBOL
    columnar=TestClient(app).get('/api/v1/instruments/'+SYMBOL+'/chart?encoding=columns-v1')
    assert columnar.status_code==200 and 'bars_columnar' in columnar.json()
    assert columnar.json()['automatic_trendlines']==response.json()['automatic_trendlines']


def flat_quote(market, index, price=11):
    market.rows[index].update({key: price for key in ('open', 'high', 'low', 'close')})


def test_one_price_up_and_down_are_separate_from_closing_limit_semantics():
    market=LimitMarket();flat_quote(market,0);flat_quote(market,1,9)
    supplement={'annual_pools':{market.days[0]:annual(market,0,raw_sha256='abc',raw_artifact='raw/abc.json')},
                'annual_down_pools':{market.days[1]:annual(market,1,'limitdown')}}
    original=market.chart();before=deepcopy(original)
    result=enrich_chart_limits(market,original,supplement)
    assert original==before and market.raw_calls==1
    assert [(event['date'],event['kind']) for event in result['pattern_events']]==[
        (market.days[0],'one_price_limit_up'),(market.days[1],'one_price_limit_down')]
    up=result['bars'][0];down=result['bars'][1]
    assert up['limit_up_state'] is True and down['limit_down_state'] is True
    assert up['pattern_status']['one_price_limit_up']['state'] is True
    assert down['pattern_status']['one_price_limit_down']['state'] is True
    event=up['pattern_events'][0]
    assert event['raw_ohlc']=={'open':11.,'high':11.,'low':11.,'close':11.}
    assert event['raw_volume']==100 and event['raw_price_basis']=='none'
    assert event['basis']=='verified-source-close-pool-and-raw-ohlc-v1'
    assert event['snapshot_id']=='fixture-generation' and event['chart_price']==11
    assert event['evidence']['closing_pool'][0]['raw_sha256']=='abc'
    assert event['evidence']['closing_pool'][0]['raw_artifact']=='raw/abc.json'
    assert event['evidence']['calculation_version']==event['basis']


def test_one_tick_range_and_flat_non_limit_day_do_not_become_one_price_limits():
    market=LimitMarket();flat_quote(market,0);market.rows[0]['high']=11.01;flat_quote(market,1)
    supplement={'annual_pools':{market.days[0]:annual(market,0)}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert result['bars'][0]['pattern_status']['one_price_limit_up']['state'] is False
    assert result['bars'][1]['pattern_status']['one_price_limit_up']['state'] is None
    assert not result['pattern_events']


@pytest.mark.parametrize('bad',[
    {'volume':0},{'volume':None},{'volume':True},{'volume':-1},
    {'high':None},{'low':0},{'open':float('nan')},{'low':11.02},
    {'price_basis':'forward_event_affine'}, {'close':11.001},
])
def test_invalid_raw_quotes_keep_pattern_unknown(bad):
    market=LimitMarket();flat_quote(market,0);market.rows[0].update(bad)
    supplement={'annual_pools':{market.days[0]:annual(market,0)}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert not result['pattern_events']
    assert all(state['state'] is None for state in result['bars'][0]['pattern_status'].values())


def test_one_price_requires_exact_pool_price_not_existing_one_cent_overlay_tolerance():
    market=LimitMarket();flat_quote(market,0)
    supplement={'annual_pools':{market.days[0]:annual(market,0,members=[entry(market,0,last_price=11.01)])}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert result['bars'][0]['limit_up_state'] is True # Existing closing-marker contract remains unchanged.
    assert result['bars'][0]['pattern_status']['one_price_limit_up']['state'] is None
    assert not result['pattern_events']


def test_conflicting_daily_ohlc_cannot_hide_behind_identical_close():
    market=LimitMarket();flat_quote(market,0)
    market.rows.append({**market.rows[0],'low':10.99})
    result=enrich_chart_limits(market,market.chart(),{'annual_pools':{market.days[0]:annual(market,0)}})
    assert result['bars'][0]['limit_up_state'] is True
    assert all(state['state'] is None for state in result['bars'][0]['pattern_status'].values())
    assert not result['pattern_events']


def test_pattern_proof_keeps_raw_prices_under_forward_display():
    market=LimitMarket();flat_quote(market,0)
    supplement={'annual_pools':{market.days[0]:annual(market,0)}}
    unadjusted=enrich_chart_limits(market,market.chart(),supplement)
    forward=enrich_chart_limits(market,market.chart(price_basis='forward'),supplement)
    assert forward['pattern_events'][0]['raw_ohlc']==unadjusted['pattern_events'][0]['raw_ohlc']
    assert forward['pattern_events'][0]['chart_price']==5.5
    assert unadjusted['pattern_events'][0]['chart_price']==11


@pytest.mark.parametrize('period',['W','M'])
def test_week_month_only_summarize_exact_daily_patterns(period):
    market=LimitMarket();flat_quote(market,0);flat_quote(market,2,9)
    supplement={'annual_pools':{market.days[0]:annual(market,0)},'annual_down_pools':{market.days[2]:annual(market,2,'limitdown')}}
    chart=market.chart(period=period)
    if period=='M':
        from app.analytics.monthly import aggregate_monthly
        chart['bars']=aggregate_monthly(market.rows,market.days,market.days[-1])
    result=enrich_chart_limits(market,chart,supplement);first=result['bars'][0]
    assert {event['date'] for event in first['pattern_events']}=={market.days[0],market.days[2]}
    assert all(event['bar_date']==first['date'] and event['chart_price'] is None for event in first['pattern_events'])
    assert first['pattern_summary']['one_price_limit_up']['count']==1
    assert first['pattern_summary']['one_price_limit_down']['count']==1
    assert all(state['state'] is None and state['status']=='daily_summary' for state in first['pattern_status'].values())


def test_pattern_index_snapshot_boundary_and_future_dates_stay_isolated():
    market=LimitMarket();flat_quote(market,0)
    chart=market.chart();chart['instrument']={'kind':'index','instrument_id':'000001.SH'}
    result=enrich_chart_limits(market,chart,{})
    assert result['pattern_coverage']['applicable'] is False and not result['pattern_events']
    assert market.raw_calls==0
    market.status=lambda:{'snapshot_id':'different-generation'}
    result=enrich_chart_limits(market,market.chart(),{'annual_pools':{market.days[0]:annual(market,0)}})
    assert not result['pattern_events']
    assert all(state['state'] is None for state in result['bars'][0]['pattern_status'].values())
    del market.status
    supplement={'annual_pools':{market.days[0]:annual(market,0)}}
    result=enrich_chart_limits(market,market.chart(as_of=market.days[0]),supplement)
    assert all(event['date']<=market.days[0] for event in result['pattern_events'])
    assert result['pattern_coverage']['expected_trade_days']==1


def earth_market(*, previous=11.21, rate=10, index=7):
    from decimal import Decimal, ROUND_HALF_UP
    market=LimitMarket();market.list_date='2000-01-01'
    prior=Decimal(str(previous));ratio=Decimal(str(rate))/100
    upper=float((prior*(1+ratio)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    lower=float((prior*(1-ratio)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    flat_quote(market,index-1,previous)
    market.rows[index].update(open=previous,high=upper,low=lower,close=upper,
                              corporate_action=False,corporate_action_coverage_known=True,source='fuyao')
    supplement={'annual_pools':{market.days[index]:annual(market,index,raw_sha256='source-sha',raw_artifact='raw/source.json')}}
    return market,supplement,index


def test_earth_to_sky_decimal_rounding_retains_independent_closing_event_and_derived_evidence():
    # Controlled fixture only: the values exercise the real-world reported
    # 11.21 -> 10.09 / 12.33 case without importing any runtime market data.
    market,supplement,index=earth_market()
    result=enrich_chart_limits(market,market.chart(),supplement);bar=result['bars'][index]
    assert bar['limit_up_state'] is True and bar['limit_down_state'] is None
    assert bar['pattern_status']['earth_to_sky']['state'] is True
    event=bar['pattern_events'][0]
    assert event['kind']=='earth_to_sky' and event['raw_low']==10.09 and event['raw_close']==12.33
    band=event['evidence']['limit_band']
    assert band['basis']=='derived-symmetric-band-from-verified-close-and-previous-session-v1'
    assert band['rate_pct']==10 and band['limit_up_price']==12.33 and band['limit_down_price']==10.09
    assert band['previous_raw_close']==11.21 and band['previous_trade_date']==market.days[index-1]
    assert band['current_st_or_code_used_to_guess_rate'] is False
    assert event['evidence']['closing_pool'][0]['raw_sha256']=='source-sha'
    assert [event['kind'] for event in bar['limit_events']]==['limit_up']
    assert not any(event['kind'].startswith('one_price') for event in bar['pattern_events'])


@pytest.mark.parametrize('rate',[5,20,30])
def test_earth_standard_symmetric_rates_do_not_use_current_name_or_code_to_guess_regime(rate):
    market,supplement,index=earth_market(previous=10,rate=rate)
    result=enrich_chart_limits(market,market.chart(),supplement)
    event=result['bars'][index]['pattern_events'][0]
    assert event['kind']=='earth_to_sky' and event['evidence']['limit_band']['rate_pct']==rate
    assert event['raw_low']==10*(1-rate/100)


@pytest.mark.parametrize(('field','delta','expected'),[('low',.01,False),('low',-.01,None),('high',.01,None),('close',.01,None)])
def test_earth_one_cent_discrepancies_never_round_into_a_positive(field,delta,expected):
    market,supplement,index=earth_market()
    market.rows[index][field]=round(market.rows[index][field]+delta,2)
    if field=='close':
        market.rows[index]['high']=market.rows[index]['close']
        supplement['annual_pools'][market.days[index]]=annual(market,index)
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert result['bars'][index]['pattern_status']['earth_to_sky']['state'] is expected
    assert not result['bars'][index]['pattern_events']


@pytest.mark.parametrize('case',[
    'corporate_action','unknown_actions','missing_actions','zero_volume','missing_previous',
    'previous_zero_volume','previous_adjusted','previous_identity','current_identity','source_one_cent',
    'multiple_source_one_cent','ambiguous_low_price','missing_listing','bad_listing','future_listing','missing_snapshot',
])
def test_earth_missing_or_ambiguous_prerequisites_remain_unknown(case):
    market,supplement,index=earth_market()
    day=market.days[index]
    if case=='corporate_action':market.rows[index]['corporate_action']=True
    if case=='unknown_actions':market.rows[index]['corporate_action_coverage_known']=False
    if case=='missing_actions':market.rows[index].pop('corporate_action_coverage_known')
    if case=='zero_volume':market.rows[index]['volume']=0
    if case=='missing_previous':market.rows.pop(index-1);index-=1
    if case=='previous_zero_volume':market.rows[index-1]['volume']=0
    if case=='previous_adjusted':market.rows[index-1]['price_basis']='forward_event_affine'
    if case=='previous_identity':market.rows[index-1]['instrument_id']=OTHER
    if case=='current_identity':market.rows[index]['thscode']=OTHER
    if case=='source_one_cent':supplement['annual_pools'][day]['items'][0]['last_price']=12.34
    if case=='multiple_source_one_cent':
        supplement['public_pools']={day:{'date':day,'items':[{'instrument_id':SYMBOL,'date':day,'close':12.34}]}}
    if case=='ambiguous_low_price':
        flat_quote(market,index-1,.10)
        market.rows[index].update(open=.10,high=.11,low=.09,close=.11)
        supplement['annual_pools'][day]=annual(market,index)
    if case=='missing_listing':market.list_date=None
    if case=='bad_listing':market.list_date='2026-02-31'
    if case=='future_listing':market.list_date='2027-01-01'
    chart=market.chart()
    if case=='missing_snapshot':chart['snapshot_id']=None
    result=enrich_chart_limits(market,chart,supplement)
    bar=next(row for row in result['bars'] if row['date']==day)
    assert bar['pattern_status']['earth_to_sky']['state'] is None
    assert not bar['pattern_events']


@pytest.mark.parametrize('index',[1,4,5])
def test_earth_excludes_ipo_first_five_days_and_retained_calendar_too_short(index):
    market,supplement,index=earth_market(index=index)
    market.list_date=market.days[0]
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert result['bars'][index]['pattern_status']['earth_to_sky']['state'] is (True if index==5 else None)


def test_earth_uses_raw_price_under_adjustment_and_week_month_daily_summary():
    market,supplement,index=earth_market()
    forward=enrich_chart_limits(market,market.chart(price_basis='forward'),supplement)
    event=forward['bars'][index]['pattern_events'][0]
    assert event['raw_low']==10.09 and event['chart_price']==10.09/2
    for period in ('W','M'):
        chart=market.chart(period=period)
        if period=='M':
            from app.analytics.monthly import aggregate_monthly
            chart['bars']=aggregate_monthly(market.rows,market.days,market.days[-1])
        result=enrich_chart_limits(market,chart,supplement)
        bar=next(bar for bar in result['bars'] if bar['pattern_events'])
        assert bar['pattern_summary']['earth_to_sky']['count']==1
        assert bar['pattern_events'][0]['date']==market.days[index]
        assert bar['pattern_events'][0]['chart_price'] is None


def test_focus_day_is_identical_to_full_chart_without_historical_pool_walk():
    market,supplement,index=earth_market();day=market.days[index]
    full=enrich_chart_limits(market,market.chart(as_of=day),supplement)
    target=full['bars'][-1]
    class ReadHistory(dict):
        def __init__(self):super().__init__();self.reads=[]
        def get(self,key,default=None):self.reads.append(key);return super().get(key,default)
    history=ReadHistory();market.ref['pool_history']=history
    chart=market.chart(as_of=day);chart['bars']=[chart['bars'][-1]]
    focused=enrich_chart_limits(market,chart,supplement,focus_day=day)
    assert history.reads==[day]
    assert focused['bars'][0]['pattern_events']==target['pattern_events']
    assert focused['bars'][0]['pattern_status']==target['pattern_status']
    assert focused['pattern_coverage']['scope']=='observation_day'
    assert focused['pattern_coverage']['expected_trade_days']==1
    assert focused['limit_event_coverage']['expected_trade_days']==1
    # The exact requested day is absent: an older chart row must never become
    # the observation day's shape merely because it is the latest available.
    market.rows=[row for row in market.rows if row['date']!=day]
    chart=market.chart(as_of=day);chart['bars']=[chart['bars'][-1]]
    missing=enrich_chart_limits(market,chart,supplement,focus_day=day)
    assert missing['pattern_events']==[] and missing['pattern_coverage']['valid_ohlcv_days']==0
    assert all(state['state'] is None for state in missing['bars'][0]['pattern_status'].values())


@pytest.mark.parametrize('bad',[
    {'snapshot_id':None}, {'snapshot_id':''}, {'snapshot_id':'   '},
    {'instrument':{'kind':'fund'}}, {'instrument':{'kind':None}},
    {'instrument':{'thscode':OTHER}},
])
def test_one_price_also_requires_explicit_stock_identity_and_determinate_generation(bad):
    market=LimitMarket();flat_quote(market,0)
    chart=market.chart()
    if 'instrument' in bad:chart['instrument'].update(bad['instrument'])
    else:chart.update(bad)
    result=enrich_chart_limits(market,chart,{'annual_pools':{market.days[0]:annual(market,0)}})
    assert result['bars'][0]['limit_up_state'] is True
    assert result['pattern_events']==[]
    assert all(state['state'] is None for state in result['bars'][0]['pattern_status'].values())


@pytest.mark.parametrize('bad',[
    {'thscode':OTHER}, {'ticker':OTHER[:6]}, {'code':OTHER[:6]},
    {'date':None}, {'trade_date':'2026-01-06'}, {'date_ms':1767664800000},
])
def test_explicit_source_identity_and_date_conflicts_block_patterns_only(bad):
    market=LimitMarket();flat_quote(market,0)
    row=entry(market,0,**bad)
    supplement={'annual_pools':{market.days[0]:annual(market,0,members=[row])}}
    result=enrich_chart_limits(market,market.chart(),supplement)
    assert result['bars'][0]['limit_up_state'] is True
    assert not result['pattern_events']
    status=result['bars'][0]['pattern_status']['one_price_limit_up']
    assert status['state'] is None and '显式证券或日期' in status['reason']


def test_pattern_validation_metadata_is_explicit_and_bound_to_requested_date_and_identity():
    market=LimitMarket();flat_quote(market,0)
    result=enrich_chart_limits(market,market.chart(),{'annual_pools':{market.days[0]:annual(market,0)}})
    proof=result['pattern_events'][0]['evidence']['closing_pool'][0]
    assert proof['pattern_validation']=={'instrument_id':SYMBOL,'trade_date':market.days[0],
                                          'source_identity_valid':True,'source_dates_valid':True}


@pytest.mark.parametrize('focus',[False,True])
def test_quote_date_fallback_never_becomes_a_verified_market_calendar_for_shapes(focus):
    market,supplement,index=earth_market();day=market.days[index]
    market.calendar=lambda:[]
    chart=market.chart(as_of=day)
    if focus:chart['bars']=[chart['bars'][-1]]
    result=enrich_chart_limits(market,chart,supplement,**({'focus_day':day} if focus else {}))
    assert result['bars'][-1]['limit_up_state'] is True # Existing fallback is unchanged.
    assert not result['pattern_events'] and result['pattern_coverage']['expected_trade_days']==0
    assert all(state['state'] is None for state in result['bars'][-1]['pattern_status'].values())


@pytest.mark.parametrize('focus',[False,True])
def test_market_calendar_missing_a_known_quote_day_cannot_skip_to_an_older_close(focus):
    market,supplement,index=earth_market();day=market.days[index]
    flat_quote(market,index-2,11.21) # Would falsely produce the same 10% band if the true previous session were skipped.
    market.calendar=lambda:[session for session in market.days if session!=market.days[index-1]]
    chart=market.chart(as_of=day)
    if focus:chart['bars']=[chart['bars'][-1]]
    result=enrich_chart_limits(market,chart,supplement,**({'focus_day':day} if focus else {}))
    status=result['bars'][-1]['pattern_status']['earth_to_sky']
    assert status['state'] is None and '日历遗漏' in status['reason']
    assert not result['bars'][-1]['pattern_events']
