"""Explainable proxy phases and current-member event shortcuts, no remote data."""
from copy import deepcopy
from datetime import date,timedelta
import pytest

from app.services.emotion import describe_emotion
from app.services.hot_topics import rank_hot_topics
from test_topic_members import memberships
from test_workspace import ws


def breadth(up,down,flat=0,**extra):
    return {'up':up,'down':down,'flat':flat,'total':up+down+flat,'scope':'same-observed-scope',**extra}


@pytest.mark.parametrize('current,previous,up,down,full,expected',[
    (breadth(300,3700),breadth(3600,400),None,None,False,'冰点'),
    (breadth(3600,400),None,100,3,True,'高潮'),
    (breadth(3000,1500),None,None,None,False,'普涨'),
    (breadth(2000,2000),breadth(800,3200),None,None,False,'修复'),
    (breadth(2000,3000),breadth(3750,1250),None,None,False,'退潮'),
    (breadth(2000,2500),None,None,None,False,'分化'),
    (breadth(300,100,total=5000,unknown=4600),None,None,None,False,'待核验'),
])
def test_proxy_phase_rules_are_explicit_and_ordered(current,previous,up,down,full,expected):
    result=describe_emotion(current,previous,limit_up=up,limit_down=down,event_pools_complete=full)
    assert result['phase']==result['cycle']==expected
    assert result['phase_reason'] and result['rules'] and '自定义' in result['notice']
    assert {item['phase'] for item in result['rules']}=={'待核验','冰点','高潮','修复','退潮','普涨','分化'}
    assert all(item['url'].startswith('https://') for item in result['references'])


def test_missing_pools_do_not_turn_high_breadth_into_confirmed_climax():
    reading=describe_emotion(breadth(3600,400),limit_up=100,limit_down=0,event_pools_complete=False)
    assert reading['phase']=='普涨' and not reading['event_pools_complete']
    assert describe_emotion(breadth(0,0))['temperature'] is None
    assert describe_emotion(breadth(0,0))['cycle']=='待核验'


def test_cross_scope_or_low_coverage_prior_day_cannot_create_a_repair_transition():
    current=breadth(2000,2000)
    for previous in (breadth(800,3200,scope='different-scope'),breadth(800,3200,total=10000)):
        result=describe_emotion(current,previous)
        assert result['temperature_delta'] is None and result['previous_temperature'] is None
        assert result['phase']=='分化'


def test_emotion_uses_one_cached_same_scope_history_without_second_panel_and_keeps_unknown_universe(monkeypatch):
    from app.services import market_service
    calls=[];previous='2026-01-30';day='2026-02-02'
    class Market:
        def breadth_history(self,observed):
            calls.append(observed)
            return [{'date':previous,**breadth(800,3200),'observed':4000},{'date':day,**breadth(2000,2000),'observed':4000}]
        def calendar(self):return [previous,day]
        def instruments(self,**kwargs):return [{'instrument_id':'600001.SH','kind':'stock','list_date':None}]
        def panel(self,*args):raise AssertionError('No repeated current/previous panel computation')
    monkeypatch.setattr(market_service,'panel',lambda _, **_kwargs: (Market(),{'snapshot_id':'fixture','data_end':day},day,{},[]))
    result=market_service.market_page('emotion',day)
    assert calls==[day] and result['emotion']['phase']=='修复'
    assert result['breadth']['expected_current_universe'] is None
    assert result['breadth']['missing'] is None
    assert result['summary']['comparable_count']==4000
    assert result['summary']['comparable_coverage_pct']==100


def days(number=30):
    result=[];current=date(2026,1,5)
    while len(result)<number:
        if current.weekday()<5:result.append(current.isoformat())
        current+=timedelta(days=1)
    return result


def test_hot_rank_deduplicates_stock_days_excludes_future_industry_archives_and_uses_current_members():
    calendar=days();day=calendar[-1];window=calendar[-20:]
    topics=[{'topic_id':'a','name':'概念甲','kind':'concept','instrument_ids':['A','B'],'members_complete':True},
            {'topic_id':'b','name':'人工乙','kind':'manual','instrument_ids':['B','C'],'members_complete':True},
            {'topic_id':'partial','name':'未完整','kind':'concept','instrument_ids':['A'],'members_complete':False},
            {'topic_id':'industry','name':'行业','kind':'industry','instrument_ids':['A','B','C']},
            {'topic_id':'archived','name':'归档','archived':True,'instrument_ids':['A','B','C']},
            {'topic_id':'cold','name':'无证据','instrument_ids':['Z']}]
    events=[{'instrument_id':symbol,'date':observed} for symbol,observed in [('A',window[0]),('A',window[0]),('B',window[1]),('C',window[2]),('C',window[3]),('Z',calendar[0]),('Z','2027-01-01')]]
    result=rank_hot_topics(topics,events,calendar,day)
    assert [item['topic_id'] for item in result['items']]==['b','a','partial']
    assert [item['event_count'] for item in result['items']]==[3,2,1]
    assert result['items'][0]['active_stock_count']==2
    assert result['items'][2]['members_complete'] is False
    assert result['window']['start']==window[0] and result['window']['complete']
    assert all(item['membership_basis']=='current_members_proxy' for item in result['items'])
    assert any('不代表当时' in warning for warning in result['warnings'])


def test_no_confirmed_events_never_gets_static_hot_shortcuts_or_fabricated_zeros():
    result=rank_hot_topics([{'topic_id':'x','name':'某题材','instrument_ids':['A']}],[],days(4),days(4)[-1])
    assert result['items']==[] and result['total']==0
    assert result['window']['observed_trade_days']==4 and not result['window']['complete']


@pytest.fixture
def hot_source(memberships,monkeypatch):
    from app.services import market_supplement
    market,client,ids,_,_=memberships
    calendar=[observed for observed in market.days if observed<=market.entry][-20:]
    a='600001.SH';b='000001.SZ';prices={a:10,b:20};calls=[]
    def record(observed,symbols):
        return {'date':observed,'pool_kind':'limitup','source':'fuyao','complete':True,'pagination_complete':True,
                'raw_codes':[symbol[:6] for symbol in symbols],'ambiguous_code_rows':0,'quarantine':[],
                'items':[{'instrument_id':symbol,'thscode':symbol,'date':observed,'last_price':prices[symbol]} for symbol in symbols]}
    document={'snapshot_id':'fixture-generation','annual_pools':{observed:record(observed,[a,b] if index in (2,18) else [b]) for index,observed in enumerate(calendar)}}
    monkeypatch.setattr(market_supplement,'load_supplement',lambda _,**kwargs:deepcopy(document))
    def history(symbols,start=None,end=None):
        calls.append((set(symbols),start,end))
        return {symbol:[{'date':observed,'close':prices[symbol]} for observed in calendar if start<=observed<=end] for symbol in symbols}
    monkeypatch.setattr(market,'history_for_instruments',history,raising=False)
    return market,client,ids,document,calls,calendar


def test_hot_shortcuts_apply_effective_manual_exclusion_to_current_proxy(hot_source):
    from app.services.hot_topics import list_hot_topics
    market,client,ids,document,calls,calendar=hot_source
    before=list_hot_topics();first=next(item for item in before['items'] if item['topic_id']==ids['概念甲'])
    assert first['event_count']==22
    excluded=client.post('/api/v1/topics/'+ids['概念甲']+'/members',json={'instrument_id':'600001.SH','action':'exclude'})
    assert excluded.status_code==200
    after=list_hot_topics();first=next(item for item in after['items'] if item['topic_id']==ids['概念甲'])
    assert first['event_count']==20 and after['business_date']==market.entry
    assert all(item['topic_id']!=ids['行业甲'] for item in after['items'])
    assert len(calls)==2 and all(call[1:]==(calendar[0],market.entry) for call in calls)


@pytest.mark.parametrize('failure',['empty','wrong_price','future_date'])
def test_hot_source_gaps_and_invalid_positive_stay_visible_without_fabricated_events(hot_source,failure):
    from app.services.hot_topics import list_hot_topics
    _,_,ids,document,_,calendar=hot_source
    affected=calendar[2];record=document['annual_pools'][affected]
    if failure=='empty':record.update(items=[],complete=False)
    elif failure=='wrong_price':record['items'][0]['last_price']=999
    else:record['items'][0]['date']='2027-01-01'
    result=list_hot_topics();topic=next(item for item in result['items'] if item['topic_id']==ids['概念甲'])
    assert topic['event_count']==(20 if failure=='empty' else 21)
    assert not result['coverage']['complete']
    assert result['coverage']['missing_dates'] or result['coverage']['unverified_candidate_stock_days']
