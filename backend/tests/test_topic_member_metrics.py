"""Dated member metrics use exact endpoints and verified source-pool evidence."""
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from test_topic_members import memberships, query
from test_workspace import ws

A='600001.SH';B='000001.SZ';C='300001.SZ';D='688001.SH';E='920001.BJ'


def metric_calendar():
    days=['2025-12-30','2025-12-31'];current=date(2026,1,5)
    while len(days)<49:
        if current.weekday()<5:days.append(current.isoformat())
        current+=timedelta(days=1)
    return days


class MetricMarket:
    """No I/O: batch reads are counted, per-security history reads are forbidden."""
    def __init__(self):
        self.days=metric_calendar();self.day=self.days[-3];self.calls=Counter()
        self.data={};self.events={};self.reference_data={};self.history_requests=[]
        self.master={symbol:{'instrument_id':symbol,'code':symbol[:6],'name':symbol,
                             'kind':'stock','list_date':self.days[22] if symbol==C else '2020-01-01'}
                     for symbol in (A,B,C,D,E)}
        for j,symbol in enumerate(self.master):
            values=[]
            for i,day in enumerate(self.days):
                if symbol==C and i<22:continue
                if symbol==D and i==1:continue
                if symbol==E and day==self.day:continue
                close=10+j*5+i*.2
                values.append({'instrument_id':symbol,'date':day,'open':close-.1,
                               'high':close+.2,'low':close-.2,'close':close,
                               'volume':1000+i,'amount':close*(1000+i),
                               'corporate_action':False})
            self.data[symbol]=values

    def calendar(self):return list(self.days)
    def status(self):return {'snapshot_id':'metric-fixture','data_end':self.days[-1]}
    def instruments(self,query='',limit=10000):
        self.calls['instruments']+=1;return deepcopy(list(self.master.values()))
    def panel(self,as_of):
        self.calls['panel']+=1
        return [deepcopy(row) for values in self.data.values() for row in values if row['date']==as_of]
    def history_panel(self,as_of=None,lookback=None):
        raise AssertionError('Member metrics must request only the selected instrument histories')
    def history_for_instruments(self,symbols,start=None,end=None):
        self.calls['history_for_instruments']+=1
        self.history_requests.append({'symbols':set(symbols),'start':start,'end':end})
        return {symbol:[deepcopy(row) for row in self.data.get(symbol,[])
                        if (not start or row['date']>=start) and (not end or row['date']<=end)]
                for symbol in symbols}
    def actions_for_instruments(self,symbols):
        self.calls['actions_for_instruments']+=1
        return {symbol:deepcopy(self.events.get(symbol,[])) for symbol in symbols}
    def bars(self,*args,**kwargs):
        raise AssertionError('Member list must batch its histories, not fetch one stock at a time')
    def actions(self,symbol,start=None,end=None):
        self.calls['actions']+=1
        return [deepcopy(event) for event in self.events.get(symbol,[])
                if (not start or event['date']>=start) and (not end or event['date']<=end)]
    def reference(self):return deepcopy(self.reference_data)


def action(day,**fields):
    return {'date':day,'dividend_per_share':1.0,'per_share_bonus':0.0,
            'allotment_ratio':0.0,'allotment_price':0.0,'currency':'CNY',**fields}


def close_at(market,symbol,day):
    return next(row['close'] for row in market.data[symbol] if row['date']==day)


def source_pool(market,day,symbols,*,complete=True,quarantine=(),empty=False):
    items=[] if empty else [{'instrument_id':symbol,'thscode':symbol,'ticker':symbol[:6],
                             'last_price':close_at(market,symbol,day),'date':day,
                             'source':'fuyao','pool_kind':'limitup'} for symbol in symbols]
    return {'date':day,'source':'fuyao','pool_kind':'limitup','items':items,
            'complete':complete,'pagination_complete':True,
            'raw_codes':sorted({symbol[:6] for symbol in symbols}|set(quarantine)),
            'ambiguous_code_rows':0,'rejected_codes':list(quarantine),
            'quarantine':[{'code':code,'reason':'fixture-isolated'} for code in quarantine]}


def evaluate(market,*,day=None,symbols=None,supplement=None):
    from app.services.topic_member_metrics import member_metrics
    day=day or market.day;symbols=symbols or list(market.master)
    quotes={row['instrument_id']:row for values in market.data.values() for row in values if row['date']==day}
    return member_metrics(market,day,symbols,market.master,quotes,supplement or {})


def complete_year(market,events=None):
    events=events or {}
    return {'snapshot_id':'metric-fixture','annual_pools':{
        day:source_pool(market,day,events.get(day,[B]))
        for day in market.days if day[:4]==market.day[:4] and day<=market.day}}


def test_exact_previous_year_close_and_market_thirty_session_endpoints():
    market=MetricMarket();row=evaluate(market,symbols=[A])[A]
    baseline='2025-12-31';thirty=market.days[market.days.index(market.day)-30]
    assert row['return_ytd_pct']==pytest.approx((close_at(market,A,market.day)/close_at(market,A,baseline)-1)*100,rel=1e-10,abs=1e-10)
    assert row['change_30d_pct']==pytest.approx((close_at(market,A,market.day)/close_at(market,A,thirty)-1)*100,rel=1e-10,abs=1e-10)
    assert row['return_ytd_coverage']['start']==baseline
    assert row['change_30d_coverage']['start']==thirty
    assert row['return_ytd_coverage']['end']==row['change_30d_coverage']['end']==market.day
    assert row['return_ytd_coverage']['complete'] and row['change_30d_coverage']['complete']
    assert row['return_ytd_reason'] is None and row['change_30d_reason'] is None


def test_missing_prior_year_quote_or_listing_this_year_never_uses_first_available_price():
    market=MetricMarket();output=evaluate(market,symbols=[C,D])
    assert output[C]['return_ytd_pct'] is None and output[C]['return_ytd_reason']
    assert output[C]['change_30d_pct'] is None and output[C]['change_30d_reason']
    assert output[D]['return_ytd_pct'] is None and output[D]['return_ytd_reason']
    assert output[D]['change_30d_pct'] is not None


def test_unproven_year_end_calendar_and_short_thirty_day_window_stay_unknown():
    market=MetricMarket();market.days.remove('2025-12-31')
    row=evaluate(market,symbols=[A])[A]
    assert row['return_ytd_pct'] is None and not row['return_ytd_coverage']['complete']
    assert row['return_ytd_reason']
    short=MetricMarket();short.day=short.days[12]
    row=evaluate(short,symbols=[A])[A]
    assert row['change_30d_pct'] is None and row['change_30d_reason']
    assert not row['change_30d_coverage']['complete']


def test_missing_thirty_day_endpoint_does_not_substitute_thirty_first_observation():
    market=MetricMarket();baseline=market.days[market.days.index(market.day)-30]
    market.data[A]=[row for row in market.data[A] if row['date']!=baseline]
    row=evaluate(market,symbols=[A])[A]
    assert row['change_30d_pct'] is None and row['change_30d_reason']
    assert row['change_30d_coverage']['start']==baseline
    assert row['return_ytd_pct'] is not None


def test_cash_action_adjusts_both_endpoint_windows_without_inventing_total_return():
    market=MetricMarket();market.events[A]=[action(market.days[25])]
    row=evaluate(market,symbols=[A])[A];close=close_at(market,A,market.day)
    thirty=market.days[market.days.index(market.day)-30]
    assert row['return_ytd_pct']==pytest.approx((close/(close_at(market,A,'2025-12-31')-1)-1)*100,rel=1e-10,abs=1e-10)
    assert row['change_30d_pct']==pytest.approx((close/(close_at(market,A,thirty)-1)-1)*100,rel=1e-10,abs=1e-10)
    assert row['return_ytd_coverage']['price_basis']=='forward_event_affine'
    assert row['change_30d_coverage']['price_basis']=='forward_event_affine'


@pytest.mark.parametrize('bad_action',[
    {'dividend_per_share':float('nan')},
    {'currency':'USD'},
    {'allotment_ratio':-1},
])
def test_unverified_actions_do_not_leave_silently_raw_cross_event_returns(bad_action):
    market=MetricMarket();market.events[A]=[action(market.days[25],**bad_action)]
    row=evaluate(market,symbols=[A])[A]
    assert row['return_ytd_pct'] is None and row['change_30d_pct'] is None
    assert row['return_ytd_reason'] and row['change_30d_reason']
    assert not row['return_ytd_coverage']['complete'] and not row['change_30d_coverage']['complete']


def test_missing_action_archive_or_bulk_history_does_not_fall_back_to_per_stock_reads(monkeypatch):
    market=MetricMarket();monkeypatch.setattr(market,'actions_for_instruments',lambda symbols:None)
    row=evaluate(market,symbols=[A])[A]
    assert row['return_ytd_pct'] is None and row['change_30d_pct'] is None
    assert row['return_ytd_reason'] and row['change_30d_reason']
    market=MetricMarket();monkeypatch.setattr(market,'history_for_instruments',None)
    row=evaluate(market,symbols=[A])[A]
    assert row['return_ytd_pct'] is None and row['change_30d_pct'] is None
    assert row['return_ytd_reason'] and row['change_30d_reason']


def test_no_business_day_quote_never_reuses_yesterdays_close():
    market=MetricMarket();row=evaluate(market,symbols=[E])[E]
    assert row['return_ytd_pct'] is None and row['change_30d_pct'] is None
    assert row['return_ytd_reason'] and row['change_30d_reason']
    from app.services.topic_member_metrics import member_metrics
    stale=next(row for row in reversed(market.data[E]) if row['date']<market.day)
    row=member_metrics(market,market.day,[E],market.master,{E:stale},{})[E]
    assert row['return_ytd_pct'] is None and row['change_30d_pct'] is None


def test_full_source_coverage_gives_exact_annual_count_and_latest_trade_session_distance():
    market=MetricMarket();last=market.days[-4]
    supplement=complete_year(market,{market.days[4]:[A],last:[A]})
    row=evaluate(market,symbols=[A],supplement=supplement)[A]
    assert row['limit_up_ytd']==row['limit_up_ytd_observed']==2
    assert row['limit_up_ytd_coverage']['complete']
    assert row['limit_up_ytd_coverage']['covered_days']==row['limit_up_ytd_coverage']['expected_days']
    assert row['last_limit_up_date']==last and row['sessions_since_limit_up']==1
    assert row['last_limit_up_complete'] and row['last_limit_up_coverage']['state']=='confirmed_latest'


def test_early_empty_pool_keeps_annual_lower_bound_but_does_not_invalidate_known_latest_suffix():
    market=MetricMarket();last=market.days[-4]
    supplement=complete_year(market,{market.days[4]:[A],last:[A]})
    gap=market.days[10];supplement['annual_pools'][gap]=source_pool(market,gap,[],empty=True)
    row=evaluate(market,symbols=[A],supplement=supplement)[A]
    assert row['limit_up_ytd'] is None and row['limit_up_ytd_observed']==2
    assert not row['limit_up_ytd_coverage']['complete'] and row['limit_up_ytd_reason']
    assert row['last_limit_up_date']==last and row['sessions_since_limit_up']==1
    assert row['last_limit_up_complete'] and row['last_limit_up_coverage']['state']=='confirmed_latest'


@pytest.mark.parametrize('gap_kind',['empty','isolated'])
def test_unknown_day_after_last_confirmed_event_cannot_claim_it_is_the_complete_latest(gap_kind):
    market=MetricMarket();last=market.days[-4]
    supplement=complete_year(market,{market.days[4]:[A],last:[A]})
    supplement['annual_pools'][market.day]=(source_pool(market,market.day,[],empty=True) if gap_kind=='empty'
        else source_pool(market,market.day,[B],complete=False,quarantine=[A[:6]]))
    row=evaluate(market,symbols=[A],supplement=supplement)[A]
    assert row['limit_up_ytd'] is None and row['limit_up_ytd_observed']==2
    assert row['last_limit_up_date']==last and row['sessions_since_limit_up']==1
    assert row['last_limit_up_complete'] is False
    assert row['last_limit_up_coverage']['state']=='latest_observed'
    assert row['last_limit_up_coverage']['unknown_days']>=1 and row['last_limit_up_reason']


@pytest.mark.parametrize('conflict',['price','date'])
def test_conflicting_or_future_events_cannot_be_counted_or_become_latest(conflict):
    market=MetricMarket();good=market.days[25];bad=market.days[42];future=market.days[-1]
    supplement=complete_year(market,{good:[A],bad:[A]})
    if conflict=='price':supplement['annual_pools'][bad]['items'][0]['last_price']+=1
    else:supplement['annual_pools'][bad]['items'][0]['date']=future
    supplement['annual_pools'][future]=source_pool(market,future,[A])
    row=evaluate(market,symbols=[A],supplement=supplement)[A]
    assert row['limit_up_ytd'] is None and row['limit_up_ytd_observed']==1
    assert row['last_limit_up_date']==good
    assert row['sessions_since_limit_up']==market.days.index(market.day)-market.days.index(good)
    assert not row['last_limit_up_complete']


def test_complete_source_absence_is_zero_count_but_does_not_invent_a_last_event():
    market=MetricMarket();row=evaluate(market,symbols=[A],supplement=complete_year(market))[A]
    assert row['limit_up_ytd']==row['limit_up_ytd_observed']==0
    assert row['limit_up_ytd_coverage']['complete']
    assert row['last_limit_up_date'] is None and row['sessions_since_limit_up'] is None
    assert row['last_limit_up_coverage']['state']=='no_confirmed_event'


def test_future_price_or_action_changes_do_not_rewrite_historical_member_metrics():
    market=MetricMarket();before=evaluate(market,symbols=[A])[A]
    for row in market.data[A]:
        if row['date']>market.day:
            for field in ('open','high','low','close'):row[field]*=10000
    market.events[A]=[action(market.days[-1],dividend_per_share=float('nan'))]
    after=evaluate(market,symbols=[A])[A]
    for field in ('return_ytd_pct','change_30d_pct','limit_up_ytd_observed','last_limit_up_date','sessions_since_limit_up'):
        assert after[field]==before[field]


def test_member_count_does_not_multiply_history_or_action_queries():
    for symbols in ([A],[A,B,C,D,E]):
        market=MetricMarket();evaluate(market,symbols=symbols)
        assert market.calls['history_for_instruments']==1
        assert market.calls['actions_for_instruments']==1
        assert market.calls['actions']==market.calls['panel']==market.calls['instruments']==0
        assert market.history_requests[0]['symbols']==set(symbols)
        assert market.history_requests[0]['end']==market.day


@pytest.mark.parametrize('sort_by',['return_ytd_pct','change_30d_pct','limit_up_ytd','last_limit_up_date','sessions_since_limit_up'])
@pytest.mark.parametrize('order',['asc','desc'])
def test_member_metrics_sort_all_values_before_unknowns_and_never_sort_lower_bound_as_exact(memberships,monkeypatch,sort_by,order):
    from app.services import topic_members, topic_member_metrics
    _,client,ids,_,_=memberships
    def metrics(_market,_day,symbols,_master,_quotes,_supplement):
        return {symbol:{sort_by:('2026-01-05' if symbol==A else '2026-01-06') if sort_by=='last_limit_up_date' and symbol in (A,B)
                        else 1 if symbol==A else 2 if symbol==B else None,
                        'limit_up_ytd_observed':999 if symbol==C else 0} for symbol in symbols}
    monkeypatch.setattr(topic_member_metrics,'member_metrics',metrics)
    monkeypatch.setattr(topic_members,'member_metrics',metrics,raising=False)
    output=query(client,ids['概念甲'],ids['概念乙'],sort_by=sort_by,sort_order=order)
    assert [row['instrument_id'] for row in output['items']]==([A,B] if order=='asc' else [B,A])+[C,D,E]


@pytest.fixture
def cached_market(tmp_path,monkeypatch):
    from app.analytics.market import MarketData
    from app.services import topic_member_metrics as service
    service.clear_metric_cache()
    def make(directory='data',snapshot='metric-cache'):
        source=MetricMarket();data_root=tmp_path/directory;folder=data_root/'published'/snapshot
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'manifest.json').write_text(json.dumps({'summary':{'snapshot_id':snapshot,'data_end':source.days[-1]}}))
        market=MarketData(data_root,snapshot_id=snapshot)
        for name in ('calendar','history_for_instruments','actions_for_instruments','reference'):
            monkeypatch.setattr(market,name,getattr(source,name))
        return market,source
    yield make
    service.clear_metric_cache()


def cached_evaluate(market,source,*,day=None,symbols=None,supplement=None,quotes=None):
    from app.services.topic_member_metrics import member_metrics
    day=day or source.day;symbols=symbols or [A]
    if quotes is None:
        quotes={row['instrument_id']:deepcopy(row) for values in source.data.values() for row in values if row['date']==day}
    return member_metrics(market,day,symbols,source.master,quotes,supplement or {})


def test_real_fixed_snapshot_cache_reuses_candidate_set_and_returned_nested_mutations_are_isolated(cached_market):
    market,source=cached_market()
    first=cached_evaluate(market,source,symbols=[A,B]);original=deepcopy(first)
    first[A]['return_ytd_pct']=-999
    first[A]['return_ytd_coverage']['complete']=False
    first[A]['last_limit_up_coverage']['unknown_days']=-999
    first.clear()
    second=cached_evaluate(market,source,symbols=[B,A,A])
    assert second==original
    second[B]['change_30d_coverage']['reason']='mutated-by-caller'
    assert cached_evaluate(market,source,symbols=[A,B])==original
    assert source.calls['history_for_instruments']==source.calls['actions_for_instruments']==1


@pytest.mark.parametrize('change',['date','quote_close','quote_date','candidates','supplement'])
def test_metric_cache_misses_when_dated_inputs_or_evidence_change(cached_market,change):
    market,source=cached_market();first=cached_evaluate(market,source)
    kwargs={}
    if change=='date':kwargs['day']=source.days[-4]
    elif change=='candidates':kwargs['symbols']=[A,B]
    elif change in ('quote_close','quote_date'):
        quotes={row['instrument_id']:deepcopy(row) for values in source.data.values() for row in values if row['date']==source.day}
        if change=='quote_close':quotes[A]['close']+=1
        else:quotes[A]['date']=source.days[-4]
        kwargs['quotes']=quotes
    else:
        last=source.days[-4]
        kwargs['supplement']={'created_at_ms':1,'annual_pools':{last:source_pool(source,last,[A])}}
    second=cached_evaluate(market,source,**kwargs)
    assert source.calls['history_for_instruments']==source.calls['actions_for_instruments']==2
    if change in ('quote_close','quote_date'):
        assert first[A]['return_ytd_pct'] is not None and second[A]['return_ytd_pct'] is None
    elif change=='candidates':assert set(second)=={A,B}
    elif change=='supplement':assert first[A]['last_limit_up_date'] is None and second[A]['last_limit_up_date']==source.days[-4]
    else:assert first[A]['return_ytd_coverage']['end']!=second[A]['return_ytd_coverage']['end']


def test_metric_cache_hashes_source_content_not_just_publication_timestamp(cached_market):
    market,source=cached_market();last=source.days[-4]
    evidence={'created_at_ms':7,'annual_pools':{last:source_pool(source,last,[A])}}
    first=cached_evaluate(market,source,supplement=evidence)
    assert first[A]['last_limit_up_date']==last
    changed=deepcopy(evidence);changed['annual_pools'][last]['items'][0]['last_price']+=1
    second=cached_evaluate(market,source,supplement=changed)
    assert second[A]['last_limit_up_date'] is None
    assert source.calls['history_for_instruments']==2


@pytest.mark.parametrize('other_directory,other_snapshot',[('other-data','metric-cache'),('data','other-snapshot')])
def test_metric_cache_never_crosses_data_directory_or_fixed_generation(cached_market,other_directory,other_snapshot):
    first,first_source=cached_market();cached_evaluate(first,first_source)
    second,second_source=cached_market(other_directory,other_snapshot);cached_evaluate(second,second_source)
    assert first_source.calls['history_for_instruments']==second_source.calls['history_for_instruments']==1


def test_metric_cache_keeps_four_recent_results_and_recomputes_evicted_day(cached_market):
    from app.services import topic_member_metrics as service
    market,source=cached_market();days=source.days[37:42]
    for day in days[:4]:cached_evaluate(market,source,day=day)
    cached_evaluate(market,source,day=days[0])
    assert source.calls['history_for_instruments']==4
    cached_evaluate(market,source,day=days[4])
    assert len(service._METRIC_CACHE)==4
    assert source.calls['history_for_instruments']==5
    cached_evaluate(market,source,day=days[0])
    assert source.calls['history_for_instruments']==5
    cached_evaluate(market,source,day=days[1])
    assert source.calls['history_for_instruments']==6 and len(service._METRIC_CACHE)==4


def test_fake_objects_and_unproven_fixed_paths_bypass_cache(cached_market):
    fake=MetricMarket();evaluate(fake,symbols=[A]);evaluate(fake,symbols=[A])
    assert fake.calls['history_for_instruments']==2
    market,source=cached_market();(market.data_dir/'published'/market._fixed_snapshot/'manifest.json').unlink()
    cached_evaluate(market,source);cached_evaluate(market,source)
    assert source.calls['history_for_instruments']==2


def test_cache_does_not_retain_results_for_more_than_2048_candidates(cached_market):
    from app.services import topic_member_metrics as service
    market,source=cached_market()
    symbols=[f'{i:06d}.SH' for i in range(2049)]
    assert service._cache_key(market,source.day,symbols,{}, {}) is None
    assert not service._METRIC_CACHE
