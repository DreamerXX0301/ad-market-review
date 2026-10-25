"""Forward signal prices never become execution prices or mutable round bases."""
from copy import deepcopy
from datetime import date, timedelta
import json
from uuid import uuid4

import pytest

from app import core
from app.analytics.backtest_engine import prepare, run_backtest, _prepare_forward, VERSION, FORWARD_VERSION
from app.domain.strategy_prices import prepare_condition_prices, VERSION as PRICE_VERSION
from app.services import tracking
from app.services.entry_high import condition_metrics, low_condition_metrics
from test_workspace import ws, add_one, advance
from test_backtests import creation_context


def action(symbol, day, cash=10):
    return {'thscode':symbol,'date':day,'dividend_per_share':cash,'per_share_bonus':0,
            'allotment_ratio':0,'allotment_price':0,'currency':'CNY'}


class ForwardMarket:
    def __init__(self, count=150, event_index=100, interval_event_index=136):
        self.verify_calls=0;self.action_batches=[]
        self.source_id=uuid4().hex
        self.days=[];day=date(2025,1,2)
        while len(self.days)<count:
            if day.weekday()<5:self.days.append(day.isoformat())
            day+=timedelta(days=1)
        self.events={'600000.SH':[action('600000.SH',self.days[event_index])],
                     '600001.SH':[action('600001.SH',self.days[interval_event_index])]}
        self.data={}
        for symbol in self.events:
            event_day=self.events[symbol][0]['date']
            self.data[symbol]=[]
            for i,day in enumerate(self.days):
                close=100+i*.2-(10 if day>=event_day else 0)
                self.data[symbol].append({'instrument_id':symbol,'date':day,'open':close-.05,
                    'high':close+.2,'low':close-.2,'close':close,'volume':100000+i,'amount':close*(100000+i),
                    'corporate_action':day==event_day,'corporate_action_coverage_known':True,'price_basis':'none'})
    def calendar(self):return list(self.days)
    def status(self):return {'symbols':2,'snapshot_id':'forward-fixture'}
    def instruments(self,limit=10000):
        return [{'instrument_id':symbol,'name':'合成前复权测试','kind':'stock','list_date':'2010-01-01'} for symbol in self.data]
    def iter_histories(self,end):
        for symbol,rows in self.data.items():yield symbol,[dict(row) for row in rows if row['date']<=end]
    def actions_for_instruments(self,symbols):
        self.action_batches.append(list(symbols))
        return {symbol:deepcopy(self.events[symbol]) for symbol in symbols}
    def verify_condition_sources(self):
        self.verify_calls+=1
        return ('synthetic-test-only', self.source_id)


def strategy(basis='forward'):
    return {'period':'D','price_basis':basis,
            'definition':{'field':'close','op':'>','right_field':'ma60'}}


def test_forward_backtest_restores_pre_interval_seed_and_keeps_execution_raw():
    market=ForwardMarket();before=deepcopy(market.data)
    start,end=market.days[130],market.days[149]
    _,_,raw_signals,raw_quality=prepare(market,strategy('none'),start,end)
    quotes,_,signals,quality=prepare(market,strategy(),start,end)
    assert raw_signals==[] and raw_quality['unknown_observations']==20
    assert len(signals)==20 and quality['exclusions']=={'corporate_action_in_interval':1}
    assert market.verify_calls==1 and len(market.action_batches)==1
    for signal in signals:
        assert signal['signal_adjustment_anchor']==signal['date']
        assert signal['metrics']['adjustment_anchor']==signal['date']
        assert signal['price_basis_contract']==PRICE_VERSION
        source=next(row for row in before['600000.SH'] if row['date']==signal['date'])
        assert quotes[signal['date']]['600000.SH']['open']==source['open']
        assert quotes[signal['date']]['600000.SH']['close']==source['close']
        assert signal['metrics']['volume']==source['volume']
    result=run_backtest(market,strategy(),{'start_date':start,'end_date':end,'slippage_bps':0})
    assert result['engine_version']==FORWARD_VERSION
    assert result['execution_price_basis']=='none' and result['signal_price_basis']=='forward'
    assert result['fills'] and all(fill['instrument_id']=='600000.SH' for fill in result['fills'])
    assert result['fills'][0]['price']==pytest.approx(round(before['600000.SH'][131]['open'],2))
    assert any('分红到账' in warning for warning in result['warnings'])
    assert market.data==before


def test_forward_backtest_future_actions_do_not_rewrite_earlier_signals():
    market=ForwardMarket();start,end=market.days[130],market.days[139]
    before=prepare(market,strategy(),start,end)
    market.events['600000.SH'].append(action('600000.SH',market.days[145],cash=50))
    for row in market.data['600000.SH'][145:]:row.update(close=900,high=901,low=899,open=900)
    assert prepare(market,strategy(),start,end)==before


def test_forward_backtest_missing_action_evidence_does_not_fall_back_to_raw():
    market=ForwardMarket();market.actions_for_instruments=lambda symbols:None
    _,_,signals,quality=prepare(market,strategy(),market.days[130],market.days[149])
    assert signals==[] and quality['exclusions']['corporate_action_coverage_unknown']==1
    assert quality['exclusions']['corporate_action_in_interval']==1


@pytest.mark.parametrize('period',['D','W','M'])
@pytest.mark.parametrize('gap',[False,True])
def test_action_free_interval_recurrence_equals_each_observation_reference(period,gap):
    market=ForwardMarket(1200,1100,1185)
    if gap:market.data['600000.SH'].pop(1150)  # A real retained-calendar gap cannot be skipped.
    ast={'all':[{'field':'close','op':'>','value':0},
        {'any':[{'field':'macd_dif','op':'>','right_field':'macd_dea','period':'W'},
                {'field':'close','op':'>','value':0}]},
        {'any':[{'field':'kdj_k','op':'>','right_field':'kdj_d','period':'M'},
                {'field':'close','op':'>','value':0}]}]}
    selected={'period':period,'price_basis':'forward','definition':ast}
    start,end=market.days[1180],market.days[1199]
    slow=_prepare_forward(market,selected,start,end,_reuse_interval=False)
    fast=_prepare_forward(market,selected,start,end)
    assert fast==slow  # Full metrics, checks, source facts, quotes and ordering.
    assert fast[2]
    for signal in fast[2]:assert signal['metrics']['adjustment_anchor']==signal['date']


def test_interval_recurrence_history_binding_equals_observation_reference():
    market=ForwardMarket();start,end=market.days[130],market.days[149]
    selected={'period':'D','price_basis':'forward','definition':{'history_event':{
        'window':{'from':28,'to':28},'match':'latest',
        'where':{'field':'volume','op':'>','value':0},
        'then':{'field':'close','op':'>','right_event_field':'close'}}}}
    assert _prepare_forward(market,selected,start,end)==_prepare_forward(market,selected,start,end,_reuse_interval=False)


def test_action_record_inside_interval_is_excluded_even_if_raw_flag_is_wrong():
    market=ForwardMarket();day=market.days[138]
    market.events['600000.SH'].append(action('600000.SH',day,cash=1))
    _,_,signals,quality=prepare(market,strategy(),market.days[130],market.days[149])
    assert not signals and quality['exclusions']['corporate_action_in_interval']==2


def test_forward_backtest_scoped_complete_week_uses_the_signal_day_basis():
    market=ForwardMarket(400,320,376);start,end=market.days[370],market.days[389]
    definition={'all':[{'field':'close','op':'>','value':0},
        {'period':'W','field':'macd_dif','op':'>','right_field':'macd_dea'}]}
    _,_,raw,_=prepare(market,{'period':'D','definition':definition},start,end)
    _,_,forward,_=prepare(market,{'period':'D','definition':definition,'price_basis':'forward'},start,end)
    assert not raw and forward
    for signal in forward:
        weekly=signal['metrics']['_period_metrics']['W']
        assert weekly['adjustment_anchor']==signal['date']
        assert weekly['macd_dif']>weekly['macd_dea']


def test_forward_backtest_freezes_basis_contract_and_separate_engine(creation_context,monkeypatch):
    store,payload=creation_context
    market,raw,inputs,generation=store.context(payload['strategy_id'])
    original=store.create_backtest(payload)
    monkeypatch.setattr(store,'context',lambda identifier:(market,{**raw,'price_basis':'forward'},inputs,generation))
    new=store.create_backtest({**payload,'request_key':'forward-click'})
    with core.connect() as connection:
        old=dict(connection.execute('SELECT * FROM backtest_run WHERE backtest_id=?',(original['backtest_id'],)).fetchone())
        forward=dict(connection.execute('SELECT * FROM backtest_run WHERE backtest_id=?',(new['backtest_id'],)).fetchone())
    assert old['engine_version']==VERSION and forward['engine_version']==FORWARD_VERSION
    assert old['input_sha256']!=forward['input_sha256']
    assert 'price_basis' not in json.loads(old['strategy_snapshot_json'])
    frozen=json.loads(forward['strategy_snapshot_json'])
    assert frozen['price_basis']=='forward' and frozen['price_basis_contract']==PRICE_VERSION
    assert store.get_backtest(new['backtest_id'])['execution_price_basis']=='none'
    with pytest.raises(store.BacktestRequestConflict):store.create_backtest(payload)


def enable_forward_fixture(market,monkeypatch):
    for rows in market.data.values():
        for row in rows:row['corporate_action_coverage_known']=True
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'forward-round-fixture'},raising=False)
    monkeypatch.setattr(market,'verify_condition_sources',lambda:('synthetic-round-source',),raising=False)
    monkeypatch.setattr(market,'actions_for_instruments',lambda symbols:{symbol:[action(symbol,day) for day in market.action_dates.get(symbol,[])] for symbol in symbols},raising=False)


def test_forward_round_freezes_basis_and_never_borrows_yesterday_review(ws,monkeypatch):
    market,client=ws;enable_forward_fixture(market,monkeypatch)
    response=client.post('/api/v1/strategies',json={'name':'前复权跟踪','price_basis':'forward',
        'definition':{'field':'volume_ratio','op':'>=','value':3},
        'policy':{'duration_sessions':30,'exits':[{'field':'close_below_entry','op':'==','value':True}]}})
    assert response.status_code==200,response.text
    saved=response.json();_,round_=add_one(client,saved);path='/api/v1/tracking-rounds/'+round_['round_id']
    initial=client.get(path).json()
    assert initial['entry_basis']['signal_price_basis']=='forward'
    assert initial['entry_basis']['price_basis']=='none' and initial['tracking_change_pct']==0
    symbol=round_['instrument_id'];entry=initial['entry_price'];market.action_dates[symbol]=[market.days[71]]
    for row in market.data[symbol][71:]:
        for field in ('open','high','low','close'):row[field]-=10
    market.data[symbol][71].update(open=118,high=120,low=117,close=119,corporate_action=True)
    # Later changes to the strategy's basis cannot change an existing round.
    changed=client.patch('/api/v1/strategies/'+saved['strategy_id'],json={'price_basis':'none'})
    assert changed.status_code==200,changed.text
    advance(market,71)
    pending=client.get(path).json()
    assert pending['latest_price']==119 and pending['tracking_change_pct'] is None
    reviewed=client.post('/api/v1/tracking-rounds/review',json={})
    assert reviewed.status_code==200,reviewed.text
    current=client.get(path).json()
    assert current['status']=='tracking'
    assert current['tracking_change_pct']==pytest.approx((119/(entry-10)-1)*100)
    assert current['tracking_reference_price']==entry-10
    assert current['entry_strategy_version']==initial['entry_strategy_version']
    assert current['entry_basis']==initial['entry_basis']
    changed=client.patch(path+'/policy',json={'policy':{'duration_sessions':30,
        'alerts':[{'field':'close','op':'>','right_field':'ma5'}],
        'exits':[{'field':'close_below_entry','op':'==','value':True}]}})
    assert changed.status_code==200,changed.text
    assert client.get(path).json()['policy_review_pending'] is True
    reviewed=client.post('/api/v1/tracking-rounds/review',json={})
    assert reviewed.status_code==200 and reviewed.json()['reviewed']==1
    assert reviewed.json()['alerts']==1  # Price evidence never reuses old policy decisions.
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'new-generation'},raising=False)
    assert client.get(path).json()['tracking_change_pct'] is None
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'forward-round-fixture'},raising=False)
    advance(market,72)
    pending=client.get(path).json()
    assert pending['tracking_change_pct'] is None and '当前观察日' in pending['comparison_reason']


def test_frozen_entry_high_low_project_to_same_observation_anchor():
    market=ForwardMarket();symbol='600000.SH';day=market.days[130];entry=market.days[90]
    context=prepare_condition_prices(market.data[symbol],market.events[symbol],market.days,day,'forward',
        instrument_id=symbol,actions_coverage=True)
    round_={'entry_business_date':entry,'entry_basis_json':'{"price_basis":"none","signal_price_basis":"forward"}'}
    high={'state':'ready','raw_high':120,'raw_high_e8':12000000000}
    low={'state':'ready','raw_low':115,'raw_low_e8':11500000000}
    quote={'date':day,'close':111,'volume':100}
    result=condition_metrics(round_,day,quote,high,None,'unknown',None,{},price_context=context)
    assert result['close_above_entry_high'] is True
    assert result['close_above_entry_high_evidence']['adjusted_reference_e8']==11000000000
    low_result=low_condition_metrics(round_,day,{**quote,'close':104},low,None,'unknown',None,{},price_context=context)
    assert low_result['close_below_entry_low'] is True
    assert high['raw_high']==120 and low['raw_low']==115


def test_existing_raw_round_does_not_inherit_later_forward_strategy(ws):
    from test_workspace import make_strategy
    market,client=ws
    saved=make_strategy(client,policy={'duration_sessions':30,
        'exits':[{'field':'close_below_entry','op':'==','value':True}]})
    _,round_=add_one(client,saved);path='/api/v1/tracking-rounds/'+round_['round_id']
    before=client.get(path).json()
    changed=client.patch('/api/v1/strategies/'+saved['strategy_id'],json={'price_basis':'forward'})
    assert changed.status_code==200,changed.text
    advance(market,71)
    assert client.post('/api/v1/tracking-rounds/review',json={}).status_code==200
    after=client.get(path).json()
    assert after['entry_basis']==before['entry_basis']
    assert after['entry_basis']['signal_price_basis']=='none'
    assert after['tracking_change_pct'] is not None
    assert 'tracking_reference_price_basis' not in after


@pytest.mark.parametrize('ending',['manual_removed','condition_ended'])
def test_ended_forward_round_keeps_its_frozen_close_and_comparison_after_new_generation(ws,monkeypatch,ending):
    market,client=ws;enable_forward_fixture(market,monkeypatch)
    created=client.post('/api/v1/strategies',json={'name':'冻结结束日价格','price_basis':'forward',
        'definition':{'field':'volume_ratio','op':'>=','value':3},
        'policy':{'duration_sessions':30,'exits':[{'field':'close_below_entry','op':'==','value':True}]}})
    assert created.status_code==200,created.text
    _,round_=add_one(client,created.json());symbol=round_['instrument_id']
    path='/api/v1/tracking-rounds/'+round_['round_id']
    market.action_dates[symbol]=[market.days[71]]
    for row in market.data[symbol][71:]:
        for field in ('open','high','low','close'):row[field]-=10
    close=110 if ending=='condition_ended' else 119
    market.data[symbol][71].update(open=close,high=close+1,low=close-1,close=close,corporate_action=True)
    advance(market,71)
    reviewed=client.post('/api/v1/tracking-rounds/review',json={})
    assert reviewed.status_code==200,reviewed.text
    if ending=='manual_removed':assert client.post(path+'/remove',json={}).status_code==200
    before=client.get(path).json()
    assert before['status']==ending and before['comparison_state']=='verified'
    assert before['tracking_change_pct'] is not None
    # The new generation even revises the old day's raw close. An ended round
    # must keep both sides from its saved ending review, not mix new/old prices.
    market.data[symbol][71].update(open=80,high=81,low=79,close=80)
    monkeypatch.setattr(market,'status',lambda:{'snapshot_id':'new-generation'},raising=False)
    advance(market,72)
    after=client.get(path).json()
    for field in ('valuation_date','latest_price','tracking_change_pct','comparison_state',
                  'tracking_reference_price','tracking_comparison_anchor','tracking_reference_evidence'):
        assert after[field]==before[field]
    assert after['latest_price']==close
