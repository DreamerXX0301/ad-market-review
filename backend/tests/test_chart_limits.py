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
        return {'instrument':{'instrument_id':symbol,'name':'*ST当前名称不回推历史','kind':'stock'},'period':period,'as_of':cutoff,'bars':display,'snapshot_id':'fixture-generation','price_basis':'forward_event_affine' if price_basis=='forward' else 'none','warnings':[]}


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
    monkeypatch.setattr(chart_limits,'load_supplement',lambda snapshot:{'quality':'unknown','failure_reason':'补充数据索引缺失或校验失败'})
    result=enrich_chart_limits(market,market.chart())
    assert result['bars'][0]['limit_up_state'] is True
    assert result['bars'][1]['limit_up_state'] is None
    assert any('补充数据索引缺失或校验失败' in warning for warning in result['warnings'])


def test_router_chart_response_contains_overlay_without_analytics_coupling(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.platform import router
    from app.services import market_service,chart_limits
    market=LimitMarket();day=market.days[0]
    market.ref={'pool_trade_date':day,'limitup':[entry(market,0)]}
    monkeypatch.setattr(market_service,'context',lambda as_of:(market,{},as_of or market.days[-1]))
    monkeypatch.setattr(chart_limits,'load_supplement',lambda snapshot:{})
    app=FastAPI();app.include_router(router)
    response=TestClient(app).get('/api/v1/instruments/'+SYMBOL+'/chart')
    assert response.status_code==200,response.text
    assert response.json()['bars'][0]['limit_up_state'] is True
    assert response.json()['limit_events'][0]['basis']=='verified-source-close-pool'
