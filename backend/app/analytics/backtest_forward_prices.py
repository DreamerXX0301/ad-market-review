"""Causal signal summaries for an explicitly verified action-free interval.

The first observation owns the adjustment seed. Later observations append real
raw quotes because no new action factor is permitted in this interval. This is
not an end-date adjusted series projected back into earlier signal dates.
"""
from bisect import bisect_right

from app.domain.conditions import FIELDS, metrics_for, metrics_from_enriched, normalized_metric_rows, merge_condition_price_metrics
from app.domain.strategy_prices import price_cache_key
from .indicators import enrich_bars
from .period_dates import completed_indicator_bars, latest_closed_date


class ForwardIntervalPrices:
    def __init__(self, seed, raw_rows, calendar, end, periods, symbol, source):
        self.seed=seed;self.calendar=calendar;self.symbol=symbol;self.source=source
        self.start=seed['anchor_date'];self.end=end
        self.raw=[dict(row) for row in raw_rows if row['date']<=end]
        self.days=[row['date'] for row in self.raw]
        self.prices=list(seed['price_rows'])
        for row in self.raw:
            if row['date']<=self.start:continue
            converted={**row,'corporate_action_original':False,'corporate_action':False,
                'price_basis':'forward_event_affine','adjustment_anchor':self.start}
            converted.update({'raw_'+field:row.get(field) for field in ('open','high','low','close','volume','amount')})
            self.prices.append(converted)
        self.series={};self.dates={}
        for lane,rows in (('raw',self.raw),('price',self.prices)):
            normalized=normalized_metric_rows(rows,end)
            valid=[dict(row) for row in normalized if all(row.get(field) is not None for field in ('open','high','low','close'))]
            for row in valid:
                row['_volume_known']=row['volume'] is not None
                row['volume']=row['volume'] if row['_volume_known'] else 0
                row['amount']=row['amount'] if row['amount'] is not None else 0
            daily=enrich_bars(valid,calendar)
            self.series[lane]={'D':daily}
            for period in periods-{'D'}:
                self.series[lane][period]=completed_indicator_bars(valid,calendar,end,period)
            self.dates[lane]={period:[row['date'] for row in values] for period,values in self.series[lane].items()}

    def context(self,day):
        stop=bisect_right(self.days,day)
        return {**self.seed,'anchor_date':day,'raw_rows':self.raw[:stop],
            'price_rows':[{**row,'adjustment_anchor':day} for row in self.prices[:stop]],
            'adjustment':{**self.seed['adjustment'],'anchor':day},
            'cache_key':price_cache_key(self.symbol,day,'forward',self.source)}

    def _metrics(self,lane,day,period,daily_facts):
        values=self.series[lane][period];dates=self.dates[lane][period]
        index=bisect_right(dates,day)-1
        expected=latest_closed_date(self.calendar,day,period)
        current=values[index] if index>=0 and dates[index]==expected else None
        if current is None:result={field:None for field in FIELDS};result['topic_ids']=None
        else:
            result=metrics_from_enriched(current,values[index-1] if index else None)
            result['indicator_period']=period
            if not current.get('_volume_known',True):
                for field in ('volume','volume_ratio','volume_expand','volume_shrink'):result[field]=None
            if index>0 and not values[index-1].get('_volume_known',True):
                for field in ('volume_ratio','volume_expand','volume_shrink'):result[field]=None
            result['limit_up_1y']=None
            result['limit_up_1y_reason']='需要滚动一年逐股来源池证据适配；不按涨跌幅或未核价的日K标记推断'
        # These fields have daily semantics even when the condition is weekly.
        for field in ('intraday_high_change_pct','turnover_ratio_pct'):
            for suffix in ('','_reason','_date'):
                if field+suffix in daily_facts:result[field+suffix]=daily_facts[field+suffix]
        if period in ('W','M'):
            ds=self.series[lane]['D'];di=bisect_right(self.dates[lane]['D'],day)-1
            if di>=0 and ds[di]['date']==day:
                for field in ('breakout_valid','breakout_previous_sessions','breakout_blocking_distance','new_high_current_close'):
                    if field in ds[di]:result[field]=ds[di][field]
        if current is not None:
            for field in ('limit_up','prev_limit_up'):
                for suffix in ('','_reason','_date'):
                    if field+suffix in daily_facts:result[field+suffix]=daily_facts[field+suffix]
        return result

    def metrics(self,day,period,context):
        stop=bisect_right(self.days,day)
        facts=metrics_for(self.raw[max(0,stop-2):stop],day,self.calendar,'D')
        source=self._metrics('raw',day,period,facts)
        price=self._metrics('price',day,period,facts)
        return merge_condition_price_metrics(source,price,context,day,period)


def interval_prices(seed,raw_rows,calendar,start,end,periods,symbol,source,actions):
    """A missing, ambiguous or newer factor always retains the slow reference."""
    if seed.get('price_basis')!='forward_event_affine' or seed.get('anchor_date')!=start:return None
    if len({row['date'] for row in raw_rows})!=len(raw_rows):return None
    if not isinstance(actions,list):return None
    for event in actions:
        day=event.get('date') if isinstance(event,dict) else None
        if not isinstance(day,str) or start<day<=end:return None
    suffix=[row for row in raw_rows if start<row['date']<=end]
    if any(row.get('corporate_action') is not False or row.get('corporate_action_coverage_known') is not True for row in suffix):return None
    return ForwardIntervalPrices(seed,raw_rows,calendar,end,set(periods)|{'D'},symbol,source)
