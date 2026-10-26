"""Exact comparisons with the frozen pre-optimization monthly path."""
from copy import deepcopy
from datetime import date, timedelta
import json
from math import isfinite, sin

import pytest

from app.analytics import monthly, period_dates
from app.domain import conditions
from oracles.monthly_aggregate_v1 import aggregate_monthly as legacy_aggregate
from oracles.monthly_aggregate_v1 import enrich_monthly_bars as legacy_enrich


def inputs(fault):
    first = date(2021, 9, 1)
    calendar = [(first+timedelta(days=i)).isoformat() for i in range(1888)
                if (first+timedelta(days=i)).weekday() < 5]
    rows = [{'date': day, 'open': 20+sin(i/11), 'high': 22+sin(i/11),
             'low': 18+sin(i/11), 'close': 20.5+sin(i/11),
             'volume': 100+i, 'amount': 2000+i,
             'corporate_action': False, 'corporate_action_coverage_known': True,
             'extra_source': {'label': '保留原始字段', 'number': i}}
            for i, day in enumerate(calendar)]
    point = len(rows)-40
    if fault == 'missing-day': rows.pop(point)
    elif fault == 'missing-month': rows = [row for row in rows if row['date'][:7] != '2026-09']
    elif fault == 'missing-two-months': rows = [row for row in rows if row['date'][:7] not in ('2026-08','2026-09')]
    elif fault == 'duplicate': rows.append({**rows[point], 'volume': 99999})
    elif fault == 'action': rows[point]['corporate_action'] = True
    elif fault == 'unknown-action': rows[point]['corporate_action_coverage_known'] = None
    elif fault == 'zero-volume': rows[point]['volume'] = 0
    elif fault == 'unknown-volume': rows[point]['volume'] = None
    elif fault == 'partial-calendar': calendar = calendar[8:]; rows = rows[8:]
    elif fault == 'partial-quotes': rows = rows[8:]
    elif fault == 'unexpected-date': rows.append({**rows[point], 'date': '2026-10-10'})
    elif fault == 'no-calendar': calendar = []
    elif fault == 'reverse-duplicate-calendar': calendar = list(reversed(calendar))+calendar[:3]
    return calendar, rows


FAULTS = ['none', 'missing-day', 'missing-month', 'missing-two-months', 'duplicate',
          'action', 'unknown-action', 'zero-volume', 'unknown-volume', 'partial-calendar',
          'partial-quotes', 'unexpected-date', 'no-calendar', 'reverse-duplicate-calendar']


def exact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@pytest.mark.parametrize('fault', FAULTS)
def test_calendar_grouping_and_bisect_keep_every_monthly_evidence_field(fault):
    calendar, rows = inputs(fault); old = deepcopy(rows)
    # Sum cannot consume unknown volume directly; the shared strategy adapter
    # uses zero with a separate invalid-month marker before aggregation.
    bars = [{**row, 'volume': row['volume'] or 0} for row in reversed(rows)]
    for as_of in ('2021-09-03','2021-09-30','2024-10-22','2026-10-15','2026-10-30'):
        expected = legacy_aggregate(bars, calendar, as_of)
        actual = monthly.aggregate_monthly(bars, calendar, as_of)
        assert exact(actual) == exact(expected)
        for extremes in (False, True):
            assert exact(monthly.enrich_monthly_bars(actual, include_chart_extremes=extremes)) == exact(
                legacy_enrich(expected, include_chart_extremes=extremes))
    assert rows == old


def legacy_completed(rows, calendar, as_of, period, *, tail=None, source_facts_only=False):
    if period != 'M':
        raise AssertionError('monthly oracle only')
    positive = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0
    invalid = {row['date'][:7] for row in rows if not positive(row.get('volume')) or row.get('_volume_known') is False}
    periods = legacy_aggregate(rows, calendar, as_of)
    for row in periods:
        if row['date'][:7] in invalid:
            row.update(complete=False, monthly_continuity_reset_reason='月内存在缺失或零成交量报价，指标重新预热')
    result = [row for row in legacy_enrich(periods) if row.get('monthly_indicator_eligible') is True]
    return result[-tail:] if tail is not None else result


@pytest.mark.parametrize('fault', FAULTS[:-2])
@pytest.mark.parametrize('as_of', ['2021-09-30', '2024-10-22', '2026-09-30', '2026-10-30'])
@pytest.mark.parametrize('usable', [True, False])
def test_raw_facts_short_projection_keeps_complete_final_forward_metrics(fault, as_of, usable, monkeypatch):
    calendar, rows = inputs(fault)
    adjusted = [{**row, **{field: row[field]*1.5+1 for field in ('open','high','low','close')},
                 'corporate_action': False} for row in rows]
    context = {'requested_price_basis': 'forward', 'price_basis': 'forward_event_affine' if usable else 'unavailable',
               'anchor_date': as_of, 'raw_rows': rows, 'price_rows': adjusted if usable else [],
               'adjustment': {'usable': usable, 'version': 'synthetic-fixture-only', 'events_count': 1}}
    expected_inputs = deepcopy(context)
    with monkeypatch.context() as old:
        old.setattr(period_dates, 'completed_indicator_bars', legacy_completed)
        expected = conditions.metrics_for(rows, as_of, calendar, period='M', price_context=context)
    actual = conditions.metrics_for(rows, as_of, calendar, period='M', price_context=context)
    assert exact(actual) == exact(expected)
    assert context == expected_inputs


def test_fact_projection_classifies_all_months_but_enriches_at_most_two(monkeypatch):
    calendar, rows = inputs('missing-month')
    calls = []; original = monthly.enrich_bars
    def observed(values, **kwargs):
        calls.append(len(values)); return original(values, **kwargs)
    monkeypatch.setattr(monthly, 'enrich_bars', observed)
    period_dates.completed_indicator_bars(rows, calendar, '2026-10-30', 'M', tail=2, source_facts_only=True)
    assert calls == [2]
    calls.clear()
    period_dates.completed_indicator_bars(rows, calendar, '2026-10-30', 'M', tail=2)
    assert calls[0] > 35
