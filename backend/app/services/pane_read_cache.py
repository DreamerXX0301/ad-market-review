"""Bounded, disposable projections of already verified pane research inputs."""
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from hashlib import sha256
import json
from pathlib import Path
import re
from sqlite3 import OperationalError
from threading import RLock
from zoneinfo import ZoneInfo

from app.infrastructure.config import published_dir, sqlite_path
from app.infrastructure.file_operations import locked_file, path_is_link_or_reparse
from app.infrastructure.storage import atomic_json, digest, hash_file, json_clean, now_ms
from app.services.market_source_proof import verified_price_source

VERSION = 'pane-read-cache-v1'
SESSIONS = 7
MAX_BLOCK_BYTES = 2 * 1024 * 1024
MAX_MEMORY_BYTES = 16 * 1024 * 1024
MAX_FILES = 128
MAX_DISK_BYTES = 64 * 1024 * 1024
HISTORY_TTL_MS = 24 * 60 * 60 * 1000
MAX_HISTORY_FILES = 64
MAX_HISTORY_BYTES = 32 * 1024 * 1024
_HISTORY_EXPIRY = '.expires-at'
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}\Z')
_NAME = re.compile(r'[a-z][a-z0-9-]{0,47}\Z')
_CLOSED_BLOCKS = ('index-summary', 'gainers-price-rank-v1', 'gainers-full-events-v1', 'gainers-overview',
                  'overview-breadth', 'emotion-facts', 'closing-ladder', 'sector-overview')
_CLOSED_OPTIONAL_BLOCKS = ('monitor-historical', 'monitor-latest', 'emotion-broken-board-facts',
                           'emotion-ladder-feedback-facts', 'emotion-first-board-facts')
_CLOSED_RECEIPT = 'closed-day-warm'
_warming = ContextVar('closed_day_cache_warming', default=None)
_history_requested = ContextVar('historical_pane_cache_requested', default=False)
_lock = RLock()
_memory = OrderedDict()
_memory_bytes = 0


def _root():
    # API and worker already share the writable business volume. This separate
    # directory is disposable and never becomes part of a business transaction.
    return sqlite_path().parent / VERSION


def with_historical_cache(read):
    """Only interactive market reads can create the temporary historical cache."""
    @wraps(read)
    def wrapped(*args, **kwargs):
        token = _history_requested.set(True)
        try:
            return read(*args, **kwargs)
        finally:
            _history_requested.reset(token)
    return wrapped


def historical_cache_requested():
    return _history_requested.get()


def retained_days(market, *, live_snapshot=False, status=None):
    if market is None:
        from app.core import get_market

        market = get_market()
    latest = (market.status() if status is None else status).get('data_end')
    dates = [day for day in market.calendar() if latest and day <= latest][-SESSIONS:]
    if live_snapshot:
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        if not dates or today > dates[-1]:
            dates.append(today)
    return dates


def _encode(value):
    return json.dumps(json_clean(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _remember(key, value):
    global _memory_bytes
    with _lock:
        if (old := _memory.pop(key, None)) is not None:
            _memory_bytes -= len(old)
        _memory[key] = value
        _memory_bytes += len(value)
        while _memory_bytes > MAX_MEMORY_BYTES or len(_memory) > MAX_FILES:
            _, removed = _memory.popitem(last=False)
            _memory_bytes -= len(removed)


def _on_disk(path, token):
    try:
        if any(path_is_link_or_reparse(candidate) for candidate in (path.parent.parent, path.parent, path)):
            return None
        with path.open('rb') as stream:
            raw = stream.read(2 * MAX_BLOCK_BYTES + 4096)
            if stream.read(1):
                return None
        record = json.loads(raw)
        if record.get('key') != token:
            return None
        encoded = record.get('body')
        if not isinstance(encoded, str):
            return None
        body = encoded.encode()
        if len(body) <= MAX_BLOCK_BYTES and sha256(body).hexdigest() == record.get('sha256'):
            return body
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def _date_folders(root):
    return [directory for directory in root.iterdir()
            if _DATE.fullmatch(directory.name) and not path_is_link_or_reparse(directory) and directory.is_dir()]


def _folder_files(directory):
    return [file for file in directory.iterdir()
            if file.suffix == '.json' and _NAME.fullmatch(file.stem)
            and not path_is_link_or_reparse(file) and file.is_file()]


def _prune_files(root, days=None, *, newest=None, historical=False):
    records = []
    folders = _date_folders(root)
    for folder in folders:
        for file in _folder_files(folder):
            try:
                if days is not None and folder.name not in days:
                    file.unlink(missing_ok=True)
                    continue
                stat = file.stat()
                records.append((file == newest, stat.st_mtime_ns, stat.st_size, file))
            except OSError:
                continue
    file_limit, byte_limit = (MAX_HISTORY_FILES, MAX_HISTORY_BYTES) if historical else (MAX_FILES, MAX_DISK_BYTES)
    total = sum(item[2] for item in records)
    for index, (_, _, size, file) in enumerate(sorted(records)):
        if len(records) - index <= file_limit and total <= byte_limit:
            break
        file.unlink(missing_ok=True)
        total -= size
    for folder in folders:
        if not _folder_files(folder):
            if historical:
                (folder / _HISTORY_EXPIRY).unlink(missing_ok=True)
            try:
                folder.rmdir()
            except OSError:
                pass


def _history_active(folder, at):
    try:
        if path_is_link_or_reparse(folder) or path_is_link_or_reparse(folder / _HISTORY_EXPIRY):
            return False
        with (folder / _HISTORY_EXPIRY).open('rb') as stream:
            deadline = int(stream.read(24))
        return at < deadline <= at + HISTORY_TTL_MS
    except (OSError, ValueError):
        return False


def _prune_history(root, at, *, newest=None):
    keep = {folder.name for folder in _date_folders(root) if _history_active(folder, at)}
    _prune_files(root, keep, newest=newest, historical=True)


def prune_historical():
    root = _root() / 'historical'
    if not path_is_link_or_reparse(root.parent) and not path_is_link_or_reparse(root) and root.is_dir():
        with locked_file(root / '.write-lock', exclusive=True):
            _prune_history(root, now_ms())


def _write_record(path, token, body):
    if any(path_is_link_or_reparse(candidate) for candidate in (path.parent.parent, path.parent, path)):
        raise OSError('盘面缓存路径指向链接')
    atomic_json(path, {'key': token, 'sha256': sha256(body).hexdigest(), 'body': body.decode()})


def _save(root, path, token, body):
    # A late request can finish with an older proof. The saved token always
    # belongs to that proof, so it cannot be used for a newer publication.
    try:
        if path_is_link_or_reparse(root):
            return
        root.mkdir(parents=True, exist_ok=True)
        with locked_file(root / '.write-lock', exclusive=True):
            _write_record(path, token, body)
            _prune_files(root, newest=path)
    except OSError:
        # Disk caches are optional, including on read-only deployments.
        pass


def _record_warm_file(name, path, token):
    checked = _warming.get()
    if checked is not None and name in _CLOSED_BLOCKS and _on_disk(path, token) is not None:
        checked.add(name)


def _read_historical(name, day, evidence, build, market, status):
    if not historical_cache_requested():
        return build()
    try:
        if market is None:
            from app.core import get_market

            market = get_market()
        status = market.status() if status is None else status
        eligible = status['data_begin'] <= day <= status['data_end'] and day in market.calendar()
        root = _root() / 'historical'
        token = digest((VERSION, name, day, evidence))
    except (OSError, ValueError, TypeError, AttributeError, KeyError, OperationalError):
        return build()
    if not eligible:
        return build()
    folder = root / day
    try:
        if any(path_is_link_or_reparse(candidate) for candidate in (root.parent, root, folder)):
            return build()
    except OSError:
        return build()
    path = folder / (name + '.json')
    if _history_active(folder, now_ms()):
        body = _on_disk(path, token)
        if body is not None:
            try:
                return json.loads(body)
            except ValueError:
                pass
    value = build()
    if status.get('snapshot_id') and market.status().get('snapshot_id') != status['snapshot_id']:
        return value
    try:
        body = _encode(value)
        if len(body) <= MAX_BLOCK_BYTES:
            root.mkdir(parents=True, exist_ok=True)
            with locked_file(root / '.write-lock', exclusive=True):
                at = now_ms()
                create_window = not _history_active(folder, at)
                if create_window:
                    _prune_history(root, at)
                _write_record(path, token, body)
                if create_window and not path_is_link_or_reparse(folder / _HISTORY_EXPIRY):
                    at = now_ms()
                    atomic_json(folder / _HISTORY_EXPIRY, at + HISTORY_TTL_MS)
                _prune_history(root, at, newest=path)
    except (OSError, ValueError, TypeError):
        pass
    return value


def read_block(name, day, evidence, build, *, market=None, live_snapshot=False):
    """Read a cacheable JSON projection. Callers verify real evidence first."""
    if evidence is None or not isinstance(day, str) or not _DATE.fullmatch(day):
        return build()
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError('无效的盘面缓存分区')
    status = None
    try:
        status = market.status() if market is not None else None
        days = retained_days(market, live_snapshot=live_snapshot, status=status)
    except (OSError, ValueError, TypeError, AttributeError, OperationalError):
        days = ()
    if day not in days:
        return _read_historical(name, day, evidence, build, market, status)
    try:
        root = _root()
        token = digest((VERSION, name, day, evidence))
    except (OSError, ValueError, TypeError, AttributeError):
        return build()
    path = root / day / (name + '.json')
    key = (root, name, day, token)
    with _lock:
        body = _memory.get(key)
        if body is not None:
            _memory.move_to_end(key)
    if body is None:
        body = _on_disk(path, token)
        if body is not None:
            _remember(key, body)
    if body is not None:
        try:
            value = json.loads(body)
        except ValueError:
            pass
        else:
            _record_warm_file(name, path, token)
            return value
    value = build()
    if status and status.get('snapshot_id') and market.status().get('snapshot_id') != status['snapshot_id']:
        return value
    try:
        body = _encode(value)
    except (ValueError, TypeError):
        return value
    if len(body) <= MAX_BLOCK_BYTES:
        _remember(key, body)
        _save(root, path, token, body)
        _record_warm_file(name, path, token)
    return value


def snapshot_evidence(market, status, reference=None, *, verified_source=None):
    """Use only the generation already selected by the ordinary read path."""
    snapshot = status.get('snapshot_id')
    manifest = status.get('manifest')
    if not snapshot or not isinstance(manifest, dict) or not manifest:
        return None
    if reference is not None:
        verified = reference.get('_supplement_cache_source')
        if not isinstance(verified, tuple) or len(verified) != 2 or verified[0] is not reference.get('_supplement'):
            return None
    if verified_source is not None and verified_source[0] == snapshot:
        return (*verified_source[:3], verified[1] if reference is not None else None)
    try:
        source = verified_price_source(market, status)
    except (ValueError, OSError):
        return None
    if source is None or market.status().get('snapshot_id') != snapshot:
        return None
    return (snapshot, digest(manifest), source, verified[1] if reference is not None else None)


def _warm(readers):
    for read in readers:
        try:
            read()
        except Exception:
            # Warming must not change a verified publication's outcome.
            continue


def warm_noon(day):
    from app.services.noon_service import read_view

    _warm((lambda: read_view(day),))


def warm_auction(day):
    from app.services import auction_feedback, auction_service

    _warm((lambda: auction_service.read_view(day), lambda: auction_feedback.read_view(day)))


def _warm_source(market, status):
    from app.services.market_suspensions import suspension_revision

    proof = snapshot_evidence(market, status)
    if proof is not None:
        try:
            return proof, hash_file(published_dir() / 'market-supplement/head.json'), suspension_revision()
        except OSError:
            pass
    return None


def _warm_token(folder, source):
    """A completed pass can be skipped while its input and cache files match."""
    if source is None:
        return None
    try:
        files = [(name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                 for name in _CLOSED_BLOCKS for info in [(folder / (name + '.json')).stat()]]
        for name in _CLOSED_OPTIONAL_BLOCKS:
            try:
                info = (folder / (name + '.json')).stat()
            except FileNotFoundError:
                continue
            files.append((name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    except OSError:
        return None
    return digest((VERSION, _CLOSED_RECEIPT, _CLOSED_BLOCKS, _CLOSED_OPTIONAL_BLOCKS, source, files))


def closed_day_warm_steps(day=None):
    """Yield after each local cache read so a startup worker can admit jobs."""
    from functools import partial

    from app.core import get_market
    from app.routers.intraday import view as intraday_view
    from app.services import auction_feedback, auction_service, dragon_tiger, market_service, noon_service

    market = get_market()
    status = market.status()
    latest = status.get('data_end')
    generation = status.get('snapshot_id')
    day = day or latest
    dates = retained_days(market, status=status)
    if day not in dates or latest != day:
        return
    root = _root()
    folder = root / day
    receipt = folder / (_CLOSED_RECEIPT + '.json')
    try:
        if not path_is_link_or_reparse(root) and root.is_dir():
            with locked_file(root / '.write-lock', exclusive=True):
                today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
                _prune_files(root, {*dates, today})
    except OSError:
        pass
    closed_readers = (
        partial(market_service.index_overview, day),
        partial(market_service.market_page, 'gainers', day, 25, top_n=100, page_size=25),
        partial(market_service.overview, day),
        partial(market_service.market_page, 'emotion', day, 1000),
        partial(market_service.market_page, 'sectors', day, 1000),
        partial(market_service.market_page, 'ladder', day, 1000),
        partial(market_service.market_page, 'abnormal', day, 1000),
        partial(market_service.market_page, 'abnormal', day, 1000, monitor_scope='latest'),
    )
    source = _warm_source(market, status)
    token = _warm_token(folder, source)
    if token is None or _on_disk(receipt, token) is None:
        checked = set()
        for read in closed_readers:
            if get_market().status().get('snapshot_id') != generation:
                return
            capture = _warming.set(checked)
            try:
                _warm((read,))
            finally:
                _warming.reset(capture)
            yield
        if get_market().status().get('snapshot_id') != generation:
            return
        if set(_CLOSED_BLOCKS) == checked and _warm_source(market, status) == source:
            completed = _warm_token(folder, source)
            if completed is not None:
                _save(root, receipt, completed, b'true')

    readers = (
        partial(auction_service.read_view, day),
        partial(auction_feedback.read_view, day),
        partial(noon_service.read_view, day),
        partial(intraday_view, day),
        partial(dragon_tiger.read_view, day),
    )
    for read in readers:
        if get_market().status().get('snapshot_id') != generation:
            return
        _warm((read,))
        yield


def warm_closed_day(day):
    for _ in closed_day_warm_steps(day):
        pass
