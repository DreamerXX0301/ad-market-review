"""Bounded immutable-generation summaries for optional stock D/W/M clauses."""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock

from app.core import data_dir
from app.domain.conditions import DAILY_ONLY_FIELDS, metrics_for, names

MAX_SUMMARIES = 16384
_cache = OrderedDict()
_lock = RLock()


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
                if tree.get('field')=='higher_lows':return {'D'}
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


def add_period_metrics(base,bars,day,calendar,trees,default='D',*,instrument_id=None,generation=None):
    requested=periods_for(trees,default)
    # Old definitions without scoped clauses keep their original metrics and
    # compact result contract; no new work is added to their fast path.
    if not any(period!=default for period in requested):return base
    by_period={default:{key:value for key,value in base.items() if key!='_period_metrics'}}
    structured=any('higher_lows' in names(tree) for tree in trees) if isinstance(trees,list) else 'higher_lows' in names(trees)
    for period in sorted(requested-{default}):
        by_period[period]=cached_metrics(bars,day,calendar,period,instrument_id=instrument_id,generation=generation,include_higher_lows=structured)
    return {**base,'_period_metrics':by_period}


def cached_metrics(bars,day,calendar,period,*,instrument_id=None,generation=None,topics=None,include_higher_lows=False):
    """Cache summaries of an immutable market generation, never user policies."""
    key=(str(data_dir()),generation,instrument_id,day,period,include_higher_lows)
    usable=bool(generation and instrument_id)
    with _lock:
        saved=_cache.get(key) if usable else None
        if saved is not None:_cache.move_to_end(key)
    if saved is None:
        saved=metrics_for(bars,day,calendar,period,include_higher_lows=include_higher_lows)
        if usable:
            with _lock:
                _cache[key]=deepcopy(saved);_cache.move_to_end(key)
                while len(_cache)>MAX_SUMMARIES:_cache.popitem(last=False)
    result=deepcopy(saved)
    if topics is not None:result['topic_ids']=topics
    return result
