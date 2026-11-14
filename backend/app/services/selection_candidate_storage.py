"""Lossless storage for the three JSON text columns of a stock candidate."""

import json
import zlib


# Version 1 is a zlib stream of a UTF-8 JSON array of the three original
# strings. The original JSON text is evidence, including escapes and spacing.
_V1_HEADER = b'SCE\x01'
_MAX_FRAME_BYTES = 4 * 1024 * 1024
# Count the three replacement TEXT values and allow for record header growth.
_RECORD_OVERHEAD_BYTES = len('{}{}[]') + 8
_JSON_COLUMNS = ('checks_json', 'metrics_json', 'topic_snapshot_json')
_INVALID_BLOB = '策略候选压缩证据无效，无法读取原始依据'


def pack_evidence(checks_json: str, metrics_json: str, topic_snapshot_json: str) -> bytes | None:
    """Return compressed JSON text if it fits the codec and costs fewer bytes."""
    fields = (checks_json, metrics_json, topic_snapshot_json)
    # Reject large legacy values before building a second copy or compressing.
    if sum(map(len, fields)) > _MAX_FRAME_BYTES:
        return None
    text_size = sum(len(field) if field.isascii() else len(field.encode('utf-8')) for field in fields)
    if text_size > _MAX_FRAME_BYTES or text_size <= len(_V1_HEADER) + _RECORD_OVERHEAD_BYTES:
        return None
    frame = json.dumps(fields, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(frame) > _MAX_FRAME_BYTES:
        return None
    blob = _V1_HEADER + zlib.compress(frame, level=1)
    return blob if len(blob) + _RECORD_OVERHEAD_BYTES < text_size else None


def unpack_evidence(blob: bytes) -> tuple[str, str, str]:
    """Read the original JSON strings, rejecting unsupported or damaged blobs."""
    if not isinstance(blob, bytes) or not blob.startswith(_V1_HEADER) or len(blob) > _MAX_FRAME_BYTES:
        raise ValueError(_INVALID_BLOB)
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(memoryview(blob)[len(_V1_HEADER):], _MAX_FRAME_BYTES + 1)
        if len(raw) > _MAX_FRAME_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError(_INVALID_BLOB)
        fields = json.loads(raw.decode('utf-8'))
    except (zlib.error, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(_INVALID_BLOB) from error
    if not isinstance(fields, list) or len(fields) != 3 or not all(isinstance(field, str) for field in fields):
        raise ValueError(_INVALID_BLOB)
    return fields[0], fields[1], fields[2]


def candidate_evidence_json(row) -> tuple[str, str, str]:
    """Read a sqlite3.Row or mapping from either layout without reserializing."""
    try:
        blob = row['packed_evidence']
    except (IndexError, KeyError):
        blob = None
    if blob is not None:
        return unpack_evidence(blob)
    checks, metrics, topics = (row[column] for column in _JSON_COLUMNS)
    return checks, metrics, topics


def candidate_evidence(row):
    """Read the candidate's checks, metrics and topics as JSON values."""
    checks, metrics, topics = candidate_evidence_json(row)
    try:
        return json.loads(checks), json.loads(metrics), json.loads(topics)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError('策略候选证据的 JSON 无效，无法读取原始依据') from error


def reference_scan_rows(connection, table, columns, *, where=''):
    """Stream the same positional text values before and after the upgrade."""
    def quote(name):
        return '"' + name.replace('"', '""') + '"'

    quoted_table = quote(table)
    selected = [quote(column) for column in columns]
    locations = []
    if table == 'selection_candidate':
        locations = [(index, _JSON_COLUMNS.index(column)) for index, column in enumerate(columns) if column in _JSON_COLUMNS]
        if locations and any(row[1] == 'packed_evidence' for row in connection.execute('PRAGMA table_info(' + quoted_table + ')')):
            selected.append('"packed_evidence"')
        else:
            locations = []

    rows = connection.execute('SELECT ' + ','.join(selected) + ' FROM ' + quoted_table + where)
    if not locations:
        yield from rows
        return
    for row in rows:
        values = row[:-1]
        if row[-1] is not None:
            fields = unpack_evidence(row[-1])
            values = list(values)
            for position, field in locations:
                values[position] = fields[field]
        yield values
