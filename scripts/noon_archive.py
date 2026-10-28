"""午评业务快照的精确备份闭包；只用标准库，不扫描raw目录或读取凭据。"""
from __future__ import annotations

import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 32768
_JOB = re.compile(r'job_[0-9a-f]{32}\Z')
_FILE = re.compile(r'[A-Za-z0-9_-]{1,160}\.json\.gz\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def source_metadata(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid noon source metadata')
    key = value.get('path')
    if not isinstance(key, str) or '\\' in key:
        raise ValueError('Unsafe noon source path')
    path = PurePosixPath(key); parts = path.parts
    if (path.is_absolute() or str(path) != key or len(parts) != 3 or parts[0] != 'noon'
            or not _JOB.fullmatch(parts[1]) or not (_FILE.fullmatch(parts[2]) or parts[2] == 'manifest.json')):
        raise ValueError('Unsafe noon source path')
    if (any(not isinstance(value.get(field), str) or not _SHA.fullmatch(value[field]) for field in ('sha256', 'stored_sha256'))
            or isinstance(value.get('bytes'), bool) or not isinstance(value.get('bytes'), int)
            or not 0 < value['bytes'] <= MAX_BYTES):
        raise ValueError('Invalid noon source checksum or size')
    return {field: value[field] for field in ('path', 'sha256', 'stored_sha256', 'bytes')}


def read_source(raw_root, source):
    source = source_metadata(source)
    try:
        directory = os.open(Path(raw_root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise ValueError('Noon raw root is missing or unsafe') from error
    descriptor = None
    try:
        parts = PurePosixPath(source['path']).parts
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = following
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != source['bytes']:
            raise ValueError('Noon source size or file type differs from snapshot')
        chunks = []; size = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > source['bytes']:
                raise ValueError('Noon source changed during read')
            chunks.append(block)
        content = b''.join(chunks); after = os.fstat(descriptor)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if identity(before) != identity(after) or sha256(content).hexdigest() != source['stored_sha256']:
            raise ValueError('Noon stored source checksum mismatch')
        if parts[-1].endswith('.gz'):
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as reader:
                original = reader.read(MAX_BYTES + 1)
            if len(original) > MAX_BYTES:
                raise ValueError('Noon source decompression exceeds limit')
        else:
            original = content
        if sha256(original).hexdigest() != source['sha256']:
            raise ValueError('Noon canonical source checksum mismatch')
        return content, original
    except (OSError, EOFError) as error:
        raise ValueError('Noon source is missing, unsafe or unreadable') from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def inventory(connection, raw_root):
    """按捕获SQLite中的正文及manifest闭包核验，不枚举磁盘历史。"""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_noon_snapshot'").fetchone():
        return {}
    required = {}; verified = set(); manifests = {}
    for row in connection.execute('SELECT snapshot_id,trade_date,job_id,content_sha256,payload_json,sources_json FROM market_noon_snapshot ORDER BY trade_date'):
        record = dict(row)
        if sha256(record['payload_json'].encode()).hexdigest() != record['content_sha256']:
            raise ValueError('Noon snapshot content checksum mismatch')
        try:
            payload = json.loads(record['payload_json']); sources = json.loads(record['sources_json'])
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid noon snapshot JSON') from error
        if (not isinstance(payload, dict) or payload.get('date') != record['trade_date']
                or payload.get('version') != 'noon-snapshot-v1' or not isinstance(sources, list)):
            raise ValueError('Invalid noon snapshot identity')
        local = {}
        for source in sources:
            if isinstance(source, dict) and 'path' not in source:
                continue
            meta = source_metadata(source); key = meta['path']
            if key in local:
                raise ValueError('Duplicate noon source in snapshot')
            if key in required and required[key] != meta:
                raise ValueError('Conflicting noon source checksums')
            local[key] = meta; required[key] = meta
            if len(required) > MAX_FILES:
                raise ValueError('Noon backup closure exceeds file limit')
            if key not in verified:
                original = read_source(raw_root, meta)[1]
                if PurePosixPath(key).name == 'manifest.json':
                    manifests[key] = original
                verified.add(key)
        manifest_key = f"noon/{record['job_id']}/manifest.json"
        if manifest_key not in local:
            raise ValueError('Noon snapshot lacks its source manifest')
        try:
            manifest = json.loads(manifests[manifest_key])
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid noon source manifest') from error
        if (not isinstance(manifest, dict) or manifest.get('version') != 'noon-snapshot-v1'
                or manifest.get('job_id') != record['job_id'] or manifest.get('date') != record['trade_date']
                or not isinstance(manifest.get('sources'), list)):
            raise ValueError('Noon source manifest identity differs from snapshot')
        declared = {}
        for source in manifest['sources']:
            if isinstance(source, dict) and 'path' not in source:
                continue
            meta = source_metadata(source)
            if meta['path'] in declared:
                raise ValueError('Duplicate noon manifest dependency')
            declared[meta['path']] = meta
        if declared != {key: value for key, value in local.items() if key != manifest_key}:
            raise ValueError('Noon source manifest dependency closure differs from snapshot')
    return required


def copy_sources(required, raw_root, destination):
    """按已核验清单再次核对并拷贝；输出只能位于全新备份staging目录。"""
    for key, source in sorted(required.items()):
        content, _ = read_source(raw_root, source)
        target = Path(destination) / 'raw' / PurePosixPath(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as writer:
            writer.write(content)
        target.chmod(0o600)
        if sha256(target.read_bytes()).hexdigest() != source['stored_sha256']:
            raise ValueError('Noon backup copy checksum mismatch')
