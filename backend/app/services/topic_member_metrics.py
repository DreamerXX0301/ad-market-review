"""Bounded, read-only historical metrics for current classification members.

Membership is current; historical returns do not imply historical membership.
Source-pool absences and verified positive closing events remain separate facts.
"""
from __future__ import annotations

from datetime import date, timedelta
from collections import OrderedDict
from copy import deepcopy
import math
import hashlib
from pathlib import Path
import threading

from app.analytics.adjustments import continuous_prices
from app.analytics.market import MarketData
from app.core import digest
from app.services.chart_limits import _evidence, _combine
from app.services.limit_history import annual_coverage

BATCH_SIZE = 256
_METRIC_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()


def clear_metric_cache():
    with _CACHE_LOCK: _METRIC_CACHE.clear()


def _cache_key(market, day, symbols, quotes, supplement):
    """Only genuine immutable generations can cache lightweight result rows."""
    if type(market) is not MarketData or len(symbols) > 2048 or not market._fixed_snapshot: return None
    try:
        status=market.status();snapshot=market._fixed_snapshot
        root=Path(market.data_dir).resolve();expected=(root/'published'/snapshot).resolve()
        if status.get('snapshot_id') != snapshot or not expected.is_relative_to((root/'published').resolve()): return None
        if Path(status.get('path','')).resolve()!=expected or not (expected/'manifest.json').is_file(): return None
        ordered=tuple(sorted(symbols))
        quote_inputs=tuple((symbol,str(quotes.get(symbol,{}).get('date',quotes.get(symbol,{}).get('trade_date',''))),_number(quotes.get(symbol,{}).get('close'))) for symbol in ordered)
        return ('topic-member-metrics-v4-unified-positive-lower-bound',str(root),snapshot,day,ordered,quote_inputs,digest(supplement))
    except (ValueError,OSError,TypeError): return None


def _number(value):
    return float(value) if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None


def _date(value):
    if not isinstance(value, str): return None
    try: return date.fromisoformat(value).isoformat()
    except ValueError: return None


def _month_baseline(market, calendar, day):
    """Use the previous month's last session, with an explicit calendar proof.

    A closed natural month-end needs a verified continuous calendar, rather
    than silently treating the latest visible quote as the previous close.
    """
    first = date.fromisoformat(day).replace(day=1)
    previous_end = (first - timedelta(days=1)).isoformat()
    if previous_end in calendar:
        return previous_end, '上月自然月末明确在已完成市场交易日历中'
    method = getattr(market, 'status', None)
    status = method() if callable(method) else {}
    proof = status.get('coverage', {}).get('calendar_provenance', {})
    checked = proof.get('basis') == 'source_derived_cross_checked' and proof.get('rules', {}).get('all_four_indices_equal_bulk') is True and proof.get('rules', {}).get('official_overlap_equal') is True
    original = (proof.get('count') == len(calendar) and proof.get('dates_sha256') == hashlib.sha256('\n'.join(calendar).encode()).hexdigest()
                and proof.get('start') == calendar[0] and proof.get('end') == calendar[-1]) if calendar else False
    extended = proof.get('retained_calendar_count') == len(calendar) and proof.get('retained_calendar_sha256') == digest(calendar)
    prior = [observed for observed in calendar if observed < first.isoformat()]
    if checked and (original or extended) and prior and day in calendar:
        return prior[-1], '完整留存日历跨越月界；日期集合与已核验全市场包及四指数日历依据一致，基准为上月最后交易日（非交易所官方历史声明）'
    return None, None


def _prepared(record, source, path, *, granular=False, public=False):
    indexed = {}
    items = record.get('items')
    if isinstance(items, list):
        for row in items:
            if not isinstance(row, dict): continue
            codes = {str(row.get(field) or '')[:6] for field in ('instrument_id', 'thscode', 'ticker', 'code')}
            for code in codes:
                if len(code) == 6 and code.isascii() and code.isdigit(): indexed.setdefault(code, []).append(row)
    return record, indexed, {'source': source, 'path': path, 'granular': granular, 'allow_absence': not public}


def _special_codes(record, indexed):
    """Only these codes can differ from a source's default absence proof."""
    result = set(indexed)
    for key in ('raw_codes', 'rejected_codes'):
        values = record.get(key)
        for value in values if isinstance(values, (list, tuple, set)) else []:
            if isinstance(value, str) and len(value) >= 6 and value[:6].isascii() and value[:6].isdigit():
                result.add(value[:6])
    for row in record.get('quarantine', []):
        if not isinstance(row, dict): continue
        for field in ('instrument_id', 'thscode', 'ticker', 'code'):
            value = row.get(field)
            if isinstance(value, str) and len(value) >= 6 and value[:6].isascii() and value[:6].isdigit():
                result.add(value[:6])
    return result


def _narrowed(record, indexed, symbol):
    original = record.get('items')
    return {**record, 'items': indexed.get(symbol[:6]) or original[:1]} if isinstance(original, list) and original else record


def _compile_record(record, indexed, options, day, kind):
    special = _special_codes(record, indexed)
    # Pick a demonstrably absent numeric code, so even malformed identities
    # cannot accidentally match the synthetic *proof probe*. No probe becomes
    # a product security or source event; it only compiles a general absence.
    candidate = 0
    while f'{candidate:06d}' in special: candidate += 1
    absent = f'{candidate:06d}.SH'
    return {'record': record, 'indexed': indexed, 'options': options, 'special': special,
            'default': _evidence(_narrowed(record, indexed, absent), absent, day, 1.0, kind=kind, **options),
            'missing': _evidence(record, absent, day, None, kind=kind, **options)}


def _combined_up(up, down):
    if up['status'] == 'conflict' or down['status'] == 'conflict' or (up['state'] is True and down['state'] is True):
        return {**up, 'state': None, 'status': 'conflict', 'reason': '同日涨跌停或来源价格证据冲突'}
    return up


def _compiled_state(kinds, slot):
    return _combined_up(_combine([record[slot] for record in kinds['up']]),
                        _combine([record[slot] for record in kinds['down']]))


def _sources(reference, supplement, calendar):
    result = {}
    for day in calendar:
        kinds = {'up': [], 'down': []}
        historical = reference.get('pool_history', {}).get(day, {})
        for kind, pool in (('up', 'limitup'), ('down', 'limitdown')):
            if pool in historical:
                record = {'date': day, 'items': historical[pool], 'complete': historical.get('coverage', {}).get(pool, {}).get('complete', False), 'quarantine': historical.get('quarantine', {}).get(pool, [])}
                kinds[kind].append(_prepared(record, 'fuyao', 'reference.pool_history.'+day+'.'+pool))
            if reference.get('pool_trade_date') == day and pool in reference:
                record = {'date': day, 'items': reference[pool], 'complete': reference.get('pool_coverage', {}).get(pool, {}).get('complete', False), 'quarantine': reference.get('pool_quarantine', {}).get(pool, [])}
                kinds[kind].append(_prepared(record, 'fuyao', 'reference.'+pool))
            key = 'annual_pools' if kind == 'up' else 'annual_down_pools'
            record = supplement.get(key, {}).get(day)
            if isinstance(record, dict): kinds[kind].append(_prepared(record, record.get('source') or 'fuyao', key+'.'+day, granular=True))
        public = supplement.get('public_pools', {}).get(day)
        if isinstance(public, dict): kinds['up'].append(_prepared(public, public.get('source') or 'eastmoney-public', 'public_pools.'+day, public=True))
        compiled = {kind: [_compile_record(*record, day, kind) for record in records] for kind, records in kinds.items()}
        # Hot-topic candidate/coverage readers consume the original tuple
        # lists. Keep that read-only contract alongside the compiled fast path.
        result[day] = {**kinds, 'kinds': compiled, 'special': set().union(*(record['special'] for records in compiled.values() for record in records)),
                       'default': _compiled_state(compiled, 'default'), 'missing': _compiled_state(compiled, 'missing')}
    return result


def _daily_states(symbol, closes, calendar, sources):
    daily = {}
    for day in calendar:
        prepared = sources[day]; close = closes.get(day); code = symbol[:6]
        # These internal proof dictionaries are read-only throughout the
        # batch. Reuse both the absent and missing-price states: unknown never
        # falls through to the known-absence fast path.
        if close is None:
            daily[day] = prepared['missing']; continue
        if code not in prepared['special']:
            daily[day] = prepared['default']; continue
        states = {}
        for kind in ('up', 'down'):
            proofs = []
            for source in prepared['kinds'][kind]:
                if code not in source['special']:
                    proofs.append(source['default']); continue
                proofs.append(_evidence(_narrowed(source['record'],source['indexed'],symbol), symbol, day, close, kind=kind, **source['options']))
            states[kind] = _combine(proofs)
        daily[day] = _combined_up(states['up'],states['down'])
    return daily


def _return(rows, actions, calendar, start, end, quote, *, baseline_proof=None, listed=None):
    coverage = {'complete': False, 'start': start, 'end': end, 'price_basis': None, 'missing_sessions': None, 'baseline_proof': baseline_proof, 'reason': None}
    def unknown(reason):
        coverage['reason'] = reason
        return None, reason, coverage
    if start is None: return unknown('没有可证明的区间基准交易日，未使用首条可见报价替代')
    if listed and listed > start: return unknown('上市日期晚于区间基准，不能把上市以来涨幅冒充完整区间涨幅')
    if not quote or str(quote.get('date', quote.get('trade_date', '')))[:10] != end or _number(quote.get('close')) is None:
        return unknown('业务日缺少有效报价，不使用上一交易日报价替代')
    selected = [row for row in rows if start <= row['date'] <= end]
    by_date = {row['date']: row for row in selected}
    coverage['missing_sessions'] = sum(day not in by_date for day in calendar if start <= day <= end)
    if start not in by_date or end not in by_date: return unknown('区间起点或终点原始收盘缺失，不移动比较端点')
    if any(_number(by_date[point].get('close')) is None or by_date[point]['close'] <= 0 for point in (start, end)):
        return unknown('区间端点价格无效')
    if abs(by_date[end]['close']-quote['close']) > .011: return unknown('业务日报价与批量历史端点不一致')
    if actions is None: return unknown('未留存可核验公司行动数据，历史价格连续口径未知')
    try:
        adjusted, info = continuous_prices(selected, actions, end, calendar)
    except (ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError):
        return unknown('公司行动或历史价格字段未通过连续口径核验')
    if not info.get('usable'): return unknown(info.get('reason') or '区间存在未核验公司行动')
    aligned = {row['date']: row for row in adjusted}
    basis = 'forward_event_affine' if info.get('events_count') else 'none'
    base = _number(aligned.get(start, {}).get('close')); last = _number(aligned.get(end, {}).get('close'))
    value = (last/base-1)*100 if base is not None and base > 0 and last is not None else None
    if _number(value) is None: return unknown('连续价格计算结果无效')
    coverage.update(complete=True, price_basis=basis, action_events=info.get('events_count', 0))
    return value, None, coverage


def _annual_verified(supplement, symbols, daily, calendar, day):
    """Keep annual_coverage's explicit-code absence semantics, verify positives.

    A missing quote cannot disprove a positive. Its original code stays in the
    source set so removal becomes unknown for that security, never false.
    """
    wanted=set(symbols); pools={}
    for observed, record in supplement.get('annual_pools', {}).items():
        if observed not in calendar or observed[:4] != day[:4] or observed > day or not isinstance(record, dict): continue
        items=record.get('items')
        valid_date=record.get('date') in (None, observed) and record.get('pool_kind') in (None, 'limitup')
        if not isinstance(items,list) or not items or not valid_date:
            pools[observed]={**record,'items':[],'complete':False,'pagination_complete':False}
            continue
        accepted=[]; rejected=set(record.get('rejected_codes', [])); changed=False
        for row in items:
            if not isinstance(row,dict) or not row.get('instrument_id'): changed=True;continue
            symbol=row['instrument_id']
            if symbol in wanted and daily[symbol][observed]['state'] is not True:
                rejected.add(symbol[:6]);changed=True;continue
            accepted.append(row)
        pools[observed]={**record,'items':accepted,'rejected_codes':sorted(rejected),'complete':record.get('complete') is True and not changed}
    return annual_coverage({'annual_pools':pools},calendar,day,instruments=symbols)


def _history_start(calendar, sources, baseline, thirty, month):
    """Read all return endpoints and every retained source-evidence date.

    Before this boundary there is no source record of either direction, so a
    real quote and a missing quote both compile to the same unknown event state.
    Keep the *full* calendar below: unknown/covered-day totals and the search for
    an old last event must not be shortened along with the quote materialization.
    Even empty, malformed or incomplete old records retain their original day;
    this intentionally avoids guessing that a source cannot affect a member.
    """
    needed = [value for value in (baseline, thirty, month) if value is not None]
    evidence_start = next((observed for observed in calendar
                           if any(sources[observed].get(kind) for kind in ('up', 'down'))), None)
    if evidence_start is not None:needed.append(evidence_start)
    return min(needed) if needed else calendar[-1]



def _limit_values(symbol, daily, calendar, day, annual):
    yearly=annual.get('instrument_coverage',{}).get(symbol,{'complete':False,'covered_days':0,'expected_days':sum(observed[:4]==day[:4] for observed in calendar),'observed_days':0,'state':'partial'})
    positives=[observed for observed,state in daily[symbol].items() if state['state'] is True]
    # A verified reference/public closing event is also a confirmed yearly
    # observation. Deduplicate by date across sources, without claiming that
    # those partial pools prove an exact annual total or negative absence.
    observed_count=max(annual['counts'].get(symbol,0),sum(observed[:4]==day[:4] for observed in positives))
    year_reason=None if yearly['complete'] else '年内来源池或本证券事件覆盖不足，仅保留已确认次数下界'
    latest=max(positives) if positives else None
    suffix=[observed for observed in calendar if latest and observed>=latest]
    known=sum(daily[symbol].get(observed,{}).get('state') is not None for observed in suffix)
    latest_complete=bool(latest and known==len(suffix))
    known_history=sum(state['state'] is not None for state in daily[symbol].values())
    latest_coverage={'complete':latest_complete,'state':'confirmed_latest' if latest_complete else 'latest_observed' if latest else 'no_confirmed_event','since':latest or calendar[0],'until':day,'covered_days':known if latest else known_history,'expected_days':len(suffix) if latest else len(calendar),'unknown_days':len(suffix)-known if latest else len(calendar)-known_history}
    return {'limit_up_ytd':observed_count if yearly['complete'] else None,'limit_up_ytd_observed':observed_count,'limit_up_ytd_reason':year_reason,'limit_up_ytd_coverage':yearly,'last_limit_up_date':latest,'sessions_since_limit_up':calendar.index(day)-calendar.index(latest) if latest else None,'last_limit_up_complete':latest_complete,'last_limit_up_reason':None if latest_complete else '最新已确认事件之后仍有未知交易日，不能断言一定最近' if latest else '留存范围内尚无可核验涨停事件，不代表历史从未涨停','last_limit_up_coverage':latest_coverage}


def limit_metrics(market, day, symbols, supplement, reference=None):
    """Same dated source proof as member pages, without return calculations.

    Rankings need all candidates before sorting, so use bounded batches rather
    than fetching one stock summary per row. No source before the first retained
    evidence date can prove a closing event; avoid materializing those prices.
    """
    symbols=sorted(set(symbols))
    if not symbols:return {}
    calendar=sorted({value for value in market.calendar() if _date(value) and value<=day})
    if not calendar or day not in calendar:return {}
    reference=reference if reference is not None else market.reference() if hasattr(market,'reference') else {}
    sources=_sources(reference,supplement,calendar)
    evidence_days=[observed for observed in calendar if sources[observed]['up'] or sources[observed]['down']]
    first=evidence_days[0] if evidence_days else None
    reader=getattr(market,'history_for_instruments',None)
    result={}
    for offset in range(0,len(symbols),BATCH_SIZE):
        batch=symbols[offset:offset+BATCH_SIZE]
        histories=reader(batch,start=first,end=day) if first is not None and callable(reader) else {}
        daily={}
        for symbol in batch:
            closes={};duplicates=set()
            for row in histories.get(symbol,[]):
                observed=_date(row.get('date'));close=_number(row.get('close'))
                if observed is None or observed not in sources:continue
                if observed in closes:duplicates.add(observed)
                closes[observed]=close if close is not None and close>0 else None
            for observed in duplicates:closes[observed]=None
            # Dates with no source can only be unknown, independently of the
            # quote. Keep their count in the full calendar, not a large map
            # repeated for every ranked stock.
            daily[symbol]=_daily_states(symbol,closes,evidence_days,sources)
        annual=_annual_verified(supplement,batch,daily,calendar,day)
        for symbol in batch:result[symbol]=_limit_values(symbol,daily,calendar,day,annual)
    return result


def _batch(market, day, symbols, master, quotes, supplement, calendar, sources, baseline, thirty, month, month_proof):
    histories=market.history_for_instruments(symbols,start=_history_start(calendar,sources,baseline,thirty,month),end=day)
    actions=market.actions_for_instruments(symbols) if callable(getattr(market,'actions_for_instruments',None)) else None
    daily={};cleaned={}
    for symbol in symbols:
        rows=[];closes={};duplicates=set()
        for raw in histories.get(symbol,[]):
            observed=_date(raw.get('date'));close=_number(raw.get('close'))
            if observed is None or observed > day or observed not in sources:continue
            if observed in closes:duplicates.add(observed)
            closes[observed]=close if close is not None and close>0 else None
            rows.append({**raw,'date':observed})
        for observed in duplicates:closes[observed]=None
        cleaned[symbol]=[row for row in rows if row['date'] not in duplicates]
        daily[symbol]=_daily_states(symbol,closes,calendar,sources)
    annual=_annual_verified(supplement,symbols,daily,calendar,day)
    result={}
    for symbol in symbols:
        rows=cleaned[symbol];identity=master.get(symbol,{})
        listed=_date(identity.get('list_date'))
        own_actions=actions.get(symbol) if isinstance(actions,dict) else None
        ytd,ytd_reason,ytd_coverage=_return(rows,own_actions,calendar,baseline,day,quotes.get(symbol),baseline_proof='上一年12月31日明确在已完成市场交易日历中' if baseline else None,listed=listed)
        if baseline is None:
            ytd_reason='无法证明上一年最后已完成交易日；日历未明确留存上一年12月31日，不用年初首条报价替代'
            ytd_coverage['reason']=ytd_reason
        change,change_reason,change_coverage=_return(rows,own_actions,calendar,thirty,day,quotes.get(symbol),baseline_proof='业务日向前精确30个市场交易日' if thirty else None,listed=listed)
        mtd,mtd_reason,mtd_coverage=_return(rows,own_actions,calendar,month,day,quotes.get(symbol),baseline_proof=month_proof,listed=listed)
        if month is None:
            mtd_reason='无法证明上月最后交易日；不使用本月首条报价或最近30个交易日替代本月基准'
            mtd_coverage['reason']=mtd_reason
        result[symbol]={'return_ytd_pct':ytd,'return_ytd_reason':ytd_reason,'return_ytd_coverage':ytd_coverage,'change_30d_pct':change,'change_30d_reason':change_reason,'change_30d_coverage':change_coverage,**_limit_values(symbol,daily,calendar,day,annual)}
        result[symbol].update(return_mtd_pct=mtd,return_mtd_reason=mtd_reason,return_mtd_coverage=mtd_coverage)
    return result


def member_metrics(market, day, symbols, master, quotes, supplement):
    """Return lightweight metrics; at most 256 securities' history live at once."""
    symbols=sorted(set(symbols))
    if not symbols:return {}
    cache_key=_cache_key(market,day,symbols,quotes,supplement)
    if cache_key is not None:
        with _CACHE_LOCK:
            if cache_key in _METRIC_CACHE:
                _METRIC_CACHE.move_to_end(cache_key)
                return deepcopy(_METRIC_CACHE[cache_key])
    full_calendar=sorted({value for value in market.calendar() if _date(value)})
    calendar=[value for value in full_calendar if value<=day]
    month,month_proof=_month_baseline(market,full_calendar,day)
    prior_end=str(int(day[:4])-1)+'-12-31'
    baseline=prior_end if prior_end in calendar else None
    thirty=calendar[calendar.index(day)-30] if day in calendar and calendar.index(day)>=30 else None
    if not calendar or day not in calendar or not callable(getattr(market,'history_for_instruments',None)):
        reason='当前行情对象没有可用的限定证券批量历史或完整业务日日历；不逐股扫描全市场替代'
        return {symbol:{'return_ytd_pct':None,'return_ytd_reason':reason,'return_ytd_coverage':{'complete':False,'start':baseline,'end':day,'price_basis':None,'missing_sessions':None,'baseline_proof':None,'reason':reason},'return_mtd_pct':None,'return_mtd_reason':reason,'return_mtd_coverage':{'complete':False,'start':month,'end':day,'price_basis':None,'missing_sessions':None,'baseline_proof':month_proof,'reason':reason},'change_30d_pct':None,'change_30d_reason':reason,'change_30d_coverage':{'complete':False,'start':thirty,'end':day,'price_basis':None,'missing_sessions':None,'baseline_proof':None,'reason':reason},'limit_up_ytd':None,'limit_up_ytd_observed':0,'limit_up_ytd_reason':reason,'limit_up_ytd_coverage':{'complete':False,'covered_days':0,'expected_days':sum(observed[:4]==day[:4] for observed in calendar),'observed_days':0,'state':'partial','basis':reason,'scope':'仅已留存来源池'},'last_limit_up_date':None,'sessions_since_limit_up':None,'last_limit_up_complete':False,'last_limit_up_reason':reason,'last_limit_up_coverage':{'complete':False,'state':'no_confirmed_event','since':calendar[0] if calendar else None,'until':day,'covered_days':0,'expected_days':len(calendar),'unknown_days':len(calendar)}} for symbol in symbols}
    reference=market.reference() if hasattr(market,'reference') else {}
    sources=_sources(reference,supplement,calendar)
    result={}
    for offset in range(0,len(symbols),BATCH_SIZE):
        result.update(_batch(market,day,symbols[offset:offset+BATCH_SIZE],master,quotes,supplement,calendar,sources,baseline,thirty,month,month_proof))
    if cache_key is not None:
        with _CACHE_LOCK:
            _METRIC_CACHE[cache_key]=deepcopy(result);_METRIC_CACHE.move_to_end(cache_key)
            while len(_METRIC_CACHE)>4:_METRIC_CACHE.popitem(last=False)
    return result
