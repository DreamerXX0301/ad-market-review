"""Batch panel outputs must match the dated scalar indicator definitions."""
from datetime import date, timedelta
import math

import numpy as np
import pandas as pd
import pytest

from app.analytics.indicators import WINDOWS, enrich_bars
from app.analytics.panel_inputs import PanelInputs


FIELDS = {
    *(f'ma{window}' for window in WINDOWS),
    'change_pct', 'pct_change', 'volume_ratio', 'volume_multiple_prev', 'return_30d',
    'macd_dif', 'macd_dea', 'macd_hist', 'macd_cross', 'macd_dead_cross',
    'kdj_k', 'kdj_d', 'kdj_j', 'kd_cross', 'kd_dead_cross',
    'price_continuity', 'indicator_warmup_bars', 'indicator_reset_reason',
    'indicator_warmup_reason', 'missing_market_sessions_before',
    'intraday_high_change_pct', 'intraday_high_change_pct_reason',
}
BOOL_FIELDS = {'macd_cross', 'kd_cross', 'macd_dead_cross', 'kd_dead_cross', 'price_continuity'}
INTEGER_FIELDS = {'indicator_warmup_bars', 'missing_market_sessions_before'}


def calendar_days(number=310):
    current=date(2025,1,2);days=[]
    while len(days)<number:
        if current.weekday()<5:days.append(current.isoformat())
        current+=timedelta(days=1)
    return days


def security_rows(symbol,calendar,*,missing=(),actions=(),start=0,end=None,flat=False):
    rows=[]
    for i,day in enumerate(calendar):
        if i<start or (end is not None and i>=end) or i in missing:continue
        close=12.5 if flat else 15+i*.017+math.sin(i*.31)*1.7+math.cos(i*.13)*.4
        volume=0 if i%17 in (0,1) else 1000+i*13+(i%7)*77
        rows.append({'instrument_id':symbol,'date':day,'open':close-.08,
                     'close':close,'high':close if flat else close+.3+(i%3)*.04,
                     'low':close if flat else close-.24-(i%4)*.03,
                     'volume':volume,'amount':close*volume,
                     'corporate_action':i in actions})
    return rows


def assert_scalar_parity(inputs,frame,calendar,as_of):
    actual=inputs.latest(as_of);expected={}
    history=frame.loc[frame.date<=as_of].sort_values(['instrument_id','date'])
    for symbol,group in history.groupby('instrument_id',sort=False):
        if group.iloc[-1]['date']==as_of:
            expected[symbol]=enrich_bars(group.to_dict('records'),calendar)[-1]
    assert set(actual)==set(expected),f'quoted symbol set at {as_of}'
    for symbol,values in actual.items():
        assert set(values)==FIELDS,f'all derived fields for {symbol} at {as_of}'
        for field in FIELDS:
            expected_value=expected[symbol][field];value=values[field]
            context=f'{symbol} {as_of} {field}'
            if expected_value is None:
                assert value is None,context
            elif field in BOOL_FIELDS:
                assert isinstance(value,bool) and value is bool(expected_value),context
            elif field in INTEGER_FIELDS:
                assert isinstance(value,int) and not isinstance(value,bool),context
                assert value==expected_value,context
            elif isinstance(expected_value,str):
                assert value==expected_value,context
            else:
                assert value==pytest.approx(expected_value,rel=1e-10,abs=1e-10),context
    return actual


def test_multiple_securities_gaps_actions_and_late_listing_match_every_derived_field():
    calendar=calendar_days()
    frame=pd.DataFrame([
        *security_rows('600001.SH',calendar),
        *security_rows('000001.SZ',calendar,missing={4,5,41,90,91,92,177,309}),
        *security_rows('300001.SZ',calendar,actions={0,39,40,41,110,225}),
        *security_rows('688001.SH',calendar,start=67,end=286),
    ]).sample(frac=1,random_state=19).reset_index(drop=True)
    inputs=PanelInputs(frame,calendar)
    for position in (0,4,6,8,29,30,33,34,35,39,40,41,42,66,67,90,93,110,144,145,177,178,224,225,249,250,259,260,285,286,309):
        assert_scalar_parity(inputs,frame,calendar,calendar[position])


@pytest.mark.parametrize('offset,ready,cross_ready',[(33,False,False),(34,True,False),(35,True,True)])
@pytest.mark.parametrize('reset_position',[None,43])
def test_macd_kdj_35_and_36_bar_boundaries_after_initial_or_action_reset(offset,ready,cross_ready,reset_position):
    calendar=calendar_days(90)
    frame=pd.DataFrame(security_rows('600001.SH',calendar,actions=() if reset_position is None else (reset_position,)))
    # Isolate the 35/36 warmup boundary from the separate no-trade gate.
    frame['volume']=1000
    frame['amount']=frame.close*1000
    as_of=calendar[(reset_position or 0)+offset]
    values=assert_scalar_parity(PanelInputs(frame,calendar),frame,calendar,as_of)['600001.SH']
    assert values['indicator_warmup_bars']==offset+1
    for field in ('macd_dif','macd_dea','macd_hist','kdj_k','kdj_d','kdj_j'):
        assert (values[field] is not None) is ready
    for field in ('macd_cross','kd_cross','macd_dead_cross','kd_dead_cross'):
        assert (values[field] is not None) is cross_ready
    if not ready:
        assert values['indicator_warmup_reason']==('初始预热：MACD/KDJ需要35根可比报价' if reset_position is None else '公司行动尚未复权，技术指标重新预热')
    else:
        assert values['indicator_warmup_reason'] is None


def test_consecutive_and_separated_actions_override_gap_reason_and_restart_each_time():
    calendar=calendar_days(130)
    frame=pd.DataFrame(security_rows('600001.SH',calendar,missing={49,50},actions={25,26,51,95}))
    inputs=PanelInputs(frame,calendar)
    for position in (24,25,26,27,48,51,52,84,85,86,94,95,129):
        values=assert_scalar_parity(inputs,frame,calendar,calendar[position])['600001.SH']
        if position in (25,26,51,95):
            assert values['indicator_warmup_bars']==1 and values['price_continuity'] is False
            assert values['indicator_reset_reason']=='公司行动尚未复权，技术指标重新预热'
    simultaneous=inputs.latest(calendar[51])['600001.SH']
    assert simultaneous['missing_market_sessions_before']==2


def test_zero_volume_does_not_suppress_prices_or_invent_a_volume_multiple():
    calendar=calendar_days(45)
    frame=pd.DataFrame(security_rows('600001.SH',calendar))
    inputs=PanelInputs(frame,calendar)
    zero=assert_scalar_parity(inputs,frame,calendar,calendar[17])['600001.SH']
    following_zero=assert_scalar_parity(inputs,frame,calendar,calendar[18])['600001.SH']
    recovery=assert_scalar_parity(inputs,frame,calendar,calendar[19])['600001.SH']
    assert zero['volume_ratio']==0 and zero['change_pct'] is not None
    assert following_zero['volume_ratio'] is None and recovery['volume_ratio'] is None
    assert following_zero['price_continuity'] and recovery['price_continuity']


def test_ma250_boundary_and_post_gap_rewarm_are_calendar_consistent():
    calendar=calendar_days(295)
    frame=pd.DataFrame([
        *security_rows('600001.SH',calendar),
        *security_rows('000001.SZ',calendar,missing={19}),
    ])
    inputs=PanelInputs(frame,calendar)
    for position in (248,249,250,267,268,269,270,294):
        actual=assert_scalar_parity(inputs,frame,calendar,calendar[position])
        if position==248:assert actual['600001.SH']['ma250'] is None
        if position==249:assert actual['600001.SH']['ma250'] is not None
        if position==268:assert actual['000001.SZ']['ma250'] is None
        if position==269:assert actual['000001.SZ']['ma250'] is not None


def test_kdj_flat_price_window_is_fifty_and_crosses_remain_false():
    calendar=calendar_days(75)
    frame=pd.DataFrame(security_rows('600001.SH',calendar,flat=True))
    inputs=PanelInputs(frame,calendar)
    for position in (8,33,34,35,74):
        actual=assert_scalar_parity(inputs,frame,calendar,calendar[position])['600001.SH']
        if position>=34:
            assert actual['kdj_k']==actual['kdj_d']==actual['kdj_j']==50
            assert actual['macd_dif']==actual['macd_dea']==actual['macd_hist']==0
        if position>=35:assert actual['kd_cross'] is False and actual['macd_cross'] is False


def test_as_of_is_order_independent_and_excludes_future_values_actions_and_new_symbols():
    calendar=calendar_days(285)
    frame=pd.DataFrame([
        *security_rows('600001.SH',calendar,actions={245,246,280}),
        *security_rows('000001.SZ',calendar,missing={70,71,275}),
        *security_rows('688001.SH',calendar,start=260),
    ])
    future=frame.date>calendar[250]
    frame.loc[future,['close','high','low','volume']]*=10000
    inputs=PanelInputs(frame,calendar)
    for position in (250,34,249,8,284,70,35,250):
        as_of=calendar[position]
        actual=assert_scalar_parity(inputs,frame,calendar,as_of)
        truncated=frame.loc[frame.date<=as_of].copy()
        assert actual==PanelInputs(truncated,calendar).latest(as_of)
        if position<260:assert '688001.SH' not in actual
    assert '000001.SZ' not in inputs.latest(calendar[70])


def test_calendar_mismatch_is_rejected_before_array_assignment():
    calendar=calendar_days(10)
    frame=pd.DataFrame(security_rows('600001.SH',calendar))
    with pytest.raises(ValueError,match='交易日历以外'):
        PanelInputs(frame,calendar[:-1])


def test_checkpoint_neighbor_reuse_and_past_future_roundtrips_keep_scalar_values():
    calendar=calendar_days(410)
    frame=pd.DataFrame([
        *security_rows('600001.SH',calendar,actions={63,64,128,257}),
        *security_rows('000001.SZ',calendar,missing={62,190,191,192,333}),
        *security_rows('688001.SH',calendar,start=130),
    ])
    frame.loc[frame.date>calendar[320],['close','high','low','volume']]*=10000
    inputs=PanelInputs(frame,calendar)
    assert_scalar_parity(inputs,frame,calendar,calendar[300])
    assert inputs.last_checkpoint_position is None and inputs.last_compute_steps==301
    assert_scalar_parity(inputs,frame,calendar,calendar[301])
    assert inputs.last_checkpoint_position==300 and inputs.last_compute_steps==1
    assert_scalar_parity(inputs,frame,calendar,calendar[299])
    assert inputs.last_checkpoint_position==255 and inputs.last_compute_steps==44
    for position in (128,127,256,33,300,409,191,193,35,301):
        assert_scalar_parity(inputs,frame,calendar,calendar[position])
        assert inputs.last_checkpoint_position is None or inputs.last_checkpoint_position<position
        assert len(inputs._checkpoints)<=inputs.MAX_CHECKPOINTS


def test_checkpoints_are_bounded_lru_and_equal_or_future_day_state_is_never_restored():
    calendar=calendar_days(20)
    frame=pd.DataFrame(security_rows('600001.SH',calendar))
    inputs=PanelInputs(frame,calendar)
    inputs.CHECKPOINT_INTERVAL=2
    inputs.MAX_CHECKPOINTS=3
    assert_scalar_parity(inputs,frame,calendar,calendar[7])
    assert list(inputs._checkpoints)==[3,5,7]
    assert_scalar_parity(inputs,frame,calendar,calendar[6])
    assert inputs.last_checkpoint_position==5 and inputs.last_compute_steps==1
    assert list(inputs._checkpoints)==[7,5,6], 'recently used day 5 survives while older day 3 is evicted'
    assert_scalar_parity(inputs,frame,calendar,calendar[4])
    assert inputs.last_checkpoint_position is None and inputs.last_compute_steps==5
    assert len(inputs._checkpoints)==3
    assert_scalar_parity(inputs,frame,calendar,calendar[4])
    assert inputs.last_checkpoint_position==3 and inputs.last_compute_steps==1, 'same-day checkpoint cannot supply its own pre-crossing state'


def test_restored_and_saved_checkpoint_arrays_never_alias_mutating_work_state_or_results():
    calendar=calendar_days(235)
    frame=pd.DataFrame(security_rows('600001.SH',calendar,actions={205}))
    inputs=PanelInputs(frame,calendar)
    inputs.latest(calendar[200])
    checkpoint=inputs._checkpoints[200]
    frozen=inputs._copy_state(checkpoint)
    output=assert_scalar_parity(inputs,frame,calendar,calendar[201])
    assert inputs.last_checkpoint_position==200
    newer=inputs._checkpoints[201]
    for key,value in frozen.items():
        if key=='sums':
            for window,total in value.items():
                np.testing.assert_array_equal(checkpoint[key][window],total)
                assert not np.shares_memory(checkpoint[key][window],newer[key][window])
        else:
            np.testing.assert_array_equal(checkpoint[key],value)
            assert not np.shares_memory(checkpoint[key],newer[key])
    output['600001.SH']['ma5']=-10000
    assert_scalar_parity(inputs,frame,calendar,calendar[201])


def test_checkpoint_replay_keeps_action_gap_and_35_36_boundaries_including_an_empty_date():
    calendar=calendar_days(280)
    frame=pd.DataFrame(security_rows('600001.SH',calendar,actions={64,128,129},missing={170,171}))
    inputs=PanelInputs(frame,calendar)
    for position in (63,64,127,128,129,161,162,163,164,169,170,171,172,204,205,206,207,249,250,279):
        actual=assert_scalar_parity(inputs,frame,calendar,calendar[position])
        if position in (170,171):assert actual=={}
        if position==172:
            assert actual['600001.SH']['indicator_warmup_bars']==1
            assert actual['600001.SH']['missing_market_sessions_before']==2


@pytest.mark.parametrize('side',['current','previous'])
@pytest.mark.parametrize('bad_volume',[0,-1,float('nan')])
def test_dead_cross_requires_current_and_previous_positive_finite_volume_after_checkpoint(side,bad_volume):
    calendar=calendar_days(110)
    rows=security_rows('600001.SH',calendar)
    for row in rows:row['volume']=1000
    baseline=enrich_bars(rows,calendar)
    crossing=next(index for index,row in enumerate(baseline) if row['kd_dead_cross'] is True)
    rows[crossing if side=='current' else crossing-1]['volume']=bad_volume
    frame=pd.DataFrame(rows);inputs=PanelInputs(frame,calendar)
    inputs.latest(calendar[crossing-1])
    if bad_volume==0:
        actual=assert_scalar_parity(inputs,frame,calendar,calendar[crossing])['600001.SH']
    else:
        # Legacy scalar ratio may preserve a negative/NaN input while panel
        # serialization returns None; this change does not redefine that field.
        actual=inputs.latest(calendar[crossing])['600001.SH']
        scalar=enrich_bars(rows[:crossing+1],calendar)[-1]
        assert actual['kd_dead_cross']==scalar['kd_dead_cross']
        assert actual['macd_dead_cross']==scalar['macd_dead_cross']
    assert inputs.last_checkpoint_position==crossing-1 and inputs.last_compute_steps==1
    assert actual['kd_dead_cross'] is None and actual['macd_dead_cross'] is None
