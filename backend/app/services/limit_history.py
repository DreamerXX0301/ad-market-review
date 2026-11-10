"""Verified dated Fuyao up/down pools and per-security annual coverage.

No price-limit rate or current ST flag is used here. A positive requires an
identified source event at the requested day's raw closing price. A zero-row
down pool additionally requires a verified published same-day zero.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, date, time
import math
import re
from zoneinfo import ZoneInfo

from app.adapters.providers import ProviderError

CHINA = ZoneInfo('Asia/Shanghai')
NORMALIZATION_VERSION = 'explicit-ticker-date-coverage-v4'
EMPTY_LIMITDOWN_REASON = '年度跌停池返回空，未确认该日无跌停；保留覆盖缺口'
ZERO_LIMITDOWN_VERSION = 'published-fuyao-limitdown-zero-v1'
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


def _declares_zero_limitdown(container, coverage_key, quarantine_key):
    coverage = container.get(coverage_key, {}).get('limitdown', {})
    return (container.get('limitdown') == []
            and coverage.get('complete') is True and coverage.get('state', 'complete') == 'complete'
            and not container.get(quarantine_key, {}).get('limitdown')
            and coverage.get('pagination_complete', True) is True
            and not coverage.get('raw_codes') and not coverage.get('rejected_codes')
            and type(coverage.get('declared_total')) is int and coverage['declared_total'] == 0
            and all(type(coverage.get(key, 0)) is int and coverage.get(key, 0) == 0
                    for key in ('observed_rows', 'identified_rows', 'quarantined_rows')))


def verified_primary_limitdown_zeros(market, status, days):
    """Bind explicit Fuyao zero declarations to the verified published reference."""
    from app.services.market_source_proof import verified_price_source

    read = getattr(market, 'reference', None)
    if not callable(read):
        return {}
    try:
        reference = read()
        eligible = set()
        for day in days:
            history = reference.get('pool_history', {}).get(day, {})
            if (history.get('source') != 'fuyao' or history.get('date', day) != day
                    or not _declares_zero_limitdown(history, 'coverage', 'quarantine')):
                continue
            if reference.get('pool_trade_date') == day and (
                reference.get('source', 'fuyao') != 'fuyao'
                or not _declares_zero_limitdown(reference, 'pool_coverage', 'pool_quarantine')
            ):
                continue
            eligible.add(day)
        proof = verified_price_source(market, status) if eligible else None
        if proof is None or market.status().get('snapshot_id') != status.get('snapshot_id'):
            return {}
    except (AttributeError, OSError, TypeError, ValueError):
        return {}
    return {day: {'version': ZERO_LIMITDOWN_VERSION, 'reference_snapshot_id': status['snapshot_id'],
                  'reference_proof_sha256': proof, 'date': day} for day in eligible}


def _zero_reference_matches(value, day):
    return (isinstance(value, dict) and value.get('version') == ZERO_LIMITDOWN_VERSION
            and value.get('date') == day and isinstance(value.get('reference_snapshot_id'), str)
            and bool(value['reference_snapshot_id'])
            and isinstance(value.get('reference_proof_sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', value['reference_proof_sha256']) is not None)


def verified_empty_limitdown(record, day):
    """Recognize only normalized empty records carrying both Fuyao receipts."""
    return (isinstance(record, dict) and record.get('date') == day and record.get('source') == 'fuyao'
            and record.get('pool_kind') == 'limitdown' and record.get('items') == []
            and record.get('complete') is True and record.get('pagination_complete') is True
            and record.get('raw_codes') == [] and not record.get('quarantine')
            and not record.get('rejected_codes')
            and all(type(record.get(key)) is int and record[key] == 0
                    for key in ('declared_total', 'verified_rows', 'ambiguous_code_rows'))
            and _zero_reference_matches(record.get('zero_evidence'), day))


def _successful_empty_down_page(body, day):
    data = body['data']
    page = data['pagination']
    if (type(body.get('code')) is not int or body['code'] != 0 or data['item'] != []
            or any(type(page.get(key)) is not int or page[key] != value
                   for key, value in {'page': 1, 'pages': 0, 'size': 200, 'total': 0}.items())
            or ('has_next' in page and page['has_next'] is not False)
            or ('complete' in page and page['complete'] is not True)):
        return False
    if any(container.get(key, day) != day for container in (body, data) for key in ('date', 'trade_date')):
        return False
    expected_ms = _midnight(day)
    if any(key in container and (type(container[key]) is not int or container[key] != expected_ms)
           for container in (body, data) for key in ('date_ms', 'trade_date_ms')):
        return False
    if data.get('quarantine') or data.get('rejected_codes'):
        return False
    stamp = data.get('timestamp')
    if type(stamp) is not int:
        return False
    try:
        collected = datetime.fromtimestamp(stamp / 1000, CHINA)
    except (OverflowError, OSError, ValueError):
        return False
    return collected >= datetime.combine(date.fromisoformat(day), time(15), CHINA)


def fetch_fuyao_pool(adapter, day, closes, master, *, payload_pages=None, pool_kind='limitup',
                     zero_reference=None):
    """Validate pages; rejected responses remain available for failure evidence."""
    captured=[]
    try:
        return _fetch_fuyao_pool(adapter,day,closes,master,payload_pages=payload_pages,pool_kind=pool_kind,
                                zero_reference=zero_reference,captured=captured)
    except ProviderError as error:
        error.raw_pages=captured
        error.pool_kind=pool_kind
        raise


def _fetch_fuyao_pool(adapter, day, closes, master, *, payload_pages=None, pool_kind='limitup',
                      zero_reference=None, captured=None):
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
    if not records and (pool_kind != 'limitdown' or not closes or len(envelopes) != 1
                        or not _zero_reference_matches(zero_reference, day)
                        or not _successful_empty_down_page(envelopes[0], day)):
        raise ProviderError(EMPTY_LIMITDOWN_REASON if pool_kind == 'limitdown'
                            else label+'返回空，未确认该日无'+spec['label']+'；保留覆盖缺口')
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
    result = {'items':accepted,'date':day,'pool_kind':pool_kind,'complete':not quarantine,'declared_total':total,'verified_rows':len(accepted),'quarantine':quarantine,'source':'fuyao','normalization_version':NORMALIZATION_VERSION,'pagination_complete':True,'raw_codes':sorted(raw_codes),'ambiguous_code_rows':ambiguous,'rejected_codes':[r['code'] for r in quarantine if r.get('code')],'scope':'Fuyao来源'+spec['label']+'池；逐股身份、日期收盘价及分页总量核验','price_check':'目标日未复权收盘价逐股核对'}
    if not records:
        result.update(zero_evidence=dict(zero_reference),
                      price_check='目标日已发布主行情同日来源池完整零，与本次来源空分页交叉核验')
    return result, envelopes


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
