"""Completed daily high is a dated percentage, never a minute or weekly proxy."""
from copy import deepcopy
from datetime import date, timedelta

import pandas as pd
import pyarrow as pa
import pytest

from app.analytics.backtest_engine import availability, config_checked, prepare, simulate, warmup
from app.analytics.indicators import enrich_bars
from app.analytics.panel_inputs import PanelInputs
from app.domain.conditions import evaluate, metrics_for, metrics_from_enriched, specs, validate
from app.domain.trade_rules import normalize_trade_rules
from test_workspace import ws, make_strategy

FIELD = 'intraday_high_change_pct'
SYMBOL = '600001.SH'


def calendar_days(count=90):
    current=date(2026,1,5);days=[]
    while len(days)<count:
        if current.weekday()<5:days.append(current.isoformat())
        current+=timedelta(days=1)
    return days


def quotes(days):
    return [{'instrument_id':SYMBOL,'date':day,'open':100,'close':100,'high':100.8,
             'low':99,'volume':1000,'amount':100000,'corporate_action':False}
            for day in days]


def rule(value=3,op='>='):return {'field':FIELD,'op':op,'value':value}


def test_numeric_contract_accepts_finite_signed_thresholds_and_rejects_non_numbers():
    spec=next(item for item in specs() if item['field']==FIELD)
    assert spec['type']=='number' and spec['operators'][0]=='>=' and spec['default']==3
    assert spec['observation_period']=='D' and spec['unit']=='%'
    for value in (0,-3,3,3.5):assert validate(rule(value))['value']==value
    for value in (True,None,'3',float('nan'),float('inf')):
        with pytest.raises(ValueError):validate(rule(value))
    for value in (None,True,float('nan'),float('inf')):
        assert evaluate(validate(rule()),{FIELD:value})['result'] is None
    assert normalize_trade_rules({'entry':rule(),'exit':rule(-2,'<')})['entry']==rule()


@pytest.mark.parametrize('period',['D','W'])
def test_daily_high_can_reach_three_percent_while_close_does_not_and_weekly_high_is_ignored(period):
    days=calendar_days();rows=quotes(days);rows[5]['high']=150
    rows[10].update(high=103,close=100.5)
    result=metrics_for(rows,days[10],days,period)
    assert result[FIELD]==3 and result[FIELD+'_reason'] is None
    assert evaluate(validate(rule()),result)['result'] is True
    assert (rows[10]['close']/rows[9]['close']-1)*100<3
    before=deepcopy(result)
    rows[11].update(high=100000,close=10000)
    assert metrics_for(rows,days[10],days,period)==before


def test_exact_zero_and_negative_boundaries_keep_numeric_meaning():
    days=calendar_days();rows=quotes(days)
    rows[10].update(high=95,close=94,low=93)
    state=metrics_for(rows,days[10],days)
    assert state[FIELD]==-5
    assert evaluate(validate(rule(-5)),state)['result'] is True
    assert evaluate(validate(rule(-5,'>')),state)['result'] is False
    rows[10].update(high=100,close=99,low=98)
    assert metrics_for(rows,days[10],days)[FIELD]==0


@pytest.mark.parametrize('mode',['first','missing_previous','missing_current','duplicate_previous','zero_today','zero_previous','negative_volume','nan_volume','unknown_volume','action_today','coverage_today','coverage_previous','unknown_calendar','invalid_high','bad_high_close'])
@pytest.mark.parametrize('period',['D','W'])
def test_unverifiable_pair_remains_unknown_with_reason(mode,period):
    days=calendar_days();rows=quotes(days);target=10;calendar=days
    if mode=='first':target=0
    elif mode=='missing_previous':rows.pop(9)
    elif mode=='missing_current':rows.pop(10)
    elif mode=='duplicate_previous':rows.append(deepcopy(rows[9]))
    elif mode=='zero_today':rows[10]['volume']=0
    elif mode=='zero_previous':rows[9]['volume']=0
    elif mode=='negative_volume':rows[10]['volume']=-1
    elif mode=='nan_volume':rows[9]['volume']=float('nan')
    elif mode=='unknown_volume':rows[10]['volume']=None
    elif mode=='action_today':rows[10]['corporate_action']=True
    elif mode=='coverage_today':rows[10]['corporate_action_coverage_known']=False
    elif mode=='coverage_previous':rows[9]['corporate_action_coverage_known']=False
    elif mode=='unknown_calendar':calendar=[day for day in days if day!=days[10]]
    elif mode=='invalid_high':rows[10]['high']=float('inf')
    elif mode=='bad_high_close':rows[10]['high']=99
    result=metrics_for(rows,days[target],calendar,period)
    assert result[FIELD] is None and result[FIELD+'_reason']
    check=evaluate(validate(rule()),result)
    assert check['result'] is None and check['reason']==result[FIELD+'_reason']


def test_day_after_action_uses_the_post_action_previous_close_not_an_old_price():
    days=calendar_days();rows=quotes(days)
    rows[9].update(corporate_action=True,close=50,high=51,low=49)
    rows[10].update(high=51.5,close=50,low=49)
    assert metrics_for(rows,days[10],days)[FIELD]==3
    enriched=enrich_bars(rows[:11],days)
    assert metrics_from_enriched(enriched[-1],enriched[-2])[FIELD]==3
    assert metrics_from_enriched({'date':days[10]})[FIELD] is None


@pytest.mark.parametrize('period',['D','W'])
def test_strategy_execution_freezes_daily_high_value_and_its_observation_date(ws,period):
    market,client=ws
    prior=market.data[SYMBOL][69]['close']
    market.data[SYMBOL][70].update(high=prior*1.03,close=prior*1.005)
    strategy=make_strategy(client,rule())
    if period=='W':
        changed=client.patch('/api/v1/strategies/'+strategy['strategy_id'],json={'period':'W'})
        assert changed.status_code==200,changed.text
    response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert response.status_code==200,response.text
    candidate=next(item for item in response.json()['items'] if item['instrument_id']==SYMBOL)
    assert candidate['decision']=='matched' and candidate['metrics'][FIELD]==3
    assert candidate['checks']['actual']==3
    assert candidate['checks']['observation_date']==market.entry
    assert candidate['checks']['observation_period']=='D'


def test_scalar_and_checkpoint_vector_share_pair_values_reasons_and_action_coverage():
    days=calendar_days(155)
    rows=quotes(days)
    rows[64].update(high=103,close=100.5)
    rows[65]['volume']=0
    rows[90]['corporate_action']=True
    rows[120]['corporate_action_coverage_known']=False
    for row in rows:row.setdefault('corporate_action_coverage_known',True)
    rows=[row for row in rows if row['date']!=days[129]]
    frame=pd.DataFrame(rows);inputs=PanelInputs(frame,days)
    for index in (0,63,64,65,66,67,89,90,91,119,120,121,122,130,154,64):
        scalar=enrich_bars([row for row in rows if row['date']<=days[index]],days)[-1]
        batch=inputs.latest(days[index])[SYMBOL]
        assert batch[FIELD]==scalar[FIELD]
        assert batch[FIELD+'_reason']==scalar[FIELD+'_reason']


def test_arrow_encoding_matches_pandas_without_using_categorical_or_date_map(monkeypatch):
    days=calendar_days(155);rows=quotes(days)
    rows[64]['corporate_action']=True
    rows.extend({**row,'instrument_id':'000001.SZ'} for row in quotes(days) if row['date']!=days[80])
    frame=pd.DataFrame(rows).sample(frac=1,random_state=12).reset_index(drop=True)
    baseline=PanelInputs(frame,days)
    compact=frame.convert_dtypes(dtype_backend='pyarrow')
    # Real Arrow columns may have multiple chunks after a retained-generation merge.
    for field in ('instrument_id','date'):
        values=compact[field].array.__arrow_array__()
        compact[field]=pd.Series(pd.arrays.ArrowExtensionArray(pa.chunked_array([values.slice(0,100).combine_chunks(),values.slice(100).combine_chunks()])))
    with monkeypatch.context() as patch:
        patch.setattr(pd,'Categorical',lambda *args,**kwargs:pytest.fail('Arrow must use index_in, not Categorical'))
        patch.setattr(pd.Series,'map',lambda *args,**kwargs:pytest.fail('Arrow dates must use index_in, not Python map'))
        encoded=PanelInputs(compact,days)
    assert encoded.symbols==baseline.symbols
    for index in (0,34,35,63,64,65,80,81,100,154,33,153):
        assert encoded.latest(days[index])==baseline.latest(days[index])
    with pytest.raises(ValueError,match='交易日历以外'):
        PanelInputs(compact,days[:-1])
    compact.loc[0,'date']=None
    with pytest.raises(ValueError,match='交易日历以外'):
        PanelInputs(compact,days)


class HighMarket:
    def __init__(self):
        self.days=calendar_days(40);self.rows=quotes(self.days)
        self.rows[5]['high']=150
        self.rows[9].update(high=103,close=100.5)
        self.rows[10].update(open=100.4,high=101,close=100.5)
    def calendar(self):return self.days
    def instruments(self,**kwargs):return [{'instrument_id':SYMBOL,'name':'合成指标验证','list_date':'2020-01-01'}]
    def status(self):return {'symbols':1}
    def iter_histories(self,end):yield SYMBOL,[dict(row) for row in self.rows if row['date']<=end]


@pytest.mark.parametrize('period',['D','W'])
def test_backtest_daily_high_dependency_entry_is_a_close_signal_and_executes_only_next_open(period):
    market=HighMarket();days=market.days
    strategy={'period':period,'definition':{'field':'close','op':'>','value':100},'trade_rules':{'entry':rule()}}
    bounds=availability(market,strategy)
    assert bounds['available'] and bounds['warmup_bars']==2 and bounds['daily_lookback_sessions']==1
    assert warmup(validate(rule()))==2
    q,signals,rows,_=prepare(market,strategy,days[5],days[20])
    signal=next(row for row in rows if row['date']==days[9])
    assert signal['metrics'][FIELD]==3
    assert signal['metrics']['change_pct']<3
    config=config_checked({'commission_bps':0,'min_commission':0,'sell_tax_bps':0,'slippage_bps':0,'hold_days':250,'stop_loss_pct':0,'take_profit_pct':0})
    result=simulate(days[4:21],q,signals,config)
    fill=next(row for row in result['fills'] if row['signal_date']==days[9])
    assert fill['date']==days[10] and fill['price']==100.4
    early=prepare(market,strategy,days[5],days[10])[2]
    market.rows[15].update(high=100000,close=10000)
    assert prepare(market,strategy,days[5],days[10])[2]==early
