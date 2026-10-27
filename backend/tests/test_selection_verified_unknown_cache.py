"""A proven source rejection may be reused; absent provenance may never be."""
from copy import deepcopy
import pytest

from app.domain.conditions import evaluate, validate
from app.domain.strategy_prices import compact_source_token
from app.services import strategies, strategy_periods as periods, history_events as events
from app.services import condition_basis_cache as basis, forward_history_cache as history
from test_selection_shared_basis import inputs


def tree(window=20):
    return validate({'history_event':{'window':{'from':1,'to':window},'match':'any',
        'where':{'field':'volume_ratio','op':'>','value':1},
        'then':{'field':'close','op':'>','right_event_field':'close'}}})


def prepare(inputs, definition):
    market, day, symbols, token, generation=inputs
    plan=periods.compile_metric_plan(definition);prices={}
    cached=events.cached_forward_contexts(symbols,[definition],market.days,day,generation[0],token)
    bars,ready,_=strategies.selection_metric_batch(market,symbols,day,plan,generation,
        price_basis='forward',source_token=token,price_contexts=prices,require_history=True,
        history_cached_symbols=set(cached))
    contexts=events.contexts_for_batch(market,symbols,[definition],market.days,day,bars,
        price_basis='forward',price_contexts=prices,prepared_contexts=cached)
    result={}
    for symbol in symbols:
        metrics=ready[symbol]['D']
        if metrics is None:metrics=periods.cached_metrics(bars[symbol],day,market.days,'D',
            instrument_id=symbol,generation=generation,request=plan.requests['D'],price_context=prices[symbol])
        result[symbol]=(metrics,evaluate(definition,metrics,contexts[symbol].at(day)))
    return result,contexts


@pytest.mark.parametrize('fault',['empty','duplicate_actions','action_quote_missing'])
@pytest.mark.parametrize('restart',[False,True])
def test_verified_unavailable_reuses_inputs_and_recalculates_new_rules(inputs,monkeypatch,fault,restart):
    history.clear_cache()
    market,day,symbols,token,generation=inputs
    for symbol in symbols:
        if fault=='empty':market.data[symbol]=[]
        elif fault=='duplicate_actions':market.events[symbol].append(deepcopy(market.events[symbol][0]))
        else:
            excluded=market.events[symbol][0]['date']
            market.data[symbol]=[row for row in market.data[symbol] if row['date']!=excluded]
    original=tree();expected,contexts=prepare(inputs,original)
    assert all(context.price_basis=='unavailable' for context in contexts.values())
    changed=tree(35)
    # Independent preparation is the semantic oracle, before reads are barred.
    periods.clear_metric_cache();basis.clear_cache(disk=True);history.clear_cache()
    changed_expected,_=prepare(inputs,changed)
    if restart:periods.clear_metric_cache();basis.clear_cache();history.clear_cache()
    def never(*args,**kwargs):pytest.fail('verified negative history must not reread source')
    monkeypatch.setattr(market,'bars',never);monkeypatch.setattr(market,'actions_for_instruments',never)
    actual,contexts=prepare(inputs,original)
    assert actual==expected
    actual,_=prepare(inputs,changed)
    assert actual==changed_expected
    assert all(context.price_basis=='unavailable' for context in contexts.values())
    pristine=events.cached_forward_contexts(symbols,[original],market.days,day,generation[0],token)
    assert all(not context.event_cache for context in pristine.values())
    # Every return graph is independent, including unknown explanation objects.
    for context in contexts.values():context.raw_quotes.clear()
    again,_=prepare(inputs,original)
    assert again==expected
    history.clear_cache()


def test_unverified_source_or_unreadable_actions_cannot_cache_negative(inputs,monkeypatch):
    history.clear_cache()
    market,day,symbols,token,generation=inputs
    monkeypatch.setattr(market,'actions_for_instruments',lambda symbols:None)
    definition=tree();first,contexts=prepare(inputs,definition)
    assert all('_verified_preparation_source' not in context.price_context for context in contexts.values())
    assert not events.cached_forward_contexts(symbols,[definition],market.days,day,generation[0],token)
    assert all(basis.restore(basis.key_for(symbol,day,generation,token)) is None for symbol in symbols)
    history.clear_cache()


@pytest.mark.parametrize('fault',['source','anchor','missing_token','date_alias'])
def test_verified_negative_never_crosses_source_anchor_or_canonical_boundary(inputs,fault):
    from app.domain.strategy_prices import prepare_condition_prices
    market,day,symbols,token,generation=inputs;symbol=symbols[0]
    rows=deepcopy(market.data[symbol]);actions=deepcopy(market.events[symbol]);actions.append(deepcopy(actions[0]))
    if fault=='date_alias':rows[-2]['date']+='T00:00:00'
    context=prepare_condition_prices(rows,actions,market.days,day,'forward',instrument_id=symbol,
        actions_coverage={'known':True,'source_token':token,'snapshot_id':generation[0]})
    key=basis.key_for(symbol,day,generation,token)
    basis.mark_verified_unavailable(context,key if fault!='missing_token' else None)
    if fault in ('date_alias','missing_token'):
        assert '_verified_preparation_source' not in context
        assert not basis.remember(key,context)
    else:
        assert basis.remember(key,context)
        if fault=='source':
            different=compact_source_token(('changed-source',))
            changed=basis.key_for(symbol,day,(generation[0],different['verified_closure_sha256']),different)
        else:changed=basis.key_for(symbol,market.days[-2],generation,token)
        assert basis.restore(changed) is None
    assert not basis.remember((),context)
