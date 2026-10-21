"""Local daily/weekly/monthly descriptive trends, with explicit warm-up gaps."""
from app.analytics.indicators import enrich_bars,aggregate_weekly
from app.analytics.monthly import aggregate_monthly,enrich_monthly_bars

FIELDS=['close','ma5','ma10','ma20','ma30','ma60','ma250','volume','amount','volume_ratio','macd_dif','macd_dea','macd_hist','kdj_k','kdj_d','kdj_j']


def build_trends(market,symbol,as_of):
    calendar=market.calendar();raw=market.bars(symbol,as_of=as_of)
    if not raw:return []
    # Match the main raw-price indicator convention; corporate actions restart
    # warm-up instead of pretending to be a complete adjusted-price account.
    if hasattr(market,'actions'):
        action_dates={row.get('date') for row in market.actions(symbol,end=as_of)}
        raw=[{**row,'corporate_action':bool(row.get('corporate_action') or row['date'] in action_dates)} for row in raw]
    periods=[('D','日线',raw),('W','周线',aggregate_weekly(raw,calendar,as_of)),('M','月线',aggregate_monthly(raw,calendar,as_of))]
    result=[]
    for period,label,values in periods:
        completed=values if period=='D' else [row for row in values if row.get('complete')]
        enriched=([row for row in enrich_monthly_bars(values) if row.get('complete')] if period=='M' else enrich_bars(completed,calendar if period=='D' else None))
        latest=enriched[-1] if enriched else None;observations={field:latest.get(field) if latest else None for field in FIELDS}
        facts=[]
        if latest:
            for window in (5,10,20,30,60,250):
                average=latest.get('ma'+str(window))
                if average is not None:facts.append('收盘'+('高于' if latest['close']>average else '低于' if latest['close']<average else '等于')+'MA'+str(window))
        missing=[field for field,value in observations.items() if value is None]
        warnings=[]
        if period=='M':warnings.append(f'月线由已留存日OHLC聚合，共{len(values)}根、其中{len(completed)}根完整月；首月、形成中月、报价缺口或尚未复权公司行动月不凑入35根可比月指标预热。')
        if period!='D' and values and not values[-1].get('complete'):warnings.append('当前周期尚未完成或报价不完整，仅展示聚合预览；指标与观察使用最近完整周期。')
        if missing:warnings.append('样本不足、缺口或公司行动后的预热不足指标保持未知；MACD/KDJ至少需要35根可比周期。')
        result.append({'period':period,'label':label,'source':'本地已发布OHLC','price_basis':'none','sample_bars':len(values),'complete_bars':len(completed),'data_begin':raw[0]['date'],'data_end':raw[-1]['date'],'latest_complete_date':latest['date'] if latest else None,'indicator_warmup_bars':latest.get('indicator_warmup_bars',0) if latest else 0,'observations':observations,'missing_indicators':missing,'facts':facts,'current_period':{key:values[-1].get(key) for key in ('date','open','high','low','close','volume','amount','complete')} if values else None,'warnings':warnings})
    return result
