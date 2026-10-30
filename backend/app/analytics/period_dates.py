"""Closed market periods use calendar evidence and never future prices."""
from datetime import date
from calendar import monthrange
from math import isfinite
from functools import lru_cache


def latest_closed_date(calendar,as_of,period):
    if len(calendar)>8192:
        return _latest_closed_date.__wrapped__(calendar,as_of,period)
    return _latest_closed_date(tuple(calendar),as_of,period)


@lru_cache(maxsize=32)
def _latest_closed_date(calendar,as_of,period):
    # Pure calendar evidence only. A changed/reordered calendar makes a new
    # key; no quotation, membership, rule or policy is retained here.
    if period=='D':return as_of if as_of in calendar else None
    result=None
    for position,session in enumerate(calendar):
        if session>as_of:break
        value=date.fromisoformat(session)
        following=calendar[position+1] if position+1<len(calendar) else None
        if period=='W':
            closed=value.weekday()==4 or following and date.fromisoformat(following).isocalendar()[:2]!=value.isocalendar()[:2]
        else:
            end=f'{session[:7]}-{monthrange(value.year,value.month)[1]:02d}'
            closed=end<=as_of or following and following[:7]!=session[:7]
        if closed:result=session
    return result


def completed_indicator_bars(rows,calendar,as_of,period,*,tail=None,source_facts_only=False):
    """One shared stock strategy/backtest series; invalid periods reset seeds."""
    if tail is not None and (not isinstance(tail,int) or isinstance(tail,bool) or tail<1):
        raise ValueError('指标末期数量必须为正整数')
    from .indicators import aggregate_weekly,enrich_bars
    from .monthly import aggregate_monthly,enrich_monthly_bars
    positive=lambda value:isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value) and value>0
    key=lambda day:date.fromisoformat(day).isocalendar()[:2] if period=='W' else day[:7]
    invalid={key(row['date']) for row in rows if not positive(row.get('volume')) or row.get('_volume_known') is False}
    if period=='M':
        periods=aggregate_monthly(rows,calendar,as_of)
        for row in periods:
            if key(row['date']) in invalid:row.update(complete=False,monthly_continuity_reset_reason='月内存在缺失或零成交量报价，指标重新预热')
        if source_facts_only and tail==2:
            from .monthly import _latest_monthly_source_facts
            return _latest_monthly_source_facts(periods)
        result=[row for row in enrich_monthly_bars(periods) if row.get('monthly_indicator_eligible') is True]
        return result[-tail:] if tail is not None else result
    expected={}
    for day in calendar:
        if day<=as_of:expected.setdefault(key(day),day)
    observed={}
    for row in rows:observed.setdefault(key(row['date']),row['date'])
    periods=aggregate_weekly(rows,calendar,as_of);eligible=[];pending=None
    for row in periods:
        week=key(row['date'])
        if not row['complete'] or week in invalid or observed[week]>expected.get(week,observed[week]):
            pending=row.get('weekly_continuity_reset_reason') or '周内日成交报价或首周边界不完整，指标重新预热'
            continue
        if pending:row={**row,'weekly_continuity_reset_reason':pending}
        eligible.append(row);pending=None
    # Raw facts in an explicitly adjusted strategy need only the latest two
    # eligible weeks. Preserve the entire calendar/coverage classification
    # above; no raw EMA/KD seed is used by that facts-only projection.
    return enrich_bars(eligible[-2:] if source_facts_only else eligible,tail=tail)
