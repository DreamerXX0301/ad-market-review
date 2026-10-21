"""Slow/weekly selections keep only one bounded symbol batch in memory."""
import math
import pytest

from app.domain.conditions import metrics_for, past_limit_up_metrics
from app.services import strategies
from test_workspace import ws, make_strategy


@pytest.mark.parametrize('period',['D','W'])
def test_slow_selection_never_materializes_all_market_histories(ws,monkeypatch,period):
    market,client=ws;template=market.data['600001.SH']
    for number in range(65):market.data[f'{600010+number:06d}.SH']=[dict(row) for row in template]
    monkeypatch.setattr(market,'history_panel',lambda **_:pytest.fail('all-market Python histories are forbidden'))
    if period=='D':
        # Force the compatibility slow path without loosening its semantics.
        original=market.panel
        def bare(day):return [{key:value for key,value in row.items() if key!='indicator_warmup_bars'} for row in original(day)]
        monkeypatch.setattr(market,'panel',bare)
    requests=[]
    def bounded(symbols,start=None,end=None):
        requests.append(list(symbols))
        assert len(symbols)<=32
        return {symbol:market.bars(symbol,end) for symbol in symbols}
    monkeypatch.setattr(market,'condition_histories',bounded,raising=False)
    definition={'field':'close','op':'>','right_field':'ma5'}
    created=client.post('/api/v1/strategies',json={'name':'bounded fixture','period':period,'definition':definition}).json()
    response=client.post('/api/v1/strategies/'+created['strategy_id']+'/runs',json={})
    assert response.status_code==200,response.text
    result=response.json()
    assert len(requests)==math.ceil(len(market.data)/32)
    assert sum(map(len,requests))==len(result['items'])==len(market.data)
    for row in result['items']:
        expected=metrics_for(market.bars(row['instrument_id']),market.entry,market.days,period)
        assert row['checks']['actual']==expected['close']
        assert row['checks']['right_actual']==expected['ma5']


def test_daily_cross_ma_uses_prior_market_day_panel_without_slow_history(ws,monkeypatch):
    market,client=ws
    monkeypatch.setattr(market,'history_panel',lambda **_:pytest.fail('D cross must use the fast panel'))
    monkeypatch.setattr(strategies,'selection_history_batch',lambda *a,**k:pytest.fail('D cross must not read all seeds again'))
    strategy=make_strategy(client,{'field':'cross_above_ma30','op':'==','value':True})
    response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert response.status_code==200,response.text
    for row in response.json()['items']:
        expected=metrics_for(market.bars(row['instrument_id']),market.entry,market.days)
        assert row['checks']['actual'] is expected['cross_above_ma30']


@pytest.mark.parametrize('count,complete,expected',[(1,False,True),(0,False,None),(0,True,False),(2,True,True)])
def test_past_year_boolean_uses_verified_rolling_count_proof(count,complete,expected):
    values={'limit_up_count_1y_confirmed_count':count,'limit_up_count_1y_coverage':{'complete':complete,'window_start':'2025-10-20','window_end':'2026-10-19'},'limit_up_count_1y_reason':'fixture gap'}
    result=past_limit_up_metrics(values)
    assert result['limit_up_1y'] is expected
    assert result['limit_up_1y_evidence']['confirmed_count']==count
    assert result['limit_up_1y_evidence']['coverage']['window_end']=='2026-10-19'


def test_year_boolean_selection_stays_fast_and_retains_verified_proof(ws,monkeypatch):
    market,client=ws
    monkeypatch.setattr(market,'history_panel',lambda **_:pytest.fail('year condition must not materialize all-market histories'))
    def counts(self,_market,symbols,day,calendar,**kwargs):
        return {symbol:{'limit_up_count_1y_confirmed_count':1 if symbol=='600001.SH' else 0,
                        'limit_up_count_1y_coverage':{'complete':False,'window_end':calendar[calendar.index(day)-1]}}
                for symbol in symbols}
    monkeypatch.setattr(strategies.LimitConditionEvidence,'batch_count_metrics',counts)
    strategy=make_strategy(client,{'field':'limit_up_1y','op':'==','value':True})
    response=client.post('/api/v1/strategies/'+strategy['strategy_id']+'/runs',json={})
    assert response.status_code==200,response.text
    result={row['instrument_id']:row for row in response.json()['items']}
    assert result['600001.SH']['decision']=='matched'
    assert result['000001.SZ']['decision']=='unknown'
    for row in result.values():
        assert row['checks']['evidence']['coverage']['complete'] is False
        assert row['metrics']['limit_up_1y_evidence']==row['checks']['evidence']
