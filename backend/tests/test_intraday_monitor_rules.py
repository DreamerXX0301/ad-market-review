"""Sampled intraday states; fixtures are synthetic and never product data."""
from copy import deepcopy
from datetime import datetime

import pytest

from app.services import intraday_monitor_rules as rules


DAY = '2026-11-13'
SYMBOL = '002187.SZ'


def stamp(slot, day=DAY):
    return int(datetime.fromisoformat(f'{day}T{slot}:05+08:00').timestamp() * 1000)


def quote(price, slot, **extra):
    return {'instrument_id': SYMBOL, 'latest_price': price,
            'received_at_ms': stamp(slot) + 1000, 'source_time_ms': stamp(slot),
            'quality': 'batch_time_only', 'valid': True, 'reason': None,
            'price_basis': 'none', **extra}


def level(kind='support', lower=9.95, upper=10.05, **extra):
    return {'key': 'manual:one:1', 'kind': kind, 'source_kind': 'manual_line',
            'source_id': 'one', 'revision': 1, 'label': '人工支撑',
            'lower': lower, 'upper': upper, 'baseline_date': '2026-11-12',
            'price_basis': 'none', 'evidence': {'saved_price': 10}, **extra}


def step(levels, price, slot, state=None, **extra):
    return rules.evaluate_snapshot(levels, quote(price, slot, **extra), state,
                                   trade_date=DAY, slot_key=slot)


def types(result):
    return [(item['type'], item['phase']) for item in result['observations']]


def test_first_sample_seeds_without_using_cumulative_high_low_or_claiming_a_cross():
    levels = [level('resistance', lower=10, upper=10)]
    original = deepcopy(levels)
    result = step(levels, 10.2, '09:30', high=11, low=9)
    assert result['unknowns'] == [] and result['observations'] == []
    assert result['state']['levels'][levels[0]['key']]['position'] == 'above'
    assert result['state']['continuity'] == 'reseeded'
    again = step(levels, 10.3, '09:40', result['state'], high=11, low=9)
    assert again['observations'] == []
    assert levels == original


def test_support_touch_then_reclaim_and_holding_keep_one_episode_and_full_evidence():
    levels = [level()]
    first = step(levels, 10.2, '09:30')
    touched = step(levels, 10, '09:40', first['state'])
    assert types(touched) == [('touch_support', 'observed')]
    reclaimed = step(levels, 10.1, '09:50', touched['state'])
    assert types(reclaimed) == [('support_reclaim', 'observed')]
    held = step(levels, 10.2, '10:00', reclaimed['state'])
    assert types(held) == [('support_reclaim', 'holding')]
    assert {item['episode'] for result in (touched, reclaimed, held) for item in result['observations']} == {'09:40'}
    event = reclaimed['observations'][0]
    assert event['sampling_only'] is True
    assert event['evidence']['touch_observed'] is True
    assert event['evidence']['approach'] == 'above'
    assert event['evidence']['previous_sample']['latest_price'] == 10
    assert event['evidence']['current_sample']['latest_price'] == 10.1
    assert event['source_time_ms'] == stamp('09:50')
    assert event['observed_at_ms'] == stamp('09:50') + 1000
    assert event['evidence']['level_evidence'] == {'saved_price': 10}


def test_direct_breakdown_then_jump_reclaim_does_not_invent_an_observed_touch():
    levels = [level()]
    first = step(levels, 10.2, '09:30')
    lost = step(levels, 9.8, '09:40', first['state'])
    assert types(lost) == [('support_breakdown', 'observed')]
    recovered = step(levels, 10.2, '09:50', lost['state'])
    assert types(recovered) == [('support_reclaim', 'observed')]
    assert recovered['observations'][0]['evidence']['touch_observed'] is False
    assert recovered['observations'][0]['evidence']['approach'] == 'below'


def test_pressure_breakout_holding_fallback_and_new_breakout_episode():
    levels = [level('resistance', lower=10, upper=10)]
    current = step(levels, 9.8, '09:30')
    expected = [
        (10.2, '09:40', 'pressure_breakout', 'observed', '09:40'),
        (10.3, '09:50', 'pressure_breakout', 'holding', '09:40'),
        (9.8, '10:00', 'pressure_fallback', 'observed', '09:40'),
        (10.2, '10:10', 'pressure_breakout', 'observed', '10:10'),
    ]
    for price, slot, kind, phase, episode in expected:
        current = step(levels, price, slot, current['state'])
        assert types(current) == [(kind, phase)]
        assert current['observations'][0]['episode'] == episode


def test_risk_has_only_breakdown_observations_and_rearms_after_recovery():
    levels = [level('risk', lower=9.5, upper=9.5)]
    current = step(levels, 10.2, '09:30')
    current = step(levels, 9.4, '09:40', current['state'])
    assert types(current) == [('risk_breakdown', 'observed')]
    current = step(levels, 9.3, '09:50', current['state'])
    assert types(current) == [('risk_breakdown', 'holding')]
    current = step(levels, 9.6, '10:00', current['state'])
    assert current['observations'] == []
    current = step(levels, 9.4, '10:10', current['state'])
    assert types(current) == [('risk_breakdown', 'observed')]
    assert current['observations'][0]['episode'] == '10:10'


def test_pressure_fallback_inside_then_breakout_starts_a_new_episode():
    levels = [level('resistance')]
    current = step(levels, 9.8, '09:30')
    current = step(levels, 10.2, '09:40', current['state'])
    assert current['observations'][0]['episode'] == '09:40'
    current = step(levels, 10, '09:50', current['state'])
    assert types(current) == [('pressure_fallback', 'observed')]
    current = step(levels, 10.2, '10:00', current['state'])
    assert types(current) == [('pressure_breakout', 'observed')]
    assert current['observations'][0]['episode'] == '10:00'


def test_lunch_slots_are_continuous_but_a_missed_slot_reseeds_without_false_cross():
    assert len(rules.SLOTS) == 25
    levels = [level('resistance', lower=10, upper=10)]
    morning = step(levels, 9.8, '11:30')
    afternoon = step(levels, 10.2, '13:10', morning['state'])
    assert types(afternoon) == [('pressure_breakout', 'observed')]
    assert afternoon['state']['continuity'] == 'continuous'
    first = step(levels, 9.8, '09:30')
    missed = step(levels, 10.2, '09:50', first['state'])
    assert missed['observations'] == []
    assert missed['state']['continuity'] == 'reseeded'


def test_changed_level_is_initialized_while_unchanged_levels_continue():
    manual = level('resistance', lower=10, upper=10)
    automatic = level('resistance', lower=10, upper=10, key='auto:one:1',
                      source_kind='automatic_zone', source_id='zone-one')
    first = step([manual, automatic], 9.8, '09:30')
    changed = {**manual, 'key': 'manual:one:2', 'revision': 2}
    result = step([changed, automatic], 10.2, '09:40', first['state'])
    assert [item['rule_key'] for item in result['observations']] == ['auto:one:1']
    assert set(result['state']['levels']) == {'manual:one:2', 'auto:one:1'}
    same_key_changed_price = step([{**manual, 'lower': 9.9, 'upper': 9.9}], 10.2, '09:40', first['state'])
    assert same_key_changed_price['observations'] == []


def test_narrow_zones_still_have_inside_positions_and_exterior_buffer_is_neutral():
    levels = [level(lower=10, upper=10.001)]
    first = step(levels, 10.2, '09:30')
    inside = step(levels, 10.0005, '09:40', first['state'])
    assert types(inside) == [('touch_support', 'observed')]
    neutral = step(levels, 10.005, '09:50', inside['state'])
    assert neutral['observations'] == []
    current = neutral['state']['levels'][levels[0]['key']]
    assert current['position'] == 'inside' and current['observed_position'] == 'buffer'
    assert current['buffer'] == pytest.approx(10.0005 * 0.001)
    clear = step(levels, 10.02, '10:00', neutral['state'])
    assert types(clear) == [('support_reclaim', 'observed')]


@pytest.mark.parametrize('patch', [
    {'valid': False, 'reason': '该股没有新鲜报价'},
    {'latest_price': None}, {'latest_price': float('nan')}, {'latest_price': True},
    {'price_basis': 'forward_event_affine'}, {'trade_date': '2026-11-12'},
    {'received_at_ms': stamp('09:40', day='2026-11-12')},
    {'source_time_ms': stamp('09:40', day='2026-11-12')},
    {'source_time_ms': None, 'quality': 'quote_time'},
    {'source_time_ms': stamp('09:50')},
    {'source_time_ms': stamp('09:30')},
    {'instrument_id': '002187'},
])
def test_bad_or_stale_quote_keeps_prior_valid_state_and_leaves_gap(patch):
    levels = [level('resistance', lower=10, upper=10)]
    first = step(levels, 9.8, '09:30')
    original = deepcopy(first['state'])
    invalid = step(levels, 10.2, '09:40', first['state'], **patch)
    assert invalid['observations'] == [] and invalid['unknowns']
    assert invalid['state'] == original and first['state'] == original
    resumed = step(levels, 10.2, '09:50', invalid['state'])
    assert resumed['observations'] == []


def test_batch_only_time_can_be_null_but_quote_time_is_retained_when_available():
    levels = [level('resistance', lower=10, upper=10)]
    first = step(levels, 9.8, '09:30', source_time_ms=None)
    second = step(levels, 10.2, '09:40', first['state'], source_time_ms=None)
    assert types(second) == [('pressure_breakout', 'observed')]
    assert second['observations'][0]['source_time_ms'] is None
    assert second['observations'][0]['evidence']['quality'] == 'batch_time_only'
    third = step(levels, 10.3, '09:50', second['state'], quality='quote_time')
    assert third['observations'][0]['evidence']['quality'] == 'quote_time'


@pytest.mark.parametrize('skew,accepted', [(0, True), (5000, True), (5001, False)])
def test_batch_clock_skew_matches_capture_tolerance_without_rewriting_source_time(skew, accepted):
    levels = [level('resistance', lower=10, upper=10)]
    first = step(levels, 9.8, '09:30')
    received = stamp('09:40') + 1000
    result = step(levels, 10.2, '09:40', first['state'], source_time_ms=received + skew)
    if accepted:
        assert types(result) == [('pressure_breakout', 'observed')]
        assert result['observations'][0]['source_time_ms'] == received + skew
        assert result['observations'][0]['evidence']['current_sample']['quality'] == 'batch_time_only'
        assert result['observations'][0]['evidence']['parameters']['source_clock_skew_ms'] == 5000
    else:
        assert result['observations'] == [] and result['unknowns']


def test_replay_reversed_slot_and_day_or_stock_changes_cannot_reuse_old_path():
    levels = [level('resistance', lower=10, upper=10)]
    first = step(levels, 9.8, '09:40')
    for slot in ('09:40', '09:30'):
        result = step(levels, 10.2, slot, first['state'])
        assert result['observations'] == [] and result['unknowns']
        assert result['state'] == first['state']
    another_stock = step(levels, 10.2, '09:50', first['state'], instrument_id='600001.SH')
    assert another_stock['observations'] == []
    tomorrow = rules.evaluate_snapshot(levels, quote(10.2, '09:50', trade_date='2026-11-16',
        received_at_ms=stamp('09:50', day='2026-11-16') + 1000,
        source_time_ms=stamp('09:50', day='2026-11-16')), first['state'], trade_date='2026-11-16', slot_key='09:50')
    assert tomorrow['observations'] == []


def test_invalid_duplicate_and_incomparable_levels_are_unknown_without_losing_valid_ones():
    good = level('resistance', lower=10, upper=10, key='good')
    invalid = [level(key=['bad']), None, level(key='bad-range', lower=11, upper=10),
               level(key='future', baseline_date='2026-11-16'),
               level(key='adjusted', price_basis='forward_event_affine')]
    duplicated = level(key='duplicated')
    first = step([good], 9.8, '09:30')
    result = step([good, *invalid, duplicated, duplicated], 10.2, '09:40', first['state'])
    assert [item['rule_key'] for item in result['observations']] == ['good']
    assert {item['rule_key'] for item in result['unknowns']} == {None, 'bad-range', 'future', 'adjusted', 'duplicated'}
    assert set(result['state']['levels']) == {'good'}


def observation(key, *, lower=10, upper=10, **extra):
    return {'instrument_id': SYMBOL, 'trade_date': DAY, 'slot_key': '09:40',
            'type': 'pressure_breakout', 'phase': 'observed', 'episode': '09:40',
            'observed_at_ms': stamp('09:40') + 1000, 'source_time_ms': stamp('09:40'),
            'price': 10.2, 'lower': lower, 'upper': upper,
            'source_kind': 'manual_line', 'source_id': key, 'rule_key': key,
            'label': key, 'title': '盘中采样越过压力', 'revision': 1,
            'sampling_only': True, 'evidence': {'source': key}, **extra}


def test_merge_keeps_manual_and_automatic_times_revisions_and_evidence_separate():
    manual = observation('manual')
    automatic = observation('automatic', lower=9.9, upper=10.1,
                            source_kind='automatic_zone', revision='zones-v1',
                            observed_at_ms=stamp('09:50') + 1000, source_time_ms=stamp('09:50'))
    original = deepcopy([manual, automatic])
    merged = rules.merge_observations([manual, automatic, deepcopy(manual)])
    assert len(merged) == 1 and merged[0]['sampling_only'] is True
    assert merged[0]['lower'] == merged[0]['upper'] == 10
    assert len(merged[0]['sources']) == 2
    assert {source['source_kind'] for source in merged[0]['sources']} == {'manual_line', 'automatic_zone'}
    assert {source['observed_at_ms'] for source in merged[0]['sources']} == {stamp('09:40') + 1000, stamp('09:50') + 1000}
    assert {source['revision'] for source in merged[0]['sources']} == {1, 'zones-v1'}
    assert merged[0]['first_observed_at_ms'] == stamp('09:40') + 1000
    assert merged[0]['last_observed_at_ms'] == stamp('09:50') + 1000
    assert [manual, automatic] == original
    merged[0]['sources'][0]['evidence']['changed'] = True
    assert [manual, automatic] == original


@pytest.mark.parametrize('patch', [
    {'lower': 11, 'upper': 11}, {'type': 'support_breakdown'}, {'phase': 'holding'},
    {'instrument_id': '600001.SH'}, {'trade_date': '2026-11-12'},
])
def test_merge_preserves_distinct_levels_directions_phases_stocks_and_days(patch):
    assert len(rules.merge_observations([observation('a'), observation('b', **patch)])) == 2


def test_merge_cannot_grow_a_chain_into_one_unrelated_price_zone():
    items = [observation('a', lower=9.9, upper=10),
             observation('b', lower=10, upper=10.1),
             observation('c', lower=10.1, upper=10.2)]
    merged = rules.merge_observations(items)
    assert len(merged) == 2 and max(len(item['sources']) for item in merged) == 2
    assert rules.merge_observations(list(reversed(items))) == merged


def test_input_quotes_states_and_levels_are_not_mutated():
    levels = [level()]
    first = step(levels, 10.2, '09:30')
    current_quote = quote(10, '09:40')
    inputs = deepcopy([levels, current_quote, first['state']])
    result = rules.evaluate_snapshot(levels, current_quote, first['state'], trade_date=DAY, slot_key='09:40')
    assert [levels, current_quote, first['state']] == inputs
    result['observations'][0]['evidence']['level_evidence']['changed'] = True
    result['state']['last_quote']['latest_price'] = 0
    assert [levels, current_quote, first['state']] == inputs
