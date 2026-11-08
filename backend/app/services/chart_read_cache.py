"""Disposable chart projections built only when a detail is opened."""
from collections import OrderedDict
import pickle
from threading import RLock

from app.services.market_source_proof import verified_price_source


def recent_chart_day(calendar, published_end, day):
    """A calendar can include dates beyond the last published close."""
    if not published_end or not day or day > published_end:
        return False
    completed = sorted({value for value in calendar if value <= published_end})
    return day in completed[-7:]


class ChartReadCache:
    """Bound result bytes and count without sharing a mutable response object."""
    def __init__(self, *, entries, bytes_limit):
        self._entries = OrderedDict()
        self._lock = RLock()
        self._count = entries
        self._limit = bytes_limit
        self._bytes = 0

    def read(self, key, revalidate=None):
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        body, evidence = entry
        try:
            if revalidate is not None:
                revalidate(evidence)
            # Only this process creates these bytes. Never deserialize from
            # provider data, an API request, or a file.
            result = pickle.loads(body)
        except (OSError, ValueError, TypeError, EOFError, pickle.UnpicklingError):
            with self._lock:
                if self._entries.get(key) is entry:
                    self._entries.pop(key)
                    self._bytes -= len(body)
            return None
        with self._lock:
            if self._entries.get(key) is entry:
                self._entries.move_to_end(key)
        return result

    def remember(self, key, result, evidence=None):
        try:
            body = pickle.dumps(result, protocol=5)
        except (TypeError, ValueError, pickle.PicklingError, RecursionError):
            return
        if len(body) > self._limit:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= len(previous[0])
            self._entries[key] = (body, evidence)
            self._bytes += len(body)
            while len(self._entries) > self._count or self._bytes > self._limit:
                _, dropped = self._entries.popitem(last=False)
                self._bytes -= len(dropped[0])


_instrument_views = ChartReadCache(entries=16, bytes_limit=12 * 1024 * 1024)


def instrument_projection(market, status, day, variant, build):
    """Cache fixed indicators, leaving event and editable overlays to callers."""
    generation = status.get('snapshot_id')
    if (not generation or not isinstance(status.get('files'), list)
            or not recent_chart_day(market.calendar(), status.get('data_end'), day)):
        return build()
    proof = verified_price_source(market, status)
    if proof is None:
        return build()
    key = (status.get('path'), generation, day, proof, status.get('quality'),
           tuple(status.get('warnings', ())), variant)
    cached = _instrument_views.read(key)
    if cached is not None and market.status().get('snapshot_id') == generation:
        return cached
    result = build()
    if (result.get('bars') and result.get('as_of') == day
            and result.get('snapshot_id') == generation
            and market.status().get('snapshot_id') == generation):
        _instrument_views.remember(key, result)
    return result
