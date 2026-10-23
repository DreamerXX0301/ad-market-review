"""Independent formula, causal setup and strict-session BOLL contract checks."""
from copy import deepcopy
from datetime import date, timedelta
from math import sin, sqrt
from statistics import pstdev

import pytest

from app.analytics import boll_conditions as boll
from app.domain import conditions, sector_conditions


def days(count, start='2024-01-01'):
    current = date.fromisoformat(start); result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def bars(values, calendar=None):
    return [{'date': day, 'open': close, 'high': close+1, 'low': close-1,
             'close': close, 'volume': 100, 'amount': 1000,
             'corporate_action': False, 'corporate_action_coverage_known': True}
            for day, close in zip(calendar or days(len(values)), values)]


def config(**values):
    return boll.validate_config({'band_period': 5, 'deviations': 1,
        'rank_window': 10, 'recent_squeeze_periods': 3, **values})


def rule(cfg=None, **extra):
    return {'field': 'boll_signal', 'op': 'matches', 'value': cfg or config(), **extra}


def checked(rows, cfg=None, calendar=None, as_of=None, period='D'):
    calendar = calendar or [row['date'] for row in rows]
    as_of = as_of or calendar[-1]
    cfg = cfg or config()
    metrics = conditions.metrics_for(rows, as_of, calendar, period, boll_configs=[cfg])
    return conditions.evaluate(conditions.validate(rule(cfg, period=period)), metrics)


def test_parameter_contract_defaults_bounds_metadata_and_exact_config_key():
    assert boll.validate_config({}) == boll.DEFAULTS
    assert boll.config_key({}) == boll.config_key(boll.DEFAULTS)
    assert boll.config_key({'deviations': 2}) == boll.config_key({'deviations': 2.0})
    assert boll.config_key({'band_period': 20}) != boll.config_key({'band_period': 21})
    stock = next(row for row in conditions.specs() if row['field'] == 'boll_signal')
    sector = next(row for row in sector_conditions.specs() if row['field'] == 'boll_signal')
    assert stock['supported_periods'] == ['D', 'W', 'M']
    assert stock['historical_event_supported'] is False
    assert stock['default'] == sector['default'] == boll.DEFAULTS
    assert stock['parameters']['rank_window'] == {'type': 'integer', 'min': 10, 'max': 750}
    for period in ('D', 'W', 'M'):
        assert conditions.validate(rule(period=period))['period'] == period
        assert sector_conditions.validate(rule(period=period))['period'] == period


@pytest.mark.parametrize('invalid', [None, 4, [], {'other': 1}, {'mode': 'guess'},
    {'mode': []}, {'middle_direction': 'up'}, {'band_period': True}, {'band_period': 1},
    {'band_period': 251}, {'band_period': 2.0}, {'rank_window': 9}, {'rank_window': 751},
    {'recent_squeeze_periods': 61}, {'recent_squeeze_periods': 0}, {'slope_periods': 0},
    {'slope_periods': 61}, {'deviations': True}, {'deviations': .09}, {'deviations': 11},
    {'deviations': float('nan')}, {'squeeze_percentile': float('inf')},
    {'squeeze_percentile': 0}, {'squeeze_percentile': 101}, {'require_squeeze': 1},
    {'require_expansion': 'true'}])
def test_invalid_configs_are_rejected_consistently(invalid):
    with pytest.raises(ValueError):
        boll.validate_config(invalid)
    with pytest.raises(ValueError):
        conditions.validate(rule(invalid) if invalid else {'field':'boll_signal','op':'matches','value':invalid})
    with pytest.raises(ValueError):
        sector_conditions.validate({'field':'boll_signal','op':'matches','value':invalid})


def test_parameter_fields_are_not_numeric_comparison_or_history_where():
    with pytest.raises(ValueError):
        conditions.validate({'field':'boll_signal','op':'matches','right_field':'close'})
    with pytest.raises(ValueError):
        conditions.validate({'field':'close','op':'>','right_field':'boll_signal'})
    event = {'history_event': {'window': {'from':1, 'to':30},
        'where':rule(), 'then':{'field':'close', 'op':'>', 'value':0}}}
    with pytest.raises(ValueError, match='where'):
        conditions.validate(event)
    event['history_event'].update(where={'field':'volume_ratio','op':'>=','value':3}, then=rule())
    clean = conditions.validate(event)
    assert clean['history_event']['then']['period'] == 'D'
    cfgs = boll.configs_for([{'all':[rule(period='W'), clean, rule(period='W')]}], 'M')
    assert cfgs['D'] == cfgs['W'] == [config()] and cfgs['M'] == []


def test_bands_match_population_std_and_prior_only_linear_quantile():
    values = [100+sin(index*.63)*4+index*.07 for index in range(80)]
    rows = bars(values); calendar = [row['date'] for row in rows]
    cfg = config(mode='squeeze', deviations=1.7, squeeze_percentile=17.5)
    key = boll.config_key(cfg)
    states = boll.states_for_bars(rows, calendar, calendar[-1], 'D', [cfg])
    widths = []
    for index, day in enumerate(calendar):
        proof = states[day][key]['evidence']
        if index < cfg['band_period']-1:
            assert proof['upper'] is None
            continue
        window = values[index-cfg['band_period']+1:index+1]
        middle = sum(window)/len(window); spread = cfg['deviations']*pstdev(window)
        width = 2*spread/middle
        assert proof['middle'] == pytest.approx(middle, rel=1e-12)
        assert proof['upper'] == pytest.approx(middle+spread, rel=1e-12)
        assert proof['lower'] == pytest.approx(middle-spread, rel=1e-12)
        assert proof['bandwidth_pct'] == pytest.approx(width*100, abs=1e-10)
        prior = sorted(widths[-cfg['rank_window']:])
        if len(prior) == cfg['rank_window']:
            position = (len(prior)-1)*.175; low = int(position)
            threshold = prior[low]+(prior[min(low+1,len(prior)-1)]-prior[low])*(position-low)
            assert proof['historical_threshold_pct'] == pytest.approx(threshold*100, abs=1e-10)
            assert states[day][key]['result'] is (width <= threshold)
        else:
            assert proof['historical_threshold_pct'] is None
        widths.append(width)


def test_rolling_population_variance_matches_naive_after_thousands_of_removals():
    # A long series exercises reversible removal, with small variance relative
    # to its price level so cancellation errors cannot hide in broad bands.
    values = [100+sin(index*.117)*.23+sin(index*.031)*.05+index*.0001 for index in range(4000)]
    rows = bars(values); calendar = [row['date'] for row in rows]
    configs = [config(band_period=window,mode='squeeze',deviations=2) for window in (2,20,250)]
    states = boll.states_for_bars(rows,calendar,calendar[-1],'D',configs)
    for cfg in configs:
        key = boll.config_key(cfg)
        for index in range(max(249,cfg['band_period']),len(values),13):
            window = values[index-cfg['band_period']+1:index+1]
            middle = sum(window)/len(window); spread = 2*pstdev(window)
            proof = states[calendar[index]][key]['evidence']
            assert proof['middle'] == pytest.approx(middle,abs=1e-10)
            assert proof['upper'] == pytest.approx(middle+spread,abs=1e-9)
            assert proof['lower'] == pytest.approx(middle-spread,abs=1e-9)


@pytest.mark.parametrize('mode',['breakout_up','breakout_down'])
def test_theoretical_equal_band_touch_never_becomes_cross_from_rounding(mode):
    # With N=2,K=1 every high/low endpoint equals its corresponding band.
    # Both repeatedly increasing and decreasing pairs must stay non-crosses.
    values = [100+sin(index*.117)*.23+sin(index*.031)*.05 for index in range(3000)]
    rows = bars(values); calendar = [row['date'] for row in rows]
    cfg = config(band_period=2,deviations=1,mode=mode,require_squeeze=False)
    states = boll.states_for_bars(rows,calendar,calendar[-1],'D',[cfg])
    key = boll.config_key(cfg)
    assert all(states[day][key]['result'] is False for day in calendar[2:])
    assert states[calendar[-1]][key]['evidence']['comparison_tolerance'] == {'relative':1e-12,'absolute':1e-12}


@pytest.mark.parametrize('mode,last,return_close', [
    ('breakout_up',110,None), ('breakout_down',90,None),
    ('reentry_from_above',110,101), ('reentry_from_below',90,99)])
def test_first_cross_and_reentry_require_two_bands_but_not_disabled_rank(mode,last,return_close):
    values = [100]*5+[last]+([return_close] if return_close else [])
    rows = bars(values)
    cfg = config(mode=mode, require_squeeze=False, rank_window=750)
    proof = checked(rows, cfg)
    assert proof['result'] is True
    assert proof['evidence']['historical_threshold_pct'] is None
    assert boll.warmup_for(cfg) == 6
    if return_close is None:
        rows = bars(values+[last+(.1 if last>100 else -.1)])
        assert checked(rows,cfg)['result'] is False # continuation is not a second cross


def test_current_breakout_does_not_create_prior_squeeze_and_future_is_ignored():
    rows = bars([100]*20+[110,111,112])
    calendar = [row['date'] for row in rows]
    cfg = config(require_squeeze=True)
    proof = checked(rows,cfg,calendar,calendar[20])
    assert proof['result'] is True
    assert proof['evidence']['recent_squeeze_date'] == calendar[19]
    assert proof['evidence']['historical_threshold_pct'] == 0
    assert proof['evidence']['squeeze'] is False
    altered = deepcopy(rows)
    for row in altered[21:]:
        row.update(open=1000,high=1001,low=999,close=1000)
    assert checked(altered,cfg,calendar,calendar[20]) == proof
    # With enough bands but no prior percentile observations, a breakout
    # cannot count itself as its own squeeze.
    assert checked(bars([100]*5+[110]),cfg)['result'] is None


def test_squeeze_ignores_require_squeeze_toggle_and_optional_filters_are_strict():
    rows = bars([100]*20)
    first = checked(rows,config(mode='squeeze',require_squeeze=False))
    second = checked(rows,config(mode='squeeze',require_squeeze=True))
    assert first['result'] is second['result'] is True
    assert first['evidence']['bandwidth_pct'] == first['evidence']['historical_threshold_pct'] == 0
    assert checked(rows,config(mode='squeeze',middle_direction='rising'))['result'] is False
    assert checked(rows,config(mode='squeeze',middle_direction='falling'))['result'] is False
    assert checked(rows,config(mode='squeeze',require_expansion=True))['result'] is False
    breakout = bars([100]*20+[110])
    assert checked(breakout,config(middle_direction='rising',require_expansion=True))['result'] is True
    assert checked(breakout,config(middle_direction='falling'))['result'] is False


@pytest.mark.parametrize('damage', ['missing','duplicate','zero','missing_volume','nan','bad_ohlc',
    'action','unknown_action','missing_coverage','missing_action_flag'])
def test_bad_market_session_resets_signal_unknown_with_bounded_proof(damage):
    rows = bars([100]*20+[110]); calendar = [row['date'] for row in rows]
    target = rows[-2]
    if damage == 'missing': rows.pop(-2)
    if damage == 'duplicate': rows.append(dict(target))
    if damage == 'zero': target['volume'] = 0
    if damage == 'missing_volume': target.pop('volume')
    if damage == 'nan': target['close'] = float('nan')
    if damage == 'bad_ohlc': target['high'] = 80
    if damage == 'action': target['corporate_action'] = True
    if damage == 'unknown_action': target['corporate_action_coverage_known'] = False
    if damage == 'missing_coverage': target.pop('corporate_action_coverage_known')
    if damage == 'missing_action_flag': target.pop('corporate_action')
    proof = checked(rows,config(),calendar)
    assert proof['result'] is None and proof['covered_periods'] == 1
    assert proof['evidence']['last_blocker']['period_end'] == calendar[-2]
    assert len(proof['evidence']['last_blocker']['market_dates']) <= 3
    assert not any(isinstance(item,list) for key,item in proof['evidence'].items() if key!='last_blocker')


def test_period_warmup_recovers_only_after_continuous_new_history():
    rows = bars([100]*45); calendar = [row['date'] for row in rows]
    rows[20]['corporate_action'] = True
    cfg = config(mode='squeeze')
    assert checked(rows,cfg,calendar,calendar[21])['result'] is None
    assert checked(rows,cfg,calendar,calendar[34])['result'] is None
    assert checked(rows,cfg,calendar,calendar[35])['result'] is True
    assert checked(rows,cfg,calendar,calendar[35])['evidence']['last_blocker']['period_end'] == calendar[20]


@pytest.mark.parametrize('period',['W','M'])
def test_whole_closed_periods_and_missing_whole_periods(period):
    calendar = days(500)
    groups = {}
    for day in calendar:
        key = date.fromisoformat(day).isocalendar()[:2] if period=='W' else day[:7]
        groups.setdefault(key,[]).append(day)
    selected = list(groups.values())[:16]
    rows = [row for index,members in enumerate(selected) for row in bars([100 if index<15 else 110]*len(members),members)]
    cfg = config(require_squeeze=False)
    day = selected[-1][-1]
    proof = checked(rows,cfg,calendar,day,period)
    assert proof['result'] is True and proof['observed_date'] == day
    # Future rows do not make the still-open last period eligible.
    earlier = checked(rows,cfg,calendar,selected[-1][0],period)
    assert earlier['result'] is False and earlier['observed_date'] == selected[-2][-1]
    missing = set(selected[-2])
    damaged = [row for row in rows if row['date'] not in missing]
    assert checked(damaged,cfg,calendar,day,period)['result'] is None


def test_unrequested_boll_does_no_work_and_missing_config_cannot_match(monkeypatch):
    rows = bars([100]*20+[110]); calendar = [row['date'] for row in rows]
    monkeypatch.setattr(boll,'latest_states',lambda *args,**kwargs:pytest.fail('old scalar rule computed BOLL'))
    assert 'boll_states' not in conditions.metrics_for(rows,calendar[-1],calendar)
    assert conditions.evaluate(rule(),{'close':110})['result'] is None
