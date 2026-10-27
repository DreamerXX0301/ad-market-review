"""Verified technical summaries shared by API and the sole worker.

Only fixed-source, same-anchor forward-price components are eligible. Rule
decisions, local memberships and policies never enter this disposable store.
The caller must validate the actual source closure before using a summary.
"""
from hashlib import sha256
import json
import sqlite3
from threading import RLock
import zlib

from app.core import data_dir
from app.domain.strategy_prices import price_cache_key
from app.services import condition_basis_cache as basis

VERSION = 'condition-technical-proof-v1'
MAX_PLAIN = 256 * 1024
MAX_COMPRESSED = 128 * 1024
_lock = RLock()
_stats = {'hits': 0, 'misses': 0, 'stored': 0, 'errors': 0, 'record_hits': 0}


def _scope(key, kind, parameter):
    if (not isinstance(key, tuple) or len(key) != 10 or key[0] != str(data_dir())
            or not isinstance(key[1], tuple) or len(key[1]) != 2
            or not isinstance(key[1][0], str) or not key[1][0]
            or not isinstance(key[1][1], str) or len(key[1][1]) != 64
            or key[4] not in ('D', 'W', 'M') or key[7] != 'forward'
            or key[8] != key[3] or kind not in ('ordinary', 'higher_lows', 'boll', 'strict-input')):
        return None
    int(key[1][1], 16)
    if key[9] != price_cache_key(key[2], key[3], 'forward', {'verified_closure_sha256': key[1][1]}):
        return None
    scope = json.dumps([VERSION, basis.VERSION, basis.NORMALIZATION_VERSION,
                        key, kind, parameter], ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False)
    return scope, sha256(scope.encode()).hexdigest()


def read_record(key, kind, parameter=None):
    """Read a bounded envelope; its full validation may run in a pure child."""
    try:
        scoped = _scope(key, kind, parameter)
        if scoped is None:
            return None
        scope, identity = scoped
        record = basis._get_disk(identity)
        if record is None:
            with _lock: _stats['misses'] += 1
            return None
        compressed, checksum, size = basis._disk_record(record)
        if len(compressed) > MAX_COMPRESSED or not 0 <= size <= MAX_PLAIN:
            raise ValueError('technical summary quota')
        with _lock: _stats['record_hits'] = _stats.get('record_hits', 0) + 1
        return scope, (compressed, checksum, size)
    except (ValueError, TypeError, KeyError, OverflowError, OSError, sqlite3.Error, zlib.error):
        with _lock: _stats['errors'] += 1
        return None


def decode_record(bound):
    """Pure checksum, size, stream and exact-scope validation, shared by readers."""
    try:
        scope, record = bound
        if not isinstance(scope, str):raise ValueError('technical summary scope')
        compressed, checksum, size = basis._disk_record(record)
        if len(compressed) > MAX_COMPRESSED or not 0 <= size <= MAX_PLAIN:
            raise ValueError('technical summary quota')
        decoder = zlib.decompressobj()
        plain = decoder.decompress(compressed, MAX_PLAIN + 1)
        if (len(plain) != size or len(plain) > MAX_PLAIN or not decoder.eof
                or decoder.unused_data or decoder.unconsumed_tail
                or sha256(plain).hexdigest() != checksum):
            raise ValueError('technical summary checksum/stream')
        payload = json.loads(plain)
        if not isinstance(payload, dict) or payload.get('scope') != scope or not isinstance(payload.get('value'), dict):
            raise ValueError('technical summary scope')
        with _lock: _stats['hits'] += 1
        return payload['value']
    except (ValueError, TypeError, KeyError, OverflowError, OSError, sqlite3.Error, zlib.error):
        with _lock: _stats['errors'] += 1
        return None


def load(key, kind, parameter=None):
    bound = read_record(key, kind, parameter)
    return decode_record(bound) if bound is not None else None


def encode_record(key, kind, value, parameter=None):
    """Pure encoding; publishing remains the parent process's responsibility."""
    try:
        scoped = _scope(key, kind, parameter)
        if scoped is None or not isinstance(value, dict):
            return None
        scope, identity = scoped
        # Ordinary inputs contain no runtime topic/policy decisions. Reject a
        # caller accidentally passing a complete strategy/round result.
        if (any(field in value for field in ('decision', 'checks', 'policy', 'current_topics',
                '_complete_topic_ids', 'identity_scope', 'tracking_round', 'current_policy', 'entry_basis'))
                or value.get('topic_ids') or any(value.get(field) is not None for field in
                    ('tracking_change_pct', 'trading_age', 'board', 'is_st'))):
            return None
        plain = json.dumps({'scope': scope, 'value': value}, ensure_ascii=False,
                           separators=(',', ':'), allow_nan=False).encode()
        if len(plain) > MAX_PLAIN:
            return None
        compressed = zlib.compress(plain, 3)
        if len(compressed) > MAX_COMPRESSED:
            return None
        return scope, (compressed, sha256(plain).hexdigest(), len(plain))
    except (ValueError, TypeError, KeyError, OverflowError, OSError, sqlite3.Error, zlib.error):
        with _lock: _stats['errors'] += 1
        return None


def store_encoded(key, kind, bound, parameter=None):
    try:
        scoped = _scope(key, kind, parameter)
        if scoped is None or not isinstance(bound, tuple) or len(bound) != 2 or bound[0] != scoped[0]:return False
        return _publish_record(scoped[1],bound[1])
    except (ValueError, TypeError, KeyError, OverflowError, OSError, sqlite3.Error, zlib.error):
        with _lock: _stats['errors'] += 1
        return False


def _publish_record(identity,record):
    try:
        record = basis._disk_record(record)
        if len(record[0]) > MAX_COMPRESSED or record[2] > MAX_PLAIN:return False
        basis._put_disk(identity, record)
        with _lock: _stats['stored'] += 1
        return True
    except (ValueError, TypeError, KeyError, OverflowError, OSError, sqlite3.Error, zlib.error):
        with _lock: _stats['errors'] += 1
        return False


def store(key, kind, value, parameter=None):
    bound = encode_record(key, kind, value, parameter)
    return _publish_record(sha256(bound[0].encode()).hexdigest(),bound[1]) if bound is not None else False


def cache_stats():
    with _lock: return dict(_stats)
