"""Batch-local normalization preserves every scalar metric and strict proof."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from app.domain import conditions
from app.services import strategies, strategy_periods as summaries
from test_boll_signal_integration import breakout_rows, config, leaf as boll_leaf


@pytest.fixture(autouse=True)
def clear_summaries():
    summaries.clear_metric_cache()
    yield
    summaries.clear_metric_cache()


def fingerprint(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def source_rows(fault):
    rows,calendar=breakout_rows(120)
    day=calendar[-1]
    if fault=='missing_volume':
        rows[-1].pop('volume');rows[-4]['volume']=None
    elif fault=='missing_ohlc':
        rows[-4].pop('open');rows[-7]['close']=None
    elif fault=='duplicate':rows.append(dict(rows[-4]))
    elif fault=='future':
        day=calendar[-4]
        for row in rows[-3:]:row.update(close=50000,high=50001,volume=50000,corporate_action=True)
    elif fault=='unknown_action':
        # Normalization deliberately retains the legacy default while marking
        # strict structures unknown. Normalizing that result twice loses it.
        rows[-4].pop('corporate_action_coverage_known')
        rows[-6].pop('corporate_action')
    elif fault=='action':rows[-4]['corporate_action']=True
    elif fault=='zero_volume':rows[-4]['volume']=0
    return list(reversed(rows)),calendar,day


@pytest.mark.parametrize('period',['D','W','M'])
@pytest.mark.parametrize('fault',['none','missing_volume','missing_ohlc','duplicate','future','unknown_action','action','zero_volume'])
def test_prepared_rows_keep_exact_scalar_payloads_and_reasons(period,fault):
    rows,calendar,day=source_rows(fault)
    before=deepcopy(rows)
    expected=conditions.metrics_for(rows,day,calendar,period,include_higher_lows=True,boll_configs=[config()])
    prepared=conditions.prepare_metric_bars(rows)
    actual=conditions.metrics_for(prepared,day,calendar,period,include_higher_lows=True,boll_configs=[config()])
    assert actual==expected
    assert fingerprint(actual)==fingerprint(expected)
    assert rows==before and prepared==before


@pytest.mark.parametrize('higher,bands',[(False,False),(True,False),(False,True),(True,True)])
def test_prepared_seed_is_unchanged_by_missing_volume_adaptation_and_result_mutations(higher,bands):
    rows,calendar,day=source_rows('missing_volume')
    prepared=conditions.prepare_metric_bars(rows)
    options={'include_higher_lows':higher,'boll_configs':[config()] if bands else None}
    expected={period:conditions.metrics_for(rows,day,calendar,period,**options) for period in ('D','W','M')}
    first=conditions.metrics_for(prepared,day,calendar,'D',**options)
    first['close']=-1
    if higher:first['higher_lows_state']['recent_lows'].append({'price':-1})
    if bands:first['boll_states'].clear()
    for period in ('M','W','D'):
        assert conditions.metrics_for(prepared,day,calendar,period,**options)==expected[period]
    normalized=conditions.normalized_metric_rows(prepared,day)
    assert normalized[-1]['volume'] is None
    assert all('_volume_known' not in row for row in normalized)
    normalized[-1].update(volume=999,amount=-1,_higher_lows_action_coverage_known=False)
    assert conditions.metrics_for(prepared,day,calendar,'D',**options)==expected['D']


def test_prepared_wrapper_snapshots_raw_inputs_but_keeps_raw_context_iteration():
    rows,calendar,day=source_rows('unknown_action')
    prepared=conditions.prepare_metric_bars(rows)
    expected=conditions.metrics_for(rows,day,calendar,include_higher_lows=True,boll_configs=[config()])
    raw=deepcopy(rows)
    rows[0].update(close=999,corporate_action=True)
    rows.append(dict(rows[0]))
    assert prepared==raw
    assert conditions.prepare_metric_bars(prepared) is prepared
    assert conditions.metrics_for(prepared,day,calendar,include_higher_lows=True,boll_configs=[config()])==expected
    normalized=conditions.normalized_metric_rows(prepared,day)
    unknown=next(row for row in normalized if row['date']==calendar[-4])
    assert unknown['corporate_action_coverage_known'] is True
    assert unknown['_higher_lows_action_coverage_known'] is False
    assert unknown['_boll_action_coverage_known'] is False


def test_dwm_batch_normalizes_once_and_new_structure_configs_reuse_the_same_seed(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path))
    rows,calendar,day=source_rows('unknown_action')
    definition={'all':[{'field':'close','op':'>','value':0,'period':'W'},
        {'field':'close','op':'>','value':0,'period':'M'},boll_leaf(),
        {'field':'higher_lows','op':'confirmed_rising_lows','value':8,'period':'D'}]}
    plan=summaries.compile_metric_plan(definition)
    expected={period:conditions.metrics_for(rows,day,calendar,period,
        include_higher_lows=plan.requests[period].include_higher_lows,
        boll_configs=plan.requests[period].boll_configs) for period in plan.periods}
    calls=[];normalizer=conditions.normalized_bar
    def observed(row):
        calls.append(row)
        return normalizer(row)
    monkeypatch.setattr(conditions,'normalized_bar',observed)
    class Market:
        def condition_histories(self,symbols,end):return {symbol:rows for symbol in symbols}
    histories,ready,_=strategies.selection_metric_batch(Market(),['one'],day,plan,'fixed')
    assert not calls, 'a batch wrapper does not eagerly normalize fallback-only histories'
    bars=histories['one']
    args={'instrument_id':'one','generation':'fixed'}
    base=summaries.cached_metrics(bars,day,calendar,'D',request=plan.requests['D'],**args)
    actual=summaries.add_period_metrics(base,bars,day,calendar,definition,plan=plan,ready=ready['one'],**args)
    assert actual['_period_metrics']==expected
    assert len(calls)==len(rows), 'three period calculations normalize the original seed once'
    late=summaries.cached_metrics(bars,day,calendar,'D',include_higher_lows=True,
        boll_configs=[config(deviations=3)],**args)
    assert len(calls)==len(rows), 'a missing structural config uses untouched prepared rows'
    monkeypatch.setattr(conditions,'normalized_bar',normalizer)
    assert late==conditions.metrics_for(rows,day,calendar,include_higher_lows=True,boll_configs=[config(deviations=3)])
    assert all(not isinstance(value,list) for value in summaries._cache.values())
