"""Small immutable quote cache for browsing, never strategy evaluations.

Tracking lists only display a close and its strict daily return. Reading two
exact market sessions in one batch avoids recalculating five years of every
security's MA/MACD/KDJ. Mutable policies, events and memberships stay uncached.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import date as date_type
import math
from pathlib import Path
from threading import RLock

from app.domain.conditions import metrics_for

_cache = OrderedDict()
_lock = RLock()
MAX_QUOTES = 4096
CACHE_METHOD = 'exact-calendar-quotes-v2'


def _valid_day(value):
    try:
        return isinstance(value, str) and date_type.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def clear_tracking_quote_cache():
    with _lock:
        _cache.clear()


def tracking_quotes(market, rounds, calendar, day, *, strict_close=False):
    status = market.status() if callable(getattr(market, 'status', None)) else {}
    generation = status.get('snapshot_id')
    folder = getattr(market, 'data_dir', None)
    namespace = str(Path(folder).resolve()) if folder else None
    cacheable = bool(generation and namespace)
    pairs = {(row['instrument_id'], row.get('end_business_date') if row['status'] != 'tracking' else day)
             for row in rounds}
    positions = {date: index for index, date in enumerate(calendar)}
    output = {}; missing = []
    for pair in pairs:
        key = (namespace, generation, CACHE_METHOD, strict_close, *pair)
        with _lock:
            saved = _cache.get(key) if cacheable and pair[1] in positions else None
            if saved is not None:
                _cache.move_to_end(key)
        if saved is not None:
            output[pair] = deepcopy(saved)
        else:
            missing.append(pair)
    if missing:
        days = [date for _, date in missing if date in positions]
        start = min((calendar[max(0, positions[date]-1)] for date in days), default=day)
        end = max(days, default=day)
        bulk = callable(getattr(market, 'history_for_instruments', None))
        selected = market.history_for_instruments(sorted({symbol for symbol, _ in missing}), start=start, end=end) if bulk else {}
        # Unlike actions(), the batch evidence API explicitly returns None
        # when the action artifact is missing. An empty list alone is no proof.
        action_reader = getattr(market, 'actions_for_instruments', None)
        action_batch = action_reader(sorted({symbol for symbol, _ in missing})) if bulk and callable(action_reader) else None
        for symbol, date in missing:
            position = positions.get(date)
            previous = calendar[position-1] if position is not None and position > 0 else None
            if bulk:
                bars = [dict(row) for row in selected.get(symbol, []) if row.get('date') in (date, previous)]
                evidence = action_batch.get(symbol) if isinstance(action_batch, dict) else None
                actions_known = (isinstance(evidence, list) and
                                 all(isinstance(action, dict) and _valid_day(action.get('date'))
                                     for action in evidence))
                for row in bars:
                    if actions_known:
                        row['corporate_action'] = any(action.get('date') == row['date'] for action in evidence)
                        row['corporate_action_coverage_known'] = True
                    else:
                        row['corporate_action_coverage_known'] = False
            else:
                bars = market.bars(symbol, as_of=date)
                # Fixtures/other readers retain the canonical algorithm; only
                # the real batch reader has the exact two-day raw contract.
            metrics = metrics_for(bars, date, calendar)
            quote = {'close': metrics.get('close'), 'change_pct': metrics.get('change_pct')}
            if strict_close:
                current=[row for row in bars if row.get('date')==date]
                volume=current[0].get('volume') if len(current)==1 else None
                if len(current)!=1 or isinstance(volume,bool) or not isinstance(volume,(int,float)) or not math.isfinite(volume) or volume<=0:
                    quote={'close':None,'change_pct':None}
            if bulk and not actions_known:
                quote['change_pct'] = None
            output[(symbol, date)] = quote
            # A caller without a valid market calendar cannot establish a
            # dated immutable result. Never poison a later valid same-generation
            # read with that provisional missing-context result.
            if cacheable and date in positions:
                with _lock:
                    key = (namespace, generation, CACHE_METHOD, strict_close, symbol, date)
                    _cache[key] = deepcopy(quote); _cache.move_to_end(key)
                    while len(_cache) > MAX_QUOTES:
                        _cache.popitem(last=False)
    return output
