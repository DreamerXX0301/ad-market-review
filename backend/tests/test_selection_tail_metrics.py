"""Tail materialization must preserve every value from the original recurrence."""
from copy import deepcopy
from datetime import date, timedelta
from random import Random

import pytest

from app.analytics import boll_conditions as boll, indicators, period_dates
from app.domain import conditions


def quotes(count=600):
    random=Random(7291); day=date(2022,1,3); calendar=[]; rows=[]; close=21.0
    while len(rows)<count:
        if day.weekday()<5:
            observed=day.isoformat(); calendar.append(observed)
            opened=close; close=max(1.0,close+random.uniform(-.8,.8))
            rows.append({'date':observed,'open':opened,'high':max(opened,close)+.35,
                         'low':min(opened,close)-.35,'close':close,
                         'volume':random.randrange(1000,2000),'amount':50000,
                         'corporate_action':False,'corporate_action_coverage_known':True})
        day+=timedelta(days=1)
    return rows,calendar


def damage(rows, fault):
    if fault=='gap':rows.pop(-18)
    elif fault=='duplicate':rows.insert(-18,dict(rows[-18]))
    elif fault=='action':rows[-18]['corporate_action']=True
    elif fault=='unknown_action':rows[-18]['corporate_action_coverage_known']=False
    elif fault=='zero':rows[-18]['volume']=0
    elif fault=='missing_volume':rows[-18]['volume']=None
    elif fault=='invalid_price':rows[-18]['high']=None
    elif fault=='last_zero':rows[-1]['volume']=0
    elif fault=='missing_last':rows.pop()


@pytest.mark.parametrize('tail',[1,2,9,10000])
@pytest.mark.parametrize('fault',['none','gap','duplicate','action','unknown_action','zero'])
def test_tail_is_exact_full_enrichment_suffix_without_mutating_input(tail,fault):
    rows,calendar=quotes();damage(rows,fault);source=deepcopy(rows)
    assert indicators.enrich_bars(rows,calendar,tail=tail)==indicators.enrich_bars(rows,calendar)[-tail:]
    assert rows==source


@pytest.mark.parametrize('period',['D','W','M'])
@pytest.mark.parametrize('fault',['none','gap','duplicate','action','unknown_action','zero',
                                 'missing_volume','invalid_price','last_zero','missing_last'])
def test_complete_strategy_metrics_equal_full_materialization(monkeypatch,period,fault):
    rows,calendar=quotes(950);damage(rows,fault);source=deepcopy(rows)
    config=boll.validate_config({'mode':'breakout_up','require_squeeze':False,'middle_direction':'rising'})
    actual=conditions.metrics_for(rows,calendar[-1],calendar,period,boll_configs=[config],include_higher_lows=True)
    original=indicators.enrich_bars
    # This forces the exact pre-optimization full-series route, including
    # complete weekly eligibility and the unchanged monthly adapter.
    monkeypatch.setattr(indicators,'enrich_bars',lambda rows,calendar=None,**kwargs:original(rows,calendar))
    expected=conditions.metrics_for(rows,calendar[-1],calendar,period,boll_configs=[config],include_higher_lows=True)
    assert actual==expected
    assert rows==source


@pytest.mark.parametrize('period',['D','W','M'])
@pytest.mark.parametrize('fault',['none','gap','duplicate','action','unknown_action','zero',
                                 'missing_volume','invalid_price','last_zero','missing_last'])
def test_latest_boll_proof_equals_original_full_history(period,fault):
    rows,calendar=quotes(600);damage(rows,fault)
    configs=[boll.validate_config({'mode':mode,'band_period':5,'rank_window':10,
                                  'recent_squeeze_periods':3,'slope_periods':2,
                                  'require_expansion':True,'middle_direction':'rising'}) for mode in boll.MODES]
    dated=boll.states_for_bars(rows,calendar,calendar[-1],period,configs)
    latest=boll.latest_states(rows,calendar,calendar[-1],period,configs)
    assert latest==dated[max(dated)]


@pytest.mark.parametrize('tail',[0,-1,True,1.5,'2'])
def test_tail_rejects_unbounded_or_ambiguous_values(tail):
    with pytest.raises(ValueError):indicators.enrich_bars([],tail=tail)


@pytest.mark.parametrize('period',['W','M'])
@pytest.mark.parametrize('tail',[0,-1,True,1.5,'2'])
def test_closed_period_tail_validation_is_consistent(period,tail):
    with pytest.raises(ValueError):period_dates.completed_indicator_bars([],[],None,period,tail=tail)


def test_empty_and_short_seed_are_not_padded():
    assert indicators.enrich_bars([],tail=2)==[]
    rows,calendar=quotes(1)
    assert indicators.enrich_bars(iter(rows),calendar,tail=2)==indicators.enrich_bars(rows,calendar)


@pytest.mark.parametrize('count',[0,1,2,34,35,36,249,250,251])
@pytest.mark.parametrize('reset_offset',[None,1,2,3])
def test_seed_boundaries_and_resets_inside_or_before_tail(count,reset_offset):
    rows,calendar=quotes(count)
    if reset_offset is not None and count>=reset_offset:
        rows[-reset_offset]['corporate_action']=True
    assert indicators.enrich_bars(rows,calendar,tail=2)==indicators.enrich_bars(rows,calendar)[-2:]


@pytest.mark.parametrize('period',['W','M'])
def test_closed_period_tail_keeps_whole_missing_period_and_partial_boundaries(period):
    rows,calendar=quotes(950)
    # Begin in the middle of the first calendar period and omit a complete
    # later month; neither omission may extend warmup through an unknown seed.
    rows=[row for row in rows[3:] if not row['date'].startswith('2023-05')]
    horizon=calendar[-3]
    actual=period_dates.completed_indicator_bars(rows,calendar,horizon,period,tail=2)
    expected=period_dates.completed_indicator_bars(rows,calendar,horizon,period)[-2:]
    assert actual==expected
