"""Disposable, verified same-anchor quotation seeds shared by stock strategies.

The caller verifies the immutable source closure before every probe. This cache
contains prices and adjustment inputs, never a rule, source-pool verification,
policy or evaluation. Corruption, quota and filesystem failures are cache misses.
"""
from collections import OrderedDict
from array import array
from base64 import b64decode, b64encode
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from threading import RLock, get_ident
import time
import zlib

from app.core import data_dir, work_dir, published_dir, sqlite_path, raw_dir
from app.domain.conditions import prepare_metric_bars
from app.domain.strategy_prices import VERSION as PRICE_VERSION, compact_source_token

VERSION = 'condition-basis-seed-v1'
# Bump when normalized_bar/_PreparedMetricBars changes its quotation semantics.
NORMALIZATION_VERSION = 'condition-normalized-quotes-v1'
MAX_ENTRIES = 16384
MAX_BYTES = 64 * 1024 * 1024
MAX_DISK_BYTES = 768 * 1024 * 1024
MAX_COMPRESSED = 2 * 1024 * 1024
MAX_DECOMPRESSED = 8 * 1024 * 1024
MAX_ROWS = 16384
MAX_FIELDS = 128
MAX_DECODED_CELLS = 1000000
_DIRECTORY = '.condition-basis-cache'
_FILENAME = 'seeds-v1.sqlite3'
_cache = OrderedDict()
_bytes = 0
_lock = RLock()
_disk_lock = RLock()
_disk_epoch = 0
_disk_session = ContextVar('condition_cache_disk_session', default=None)
SESSION_MAX_RECORDS = 64
SESSION_MAX_BYTES = 16 * 1024 * 1024
SESSION_MAX_MISSES = 16384
_stats = {'memory_hits': 0, 'disk_hits': 0, 'structure_hits': 0, 'misses': 0, 'stored': 0, 'disk_errors': 0}
_CONTEXT_FIELDS = ('requested_price_basis', 'price_basis', 'anchor_date', 'cache_key', 'adjustment')


def key_for(symbol, day, generation, source_token):
    """No rule/window/run identity: only verified immutable technical inputs."""
    if (not isinstance(symbol, str) or not symbol or not isinstance(day, str)
            or not generation or source_token is None):
        return None
    try:
        proof = compact_source_token(source_token)
        closure = proof['verified_closure_sha256']
        if not isinstance(closure, str) or len(closure) != 64:
            return None
        int(closure, 16)
        if isinstance(generation, (tuple, list)) and (len(generation) != 2
                or not isinstance(generation[0], str) or not generation[0]
                or generation[1] != closure):
            return None
        frozen_generation = json.dumps(generation, sort_keys=True, separators=(',', ':'), allow_nan=False)
        return (str(data_dir()), frozen_generation, symbol, day, closure,
                PRICE_VERSION, NORMALIZATION_VERSION, VERSION)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def _identity(key):
    if not isinstance(key, tuple) or len(key) != 8 or key[-3:] != (PRICE_VERSION, NORMALIZATION_VERSION, VERSION):
        raise ValueError('cache key contract')
    if key[0] != str(data_dir()):
        raise ValueError('cache namespace changed')
    return sha256(json.dumps(key, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


_MISSING = object()


def _binary(values, code):
    result = array(code, values)
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


class _Columns:
    """Share identical date/quote/raw_* columns across all four input lanes.

    Numeric bytes preserve exact floats (including signed zero and NaN) and
    integer types. Separate sparse missing/null offsets preserve source keys.
    """
    def __init__(self):
        self.columns = []; self.index = {}

    def table(self, rows):
        fields = sorted({field for row in rows for field in row})
        return self.table_from_columns(fields,
            ([row.get(field, _MISSING) for row in rows] for field in fields), len(rows))

    def table_from_columns(self, fields, values, count):
        """Encode canonical columns without first materializing row dictionaries."""
        if (not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_ROWS
                or not isinstance(fields, list) or len(fields) > MAX_FIELDS
                or not all(isinstance(field, str) for field in fields)
                or fields != sorted(set(fields))):
            raise ValueError('cache quotation dimensions')
        references = []
        for column in values:
            if len(references) >= len(fields) or len(column) != count:
                raise ValueError('cache quotation column dimensions')
            references.append(self._column(column))
        if len(references) != len(fields):
            raise ValueError('cache quotation column dimensions')
        return {'fields': fields, 'columns': references, 'count': count}

    def _column(self, values):
        # Normal quote lanes overwhelmingly contain complete, homogeneous
        # columns. Retain the exact old wire bytes while avoiding four extra
        # Python scans and temporary masks/lists on that common path.
        first_type = type(values[0]) if values else None
        homogeneous = first_type in (float, int, bool, str, type(None)) and all(
            type(value) is first_type for value in values)
        raw = None
        missing = null = ()
        if homogeneous:
            if first_type is float:
                kind = 'float'; raw = _binary(values, 'd')
            elif first_type is int:
                try:
                    kind = 'int'; raw = _binary(values, 'q')
                except OverflowError:
                    pass  # Arbitrarily large integers retain the original JSON fallback.
            elif first_type is bool:
                kind = 'bool'; raw = bytes(values)
            else:
                kind = 'json'
                raw = json.dumps(values, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
                if first_type is type(None):
                    null = tuple(range(len(values)))
        if raw is None:
            missing = tuple(index for index, value in enumerate(values) if value is _MISSING)
            null = tuple(index for index, value in enumerate(values) if value is None)
            types = {type(value) for value in values if value is not _MISSING and value is not None}
            kind = 'float' if types == {float} else 'int' if types == {int} else 'bool' if types == {bool} else 'json'
            present = [0 if value is _MISSING or value is None else value for value in values]
            if kind == 'float':
                raw = _binary(present, 'd')
            elif kind == 'int' and all(-(1 << 63) <= value < (1 << 63) for value in present):
                raw = _binary(present, 'q')
            elif kind == 'bool':
                raw = bytes(present)
            else:
                kind = 'json'; raw = json.dumps([None if value is _MISSING else value for value in values],
                    ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        identity = (kind, raw, missing, null)
        reference = self.index.get(identity)
        if reference is None:
            reference = len(self.columns); self.index[identity] = reference
            self.columns.append({'kind': kind, 'count': len(values), 'data': b64encode(raw).decode(),
                                 'missing': missing, 'null': null})
        return reference


class _ReadColumns:
    def __init__(self, columns):
        if not isinstance(columns, list) or len(columns) > MAX_FIELDS * 4:
            raise ValueError('cache quotation columns')
        self.columns = columns; self.values = {}; self.cells = 0

    def column(self, reference):
        if not isinstance(reference, int) or isinstance(reference, bool) or not 0 <= reference < len(self.columns):
            raise ValueError('cache column reference')
        if reference not in self.values:
            column = self.columns[reference]; count = column['count']; kind = column['kind']
            if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_ROWS:
                raise ValueError('cache column count')
            raw = b64decode(column['data'], validate=True)
            if kind in ('int', 'float'):
                if len(raw) != count * 8:
                    raise ValueError('cache numeric column')
                values = array('q' if kind == 'int' else 'd'); values.frombytes(raw)
                if sys.byteorder != 'little':
                    values.byteswap()
                values = values.tolist()
            elif kind == 'bool':
                if len(raw) != count or any(value not in (0, 1) for value in raw):
                    raise ValueError('cache boolean column')
                values = list(map(bool, raw))
            elif kind == 'json':
                values = json.loads(raw)
                if not isinstance(values, list) or len(values) != count:
                    raise ValueError('cache JSON column')
            else:
                raise ValueError('cache column kind')
            for field, value in (('missing', _MISSING), ('null', None)):
                for offset in column[field]:
                    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset < count:
                        raise ValueError('cache column mask')
                    values[offset] = value
            self.values[reference] = values
        return self.values[reference]

    def table(self, table):
        fields = table['fields']; references = table['columns']; count = table['count']
        if (not isinstance(fields, list) or not all(isinstance(field, str) for field in fields)
                or len(fields) != len(set(fields)) or len(fields) != len(references)
                or len(fields) > MAX_FIELDS or not isinstance(count, int) or isinstance(count, bool)
                or not 0 <= count <= MAX_ROWS):
            raise ValueError('cache quotation table')
        self.cells += count * max(1, len(fields))
        if self.cells > MAX_DECODED_CELLS:
            raise ValueError('cache decoded quotation quota')
        columns = [self.column(reference) for reference in references]
        if any(len(column) != count for column in columns):
            raise ValueError('cache table row count')
        return [{field: value for field, value in zip(fields, row) if value is not _MISSING}
                for row in zip(*columns)] if fields else [{} for _ in range(count)]


def _encode(key, context):
    from app.domain.strategy_prices import price_cache_key
    if (not isinstance(context, dict) or context.get('projection') or context.get('requested_price_basis') != 'forward'
            or context.get('price_basis') != 'forward_event_affine'
            or context.get('anchor_date') != key[3]
            or context.get('cache_key') != price_cache_key(key[2], key[3], 'forward', {'verified_closure_sha256': key[4]})
            or context.get('adjustment', {}).get('usable') is not True):
        raise ValueError('unverified price context')
    payload = {'key': list(key), 'context': {field: context.get(field) for field in _CONTEXT_FIELDS}, 'lanes': {}}
    columns = _Columns()
    for field in ('raw_rows', 'price_rows'):
        rows = context[field]
        if not isinstance(rows, (list, tuple)) or not all(isinstance(row, dict) for row in rows):
            raise ValueError('cache quotation lane')
        prepared = prepare_metric_bars(rows)
        payload['lanes'][field] = {'raw': columns.table(prepared), 'normalized': columns.table(prepared.normalized())}
    if sum(table['count'] * max(1, len(table['fields'])) for lane in payload['lanes'].values()
           for table in lane.values()) > MAX_DECODED_CELLS:
        raise ValueError('cache decoded quotation quota')
    payload['columns'] = columns.columns
    plain = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(plain) > MAX_DECOMPRESSED:
        raise ValueError('cache plain quota')
    compressed = zlib.compress(plain, 3)
    if len(compressed) > MAX_COMPRESSED:
        raise ValueError('cache compressed quota')
    return compressed, sha256(plain).hexdigest(), len(plain)


def _decode(key, record, *, structure_only=False):
    compressed, expected, plain_size = record
    if len(compressed) > MAX_COMPRESSED or not 0 <= plain_size <= MAX_DECOMPRESSED:
        raise ValueError('cache record quota')
    decoder = zlib.decompressobj()
    plain = decoder.decompress(compressed, MAX_DECOMPRESSED + 1)
    if (len(plain) != plain_size or len(plain) > MAX_DECOMPRESSED or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail or sha256(plain).hexdigest() != expected):
        raise ValueError('cache checksum or stream')
    value = json.loads(plain)
    if value['key'] != list(key):
        raise ValueError('cache identity')
    result = value['context']
    if (set(result) != set(_CONTEXT_FIELDS) or result['anchor_date'] != key[3]
            or result['requested_price_basis'] != 'forward' or result['price_basis'] != 'forward_event_affine'
            or result['adjustment'].get('usable') is not True):
        raise ValueError('cache context')
    columns = _ReadColumns(value['columns'])
    if structure_only:
        prepared = prepare_metric_bars([])
        prepared._normalized = tuple(columns.table(value['lanes']['price_rows']['normalized']))
        result.update(raw_rows=[], price_rows=prepared, projection='structure-only')
        return result
    for field in ('raw_rows', 'price_rows'):
        lane = value['lanes'][field]
        prepared = prepare_metric_bars(columns.table(lane['raw']))
        prepared._normalized = tuple(columns.table(lane['normalized']))
        result[field] = prepared
    return result


def _remove(key):
    global _bytes
    try:
        old = _cache.pop(key, None)
    except TypeError:
        return
    if old is not None:
        _bytes -= len(old[0])


def _remember_memory(key, record):
    global _bytes
    with _lock:
        _remove(key)
        if len(record[0]) > MAX_BYTES:
            return
        _cache[key] = record; _bytes += len(record[0])
        while len(_cache) > MAX_ENTRIES or _bytes > MAX_BYTES:
            _remove(next(iter(_cache)))


def _safe_directory(create):
    root = Path(work_dir()).absolute()
    # config.work_dir resolves its environment input; inspect the original too.
    raw = Path(os.environ.get('SHIXU_WORK_DIR', root)).expanduser().absolute()
    for location in (raw, root):
        for path in (location, *location.parents):
            if path.is_symlink():
                raise ValueError('cache path symlink')
    path = root / _DIRECTORY
    protected_roots = (published_dir(), sqlite_path().parent, raw_dir())
    for protected in protected_roots:
        if path.is_relative_to(Path(protected)):
            raise ValueError('cache must be disposable work data')
    if create:
        root.mkdir(parents=True, exist_ok=True)
        path.mkdir(mode=0o700, exist_ok=True)
    if not create and not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_dir():
        raise ValueError('cache directory unavailable')
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('cache directory must be private')
    return path


def _disk_connection(create, identity_out=None):
    directory = _safe_directory(create)
    if directory is None:
        return None
    root_info, directory_info = directory.parent.stat(), directory.stat()
    path = directory / _FILENAME
    for candidate in (path, Path(str(path) + '-journal'), Path(str(path) + '-wal'), Path(str(path) + '-shm')):
        if candidate.is_symlink():
            raise ValueError('cache file symlink')
    if not create and not path.is_file():
        return None
    # Precreate privately, without following a symlink. SQLite's transaction is
    # the atomic publication; this database is disposable, not a source ledger.
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    identity = os.fstat(descriptor)
    os.close(descriptor)
    if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid()
            or stat.S_IMODE(identity.st_mode) & 0o077 or identity.st_size > MAX_DISK_BYTES):
        raise ValueError('cache file ownership/type')
    connection = sqlite3.connect(path, timeout=.1)
    try:
        info = path.stat()
        if (info.st_dev, info.st_ino) != (identity.st_dev, identity.st_ino) or path.is_symlink():
            raise ValueError('cache file changed')
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA recursive_triggers=ON')
        connection.execute('PRAGMA busy_timeout=100')
        connection.execute('PRAGMA synchronous=OFF')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('PRAGMA cache_size=-2048')
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        connection.execute('PRAGMA max_page_count=' + str(max(16, MAX_DISK_BYTES // page_size)))
        if create:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS seed(
                    key_sha256 TEXT PRIMARY KEY, payload BLOB NOT NULL,
                    plain_sha256 TEXT NOT NULL, plain_bytes INTEGER NOT NULL,
                    compressed_bytes INTEGER NOT NULL, touched_ns INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS seed_lru ON seed(touched_ns,key_sha256);
                CREATE TABLE IF NOT EXISTS quota(singleton INTEGER PRIMARY KEY CHECK(singleton=1),bytes INTEGER NOT NULL);
                INSERT OR IGNORE INTO quota VALUES(1,0);
            ''')
        if identity_out is not None:
            current = _disk_identity()
            if current[1:] != (root_info.st_dev, root_info.st_ino, directory_info.st_dev,
                               directory_info.st_ino, identity.st_dev, identity.st_ino):
                raise ValueError('cache namespace changed while opening')
            identity_out.append(current)
        return connection
    except BaseException:
        connection.close()
        raise


def _disk_identity():
    """Recheck the current namespace and inode before reusing an open handle.

    A task can run long enough for another process to replace its disposable
    store. Never keep writing the unlinked old database, follow a new sidecar
    symlink, or reuse a handle after the configured work namespace changes.
    """
    directory = _safe_directory(False)
    if directory is None:
        raise ValueError('cache directory changed')
    path = directory / _FILENAME
    for candidate in (path, Path(str(path) + '-journal'), Path(str(path) + '-wal'), Path(str(path) + '-shm')):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if candidate == path:
                raise ValueError('cache file changed')
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('cache file ownership/type')
        if candidate == path and info.st_size > MAX_DISK_BYTES:
            raise ValueError('cache physical quota')
    root = directory.parent.stat()
    parent = directory.stat()
    info = path.lstat()
    return (str(path), root.st_dev, root.st_ino, parent.st_dev, parent.st_ino,
            info.st_dev, info.st_ino)


def _cache_size(value):
    # SQLite's non-STRICT INTEGER columns can contain arbitrary text/blobs.
    # Reject corruption before Python arithmetic can escape the cache fallback.
    if type(value) is not int or value < 0:
        raise ValueError('cache size metadata')
    return value


def _disk_record(record):
    if not isinstance(record, (tuple, list)) or len(record) != 3:
        raise ValueError('cache record shape')
    compressed, checksum, plain_size = record
    if not isinstance(compressed, bytes) or not isinstance(checksum, str):
        raise ValueError('cache record types')
    return compressed, checksum, _cache_size(plain_size)


def _write_records(connection, records, touches=()):
    """Publish pre-encoded records in one short, all-or-nothing transaction."""
    records = [(identity, _disk_record(record), touched) for identity, record, touched in records]
    try:
        connection.execute('BEGIN IMMEDIATE')
        if records:
            quota = connection.execute('SELECT bytes FROM quota WHERE singleton=1').fetchone()
            if quota is None:
                raise ValueError('cache quota unavailable')
            size = _cache_size(quota[0])
            budget = max(0, MAX_DISK_BYTES * 7 // 8)
            records = [(identity, record, touched) for identity, record, touched in records
                       if len(record[0]) <= budget]
            if records:
                identities = [value[0] for value in records]
                placeholders = ','.join('?' for _ in identities)
                previous = dict(connection.execute(
                    'SELECT key_sha256,compressed_bytes FROM seed WHERE key_sha256 IN (' + placeholders + ')',
                    identities).fetchall())
                previous = {identity: _cache_size(value) for identity, value in previous.items()}
                previous_bytes = sum(previous.values())
                if previous_bytes > size:
                    raise ValueError('cache quota inconsistent')
                projected = size - previous_bytes + sum(len(record[0]) for _, record, _ in records)
                if projected <= budget:
                    connection.executemany('INSERT OR REPLACE INTO seed VALUES(?,?,?,?,?,?)',
                        [(identity, record[0], record[1], record[2], len(record[0]), touched)
                         for identity, record, touched in records])
                    size = projected
                else:
                    # At capacity preserve the existing record-by-record LRU
                    # admission, including eviction of earlier batch entries.
                    for identity, record, touched in records:
                        compressed, checksum, plain_size = record
                        size -= previous.get(identity, 0)
                        while size + len(compressed) > budget:
                            oldest = connection.execute(
                                'SELECT key_sha256,compressed_bytes FROM seed WHERE key_sha256<>? '
                                'ORDER BY touched_ns,key_sha256 LIMIT 1', (identity,)).fetchone()
                            if oldest is None:
                                raise ValueError('cache quota inconsistent')
                            old_size = _cache_size(oldest[1])
                            connection.execute('DELETE FROM seed WHERE key_sha256=?', (oldest[0],))
                            size = _cache_size(size - old_size)
                            previous.pop(oldest[0], None)
                        connection.execute('INSERT OR REPLACE INTO seed VALUES(?,?,?,?,?,?)',
                            (identity, compressed, checksum, plain_size, len(compressed), touched))
                        previous[identity] = len(compressed)
                        size += len(compressed)
                connection.execute('UPDATE quota SET bytes=? WHERE singleton=1', (size,))
        if touches:
            connection.executemany('UPDATE seed SET touched_ns=? WHERE key_sha256=?', touches)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _disk_error():
    with _lock:
        _stats['disk_errors'] += 1


class _DiskSession:
    """One thread-owned connection; no transaction spans business work."""
    def __init__(self):
        self.owner = get_ident()
        self.epoch = _disk_epoch
        self.connection = None
        self.identity = None
        self.pending = OrderedDict()
        self.pending_bytes = 0
        self.touches = OrderedDict()
        self.misses = OrderedDict()
        self.disabled = False

    def discard(self):
        self.disabled = True
        self.pending.clear(); self.pending_bytes = 0
        self.touches.clear(); self.misses.clear()
        if self.connection is not None:
            try:
                self.connection.close()
            except sqlite3.Error:
                pass
            self.connection = None

    def ensure(self):
        if self.disabled or self.owner != get_ident() or self.epoch != _disk_epoch:
            raise ValueError('cache session closed or invalidated')
        if self.connection is None:
            # Schema/connection setup happens once per task, not per symbol.
            opened = []
            self.connection = _disk_connection(True, opened)
            self.identity = opened[0]
        elif _disk_identity() != self.identity:
            raise ValueError('cache namespace or inode changed')
        return self.connection

    def flush(self):
        if not self.pending and not self.touches:
            return
        connection = self.ensure()
        records = [(identity, record, touched) for identity, (record, touched) in self.pending.items()]
        _write_records(connection, records, [(touched, identity) for identity, touched in self.touches.items()])
        self.pending.clear(); self.pending_bytes = 0; self.touches.clear()

    def put(self, identity, record):
        self.ensure()
        if len(record[0]) > SESSION_MAX_BYTES:
            # Public encoders are much smaller than this limit. Retain the
            # bound even for another internal codec's oversized input.
            self.flush()
            _write_records(self.connection, [(identity, record, time.time_ns())])
            self.misses.pop(identity, None)
            return
        previous = self.pending.get(identity)
        extra = len(record[0]) - (len(previous[0][0]) if previous else 0)
        if self.pending and (len(self.pending) + (previous is None) > SESSION_MAX_RECORDS
                             or self.pending_bytes + extra > SESSION_MAX_BYTES):
            self.flush()
            previous = None
            extra = len(record[0])
        self.pending[identity] = (record, time.time_ns())
        self.pending_bytes += extra
        self.misses.pop(identity, None)
        self.touches.pop(identity, None)
        if len(self.pending) >= SESSION_MAX_RECORDS or self.pending_bytes >= SESSION_MAX_BYTES:
            self.flush()

    def get(self, identity):
        connection = self.ensure()
        pending = self.pending.get(identity)
        if pending is not None:
            record, _ = pending
            self.pending[identity] = (record, time.time_ns())
            return record
        if identity in self.misses:
            self.misses.move_to_end(identity)
            return None
        row = connection.execute('SELECT payload,plain_sha256,plain_bytes FROM seed WHERE key_sha256=?', (identity,)).fetchone()
        if row is None:
            self.misses[identity] = None
            while len(self.misses) > SESSION_MAX_MISSES:
                self.misses.popitem(last=False)
            return None
        self.touches[identity] = time.time_ns()
        if len(self.touches) >= SESSION_MAX_RECORDS:
            self.flush()
        return row


def _current_disk_session():
    session = _disk_session.get()
    # ContextVars may be copied into a worker thread. SQLite handles must never
    # cross that boundary; the helper's original one-shot path remains safe.
    return session if session is not None and session.owner == get_ident() else None


@contextmanager
def disk_session():
    """Optional task-local I/O reuse, also usable as a synchronous decorator.

    The buffers contain only already encoded immutable inputs. On cancellation
    or failure they can be dropped. Nested calls reuse the outer scope; an
    ordinary helper call outside this scope retains immediate persistence.
    """
    existing = _current_disk_session()
    if existing is not None:
        yield existing
        return
    session = _DiskSession()
    token = _disk_session.set(session)
    try:
        yield session
    except BaseException:
        raise
    else:
        try:
            with _disk_lock:
                session.flush()
        except (OSError, ValueError, sqlite3.Error):
            _disk_error()
    finally:
        with _disk_lock:
            session.discard()
        _disk_session.reset(token)


def _put_disk(identity, record):
    session = _current_disk_session()
    with _disk_lock:
        if session is not None:
            if not session.disabled:
                try:
                    session.put(identity, _disk_record(record))
                except (OSError, ValueError, sqlite3.Error):
                    session.discard(); _disk_error()
            return
        connection = _disk_connection(True)
        try:
            _write_records(connection, [(identity, _disk_record(record), time.time_ns())])
        finally:
            connection.close()


def _get_disk(identity):
    session = _current_disk_session()
    with _disk_lock:
        if session is not None:
            if session.disabled:
                return None
            try:
                return session.get(identity)
            except (OSError, ValueError, sqlite3.Error):
                session.discard(); _disk_error()
                return None
        connection = _disk_connection(False)
        if connection is None:
            return None
        try:
            row = connection.execute('SELECT payload,plain_sha256,plain_bytes FROM seed WHERE key_sha256=?', (identity,)).fetchone()
            if row is not None:
                connection.execute('UPDATE seed SET touched_ns=? WHERE key_sha256=?', (time.time_ns(), identity))
                connection.commit()
                return row
        finally:
            connection.close()
    return None


def remember(key, price_context):
    """Capture a pristine full technical seed; false means an optional miss."""
    try:
        identity = _identity(key)
        record = _encode(key, price_context)
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return False
    _remember_memory(key, record)
    with _lock:
        _stats['stored'] += 1
    try:
        _put_disk(identity, record)
    except (OSError, ValueError, sqlite3.Error):
        with _lock:
            _stats['disk_errors'] += 1
    return True


def _restore(key, *, structure_only=False):
    try:
        identity = _identity(key)
        with _lock:
            record = _cache.get(key)
            if record is not None:
                _cache.move_to_end(key)
        origin = 'memory_hits'
        if record is None:
            record = _get_disk(identity); origin = 'disk_hits'
        if record is None:
            with _lock:
                _stats['misses'] += 1
            return None
        result = _decode(key, record, structure_only=structure_only)
        _remember_memory(key, record)
        with _lock:
            _stats[origin] += 1
            if structure_only:
                _stats['structure_hits'] += 1
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError, sqlite3.Error, zlib.error):
        with _lock:
            _remove(key); _stats['misses'] += 1
        return None


def restore(key):
    """Return private prepared lanes. Always verify actual sources beforehand."""
    return _restore(key)


def restore_structure(key):
    """Project only strict structure inputs, never a complete indicator seed.

    Use only when the ordinary D/W/M summaries have already been captured.
    The empty raw list makes accidental ordinary recomputation visibly wrong;
    remember explicitly rejects this projection instead of replacing a seed.
    """
    return _restore(key, structure_only=True)


def clear_cache(*, disk=False):
    """Ordinary clear is memory-only; opt-in disk clear touches only our table."""
    global _bytes, _disk_epoch
    with _lock:
        _cache.clear(); _bytes = 0
        for field in _stats:
            _stats[field] = 0
    if disk:
        try:
            with _disk_lock:
                # Existing sessions must not republish pre-clear buffers.
                _disk_epoch += 1
                connection = _disk_connection(False)
                if connection is not None:
                    try:
                        connection.execute('DELETE FROM seed')
                        connection.execute('UPDATE quota SET bytes=0 WHERE singleton=1')
                        connection.commit()
                    finally:
                        connection.close()
        except (OSError, ValueError, sqlite3.Error):
            pass


def cache_stats():
    with _lock:
        return {'entries': len(_cache), 'compressed_bytes': _bytes,
                'max_entries': MAX_ENTRIES, 'max_bytes': MAX_BYTES,
                'max_disk_bytes': MAX_DISK_BYTES, 'max_compressed': MAX_COMPRESSED,
                'max_decompressed': MAX_DECOMPRESSED, **_stats}
