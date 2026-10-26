"""Bounded compressed values for a verified, same-anchor forward history window.

Only immutable quotation/indicator inputs enter this disposable cache. Every
caller receives a new HistoryContext, without event decisions, policy or topics.
Source verification belongs to the caller and happens before a cache probe.
"""
from collections import OrderedDict
from copy import deepcopy
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
SHARED_WINDOW_VERSION='forward-history-technical-tail-64-columns-v2'
SHARED_WINDOW_SESSIONS=64
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


def _payload(context,scope):
    price=context.price_context or {}
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
    return {'version':VERSION,'as_of':last,'snapshot_id':context.snapshot_id,'price_context':thin,
             'quotes':quote_map(context.quotes),'raw_quotes':quote_map(context.raw_quotes),'daily':daily,
             **{field:[day for day in getattr(context,field) if first<=day<=last]
                for field in ('invalid_dates','action_dates','unknown_action_dates')}}


def remember(key,context,scope):
    """Capture a compact pristine input window before attaching source pools."""
    global _bytes
    price=context.price_context or {}
    if key is None or context.price_basis!='forward_event_affine' or price.get('anchor_date')!=context.as_of:return
    payload=_payload(context,scope)
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


def shared_window_scope(calendar,day,requested_scope):
    """A fixed technical tail covers ordinary edits without binding a rule.

    Longer requested windows retain the original path instead of enlarging
    every security's shared record. Full original seeds still feed the tail.
    """
    from app.domain.conditions import FIELDS
    from app.domain.history_events import supported_field
    if day not in calendar:return None
    position=calendar.index(day);first=calendar[max(0,position-SHARED_WINDOW_SESSIONS+1)]
    if requested_scope['first']<first:return None
    fields=tuple(sorted(field for field in FIELDS if supported_field(field)))
    signature=sha256(json.dumps([SHARED_WINDOW_VERSION,first,fields,list(calendar)],
        separators=(',',':')).encode()).hexdigest()
    return {'first':first,'fields':fields,'signature':signature}


def _shared_identity(key):
    if (not isinstance(key,tuple) or len(key)!=11 or key[0]!=str(data_dir())
            or key[6:]!=(VERSION,HISTORY_VERSION,ANY_VERSION,PRICE_VERSION,INDICATOR_VERSION)):
        raise ValueError('shared history key')
    return sha256(json.dumps([SHARED_WINDOW_VERSION,key],separators=(',',':')).encode()).hexdigest()


def _shared_meta(scope):
    return {'first':scope['first'],'fields':list(scope['fields']),'signature':scope['signature']}


def _complete_shared_input(value,scope,calendar,day):
    expected={observed for observed in calendar if scope['first']<=observed<=day}
    quotes=value['raw_quotes'];daily=value['daily'];invalid=set(value['invalid_dates'])
    if not isinstance(quotes,dict) or not isinstance(daily,dict):return False
    # A smaller exact-window context must never be relabelled as a wider one.
    if not expected.issubset(set(quotes)|invalid):return False
    if not (expected-invalid).issubset(daily):return False
    fields=set(scope['fields'])
    return all(isinstance(metrics,dict) and fields.issubset(metrics)
               for observed,metrics in daily.items() if observed in expected)


def _encode_shared_input(value):
    """Pool exact typed columns, including duplicated quote/fact lanes.

    Daily proof dictionaries have more fields than source quotations. Split
    them into bounded tables instead of widening the quotation codec's caps.
    """
    from app.services import condition_basis_cache as disk
    columns=disk._Columns();tables={}
    for lane in ('quotes','raw_quotes'):
        items=sorted(value[lane].items())
        fields=sorted({'_cache_day'}|{field for _,row in items
                      for field in (row if isinstance(row,dict) else ('_cache_none',))})
        def quote_column(field):
            if field=='_cache_day':
                return [row.get(field,day) if isinstance(row,dict) else day for day,row in items]
            if field=='_cache_none':
                return [row.get(field,disk._MISSING) if isinstance(row,dict) else True for _,row in items]
            return [row.get(field,disk._MISSING) if isinstance(row,dict) else disk._MISSING for _,row in items]
        tables[lane]=columns.table_from_columns(fields,(quote_column(field) for field in fields),len(items))
    daily=value['daily'];items=sorted(daily.items())
    fields=sorted({field for metrics in daily.values() for field in metrics})
    if len(fields)>disk.MAX_FIELDS*4:raise ValueError('shared daily field quota')
    blocks=[]
    for offset in range(0,len(fields),disk.MAX_FIELDS-1):
        selected=fields[offset:offset+disk.MAX_FIELDS-1]
        block_fields=sorted({'_cache_day',*selected})
        def daily_column(field):
            if field=='_cache_day':
                return [metrics.get(field,day) if field in selected else day for day,metrics in items]
            return [metrics.get(field,disk._MISSING) for _,metrics in items]
        blocks.append(columns.table_from_columns(block_fields,
            (daily_column(field) for field in block_fields),len(items)))
    if len(columns.columns)>disk.MAX_FIELDS*4:raise ValueError('shared column quota')
    return {'encoding':'technical-columns-v1','metadata':{key:item for key,item in value.items()
            if key not in ('quotes','raw_quotes','daily')},'tables':tables,'daily_blocks':blocks,'columns':columns.columns}


def _decode_shared_input(encoded):
    from app.services import condition_basis_cache as disk
    if not isinstance(encoded,dict) or encoded.get('encoding')!='technical-columns-v1':
        raise ValueError('shared encoding contract')
    columns=disk._ReadColumns(encoded['columns']);value=dict(encoded['metadata'])
    for lane in ('quotes','raw_quotes'):
        result={}
        for row in columns.table(encoded['tables'][lane]):
            day=row.pop('_cache_day')
            if not isinstance(day,str) or day in result:raise ValueError('shared quote date')
            result[day]=None if row.pop('_cache_none',False) else row
        value[lane]=result
    blocks=encoded['daily_blocks']
    if not isinstance(blocks,list) or len(blocks)>5:raise ValueError('shared daily block quota')
    daily={};dates=None;seen_fields=set()
    for table in blocks:
        records={}
        block_fields=set(table['fields'])-{'_cache_day'}
        if seen_fields&block_fields:raise ValueError('shared duplicate daily field')
        seen_fields.update(block_fields)
        for row in columns.table(table):
            day=row.pop('_cache_day')
            if not isinstance(day,str) or day in records:raise ValueError('shared daily date')
            records[day]=row
        if dates is not None and set(records)!=dates:raise ValueError('shared daily date alignment')
        dates=set(records)
        for day,metrics in records.items():daily.setdefault(day,{}).update(metrics)
    value['daily']=daily
    return value


def remember_shared(key,context,scope):
    """Persist pure daily technical inputs; never retain closing-pool proofs."""
    from app.services import condition_basis_cache as disk
    import sqlite3
    try:
        price=context.price_context or {}
        if (key is None or context.price_basis!='forward_event_affine'
                or price.get('anchor_date')!=context.as_of or price.get('cache_key')!=key[4]):return False
        identity=_shared_identity(key)
        value=_payload(context,scope)
        if key[5]!=scope['signature'] or not _complete_shared_input(value,scope,context.calendar,context.as_of):return False
        payload={'identity':list(key),'scope':_shared_meta(scope),'input':_encode_shared_input(value)}
        plain=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        if len(plain)>MAX_DECOMPRESSED:return False
        compressed=zlib.compress(plain,3)
        if len(compressed)>disk.MAX_COMPRESSED:return False
        disk._put_disk(identity,(compressed,sha256(plain).hexdigest(),len(plain)))
        return True
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError,sqlite3.Error,zlib.error):
        return False


def project_context(context,scope,calendar,positions):
    """Return exactly the requested view of a wider pristine technical tail."""
    value=deepcopy(_payload(context,scope))
    projected=object.__new__(HistoryContext)
    projected.calendar=list(calendar);projected.positions=dict(positions)
    projected.as_of=value['as_of'];projected.snapshot_id=value['snapshot_id']
    projected.price_context=value['price_context'];projected.price_basis='forward_event_affine'
    for field in ('quotes','raw_quotes','daily','invalid_dates','action_dates','unknown_action_dates'):
        setattr(projected,field,value[field])
    projected.event_cache={}
    return projected


def restore_shared(key,requested_scope,calendar,positions):
    """Read a verified same-anchor tail, then project current rule inputs."""
    from app.services import condition_basis_cache as disk
    import sqlite3
    try:
        if key is None:return None
        record=disk._get_disk(_shared_identity(key))
        if record is None:return None
        compressed,checksum,size=record
        if len(compressed)>disk.MAX_COMPRESSED or not 0<=size<=MAX_DECOMPRESSED:return None
        decoder=zlib.decompressobj();plain=decoder.decompress(compressed,MAX_DECOMPRESSED+1)
        if (len(plain)!=size or len(plain)>MAX_DECOMPRESSED or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail or sha256(plain).hexdigest()!=checksum):return None
        wrapper=json.loads(plain)
        if not isinstance(wrapper,dict) or wrapper.get('identity')!=list(key):return None
        wider=shared_window_scope(calendar,key[3],requested_scope)
        if wider is None or key[5]!=wider['signature'] or wrapper.get('scope')!=_shared_meta(wider):return None
        value=_decode_shared_input(wrapper['input'])
        if (value['version']!=VERSION or value['as_of']!=key[3] or value['snapshot_id']!=key[1]
                or value['price_context']['anchor_date']!=key[3]
                or value['price_context']['cache_key']!=key[4]
                or value['price_context'].get('requested_price_basis')!='forward'
                or value['price_context'].get('price_basis')!='forward_event_affine'
                or value['price_context'].get('adjustment',{}).get('usable') is not True):return None
        if not _complete_shared_input(value,wider,calendar,key[3]):return None
        context=object.__new__(HistoryContext)
        context.calendar=list(calendar);context.positions=dict(positions)
        context.as_of=value['as_of'];context.snapshot_id=value['snapshot_id']
        context.price_context=value['price_context'];context.price_basis='forward_event_affine'
        for field in ('quotes','raw_quotes','daily','invalid_dates','action_dates','unknown_action_dates'):
            setattr(context,field,value[field])
        context.event_cache={}
        return project_context(context,requested_scope,calendar,positions)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError,sqlite3.Error,zlib.error):
        return None
