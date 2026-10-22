"""Offline stage drafts preserve missing ranges, dates and first confirmation."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.services.sentiment_context import cohort_group, fingerprint
from app.services.sentiment_stage_research import (evaluate_stage_candidate, advance_stage_state,
    replay_stage_candidates, observation_from_group, VERSION)

CALENDAR = [(date(2026, 1, 1)+timedelta(days=i)).isoformat() for i in range(80)]
CLOCK = '2026-10-22T01:00:00+08:00'


def point(value): return {'lower': value, 'upper': value}


def observation(day, m=0, positive=50, q=25, **extra):
    return {'date': day, 'group_id': 'ordinary_10', 'observed_count': 10,
            'cohort_source_complete': True, 'identity_complete': True, 'coverage_pct': 100,
            'median_bounds_pct': point(m), 'positive_ratio_bounds_pct': point(positive),
            'promotion_bounds_pct': point(q), 'activity_ratio': None, 'activity_comparable': False,
            'provenance': {'input_fingerprint': fingerprint([day, m, positive, q]), 'input_snapshot_ids': ['fixture'],
                           'first_known_at': None}, **extra}


def evaluate(current, history=(), **options):
    return evaluate_stage_candidate(current, list(history), CALENDAR, computed_at=CLOCK, **options)


def test_missing_feedback_stays_a_range_and_does_not_become_40_percent_heat():
    members = [{'promotion': True, 'return_pct': 2, 'current_identity_known': True}]*3
    members += [{'promotion': False, 'return_pct': 1, 'current_identity_known': True}]*5
    members += [{'promotion': None, 'return_pct': None, 'current_identity_known': False}]*2
    group = cohort_group('ordinary_10', members, source_complete=True, identity_complete=True)
    row = observation_from_group(group, CALENDAR[60], {'input_fingerprint': 'fixture-proof', 'input_snapshot_ids': ['fixture']})
    result = evaluate(row, [observation(day) for day in CALENDAR[:60]])
    assert group['promotion_bounds_pct'] == {'lower': 30, 'upper': 50}
    assert group['median_bounds_pct'] == {'lower': 1, 'upper': 1.5}
    assert result['predicate_evidence']['H'] is None
    assert result['candidate_states']['高潮'] is None
    assert result['validated'] is False and result['usage'] == 'offline_research_only'


def test_cold_candidate_is_not_a_future_bottom_or_already_confirmed():
    first = observation(CALENDAR[1], -1, 35, 20)
    result = evaluate(first)
    assert result['primary_candidate'] == '冰点'
    state = advance_stage_state(None, result, CALENDAR)
    assert state['status'] == 'candidate' and state['confirmed_phase'] is None
    assert state['candidate_count'] == 1 and state['candidate_first_trade_date'] == CALENDAR[1]
    # Subsequent warmth cannot revise the first day's saved result.
    saved = deepcopy(result)
    warm = evaluate(observation(CALENDAR[2], 1, 70, 45), [first])
    assert warm['candidate_states']['回暖'] is True
    assert result == saved
    assert state['confirmed_trade_date'] is None


def test_confirmation_occurs_on_second_exact_session_and_missing_day_resets_it():
    rows = [observation(day, -2, 20, 10) for day in CALENDAR[:4]]
    result = replay_stage_candidates(rows, CALENDAR, computed_at=CLOCK)
    first, second = [item['state'] for item in result['items'][:2]]
    assert first['status'] == 'candidate'
    assert second['status'] == 'confirmed' and second['confirmed_trade_date'] == CALENDAR[1]
    assert second['candidate_first_trade_date'] == CALENDAR[0]
    with_gap = replay_stage_candidates([rows[0], rows[2]], CALENDAR, computed_at=CLOCK)
    assert with_gap['missing_observation_count'] == 1
    assert with_gap['items'][1]['state']['status'] == 'unknown'
    assert with_gap['items'][2]['state']['candidate_count'] == 1
    assert with_gap['items'][2]['state']['confirmed_phase'] is None


@pytest.mark.parametrize('change', [
    {'cohort_source_complete': False}, {'identity_complete': False}, {'coverage_pct': 79.99},
    {'observed_count': 9}, {'group_id': 'twenty_20'}, {'promotion_bounds_pct': None},
    {'positive_ratio_bounds_pct': {'lower': -1, 'upper': 30}},
])
def test_required_evidence_cannot_be_replaced_by_some_matching_thresholds(change):
    row = observation(CALENDAR[2], -2, 20, 10, **change)
    result = evaluate(row)
    assert result['eligible'] is False and result['status'] == 'unknown'
    assert result['primary_candidate'] is None and result['eligibility_reasons']


def test_hot_requires_real_activity_and_prior_only_historical_percentile():
    past = [observation(day) for day in CALENDAR[:60]]
    hot = observation(CALENDAR[60], 1, 70, 45, activity_ratio=1.2, activity_comparable=True)
    result = evaluate(hot, past)
    assert result['candidate_states']['高潮'] is True
    assert result['percentile_bounds'] == {'lower': 100, 'upper': 100}
    assert result['historical_valid_count'] == 60
    assert evaluate({**hot, 'activity_comparable': False}, past)['candidate_states']['高潮'] is None
    # Twenty missing dates do not disappear into a compressed 40-day sample.
    gappy = evaluate(hot, past[:40])
    assert gappy['historical_valid_count'] == 40
    assert gappy['percentile_bounds']['lower'] == pytest.approx(40/60*100)
    assert gappy['candidate_states']['高潮'] is None
    future = observation(CALENDAR[61], -999, 0, 0, seal_nextday=999)
    assert evaluate(hot, [*past, future]) == result


def test_current_or_later_roles_cannot_create_yesterday_core_divergence():
    past = [observation(CALENDAR[3], 1, 70, 45), observation(CALENDAR[4], 1, 70, 45)]
    role = {'role_date': CALENDAR[5], 'frozen_roles': True,
            'high': {'sample_count': 5, 'median_bounds_pct': point(2)},
            'low': {'sample_count': 5, 'median_bounds_pct': point(-1)}}
    current = observation(CALENDAR[5], .5, 70, 45, high_low=role)
    wrong = evaluate(current, past)
    assert wrong['predicate_evidence']['high_low_divergence'] is None
    assert wrong['candidate_states']['分歧'] is None
    fixed = evaluate({**current, 'high_low': {**role, 'role_date': CALENDAR[4]}}, past)
    assert fixed['predicate_evidence']['high_low_divergence'] is True
    assert fixed['candidate_states']['分歧'] is True


def test_as_known_mode_cannot_rename_fetch_time_or_use_late_material():
    row = observation(CALENDAR[1], -2, 20, 10)
    row['provenance']['fetched_at'] = '2026-01-02T15:00:00+08:00'
    assert evaluate(row, mode='as_known', cutoff='2026-01-02T16:00:00+08:00')['eligible'] is False
    row['provenance']['first_known_at'] = '2026-10-21T21:00:00+08:00'
    assert evaluate(row, mode='as_known', cutoff='2026-01-02T16:00:00+08:00')['eligible'] is False
    # Current-retained reconstruction remains allowed and labelled as such.
    assert evaluate(row)['primary_candidate'] == '冰点'
    row['provenance']['first_known_at'] = '2026-01-02T15:30:00+08:00'
    assert evaluate(row, mode='as_known', cutoff='2026-01-02T16:00:00+08:00')['eligible'] is True


def test_missing_core_evidence_retires_current_confirmation_but_retains_dated_history():
    rows = [observation(day, -2, 20, 10) for day in CALENDAR[:2]]
    rows += [observation(CALENDAR[2], -2, 20, 10, identity_complete=False), observation(CALENDAR[3], -2, 20, 10)]
    result = replay_stage_candidates(rows, CALENDAR, computed_at=CLOCK)
    unknown = result['items'][2]['state']
    assert unknown['status'] == 'unknown' and unknown['confirmed_phase'] is None
    assert unknown['last_confirmed_phase'] == '冰点' and unknown['last_confirmed_trade_date'] == CALENDAR[1]
    assert result['items'][3]['state']['candidate_count'] == 1


def test_hysteresis_keeps_confirmation_in_band_then_exits_after_two_failures():
    rows = [observation(day, -2, 20, 10) for day in CALENDAR[:2]]
    # At -0.6/40/20, entering C is false while continuing C is true.
    rows.append(observation(CALENDAR[2], -.6, 40, 20))
    result = replay_stage_candidates(rows, CALENDAR, computed_at=CLOCK)
    third = result['items'][2]
    assert third['evaluation']['candidate_states']['冰点'] is False
    assert third['evaluation']['continuation_states']['冰点'] is True
    # Other candidate rules can be unknown at the history edge; an independently
    # proved continuation still must not be treated as a fresh C confirmation.
    assert third['state']['candidate_count'] == 0
    assert third['state']['status'] == 'confirmed'
    assert third['state']['confirmed_trade_date'] == CALENDAR[1]
    full = evaluate(observation(CALENDAR[65], 0, 50, 25), [observation(day) for day in CALENDAR[:65]])
    assert full['candidate_states']['冰点'] is False
    previous = {**result['items'][1]['state'], 'date': CALENDAR[64]}
    first_exit = advance_stage_state(previous, full, CALENDAR)
    assert first_exit['status'] == 'pending_review' and first_exit['exit_count'] == 1
    second_eval = evaluate(observation(CALENDAR[66], 0, 50, 25), [observation(day) for day in CALENDAR[:66]])
    second_exit = advance_stage_state(first_exit, second_eval, CALENDAR)
    assert second_exit['confirmed_phase'] is None and second_exit['exit_count'] == 2


def test_returned_config_and_evidence_do_not_mutate_input_or_future_defaults():
    row = observation(CALENDAR[2], -2, 20, 10)
    before = deepcopy(row)
    result = evaluate(row)
    result['config']['C'][0] = -999
    assert evaluate(row)['config']['C'][0] == -1
    assert row == before
    for invalid in ({'unknown_parameter': 1}, {'C': [1]}, {'confirmation_sessions': 1}, {'hot_activity_ratio': float('nan')}):
        with pytest.raises(ValueError): evaluate(row, config=invalid)
