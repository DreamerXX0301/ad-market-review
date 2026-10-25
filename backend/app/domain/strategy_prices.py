"""Causal, explicit-anchor strategy prices; no database or source writes."""
from datetime import date
from hashlib import sha256
import json
from math import isfinite

VERSION = 'strategy-local-forward-v1'


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()


def _positive(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value) and value>0


def compact_source_token(source_token):
    """Hash a verified closure once per caller cohort, never once per price row."""
    if source_token is None:return None
    if isinstance(source_token,dict) and isinstance(source_token.get('verified_closure_sha256'),str):return source_token
    return {'verified_closure_sha256':_digest(source_token)}


def _action_date(action):
    raw=action.get('date',action.get('ex_date'))
    if raw is not None:return date.fromisoformat(str(raw)).isoformat()
    from app.analytics.market import date_ms
    return date_ms(action['ex_date_ms'])


def price_cache_key(instrument_id,as_of,price_basis,source_token):
    """A verified closure covers prices, actions and calendar without rehashing rows."""
    proof=compact_source_token(source_token)
    return _digest([VERSION,instrument_id,as_of,price_basis,proof['verified_closure_sha256']]) if proof is not None else None


def prepare_condition_prices(raw_bars,actions,calendar,as_of,price_basis='none',*,instrument_id=None,actions_coverage=None):
    if price_basis not in ('none','forward'):raise ValueError('策略价格口径仅支持none/forward')
    raw=sorted((dict(row) for row in raw_bars if isinstance(row.get('date',row.get('trade_date')),str)
                and row.get('date',row.get('trade_date'))<=as_of),key=lambda row:row.get('date',row.get('trade_date')))
    coverage=actions_coverage if isinstance(actions_coverage,dict) else {'known':actions_coverage is True}
    source_proof=compact_source_token(coverage.get('source_token'))
    context={'requested_price_basis':price_basis,'price_basis':'none' if price_basis=='none' else 'unavailable',
             'anchor_date':as_of,'raw_rows':raw,'price_rows':raw if price_basis=='none' else [],
             'adjustment':{'version':VERSION,'anchor':as_of,'usable':price_basis=='none','events':[],
                           'events_count':0,'quarantined':[],'source_snapshot_id':coverage.get('snapshot_id')},
             'cache_key':price_cache_key(instrument_id,as_of,price_basis,source_proof)}
    if price_basis=='none':return context
    errors=[];positions={day:index for index,day in enumerate(calendar)}
    if list(calendar)!=sorted(set(calendar)):errors.append({'date':None,'reason':'市场交易日历重复或未按日期排序'})
    if as_of not in positions:errors.append({'date':as_of,'reason':'观察日不在已核验市场交易日历'})
    if coverage.get('known') is not True:errors.append({'date':None,'reason':coverage.get('reason') or '公司行动来源覆盖或固定来源SHA未核验'})
    if not isinstance(actions,list):errors.append({'date':None,'reason':'本证券公司行动明细未留存，不能把缺失当作无行动'});actions=[]
    if not raw:errors.append({'date':None,'reason':'锚定日前没有原始报价'})
    if len({row.get('date',row.get('trade_date')) for row in raw})!=len(raw):
        errors.append({'date':None,'reason':'同一市场交易日存在重复原始报价'})
    for row in raw:
        day=row.get('date',row.get('trade_date'));row['date']=day
        if day not in positions:
            errors.append({'date':day,'reason':'原始报价日期不在已核验市场交易日历'});break
        if row.get('corporate_action_coverage_known') is not True or not isinstance(row.get('corporate_action'),bool):
            errors.append({'date':day,'reason':'原始报价的公司行动覆盖未知'});break
        symbol=row.get('instrument_id',row.get('thscode'))
        if instrument_id and symbol and str(symbol).upper()!=instrument_id.upper():
            errors.append({'date':day,'reason':'原始报价证券身份与请求不一致'});break
    seen=set();dated=[]
    for source in actions:
        try:
            if not isinstance(source,dict):raise ValueError('公司行动记录格式无效')
            day=_action_date(source)
            if day>as_of:continue
            symbol=source.get('thscode',source.get('instrument_id'))
            if instrument_id and symbol and str(symbol).upper()!=instrument_id.upper():raise ValueError('公司行动证券身份与请求不一致')
            if day in seen:raise ValueError('同除权日多条事件，不能擅自合并')
            seen.add(day)
            if raw and day>raw[0]['date'] and day not in positions:raise ValueError('除权日不在已核验市场交易日历')
            dated.append({**source,'date':day})
        except (ValueError,TypeError,KeyError,OverflowError,OSError) as error:
            errors.append({'date':source.get('date') if isinstance(source,dict) else None,'reason':str(error)})
    action_days={item['date'] for item in dated}
    if any(row.get('corporate_action') is True and row['date'] not in action_days for row in raw):
        errors.append({'date':next(row['date'] for row in raw if row.get('corporate_action') is True and row['date'] not in action_days),
                       'reason':'原始行情标记公司行动，但对应行动明细缺失'})
    rows_by_date={row['date']:row for row in raw}
    if any(day in rows_by_date and not rows_by_date[day].get('corporate_action') for day in action_days):
        errors.append({'date':next(day for day in action_days if day in rows_by_date and not rows_by_date[day].get('corporate_action')),
                       'reason':'公司行动明细与原始行情行动标记不一致'})
    if errors:
        context['adjustment'].update(quarantined=errors[:8],reason=errors[0]['reason'])
        return context
    from app.analytics.adjustments import continuous_prices
    try:prices,adjustment=continuous_prices(raw,dated,as_of,calendar)
    except (ValueError,TypeError,KeyError,ZeroDivisionError,OverflowError) as error:
        context['adjustment'].update(quarantined=[{'date':None,'reason':str(error)}],reason=str(error));return context
    adjustment={**adjustment,'version':VERSION,'source_adjustment_version':adjustment.get('version'),
                'source_snapshot_id':coverage.get('snapshot_id'),
                'source_proof_sha256':source_proof['verified_closure_sha256'] if source_proof else None}
    if not adjustment.get('usable'):
        context['adjustment']=adjustment;return context
    for row,source in zip(prices,raw):
        row.update(corporate_action_original=source['corporate_action'],corporate_action_coverage_known=True,
                   raw_open=source['open'],raw_high=source['high'],raw_low=source['low'],raw_close=source['close'],
                   raw_volume=source.get('volume'),raw_amount=source.get('amount'))
    context.update(price_basis='forward_event_affine',price_rows=prices,adjustment=adjustment)
    return context


def condition_prices_for(market,symbol,raw_bars,calendar,as_of,price_basis):
    """Read-only source adapter. Call outside SQLite transactions."""
    if price_basis=='none':return prepare_condition_prices(raw_bars,[],calendar,as_of,'none',instrument_id=symbol)
    status=getattr(market,'status',None);before=status().get('snapshot_id') if callable(status) else None
    verify=getattr(market,'verify_condition_sources',None)
    token=verify() if callable(verify) else None
    method=getattr(market,'actions_for_instruments',None)
    action_map=method([symbol]) if callable(method) else None
    snapshot=status().get('snapshot_id') if callable(status) else None
    known=before==snapshot and token is not None and isinstance(action_map,dict) and symbol in action_map and isinstance(action_map[symbol],list)
    if before!=snapshot:token=None
    return prepare_condition_prices(raw_bars,action_map.get(symbol) if isinstance(action_map,dict) else None,
        calendar,as_of,price_basis,instrument_id=symbol,
        actions_coverage={'known':known,'source_token':token,'snapshot_id':snapshot})


def project_reference_price(raw_price,reference_date,price_context):
    """Project an already frozen raw reference; never replace it with a new quote."""
    context=price_context or {};anchor=context.get('anchor_date');basis=context.get('price_basis')
    proof={'state':'unknown','reason':None,'anchor_date':anchor,'reference_date':reference_date,
           'price_basis':basis,'requested_price_basis':context.get('requested_price_basis'),'contract':VERSION}
    if not _positive(raw_price) or not isinstance(reference_date,str) or not isinstance(anchor,str) or reference_date>anchor:
        proof['reason']='冻结原始参照价或参照日期无效';return None,proof
    if context.get('requested_price_basis')=='forward' and basis!='forward_event_affine':
        proof['reason']=context.get('adjustment',{}).get('reason') or '本地前复权证据不可用';return None,proof
    events=[item for item in context.get('adjustment',{}).get('events',[]) if reference_date<item['date']<=anchor]
    if context.get('requested_price_basis')=='none':
        if any(reference_date<row.get('date','')<=anchor and row.get('corporate_action') for row in context.get('raw_rows',[])):
            proof['reason']='参照日至观察日存在未转换的公司行动';return None,proof
        proof.update(state='ready');return float(raw_price),proof
    value=float(raw_price)
    for event in events:value=(value-event['cash']+event['rights_price']*event['rights'])/(1+event['bonus']+event['rights'])
    if not _positive(value):proof['reason']='冻结参照价变换后非正或无效';return None,proof
    proof.update(state='ready',events_count=len(events));return value,proof
