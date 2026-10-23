"""Strict bottom structure is causal and shares stock/sector/backtest semantics."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.analytics.higher_lows import latest, summaries, STATE, VERSION
from app.analytics.backtest_engine import prepare, warmup
from app.domain.conditions import evaluate, metrics_for, specs, validate
from app.domain import sector_conditions
from app.services.strategies import evaluator_for
from test_workspace import ws, make_strategy, add_one
from test_backtests import creation_context


def rule(window=8, **extra):
    return {'field':'higher_lows','op':'confirmed_rising_lows','value':window,**extra}


def days(count, begin='2025-01-06'):
    result=[];cursor=date.fromisoformat(begin)
    while len(result)<count:
        if cursor.weekday()<5:result.append(cursor.isoformat())
        cursor+=timedelta(days=1)
    return result


def bars(lows, calendar=None):
    calendar=calendar or days(len(lows))
    return [{'date':day,'open':low+.5,'high':low+2,'low':low,'close':low+.5,
             'volume':100,'amount':1000,'corporate_action':False,
             'corporate_action_coverage_known':True} for day,low in zip(calendar,lows)]


def checked(rows, calendar=None, as_of=None, window=8, period='D'):
    calendar=calendar or [row['date'] for row in rows]
    as_of=as_of or calendar[-1]
    state=latest(rows,calendar,as_of,period)
    return evaluate(validate(rule(window,period=period)),
                    {'indicator_period':period,'observed_date':state['observed_date'],STATE:state})


def test_contract_rejects_invalid_windows_comparisons_and_historical_where():
    spec=next(item for item in specs() if item['field']=='higher_lows')
    assert spec['parameter_kind']=='lookback_periods' and spec['structure_version']==VERSION
    assert spec['supported_periods']==['D','W','M'] and spec['historical_event_supported'] is False
    for window in (0,7,751,8.5,True,'60',None):
        with pytest.raises(ValueError):validate(rule(window))
        with pytest.raises(ValueError):sector_conditions.validate(rule(window))
    for period in ('D','W','M'):
        assert validate(rule(60,period=period))['period']==period
        assert sector_conditions.validate(rule(60,period=period))['period']==period
    with pytest.raises(ValueError):validate({'field':'higher_lows','op':'confirmed_rising_lows','right_field':'close'})
    with pytest.raises(ValueError):validate({'field':'close','op':'>','right_field':'higher_lows'})
    with pytest.raises(ValueError):validate({'history_event':{'window':{'from':1,'to':30},'where':rule(),'then':{'field':'close','op':'>','value':0}}})
    old={'field':'close','op':'>','value':0}
    assert evaluator_for(old,'D')=='workspace-v8-bounded-history-events'
    assert evaluator_for(rule(),'D')=='workspace-v13-higher-lows-v1'
    assert warmup(validate(rule(60)))==62


def test_left_neighbors_may_precede_window_but_confirmation_never_uses_future():
    rows=bars([14,13,10,13,14,13,11,14,15,14]);calendar=[row['date'] for row in rows]
    proof=checked(rows,calendar)
    assert proof['result'] is True
    assert [row['price'] for row in proof['evidence']['recent_lows']]==[10,11]
    assert proof['confirmation_date']==calendar[8]
    assert proof['evidence']['recent_lows'][0]['date']==calendar[2] # first center in eight-bar window
    before=checked(rows,calendar,calendar[7])
    assert before['result'] is None # eight-bar window plus two left warmup bars not retained
    altered=deepcopy(rows)
    altered[-1].update(low=1,close=1,open=1,high=2)
    assert checked(altered,calendar,calendar[8])==checked(rows,calendar,calendar[8])


def test_completed_window_with_too_few_or_equal_lows_is_false():
    assert checked(bars(list(range(10,20))))['result'] is False
    rows=bars([14,13,10,13,14,13,10,14,15,14])
    assert checked(rows)['result'] is False
    # An equal adjacent low is a plateau, not a strict five-bar local low.
    rows=bars([14,13,10,10,14,13,11,14,15,14])
    assert checked(rows)['result'] is False


def test_recent_failed_pair_cannot_skip_back_to_favorable_older_pair():
    rows=bars([14,13,10,13,14,13,12,14,15,14,11,14,15])
    proof=checked(rows,window=11)
    assert proof['result'] is False
    assert [low['price'] for low in proof['evidence']['recent_lows']]==[12,11]
    assert '严格抬高' in proof['reason']


def test_close_break_below_l2_remains_invalid_after_recovery_until_new_confirmed_pair():
    rows=bars([14,13,10,13,14,13,11,14,15,10,13])
    rows[9].update(close=10.5,open=10.5,high=14)
    proof=checked(rows,window=9)
    assert proof['result'] is False and '仍失效' in proof['reason']
    assert proof['evidence']['minimum_close_since_l2_confirmation']==10.5
    rows[9].update(low=10.5,close=11,open=11)
    assert checked(rows,window=9)['result'] is True # intraday breach alone does not break close protection


@pytest.mark.parametrize('damage',['missing','duplicate','zero','nan','action','uncovered','missing_action_coverage','missing_action_flag'])
def test_invalid_full_window_or_left_seed_is_unknown(damage):
    rows=bars([14,13,10,13,14,13,11,14,15,14]);calendar=[row['date'] for row in rows]
    if damage=='missing':del rows[0]
    if damage=='duplicate':rows.append(dict(rows[0]))
    if damage=='zero':rows[0]['volume']=0
    if damage=='nan':rows[0]['low']=float('nan')
    if damage=='action':rows[0]['corporate_action']=True
    if damage=='uncovered':rows[0]['corporate_action_coverage_known']=False
    if damage=='missing_action_coverage':del rows[0]['corporate_action_coverage_known']
    if damage=='missing_action_flag':del rows[0]['corporate_action']
    assert checked(rows,calendar)['result'] is None
    assert evaluate(rule(),metrics_for(rows,calendar[-1],calendar,include_higher_lows=True))['result'] is None


def test_old_scalar_rules_do_not_compute_new_structure(monkeypatch):
    import app.analytics.higher_lows as module
    monkeypatch.setattr(module,'latest',lambda *a,**k:pytest.fail('old rules must not compute structures'))
    rows=bars(list(range(10,20)));calendar=[row['date'] for row in rows]
    assert STATE not in metrics_for(rows,calendar[-1],calendar)


def test_confirming_bar_protection_is_included_but_impossible_ohlc_stays_unknown():
    # Retain two earlier bars so the same confirmation day has a full window.
    calendar=days(12);rows=bars([20,19]+[14,13,10,13,14,13,11,14,15,14],calendar)
    proof=checked(rows,calendar,calendar[10],window=8)
    assert proof['result'] is True and proof['confirmation_date']==calendar[10]
    assert proof['evidence']['minimum_close_since_l2_confirmation']==rows[10]['close']
    rows[10]['close']=10 # Below its own low is malformed, not a valid held structure.
    assert checked(rows,calendar,calendar[10],window=8)['result'] is None


def test_unknown_explains_action_date_and_exact_covered_required_window():
    rows=bars([20]*53+[14,13,10,13,14,13,11,14,15,14]);calendar=[row['date'] for row in rows]
    rows[1]['corporate_action']=True
    proof=checked(rows,calendar,window=60)
    assert proof['result'] is None and proof['structure_version']==VERSION
    assert proof['covered_periods']==61 and proof['required_periods']==62
    assert calendar[1] in proof['reason'] and '未复权公司行动' in proof['reason']
    assert '连续61根' in proof['reason'] and '需62根' in proof['reason']
    blocker=proof['evidence']['last_blocker']
    assert blocker['kind']=='corporate_action' and blocker['market_dates']==[calendar[1]]
    assert checked(rows,calendar,window=30)['result'] is True


def test_unknown_period_blocker_evidence_is_bounded():
    rows,calendar,groups=periodic_rows('W')
    affected=groups[-1]
    for row in rows:
        if row['date'] in affected:row['corporate_action']=True
    proof=checked(rows,calendar,groups[-1][-1],period='W')
    assert proof['result'] is None
    blocker=proof['evidence']['last_blocker']
    assert blocker['affected_market_days']==5 and len(blocker['market_dates'])==3


def periodic_rows(period):
    calendar=days(300 if period=='W' else 950,'2022-01-03')
    key=lambda day:date.fromisoformat(day).isocalendar()[:2] if period=='W' else day[:7]
    groups={}
    for day in calendar:groups.setdefault(key(day),[]).append(day)
    selected=list(groups.values())[:12]
    lows=[16,15,14,13,10,13,14,13,11,14,15,14]
    rows=[]
    for members,low in zip(selected,lows):rows.extend(bars([low]*len(members),members))
    return rows,calendar,selected


@pytest.mark.parametrize('period',['W','M'])
def test_closed_periods_and_missing_whole_period_remain_strict(period):
    rows,calendar,groups=periodic_rows(period)
    day=groups[-1][-1]
    proof=checked(rows,calendar,day,window=8,period=period)
    assert proof['result'] is True
    assert proof['confirmation_date']==groups[10][-1]
    actual=evaluate(rule(8,period=period),metrics_for(rows,day,calendar,period,include_higher_lows=True))
    assert actual==proof
    absent=set(groups[3])
    assert checked([row for row in rows if row['date'] not in absent],calendar,day,period=period)['result'] is None
    # Full future rows do not complete the still-open confirmation period.
    midpoint=groups[10][0]
    before=checked(rows,calendar,midpoint,period=period)
    assert before['result'] is not True
    altered=deepcopy(rows)
    for row in altered:
        if row['date']>midpoint:row.update(low=1,open=1,close=1,high=2)
    assert checked(altered,calendar,midpoint,period=period)==before


def test_stock_selection_persists_only_small_structure_and_preserves_other_rules(ws,monkeypatch):
    market,client=ws;symbol='600001.SH'
    sample=bars([14,13,10,13,14,13,11,14,15,14],market.days[61:71])
    market.data[symbol][61:71]=sample
    monkeypatch.setattr(market,'history_panel',lambda *a,**k:pytest.fail('whole-market history forbidden'))
    strategy=make_strategy(client,{'all':[rule(),{'field':'volume','op':'>','value':0}]})
    from app import core
    with core.connect() as connection:
        assert connection.execute('SELECT evaluator_version FROM strategy_version WHERE strategy_id=?',(strategy['strategy_id'],)).fetchone()[0]=='workspace-v13-higher-lows-v1'
    result=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert result.status_code==200,result.text
    candidate=next(row for row in result.json()['items'] if row['instrument_id']==symbol)
    assert candidate['decision']=='matched'
    proof=candidate['checks']['checks'][0]
    assert proof['confirmation_date']==market.days[69]
    assert len(candidate['metrics'][STATE]['recent_lows'])==2


def test_tracking_review_uses_strict_structure_from_frozen_observation_day(ws):
    market,client=ws;symbol='600001.SH'
    market.data[symbol][61:71]=bars([14,13,10,13,14,13,11,14,15,14],market.days[61:71])
    strategy=make_strategy(client,rule(),policy={'allow_entry_day_alerts':True,'alerts':[rule(period='D')],'exits':[]})
    _,round_=add_one(client,strategy)
    response=client.post('/api/v1/tracking-rounds/review',json={})
    assert response.status_code==200,response.text
    detail=client.get('/api/v1/tracking-rounds/'+round_['round_id']).json()
    event=next(event for event in detail['events'] if event['event_type']=='alert')
    proof=event['evidence']['alerts']['checks'][0]
    assert proof['result'] is True and proof['confirmation_date']==market.days[69]
    assert proof['structure_version']==VERSION


def test_historical_then_reads_observation_structure_and_can_combine_event():
    from app.domain.history_events import HistoryContext
    rows=bars([14,13,10,13,14,13,11,14,15,14]);calendar=[row['date'] for row in rows]
    day=calendar[-1]
    tree=validate({'history_event':{'window':{'from':1,'to':2},'match':'latest',
        'where':{'field':'volume','op':'>','value':0},'then':rule(period='D')}})
    context=HistoryContext(rows,calendar,day)
    metrics=metrics_for(rows,day,calendar,include_higher_lows=True)
    proof=evaluate(tree,metrics,context.at(day))
    assert proof['result'] is True
    assert proof['checks'][0]['confirmation_date']==calendar[8]


def test_backtest_matches_scalar_and_never_backdates_confirmation():
    calendar=days(70)
    rows=bars([20]*15+[14,13,10,13,14,13,11,14,15,14]+[16]*45,calendar)
    class Market:
        def calendar(self):return calendar
        def instruments(self,limit=10000):return [{'instrument_id':'600001.SH','name':'测试','list_date':'2020-01-01'}]
        def status(self):return {'symbols':1,'snapshot_id':'fixture'}
        def iter_histories(self,end):yield '600001.SH',[dict(row) for row in rows if row['date']<=end]
    market=Market()
    _,_,signals,quality=prepare(market,{'period':'D','definition':rule()},calendar[14],calendar[-1])
    signal_dates={row['date'] for row in signals}
    expected={day for day in calendar[14:] if evaluate(rule(),metrics_for(rows,day,calendar,include_higher_lows=True))['result'] is True}
    assert signal_dates==expected and quality['unknown_observations']==0
    assert calendar[23] in signal_dates and not any(day<calendar[23] for day in signal_dates)
    altered=deepcopy(rows)
    for row in altered:
        if row['date']>calendar[23]:row.update(open=1,close=1,low=1,high=2)
    market.iter_histories=lambda end:iter([('600001.SH',altered)])
    _,_,changed,_=prepare(market,{'period':'D','definition':rule()},calendar[14],calendar[-1])
    assert next(row for row in changed if row['date']==calendar[23])['checks']==next(row for row in signals if row['date']==calendar[23])['checks']


@pytest.mark.parametrize('period',['W','M'])
def test_daily_backtest_closed_period_clause_matches_scalar_without_future(period):
    rows,calendar,groups=periodic_rows(period)
    class Market:
        def calendar(self):return calendar
        def instruments(self,limit=10000):return [{'instrument_id':'600001.SH','name':'测试','list_date':'2020-01-01'}]
        def status(self):return {'symbols':1,'snapshot_id':'fixture'}
        def iter_histories(self,end):yield '600001.SH',[dict(row) for row in rows if row['date']<=end]
    market=Market();start=groups[10][0];end=groups[-1][-1]
    _,_,signals,_=prepare(market,{'period':'D','definition':rule(period=period)},start,end)
    expected={day for day in calendar if start<=day<=end and evaluate(rule(period=period),
        metrics_for(rows,day,calendar,period,include_higher_lows=True))['result'] is True}
    assert {item['date'] for item in signals}==expected
    assert signals and all(item['checks']['confirmation_date']<=item['date'] for item in signals)


def test_sector_and_stock_share_same_independent_period_evidence():
    rows=bars([14,13,10,13,14,13,11,14,15,14]);calendar=[row['date'] for row in rows]
    from app.services.sector_strategies import _indicators,_evaluator
    metrics,_=_indicators({'bars':rows},calendar,calendar[-1],True)
    check=sector_conditions.evaluate(sector_conditions.validate(rule()),metrics)
    assert check['result'] is True and check['evidence']==checked(rows)['evidence']
    assert _evaluator({'definition':rule(),'policy':{}})=='sector-index-dwm-higher-lows-v2'


def test_new_backtest_freezes_structure_contract_without_invalidating_old_engine(creation_context,monkeypatch):
    import json
    from app.core import connect
    from app.analytics import higher_lows
    store,payload=creation_context
    market,strategy,inputs,generation=store.context(payload['strategy_id'])
    monkeypatch.setattr(store,'context',lambda identifier:(market,{**strategy,'definition':rule()},inputs,generation))
    with monkeypatch.context() as old_contract:
        old_contract.setattr(higher_lows,'VERSION','higher-lows-older-fixture')
        saved=store.create_backtest(payload)
    with connect() as connection:
        row=connection.execute('SELECT * FROM backtest_run WHERE backtest_id=?',(saved['backtest_id'],)).fetchone()
        frozen=json.loads(row['strategy_snapshot_json'])
        assert frozen['condition_contracts']=={'higher_lows':'higher-lows-older-fixture'}
        assert row['engine_version']==store.VERSION
    monkeypatch.setattr(store,'MarketData',lambda *a,**k:pytest.fail('incompatible structure cannot open market'))
    with pytest.raises(ValueError,match='底部抬高冻结条件版本不匹配'):
        store.execute_backtest(saved['job_id'])
