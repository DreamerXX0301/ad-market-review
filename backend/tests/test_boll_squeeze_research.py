"""Causal BOLL study labels: controlled fixtures only, never product data."""
from datetime import date,timedelta
from copy import deepcopy

import pytest

from app.analytics.boll_squeeze_research import prepare,study_symbol,forward_label,failure_labels,aggregate


def calendar(count):
    current=date(2024,1,2);result=[]
    while len(result)<count:
        if current.weekday()<5:result.append(current.isoformat())
        current+=timedelta(days=1)
    return result


def rows(prices,days,volumes=None):
    return [{'date':day,'open':price,'high':price+1,'low':price-1,'close':price,
             'volume':volumes[index] if volumes else 100,'amount':10000}
            for index,(day,price) in enumerate(zip(days,prices))]


def test_current_width_is_excluded_from_historical_threshold_and_uses_population_sd():
    days=calendar(12);prices=[10.0]*12;prices[7]=11
    values=prepare(rows(prices,days),days,[],{'band_period':3,'rank_window':4,'squeeze_quantile':1.0})
    value=values[7]
    assert value['historical_quantile']==0
    assert value['squeeze'] is False,'using the current width in its own maximum would falsely mark this a squeeze'
    assert value['middle']==pytest.approx(31/3)
    assert value['upper']-value['middle']==pytest.approx(2*((2/9)**.5))


def test_future_prices_do_not_rewrite_prior_band_percentile_or_signal():
    days=calendar(240);prices=[100.0]*145+[110.0]*35+[80.0]*60
    source=rows(prices,days,[100.0]*145+[200.0]+[100.0]*94)
    original=prepare(source,days,[])
    changed=deepcopy(source)
    for row in changed[180:]:
        for field in ('open','high','low','close'):row[field]*=10
    later=prepare(changed,days,[])
    assert original[:180]==later[:180]
    initial=study_symbol('FIXTURE',source,days,[])
    revised=study_symbol('FIXTURE',changed,days,[])
    labels=('outcomes','false_breakout_within_5','false_breakout_reason','strict_failure_within_5','strict_failure_reason')
    left=[{key:value for key,value in event.items() if key not in labels} for event in initial['events'] if event['event_date']<days[180]]
    right=[{key:value for key,value in event.items() if key not in labels} for event in revised['events'] if event['event_date']<days[180]]
    assert left==right and left


def test_forward_return_starts_at_next_exact_market_open():
    days=calendar(8)
    values=[{'date':day,'row':{'open':20,'close':25,'low':19},'action':False} for day in days]
    values[2]['row']={'open':9,'close':10,'low':8}
    result=forward_label(values,2,5,'up')
    assert result['entry_date']==days[3] and result['exit_date']==days[7]
    assert result['return_pct']==25
    assert result['minimum_low_return_pct']==pytest.approx(-5)
    assert result['close_path_drawdown_pct']==0


@pytest.mark.parametrize('failure,expected', [('missing','missing_or_invalid_market_session_quote'),('action','known_corporate_action_in_forward_window'),('tail','right_censored')])
def test_missing_quotes_actions_and_right_censoring_stay_unknown(failure,expected):
    days=calendar(10)
    values=[{'date':day,'row':{'open':10,'close':11,'low':9},'action':False} for day in days]
    if failure=='missing':values[4]['row']=None
    elif failure=='action':values[4]['action']=True
    else:values=values[:5]
    result=forward_label(values,2,5,'up')
    assert result['return_pct'] is None and result['reason']==expected


def test_gap_or_action_resets_both_band_and_rank_warmup():
    days=calendar(24);source=rows([10.0]*24,days)
    config={'band_period':3,'rank_window':4}
    values=prepare(source,days,[days[10]],config)
    assert values[9]['squeeze'] is True
    assert values[10]['middle'] is None
    assert all(value['squeeze'] is None for value in values[10:16])
    assert values[16]['squeeze'] is True
    missing=prepare([row for row in source if row['date']!=days[10]],days,[],config)
    assert missing[10]['row'] is None and missing[11]['middle'] is None
    assert missing[17]['squeeze'] is True


def test_event_cooldown_is_before_outcomes_and_all_filters_are_same_event_subsets():
    days=calendar(260);prices=[100.0]*145+[110.0]*35+[80.0]*35+[100.0]*45
    source=rows(prices,days,[100.0]*145+[200.0]+[100.0]*114)
    study=study_symbol('FIXTURE',source,days,[])
    events=study['events'];assert len(events)>=2
    positions={day:index for index,day in enumerate(days)}
    assert all(positions[b['event_date']]-positions[a['event_date']]>=20 for a,b in zip(events,events[1:]))
    assert all(event['variants'][0]=='base' for event in events)
    assert all('volume_slope' in event['variants'] for event in events if 'volume_slope_macd' in event['variants'] or 'volume_slope_kdj' in event['variants'])
    # Removing forward quotes must not make a future-informed different entry.
    shorter=study_symbol('FIXTURE',source[:150],days,[])
    assert shorter['events'][0]['event_date']==events[0]['event_date']
    assert shorter['events'][0]['outcomes']['20']['return_pct'] is None


def test_invalid_action_dates_are_explicit_instrument_gaps_and_empty_stats_are_null():
    days=calendar(200)
    for action in (None,'bad-date'):
        result=study_symbol('FIXTURE',rows([10.0]*200,days),days,[action])
        assert result['skipped_reason']=='invalid_corporate_action_date'
        report=aggregate([result])
        assert report['skipped_instruments']=={'invalid_corporate_action_date':1}
        assert all(group['horizons']['20']['mean_return_pct'] is None for group in report['groups'])


def test_reentry_is_not_strict_failure_and_strict_extreme_is_not_inclusive():
    days=calendar(6)
    values=[{'date':day,'row':{'open':104,'high':105,'low':100,'close':104},'upper':106,'lower':98,'middle':102,'action':False} for day in days]
    assert failure_labels(values,0,'up')=={'reentry':True,'strict_failure':False,'reason':None}
    values[2]['row']['close']=100
    assert failure_labels(values,0,'up')['strict_failure'] is False,'touching the event low is not a strict break'
    values[2]['row']['close']=99
    assert failure_labels(values,0,'up')['strict_failure'] is True
    values[2]['row']['close']=106
    assert failure_labels(values,0,'down')['strict_failure'] is True
    values[3]['action']=True
    assert failure_labels(values,0,'down')['strict_failure'] is None


def test_strict_failure_predicates_must_hold_on_the_same_future_session():
    days=calendar(6)
    values=[{'date':day,'row':{'open':104,'high':105,'low':100,'close':104},'upper':106,'lower':98,'middle':102,'action':False} for day in days]
    values[1]['row']['close']=99;values[1]['middle']=98
    values[2]['row']['close']=101;values[2]['middle']=102
    assert failure_labels(values,0,'up')['strict_failure'] is False
