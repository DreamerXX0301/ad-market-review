"""Compact fixed-data ranking facts in the shared bounded pane cache."""
from base64 import b64decode, b64encode
import json
import zlib

from app.infrastructure.storage import json_clean
from app.services.pane_read_cache import read_block


_MAX_DECODED_BYTES = 16 * 1024 * 1024


class _Uncacheable(Exception):
    pass


def _pack(value):
    try:
        raw = json.dumps(json_clean(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise _Uncacheable() from error
    if len(raw) > _MAX_DECODED_BYTES:
        raise _Uncacheable()
    return b64encode(zlib.compress(raw, level=1)).decode('ascii')


def _unpack(packed):
    source = b64decode(packed, validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(source, _MAX_DECODED_BYTES + 1)
    if len(raw) > _MAX_DECODED_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError('无效的排名缓存')
    return json.loads(raw)


def read_rank_block(name, day, proof, market, build):
    if proof is None:
        return build()
    result = None

    def fill():
        nonlocal result
        result = build()
        return _pack(result)

    try:
        packed = read_block(name, day, proof, fill, market=market)
    except _Uncacheable:
        return result
    if result is not None:
        return result
    try:
        return _unpack(packed)
    except (ValueError, TypeError, zlib.error):
        return build()
