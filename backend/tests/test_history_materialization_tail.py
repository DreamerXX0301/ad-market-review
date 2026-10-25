"""Keep all seed recurrence while materializing only retained event proofs."""
from copy import deepcopy
from datetime import date,timedelta
import pytest
from app.analytics.indicators import enrich_bars
from app.domain import history_events as history
from app.domain.strategy_prices import prepare_condition_prices
from test_workspace import ws


def sample(size=350):
    start=date(2024,1,1);days=[]
    while len(days)<size:
        if start.weekday()<5:days.append(start.isoformat())
        start+=timedelta(days=1)
    rows=[{'date':day,'open':20+i%7*.17,'high':21+i%7*.17,'low':19+i%7*.17,'close':20.2+i%7*.17,
           'volume':1000+i%13*100,'amount':20000+i*4,'corporate_action':False,'corporate_action_coverage_known':True}
          for i,day in enumerate(days)]
    return rows,days


@pytest.mark.parametrize('basis',['none','forward'])
@pytest.mark.parametrize('window',[1,2,3,28,35,100,500])
@pytest.mark.parametrize('fault',['none','gap','zero_volume','invalid_ohlc','unknown_action','action'])
def test_tail_context_equals_full_output_for_every_retained_quote(monkeypatch,basis,window,fault):
    rows,calendar=sample();day=calendar[-1]
    if fault=='gap':rows.pop(-12)
    elif fault=='zero_volume':rows[-10]['volume']=0
    elif fault=='invalid_ohlc':rows[-9]['high']=rows[-9]['low']-1
    elif fault=='unknown_action':rows[-8]['corporate_action_coverage_known']=False
    elif fault=='action':rows[-20]['corporate_action']=True
    first=calendar[max(0,len(calendar)-window)]
    price=None
    if basis=='forward':
        # The action case is deliberately quarantined: no invented dividend.
        price=prepare_condition_prices(rows,[],calendar,day,'forward',instrument_id='600000.SH',
            actions_coverage={'known':True,'source_token':('immutable','one'),'snapshot_id':'test'})
    before=deepcopy(rows)
    original=history.retained_indicator_rows
    monkeypatch.setattr(history,'retained_indicator_rows',lambda records,cal,first=None:enrich_bars(records,cal))
    expected=history.HistoryContext(rows,calendar,day,'test',retain_start=first,price_context=deepcopy(price))
    calls=[]
    def observed(records,cal,first=None):
        result=original(records,cal,first);calls.append((len(records),len(result)));return result
    monkeypatch.setattr(history,'retained_indicator_rows',observed)
    actual=history.HistoryContext(rows,calendar,day,'test',retain_start=first,price_context=deepcopy(price))
    for key in ('quotes','raw_quotes','daily','invalid_dates','action_dates','unknown_action_dates','price_basis'):
        assert getattr(actual,key)==getattr(expected,key),key
    assert actual.event_cache==expected.event_cache=={}
    assert calls
    assert all(emitted<=min(seed,window+1) for seed,emitted in calls)
    assert rows==before  # all computations use private normalized rows


def test_first_retained_previous_quote_and_none_window_are_identical():
    rows,calendar=sample(80)
    assert history.retained_indicator_rows(rows,calendar)==enrich_bars(rows,calendar)
    result=history.retained_indicator_rows(rows,calendar,calendar[-5])
    assert len(result)==6 and result==enrich_bars(rows,calendar)[-6:]
    assert history.retained_indicator_rows([],calendar,calendar[-1])==[]
    assert history.retained_indicator_rows(rows,calendar,'2099-01-01')==enrich_bars(rows,calendar)[-1:]


@pytest.mark.parametrize('with_history',[True,False])
def test_forward_selection_keeps_small_batches_and_every_candidate(ws,monkeypatch,with_history):
    from app.services import strategies
    from app.services.strategy_periods import clear_metric_cache
    from app.services.forward_history_cache import clear_cache
    from test_forward_tracking_backtest import enable_forward_fixture
    market,client=ws;template=market.data['600001.SH']
    for number in range(35):
        symbol=f'{600010+number:06d}.SH'
        market.data[symbol]=[{**row,'instrument_id':symbol} for row in template]
    enable_forward_fixture(market,monkeypatch);requests=[];batch_size=16 if with_history else 32
    def bounded(symbols,start=None,end=None):
        requests.append(list(symbols));assert len(symbols)<=batch_size
        return {symbol:market.bars(symbol,end) for symbol in symbols}
    monkeypatch.setattr(market,'condition_histories',bounded,raising=False)
    definition={'all':[{'field':'close','op':'>','value':0},
        {'history_event':{'window':{'from':1,'to':10},'match':'latest',
                         'where':{'field':'volume','op':'>','value':0},
                         'then':{'field':'close','op':'>','right_event_field':'close'}}}]}
    if not with_history:definition={'field':'close','op':'>','value':0}
    clear_metric_cache();clear_cache()
    try:
        made=client.post('/api/v1/strategies',json={'name':'有界前复权','price_basis':'forward','definition':definition})
        assert made.status_code==200,made.text
        run=client.post('/api/v1/strategies/'+made.json()['strategy_id']+'/runs',json={})
        assert run.status_code==200,run.text
        rows=run.json()['items']
        assert {row['instrument_id'] for row in rows}==set(market.data)
        assert sum(map(len,requests))==len(rows)==len(market.data)
        assert max(map(len,requests))==batch_size
        assert len(requests)==(len(rows)+batch_size-1)//batch_size
        assert all(row['metrics']['requested_price_basis']=='forward' for row in rows)
    finally:clear_metric_cache();clear_cache()
