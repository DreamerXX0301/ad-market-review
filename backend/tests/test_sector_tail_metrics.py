"""Compact board summaries retain complete-period and recurrence evidence."""
from copy import deepcopy
from math import sin

import pandas as pd
import pytest

from app.analytics.indicators import enrich_bars
from app.services import sector_strategies as strategies


def prices():
    days = [d.strftime('%Y-%m-%d') for d in pd.bdate_range('2023-01-02', periods=800)]
    bars = []
    for index, day in enumerate(days):
        close = 100+index*.03+sin(index/7)*3
        bars.append({'date': day, 'open': close-.2, 'high': close+1,
                     'low': close-1, 'close': close, 'volume': 1000+index,
                     'amount': (1000+index)*close})
    return days, bars


@pytest.mark.parametrize('boundary', ['complete', 'first_week', 'daily_gap',
                                      'last_zero', 'weekly_gap', 'unknown_amount'])
@pytest.mark.parametrize('extras', [False, True])
def test_board_dwm_metrics_and_current_proof_equal_full_output(monkeypatch, boundary, extras):
    calendar, bars = prices()
    if boundary == 'first_week':
        bars = bars[2:]
    elif boundary == 'daily_gap':
        del bars[-3]
    elif boundary == 'last_zero':
        bars[-1]['volume'] = 0
    elif boundary == 'weekly_gap':
        del bars[-15:-10]
    elif boundary == 'unknown_amount':
        bars[-2]['amount'] = None
    configs = {'D': [{'mode': 'breakout_up', 'require_squeeze': False}],
               'W': [{'mode': 'squeeze', 'band_period': 5, 'rank_window': 10}]} if extras else None
    record = {'bars': bars}; before = deepcopy(record)
    with monkeypatch.context() as reference:
        reference.setattr(strategies, 'enrich_bars',
                          lambda rows, calendar=None, *, tail=None: enrich_bars(rows, calendar))
        full = strategies._indicators(record, calendar, calendar[-1], extras, configs)
    calls = []
    def tail_series(rows, calendar=None, *, tail=None):
        calls.append(tail)
        return enrich_bars(rows, calendar, tail=tail)
    monkeypatch.setattr(strategies, 'enrich_bars', tail_series)
    compact = strategies._indicators(record, calendar, calendar[-1], extras, configs)
    assert calls == [2, 2]
    assert compact == full
    assert record == before
