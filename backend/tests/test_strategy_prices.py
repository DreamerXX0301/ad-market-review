"""Explicit forward strategy prices retain independent raw source evidence."""
from copy import deepcopy
from datetime import date,timedelta

import pytest

from app.domain.conditions import evaluate,metrics_for
from app.domain.history_events import HistoryContext
from app.domain.strategy_prices import prepare_condition_prices,condition_prices_for,project_reference_price,VERSION
from app.services import strategy_periods
from test_workspace import ws

SYMBOL='600001.SH'


def fixture(count=400,action_offset=80):
    calendar=[];cursor=date(2024,1,1)
    while len(calendar)<count:
        if cursor.weekday()<5:calendar.append(cursor.isoformat())
        cursor+=timedelta(days=1)
    at=count-action_offset
    rows=[{'instrument_id':SYMBOL,'date':day,'open':20+i*.01-(1 if i>=at else 0),
           'high':21+i*.01-(1 if i>=at else 0),'low':19+i*.01-(1 if i>=at else 0),
           'close':20+i*.01-(1 if i>=at else 0),'volume':100+(i%5)*5,'amount':10000.,
           'corporate_action':i==at,'corporate_action_coverage_known':True} for i,day in enumerate(calendar)]
    actions=[{'thscode':SYMBOL,'date':calendar[at],'dividend_per_share':1.,'per_share_bonus':0.,
              'allotment_ratio':0.,'allotment_price':0.,'currency':'CNY'}]
    return rows,actions,calendar


def prepared(rows,actions,calendar,day=None,**kwargs):
    return prepare_condition_prices(rows,actions,calendar,day or calendar[-1],'forward',instrument_id=SYMBOL,
        actions_coverage=kwargs.pop('actions_coverage',{'known':True,'source_token':('fixture-source-sha',1),'snapshot_id':'fixture'}),**kwargs)


@pytest.mark.parametrize('period',['D','W','M'])
def test_none_context_preserves_the_original_metric_results_exactly(period):
    rows,actions,calendar=fixture();original=deepcopy(rows)
    context=prepare_condition_prices(rows,actions,calendar,calendar[-1],'none')
    assert metrics_for(rows,calendar[-1],calendar,period,price_context=context)==metrics_for(rows,calendar[-1],calendar,period)
    assert rows==original


def test_forward_weekly_indicators_are_continuous_but_raw_data_are_untouched():
    rows,actions,calendar=fixture();original=deepcopy(rows);context=prepared(rows,actions,calendar)
    assert context['price_basis']=='forward_event_affine'
    assert context['price_rows'][0]['close']==rows[0]['close']-1
    assert context['price_rows'][-1]['close']==rows[-1]['close']
    assert context['price_rows'][-80]['corporate_action_original'] is True
    assert context['price_rows'][-80]['corporate_action'] is False
    assert context['price_rows'][0]['raw_close']==rows[0]['close']
    before=metrics_for(rows,calendar[-1],calendar,'W');after=metrics_for(rows,calendar[-1],calendar,'W',price_context=context)
    assert before['macd_dif'] is None and after['macd_dif'] is not None
    assert after['indicator_warmup_bars']>=35 and after['adjustment']['usable'] is True
    assert rows==original


def test_action_day_does_not_turn_into_a_proven_raw_gain_or_volume_signal():
    rows,actions,calendar=fixture();day=actions[0]['date'];context=prepared(rows,actions,calendar,day)
    metrics=metrics_for(rows,day,calendar,price_context=context)
    assert metrics['ma60'] is not None
    for field in ('change_pct','volume_ratio','volume_expand','volume_shrink'):
        assert metrics[field] is None,field
    assert metrics['volume']==rows[-80]['volume'] and metrics['amount']==10000
    assert context['price_rows'][-1]['corporate_action_original'] is True


@pytest.mark.parametrize('fault',['coverage','missing_actions','empty_actions','duplicate_action','wrong_identity','currency','negative','missing_previous','duplicate_quote','unmarked_action'])
def test_unknown_or_invalid_action_evidence_cannot_silently_fall_back_to_raw(fault):
    rows,actions,calendar=fixture();coverage={'known':True,'source_token':('fixture',1)}
    if fault=='coverage':coverage['known']=False
    elif fault=='missing_actions':actions=None
    elif fault=='empty_actions':actions=[]
    elif fault=='duplicate_action':actions+=deepcopy(actions)
    elif fault=='wrong_identity':actions[0]['thscode']='600002.SH'
    elif fault=='currency':actions[0]['currency']='USD'
    elif fault=='negative':actions[0]['dividend_per_share']=-1
    elif fault=='missing_previous':rows.pop(-81)
    elif fault=='duplicate_quote':rows.append(dict(rows[-1]))
    else:rows[-80]['corporate_action']=False
    context=prepared(rows,actions,calendar,actions_coverage=coverage)
    assert context['price_basis']=='unavailable' and context['price_rows']==[]
    metrics=metrics_for(rows,calendar[-1],calendar,price_context=context)
    assert metrics['close'] is None and metrics['ma5'] is None and metrics['macd_dif'] is None
    assert evaluate({'field':'close','op':'>','value':0},metrics)['result'] is None
    assert context['adjustment']['reason'] and metrics['adjustment']['reason']


def test_later_actions_cannot_change_an_earlier_anchor_and_future_anchor_is_rejected():
    rows,actions,calendar=fixture();day=calendar[-100]
    old=prepared(rows,actions,calendar,day)
    without=prepared(rows,[],calendar,day)
    assert old['price_rows']==without['price_rows']
    current=prepared(rows,actions,calendar)
    result=metrics_for(rows,day,calendar,price_context=current)
    assert result['close'] is None and '未来锚' in result['close_reason']
    context=HistoryContext(rows,calendar,calendar[-1],price_context=current)
    assert context.at(day)['context'] is None


def test_event_price_comparison_and_reclaim_share_the_observation_anchor():
    rows,actions,calendar=fixture();context=prepared(rows,actions,calendar)
    history=HistoryContext(rows,calendar,calendar[-1],price_context=context)
    raw_event=rows[-90];converted_event=history.quotes[raw_event['date']]
    assert converted_event['close']==raw_event['close']-1
    current,reason=history.price_binding(converted_event,calendar[-1])
    assert reason is None and current==rows[-1]['close']
    projected,proof=project_reference_price(raw_event['close'],raw_event['date'],context)
    assert projected==converted_event['close'] and proof['state']=='ready'
    raw_history=HistoryContext(rows,calendar,calendar[-1])
    assert raw_history.price_binding(raw_history.quotes[raw_event['date']],calendar[-1])[0] is None
    for day in calendar[-28:]:
        raw_metrics=metrics_for(rows,day,calendar)
        assert history.daily[day]['volume_ratio']==raw_metrics['volume_ratio']
        assert history.daily[day]['change_pct']==raw_metrics['change_pct']


def test_historical_absolute_price_where_is_a_review_at_T_on_the_same_T_anchor():
    rows,actions,calendar=fixture();context=prepared(rows,actions,calendar)
    history=HistoryContext(rows,calendar,calendar[-1],price_context=context)
    event=rows[-90];threshold=event['close']-.5
    rule={'field':'close','op':'>','value':threshold}
    assert evaluate(rule,metrics_for(rows,event['date'],calendar))['result'] is True
    assert evaluate(rule,history.daily[event['date']])['result'] is False
    # The change is the explicitly selected T-anchor price convention, not a
    # change to the event day's raw volume or daily return facts.
    assert history.daily[event['date']]['change_pct']==metrics_for(rows,event['date'],calendar)['change_pct']


def test_forward_selection_uses_bounded_dual_lane_history_and_freezes_the_basis(ws,monkeypatch):
    from app import core
    from app.services import strategies
    market,client=ws
    for symbol,rows in market.data.items():
        for row in rows:row['corporate_action_coverage_known']=True
        rows[50]['corporate_action']=True;rows[40]['volume']=300
    actions={symbol:[{'thscode':symbol,'date':market.days[50],'dividend_per_share':1.,'per_share_bonus':0.,
                     'allotment_ratio':0.,'allotment_price':0.,'currency':'CNY'}] for symbol in market.data}
    monkeypatch.setattr(market,'verify_condition_sources',lambda:('verified-fixture-source-sha',),raising=False)
    calls=[]
    def action_batch(ids):calls.append(list(ids));assert len(ids)<=32;return {symbol:actions[symbol] for symbol in ids}
    monkeypatch.setattr(market,'actions_for_instruments',action_batch,raising=False)
    monkeypatch.setattr(market,'panel',lambda *a,**k:pytest.fail('forward must not use a raw panel'))
    monkeypatch.setattr(market,'history_condition_metrics',lambda *a,**k:pytest.fail('forward must not use raw prepared matrices'),raising=False)
    definition={'all':[{'field':'close','op':'>','right_field':'ma60'},
        {'history_event':{'window':{'from':1,'to':35},'match':'any',
         'where':{'field':'volume_ratio','op':'>=','value':2},
         'then':{'field':'close','op':'>','right_event_field':'close'}}}]}
    response=client.post('/api/v1/strategies',json={'name':'前复权合成测试','period':'D','price_basis':'forward','definition':definition})
    assert response.status_code==200,response.text
    result=client.post('/api/v1/strategies/'+response.json()['strategy_id']+'/runs',json={})
    assert result.status_code==200,result.text
    run=result.json();row=next(item for item in run['items'] if item['instrument_id']=='600001.SH')
    assert row['decision']=='matched'
    assert row['metrics']['price_basis']=='forward_event_affine' and row['metrics']['anchor_date']==market.entry
    assert row['metrics']['requested_price_basis']=='forward'
    event=row['checks']['checks'][1]['selected_event']
    assert event['price_basis']=='forward_event_affine' and event['anchor_date']==market.entry
    assert event['close']==pytest.approx(event['raw_close']-1)
    assert calls==[list(sorted(market.data))]
    assert run['universe_definition']['requested_price_basis']=='forward'
    assert run['universe_definition']['price_contract']==VERSION
    with core.connect() as connection:
        stored=connection.execute('SELECT input_sha256 FROM selection_run WHERE run_id=?',(run['run_id'],)).fetchone()
    assert len(stored[0])==64


def test_gap_and_incomplete_weeks_still_reset_adjusted_indicators():
    rows,actions,calendar=fixture();rows.pop(-30);context=prepared(rows,actions,calendar)
    assert context['price_basis']=='forward_event_affine'
    daily=metrics_for(rows,calendar[-1],calendar,price_context=context)
    weekly=metrics_for(rows,calendar[-1],calendar,'W',price_context=context)
    assert daily['macd_dif'] is None and weekly['macd_dif'] is None
    assert '缺少' in daily['macd_dif_reason'] and weekly['indicator_warmup_bars']<35


def test_summary_keys_separate_basis_anchor_and_verified_source(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path));strategy_periods.clear_metric_cache()
    rows,actions,calendar=fixture();day=calendar[-1];context=prepared(rows,actions,calendar)
    kwargs={'instrument_id':SYMBOL,'generation':'fixture'}
    raw=strategy_periods.cached_metrics(rows,day,calendar,'W',**kwargs)
    forward=strategy_periods.cached_metrics(rows,day,calendar,'W',price_context=context,**kwargs)
    assert raw['macd_dif'] is None and forward['macd_dif'] is not None
    assert strategy_periods.cached_metrics_if_ready(day,'W',price_context=context,**kwargs)==forward
    other=prepared(rows,actions,calendar,actions_coverage={'known':True,'source_token':('fixture-source-sha',2)})
    assert strategy_periods.cached_metrics_if_ready(day,'W',price_context=other,**kwargs) is None
    no_proof=prepared(rows,actions,calendar,actions_coverage={'known':True})
    assert no_proof['cache_key'] is None
    assert strategy_periods.cached_metrics_if_ready(day,'W',price_context=no_proof,**kwargs) is None
    strategy_periods.clear_metric_cache()


def test_real_source_wrapper_requires_verified_closure_and_explicit_symbol_actions():
    rows,actions,calendar=fixture()
    class Market:
        def status(self):return {'snapshot_id':'fixture'}
        def actions_for_instruments(self,ids):return {SYMBOL:actions}
        def verify_condition_sources(self):return ('verified-fixture-source-sha',)
    market=Market();context=condition_prices_for(market,SYMBOL,rows,calendar,calendar[-1],'forward')
    assert context['price_basis']=='forward_event_affine'
    market.verify_condition_sources=lambda:None
    unavailable=condition_prices_for(market,SYMBOL,rows,calendar,calendar[-1],'forward')
    assert unavailable['price_basis']=='unavailable'
    assert VERSION=='strategy-local-forward-v1'


def test_forward_historical_pool_checks_receive_original_close_not_adjusted_close():
    from app.services.history_events import attach_limit_events
    rows,actions,calendar=fixture();context=prepared(rows,actions,calendar)
    history=HistoryContext(rows,calendar,calendar[-1],price_context=context,retain_start=calendar[-90])
    expected={row['date']:row['close'] for row in rows}
    class Evidence:
        def historical_metrics(self,symbol,day,quote):
            assert symbol==SYMBOL and quote['close']==expected[day]
            return {'limit_up':day==calendar[-90]}
    node={'history_event':{'where':{'field':'limit_up','op':'==','value':True}}}
    attach_limit_events(history,SYMBOL,[node],Evidence())
    assert history.daily[calendar[-90]]['limit_up'] is True
    assert history.quotes[calendar[-90]]['close']!=expected[calendar[-90]]


def test_unknown_raw_action_coverage_does_not_prove_volume_or_daily_gain():
    rows,actions,calendar=fixture();rows[-1]['corporate_action_coverage_known']=False
    context=prepared(rows,actions,calendar)
    metrics=metrics_for(rows,calendar[-1],calendar,price_context=context)
    assert metrics['volume_ratio'] is metrics['change_pct'] is None
    assert evaluate({'field':'volume_ratio','op':'>','value':0},metrics)['result'] is None


def test_explicit_none_and_forward_contexts_do_not_cross_contaminate_evidence_or_cache(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path));strategy_periods.clear_metric_cache()
    rows,actions,calendar=fixture();day=calendar[-1]
    none=prepare_condition_prices(rows,actions,calendar,day,'none',instrument_id=SYMBOL,
        actions_coverage={'known':True,'source_token':('fixture',1)})
    forward=prepared(rows,actions,calendar)
    originals=deepcopy((none,forward))
    rule={'history_event':{'window':{'from':1,'to':20},'match':'latest',
        'where':{'field':'volume','op':'>','value':0},
        'then':{'field':'close','op':'>','right_event_field':'close'}}}
    reference=HistoryContext(rows,calendar,day)
    expected=evaluate(rule,metrics_for(rows,day,calendar),reference.at(day))
    kwargs={'instrument_id':SYMBOL,'generation':'shared-fixture'}
    raw_metrics=strategy_periods.cached_metrics(rows,day,calendar,'W',**kwargs)
    for context in (none,forward,none,forward,none):
        history=HistoryContext(rows,calendar,day,price_context=context)
        metrics=strategy_periods.cached_metrics(rows,day,calendar,'W',price_context=context,**kwargs)
        checks=evaluate(rule,metrics_for(rows,day,calendar,price_context=context),history.at(day))
        if context is none:
            assert history.price_context is None
            assert metrics==raw_metrics and checks==expected
            assert 'requested_price_basis' not in checks['evidence']
            assert 'raw_close' not in checks['selected_event']
            assert checks['evidence']['price_basis']==checks['selected_event']['price_basis']=='none'
        else:
            assert metrics['requested_price_basis']=='forward'
            assert checks['evidence']['requested_price_basis']=='forward'
            assert checks['selected_event']['price_basis']=='forward_event_affine'
    assert (none,forward)==originals
    strategy_periods.clear_metric_cache()


@pytest.mark.parametrize('fault',['zero','missing_volume','invalid_open','invalid_high','missing_session','unknown_action','missing_amount','negative_amount'])
def test_forward_historical_raw_facts_match_the_strict_none_quote_boundary(fault):
    rows,actions,calendar=fixture();previous=rows[-6]
    if fault=='zero':previous['volume']=0
    elif fault=='missing_volume':previous['volume']=None
    elif fault=='invalid_open':previous['open']=0
    elif fault=='invalid_high':previous['high']=previous['close']-1
    elif fault=='missing_session':rows.pop(-6)
    elif fault=='unknown_action':previous['corporate_action_coverage_known']=False
    elif fault=='missing_amount':rows[-5]['amount']=None
    else:rows[-5]['amount']=-1
    context=prepared(rows,actions,calendar)
    raw=HistoryContext(rows,calendar,calendar[-1]);adjusted=HistoryContext(rows,calendar,calendar[-1],price_context=context)
    day=calendar[-5]
    for field in ('change_pct','volume_ratio','volume','amount','volume_expand','volume_shrink','intraday_high_change_pct'):
        assert adjusted.daily[day][field]==raw.daily[day][field],(fault,field)
    if fault not in ('missing_amount','negative_amount'):
        assert adjusted.daily[day]['change_pct'] is adjusted.daily[day]['volume_ratio'] is None
        assert adjusted.daily[day]['intraday_high_change_pct'] is None


@pytest.mark.parametrize('action',[False,True])
def test_raw_pair_facts_avoid_indicator_recurrence_but_preserve_valid_pair_metrics(action):
    from app.domain.history_events import _raw_daily_facts
    from app.domain.conditions import normalized_bar
    rows,_,calendar=fixture();pair=[normalized_bar(row) for row in rows[-2:]]
    pair[-1]['corporate_action']=action;pair[0]['limit_up']=True;pair[-1]['limit_up']=False
    source=metrics_for(pair,calendar[-1],calendar)
    actual=_raw_daily_facts(pair[-1],pair[0],{day:i for i,day in enumerate(calendar)},calendar[-2])
    for key,value in actual.items():assert value==source.get(key),(key,value,source.get(key))
