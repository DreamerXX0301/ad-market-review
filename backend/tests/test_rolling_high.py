"""Scalar, vectorized selection and historical signals share strict daily highs."""
from datetime import date,timedelta
import numpy as np
import pytest
from app.analytics.indicators import enrich_bars
from app.analytics.panel_inputs import PanelInputs
from app.analytics.rolling_high import rolling_high_state,matrix_breakout_states
from app.domain.conditions import evaluate,validate


def quotes(n=90):
    days=[];cursor=date(2026,1,5)
    while len(days)<n:
        if cursor.weekday()<5:days.append(cursor.isoformat())
        cursor+=timedelta(days=1)
    rows=[{'date':day,'open':100+i,'high':102+i,'low':99+i,'close':101+i,'volume':100,'amount':1000} for i,day in enumerate(days)]
    return days,rows


def decision(state,n):
    return evaluate(validate({'field':'close_breakout_high','op':'new_high','value':n}),state)['result']


def test_window_excludes_today_and_requires_strictly_higher_close():
    days,rows=quotes(7)
    rows[-1]['close']=rows[-2]['high'];rows[-1]['high']=rows[-1]['close']+1
    state=rolling_high_state(rows,days,days[-1])
    assert state['breakout_blocking_distance']==1
    assert decision(state,1) is False
    rows[-1]['close']+=.01
    state=rolling_high_state(rows,days,days[-1])
    assert decision(state,6) is True
    assert decision(state,7) is None


@pytest.mark.parametrize('kind',['gap','zero_volume','missing_volume','bad_high','known_action','unknown_action_coverage','duplicate'])
def test_window_does_not_replace_unknown_market_days_with_older_quotes(kind):
    days,rows=quotes(12)
    if kind=='gap':rows.pop(9)
    elif kind=='zero_volume':rows[9]['volume']=0
    elif kind=='missing_volume':rows[9].pop('volume')
    elif kind=='bad_high':rows[9]['high']=float('nan')
    elif kind=='known_action':rows[9]['corporate_action']=True
    elif kind=='unknown_action_coverage':rows[9]['corporate_action_coverage_known']=False
    else:rows.insert(10,dict(rows[9]))
    state=rolling_high_state(rows,days,days[-1])
    assert decision(state,5) is None
    assert state['breakout_previous_sessions']<5


def test_exact_observation_day_and_future_quotes_are_not_substituted():
    days,rows=quotes()
    original=rolling_high_state(rows,days,days[70])
    rows[-1]['high']=1e20
    assert rolling_high_state(rows,days,days[70])==original
    assert decision(rolling_high_state(rows[:-1],days,days[-1]),1) is None


def test_array_feature_matches_scalar_with_actions_gaps_and_suspension():
    import pandas as pd
    days,template=quotes(95);all_rows=[]
    generator=np.random.default_rng(42)
    histories={}
    for k in range(6):
        symbol=f'{600000+k}.SH';rows=[]
        for i,bar in enumerate(template):
            close=float(generator.uniform(10,30));high=close+float(generator.uniform(0,4))
            rows.append({**bar,'instrument_id':symbol,'close':close,'high':high,'corporate_action':i==40 and k==1})
        if k==2:del rows[55]
        if k==3:rows[60]['volume']=0
        if k==4:rows[60]['high']=float('nan')
        histories[symbol]=rows;all_rows.extend(rows)
    inputs=PanelInputs(pd.DataFrame(all_rows),days)
    for index in (5,40,56,61,75,94):
        current=inputs.latest(days[index]);counts=[current.get(symbol,{}).get('indicator_warmup_bars',0) for symbol in inputs.symbols]
        vector=matrix_breakout_states(inputs.values['close'],inputs.values['high'],counts,index,inputs.symbols,inputs.values['volume'])
        for symbol,rows in histories.items():
            scalar=rolling_high_state(rows,days,days[index])
            # The nearest blocker before a reset need not be retained; only a
            # requested window inside the verified contiguous span may decide.
            assert vector[symbol]['breakout_previous_sessions']==scalar['breakout_previous_sessions']
            assert vector[symbol]['breakout_valid']==scalar['breakout_valid']
            for n in (1,3,10,20,60):
                assert decision(vector[symbol],n)==decision(scalar,n),(index,symbol,n)


def test_shared_indicator_rows_keep_only_constant_size_high_state():
    days,rows=quotes(90)
    rows[-1]['close']=rows[-1]['high']
    enriched=enrich_bars(rows,days)
    assert enriched[-1]['breakout_previous_sessions']==89
    assert decision(enriched[-1],60) is True
    assert not any(isinstance(value,dict) for key,value in enriched[-1].items() if key.startswith('breakout'))


@pytest.mark.parametrize('invalid_close',[0,-1])
def test_nonpositive_prior_close_resets_both_scalar_and_matrix_windows(invalid_close):
    days,rows=quotes(3)
    for row,close,high in zip(rows,[invalid_close,10,20],[9,11,21]):
        row.update(close=close,high=high)
    close=np.array([[row['close']] for row in rows],dtype=float)
    high=np.array([[row['high']] for row in rows],dtype=float)
    volume=np.array([[row['volume']] for row in rows],dtype=float)
    vector=matrix_breakout_states(close,high,[3],2,['600000.SH'],volume)['600000.SH']
    scalar=rolling_high_state(rows,days,days[-1])
    assert vector['breakout_previous_sessions']==scalar['breakout_previous_sessions']==1
    assert decision(vector,1)==decision(scalar,1) is True
    assert decision(vector,2)==decision(scalar,2) is None


@pytest.mark.parametrize('n',[0,-1,751,1.5,True,'60'])
def test_lookback_parameter_requires_supported_positive_integer(n):
    with pytest.raises(ValueError):validate({'field':'close_breakout_high','op':'new_high','value':n})
