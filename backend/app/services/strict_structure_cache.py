"""Bounded, rule-independent strict period inputs in the existing proof store.

The source-bound summary key covers calendar, actual source closure, anchor,
security and data root. No final structure result or threshold is reused: new
BOLL configurations replay their own complete original recurrence.
"""
from app.analytics import boll_conditions as boll, higher_lows as lows
from app.analytics.strict_period_layout import period_layout
from app.services import condition_proof_cache as disk
from math import isfinite

VERSION = ('strict-period-input-v1', lows.VERSION, boll.VERSION)


def restore(key):
    value = disk.load(key, 'strict-input', VERSION)
    return _validated(value)


def record_for(key):
    return disk.read_record(key, 'strict-input', VERSION)


def decode_record(record):
    return _validated(disk.decode_record(record))


def _validated(value):
    if value is None or not isinstance(value.get('rows'), list):return None
    rows=value['rows']
    if len(rows)>16384:return None
    for row in rows:
        if (not isinstance(row,dict) or set(row)!={'date','low','close','valid','blocker'}
                or not isinstance(row['date'],str) or not isinstance(row['valid'],bool)):
            return None
        if row['valid']:
            if row['blocker'] is not None or any(type(row[field]) not in (int,float)
                or not isfinite(row[field]) or row[field]<=0 for field in ('low','close')):return None
        elif (row['low'] is not None or row['close'] is not None or not isinstance(row['blocker'],dict)
              or not isinstance(row['blocker'].get('reason'),str)):
            return None
    return rows


def prepare(key, rows, calendar, day, period):
    cached = restore(key)
    if cached is not None:return cached
    periods = build(rows, calendar, day, period)
    if periods is not None:disk.store(key, 'strict-input', {'rows': periods}, VERSION)
    return periods


def build(rows, calendar, day, period):
    """Pure preparation; the parent alone may publish the resulting proof."""
    layout = period_layout(calendar, day, period)
    # Only this common strict domain has identical blocker priority and
    # wording in both indicators. Legacy aliases and coverage overrides keep
    # their independent original paths, never a broadened proof.
    compatible = all(row.get('date') in layout.canonical_days
        and row.get('corporate_action_coverage_known') is True
        and row.get('_higher_lows_action_coverage_known', True) is True
        and row.get('_boll_action_coverage_known', True) is True
        and isinstance(row.get('corporate_action'), bool) for row in rows)
    if not compatible:return None
    return lows._period_rows(rows, calendar, day, period)


def calculate(periods, period, *, include_higher_lows=False, boll_configs=()):
    higher = lows.latest_from_periods(periods, period) if include_higher_lows else None
    # BOLL's price column is explicitly float. The normalized strict input is
    # finite float/None already, but preserve the conversion for generic rows.
    bands={}
    if boll_configs:
        rows = [{key: value for key, value in row.items() if key != 'low'} for row in periods]
        for row in rows:
            if row['valid']:row['close'] = float(row['close'])
        bands = boll.latest_states_from_periods(rows, period, boll_configs)
    return higher, bands
