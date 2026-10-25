"""Raw facts projected into an adjusted strategy keep exact legacy evidence."""
from copy import deepcopy
import pytest
from app.domain.conditions import metrics_for,merge_condition_price_metrics
from app.domain.strategy_prices import prepare_condition_prices
from test_history_materialization_tail import sample


@pytest.mark.parametrize('period',['D','W','M'])
@pytest.mark.parametrize('fault',['none','gap','duplicate','zero_volume','missing_volume','missing_amount','negative_amount','unknown_action','invalid_ohlc','missing_today','action'])
@pytest.mark.parametrize('offset',[0,1,4,13])
def test_raw_projection_skips_only_discarded_indicators(period,fault,offset):
    rows,calendar=sample(200);day=calendar[-1-offset];rows=[r for r in rows if r['date']<=day]
    if fault=='gap':rows.pop(-13)
    elif fault=='duplicate':rows.append(dict(rows[-11]))
    elif fault=='zero_volume':rows[-12]['volume']=0
    elif fault=='missing_volume':rows[-12]['volume']=None
    elif fault=='missing_amount':rows[-1]['amount']=None
    elif fault=='negative_amount':rows[-1]['amount']=-1
    elif fault=='unknown_action':rows[-5]['corporate_action_coverage_known']=False
    elif fault=='invalid_ohlc':rows[-7]['high']=rows[-7]['low']-1
    elif fault=='missing_today':rows.pop()
    elif fault=='action':rows[-9]['corporate_action']=True
    context=prepare_condition_prices(rows,[],calendar,day,'forward',instrument_id='600000.SH',
        actions_coverage={'known':True,'source_token':('fixed','one'),'snapshot_id':'test'})
    raw=context['raw_rows'];usable=context['price_basis']=='forward_event_affine'
    source=metrics_for(raw[-2:] if period=='D' else raw,day,calendar,period)
    if period=='D' and any(row.get('corporate_action_coverage_known') is not True for row in raw[-2:]):
        for field in ('change_pct','volume_ratio','volume_expand','volume_shrink'):
            source[field]=None;source[field+'_reason']='前后原始报价的公司行动覆盖未知，不能证明原价涨幅或量能可比'
    prices=metrics_for(context['price_rows'] if usable else [],day,calendar,period)
    expected=merge_condition_price_metrics(source,prices,context,day,period)
    before=deepcopy(context)
    actual=metrics_for(rows,day,calendar,period,price_context=context)
    assert actual==expected
    assert context==before


def test_raw_fact_path_does_not_calculate_discarded_breakout(monkeypatch):
    from app.analytics import rolling_high,indicators
    rows,calendar=sample(160);day=calendar[-1]
    # All input coverage/period flags still come from complete classification.
    calls=[];original=indicators.enrich_bars
    def observed(records,cal=None,**kwargs):
        calls.append(len(records));return original(records,cal,**kwargs)
    monkeypatch.setattr(indicators,'enrich_bars',observed)
    monkeypatch.setattr(rolling_high,'rolling_high_state',lambda *a,**k:pytest.fail('raw breakout is discarded'))
    result=metrics_for(rows,day,calendar,'W',_source_facts_only=True)
    assert result['observed_date'] and max(calls)<=2


@pytest.mark.parametrize('fault',['none','gap','duplicate','zero_volume','unknown_action','action','noncanonical_date','date_alias','unexpected_date'])
@pytest.mark.parametrize('require_history',[True,False])
def test_selection_prepared_lanes_share_normalization_but_never_mutable_metrics(monkeypatch,fault,require_history):
    from app.domain import conditions
    from app.domain.history_events import HistoryContext
    from app.services.strategies import selection_metric_batch
    from app.services.strategy_periods import compile_metric_plan,cached_metrics,clear_metric_cache
    rows,calendar=sample(180);day=calendar[-1];symbol='600000.SH'
    if fault=='gap':rows.pop(-12)
    elif fault=='duplicate':rows.append(dict(rows[-9]))
    elif fault=='zero_volume':rows[-7]['volume']=0
    elif fault=='unknown_action':rows[-4]['corporate_action_coverage_known']=False
    elif fault=='action':rows[-10]['corporate_action']=True
    elif fault=='noncanonical_date':rows[-7]['date']+='T10:00:00'
    elif fault=='date_alias':rows[-7]['trade_date']=rows[-7].pop('date')
    elif fault=='unexpected_date':rows[-7]['date']='2020-01-01'
    class Market:
        def condition_histories(self,identifiers,end):return {symbol:deepcopy(rows)}
        def actions_for_instruments(self,identifiers):return {symbol:[]}
        def status(self):return {'snapshot_id':'fixed'}
        def calendar(self):return calendar
    plan=compile_metric_plan({'all':[{'field':'ma5','op':'>','value':0,'period':period} for period in ('D','W','M')]})
    token={'verified_closure_sha256':'e'*64}
    contexts={}
    histories,_,_=selection_metric_batch(Market(),[symbol],day,plan,('fixed','closure'),price_basis='forward',source_token=token,price_contexts=contexts,require_history=require_history)
    plain=prepare_condition_prices(rows,[],calendar,day,'forward',instrument_id=symbol,
        actions_coverage={'known':True,'source_token':token,'snapshot_id':'fixed'})
    for lane in ('raw_rows','price_rows'):
        canonical=all(isinstance(row.get('date'),str) and len(row['date'])==10 for row in plain[lane])
        assert isinstance(contexts[symbol][lane],conditions._PreparedMetricBars)==(require_history and canonical)
    expected_history=HistoryContext(rows,calendar,day,'fixed',retain_start=calendar[-31],price_context=deepcopy(plain))
    expected={period:metrics_for(rows,day,calendar,period,price_context=plain) for period in ('D','W','M')}
    calls=[];original=conditions.normalized_bar
    def observed(row):calls.append(row.get('date'));return original(row)
    monkeypatch.setattr(conditions,'normalized_bar',observed)
    actual_history=HistoryContext(histories[symbol],calendar,day,'fixed',retain_start=calendar[-31],price_context=contexts[symbol])
    clear_metric_cache()
    try:
        actual={period:cached_metrics(histories[symbol],day,calendar,period,instrument_id=symbol,generation=('fixed','closure'),price_context=contexts[symbol]) for period in ('D','W','M')}
        assert actual==expected
        for field in ('daily','quotes','raw_quotes','invalid_dates','unknown_action_dates'):
            assert getattr(actual_history,field)==getattr(expected_history,field)
        if require_history and fault!='noncanonical_date':
            assert len(calls)<=len(rows)*2+2  # raw lane + adjusted lane + two raw day facts
        last=next(reversed(actual_history.quotes),None)
        if last and actual_history.quotes[last]:actual_history.quotes[last]['close']=-1
        actual['D']['close']=-1
        assert cached_metrics([],day,calendar,'D',instrument_id=symbol,generation=('fixed','closure'),price_context=contexts[symbol])==expected['D']
    finally:clear_metric_cache()
