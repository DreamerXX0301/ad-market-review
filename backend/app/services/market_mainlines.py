"""Evidence-led daily manual notes, separate from topics, source quotes and signals.

No fetches, jobs, price queries or DuckDB access. Identity/calendar validation reads
the already-published immutable catalogue before the short SQLite append transaction.
"""
from datetime import date, datetime, timezone
import hashlib
import json
import re

from app.core import connect, get_market, new_id, now_ms
from app.infrastructure import mainline_repository as repository
from app.services.workspace_common import fail

MAX_ATTRIBUTIONS = 12
STATUSES = {'source_claim', 'user_judgment', 'unverified', 'conflict'}
ROLES = {'unknown', 'core_candidate', 'followup'}
FIELDS = {'mainline_name', 'is_display_primary', 'role', 'evidence_status', 'source_name',
          'source_reason', 'evidence_ref', 'source_published_at', 'evidence_sha256', 'catalyst_note', 'user_note'}
NOTICE = '用户逐日录入的来源转述或判断；不是供应商认证、目录成员或已验证的上涨因果。缺少记录保持未归因。'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _day(value):
    try:
        valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        fail('date需要YYYY-MM-DD形式的真实日期')
    return value


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', value.strip().upper()):
        fail('instrument_id需要带SH/SZ/BJ后缀的明确个股代码')
    return value.strip().upper()


def _text(value, field, maximum, required=False, multiline=True):
    if value is None and not required:
        return ''
    if not isinstance(value, str):
        fail(field + '需要文本')
    value = value.strip()
    if (required and not value) or len(value) > maximum or any(
            ord(char) < 32 and (not multiline or char not in '\n\t\r') for char in value):
        fail(f'{field}需要{"1至" if required else "不超过"}{maximum}字，不能含非法控制字符')
    return value


def _published(value, stamp):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or len(value) > 50:
        fail('source_published_at需要带时区的ISO时间，未知请留空')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        valid = parsed.tzinfo is not None and parsed.utcoffset() is not None and parsed.timestamp() * 1000 <= stamp
        normalized = parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if valid else None
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        fail('source_published_at需要不晚于实际记录时刻的带时区ISO时间；不能倒填系统首见时间')
    return normalized


def _attribution(value, stamp):
    if not isinstance(value, dict) or set(value) - FIELDS:
        fail('归属字段不受支持；首见时间与审计身份由服务器记录')
    result = {field: _text(value.get(field), field, maximum, required, multiline)
              for field, maximum, required, multiline in (
                  ('mainline_name', 80, True, False), ('source_name', 200, True, False),
                  ('source_reason', 2000, False, True), ('evidence_ref', 2000, True, True),
                  ('catalyst_note', 2000, False, True), ('user_note', 2000, False, True))}
    result['role'] = value.get('role', 'unknown')
    result['evidence_status'] = value.get('evidence_status', 'unverified')
    result['is_display_primary'] = value.get('is_display_primary', False)
    if not isinstance(result['role'], str) or result['role'] not in ROLES:
        fail('role需要unknown/core_candidate/followup；核心候选不是已验证龙头')
    if not isinstance(result['evidence_status'], str) or result['evidence_status'] not in STATUSES:
        fail('evidence_status需要source_claim/user_judgment/unverified/conflict')
    if not isinstance(result['is_display_primary'], bool):
        fail('is_display_primary需要布尔值')
    if result['is_display_primary'] and result['evidence_status'] not in ('source_claim', 'user_judgment'):
        fail('待核验或冲突陈述不能直接选为展示主组，请先保留其证据并明确判断')
    result['source_published_at'] = _published(value.get('source_published_at'), stamp)
    sha = value.get('evidence_sha256')
    if sha == '':
        sha = None
    if sha is not None and (not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', sha)):
        fail('evidence_sha256需要64位十六进制，未归档证据请留空')
    result['evidence_sha256'] = sha.lower() if sha else None
    return result


def _assertion_key(item):
    # Display placement is an audited choice, not a new source assertion.
    return hashlib.sha256(_json({key: item[key] for key in sorted(FIELDS - {'is_display_primary'})}).encode()).hexdigest()


def _view(row):
    item = dict(row)
    item.pop('position', None)
    assertions = json.loads(item.pop('attributions_json'))
    item['date'] = item.pop('trade_date')
    item['attributions'] = assertions
    names = list(dict.fromkeys(a['mainline_name'] for a in assertions
                              if a['evidence_status'] in ('source_claim', 'user_judgment')))
    explicit = next((a['mainline_name'] for a in assertions if a['is_display_primary']), None)
    conflict = any(a['evidence_status'] == 'conflict' for a in assertions)
    primary = None if conflict else explicit or (names[0] if len(names) == 1 else None)
    item['display_state'] = 'conflict' if conflict else 'attributed' if primary else 'primary_pending' if names else 'unattributed'
    item['display_mainline'] = primary
    item['secondary_mainlines'] = [name for name in names if name != primary]
    item['display_basis'] = 'user_selected' if primary and explicit else 'single_evidenced_name' if primary else None
    return item


def list_mainlines(day, known_at_ms=None):
    day = _day(day)
    if known_at_ms is not None and (isinstance(known_at_ms, bool) or not isinstance(known_at_ms, int) or not 0 < known_at_ms <= 9007199254740991):
        fail('known_at_ms需要正整数毫秒时间')
    with connect() as connection:
        items = [_view(row) for row in repository.by_date(connection, day, known_at_ms)]
    return {'date': day, 'known_at_ms': known_at_ms, 'items': items, 'total': len(items), 'notice': NOTICE}


def mainline_history(day, instrument_id, limit=100, offset=0):
    day, symbol = _day(day), _symbol(instrument_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500 or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2147483647:
        fail('审计分页需要limit=1..500，offset=0..2147483647')
    with connect() as connection:
        rows, total = repository.history(connection, day, symbol, limit, offset)
        items = [_view(row) for row in rows]
    return {'date': day, 'instrument_id': symbol, 'items': items, 'total': total,
            'limit': limit, 'offset': offset, 'notice': NOTICE}


def save_revision(payload):
    if not isinstance(payload, dict) or set(payload) - {'date', 'instrument_id', 'expected_revision', 'action', 'change_reason', 'attributions'}:
        fail('主线修订字段不受支持，不能设置系统审计时间')
    day, symbol = _day(payload.get('date')), _symbol(payload.get('instrument_id'))
    expected = payload.get('expected_revision')
    if isinstance(expected, bool) or not isinstance(expected, int) or not 0 <= expected <= 2147483646:
        fail('expected_revision需要当前修订号，首次录入为0')
    action = payload.get('action')
    if action not in ('replace', 'revoke'):
        fail('action需要replace或revoke')
    reason = _text(payload.get('change_reason'), '更正理由', 1000, True)
    values = payload.get('attributions')
    if not isinstance(values, list) or len(values) > MAX_ATTRIBUTIONS or (action == 'revoke' and values) or (action == 'replace' and not values):
        fail(f'replace需要1至{MAX_ATTRIBUTIONS}条有据归属；revoke需要空归属数组')
    checked_at = now_ms()
    assertions = [_attribution(value, checked_at) for value in values]
    if sum(a['is_display_primary'] for a in assertions) > 1:
        fail('每股每日只能有一个展示主组；其余归属作为次级保留')
    keys = [_assertion_key(a) for a in assertions]
    if len(set(keys)) != len(keys):
        fail('同一来源陈述不能重复录入，请在一条归属中选择展示主组')
    # get_market reads published immutable files. Never call it while holding the
    # writer lock, and never access quotes, live DuckDB, directories or the network.
    with connect() as connection:
        retained = repository.latest(connection, day, symbol)
    if retained:
        # A later rolling catalogue may drop this stock/date. Its existing audit
        # must still be correctable/revocable without inventing a fresh identity.
        name = retained['instrument_name']
    else:
        market = get_market()
        instrument = next((item for item in market.instruments(limit=10000)
                           if str(item.get('instrument_id', '')).upper() == symbol), None)
        if not instrument or instrument.get('kind', instrument.get('asset_type', 'stock')) not in ('stock', 'equity', 'a_share', 'A'):
            fail('仅能为本地已留存的明确个股记录主线；指数/板块不可混入', 404)
        if day not in market.calendar():
            fail('该日不在本地已发布交易日历中，不能补造交易日')
        name = str(instrument.get('name') or instrument.get('display_name') or symbol)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        clock = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        if not clock or day > clock[0]:
            fail('只能录入截至当前已发布业务日的逐日主线', 409)
        previous = repository.latest(connection, day, symbol)
        actual = previous['revision'] if previous else 0
        if actual != expected:
            fail({'message': '记录已被更正，请刷新当前修订并核对草稿后重新保存', 'current_revision': actual}, 409)
        stamp = now_ms()
        latest_recorded = connection.execute('SELECT max(recorded_at_ms) FROM market_mainline_revision').fetchone()[0]
        # Knowledge-time reads span the whole module, not just this stock/day.
        # Reject clock rollback, including during validation; never manufacture
        # a monotonic timestamp that the real server clock has not reached.
        if stamp < checked_at or (latest_recorded is not None and stamp < latest_recorded):
            fail('系统时钟早于本次校验或主线模块已记录时刻，暂停写入以保留真实审计顺序', 409)
        old = {_assertion_key(a): a for a in json.loads(previous['attributions_json'])} if previous else {}
        for key, assertion in zip(keys, assertions):
            retained = old.get(key)
            assertion['assertion_id'] = retained['assertion_id'] if retained else new_id('assertion')
            assertion['first_known_at_ms'] = retained['first_known_at_ms'] if retained else stamp
        item = {'revision_id': new_id('mainline'), 'trade_date': day, 'instrument_id': symbol,
                'instrument_name': name, 'revision': actual + 1, 'action': action,
                'source_kind': 'manual_user_entry', 'change_reason': reason,
                'attributions_json': _json(assertions), 'recorded_at_ms': stamp,
                'supersedes_revision_id': previous['revision_id'] if previous else None}
        repository.append(connection, item)
    return _view(item)
