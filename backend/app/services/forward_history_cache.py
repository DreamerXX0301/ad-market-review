"""Bounded compressed values for a verified, same-anchor forward history window.

Only immutable quotation/indicator inputs enter this disposable cache. Every
caller receives a new HistoryContext, without event decisions, policy or topics.
Source verification belongs to the caller and happens before a cache probe.
"""
from collections import OrderedDict
from hashlib import sha256
import json
from threading import RLock
import zlib

from app.core import data_dir
from app.domain.history_events import HistoryContext, VERSION as HISTORY_VERSION, ANY_VERSION, prepared_fields, retain_start
from app.analytics.indicators import INDICATOR_VERSION
from app.domain.strategy_prices import VERSION as PRICE_VERSION

VERSION='forward-history-input-window-v1'
MAX_ENTRIES=16384
MAX_BYTES=64*1024*1024
MAX_DECOMPRESSED=8*1024*1024
_cache=OrderedDict()
_bytes=0
_lock=RLock()
QUOTE_FIELDS=('date','open','high','low','close','volume','amount','corporate_action','corporate_action_coverage_known','price_basis')


def clear_cache():
    global _bytes
    with _lock:_cache.clear();_bytes=0


def cache_stats():
    with _lock:return {'entries':len(_cache),'compressed_bytes':_bytes,'max_entries':MAX_ENTRIES,'max_bytes':MAX_BYTES}


def window_scope(trees,calendar,day):
    first=retain_start(trees,calendar,day);fields=tuple(sorted(prepared_fields(trees)))
    days=[value for value in calendar if first<=value<=day]
    signature=sha256(json.dumps([first,fields,days],separators=(',',':')).encode()).hexdigest()
    return {'first':first,'fields':fields,'signature':signature}


def key_for(symbol,day,snapshot,price_key,scope):
    if not isinstance(price_key,str) or not price_key or not snapshot:return None
    return (str(data_dir()),snapshot,symbol,day,price_key,scope['signature'],
            VERSION,HISTORY_VERSION,ANY_VERSION,PRICE_VERSION,INDICATOR_VERSION)


def _remove(key):
    global _bytes
    previous=_cache.pop(key,None)
    if previous is not None:_bytes-=len(previous[0])


def remember(key,context,scope):
    """Capture a compact pristine input window before attaching source pools."""
    global _bytes
    price=context.price_context or {}
    if key is None or context.price_basis!='forward_event_affine' or price.get('anchor_date')!=context.as_of:return
    fields=set(scope['fields'])
    kept=(fields | {field+suffix for field in fields for suffix in ('_reason','_evidence','_date','_status')}
          | {'observed_date','indicator_warmup_bars','indicator_reset_reason','indicator_warmup_reason','price_basis','adjustment_anchor'})
    if 'close_breakout_high' in fields:kept|={'breakout_valid','breakout_previous_sessions','breakout_blocking_distance','new_high_current_close'}
    first=scope['first'];last=context.as_of
    def quote_map(values):
        return {day:({field:row[field] for field in QUOTE_FIELDS if field in row} if isinstance(row,dict) else None)
                for day,row in values.items() if first<=day<=last}
    adjustment=price.get('adjustment',{})
    thin={field:price.get(field) for field in ('requested_price_basis','price_basis','anchor_date','cache_key')}
    thin['adjustment']={field:adjustment.get(field) for field in ('version','model','usable','reason','events_count','quarantined','source_snapshot_id','source_proof_sha256')}
    daily={day:{field:value for field,value in values.items() if field in kept}
           for day,values in context.daily.items() if first<=day<=last}
    # Closing-pool proofs are refreshed after restore. A source update must
    # never reuse a previously attached true/false pool decision.
    if 'limit_up' in fields:
        for values in daily.values():
            for name in list(values):
                if name.startswith('limit_up_'):values.pop(name)
            values.update(limit_up=None,limit_up_reason='历史涨停尚未核验同日来源收盘池与原始成交报价')
    payload={'version':VERSION,'as_of':last,'snapshot_id':context.snapshot_id,'price_context':thin,
             'quotes':quote_map(context.quotes),'raw_quotes':quote_map(context.raw_quotes),'daily':daily,
             **{field:[day for day in getattr(context,field) if first<=day<=last]
                for field in ('invalid_dates','action_dates','unknown_action_dates')}}
    try:plain=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    except (TypeError,ValueError):return
    if len(plain)>MAX_DECOMPRESSED:return
    compressed=zlib.compress(plain,3)
    if len(compressed)>MAX_BYTES:return
    with _lock:
        _remove(key);_cache[key]=(compressed,sha256(plain).digest());_bytes+=len(compressed)
        while len(_cache)>MAX_ENTRIES or _bytes>MAX_BYTES:
            oldest=next(iter(_cache));_remove(oldest)


def restore(key,calendar,positions):
    if key is None:return None
    with _lock:
        record=_cache.get(key)
        if record is None:return None
        _cache.move_to_end(key)
    try:
        decoder=zlib.decompressobj();plain=decoder.decompress(record[0],MAX_DECOMPRESSED+1)
        if len(plain)>MAX_DECOMPRESSED or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError('cache size/stream')
        if sha256(plain).digest()!=record[1]:raise ValueError('cache checksum')
        value=json.loads(plain)
        if (value['version']!=VERSION or value['as_of']!=key[3] or value['snapshot_id']!=key[1]
                or value['price_context']['anchor_date']!=key[3]
                or value['price_context']['cache_key']!=key[4]):raise ValueError('cache identity')
        context=object.__new__(HistoryContext)
        context.calendar=list(calendar);context.positions=dict(positions)
        context.as_of=value['as_of'];context.snapshot_id=value['snapshot_id']
        context.price_context=value['price_context'];context.price_basis='forward_event_affine'
        for field in ('quotes','raw_quotes','daily','invalid_dates','action_dates','unknown_action_dates'):setattr(context,field,value[field])
        context.event_cache={}
        return context
    except (ValueError,TypeError,KeyError,zlib.error):
        with _lock:
            if _cache.get(key) is record:_remove(key)
        return None
