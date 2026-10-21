"""Research signal timing, version preservation and missing-value semantics."""
import json
import pytest
from app.analytics.backtest_engine import availability, prepare, simulate
from app.domain.trade_rules import normalize_trade_rules
from test_backtests import CalendarOnly, execution_fixture, creation_context
from test_workspace import ws, make_strategy


def test_strategy_trade_rules_are_versioned_without_changing_selection(ws):
    _,client=ws
    strategy=make_strategy(client)
    assert strategy['trade_rules']=={'entry':None,'exit':None}
    rules={'entry':{'field':'macd_cross','op':'==','value':True},'exit':{'field':'close','op':'<','right_field':'ma20'}}
    response=client.patch('/api/v1/strategies/'+strategy['strategy_id'],json={'trade_rules':rules})
    assert response.status_code==200,response.text
    changed=response.json()
    assert changed['version']==2 and changed['trade_rules']==rules
    assert changed['definition']==strategy['definition']
    from app.core import connect
    with connect() as c:
        versions=c.execute('SELECT version,trade_rules_json FROM strategy_version WHERE strategy_id=? ORDER BY version',(strategy['strategy_id'],)).fetchall()
    assert json.loads(versions[0]['trade_rules_json'])=={'entry':None,'exit':None}
    assert json.loads(versions[1]['trade_rules_json'])==rules
    # Editing an unrelated property preserves the saved rule instead of clearing it.
    again=client.patch('/api/v1/strategies/'+strategy['strategy_id'],json={'name':'保留买卖规则'}).json()
    assert again['trade_rules']==rules


def test_backtest_freezes_trade_rules_and_idempotency_rejects_changed_rule(creation_context):
    from app.core import connect
    store,payload=creation_context
    _,strategy,_,_=store.context(payload['strategy_id'])
    strategy['trade_rules']={'exit':{'field':'close','op':'<','right_field':'ma20'}}
    first=store.create_backtest(payload)
    with connect() as c:
        frozen=c.execute('SELECT strategy_snapshot_json,required_features_json FROM backtest_run WHERE backtest_id=?',(first['backtest_id'],)).fetchone()
    assert json.loads(frozen['strategy_snapshot_json'])['trade_rules']['exit']==strategy['trade_rules']['exit']
    assert 'ma20' in json.loads(frozen['required_features_json'])
    strategy['trade_rules']['exit']['right_field']='ma30'
    with pytest.raises(store.BacktestRequestConflict):store.create_backtest(payload)


@pytest.mark.parametrize('field',['topic_ids','limit_up','prev_limit_up','limit_up_count_1y','turnover_ratio_pct','tracking_change_pct','close_above_entry_high'])
def test_unsupported_historical_or_tracking_features_cannot_be_silently_used(field):
    from app.domain.conditions import FIELDS
    spec=FIELDS[field];value=True if spec['type']=='boolean' else 'topic' if spec['type']=='topic' else 3
    with pytest.raises(ValueError):normalize_trade_rules({'exit':{'field':field,'op':spec['operators'][0],'value':value}})


def test_sell_rule_dependencies_change_available_warmup():
    strategy={'definition':{'field':'close','op':'>','value':0},'trade_rules':{'exit':{'field':'close','op':'<','right_field':'ma60'}}}
    result=availability(CalendarOnly(),strategy)
    assert result['warmup_bars']==60
    strategy['trade_rules']['exit']['right_field']='ma250'
    assert availability(CalendarOnly(),strategy)['available'] is False


def test_entry_universe_is_combined_with_selection_but_exit_is_not():
    strategy={'definition':{'field':'close','op':'>','value':0},'trade_rules':{'entry':{'field':'board','op':'in','value':['star']}}}
    assert availability(CalendarOnly(),strategy)['available'] is False
    strategy['trade_rules']={'exit':{'field':'board','op':'in','value':['star']}}
    assert availability(CalendarOnly(),strategy)['available'] is True


def test_weekly_signals_daily_risk_ma_uses_daily_not_weekly_warmup():
    strategy={'period':'W','definition':{'field':'close','op':'>','value':0}}
    result=availability(CalendarOnly(),strategy,exit_ma=60)
    assert result['available'] is True
    assert result['warmup_bars']==2
    assert result['min_date']==CalendarOnly().calendar()[59]


class TradeMarket(CalendarOnly):
    def instruments(self,limit=10000):return [{'instrument_id':'600000.SH','name':'合成主板','list_date':'2020-01-01'}]
    def status(self):return {'symbols':1}
    def iter_histories(self,end):
        yield '600000.SH',[{'date':day,'open':10+i*.1,'close':10+i*.1,'high':10.1+i*.1,'low':9.9+i*.1,'volume':10000,'amount':100000,'corporate_action':False} for i,day in enumerate(self.calendar()) if day<=end]


def test_entry_is_selection_and_buy_condition_and_exit_does_not_require_selection():
    market=TradeMarket();days=market.calendar()
    strategy={'period':'D','definition':{'field':'close','op':'<','value':14},'trade_rules':{'entry':{'field':'close','op':'>','value':13},'exit':{'field':'close','op':'>','value':14}}}
    quotes,signals,_,quality=prepare(market,strategy,days[20],days[50])
    assert min(signals)==days[31] and max(signals)==days[39]
    assert quotes[days[41]]['600000.SH']['custom_exit'] is True
    assert not signals.get(days[41])
    assert quality['exit_unknown_observations']==0


def test_custom_exit_is_next_open_and_proof_survives_failed_fill():
    dates,quotes,signals,config=execution_fixture(4);symbol='600000.SH'
    config['hold_days']=250
    proof={'field':'close','op':'>','value':10,'actual':10.2,'result':True}
    quotes[dates[1]][symbol].update(custom_exit=True,custom_exit_checks=proof)
    quotes[dates[2]][symbol].update(open=9.18,high=9.3,low=9.18,close=9.2)
    quotes[dates[3]][symbol].update(open=9.3,high=9.5,low=9.2,close=9.4)
    result=simulate(dates,quotes,signals,config)
    assert result['trades'][0]['exit_date']==dates[3]
    assert result['trades'][0]['exit_reason']=='自定义卖出条件'
    assert result['trades'][0]['exit_checks']==proof
    sells=[order for order in result['orders'] if order['side']=='sell']
    assert sells[0]['signal_date']==dates[1] and sells[0]['checks']==proof
    assert '跌停' in sells[0]['reason']
    assert next(fill for fill in result['fills'] if fill['side']=='sell')['checks']==proof


def test_buy_checks_survive_fill_and_closed_trade():
    dates,quotes,signals,config=execution_fixture()
    proof={'field':'close','op':'>','value':9,'actual':10,'result':True}
    signals[dates[0]][0]['checks']=proof
    result=simulate(dates,quotes,signals,config)
    assert result['fills'][0]['checks']==proof
    assert result['trades'][0]['entry_checks']==proof


def test_unknown_sell_condition_does_not_force_liquidation():
    dates,quotes,signals,config=execution_fixture();config['hold_days']=250
    for day in dates[1:]:quotes[day]['600000.SH']['custom_exit']=None
    result=simulate(dates,quotes,signals,config)
    assert result['stats']['holding_count']==1 and result['trades']==[]
