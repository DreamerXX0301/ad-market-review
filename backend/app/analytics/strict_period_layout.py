"""Bounded reuse of pure calendar slots for strict structural indicators.

The complete supplied calendar, including future sessions used as closure
evidence, is part of the key. No quote, validity flag, indicator seed or result
is cached here. Each consumer keeps its own quote and unknown-state semantics.
"""
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from typing import NamedTuple

from .period_dates import latest_closed_date

MAX_CACHED_CALENDAR_SESSIONS = 10000
MAX_CALENDARS = 4
MAX_LAYOUTS = 24


class _Calendar(NamedTuple):
    sessions: tuple
    session_set: frozenset
    canonical_days: frozenset


class PeriodLayout(NamedTuple):
    sessions: tuple
    session_set: frozenset
    canonical_days: frozenset
    closed: str | None
    # (original period key, all expected market dates, boundary known)
    groups: tuple


def _canonical(day):
    try:
        return isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except ValueError:
        return False


def _calendar(calendar):
    sessions = tuple(sorted(set(calendar)))
    return _Calendar(sessions, frozenset(sessions), frozenset(day for day in sessions if _canonical(day)))


@lru_cache(maxsize=MAX_CALENDARS)
def _cached_calendar(calendar):
    return _calendar(calendar)


def _layout(calendar, as_of, period):
    sessions = calendar.sessions
    closed = latest_closed_date(sessions, as_of, period)
    groups = defaultdict(list)
    if closed is not None:
        for day in sessions:
            if day <= closed:
                key = date.fromisoformat(day).isocalendar()[:2] if period == 'W' else day[:7] if period == 'M' else day
                groups[key].append(day)
    slots = []
    for key, days in groups.items():
        boundary_known = True
        if period != 'D':
            first = date.fromisoformat(days[0])
            boundary = (first-timedelta(days=first.weekday())).isoformat() if period == 'W' else days[0][:7]+'-01'
            boundary_known = sessions[0] <= boundary
        slots.append((key, tuple(days), boundary_known))
    return PeriodLayout(sessions, calendar.session_set, calendar.canonical_days, closed, tuple(slots))


@lru_cache(maxsize=MAX_LAYOUTS)
def _cached_layout(calendar, as_of, period):
    return _layout(_cached_calendar(calendar), as_of, period)


def period_layout(calendar, as_of, period):
    """Immutable expected slots, preserving sorting/closure/boundary behavior.

    Large diagnostic inputs take the exact same uncached path instead of
    retaining an unexpectedly large calendar in the process LRU.
    """
    calendar = tuple(calendar)
    if len(calendar) > MAX_CACHED_CALENDAR_SESSIONS:
        return _layout(_calendar(calendar), as_of, period)
    return _cached_layout(calendar, as_of, period)


def canonical_quote_day(day, layout):
    """Reuse calendar date parsing without accepting noncanonical quote aliases."""
    if not isinstance(day, str):
        return False
    if day in layout.session_set:
        return day in layout.canonical_days
    return _canonical(day)


def clear_period_layout_cache():
    _cached_layout.cache_clear()
    _cached_calendar.cache_clear()
