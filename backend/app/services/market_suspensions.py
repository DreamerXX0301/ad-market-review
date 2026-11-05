"""Read a bounded archive of verified full-session stock suspensions.

Display metrics may use a prior actual close across a proven halt. No missing
quote, zero volume, or open-ended estimate creates evidence here.
"""

from bisect import bisect_left
from datetime import date
from functools import lru_cache
import json
import math
import re
from urllib.parse import urlsplit

from app.core import work_dir

VERSION = 'market-suspensions-v1'
SUSPENSION_ENDPOINT_LOOKBACK = 20
MAX_BYTES = 2 * 1024 * 1024
STOCK_CODE = re.compile(r'\d{6}\.(SH|SZ|BJ)\Z')
SOURCE_HOSTS = {
    'exchange': {'www.sse.com.cn', 'query.sse.com.cn', 'star.sse.com.cn', 'www.szse.cn',
                 'disc.static.szse.cn', 'docs.static.szse.cn', 'www.bse.cn', 'static.cninfo.com.cn'},
    'eastmoney_choice': {'datapc.eastmoney.com', 'datacenter-web.eastmoney.com'},
}


def _path():
    return work_dir() / 'market-suspensions' / 'confirmed.json'


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def suspension_revision():
    try:
        path = _path()
        stamp = path.stat()
        if 0 < stamp.st_size <= MAX_BYTES:
            return (str(path), stamp.st_ino, stamp.st_mtime_ns, stamp.st_size)
    except OSError:
        pass
    return None


def _archive_item(item):
    if not isinstance(item, dict) or item.get('state') != 'suspended' or item.get('full_session') is not True:
        return None
    symbol, start, end = (item.get(key) for key in ('instrument_id', 'start_date', 'end_date'))
    source, url = item.get('source'), item.get('source_url')
    if (not isinstance(symbol, str) or not STOCK_CODE.fullmatch(symbol) or not _day(start) or not _day(end)
            or start > end or not isinstance(source, str) or source not in SOURCE_HOSTS
            or not isinstance(url, str) or len(url) > 2048):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc not in SOURCE_HOSTS[source]:
            return None
    except ValueError:
        return None
    return {key: item[key] for key in ('instrument_id', 'state', 'full_session', 'start_date', 'end_date', 'source', 'source_url')}


@lru_cache(maxsize=2)
def _load(revision):
    if revision is None:
        return ()
    try:
        with _path().open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) != revision[-1] or suspension_revision() != revision:
            return ()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('version') != VERSION or not isinstance(payload.get('items'), list):
            return ()
        return tuple(item for row in payload['items'] if (item := _archive_item(row)) is not None)
    except (OSError, ValueError, TypeError, RecursionError):
        return ()


@lru_cache(maxsize=256)
def _date_items(revision, day):
    items = {}
    for item in _load(revision):
        if item['start_date'] <= day <= item['end_date']:
            previous = items.get(item['instrument_id'])
            if previous is None or item['source'] == 'exchange':
                items[item['instrument_id']] = item
    return items


def suspensions_for_date(day):
    """Source evidence for this date; absence does not prove normal trading."""
    return _date_items(suspension_revision(), day) if _day(day) else {}


def suspension_status(symbol, day):
    return suspensions_for_date(day).get(symbol)


def suspended_on(symbol, day):
    return suspension_status(symbol, day) is not None


def _positive(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 and math.isfinite(value)
    except OverflowError:
        return False


def suspension_endpoint(index, calendar, day, symbol):
    """Return a real endpoint, walking back through confirmed halt days only."""
    if not suspended_on(symbol, day):
        return day if day in index else None
    position = bisect_left(calendar, day)
    if position == len(calendar) or calendar[position] != day:
        return None
    for offset in range(SUSPENSION_ENDPOINT_LOOKBACK + 1):
        if position < offset:
            break
        current = calendar[position - offset]
        quote = index.get(current) or {}
        traded = _positive(quote.get('volume'))
        if suspended_on(symbol, current):
            if traded:
                return None  # Quotes contradict the halt record.
            continue
        if (traded and quote.get('instrument_id', symbol) == symbol and _positive(quote.get('close'))):
            return current
        return None  # An unverified gap must never be crossed.
    return None
