"""Pure bounded preparation keeps the original full metric/history contract."""
from copy import deepcopy

import pytest

from app.domain.conditions import metrics_for,validate
from app.domain.history_events import HistoryContext
from app.domain.strategy_prices import prepare_condition_prices
from app.services import condition_basis_cache as basis, strategy_periods as periods
from app.services import selection_compute as compute, strict_structure_cache as strict
from test_selection_shared_basis import inputs


@pytest.mark.parametrize('fault',['none','empty','duplicate_action','gap','zero','amount','duplicate_day','unsorted'])
def test_pure_preparation_matches_original_metrics_history_and_reusable_seed(inputs,fault):
    market,day,symbols,token,generation=inputs;symbol=symbols[0]
    rows=deepcopy(market.data[symbol]);actions=deepcopy(market.events[symbol])
    if fault=='empty':rows=[]
    elif fault=='duplicate_action':actions.append(deepcopy(actions[0]))
    elif fault=='gap':rows.pop(-17)
    elif fault=='zero':rows[-17]['volume']=0
    elif fault=='amount':rows[-17]['amount']=None
    elif fault=='duplicate_day':rows.insert(-17,deepcopy(rows[-17]))
    elif fault=='unsorted':rows.reverse()
    calendar=market.days;coverage={'known':True,'source_token':token,'snapshot_id':generation[0],'reason':None}
    options=periods.metric_request(True,[{'band_period':21,'rank_window':25}])
    request={'symbol':symbol,'day':day,'calendar':calendar,'rows':rows,'actions':actions,
        'basis_key':basis.key_for(symbol,day,generation,token),'snapshot':generation[0],
        'history_first':calendar[-64],'coverage':coverage,
        'requests':{period:{'include_higher_lows':True,'boll_configs':options.boll_configs} for period in ('D','W','M')}}
    context=prepare_condition_prices(rows,actions,calendar,day,'forward',instrument_id=symbol,actions_coverage=coverage)
    expected={period:metrics_for(rows,day,calendar,period,include_higher_lows=True,
        boll_configs=options.boll_configs,price_context=deepcopy(context)) for period in request['requests']}
    history=HistoryContext(rows,calendar,day,generation[0],retain_start=calendar[-64],price_context=deepcopy(context))
    result=compute.prepare_job(request)
    assert result['metrics']==expected
    for field in ('quotes','raw_quotes','daily','invalid_dates','action_dates','unknown_action_dates','price_basis'):
        assert getattr(result['history'],field)==getattr(history,field)
    assert 'raw_rows' not in result['price_context'] and 'price_rows' not in result['price_context']
    if result['basis_record'] is not None:
        assert basis.remember_encoded(request['basis_key'],result['basis_record'])
        restored=basis.restore(request['basis_key'])
        assert restored['raw_rows']==context['raw_rows'] and restored['price_rows']==context['price_rows']
    # Original ordinary computation also prepares a strict input proof for a
    # later different rule. No price read or grouping may be needed then.
    for period in request['requests']:
        periods.remember_prepared(day,period,result['metrics'][period],instrument_id=symbol,
            generation=generation,request=options,price_context=result['price_context'],strict_record=result['strict_records'][period])
        key=periods._summary_key(day,period,symbol,generation,result['price_context'])
        assert strict.restore(key)==strict.decode_record(result['strict_records'][period])
    if all(value is not None for value in result['strict_records'].values()):
        changed=periods.metric_request(True,[{'band_period':22,'rank_window':25}])
        replay=compute.prepare_job({'kind':'strict-structures','identity':(symbol,day,generation),
            'periods':{period:{'record':value,'include_higher_lows':True,'boll_configs':changed.boll_configs}
                       for period,value in result['strict_records'].items()}})
        for period,(higher,bands) in replay['values'].items():
            original=metrics_for(rows,day,calendar,period,include_higher_lows=True,
                boll_configs=changed.boll_configs,price_context=deepcopy(context))
            assert (higher,bands)==(original['higher_lows_state'],original['boll_states'])


def test_pure_ordinary_prewarms_new_structure_without_source_or_seed_read(inputs,monkeypatch):
    market,day,symbols,token,generation=inputs;symbol=symbols[0]
    request={'symbol':symbol,'day':day,'calendar':market.days,'rows':market.data[symbol],
        'actions':market.events[symbol],'basis_key':basis.key_for(symbol,day,generation,token),
        'snapshot':generation[0],'history_first':None,
        'coverage':{'known':True,'source_token':token,'snapshot_id':generation[0],'reason':None},
        'requests':{'D':{'include_higher_lows':False,'boll_configs':()}}}
    result=compute.prepare_job(request);ordinary=periods.metric_request()
    periods.remember_prepared(day,'D',result['metrics']['D'],instrument_id=symbol,generation=generation,
        request=ordinary,price_context=result['price_context'],strict_record=result['strict_records']['D'])
    config={'band_period':21,'rank_window':25};changed=periods.metric_request(True,[config])
    context=prepare_condition_prices(request['rows'],request['actions'],market.days,day,'forward',
        instrument_id=symbol,actions_coverage=request['coverage'])
    expected=metrics_for(request['rows'],day,market.days,include_higher_lows=True,boll_configs=changed.boll_configs,price_context=context)
    def forbidden(*args,**kwargs):pytest.fail('new rule must use strict proof, never reprepare quotes')
    monkeypatch.setattr(strict,'build',forbidden);monkeypatch.setattr(basis,'restore',forbidden)
    actual=periods.cached_metrics_if_ready(day,'D',instrument_id=symbol,generation=generation,
        request=changed,price_context=result['price_context'])
    assert actual==expected


def test_spawn_replays_new_multi_period_structures_only_once_from_verified_inputs(inputs,monkeypatch):
    from app.services.selection_compute_pool import compute_session
    market,day,_,token,generation=inputs;contexts={};expected={};symbols=[]
    tree=validate({'all':[{'field':'higher_lows','op':'confirmed_rising_lows','value':60},
        *[{'field':'boll_signal','op':'matches','value':{'band_period':n,'rank_window':25},'period':period}
          for period,n in [('D',20),('D',22),('W',20),('M',22)]]]})
    plan=periods.compile_metric_plan(tree)
    for number in range(8):
        symbol=f'{600010+number:06}.SH';symbols.append(symbol)
        rows=deepcopy(market.data['600000.SH']);actions=deepcopy(market.events['600000.SH'])
        for row in rows:row['instrument_id']=symbol
        for action in actions:action['thscode']=symbol
        coverage={'known':True,'source_token':token,'snapshot_id':generation[0],'reason':None}
        request={'symbol':symbol,'day':day,'calendar':market.days,'rows':rows,'actions':actions,
            'basis_key':basis.key_for(symbol,day,generation,token),'snapshot':generation[0],
            'history_first':None,'coverage':coverage,
            'requests':{period:{'include_higher_lows':False,'boll_configs':()} for period in plan.periods}}
        prepared=compute.prepare_job(request);contexts[symbol]=prepared['price_context']
        price=prepare_condition_prices(rows,actions,market.days,day,'forward',instrument_id=symbol,actions_coverage=coverage)
        for period in plan.periods:
            periods.remember_prepared(day,period,prepared['metrics'][period],instrument_id=symbol,generation=generation,
                request=periods.metric_request(),price_context=contexts[symbol],strict_record=prepared['strict_records'][period])
            expected[(symbol,period)]=metrics_for(rows,day,market.days,period,include_higher_lows=True,
                boll_configs=plan.requests[period].boll_configs,price_context=deepcopy(price))
        if number%2==0:
            periods.cached_metrics_if_ready(day,'W',instrument_id=symbol,generation=generation,
                request=periods.metric_request(False,[{'band_period':20,'rank_window':25}]),price_context=contexts[symbol])
    def forbidden(*args,**kwargs):pytest.fail('structure replay must never reopen or regroup a full seed')
    monkeypatch.setattr(basis,'restore',forbidden);monkeypatch.setattr(basis,'restore_structure',forbidden)
    monkeypatch.setattr(strict,'build',forbidden)
    with compute_session(max_workers=2) as session:
        periods.prepare_saved_structures(symbols,day,plan.periods,plan,generation,contexts)
        assert session.stats['submitted_jobs']==8
        for symbol in symbols:
            for period in plan.periods:
                assert periods.cached_metrics_if_ready(day,period,instrument_id=symbol,generation=generation,
                    request=plan.requests[period],price_context=contexts[symbol])==expected[(symbol,period)]
        periods.prepare_saved_structures(symbols,day,plan.periods,plan,generation,contexts)
        assert session.stats['submitted_jobs']==8
        periods.prepare_saved_structures(symbols,day,plan.periods,plan,('changed','b'*64),contexts)
        assert session.stats['submitted_jobs']==8
    assert session.stats['remaining_pids']==[]
