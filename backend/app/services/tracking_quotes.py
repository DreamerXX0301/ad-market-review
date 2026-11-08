"""Small immutable quote cache for tracking and watchlist browsing.

Reading two exact market sessions in one batch supplies the displayed close
and daily return. The watchlist can also reuse compact corporate-action dates
to check price comparability against an independently frozen personal baseline.
"""
from collections import OrderedDict, deque
from datetime import date as date_type
import math
from pathlib import Path
from threading import RLock

from app.domain.conditions import metrics_for
from app.services.market_source_proof import verified_price_source

_cache = OrderedDict()
_lock = RLock()
MAX_QUOTES = 4096
MAX_CACHED_ACTION_DATES = 128
CACHE_METHOD = 'exact-calendar-quotes-v2'


def _valid_day(value):
    try:
        return isinstance(value, str) and date_type.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def action_dates_for(batch, symbol):
    evidence = batch.get(symbol) if isinstance(batch, dict) else None
    if not isinstance(evidence, list) or any(
        not isinstance(action, dict) or not _valid_day(action.get('date'))
        for action in evidence
    ):
        return None
    return tuple(action['date'] for action in evidence)


def clear_tracking_quote_cache():
    with _lock:
        _cache.clear()


def tracking_quotes(market, rounds, calendar, day, *, strict_close=False):
    pairs = {(row['instrument_id'], row.get('end_business_date') if row['status'] != 'tracking' else day)
             for row in rounds}
    return _browse_quotes(market, pairs, calendar, day, strict_close=strict_close)


def watchlist_quotes(market, symbols, calendar, day, *, require_current=None):
    return _browse_quotes(market, {(symbol, day) for symbol in symbols}, calendar, day,
                          strict_close=True, include_action_dates=True, require_current=require_current)


def _browse_quotes(market, pairs, calendar, day, *, strict_close, include_action_dates=False,
                   require_current=None):
    if not pairs:
        if require_current is not None:
            require_current()
        return {}
    status = market.status() if callable(getattr(market, 'status', None)) else {}
    generation = status.get('snapshot_id')
    folder = getattr(market, 'data_dir', None)
    namespace = str(Path(folder).resolve()) if folder else None
    cacheable = bool(generation and namespace)
    source_sha = None
    if include_action_dates and cacheable:
        try:
            source_sha = verified_price_source(market, status)
        except (OSError, ValueError):
            if require_current is not None:
                require_current()
            return {pair: {'close': None, 'change_pct': None, 'action_dates': None,
                           'source_reason': '已发布行情来源文件未通过核验，报价保持未知'} for pair in pairs}
        cacheable = source_sha is not None
    scope = (namespace, generation, CACHE_METHOD, strict_close, include_action_dates, source_sha)
    positions = {date: index for index, date in enumerate(calendar)}
    output = {}
    missing = []
    entries = deque(maxlen=MAX_QUOTES)
    for symbol, date in pairs:
        position = positions.get(date)
        previous = calendar[position-1] if position is not None and position > 0 else None
        key = (*scope, symbol, date, previous)
        with _lock:
            saved = _cache.get(key) if cacheable and position is not None else None
            if saved is not None:
                _cache.move_to_end(key)
        if saved is not None:
            output[(symbol, date)] = saved.copy()
        else:
            missing.append((symbol, date, previous, key))
    if missing:
        days = [date for _, date, _, _ in missing if date in positions]
        start = min((calendar[max(0, positions[date]-1)] for date in days), default=day)
        end = max(days, default=day)
        symbols = sorted({symbol for symbol, _, _, _ in missing})
        bulk = callable(getattr(market, 'history_for_instruments', None))
        selected = market.history_for_instruments(symbols, start=start, end=end) if bulk else {}
        # Unlike actions(), the batch evidence API explicitly returns None
        # when the action artifact is missing. An empty list alone is no proof.
        action_reader = getattr(market, 'actions_for_instruments', None)
        action_batch = action_reader(symbols) if (bulk or include_action_dates) and callable(action_reader) else None
        for symbol, date, previous, key in missing:
            action_dates = action_dates_for(action_batch, symbol)
            if bulk:
                bars = [dict(row) for row in selected.get(symbol, []) if row.get('date') in (date, previous)]
                for row in bars:
                    row['corporate_action_coverage_known'] = action_dates is not None
                    if action_dates is not None:
                        row['corporate_action'] = row['date'] in action_dates
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
            if bulk and action_dates is None:
                quote['change_pct'] = None
            if include_action_dates:
                quote['action_dates'] = action_dates
            output[(symbol, date)] = quote
            # A caller without a valid market calendar cannot establish a
            # dated immutable result. Never poison a later valid same-generation
            # read with that provisional missing-context result.
            if cacheable and date in positions:
                if include_action_dates and action_dates is not None and len(action_dates) > MAX_CACHED_ACTION_DATES:
                    continue
                entries.append((key, quote.copy()))
    if require_current is not None:
        require_current()
    elif include_action_dates and entries and market.status().get('snapshot_id') != generation:
        entries.clear()
    if entries:
        with _lock:
            for key, quote in entries:
                _cache[key] = quote
                _cache.move_to_end(key)
            while len(_cache) > MAX_QUOTES:
                _cache.popitem(last=False)
    return output
