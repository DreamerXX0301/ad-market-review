"""Verified dated Fuyao up/down pools and per-security annual coverage.

No price-limit rate or current ST flag is used here. A positive requires an
identified source event at the requested day's raw closing price; absence needs
complete pagination and unambiguous source codes. Empty history stays unknown.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, date, time
import math
from zoneinfo import ZoneInfo

from app.adapters.providers import ProviderError

CHINA = ZoneInfo('Asia/Shanghai')
NORMALIZATION_VERSION = 'explicit-ticker-date-coverage-v4'
POOL_SPECS = {
    'limitup': {'document_key':'annual_pools', 'coverage_key':'annual_coverage', 'endpoint':'limit-up-pool', 'label':'涨停', 'failure_capability':'year_limitup'},
    'limitdown': {'document_key':'annual_down_pools', 'coverage_key':'annual_down_coverage', 'endpoint':'limit-down-pool', 'label':'跌停', 'failure_capability':'year_limitdown'},
}


def pool_spec(pool_kind='limitup'):
    if pool_kind not in POOL_SPECS:
        raise ValueError('来源池类型仅支持limitup/limitdown')
    return POOL_SPECS[pool_kind]


def _midnight(day):
    return int(datetime.combine(date.fromisoformat(day), time(), CHINA).timestamp()*1000)


def fetch_fuyao_pool(adapter, day, closes, master, *, payload_pages=None, pool_kind='limitup'):
    """Validate pages; rejected responses remain available for failure evidence."""
    captured=[]
    try:
        return _fetch_fuyao_pool(adapter,day,closes,master,payload_pages=payload_pages,pool_kind=pool_kind,captured=captured)
    except ProviderError as error:
        error.raw_pages=captured
        error.pool_kind=pool_kind
        raise


def _fetch_fuyao_pool(adapter, day, closes, master, *, payload_pages=None, pool_kind='limitup', captured=None):
    """Internal normalizer, with a caller-owned list of observed responses."""
    spec = pool_spec(pool_kind); label = '年度'+spec['label']+'池'
    records=[]; envelopes=captured if captured is not None else []; total=None; page=1
    while True:
        if payload_pages is not None and page>len(payload_pages):
            raise ProviderError('已缓存'+label+'响应缺少后续分页')
        body = payload_pages[page-1] if payload_pages is not None else adapter.request(
            '/api/a-share/special-data/'+spec['endpoint'], {'date_ms':_midnight(day),'page':page,'size':200})
        envelopes.append(body)
        if not isinstance(body,dict) or not isinstance(body.get('data'),dict):raise ProviderError(label+'响应结构变化')
        data=body['data']; batch=data.get('item'); pagination=data.get('pagination',{})
        if not isinstance(pagination,dict):raise ProviderError(label+'分页结构变化')
        declared=pagination.get('total')
        if not isinstance(batch,list) or isinstance(declared,bool) or not isinstance(declared,int) or declared<0:
            raise ProviderError(label+'缺少可信分页总量')
        if total is not None and declared!=total:
            raise ProviderError(label+'分页总量变化，未标记完整')
        total=declared;records.extend(batch)
        if len(records)>=total:break
        if not batch or page>=100:raise ProviderError(label+'分页未完成')
        page+=1
    if len(records)!=total or (payload_pages is not None and len(payload_pages)!=page):
        raise ProviderError(label+'分页累计与总量不符')
    if not records:
        raise ProviderError(label+'返回空，未确认该日无'+spec['label']+'；保留覆盖缺口')
    known={r['instrument_id'] for r in master}
    by_code={r.get('code',r['instrument_id'][:6]):r['instrument_id'] for r in master}
    accepted=[];quarantine=[];seen=set();raw_codes=set();ambiguous=0
    for row in records:
        if not isinstance(row,dict):raise ProviderError(label+'成员结构变化')
        symbol=row.get('thscode');price=row.get('last_price');explicit=row.get('ticker')
        raw_code=explicit if isinstance(explicit,str) and len(explicit)==6 and explicit.isdigit() and explicit.isascii() else symbol[:6] if isinstance(symbol,str) and len(symbol)==9 and symbol[:6].isdigit() and symbol[:6].isascii() and symbol[-3:].upper() in ('.SH','.SZ','.BJ') else None
        if raw_code:
            if raw_code in raw_codes:raise ProviderError(label+'原始证券代码重复，拒绝计数')
            if isinstance(symbol,str) and len(symbol)==9 and symbol[:6]!=raw_code:
                raise ProviderError('来源证券代码与ticker冲突')
            raw_codes.add(raw_code)
        else:ambiguous+=1
        source_day=None
        if isinstance(row.get('date'),str):
            try:source_day=date.fromisoformat(row['date']).isoformat()
            except ValueError:pass
        if source_day is not None and source_day!=day:
            quarantine.append({'code':raw_code,'reason':'来源明确日期与目标交易日冲突','source_date':row['date'],'requested_date':day})
            continue
        if not isinstance(symbol,str) and isinstance(row.get('ticker'),str):symbol=by_code.get(row['ticker'])
        if not isinstance(symbol,str) or symbol.upper() not in known or symbol.upper() not in closes:
            quarantine.append({'code':raw_code,'reason':'缺少可核验的历史证券身份或该日报价'})
            continue
        symbol=symbol.upper()
        if symbol in seen:raise ProviderError(label+'存在重复证券，拒绝计数')
        seen.add(symbol)
        if isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or abs(price-closes[symbol])>.011:
            raise ProviderError(label+'价格与目标交易日收盘不符，拒绝该日')
        accepted.append({**row,'thscode':symbol,'instrument_id':symbol,'date':day,'source':'fuyao','pool_kind':pool_kind})
    return {'items':accepted,'date':day,'pool_kind':pool_kind,'complete':not quarantine,'declared_total':total,'verified_rows':len(accepted),'quarantine':quarantine,'source':'fuyao','normalization_version':NORMALIZATION_VERSION,'pagination_complete':True,'raw_codes':sorted(raw_codes),'ambiguous_code_rows':ambiguous,'rejected_codes':[r['code'] for r in quarantine if r.get('code')],'scope':'Fuyao来源'+spec['label']+'池；逐股身份、日期收盘价及分页总量核验','price_check':'目标日未复权收盘价逐股核对'}, envelopes


def annual_coverage(document, calendar, day, instruments=None, pool_kind='limitup'):
    spec=pool_spec(pool_kind)
    expected=[d for d in calendar if d[:4]==day[:4] and d<=day]
    pools=document.get(spec['document_key'],{})
    accepted={d:p for d,p in pools.items() if d in expected and p.get('items') is not None}
    complete=[d for d,p in accepted.items() if p.get('complete')]
    counts=Counter(r['instrument_id'] for p in accepted.values() for r in p['items'])
    first=expected[0] if expected else None
    start_known=bool(first and first[5:7]=='01' and int(first[-2:])<=10)
    full=bool(expected) and len(complete)==len(expected) and start_known
    result={'counts':dict(counts),'covered_days':len(complete),'observed_days':len(accepted),'expected_days':len(expected),'missing_dates':[d for d in expected if d not in complete],'year_start_in_calendar':start_known,'complete':full,'state':'complete' if full else 'partial','basis':'完整已核验来源'+spec['label']+'池逐交易日计数；不完整时仅显示已确认次数下界','scope':'Fuyao来源池覆盖；未把单次“'+spec['label']+'统计”滚动窗口当年内次数'}
    if pool_kind=='limitdown':
        result.update(pool_kind=pool_kind,expected_begin=first,expected_end=expected[-1] if expected else None,observed_begin=min(accepted) if accepted else None,observed_end=max(accepted) if accepted else None,continuous_begin=first if full else None,continuous_end=expected[-1] if full else None,history_scope='仅当前锚定年份内的已留存交易日；上一年及当年未留存日期没有在本任务补齐')
    if instruments is not None:
        checks=[]
        for d in expected:
            p=accepted.get(d,{})
            positive={r['instrument_id'] for r in p.get('items',[])}
            clean=p.get('pagination_complete') is True and p.get('ambiguous_code_rows')==0 and isinstance(p.get('raw_codes'),list)
            checks.append((positive,set(p.get('raw_codes',[])),clean))
        # Every clean page proves absence for all codes except those present in
        # its raw list. Index those exceptions once instead of revisiting every
        # day for every security on each dated page request.
        clean_days=sum(clean for _,_,clean in checks)
        blocked_codes=Counter(code for _,raw,clean in checks if clean for code in raw)
        confirmed_exceptions=Counter(symbol for positive,raw,clean in checks for symbol in positive if not clean or symbol[:6] in raw)
        specific={}
        for instrument in instruments:
            symbol=instrument if isinstance(instrument,str) else instrument['instrument_id'];code=symbol[:6]
            known=clean_days-blocked_codes[code]+confirmed_exceptions[symbol]
            exact=bool(expected) and start_known and known==len(expected)
            specific[symbol]={'covered_days':known,'observed_days':len(accepted),'expected_days':len(expected),'complete':exact,'state':'complete' if exact else 'partial','market_full_days':len(complete),'basis':'本证券逐日来源池覆盖：已核验入池，或完整分页且所有原始代码可判别、该代码未入池；目标自身隔离或无代码歧义时未知','scope':'来源'+spec['label']+'池口径；不是交易所官方全集'}
        result['instrument_coverage']=specific
    return result
