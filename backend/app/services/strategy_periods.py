"""Bounded immutable-generation summaries for optional stock D/W/M clauses."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock

from app.core import data_dir
from app.domain.conditions import DAILY_ONLY_FIELDS, metrics_for, names, normalized_metric_rows

# One complete same-day D/W/M cohort must fit together. 5578 securities need
# 16734 summaries; a 16384-entry LRU would otherwise evict itself during an
# ordered full-market scan even when the quotation generation has not changed.
MAX_SUMMARIES = 32768
# Ordinary summaries carry many indicator fields; structural proofs are small
# and are bounded separately so changing a rule does not evict the same D/W
# calculations from another rule. No histories or user policies are retained.
MAX_STRUCTURE_SUMMARIES = 16384
_cache = OrderedDict()
_higher_lows_cache = OrderedDict()
_boll_cache = OrderedDict()
_lock = RLock()


@dataclass(frozen=True)
class MetricRequest:
    include_higher_lows: bool
    boll_configs: tuple
    boll_keys: tuple


@dataclass(frozen=True)
class MetricPlan:
    default: str
    periods: tuple
    requests: dict


def metric_request(include_higher_lows=False,boll_configs=None):
    from app.analytics.boll_conditions import config_key,validate_config
    configs={config_key(config):validate_config(config) for config in boll_configs or []}
    return MetricRequest(include_higher_lows,tuple(configs.values()),tuple(configs))


def compile_metric_plan(trees,default='D'):
    """Resolve the immutable rule requirements once, before visiting stocks."""
    from app.analytics.boll_conditions import configs_for
    required=set().union(*(names(tree) for tree in trees)) if isinstance(trees,list) else names(trees)
    periods=tuple(sorted(periods_for(trees,default)|{default}))
    configs=configs_for(trees,default)
    # Preserve the established full matched-result contract, including the
    # extra period's structure proof when any clause requests higher lows.
    return MetricPlan(default,periods,{period:metric_request('higher_lows' in required,configs.get(period)) for period in periods})


def clear_metric_cache():
    with _lock:
        for cache in (_cache,_higher_lows_cache,_boll_cache):cache.clear()


def periods_for(node, default='D'):
    """Historical bindings retain their explicitly daily contract."""
    result=set()
    if isinstance(node, list):
        for child in node:result.update(periods_for(child,default))
    elif isinstance(node, dict):
        if 'history_event' in node:
            # Historical "where" is deliberately not a structure scanner.
            # A new daily structure in "then" needs the observation's full
            # strict daily window, rather than the event's compact EMA seed.
            def structure_periods(tree):
                if tree.get('field') in ('higher_lows','boll_signal'):return {'D'}
                return set().union(*(structure_periods(child) for child in tree.get('all',tree.get('any',[]))))
            return structure_periods(node['history_event']['then'])
        if 'field' in node:
            # Identity and entry/day event fields are supplied by the caller's
            # daily evidence, not a second technical-indicator calculation.
            if node['field'] not in DAILY_ONLY_FIELDS:result.add(node.get('period',default))
        else:
            for group in ('all','any'):
                for child in node.get(group,[]):result.update(periods_for(child,default))
    return result


def dependencies_for_period(node,period,default='D'):
    """Keep a period's own warmup requirements; daily MA250 is not 250 months."""
    if isinstance(node,list):
        children=[dependencies_for_period(child,period,default) for child in node]
    elif isinstance(node,dict):
        if 'history_event' in node:
            return node if period=='D' else dependencies_for_period(node['history_event']['then'],period,default)
        if 'field' in node:
            selected='D' if node['field'] in DAILY_ONLY_FIELDS else node.get('period',default)
            return node if selected==period else None
        children=[dependencies_for_period(child,period,default) for group in ('all','any') for child in node.get(group,[])]
    else:return None
    children=[child for child in children if child is not None]
    return {'all':children} if children else None


def add_period_metrics(base,bars,day,calendar,trees,default='D',*,instrument_id=None,generation=None,boll_states_by_period=None,plan=None,ready=None,price_context=None):
    plan=plan or compile_metric_plan(trees,default)
    requested=set(plan.periods)
    # Old definitions without scoped clauses keep their original metrics and
    # compact result contract; no new work is added to their fast path.
    if not any(period!=default for period in requested):return base
    by_period={default:{key:value for key,value in base.items() if key!='_period_metrics'}}
    for period in sorted(requested-{default}):
        request=plan.requests[period]
        if boll_states_by_period is not None:request=metric_request(request.include_higher_lows)
        by_period[period]=(ready or {}).get(period)
        if by_period[period] is None:
            by_period[period]=cached_metrics(bars,day,calendar,period,instrument_id=instrument_id,generation=generation,request=request,price_context=price_context)
        if boll_states_by_period is not None and period in boll_states_by_period:
            by_period[period]['boll_states']=boll_states_by_period[period]
    return {**base,'_period_metrics':by_period}


def _summary_key(day,period,instrument_id,generation,price_context=None):
    from app.analytics.indicators import INDICATOR_VERSION
    key=(str(data_dir()),generation,instrument_id,day,period,INDICATOR_VERSION,'volume-comparison-v1')
    return key+('forward',price_context.get('anchor_date'),price_context.get('cache_key')) if price_context and price_context.get('requested_price_basis')=='forward' else key


def _get(cache,key):
    value=cache.get(key)
    if value is not None:cache.move_to_end(key)
    return value


def _put(cache,key,value,capacity):
    cache[key]=deepcopy(value);cache.move_to_end(key)
    while len(cache)>capacity:cache.popitem(last=False)
    # A single execution can live in the API process or in the worker. Both
    # consume the same bounded, verified technical store instead of losing all
    # ordinary/structure reuse at that process boundary.
    from app.services import condition_proof_cache as disk
    if cache is _cache:disk.store(key,'ordinary',value)
    elif cache is _higher_lows_cache:disk.store(key[:-1],'higher_lows',value,key[-1])
    elif cache is _boll_cache:disk.store(key[:-2],'boll',value,key[-2:])


def _saved_parts(key,request):
    from app.analytics.higher_lows import VERSION as higher_lows_version
    from app.analytics.boll_conditions import VERSION as boll_version
    with _lock:
        ordinary=_get(_cache,key)
        higher=_get(_higher_lows_cache,key+(higher_lows_version,)) if request.include_higher_lows else None
        bands={config:_get(_boll_cache,key+(boll_version,config)) for config in request.boll_keys}
    from app.services import condition_proof_cache as disk
    wanted=[(_cache,key,'ordinary',None,ordinary,MAX_SUMMARIES)]
    if request.include_higher_lows:wanted.append((_higher_lows_cache,key+(higher_lows_version,),'higher_lows',higher_lows_version,higher,MAX_STRUCTURE_SUMMARIES))
    wanted.extend((_boll_cache,key+(boll_version,config),'boll',(boll_version,config),value,MAX_STRUCTURE_SUMMARIES)
                  for config,value in bands.items())
    for cache,stored_key,kind,parameter,existing,capacity in wanted:
        if existing is not None:continue
        value=disk.load(key,kind,parameter)
        if value is None:continue
        with _lock:
            cache[stored_key]=deepcopy(value);cache.move_to_end(stored_key)
            while len(cache)>capacity:cache.popitem(last=False)
        if kind=='ordinary':ordinary=value
        elif kind=='higher_lows':higher=value
        else:bands[parameter[1]]=value
    return ordinary,higher,bands


def _compose(ordinary,higher,bands,request,topics):
    result=deepcopy(ordinary)
    if request.include_higher_lows:result['higher_lows_state']=deepcopy(higher)
    if request.boll_keys:result['boll_states']=deepcopy(bands)
    if topics is not None:result['topic_ids']=topics
    return result


def cached_metrics_if_ready(day,period,*,instrument_id=None,generation=None,topics=None,request=None,price_context=None):
    """Probe small proofs before materializing a security's full EMA seed."""
    if not generation or not instrument_id:return None
    if price_context and price_context.get('requested_price_basis')=='forward' and not price_context.get('cache_key'):return None
    request=request or metric_request()
    ordinary,higher,bands=_saved_parts(_summary_key(day,period,instrument_id,generation,price_context),request)
    if ordinary is None or request.include_higher_lows and higher is None or any(value is None for value in bands.values()):return None
    return _compose(ordinary,higher,bands,request,topics)


def ordinary_metrics_ready(day,period,*,instrument_id=None,generation=None,price_context=None):
    """A small probe only; callers must tolerate eviction before composition."""
    if not generation or not instrument_id:return False
    if price_context and price_context.get('requested_price_basis')=='forward' and not price_context.get('cache_key'):return False
    with _lock:return _summary_key(day,period,instrument_id,generation,price_context) in _cache


def complete_from_basis(day,calendar,period,*,instrument_id,generation,request,price_context):
    """Add a new structure to an existing ordinary proof using private inputs.

    A structure-only quotation projection has no raw source-facts lane and is
    never valid for rebuilding ordinary indicators or historical events. Hold
    the already fetched ordinary value across computation; concurrent LRU
    eviction can then only force a future miss, not change this result.
    """
    if not generation or not instrument_id:return None
    if (not price_context or price_context.get('requested_price_basis')!='forward'
            or price_context.get('price_basis')!='forward_event_affine'
            or price_context.get('anchor_date')!=day or not price_context.get('cache_key')):return None
    key=_summary_key(day,period,instrument_id,generation,price_context)
    ordinary,higher,bands=_saved_parts(key,request)
    if ordinary is None:return None
    missing={config for config,value in bands.items() if value is None}
    configs=[config for config,identifier in zip(request.boll_configs,request.boll_keys) if identifier in missing]
    need_higher=request.include_higher_lows and higher is None
    if need_higher or configs:
        from app.analytics.higher_lows import VERSION as higher_version,latest
        from app.analytics.boll_conditions import VERSION as boll_version,latest_states
        rows=normalized_metric_rows(price_context.get('price_rows',[]),day)
        if need_higher:higher=latest(rows,calendar,day,period)
        if configs:bands.update(latest_states(rows,calendar,day,period,configs))
        with _lock:
            if need_higher:_put(_higher_lows_cache,key+(higher_version,),higher,MAX_STRUCTURE_SUMMARIES)
            for identifier in missing:_put(_boll_cache,key+(boll_version,identifier),bands[identifier],MAX_STRUCTURE_SUMMARIES)
    return _compose(ordinary,higher,bands,request,None)


def cached_metrics(bars,day,calendar,period,*,instrument_id=None,generation=None,topics=None,include_higher_lows=False,boll_configs=None,request=None,price_context=None):
    """Compose independently cached indicator/structure/configuration proofs.

    The fixed generation binds prices, calendar and action coverage. Unknown
    proofs belong to that same identity; caller identity/topic mutations are
    made only on a deep copy. Full original seeds remain required on misses.
    """
    from app.analytics.higher_lows import VERSION as higher_lows_version,latest as higher_lows_latest
    from app.analytics.boll_conditions import VERSION as boll_version,latest_states
    request=request or metric_request(include_higher_lows,boll_configs)
    usable=bool(generation and instrument_id)
    if price_context and price_context.get('requested_price_basis')=='forward' and not price_context.get('cache_key'):usable=False
    if not usable:
        return metrics_for(bars,day,calendar,period,topics=topics,include_higher_lows=request.include_higher_lows,boll_configs=request.boll_configs,price_context=price_context)
    key=_summary_key(day,period,instrument_id,generation,price_context)
    ordinary,higher,bands=_saved_parts(key,request)
    need_ordinary=ordinary is None
    missing_keys={config_id for config_id,value in bands.items() if value is None}
    missing_configs=[config for config,config_id in zip(request.boll_configs,request.boll_keys) if config_id in missing_keys]
    need_higher=request.include_higher_lows and higher is None
    if ordinary is None:
        computed=metrics_for(bars,day,calendar,period,include_higher_lows=need_higher,boll_configs=missing_configs,price_context=price_context)
        ordinary={field:value for field,value in computed.items() if field not in ('higher_lows_state','boll_states')}
        if need_higher:higher=computed['higher_lows_state']
        bands.update(computed.get('boll_states',{}))
    elif need_higher or missing_configs:
        # Normalize exactly as metrics_for does, before its legacy missing-
        # volume adaptation. A new structure must keep missing/duplicate/action
        # blockers even when an ordinary indicator summary is already cached.
        basis_rows=price_context.get('price_rows',[]) if price_context and price_context.get('requested_price_basis')=='forward' else bars
        rows=normalized_metric_rows(basis_rows,day)
        if need_higher:higher=higher_lows_latest(rows,calendar,day,period)
        if missing_configs:bands.update(latest_states(rows,calendar,day,period,missing_configs))
    with _lock:
        if need_ordinary:_put(_cache,key,ordinary,MAX_SUMMARIES)
        if need_higher:_put(_higher_lows_cache,key+(higher_lows_version,),higher,MAX_STRUCTURE_SUMMARIES)
        for config_id in missing_keys:_put(_boll_cache,key+(boll_version,config_id),bands[config_id],MAX_STRUCTURE_SUMMARIES)
    return _compose(ordinary,higher,bands,request,topics)
