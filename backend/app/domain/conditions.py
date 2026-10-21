"""Whitelisted three-valued conditions and indicators; never execute user code."""
from __future__ import annotations
from datetime import date
from math import isfinite
from typing import Any

NUMERIC_FIELDS = ['close','change_pct','volume','amount','volume_ratio','turnover_ratio_pct','macd_dif','macd_dea','macd_hist','kdj_k','kdj_d','kdj_j','tracking_change_pct','trading_age','limit_up_count_1y'] + [f'ma{n}' for n in (5,10,20,30,60,250)]
NUMERIC_FIELDS += ['intraday_high_change_pct']
NUMERIC_FIELDS += ['open', 'high', 'low']
BOOLEAN_FIELDS = ['macd_cross','kd_cross','limit_up','prev_limit_up','limit_up_1y','bullish_ma_alignment','volume_expand','volume_shrink','close_below_entry','close_above_entry_high','close_below_entry_low','is_st'] + [f'{action}_ma{n}' for action in ('close_above','close_below','cross_above') for n in (5,10,20,30,60,250)]
BOOLEAN_FIELDS += ['macd_dead_cross', 'kd_dead_cross']
FIELDS = {k:{'type':'number','operators':['>','>=','<','<=','==','!=']} for k in NUMERIC_FIELDS}
FIELDS.update({k:{'type':'boolean','operators':['==','!=']} for k in BOOLEAN_FIELDS})
FIELDS['intraday_high_change_pct'].update(operators=['>=','>','<=','<','==','!='],default=3,unit='%',observation_period='D')
FIELDS['limit_up_count_1y'].update(operators=['>=','>','==','<=','<','!='],integer=True,min=0,step=1,constant_only=True)
FIELDS['close_above_entry_high'].update(tracking_only=True,alerts_only=True)
FIELDS['close_below_entry_low'].update(tracking_only=True)
FIELDS['close_breakout_high']={'type':'number','operators':['new_high'],'integer':True,'min':1,'max':750,'step':1,'constant_only':True,'parameter_kind':'lookback_sessions','default':60}
FIELDS['topic_ids']={'type':'topic','operators':['contains','not_contains']}
FIELDS['board']={'type':'enum_set','operators':['in','not_in'],'choices':['main','chinext','star','beijing']}
LABELS={'close':'收盘价','change_pct':'今日涨幅(%)','volume_ratio':'较昨日成交量倍数','macd_cross':'MACD金叉','kd_cross':'KDJ金叉（K线上穿D线）','limit_up':'今日是否涨停（收盘）','prev_limit_up':'昨日是否涨停（前一市场交易日）','limit_up_1y':'过去一年曾涨停（不含今日）','volume_expand':'今日放量','volume_shrink':'今日缩量','tracking_change_pct':'本轮跟踪涨幅(%)','trading_age':'本轮跟踪交易日','close_below_entry':'跌破本轮事件日价格','topic_ids':'题材包含'}
LABELS['limit_up_count_1y']='截至昨日近一年涨停次数'
LABELS['bullish_ma_alignment']='均线多头排列（5>10>20>30>60）'
LABELS['close_above_entry_high']='收盘价高于本轮事件日最高价'
LABELS['close_below_entry_low']='收盘价跌破本轮事件日最低价'
LABELS['macd_dead_cross']='MACD死叉（DIF下穿DEA）'
LABELS['kd_dead_cross']='KDJ死叉（K线下穿D线）'
LABELS['close_breakout_high']='收盘价突破前N个市场交易日最高价'
LABELS['intraday_high_change_pct']='当日最高涨幅（相对前收，%）'
LABELS.update(open='开盘价', high='最高价', low='最低价')
DAILY_ONLY_FIELDS = {'limit_up','prev_limit_up','limit_up_1y','limit_up_count_1y',
                     'close_breakout_high','intraday_high_change_pct','turnover_ratio_pct','board','is_st','topic_ids',
                     'tracking_change_pct','trading_age','close_below_entry',
                     'close_above_entry_high','close_below_entry_low'}

def specs():
    from app.domain.history_events import supported_field
    result=[]
    for key,spec in FIELDS.items():
        hidden=key.startswith(('close_above_ma','close_below_ma')) or key in ('volume_expand','volume_shrink','board','is_st','close_above_entry_high','close_below_entry_low') or (key.startswith('ma') and key[2:].isdigit())
        category='趋势状态' if key in ('bullish_ma_alignment','close_breakout_high') else '事件信号' if key in ('macd_cross','kd_cross','macd_dead_cross','kd_dead_cross','limit_up','prev_limit_up') or key.startswith('cross_above_ma') else '题材归属' if key=='topic_ids' else '跟踪状态' if key in ('tracking_change_pct','trading_age','close_below_entry','close_above_entry_high','close_below_entry_low') else '数值比较'
        result.append({'field':key,'label':LABELS.get(key,key),'category':category,'hidden_in_builder':hidden,'historical_event_supported':supported_field(key),'supported_periods':['D'] if key in DAILY_ONLY_FIELDS else ['D','W','M'],**({'fixed_period':'D'} if key in DAILY_ONLY_FIELDS else {}),**spec})
    return result

def validate(node:dict, depth=0, counter=None, scope=None)->dict:
    if counter is None: counter=[0]
    counter[0]+=1
    if counter[0]>100 or depth>8:raise ValueError('条件过多或嵌套过深（最多100节点/8层）')
    if not isinstance(node,dict):raise ValueError('条件必须是对象')
    if 'period' in node:
        period=node['period']
        if period not in ('D','W','M') or 'field' not in node:raise ValueError('单项条件周期仅支持日K、周K、月K')
        if (scope is not None or node.get('field') in DAILY_ONLY_FIELDS or node.get('right_field') in DAILY_ONLY_FIELDS) and period!='D':
            raise ValueError('日线事件、交易日新高、身份及跟踪基准条件固定使用日线')
        clean={key:value for key,value in node.items() if key!='period'}
        counter[0]-=1
        return {**validate(clean,depth,counter,scope),'period':period}
    if 'history_event' in node:
        if scope is not None:raise ValueError('历史事件内部不能再嵌套历史事件')
        event=node['history_event']
        if set(node)!={'history_event'} or not isinstance(event,dict) or set(event)-{'window','match','where','then'}:raise ValueError('历史事件仅接受window/match/where/then')
        window=event.get('window');match=event.get('match','latest')
        if not isinstance(window,dict) or set(window)!={'from','to'} or any(not isinstance(v,int) or isinstance(v,bool) or not 1<=v<=750 for v in window.values()) or window['from']>window['to']:raise ValueError('历史事件窗口需1至750个市场交易日，from不能大于to')
        if match not in ('latest','earliest'):raise ValueError('历史事件匹配规则仅支持latest/earliest')
        return {'history_event':{'window':dict(window),'match':match,'where':validate(event.get('where'),depth+1,counter,'event_where'),'then':validate(event.get('then'),depth+1,counter,'event_then')}}
    if 'conditions' in node:
        logic=node.get('logic','AND')
        if logic not in ('AND','OR'):raise ValueError('logic只能为AND或OR')
        return validate({'all' if logic=='AND' else 'any':node['conditions']},depth,counter,scope)
    group='all' if 'all' in node else 'any' if 'any' in node else None
    if group:
        if set(node)!={group}:raise ValueError('组合条件仅接受all或any')
        if not isinstance(node[group],list) or not node[group] or len(node[group])>50:raise ValueError('组合条件需要1至50个条件')
        return {group:[validate(c,depth+1,counter,scope) for c in node[group]]}
    if set(node)-{'field','op','value','right_field','right_event_field'}:raise ValueError('条件包含不支持字段，不允许执行代码')
    field=node.get('field');op=node.get('op');value=node.get('value')
    if field not in FIELDS:raise ValueError('不支持的策略字段：'+str(field))
    spec=FIELDS[field]
    if scope=='event_where':
        from app.domain.history_events import supported_field
        if not supported_field(field) or 'right_field' in node and not supported_field(node['right_field']):raise ValueError('历史事件条件目前仅支持日OHLCV、均线与技术指标；历史题材、身份、跟踪和涨停来源事件尚不支持')
    if op not in spec['operators']:raise ValueError('字段不支持比较符：'+str(op))
    if 'right_event_field' in node:
        if scope!='event_then' or field!='close' or node['right_event_field'] not in ('close','high','low') or 'value' in node or 'right_field' in node:raise ValueError('事件价格比较仅能在本事件then内使用观察日收盘价，对比事件日close/high/low')
        return {'field':field,'op':op,'right_event_field':node['right_event_field']}
    if 'right_field' in node:
        right=node['right_field']
        if spec.get('constant_only') or FIELDS.get(right,{}).get('constant_only'):raise ValueError('参数条件只支持有效常量整数，不支持字段比较')
        if 'value' in node or right not in FIELDS or spec['type']!='number' or FIELDS[right]['type']!='number':raise ValueError('right_field只支持数值白名单字段且不能同时提供value')
        return {'field':field,'op':op,'right_field':right}
    if spec['type']=='number' and (isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value)):raise ValueError('数值条件需要有限数字')
    if spec.get('integer') and (not isinstance(value,int) or isinstance(value,bool) or value<spec.get('min',0) or value>spec.get('max',float('inf'))):raise ValueError('新高窗口需要1至750个市场交易日的正整数' if field=='close_breakout_high' else '近一年涨停次数需要非负整数')
    if spec['type']=='boolean' and not isinstance(value,bool):raise ValueError('布尔条件value必须为true/false')
    if spec['type']=='topic' and (not isinstance(value,str) or not value or len(value)>128):raise ValueError('题材条件需要有效topic_id')
    if spec['type']=='enum_set' and (not isinstance(value,list) or not value or len(value)>4 or len(set(map(str,value)))!=len(value) or any(v not in spec['choices'] for v in value)):raise ValueError('板块范围需选择主板、创业板、科创板或北交所，且不能重复')
    return {'field':field,'op':op,'value':value}

def evaluate(node:dict, metrics:dict, history=None)->dict:
    if 'period' in node and 'field' in node:
        period=node['period'];clean={key:value for key,value in node.items() if key!='period'}
        if node['field'] in DAILY_ONLY_FIELDS or node.get('right_field') in DAILY_ONLY_FIELDS:
            scoped=metrics
        else:
            scoped=metrics.get('_period_metrics',{}).get(period)
            if scoped is None:scoped=metrics if metrics.get('indicator_period','D')==period else {}
        return {**evaluate(clean,scoped,history),'period':period,
                'observed_date':scoped.get('observed_date')}
    if 'history_event' in node:
        from app.domain.history_events import evaluate_event
        return evaluate_event(node,metrics,history)
    if 'right_event_field' in node:
        from app.domain.history_events import evaluate_event_price
        return evaluate_event_price(node,history)
    group='all' if 'all' in node else 'any' if 'any' in node else None
    if group:
        checks=[evaluate(c,metrics,history) for c in node[group]];values=[c['result'] for c in checks]
        result=(False if False in values else None if None in values else True) if group=='all' else (True if True in values else None if None in values else False)
        return {'logic':'AND' if group=='all' else 'OR','result':result,'checks':checks}
    a=metrics.get(node['field']);b=metrics.get(node['right_field']) if 'right_field' in node else node['value'];op=node['op'];result=None
    if 'intraday_high_change_pct' in (node['field'],node.get('right_field')):a=finite(a);b=finite(b)
    if node['field']=='limit_up_count_1y':return _evaluate_limit_count(node,metrics)
    if node['field']=='close_breakout_high':return _evaluate_breakout(node,metrics)
    if FIELDS[node['field']]['type']=='boolean' and not isinstance(a,bool):a=None
    if a is not None and b is not None:
        try:
            if op in ('in','not_in'):
                result=None if a=='unknown' else (a in b if op=='in' else a not in b)
            elif op in ('contains','not_contains'):
                present=b in a;complete=metrics.get('_complete_topic_ids')
                result=(present if op=='contains' else not present) if present or complete is None or b in complete else None
            elif op=='>':result=a>b
            elif op=='>=':result=a>=b
            elif op=='<':result=a<b
            elif op=='<=':result=a<=b
            elif op=='==':result=a==b
            elif op=='!=':result=a!=b
        except (TypeError,ValueError):result=None
    extra={'evidence':metrics.get(node['field']+'_evidence')} if node['field'] in ('close_above_entry_high','close_below_entry_low','limit_up_1y') else {}
    if node['field']=='intraday_high_change_pct':extra.update(observation_date=metrics.get(node['field']+'_date'),observation_period='D')
    return {**node,'actual':a,'right_actual':b,'result':result,'reason':(metrics.get(node['field']+'_reason') or '数据/预热/历史身份不足，按未知处理') if result is None else None,**extra}

def _evaluate_limit_count(node,metrics):
    """A partial count stays null; only mathematically certain bounds decide."""
    field='limit_up_count_1y';actual=metrics.get(field);lower=metrics.get(field+'_lower_bound');upper=metrics.get(field+'_upper_bound')
    integer=lambda value:isinstance(value,int) and not isinstance(value,bool) and value>=0
    invalid=any(value is not None and not integer(value) for value in (actual,lower,upper)) or lower is not None and upper is not None and integer(lower) and integer(upper) and upper<lower
    invalid=invalid or actual is not None and integer(actual) and ((lower is not None and integer(lower) and actual<lower) or (upper is not None and integer(upper) and actual>upper))
    if invalid:actual=lower=upper=None
    actual=actual if integer(actual) else None
    lower=actual if actual is not None else lower if integer(lower) else None
    upper=actual if actual is not None else upper if integer(upper) and lower is not None and upper>=lower else None
    value=node['value'];op=node['op'];result=None
    state='exact' if actual is not None else 'partial_bound' if lower is not None else 'unknown'
    if lower is not None:
        if op=='>=':result=True if lower>=value else False if upper is not None and upper<value else None
        elif op=='>':result=True if lower>value else False if upper is not None and upper<=value else None
        elif op=='<=':result=False if lower>value else True if upper is not None and upper<=value else None
        elif op=='<':result=False if lower>=value else True if upper is not None and upper<value else None
        elif op=='==':result=False if lower>value or upper is not None and upper<value else True if lower==upper==value else None
        elif op=='!=':result=True if lower>value or upper is not None and upper<value else False if lower==upper==value else None
    coverage=metrics.get(field+'_coverage') or {}
    reason='计数或上下界证据非法或相互矛盾，保持未知' if invalid else metrics.get(field+'_reason')
    if state=='partial_bound' and result is not None:reason='完整次数待核验；仅由已确认次数下界'+str(lower)+('及可靠上界'+str(upper) if upper is not None else '')+'证明此比较'+('成立' if result else '不成立')
    return {**node,'actual':actual,'right_actual':value,'actual_lower_bound':lower,'actual_upper_bound':upper,'confirmed_count':metrics.get(field+'_confirmed_count'),'value_state':state,'result':result,'window_start':coverage.get('window_start'),'window_end':coverage.get('window_end'),'covered_days':coverage.get('covered_days'),'expected_days':coverage.get('expected_days'),'coverage':coverage,'reason':reason or ('近一年来源或逐日原始报价证据不足，不能把已确认次数当完整次数' if result is None else None)}

def _evaluate_breakout(node,metrics):
    lookback=node['value'];available=metrics.get('breakout_previous_sessions');blocker=metrics.get('breakout_blocking_distance')
    integer=lambda value:isinstance(value,int) and not isinstance(value,bool) and value>=0
    current=metrics.get('new_high_current_close',metrics.get('current_daily_close',metrics.get('close')))
    price_valid=isinstance(current,(int,float)) and not isinstance(current,bool) and isfinite(current) and current>0
    valid=metrics.get('breakout_valid') is True and integer(available) and (blocker is None or integer(blocker) and blocker>0) and price_valid
    result=(blocker is None or blocker>lookback) if valid and available>=lookback else None
    return {**node,'actual':result,'right_actual':lookback,'lookback_sessions':lookback,'current_close':current,'covered_sessions':available if integer(available) else None,'blocking_distance':blocker if valid else None,'result':result,'reason':'连续市场交易日报价或公司行动可比口径不足，不能用最近N条有效报价或周K替代日窗' if result is None else None}

def names(node):
    if 'history_event' in node:return names(node['history_event']['where'])|names(node['history_event']['then'])
    if 'field' in node:return {node['field']}|({node['right_field']} if 'right_field' in node else set())
    return set().union(*(names(c) for c in node.get('all',node.get('any',[]))))

def topic_dependencies(node):
    if 'history_event' in node:return topic_dependencies(node['history_event']['where'])|topic_dependencies(node['history_event']['then'])
    if 'field' in node:return {node['value']} if node['field']=='topic_ids' else set()
    return set().union(*(topic_dependencies(c) for c in node.get('all',node.get('any',[]))))

def finite(v):
    if v is None or isinstance(v,bool):return None
    try:
        value=float(v)
        return value if isfinite(value) else None
    except (ValueError,TypeError):return None


def past_limit_up_metrics(count_metrics):
    """Boolean projection of the same verified, today-excluded year proof."""
    field='limit_up_count_1y';count=count_metrics.get(field+'_confirmed_count')
    coverage=count_metrics.get(field+'_coverage') or {}
    valid=isinstance(count,int) and not isinstance(count,bool) and count>=0
    state=True if valid and count>=1 else False if valid and coverage.get('complete') is True and count==0 else None
    return {'limit_up_1y':state,
            'limit_up_1y_reason':None if state is not None else count_metrics.get(field+'_reason') or '滚动一年来源池或逐股成交报价不完整，不能把有限样本当作一年未涨停',
            'limit_up_1y_evidence':{'basis':'截至昨日滚动一自然年，逐股原始收盘及有效成交量核验的来源收盘池',
                                     'confirmed_count':count if valid else None,'coverage':coverage}}

def normalized_bar(row):
    r=dict(row)
    aliases={'date':('date','trade_date','day','time'),'open':('open','open_price'),'high':('high','high_price'),'low':('low','low_price'),'close':('close','close_price'),'volume':('volume','volume_shares','vol'),'amount':('amount','turnover_cny','turnover','money')}
    out={}
    for key,choices in aliases.items():
        value=next((r[c] for c in choices if c in r),None)
        out[key]=str(value)[:10] if key=='date' and value is not None else finite(value)
    out['limit_up']=r.get('limit_up') if isinstance(r.get('limit_up'),bool) else None
    out['turnover_ratio_pct']=finite(r.get('turnover_ratio_pct'))
    out['corporate_action']=bool(r.get('corporate_action',False))
    out['corporate_action_coverage_known']=r.get('corporate_action_coverage_known',True) is True
    return out

def metrics_from_enriched(row,previous=None,topics=None):
    """Condition-only adapter over the one shared chart/backtest indicator implementation."""
    m={field:None for field in FIELDS}
    m['topic_ids']=topics
    if not row:return m
    for field in FIELDS:
        if field in row:m[field]=row[field]
    for field in ('breakout_valid','breakout_previous_sessions','breakout_blocking_distance','new_high_current_close'):
        if field in row:m[field]=row[field]
    for field in ('limit_up','prev_limit_up'):
        if not isinstance(m[field],bool):m[field]=None
    m['topic_ids']=topics
    m['observed_date']=row.get('date')
    m['indicator_warmup_bars']=row.get('indicator_warmup_bars')
    field='intraday_high_change_pct'
    m[field]=finite(row.get(field))
    m[field+'_date']=row.get('date')
    m[field+'_reason']=None if m[field] is not None else row.get(field+'_reason') or '需要观察日及前一市场交易日有效成交报价与可核验公司行动覆盖'
    for field, fast, slow in (('macd_dead_cross', 'macd_dif', 'macd_dea'), ('kd_dead_cross', 'kdj_k', 'kdj_d')):
        # Trust only the shared, warmed-up continuous observation. Old cached
        # rows without the new field stay unknown until recalculated.
        warmup = row.get('indicator_warmup_bars')
        ready = isinstance(warmup, int) and not isinstance(warmup, bool) and warmup >= 36 and row.get('price_continuity') is True
        ready = ready and finite(row.get(fast)) is not None and finite(row.get(slow)) is not None
        volume = finite(row.get('volume'))
        ready = ready and volume is not None and volume > 0
        if previous is not None:
            old_volume = finite(previous.get('volume'))
            ready = ready and old_volume is not None and old_volume > 0
        m[field] = row[field] if ready and isinstance(row.get(field), bool) else None
        m[field + '_reason'] = None if m[field] is not None else row.get('indicator_reset_reason') or row.get('indicator_warmup_reason') or '死叉需至少36根连续可比报价、前后两期有效成交及指标；停牌零量保留未知，不以金叉未触发替代'
    m['volume_expand']=row['volume_ratio']>1 if row.get('volume_ratio') is not None else None
    m['volume_shrink']=row['volume_ratio']<1 if row.get('volume_ratio') is not None else None
    alignment = [finite(row.get(f'ma{window}')) for window in (5,10,20,30,60)]
    ready = all(value is not None and value>0 for value in alignment)
    m['bullish_ma_alignment'] = all(a>b for a,b in zip(alignment,alignment[1:])) if ready else None
    m['bullish_ma_alignment_reason'] = None if ready else '多头排列需同一判断周期有效MA5/10/20/30/60；缺数或指标预热不足，不能判为否'
    for window in (5,10,20,30,60,250):
        ma=row.get(f'ma{window}');close=row.get('close')
        if ma is not None and close is not None:
            m[f'close_above_ma{window}']=close>ma
            m[f'close_below_ma{window}']=close<ma
            old_ma=previous.get(f'ma{window}') if previous else None
            if old_ma is not None and row.get('price_continuity',True):
                m[f'cross_above_ma{window}']=previous['close']<=old_ma and close>ma
    return m


def metrics_for(bars:list,as_of:str,calendar:list[str],period='D',topics=None)->dict:
    from app.analytics.indicators import enrich_bars,aggregate_weekly,intraday_high_change
    rows=sorted((normalized_bar(b) for b in bars),key=lambda r:r['date'] or '')
    rows=[r for r in rows if r['date'] and r['date']<=as_of]
    daily_rows=rows
    empty={field:None for field in FIELDS};empty['topic_ids']=topics
    positions={day:index for index,day in enumerate(calendar)}
    prior_position=positions.get(as_of)
    prior_day=calendar[prior_position-1] if prior_position is not None and prior_position>0 else None
    found_current=[row for row in daily_rows if row['date']==as_of]
    found_previous=[row for row in daily_rows if row['date']==prior_day]
    daily_high,daily_high_reason=intraday_high_change(found_current[0] if len(found_current)==1 else {},found_previous[0] if len(found_previous)==1 else None,positions)
    daily_high_metrics={'intraday_high_change_pct':daily_high,'intraday_high_change_pct_reason':daily_high_reason,'intraday_high_change_pct_date':as_of}
    today=found_current[0] if len(found_current)==1 else None
    daily_high_metrics['turnover_ratio_pct']=today.get('turnover_ratio_pct') if today and today.get('volume') is not None and today['volume']>0 else None
    empty.update(daily_high_metrics)
    if not rows:return empty
    # Invalid price records remain unknown. Removing a bad day makes shared calendar logic restart.
    rows=[r for r in rows if all(r[k] is not None for k in ('open','high','low','close'))]
    if not rows or (period=='D' and rows[-1]['date']!=as_of):return empty
    for row in rows:
        row['_volume_known']=row['volume'] is not None
        row['volume']=row['volume'] if row['_volume_known'] else 0
        row['amount']=row['amount'] if row['amount'] is not None else 0
    daily_breakout={}
    if period in ('W','M'):
        from app.analytics.rolling_high import rolling_high_state
        daily_breakout=rolling_high_state(daily_rows,calendar,as_of)
        empty.update(daily_breakout)
    if period in ('W','M'):
        from app.analytics.period_dates import completed_indicator_bars
        enriched=completed_indicator_bars(rows,calendar,as_of,period)
    else:enriched=enrich_bars(rows,calendar)
    if not enriched:return empty
    current=enriched[-1];previous=enriched[-2] if len(enriched)>1 else None
    if period in ('W','M'):
        from app.analytics.period_dates import latest_closed_date
        if current['date']!=latest_closed_date(calendar,as_of,period):return empty
    result=metrics_from_enriched(current,previous,topics)
    result.update(daily_high_metrics)
    result.update(daily_breakout)
    target_position=calendar.index(as_of) if as_of in calendar else None
    prior_day=calendar[target_position-1] if target_position is not None and target_position>0 else None
    for field,target in (('limit_up',as_of),('prev_limit_up',prior_day)):
        found=[row for row in daily_rows if row['date']==target]
        quote=found[0] if len(found)==1 else None
        valid=target_position is not None and quote and quote.get('close') is not None and quote['close']>0 and quote.get('volume') is not None and quote['volume']>0
        result[field]=quote['limit_up'] if valid else None
        result[field+'_date']=target
        result[field+'_reason']=None if result[field] is not None else '缺少精确市场交易日的有效成交报价或可核验涨停来源；不回溯其他报价日'
    result['indicator_period']=period
    if not current.get('_volume_known',True):
        for key in ('volume','volume_ratio','volume_expand','volume_shrink'):result[key]=None
    if previous is not None and not previous.get('_volume_known',True):
        for key in ('volume_ratio','volume_expand','volume_shrink'):result[key]=None
    result['limit_up_1y']=None
    result['limit_up_1y_reason']='需要滚动一年逐股来源池证据适配；不按涨跌幅或未核价的日K标记推断'
    return result
