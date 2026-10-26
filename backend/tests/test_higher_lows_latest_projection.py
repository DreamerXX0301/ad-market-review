"""An independent batch pivot oracle checks the latest-only projection.

The oracle scans the final uninterrupted valid segment and its five-point
pivots, rather than calling the production state generator. Full historical
blocker/availability evidence remains part of the expected result.
"""
from copy import deepcopy
from datetime import date, timedelta
from math import sin
import random

import pytest

from app.analytics import higher_lows as hl


def oracle(slots, period):
    if not slots:
        return {'version': hl.VERSION, 'period': period, 'observed_date': None,
            'covered_periods': 0, 'available_periods': 0, 'recent_lows': [],
            'minimum_close_since_l2_confirmation': None, 'last_blocker': None,
            'reason': '尚无已完整结束并可核验的市场周期'}
    invalid = [i for i, row in enumerate(slots) if not row['valid']]
    boundary = invalid[-1] if invalid else -1
    segment = slots[boundary+1:]
    pivots = [i for i in range(2, len(segment)-2)
              if all(segment[i]['low'] < segment[j]['low']
                     for j in range(i-2, i+3) if j != i)]
    lows = [{'date': segment[i]['date'], 'confirmed_on': segment[i+2]['date'],
             'price': segment[i]['low'], 'age_periods': len(segment)-1-i}
            for i in pivots[-2:]]
    blocker = slots[boundary]['blocker'] if invalid else None
    return {'version': hl.VERSION, 'period': period, 'observed_date': slots[-1]['date'],
        'covered_periods': min(hl.MAX_WINDOW+hl.NEIGHBORS, len(segment)),
        'available_periods': len(slots), 'recent_lows': lows,
        'minimum_close_since_l2_confirmation': min(row['close'] for row in segment[pivots[-1]+2:]) if pivots else None,
        'last_blocker': blocker, 'reason': None if slots[-1]['valid'] else blocker['reason']}


@pytest.mark.parametrize('length', [0, 1, 2, 4, 5, 6, 8, 60, 752, 900])
@pytest.mark.parametrize('seed', [11, 41, 103])
def test_latest_advances_all_seed_slots_but_only_materializes_last(length, seed, monkeypatch):
    rng = random.Random(seed); first = date(2020, 1, 1)
    slots = [{'date': (first+timedelta(days=i)).isoformat(), 'valid': rng.random() > .025,
              'low': 10+rng.randrange(12), 'close': 23+rng.random(),
              'blocker': {'reason': 'fixture gap', 'period_end': (first+timedelta(days=i)).isoformat()}}
             for i in range(length)]
    monkeypatch.setattr(hl, '_period_rows', lambda *args: deepcopy(slots))
    assert hl.latest([], [], '2026-10-23') == oracle(slots, 'D')
    projected = list(hl._states([], [], '2026-10-23', 'D', latest_only=True))
    assert len(projected) == bool(length)
    assert hl.summaries([], [], '2026-10-23') == {
        row['date']: oracle(slots[:i+1], 'D') for i, row in enumerate(slots)}


def fixture_rows():
    start = date(2022, 1, 3)
    calendar = [(start+timedelta(days=i)).isoformat() for i in range(1760)
                if (start+timedelta(days=i)).weekday() < 5]
    rows = [{'date': day, 'open': 20+sin(i/5), 'close': 20+sin(i/5),
             'high': 22+sin(i/5), 'low': 18+sin(i/5), 'volume': 100+i,
             'corporate_action': False, 'corporate_action_coverage_known': True}
            for i, day in enumerate(calendar)]
    return calendar, rows


@pytest.mark.parametrize('period', ['D', 'W', 'M'])
@pytest.mark.parametrize('fault', ['none', 'missing', 'duplicate', 'zero-volume', 'unknown-volume',
                                 'invalid-ohlc', 'unknown-action', 'action', 'unexpected-date', 'partial-calendar'])
def test_real_period_boundaries_faults_and_future_quotes_match_batch_oracle(period, fault):
    calendar, rows = fixture_rows(); index = len(rows)-17
    if fault == 'missing': rows.pop(index)
    elif fault == 'duplicate': rows.append(dict(rows[index]))
    elif fault == 'zero-volume': rows[index]['volume'] = 0
    elif fault == 'unknown-volume': rows[index]['volume'] = None
    elif fault == 'invalid-ohlc': rows[index]['high'] = rows[index]['low']-1
    elif fault == 'unknown-action': rows[index]['corporate_action_coverage_known'] = None
    elif fault == 'action': rows[index]['corporate_action'] = True
    elif fault == 'unexpected-date': rows.append({**rows[index], 'date': '2026-10-10'})
    elif fault == 'partial-calendar': calendar = calendar[3:]; rows = rows[3:]
    for as_of in (calendar[2], calendar[4], calendar[25], calendar[-20], calendar[-1]):
        slots = hl._period_rows(rows, calendar, as_of, period)
        assert hl.latest(rows, calendar, as_of, period) == oracle(slots, period)
        full = hl.summaries(rows, calendar, as_of, period)
        assert full == {row['date']: oracle(slots[:i+1], period) for i, row in enumerate(slots)}


def test_ancient_blocker_and_more_than_750_periods_are_not_truncated(monkeypatch):
    slots = [{'date': str(i), 'low': 10+sin(i), 'close': 15+sin(i), 'valid': True, 'blocker': None}
             for i in range(1100)]
    slots[5].update(valid=False, blocker={'reason': 'old known gap', 'period_end': '5'})
    monkeypatch.setattr(hl, '_period_rows', lambda *args: slots)
    actual = hl.latest([], [], '2026-10-23')
    assert actual == oracle(slots, 'D')
    assert actual['covered_periods'] == 752 and actual['available_periods'] == 1100
    assert actual['last_blocker'] == slots[5]['blocker']
