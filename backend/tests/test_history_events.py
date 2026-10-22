"""Historical event fixtures are local, daily and strictly observation-bounded."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.domain.conditions import evaluate, metrics_for, names, specs, validate
from app.domain.history_events import HistoryContext, read_start
from app.domain.tracking import condition_decisions
from app.analytics.backtest_engine import availability, prepare
from app.services.history_events import contexts_for_batch
from test_workspace import ws, advance


def days(count=125):
    result=[]; day=date(2026,1,5)
    while len(result)<count:
        if day.weekday()<5:result.append(day.isoformat())
        day+=timedelta(days=1)
    return result


def quotes(calendar):
    return [{'instrument_id':'600001.SH','date':day,'open':10+i/10,'high':11+i/10,
             'low':9+i/10,'close':10+i/10,'volume':100.,'amount':1000.,
             'corporate_action':False,'corporate_action_coverage_known':True}
            for i,day in enumerate(calendar)]


def event(from_=30,to=None,match='latest',where=None,then=None):
    return validate({'history_event':{'window':{'from':from_,'to':from_ if to is None else to},
        'match':match,'where':where or {'field':'volume_ratio','op':'>=','value':3},
        'then':then or {'field':'close','op':'>','right_event_field':'close'}}})


def check(tree,rows,calendar,as_of=None,period='D',context=None):
    as_of=as_of or calendar[-1]
    context=context or HistoryContext(rows,calendar,calendar[-1],'market-fixture')
    return evaluate(tree,metrics_for(rows,as_of,calendar,period),context.at(as_of))


def test_exact_30_market_sessions_combines_independent_today_conditions_and_event_proof():
    calendar=days();rows=quotes(calendar);rows[-31]['volume']=300
    tree=validate({'all':[event(),{'field':'change_pct','op':'>','value':0}]})
    result=check(tree,rows,calendar)
    assert result['result'] is True
    bound=result['checks'][0]
    assert bound['selected_event']['date']==calendar[-31] and bound['selected_event']['offset']==30
    assert bound['selected_event']['close']==rows[-31]['close']
    assert bound['evidence']['snapshot_id']=='market-fixture'
    assert bound['window']['requested_sessions']==bound['window']['available_sessions']==1
    assert bound['checks'][0]['actual']==rows[-1]['close']
    assert 'history_event' not in names(tree), 'node is not a fake numeric field'


@pytest.mark.parametrize('match,offset',[('latest',2),('earliest',3)])
def test_window_priority_is_explicit_and_then_does_not_pick_a_different_event(match,offset):
    calendar=days();rows=quotes(calendar)
    rows[-3]['amount']=rows[-4]['amount']=5000
    tree=event(1,4,match,{'field':'amount','op':'>','value':4000},
               {'field':'close','op':'<','right_event_field':'close'})
    result=check(tree,rows,calendar)
    assert result['selected_event']['offset']==offset
    assert result['result'] is False, 'failed today comparison cannot choose a later matching historical day'


def test_unknown_more_preferred_event_cannot_be_skipped_but_later_unknown_does_not_rebind():
    calendar=days();rows=quotes(calendar)
    rows[-3]['amount']=5000
    rows[-2]['volume']=0
    tree=event(1,3,where={'field':'amount','op':'>','value':4000})
    result=check(tree,rows,calendar)
    assert result['result'] is None and result['selected_event'] is None
    assert result['evidence']['blocking_offset']==1
    rows[-2]['volume']=100;rows[-2]['amount']=5000;rows[-3]['volume']=0
    result=check(tree,rows,calendar)
    assert result['result'] is True and result['selected_event']['offset']==1


@pytest.mark.parametrize('mode',['missing','duplicate','zero_volume','unknown_actions'])
def test_missing_or_ambiguous_event_day_is_unknown(mode):
    calendar=days();rows=quotes(calendar);rows[-31]['volume']=300
    if mode=='missing':rows.pop(-31)
    elif mode=='duplicate':rows.append(dict(rows[-31]))
    elif mode=='zero_volume':rows[-31]['volume']=0
    else:rows[-31]['corporate_action_coverage_known']=False
    result=check(event(),rows,calendar)
    assert result['result'] is None and result['selected_event'] is None


def test_full_known_false_window_is_false_and_calendar_underflow_is_unknown():
    calendar=days(10);rows=quotes(calendar)
    assert check(event(1,3),rows,calendar)['result'] is False
    result=check(event(10),rows,calendar)
    assert result['result'] is None and result['window']['available_sessions']==0


@pytest.mark.parametrize('mode',['action','unknown_actions','missing_current','duplicate_current','suspended_current'])
def test_event_raw_prices_do_not_cross_unverified_action_or_bad_observation(mode):
    calendar=days();rows=quotes(calendar);rows[-31]['volume']=300
    if mode=='action':rows[-5]['corporate_action']=True
    elif mode=='unknown_actions':rows[-5]['corporate_action_coverage_known']=False
    elif mode=='missing_current':rows.pop()
    elif mode=='duplicate_current':rows.append(dict(rows[-1]))
    else:rows[-1]['volume']=0
    result=check(event(),rows,calendar)
    assert result['selected_event']['date']==calendar[-31]
    assert result['result'] is None and result['evidence']['price_comparable'] is False


@pytest.mark.parametrize('mode',['missing','duplicate','zero_volume'])
def test_event_price_comparison_requires_the_intermediate_daily_evidence(mode):
    calendar=days();rows=quotes(calendar);rows[-31]['volume']=300
    if mode=='missing':rows.pop(-5)
    elif mode=='duplicate':rows.append(dict(rows[-5]))
    else:rows[-5]['volume']=0
    result=check(event(),rows,calendar)
    assert result['selected_event']['date']==calendar[-31], 'event matching and endpoint comparison are distinct'
    assert result['result'] is None and result['evidence']['price_comparable'] is False


def test_multiple_bindings_are_isolated_and_today_zero_is_not_a_missing_value():
    calendar=days();rows=quotes(calendar)
    rows[-3]['amount']=rows[-5]['amount']=5000
    first=event(2,where={'field':'amount','op':'>','value':4000})
    second=event(4,where={'field':'amount','op':'>','value':4000},then={'field':'close','op':'<','right_event_field':'high'})
    result=check(validate({'all':[first,second]}),rows,calendar)
    assert [row['selected_event']['offset'] for row in result['checks']]==[2,4]
    assert result['checks'][0]['checks'][0]['right_actual']==rows[-3]['close']
    assert result['checks'][1]['checks'][0]['right_actual']==rows[-5]['high']


def test_where_supports_custom_all_any_ohlc_ma_and_technical_unknown():
    calendar=days();rows=quotes(calendar)
    where={'all':[{'field':'high','op':'>','right_field':'ma20'},
                  {'any':[{'field':'low','op':'>','value':0},{'field':'macd_cross','op':'==','value':True}]}]}
    assert check(event(10,where=where),rows,calendar)['result'] is True
    short=calendar[:15]
    assert check(event(1,where={'field':'macd_cross','op':'==','value':False}),rows[:15],short)['result'] is None


def test_w_strategy_event_price_comparison_is_observation_day_daily_close():
    calendar=days(123);rows=quotes(calendar);rows[-31]['volume']=300
    current=metrics_for(rows,calendar[-1],calendar,'W')
    assert current['observed_date']<calendar[-1]
    result=check(event(),rows,calendar,period='W')
    assert result['result'] is True
    assert result['checks'][0]['actual']==rows[-1]['close']
    assert result['checks'][0]['observation_period']=='D'
    assert result['checks'][0]['observation_date']==calendar[-1]


def test_future_quotes_and_actions_do_not_change_an_earlier_binding():
    calendar=days();rows=quotes(calendar);rows[60]['volume']=300
    context=HistoryContext(rows,calendar,calendar[-1],'market-fixture')
    before=check(event(),rows,calendar,calendar[90],context=context)
    future=deepcopy(rows);future[100].update(corporate_action=True,close=999,high=1000)
    after=check(event(),future,calendar,calendar[90])
    assert before==after
    assert before['selected_event']['date']==calendar[60]


def test_tracking_uses_identical_history_binding_without_serializing_context():
    calendar=days();rows=quotes(calendar);rows[-31]['volume']=300
    tree=event();context=HistoryContext(rows,calendar,calendar[-1],'fixture')
    metrics=metrics_for(rows,calendar[-1],calendar)
    policy={'alerts':[tree],'alert_logic':'OR','exits':[],'exit_logic':'OR',
            'allow_entry_day_alerts':False,'allow_entry_day_exits':False}
    alert,exit_=condition_decisions(policy,metrics,1,context.at(calendar[-1]))
    assert alert['result'] is True and exit_['result'] is False
    assert all(not isinstance(value,HistoryContext) for value in metrics.values())


@pytest.mark.parametrize('mutation',[
    lambda node:node['history_event']['window'].update(from_=1),
    lambda node:node['history_event']['window'].update({'from':0}),
    lambda node:node['history_event']['window'].update({'to':751}),
    lambda node:node['history_event']['window'].update({'from':31}),
    lambda node:node['history_event'].update(match='unsupported'),
    lambda node:node['history_event'].update(where={'field':'prev_limit_up','op':'==','value':True}),
    lambda node:node['history_event'].update(where={'field':'is_st','op':'==','value':False}),
    lambda node:node['history_event'].update(where={'field':'close','op':'>','right_event_field':'close'}),
    lambda node:node['history_event'].update(then={'field':'high','op':'>','right_event_field':'close'}),
    lambda node:node['history_event'].update(then={'field':'close','op':'>','right_event_field':'close','value':10}),
    lambda node:node['history_event'].update(where=event(1)),
])
def test_invalid_or_recursive_event_ast_is_rejected(mutation):
    node=deepcopy(event());mutation(node)
    with pytest.raises(ValueError):validate(node)


def test_event_rhs_is_scoped_and_specs_explain_supported_historical_fields():
    with pytest.raises(ValueError):validate({'field':'close','op':'>','right_event_field':'close'})
    fields={row['field']:row for row in specs()}
    for field in ('open','high','low','close','volume_ratio','macd_cross','kd_dead_cross','ma250','limit_up'):
        assert fields[field]['historical_event_supported'] is True
    for field in ('topic_ids','board','is_st','prev_limit_up','tracking_change_pct'):
        assert fields[field]['historical_event_supported'] is False


def test_bounded_history_read_includes_indicator_prehistory_and_batches_actions_once():
    calendar=days(400);rows=quotes(calendar);tree=event(where={'field':'close','op':'>','right_field':'ma60'})
    assert read_start([tree],calendar,calendar[-1])==calendar[-90]
    class Market:
        def __init__(self):self.calls=[];self.actions=0
        def history_for_instruments(self,symbols,start=None,end=None):
            self.calls.append((list(symbols),start,end))
            return {symbol:[{key:value for key,value in row.items() if key not in ('corporate_action','corporate_action_coverage_known')} for row in rows if start<=row['date']<=end] for symbol in symbols}
        def actions_for_instruments(self,symbols):self.actions+=1;return {symbol:[] for symbol in symbols}
        def status(self):return {'snapshot_id':'fixed-fixture'}
    market=Market();symbols=['600001.SH','600002.SH']
    result=contexts_for_batch(market,symbols,[tree],calendar,calendar[-1])
    assert len(market.calls)==market.actions==1
    assert market.calls[0][1:]==(calendar[-90],calendar[-1])
    assert all(evaluate(tree,metrics_for(rows,calendar[-1],calendar),context.at(calendar[-1]))['result'] is True for context in result.values())
    with pytest.raises(ValueError):contexts_for_batch(market,['600001.SH']*257,[tree],calendar,calendar[-1])
    assert read_start([event(where={'field':'macd_cross','op':'==','value':True})],calendar,calendar[-1])==calendar[0], 'recursive EMA retains the shared original seed'


def test_recursive_indicator_seed_is_not_retained_as_a_large_daily_context():
    calendar=days(1000);rows=quotes(calendar)
    context=HistoryContext(rows,calendar,calendar[-1],'fixture',retain_start=calendar[-31])
    assert len(context.daily)==len(context.quotes)==31
    node=event(where={'field':'macd_dif','op':'>','value':0})
    result=check(node,rows,calendar,context=context)
    assert result['selected_event']['date']==calendar[-31]
    assert result['result'] is True


def test_selection_tracking_and_saved_run_share_scoped_event_proofs(ws,monkeypatch):
    market,client=ws
    for values in market.data.values():
        for row in values:row['corporate_action_coverage_known']=True
    market.data['600001.SH'][40]['volume']=300
    reads=[]
    def bounded(symbols,start=None,end=None):
        reads.append((list(symbols),start,end))
        return {symbol:[row for row in market.bars(symbol,end) if row['date']>=start] for symbol in symbols}
    monkeypatch.setattr(market,'history_for_instruments',bounded,raising=False)
    monkeypatch.setattr(market,'history_panel',lambda **_:pytest.fail('D history-event selection must not materialize all-stock histories'))
    tree=event();alert=event(30,31)
    created=client.post('/api/v1/strategies',json={'name':'历史事件fixture','period':'D','definition':tree,'policy':{'alerts':[alert],'exits':[]}})
    assert created.status_code==200,created.text
    strategy=created.json();sid=strategy['strategy_id']
    run=client.post('/api/v1/strategies/'+sid+'/runs',json={})
    assert run.status_code==200,run.text
    saved=run.json();matched=next(row for row in saved['items'] if row['decision']=='matched')
    assert matched['checks']['selected_event']['date']==market.days[40]
    assert len(reads)==1 and reads[0][1]==market.days[39], 'exact 30-day event keeps its previous volume quote'
    added=client.post('/api/v1/tracking-rounds',json={'run_id':saved['run_id'],'instrument_ids':[matched['instrument_id']]})
    assert added.status_code==200,added.text
    advance(market,71)
    from app.services.tracking import review_tracking
    reviewed=review_tracking({'strategy_id':sid})
    assert reviewed['alerts']==1 and reviewed['ended']==0
    assert review_tracking({'strategy_id':sid})['alerts']==0
    client.patch('/api/v1/strategies/'+sid,json={'definition':event(1)})
    original=client.get('/api/v1/strategy-runs/'+saved['run_id']).json()
    assert next(row for row in original['items'] if row['decision']=='matched')['checks']==matched['checks']


@pytest.mark.parametrize('period',['D','W'])
def test_backtest_uses_daily_event_window_and_freezes_selected_date(period):
    calendar=days();rows=quotes(calendar);rows[64]['volume']=300
    class Market:
        def calendar(self):return calendar
        def instruments(self,limit=10000):return [{'instrument_id':'600001.SH','name':'测试','kind':'stock','list_date':'2000-01-01'}]
        def status(self):return {'symbols':1,'snapshot_id':'fixed-test'}
        def iter_histories(self,end):yield '600001.SH',[row for row in rows if row['date']<=end]
    strategy={'definition':event(),'period':period}
    _,signals,_,_=prepare(Market(),strategy,calendar[80],calendar[104])
    assert signals[calendar[94]][0]['checks']['selected_event']['date']==calendar[64]
    assert all(row['date']==calendar[94] for group in signals.values() for row in group)
    # Historical MA60 requires 60 daily bars plus its event offset, not 60 weeks.
    strategy['definition']=event(where={'field':'close','op':'>','right_field':'ma60'})
    available=availability(Market(),strategy)
    assert available['available'] is True and available['min_date']<=calendar[94]
