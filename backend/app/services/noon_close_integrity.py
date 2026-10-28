"""Validate self-contained SQLite close-review evidence without opening files.

This module intentionally imports no application configuration or database code.
Backups and collectors can distinguish provenance from a live file dependency
only after every retained derived record has passed these checks.
"""
from hashlib import sha256
import json
import math
import re

TABLES = frozenset({'market_noon_close_review', 'market_noon_close_item', 'market_noon_close_attempt'})
SHA = re.compile(r'[a-f0-9]{64}\Z')


def _records(connection, sql, parameters=()):
    cursor = connection.execute(sql, parameters)
    names = [column[0] for column in cursor.description]
    for row in cursor:
        yield dict(zip(names, row))


def _document(encoded, expected=None, *, array=False):
    if not isinstance(encoded, str) or len(encoded.encode()) > 16 * 1024 * 1024:
        raise ValueError('Noon close evidence exceeds validation budget')
    if expected is not None and (not isinstance(expected, str) or not SHA.fullmatch(expected)
                                 or sha256(encoded.encode()).hexdigest() != expected):
        raise ValueError('Noon close evidence checksum mismatch')
    def reject(value):
        raise ValueError('Noon close evidence contains nonfinite JSON')
    value = json.loads(encoded, parse_constant=reject)
    if not isinstance(value, list if array else dict):
        raise ValueError('Noon close evidence JSON shape mismatch')
    return value


def validate_close_reviews(connection, review_id=None):
    """Stream exact stored records; never scan market directories or follow heads."""
    present = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not TABLES & present:
        return {'reviews': 0, 'items': 0}
    if not TABLES <= present or 'market_noon_snapshot' not in present:
        raise ValueError('Noon close evidence schema is incomplete')
    reviews = items = 0
    sql = '''SELECT r.*,s.content_sha256 AS parent_sha,s.trade_date AS parent_day
        FROM market_noon_close_review r LEFT JOIN market_noon_snapshot s USING(snapshot_id)
        WHERE (? IS NULL OR r.review_id=?) ORDER BY r.review_id'''
    for record in _records(connection, sql, (review_id, review_id)):
        row = dict(record)
        if (row['rule_version'] != 'noon-close-v1' or row['parent_sha'] != row['noon_sha256']
                or row['parent_day'] != row['trade_date']):
            raise ValueError('Noon close parent identity mismatch')
        inputs = _document(row['input_json'], row['close_input_sha256'])
        result = _document(row['result_json'], row['result_sha256'])
        if inputs.get('date') != row['trade_date'] or result.get('date', row['trade_date']) != row['trade_date']:
            raise ValueError('Noon close evidence date mismatch')
        provenance_sha = result.get('provenance_sha256')
        if not isinstance(provenance_sha, str):
            raise ValueError('Noon close provenance checksum is missing')
        _document(row['provenance_json'], provenance_sha)
        aggregate = sha256(b'['); count = 0
        for saved in _records(connection, 'SELECT * FROM market_noon_close_item WHERE review_id=? ORDER BY instrument_id', (row['review_id'],)):
            saved = dict(saved)
            item = _document(saved['item_json'], saved['item_sha256'])
            if (saved['snapshot_id'] != row['snapshot_id'] or item.get('instrument_id') != saved['instrument_id']
                    or item.get('name') != saved['name'] or item.get('board') != saved['board_key']
                    or item.get('transition') != saved['transition']):
                raise ValueError('Noon close item identity differs from its index')
            for key in ('final_board_count', 'afternoon_change_pct', 'close_change_pct', 'noon_change_pct'):
                if item.get(key) != saved[key] or isinstance(item.get(key), float) and not math.isfinite(item[key]):
                    raise ValueError('Noon close numeric index differs from item evidence')
            if item.get('close_amount') != saved['amount']:
                raise ValueError('Noon close amount index differs from item evidence')
            if item.get('sections', []) != _document(saved['sections_json'], array=True):
                raise ValueError('Noon close section index differs from item evidence')
            if item.get('classification_keys', []) != _document(saved['classification_keys_json'], array=True):
                raise ValueError('Noon close classification index differs from item evidence')
            if count:
                aggregate.update(b',')
            aggregate.update(saved['item_json'].encode()); count += 1
        aggregate.update(b']')
        if result.get('items_count') != count or result.get('items_sha256') != aggregate.hexdigest():
            raise ValueError('Noon close item inventory checksum mismatch')
        reviews += 1; items += count
    if review_id is not None and not reviews:
        raise ValueError('Noon close review evidence is missing')
    if connection.execute('''SELECT 1 FROM market_noon_close_item i
            LEFT JOIN market_noon_close_review r USING(review_id)
            WHERE (r.review_id IS NULL OR r.snapshot_id!=i.snapshot_id)
            AND (? IS NULL OR i.review_id=?) LIMIT 1''', (review_id, review_id)).fetchone():
        raise ValueError('Noon close orphan item evidence')
    return {'reviews': reviews, 'items': items}
