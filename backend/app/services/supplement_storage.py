"""Immutable per-date supplement objects and small, backwards-compatible indexes."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
import threading

from app.infrastructure.storage import atomic_json, digest, dumps

STORAGE_VERSION = 'supplement-index-v2'
FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools', 'fund_flows', 'sector_index_history')
_cache = OrderedDict()
_cache_bytes = 0
_lock = threading.RLock()


def _read_object(root, pointer):
    global _cache_bytes
    if not isinstance(pointer, dict) or set(pointer) != {'record_ref', 'sha256'}:
        raise ValueError('补充数据索引引用格式无效')
    relative, expected = pointer['record_ref'], pointer['sha256']
    if not isinstance(relative, str) or not re.fullmatch(r'artifacts/[0-9a-f]{64}\.json', relative):
        raise ValueError('补充数据引用越出已发布对象目录')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('补充数据对象缺少SHA')
    root = Path(root).resolve()
    path = root / relative
    if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('补充数据对象不接受符号链接')
    try:
        info = path.stat()
    except OSError as error:
        raise ValueError('补充数据索引引用的对象缺失') from error
    if not path.is_file() or info.st_size > 32 * 1024 * 1024:
        raise ValueError('补充数据对象类型或体积不受支持')
    key = (str(path), expected, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            # Keep immutable verified JSON bytes, not a large shared Python
            # tree that needs a recursive deepcopy on every chart/summary.
            # Decoding produces a caller-owned value and keeps the same file
            # identity/integrity checks before every cache hit.
            return json.loads(cached[0])
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('补充数据索引引用的对象SHA不一致')
    value = json.loads(content)
    if not isinstance(value, dict) or digest(value) != path.stem:
        raise ValueError('补充数据对象内容标识不一致')
    with _lock:
        if key not in _cache:
            _cache[key] = (content, len(content)); _cache_bytes += len(content)
        while len(_cache) > 1024 or _cache_bytes > 32 * 1024 * 1024:
            _, (_, size) = _cache.popitem(last=False); _cache_bytes -= size
    return value


def _read_record(root, pointer):
    value = _read_object(root, pointer)
    references = value.pop('__supplement_fields_v1', None)
    if references is None:
        return value
    if not isinstance(references, dict) or len(references) > 64:
        raise ValueError('补充数据分字段引用无效')
    for key, reference in references.items():
        if not isinstance(key, str) or key in value or key == '__supplement_fields_v1':
            raise ValueError('补充数据分字段引用与内容冲突')
        payload = _read_object(root, reference)
        if set(payload) != {'__supplement_payload_v1'}:
            raise ValueError('补充数据分字段对象格式无效')
        value[key] = payload['__supplement_payload_v1']
    return value


def _store_object(root, value):
    content = (dumps(value) + '\n').encode()
    expected = hashlib.sha256(content).hexdigest()
    relative = 'artifacts/' + digest(value) + '.json'
    path = root / relative
    if not path.exists():
        atomic_json(path, value)
    pointer = {'record_ref': relative, 'sha256': expected}
    _read_object(root, pointer)
    return pointer


def compact_document(root, document):
    root = Path(root)
    result = dict(document)
    count = 0
    for field in FIELDS:
        values = document.get(field)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ValueError('补充数据日期索引需要映射结构')
        references = {}
        for day, record in values.items():
            count += 1
            if count > 4096:
                raise ValueError('补充数据日期对象超过有界索引上限')
            if not isinstance(record, dict):
                references[day] = record
                continue
            if '__supplement_fields_v1' in record:
                raise ValueError('补充数据包含保留的存储字段')
            stored, payloads = dict(record), {}
            # Validation dates/signatures can change while the bulky quoted
            # rows remain identical. Share those rows independently of context.
            for key, value in record.items():
                if isinstance(value, (list, dict)) and len(dumps(value).encode()) >= 2048:
                    payloads[key] = _store_object(root, {'__supplement_payload_v1': value})
                    del stored[key]
            if payloads:
                stored['__supplement_fields_v1'] = payloads
            pointer = _store_object(root, stored)
            references[day] = pointer
        result[field] = references
    result['storage_version'] = STORAGE_VERSION
    return result


def expand_document(root, document, *, dates=None, fields=None):
    version = document.get('storage_version')
    if version not in (None, STORAGE_VERSION):
        raise ValueError('补充数据索引存储版本不受支持')
    selected = set(FIELDS) if fields is None else set(fields)
    if selected - set(FIELDS):
        raise ValueError('补充数据选择字段不受支持')
    allowed = None if dates is None else set(dates)
    if allowed is not None and (len(allowed) > 4096 or any(not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) for day in allowed)):
        raise ValueError('补充数据选择日期不受支持')
    result = {key: value for key, value in document.items() if key != 'storage_version'}
    count = 0
    for field in FIELDS:
        if field not in document:
            continue
        if field not in selected:
            result.pop(field, None)
            continue
        if not isinstance(document[field], dict):
            raise ValueError('补充数据日期索引需要映射结构')
        values = {}
        for day, pointer in document[field].items():
            if allowed is not None and day not in allowed:
                continue
            count += 1
            if count > 4096:
                raise ValueError('补充数据日期对象超过有界索引上限')
            values[day] = _read_record(root, pointer) if version == STORAGE_VERSION and isinstance(pointer, dict) else pointer
        result[field] = values
    return result
