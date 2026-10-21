"""Small rolling-high proof: current close must exceed every prior high.

The nearest blocking bar and continuous history length answer any requested
window without storing a window-sized dictionary on every enriched bar.
"""
from bisect import bisect_right
from math import isfinite

MAX_HIGH_LOOKBACK = 750
BREAKOUT_STATE_FIELDS = ('breakout_previous_sessions', 'breakout_blocking_distance', 'breakout_valid', 'new_high_current_close')


def _positive(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value) and value>0


class RollingHighState:
    def __init__(self, calendar=None):
        self.positions={day:i for i,day in enumerate(calendar or [])}
        self.highs=[];self.indices=[];self.index=0;self.available=0;self.previous_day=None

    def observe(self,row,reset=False):
        day=row.get('date');known_day=not self.positions or day in self.positions
        duplicate=self.previous_day is not None and day==self.previous_day
        gap=self.previous_day is not None and self.positions and (self.previous_day not in self.positions or not known_day or self.positions[day]-self.positions[self.previous_day]!=1)
        if reset or gap or duplicate or row.get('corporate_action',False):
            self.highs.clear();self.indices.clear();self.available=0
        close=row.get('close');high=row.get('high')
        valid=bool(known_day and not duplicate and _positive(close) and _positive(high) and high>=close and _positive(row.get('volume')) and row.get('corporate_action_coverage_known') is not False)
        blocker=bisect_right(self.highs,-float(close))-1 if valid else -1
        result={'breakout_previous_sessions':min(self.available,MAX_HIGH_LOOKBACK),
                'breakout_blocking_distance':self.index-self.indices[blocker] if blocker>=0 else None,
                'breakout_valid':valid,'new_high_current_close':float(close) if valid else None}
        if valid:
            while self.highs and self.highs[-1]>=-high:
                self.highs.pop();self.indices.pop()
            self.highs.append(-float(high));self.indices.append(self.index);self.available+=1
        else:
            self.highs.clear();self.indices.clear();self.available=0
        self.index+=1;self.previous_day=day
        return result


def rolling_high_state(rows,calendar,as_of):
    state=RollingHighState(calendar)
    result={'breakout_previous_sessions':0,'breakout_blocking_distance':None,'breakout_valid':False,'new_high_current_close':None}
    if as_of not in calendar:return result
    found=False
    for row in sorted((row for row in rows if row.get('date') and row['date']<=as_of),key=lambda row:row['date']):
        value=state.observe(row)
        if row['date']==as_of:result=value;found=True
    return result if found else {'breakout_previous_sessions':0,'breakout_blocking_distance':None,'breakout_valid':False,'new_high_current_close':None}


def matrix_breakout_states(close,high,counts,end,symbols,volume=None):
    """Vectorized current-day equivalent; only the last 750 prior rows matter."""
    import numpy as np
    current=close[end];current_high=high[end]
    valid=np.isfinite(current)&(current>0)&np.isfinite(current_high)&(current_high>=current)
    valid &= np.isfinite(volume[end])&(volume[end]>0) if volume is not None else False
    start=max(0,end-MAX_HIGH_LOOKBACK)
    prior_high=high[start:end];prior_close=close[start:end]
    available=np.minimum(np.maximum(np.asarray(counts,dtype=np.int64)-1,0),end-start)
    blocking=np.zeros(len(symbols),dtype=np.int64)
    if end>start:
        positions=np.arange(start,end,dtype=np.int64)[:,None]
        invalid=~np.isfinite(prior_high)|(prior_high<=0)|~np.isfinite(prior_close)|(prior_close<=0)|(prior_high<prior_close)
        invalid |= (~np.isfinite(volume[start:end])|(volume[start:end]<=0)) if volume is not None else True
        last_invalid=np.max(np.where(invalid,positions,-1),axis=0)
        available=np.minimum(available,np.where(last_invalid>=0,end-last_invalid-1,end-start))
        last_block=np.max(np.where(prior_high>=current,positions,-1),axis=0)
        blocking=np.where(last_block>=0,end-last_block,0)
    return {symbol:{'breakout_previous_sessions':int(available[i]),
                    'breakout_blocking_distance':int(blocking[i]) if blocking[i] else None,
                    'breakout_valid':bool(valid[i]),'new_high_current_close':float(current[i]) if valid[i] else None}
            for i,symbol in enumerate(symbols)}
