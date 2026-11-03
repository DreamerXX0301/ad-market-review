"""Freeze a bounded prior-close observation fragment before a noon capture.

The collector freezes the original fragment. A read-only compatibility projection
may recover missing classifications while the exact source remains retained. IDs and SHAs are not
foreign keys or dependencies that indefinitely pin earlier noon snapshots.
"""
from hashlib import sha256
from datetime import date, datetime, timezone, timedelta
import json
import math
import re
import sqlite3

from app.core import connect, dumps
from app.services.noon_close_integrity import validate_close_reviews

MAX_ITEMS = 600
MAX_BYTES = 256 * 1024
STOCK = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
KEYS = {'resealed', 'opened', 'new_limit_up'}
SCOPE = '仅冻结采集开始前已生成的严格前日收盘回看小摘要；不追读当前发布头，不改变正常昨日涨停及连板分组。'
SHA = re.compile(r'[0-9a-f]{64}\Z')
REVIEW_ID = re.compile(r'[A-Za-z0-9_-]{1,120}\Z')
SECTOR = re.compile(r'\d{6}\.TI\Z')


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _empty(day, reason):
    return {'date': day, 'state': 'unavailable', 'review_id': None, 'generated_at_ms': None,
            'result_sha256': None, 'close_input_sha256': None, 'groups': [], 'items': [],
            'coverage': {'requested_items': 0, 'retained_items': 0, 'omitted_items': 0},
            'reason': reason, 'scope': SCOPE}


def validated_frozen_provenance(payload):
    """Return the one self-contained fragment label that is not a live pin.

    This performs no I/O. Retention must additionally verify the enclosing
    immutable payload SHA and its SQL date/start identity before masking only
    this exact review_id field. Other manual text and references still count.
    """
    def integer(value, maximum=20000):
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum
    def text(value, maximum):
        return isinstance(value, str) and 0 < len(value) <= maximum
    try:
        if not isinstance(payload, dict) or payload.get('version') != 'noon-snapshot-v1' or not _day(payload.get('date')):
            return None
        previous, calendar = payload.get('previous_review'), payload.get('calendar')
        if not isinstance(previous, dict) or not isinstance(calendar, dict):
            return None
        day, prior = payload['date'], previous.get('date')
        if not _day(prior) or prior >= day or calendar.get('verified') is not True or calendar.get('previous_day') != prior:
            return None
        known = previous.get('known_at_ms')
        if not integer(known, 2**63 - 1) or known <= 0:
            return None
        start = datetime.fromisoformat(payload.get('collected_start_at'))
        china = timezone(timedelta(hours=8))
        if (start.tzinfo is None or start.astimezone(china).date().isoformat() != day
                or int(start.timestamp() * 1000) != known):
            return None
        value = previous.get('close_review_summary')
        keys = {'date', 'state', 'review_id', 'revision', 'generated_at_ms', 'result_sha256',
                'close_input_sha256', 'groups', 'items', 'coverage', 'scope', 'reason'}
        if (not isinstance(value, dict) or set(value) != keys or value.get('date') != prior
                or value.get('state') not in ('retained', 'partial') or value.get('scope') != SCOPE
                or not text(value.get('review_id'), 120) or not REVIEW_ID.fullmatch(value['review_id'])
                or not integer(value.get('revision'), 2**63 - 1) or value['revision'] <= 0
                or any(not isinstance(value.get(key), str) or not SHA.fullmatch(value[key])
                       for key in ('result_sha256', 'close_input_sha256'))):
            return None
        generated = value.get('generated_at_ms')
        if (not integer(generated, 2**63 - 1) or not 0 < generated <= known
                or datetime.fromtimestamp(generated / 1000, china).date().isoformat() < prior
                or value['reason'] is not None and not isinstance(value['reason'], str)
                or len(dumps(value).encode()) > MAX_BYTES):
            return None
        groups, rows, coverage = value['groups'], value['items'], value['coverage']
        if (not isinstance(groups, list) or len(groups) > 3 or not isinstance(rows, list) or len(rows) > MAX_ITEMS
                or not isinstance(coverage, dict) or set(coverage) != {'requested_items', 'retained_items', 'omitted_items'}
                or any(not integer(count) for count in coverage.values())):
            return None
        group_keys, retained = set(), set()
        for group in groups:
            if (not isinstance(group, dict) or set(group) != {'key', 'label', 'source_count', 'instrument_ids', 'retained_count', 'omitted_count'}
                    or group['key'] not in KEYS or group['key'] in group_keys or not text(group['label'], 100)
                    or any(not integer(group[key]) for key in ('source_count', 'retained_count', 'omitted_count'))):
                return None
            ids = group['instrument_ids']
            if (not isinstance(ids, list) or len(ids) > MAX_ITEMS
                    or any(not isinstance(symbol, str) or not STOCK.fullmatch(symbol) for symbol in ids)
                    or len(set(ids)) != len(ids) or group['retained_count'] != len(ids)
                    or group['source_count'] != len(ids) + group['omitted_count']):
                return None
            group_keys.add(group['key']); retained.update(ids)
        if (coverage['retained_items'] != len(rows) or coverage['retained_items'] != len(retained)
                or coverage['requested_items'] != coverage['retained_items'] + coverage['omitted_items']
                or coverage['requested_items'] > sum(group['source_count'] for group in groups)
                or coverage['omitted_items'] > sum(group['omitted_count'] for group in groups)):
            return None
        partial = bool(coverage['omitted_items'] or any(group['omitted_count'] for group in groups))
        if (value['state'] == 'partial') != partial:
            return None
        indexed = {}
        row_keys = {'instrument_id', 'name', 'board', 'board_label', 'final_board_count', 'relay_state',
                    'close_price', 'close_change_pct', 'transition', 'transition_flags', 'industries',
                    'topics', 'membership_state', 'source_reason'}
        for row in rows:
            if (not isinstance(row, dict) or not row_keys <= set(row)
                    or set(row) - row_keys - {'reported_final_board_count', 'height_reason'}
                    or row['instrument_id'] not in retained or row['instrument_id'] in indexed
                    or not text(row['name'], 200) or row['board'] not in ('main', 'chinext', 'star', 'beijing', 'unknown')
                    or not text(row['board_label'], 30) or row['relay_state'] not in (None, 'promoted', 'broken', 'unknown', 'height_conflict')
                    or row['final_board_count'] is not None and (not integer(row['final_board_count']) or row['final_board_count'] < 1)
                    or row['membership_state'] not in ('observed_sample', 'partial', 'unknown')
                    or row['source_reason'] is not None and not isinstance(row['source_reason'], str)):
                return None
            for field in ('close_price', 'close_change_pct'):
                number = row[field]
                if (number is not None and (isinstance(number, bool) or not isinstance(number, (int, float))
                                           or not math.isfinite(number) or field == 'close_price' and number <= 0)):
                    return None
            flags = row['transition_flags']
            if not isinstance(flags, list) or len(flags) > 6 or any(not isinstance(flag, str) for flag in flags):
                return None
            if row['relay_state'] == 'height_conflict' and (row['final_board_count'] is not None
                    or not integer(row.get('reported_final_board_count')) or row['reported_final_board_count'] < 1
                    or not text(row.get('height_reason'), 200)):
                return None
            for field, kind in (('industries', 'industry'), ('topics', 'concept')):
                tags, identities = row[field], set()
                if not isinstance(tags, list): return None
                for tag in tags:
                    if (not isinstance(tag, dict) or set(tag) != {'topic_code', 'name', 'kind', 'source', 'members_observed_date'}
                            or not isinstance(tag['topic_code'], str) or not SECTOR.fullmatch(tag['topic_code'])
                            or tag['kind'] != kind or tag['source'] != 'fuyao' or not text(tag['name'], 200)
                            or not _day(tag['members_observed_date']) or tag['members_observed_date'] > prior):
                        return None
                    identity = (tag['topic_code'], tag['members_observed_date'])
                    if identity in identities: return None
                    identities.add(identity)
            indexed[row['instrument_id']] = row
        for group in groups:
            for symbol in group['instrument_ids']:
                row = indexed[symbol]
                if row['transition'] != group['key'] and group['key'] not in row['transition_flags']:
                    return None
        return value['review_id']
    except (TypeError, ValueError, KeyError, OverflowError, OSError):
        return None


def retained_close_classifications(payload):
    """Recover only missing display tags from the pinned prior review's original noon."""
    review_id = validated_frozen_provenance(payload)
    if review_id is None:
        return {}
    frozen = payload['previous_review']['close_review_summary']
    missing = [row['instrument_id'] for row in frozen['items'] if not row['industries'] and not row['topics']]
    if not missing:
        return {}
    try:
        with connect() as connection:
            source = connection.execute("""SELECT s.payload_json,s.content_sha256 FROM market_noon_close_review r
                JOIN market_noon_snapshot s ON s.snapshot_id=r.snapshot_id
                WHERE r.review_id=? AND r.trade_date=? AND s.trade_date=r.trade_date
                  AND r.revision=? AND r.generated_at_ms=? AND r.result_sha256=? AND r.close_input_sha256=?
                  AND s.content_sha256=r.noon_sha256""",
                (review_id, frozen['date'], frozen['revision'], frozen['generated_at_ms'],
                 frozen['result_sha256'], frozen['close_input_sha256'])).fetchone()
        if not source or sha256(source['payload_json'].encode()).hexdigest() != source['content_sha256']:
            return {}
        original = json.loads(source['payload_json'])
        if original.get('date') != frozen['date']:
            return {}
        from app.services.noon_sector_views import close_classifications
        return close_classifications(original, missing)
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        return {}


def freeze_previous_close_summary(previous_day, known_at_ms):
    """Read one exact day's already-known revision and copy only small facts."""
    empty = _empty(previous_day, '上个交易日在采集开始前没有已留存收盘回看，正常昨日反馈仍独立可用')
    if (not _day(previous_day) or not isinstance(known_at_ms, int) or isinstance(known_at_ms, bool)
            or known_at_ms <= 0):
        return {**empty, 'reason': '严格前日或采集开始时间未通过核验，未读取其它日期'}
    try:
        with connect() as connection:
            connection.execute('BEGIN')
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_noon_close_review'").fetchone():
                return empty
            saved = connection.execute('''SELECT review_id,trade_date,revision,generated_at_ms,rule_version,
                    result_json,result_sha256,close_input_sha256 FROM market_noon_close_review
                WHERE trade_date=? AND generated_at_ms<=?
                ORDER BY generated_at_ms DESC,revision DESC LIMIT 1''', (previous_day, known_at_ms)).fetchone()
            if not saved:
                return empty
            review = dict(saved)
            validate_close_reviews(connection, review['review_id'])
            result = json.loads(review['result_json'])
            if result.get('version') != 'noon-close-v1' or review['rule_version'] != 'noon-close-v1':
                raise ValueError('收盘回看规则版本不符')
            original_groups = result.get('tomorrow', {}).get('groups', [])
            if not isinstance(original_groups, list) or len(original_groups) > 3:
                raise ValueError('明日名单结构或组数超过有界范围')
            groups = []
            required = set()
            for group in original_groups:
                if not isinstance(group, dict) or group.get('key') not in KEYS:
                    raise ValueError('明日观察组身份无效')
                ids = group.get('instrument_ids')
                count = group.get('count')
                if (not isinstance(ids, list) or len(ids) > 2000
                        or any(not isinstance(value, str) or not STOCK.fullmatch(value) for value in ids)
                        or len(set(ids)) != len(ids) or not isinstance(count, int) or isinstance(count, bool)
                        or count < len(ids) or any(old['key'] == group['key'] for old in groups)):
                    raise ValueError('明日观察名单不唯一或计数不符')
                groups.append({'key': group['key'], 'label': str(group.get('label') or group['key'])[:100],
                               'source_count': count, 'instrument_ids': ids})
                required.update(ids)
            frozen = {'date': previous_day, 'state': 'retained', 'review_id': review['review_id'],
                      'revision': review['revision'], 'generated_at_ms': review['generated_at_ms'],
                      'result_sha256': review['result_sha256'], 'close_input_sha256': review['close_input_sha256'],
                      'groups': groups, 'items': [], 'coverage': {'requested_items': len(required),
                          'retained_items': 0, 'omitted_items': 0}, 'scope': SCOPE, 'reason': None}
            retained = set()
            estimated_bytes = len(dumps(frozen).encode())
            # Fixed-size identity parameters avoid an unbounded SQL placeholder
            # list and still read only the already-validated selected review.
            for value in connection.execute('''SELECT instrument_id,item_json,item_sha256
                    FROM market_noon_close_item WHERE review_id=? ORDER BY instrument_id''', (review['review_id'],)):
                if value['instrument_id'] not in required:
                    continue
                if sha256(value['item_json'].encode()).hexdigest() != value['item_sha256']:
                    raise ValueError('明日观察逐股证据SHA不符')
                row = json.loads(value['item_json'])
                mini = {field: row.get(field) for field in ('instrument_id', 'name', 'board', 'board_label',
                    'final_board_count', 'relay_state', 'close_price', 'close_change_pct', 'transition', 'transition_flags',
                    'industries', 'topics', 'membership_state', 'source_reason')}
                if row.get('relay_state') == 'height_conflict':
                    mini.update(reported_final_board_count=row.get('final_board_count'), final_board_count=None,
                                height_reason='前日来源板数与此前高度关系冲突，不当作已确认昨日高度')
                # Estimate each appended row before accepting it. The final
                # envelope is checked again after trimming group identities.
                item_bytes = len(dumps(mini).encode()) + 1
                if len(retained) >= MAX_ITEMS or estimated_bytes + item_bytes > MAX_BYTES - 8192:
                    continue
                frozen['items'].append(mini)
                retained.add(row['instrument_id'])
                estimated_bytes += item_bytes
            omitted = len(required) - len(retained)
            for group in groups:
                group['instrument_ids'] = [symbol for symbol in group['instrument_ids'] if symbol in retained]
                group.update(retained_count=len(group['instrument_ids']),
                             omitted_count=group['source_count'] - len(group['instrument_ids']))
            frozen['coverage'].update(retained_items=len(retained), omitted_items=omitted)
            if omitted or any(group['omitted_count'] for group in groups):
                frozen.update(state='partial', reason='前日回看仅冻结有界小摘要，未留存部分计为未知，不当作失败或零涨幅')
            if len(dumps(frozen).encode()) > MAX_BYTES:
                raise ValueError('前日回看小摘要超过字节预算')
            return frozen
    except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError):
        return {**empty, 'reason': '前日收盘回看身份、SHA或有界结构未通过核验；未退回更早日期或读取当前资料替代'}
