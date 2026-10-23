"""Offline event study of causal BOLL squeezes, not a selection/trading engine.

Signals only use data at/before the event close. Forward labels start at the
next exact market session's open; missing quotes and known actions stay unknown.
"""
from bisect import bisect_left, insort
from collections import Counter, deque
from datetime import date
from math import isfinite, sqrt
from statistics import mean, median

from app.analytics.indicators import enrich_bars

VERSION = 'boll-squeeze-event-study-v1'
DEFAULTS = {'band_period': 20, 'deviations': 2, 'rank_window': 125, 'squeeze_quantile': .10,
            'recent_squeeze_sessions': 5, 'event_cooldown_sessions': 20, 'setup_direction_sessions': 10,
            'volume_multiple': 1.5, 'slope_sessions': 5, 'false_breakout_sessions': 5,
            'horizons': [5, 10, 20]}
VARIANTS = ('base', 'volume', 'volume_slope', 'volume_slope_macd', 'volume_slope_kdj')


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered)-1)*fraction
    low = int(position); high = min(low+1,len(ordered)-1)
    return ordered[low]+(ordered[high]-ordered[low])*(position-low)


def prepare(rows, calendar, action_dates, config=None):
    """Strict market-session alignment, action/gap resets, historical quantiles."""
    config = {**DEFAULTS, **(config or {})}
    positions = {day:index for index,day in enumerate(calendar)}
    indexed={};duplicates=set()
    for source in rows:
        day=source.get('date')
        if day not in positions:
            continue
        if day in indexed:
            duplicates.add(day)
        indexed[day]=dict(source)
    actions=set()
    for day in action_dates:
        try:valid_day=isinstance(day,str) and date.fromisoformat(day).isoformat()==day
        except ValueError:valid_day=False
        if not valid_day:
            return None
        position=bisect_left(calendar,day)
        if position<len(calendar):
            actions.add(position)
    valid=[]
    for index,day in enumerate(calendar):
        row=indexed.get(day)
        if row and day not in duplicates and all(positive(row.get(field)) for field in ('open','high','low','close','volume')) and row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']:
            valid.append({**row,'corporate_action':index in actions})
    enriched={row['date']:row for row in enrich_bars(valid,calendar)}
    output=[];closes=deque();width_history=deque();ordered_widths=[];prior=None
    period=config['band_period'];lookback=config['rank_window']
    for index,day in enumerate(calendar):
        row=enriched.get(day)
        if row is None or index in actions or prior is not None and index-prior!=1:
            closes.clear();width_history.clear();ordered_widths.clear()
        prior=index if row else None
        value={'date':day,'row':row,'action':index in actions,'middle':None,'upper':None,'lower':None,
               'bandwidth':None,'historical_quantile':None,'squeeze':None}
        if row:
            closes.append(float(row['close']))
            if len(closes)>period:
                closes.popleft()
            if len(closes)==period:
                middle=sum(closes)/period
                deviation=sqrt(sum((price-middle)**2 for price in closes)/period)
                upper=middle+config['deviations']*deviation;lower=middle-config['deviations']*deviation
                width=(upper-lower)/middle
                value.update(middle=middle,upper=upper,lower=lower,bandwidth=width,
                             percent_b=(row['close']-lower)/(upper-lower) if upper>lower else None)
                if len(ordered_widths)==lookback:
                    threshold=quantile(ordered_widths,config['squeeze_quantile'])
                    value.update(historical_quantile=threshold,squeeze=width<=threshold)
                # The current width is inserted only AFTER evaluating its
                # threshold. No full-history or future percentile can leak in.
                width_history.append(width);insort(ordered_widths,width)
                if len(width_history)>lookback:
                    old=width_history.popleft();ordered_widths.pop(bisect_left(ordered_widths,old))
        output.append(value)
    return output


def direction(current,previous):
    if not current['row'] or not previous['row'] or current['upper'] is None or previous['upper'] is None or current['action']:
        return None
    close=current['row']['close'];prior=previous['row']['close']
    if close>current['upper'] and prior<=previous['upper']:
        return 'up'
    if close<current['lower'] and prior>=previous['lower']:
        return 'down'
    return None


def _window(values,index,horizon):
    if index+horizon>=len(values):
        return None,'right_censored'
    selected=values[index+1:index+horizon+1]
    if any(value['row'] is None for value in selected):
        return None,'missing_or_invalid_market_session_quote'
    if any(value['action'] for value in selected):
        return None,'known_corporate_action_in_forward_window'
    return selected,None


def forward_label(values,index,horizon,side):
    window,reason=_window(values,index,horizon)
    if reason:
        return {'return_pct':None,'close_path_drawdown_pct':None,'minimum_low_return_pct':None,'reason':reason}
    entry=window[0]['row']['open'];exit_close=window[-1]['row']['close']
    peak=entry;drawdown=0.0
    for value in window:
        close=value['row']['close'];peak=max(peak,close);drawdown=min(drawdown,(close/peak-1)*100)
    return {'entry_date':window[0]['date'],'exit_date':window[-1]['date'],
            'return_pct':(exit_close/entry-1)*100,'close_path_drawdown_pct':drawdown,
            'minimum_low_return_pct':(min(value['row']['low'] for value in window)/entry-1)*100,'reason':None}


def failure_labels(values,index,side,horizon=5):
    """Prespecified labels, not a stop-loss or an executable trade outcome.

    Re-entry is broad. Strict failure additionally closes beyond the event
    candle's opposite extreme AND the current middle band in the same session.
    """
    window,reason=_window(values,index,horizon)
    if reason:
        return {'reentry':None,'strict_failure':None,'reason':reason}
    event=values[index]['row']
    if side=='up':
        reentry=any(value['row']['close']<=value['upper'] for value in window)
        strict=any(value['row']['close']<event['low'] and value['row']['close']<value['middle'] for value in window)
    else:
        reentry=any(value['row']['close']>=value['lower'] for value in window)
        strict=any(value['row']['close']>event['high'] and value['row']['close']>value['middle'] for value in window)
    return {'reentry':reentry,'strict_failure':strict,'reason':None}


def study_symbol(symbol,rows,calendar,action_dates,config=None):
    config={**DEFAULTS,**(config or {})};values=prepare(rows,calendar,action_dates,config)
    if values is None:
        return {'instrument_id':symbol,'events':[],'setup_counts':{},'skipped_reason':'invalid_corporate_action_date'}
    events=[];setups=Counter();last_event=-10000;last_setup=-10000
    for index,current in enumerate(values):
        if current['squeeze'] is True and index-last_setup>=config['event_cooldown_sessions']:
            last_setup=index
            window,reason=_window(values,index,config['setup_direction_sessions'])
            first=None
            if reason:
                setups['unknown_'+reason]+=1
            else:
                for offset,value in enumerate(window,start=1):
                    first=direction(value,values[index+offset-1])
                    if first:
                        break
                setups[first or 'no_breakout_within_window']+=1
        if not index or index-last_event<config['event_cooldown_sessions']:
            continue
        side=direction(current,values[index-1])
        if side is None:
            continue
        # Deduplication is fixed before reading forward labels. All variants
        # are subsets of these same base events; no per-filter cherry picking.
        last_event=index
        prior=values[max(0,index-config['recent_squeeze_sessions']):index]
        squeeze=True if any(value['squeeze'] is True for value in prior) else None if len(prior)<config['recent_squeeze_sessions'] or any(value['squeeze'] is None for value in prior) else False
        volume_window=values[max(0,index-config['band_period']):index]
        average_volume=mean(value['row']['volume'] for value in volume_window) if len(volume_window)==config['band_period'] and all(value['row'] for value in volume_window) else None
        multiple=current['row']['volume']/average_volume if average_volume and average_volume>0 else None
        previous_middle=values[index-config['slope_sessions']]['middle'] if index>=config['slope_sessions'] else None
        slope=current['middle']/previous_middle-1 if previous_middle and previous_middle>0 else None
        volume=multiple is not None and multiple>=config['volume_multiple']
        slope_ok=slope is not None and (slope>0 if side=='up' else slope<0)
        row=current['row'];macd=row.get('macd_hist');k=row.get('kdj_k');d=row.get('kdj_d')
        macd_ok=macd is not None and (macd>0 if side=='up' else macd<0)
        kdj_ok=k is not None and d is not None and ((k>d and k<=80) if side=='up' else (k<d and k>=20))
        variants=['base']
        if volume:variants.append('volume')
        if volume and slope_ok:
            variants.append('volume_slope')
            if macd_ok:variants.append('volume_slope_macd')
            if kdj_ok:variants.append('volume_slope_kdj')
        failure=failure_labels(values,index,side,config['false_breakout_sessions'])
        events.append({'instrument_id':symbol,'event_date':current['date'],'year':current['date'][:4],
            'direction':side,'recent_squeeze':squeeze,'variants':variants,'volume_multiple':multiple,
            'middle_slope':slope,'bandwidth':current['bandwidth'],
            'false_breakout_within_5':failure['reentry'],'false_breakout_reason':failure['reason'],
            'strict_failure_within_5':failure['strict_failure'],'strict_failure_reason':failure['reason'],
            'outcomes':{str(horizon):forward_label(values,index,horizon,side) for horizon in config['horizons']}})
    return {'instrument_id':symbol,'events':events,'setup_counts':dict(setups),'skipped_reason':None,
            'valid_quote_sessions':sum(value['row'] is not None for value in values),
            'squeeze_known_sessions':sum(value['squeeze'] is not None for value in values)}


def _aggregate(events,horizon):
    values=[event['outcomes'][str(horizon)] for event in events]
    valid=[value for value in values if value['return_pct'] is not None]
    fake=[event['false_breakout_within_5'] for event in events if event['false_breakout_within_5'] is not None]
    strict=[event['strict_failure_within_5'] for event in events if event['strict_failure_within_5'] is not None]
    returns=[value['return_pct'] for value in valid]
    return {'events':len(events),'valid_forward_events':len(valid),'unknown_forward_events':len(values)-len(valid),
            'unknown_reasons':dict(Counter(value['reason'] for value in values if value['reason'])),
            'mean_return_pct':mean(returns) if returns else None,'median_return_pct':median(returns) if returns else None,
            'positive_return_fraction':sum(value>0 for value in returns)/len(returns) if returns else None,
            'return_p10_pct':quantile(returns,.10),'return_p90_pct':quantile(returns,.90),
            'median_close_path_drawdown_pct':median(value['close_path_drawdown_pct'] for value in valid) if valid else None,
            'median_minimum_low_return_pct':median(value['minimum_low_return_pct'] for value in valid) if valid else None,
            'false_breakout_known_events':len(fake),'false_breakout_unknown_events':len(events)-len(fake),
            'false_breakout_5_fraction':sum(fake)/len(fake) if fake else None,
            'strict_failure_known_events':len(strict),'strict_failure_unknown_events':len(events)-len(strict),
            'strict_failure_5_fraction':sum(strict)/len(strict) if strict else None}


def aggregate(results,config=None):
    config={**DEFAULTS,**(config or {})}
    events=[event for result in results for event in result['events']]
    groups=[]
    years=sorted({event['year'] for event in events})
    for cohort in ('squeeze','other_breakout'):
        chosen=[event for event in events if event['recent_squeeze'] is (cohort=='squeeze')]
        for year in ['all',*years]:
            for side in ('up','down'):
                for variant in VARIANTS:
                    selected=[event for event in chosen if event['direction']==side and variant in event['variants'] and (year=='all' or event['year']==year)]
                    groups.append({'cohort':cohort,'year':year,'direction':side,'variant':variant,
                        'horizons':{str(horizon):_aggregate(selected,horizon) for horizon in config['horizons']}})
    setup=Counter()
    for result in results:setup.update(result['setup_counts'])
    return {'research_version':VERSION,'config':config,'sampled_instruments':len(results),
            'instruments_with_valid_quotes':sum(result.get('valid_quote_sessions',0)>0 for result in results),
            'instruments_with_known_squeeze':sum(result.get('squeeze_known_sessions',0)>0 for result in results),
            'skipped_instruments':dict(Counter(result['skipped_reason'] for result in results if result['skipped_reason'])),
            'setup_first_direction_counts':dict(setup),'all_deduplicated_band_cross_events':len(events),
            'squeeze_unknown_event_count':sum(event['recent_squeeze'] is None for event in events),
            'squeeze_event_direction_counts':dict(Counter(event['direction'] for event in events if event['recent_squeeze'] is True)),
            'groups':groups}
