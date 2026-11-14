"""Exact intraday monitoring archive closure from captured SQLite only.

Standard-library tooling: no application initialization, credentials, DuckDB or
directory enumeration. File identities and checksums are checked again on copy.
"""
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

VERSION = 'intraday-monitor-v1'
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_FILES = 32768
_BATCH = re.compile(r'[A-Za-z0-9_-]{1,160}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def file_metadata(value, *, root=None, day=None, batch_id=None):
    if not isinstance(value, dict) or value.get('root') not in ('published', 'raw'):
        raise ValueError('Invalid intraday monitor file metadata')
    kind, key = value['root'], value.get('path')
    if root is not None and kind != root:
        raise ValueError('Invalid intraday monitor file root')
    if not isinstance(key, str) or '\\' in key:
        raise ValueError('Unsafe intraday monitor file path')
    path = PurePosixPath(key)
    suffix = '.parquet' if kind == 'published' else '.json.gz'
    parts = path.parts
    if (path.is_absolute() or str(path) != key or len(parts) != 3
            or parts[0] != 'intraday-monitor' or not parts[2].endswith(suffix)):
        raise ValueError('Unsafe intraday monitor file path')
    try:
        valid_day = date.fromisoformat(parts[1]).isoformat() == parts[1]
    except ValueError:
        valid_day = False
    identifier = parts[2][:-len(suffix)]
    if (not valid_day or not _BATCH.fullmatch(identifier)
            or day is not None and parts[1] != day
            or batch_id is not None and identifier != batch_id):
        raise ValueError('Intraday monitor file identity differs from its batch')
    if (not isinstance(value.get('sha256'), str) or not _SHA.fullmatch(value['sha256'])
            or type(value.get('bytes')) is not int or not 0 < value['bytes'] <= MAX_FILE_BYTES):
        raise ValueError('Invalid intraday monitor file checksum or size')
    return {field: value[field] for field in ('root', 'path', 'sha256', 'bytes')}


def read_file(root, metadata):
    """Read one regular, non-symlink file through exact relative descriptors."""
    metadata = file_metadata(metadata)
    directory = descriptor = None
    try:
        directory = os.open(Path(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parts = PurePosixPath(metadata['path']).parts
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != metadata['bytes']:
            raise ValueError('Intraday monitor file size or type differs from manifest')
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > metadata['bytes']:
                raise ValueError('Intraday monitor file changed during read')
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        content = b''.join(chunks)
        if identity(before) != identity(after) or sha256(content).hexdigest() != metadata['sha256']:
            raise ValueError('Intraday monitor file checksum mismatch or changed during read')
        return content
    except OSError as error:
        raise ValueError('Intraday monitor file is missing, unsafe or unreadable') from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def inventory(connection, published_root, raw_root):
    """Skip purged or fileless receipts; validate both files for every capture."""
    required = {'published': {}, 'raw': {}}
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_monitor_batch'").fetchone():
        return required
    roots = {'published': published_root, 'raw': raw_root}
    for batch_id, day, encoded, purged in connection.execute('''SELECT batch_id,trade_date,manifest_json,purged_at_ms
            FROM intraday_monitor_batch ORDER BY trade_date,slot_key,batch_id'''):
        if purged is not None:
            continue
        try:
            manifest = json.loads(encoded)
        except (ValueError, TypeError) as error:
            raise ValueError('Invalid intraday monitor manifest JSON') from error
        if not isinstance(manifest, dict):
            raise ValueError('Invalid intraday monitor manifest')
        if not {'quote_file', 'raw_file'}.intersection(manifest):
            continue
        if (manifest.get('version') != VERSION or not {'quote_file', 'raw_file'} <= manifest.keys()
                or not isinstance(batch_id, str) or not _BATCH.fullmatch(batch_id)):
            raise ValueError('Intraday monitor batch lacks its complete file pair')
        for field, kind in (('quote_file', 'published'), ('raw_file', 'raw')):
            meta = file_metadata(manifest[field], root=kind, day=day, batch_id=batch_id)
            key = meta['path']
            previous = required[kind].get(key)
            if previous is not None and previous != meta:
                raise ValueError('Conflicting intraday monitor file checksums')
            if previous is None:
                read_file(roots[kind], meta)
                required[kind][key] = meta
            if sum(len(items) for items in required.values()) > MAX_FILES:
                raise ValueError('Intraday monitor archive closure exceeds file limit')
    return required


def copy_raw_sources(required, raw_root, destination):
    for key, metadata in sorted(required.items()):
        meta = file_metadata(metadata, root='raw')
        if key != meta['path']:
            raise ValueError('Intraday monitor copy key differs from metadata')
        content = read_file(raw_root, meta)
        target = Path(destination) / 'raw' / PurePosixPath(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as writer:
            writer.write(content)
        target.chmod(0o600)
        read_file(Path(destination) / 'raw', meta)
