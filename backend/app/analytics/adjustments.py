"""Explicit-anchor action models, with guarded chart opt-in and raw-price fallback."""
from collections import Counter
from math import isfinite
from .market import date_ms

VERSION='adjustment-research-v1'
MODELS=('theoretical_ratio','event_affine','additive_gap')


def finite_number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value):raise ValueError('公司行动字段不是有限数值')
    return float(value)


def action_plan(bars,actions,anchor,calendar=None):
    rows=sorted((r for r in bars if r['date']<=anchor),key=lambda r:r['date'])
    if not rows:raise ValueError('锚定日前没有行情')
    by_date={r['date']:r for r in rows};all_dates=sorted(by_date)
    dated=[];future=[];quarantined=[];ignored=[]
    for action in actions:
        try:day=action.get('date') or date_ms(action['ex_date_ms'])
        except (TypeError,ValueError,KeyError,OverflowError):quarantined.append({'date':None,'reason':'无效除权日期'});continue
        if day>anchor:future.append(day);continue
        if day<=all_dates[0]:ignored.append(day);continue
        dated.append((day,action))
    duplicates={day for day,count in Counter(d for d,_ in dated).items() if count>1}
    events=[]
    for day,action in sorted(dated,key=lambda item:item[0]):
        if day in duplicates:
            if not any(q.get('date')==day for q in quarantined):quarantined.append({'date':day,'reason':'同除权日多条事件，不能擅自相加或去重'})
            continue
        try:
            cash=finite_number(action['dividend_per_share']);bonus=finite_number(action['per_share_bonus']);rights=finite_number(action['allotment_ratio']);rights_price=finite_number(action['allotment_price'])
            if min(cash,bonus,rights,rights_price)<0:raise ValueError('负现金/送转/配股字段需要独立公司公告核验')
            if action.get('currency','CNY')!='CNY':raise ValueError('币种未经本模型验证')
            before=[d for d in all_dates if d<day]
            if day not in by_date or not before:raise ValueError('除权日或前收盘缺失')
            previous=before[-1]
            if calendar:
                expected=next((d for d in reversed(calendar) if d<day),None)
                if previous!=expected:raise ValueError('前一市场交易日报价缺失，不能猜测除权基准')
            prev=finite_number(by_date[previous]['close'])
            theoretical=(prev-cash+rights_price*rights)/(1+bonus+rights)
            factor=theoretical/prev if prev>0 else 0
            if not isfinite(factor) or factor<=0:raise ValueError('除权参考价或因子非正')
            events.append({'date':day,'previous_date':previous,'previous_close':prev,'cash':cash,'bonus':bonus,'rights':rights,'rights_price':rights_price,'theoretical_ex_price':theoretical,'factor':factor,'gap':prev-theoretical})
        except (ValueError,KeyError,ZeroDivisionError) as error:quarantined.append({'date':day,'reason':str(error)})
    return {'anchor':anchor,'events':events,'quarantined':quarantined,'future_excluded':future,'before_window_ignored':ignored,'usable':not quarantined,'version':VERSION}


def forward_prices(bars,plan,model='theoretical_ratio'):
    if model not in MODELS:raise ValueError('未知实验复权模型')
    if not plan['usable']:raise ValueError('存在隔离公司行动，本证券不能被标记已验证复权')
    result=[]
    for bar in bars:
        if bar['date']>plan['anchor']:continue
        row=dict(bar)
        for field in ('open','high','low','close'):
            price=finite_number(row[field])
            for event in plan['events']:
                if bar['date']>=event['date']:continue
                if model=='theoretical_ratio':price*=event['factor']
                elif model=='event_affine':price=(price-event['cash']+event['rights_price']*event['rights'])/(1+event['bonus']+event['rights'])
                else:price-=event['gap']
            row[field]=price
        row.update(price_basis='experimental_'+model,adjustment_anchor=plan['anchor'],adjustment_version=VERSION)
        # Volume and turnover retain source shares/CNY; no invented volume factor.
        result.append(row)
    return result


def compare_forward(model_rows,provider_rows,tolerance=.00005):
    left={r['date']:r for r in model_rows};right={r['date']:r for r in provider_rows}
    common=sorted(left.keys()&right.keys());outside=0;maximum=0.0;worst=None
    for day in common:
        for field in ('open','high','low','close'):
            delta=abs(left[day][field]-right[day][field])
            if delta>tolerance:outside+=1
            if delta>maximum:maximum=delta;worst={'date':day,'field':field,'derived':left[day][field],'provider':right[day][field],'absolute_error':delta}
    return {'common_dates':len(common),'model_only_dates':sorted(left.keys()-right.keys()),'provider_only_dates':sorted(right.keys()-left.keys()),'price_comparisons':len(common)*4,'tolerance_cny':tolerance,'outside_tolerance':outside,'max_absolute_error':maximum,'worst':worst,'matched':bool(common) and not outside and set(left)==set(right)}


def continuous_prices(bars,actions,anchor,calendar=None):
    """Derive an explicitly chosen chart basis; never invent missing daily quotes.

    This affine convention was compared with Fuyao forward OHLC. It is a local
    price transformation, not a total-return account or proof of complete actions.
    Unresolved events leave the entire instrument on its original basis.
    """
    raw=[dict(r) for r in bars if r['date']<=anchor]
    if not raw:return raw,{'usable':False,'anchor':anchor,'events_count':0,'quarantined':[],'reason':'没有锚定日前报价','version':VERSION}
    plan=action_plan(raw,actions,anchor,calendar)
    info={**plan,'events_count':len(plan['events']),'model':'event_affine','price_basis':'forward_event_affine','scope':'本地事件前复权价格，不含持仓分红到账或股数记账'}
    if not plan['usable']:
        info.update(price_basis='none',reason='存在未核验公司行动，保留未复权价格')
        return raw,info
    converted=forward_prices(raw,plan,'event_affine')
    if any(not isfinite(r[f]) or r[f]<=0 for r in converted for f in ('open','high','low','close')):
        info.update(usable=False,price_basis='none',reason='变换产生非正或无效价格，保留未复权价格')
        return raw,info
    for row,source in zip(converted,raw):
        row.update(raw_open=source['open'],raw_high=source['high'],raw_low=source['low'],raw_close=source['close'],corporate_action_original=bool(source.get('corporate_action')),corporate_action=False,price_basis='forward_event_affine',adjustment_anchor=anchor)
    return converted,info
