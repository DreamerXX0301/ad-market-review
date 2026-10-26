"""Request-local board planning and a worker-only verified-record consumer.

Only small directory/calendar plans are shared. Full board histories are never
kept here; a consumer processes one verified record before the next request.
The context is discarded on pause, cancellation and ordinary exceptions.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from app.core import digest

_planning = ContextVar('sector_read_planning', default=None)
_consumer = ContextVar('sector_verified_consumer', default=None)


def _catalog_stamp(reference):
    return digest([reference.get('industry_catalog'), reference.get('concept_catalog')])


class _Planning:
    def __init__(self, market, day, plans, calendar, status, reference):
        self.market = market
        self.day = day
        self.plans = {plan['topic_code']: deepcopy(plan) for plan in plans}
        self.calendar = tuple(calendar)
        self.snapshot_id = status.get('snapshot_id')
        self.catalog_stamp = _catalog_stamp(reference)
        self.valid = True

    def lookup(self, market, code, day):
        if (not self.valid or market is not self.market or day != self.day
                or not isinstance(code, str) or code not in self.plans):
            return None
        # A changed fake/legacy mutable reader falls back to the ordinary
        # planner, which retains its existing identity/window rejection path.
        if (market.status().get('snapshot_id') != self.snapshot_id
                or tuple(market.calendar()) != self.calendar
                or _catalog_stamp(market.reference()) != self.catalog_stamp):
            self.valid = False
            return None
        return deepcopy(self.plans[code])


@contextmanager
def planning_scope(market, day, *, plans=None, catalog=None, calendar=None, status=None):
    """Bind one verified catalog, keeping mutable-reader changes visible."""
    from .sector_charts import _plan_from_inputs
    from .sector_history import _catalog
    reference = market.reference()
    status = market.status() if status is None else status
    calendar = market.calendar() if calendar is None else calendar
    if plans is None:
        catalog = _catalog(reference)[0] if catalog is None else catalog
        plans = [_plan_from_inputs(status, set(calendar), catalog, code, day) for code in sorted(catalog)]
    if len(plans) > 800:
        raise ValueError('板块读取上下文超过800个目录保护上限')
    context = _Planning(market, day, plans, calendar, status, reference)
    token = _planning.set(context)
    try:
        yield context
    finally:
        _planning.reset(token)


def prepared_plan(market, code, day):
    context = _planning.get()
    return context.lookup(market, code, day) if context is not None else None


@contextmanager
def verified_consumer(callback):
    """The sole worker may prepare a derivative between serial requests."""
    token = _consumer.set(callback)
    try:
        yield
    finally:
        _consumer.reset(token)


def consume_verified(plan, result):
    callback = _consumer.get()
    if callback is not None:
        callback(plan, result)
