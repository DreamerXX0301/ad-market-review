"""Manual retention of current server evidence; no fetches or automatic jobs."""
from datetime import date
from hashlib import sha256
import json
import re

from app.core import connect, new_id, now_ms
from app.infrastructure import sentiment_snapshot_repository as repository
from app.services.workspace_common import fail

NOTICE = ('记录时间只表示本次手动留存时点；内容按当时留存材料重算，'
          '不代表被描述交易日已知。原来源抓取/首次可知字段保持原样，不倒填历史。')


def _day(value):
    try: valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError: valid = False
    if not valid: fail('date需要YYYY-MM-DD形式的真实日期')
    return value


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def compute_context(day):
    """All market/Parquet work happens before the append transaction begins."""
    from app.services import market_service
    from app.services.market_sentiment import describe_sentiment
    market, status, actual_day, reference, rows = market_service.panel(day)
    if actual_day != day or not status.get('data_end') or day > status['data_end']:
        fail('只能留存截至当前已发布行情日的复盘上下文', 409)
    return describe_sentiment(market, reference, rows, day)


def _view(row):
    item = dict(row); item['date'] = item.pop('trade_date')
    encoded = item.pop('payload_json', None)
    if encoded is not None:
        if sha256(encoded.encode()).hexdigest() != item['content_sha256']:
            fail('留存内容SHA校验失败，暂停展示；请保留原记录检查存储', 409)
        try: item['payload'] = json.loads(encoded)
        except (TypeError, ValueError): fail('留存内容不是有效JSON，请保留原记录检查存储', 409)
    item['notice'] = NOTICE
    return item


def save_snapshot(payload):
    if not isinstance(payload, dict) or set(payload) != {'date', 'expected_input_fingerprint'}:
        fail('手动留存仅接受date和expected_input_fingerprint；内容和时间由服务器计算')
    day = _day(payload.get('date')); expected = payload.get('expected_input_fingerprint')
    if not _hash(expected): fail('expected_input_fingerprint需要页面当前的64位小写十六进制指纹')
    checked_at = now_ms()
    if isinstance(checked_at, bool) or not isinstance(checked_at, int) or checked_at <= 0:
        fail('系统时钟无效，未留存复盘', 409)
    context = compute_context(day)
    proof = context.get('provenance') if isinstance(context, dict) else None
    if not isinstance(proof, dict) or context.get('date') != day or not _hash(proof.get('input_fingerprint')):
        fail('服务器尚未形成可留存的完整输入指纹，请刷新复盘页面', 409)
    actual = proof['input_fingerprint']
    if expected != actual:
        fail({'message': '复盘输入已变化，请刷新核对后重新留存', 'current_input_fingerprint': actual}, 409)
    model, version = context.get('model'), proof.get('rule_version')
    if (not isinstance(model, str) or not 1 <= len(model) <= 128
            or not isinstance(version, str) or not 1 <= len(version) <= 128
            or proof.get('mode') != 'retained_retrospective'):
        fail('服务器上下文版本或回看模式无效，未保存', 409)
    try: encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError): fail('服务器上下文含无效数值或结构，未保存', 409)
    if len(encoded.encode()) > 1048576: fail('复盘上下文超过留存大小上限，未保存', 409)
    content_hash = sha256(encoded.encode()).hexdigest()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        previous = repository.by_key(connection, day, version, actual)
        if previous:
            if previous['model'] != model:
                fail('模型与已有规则版本冲突，请先核对服务器版本；不会覆盖旧留存', 409)
            return {**_view(previous), 'created': False, 'reused': True}
        stamp = now_ms()
        retained_clock = connection.execute('SELECT max(recorded_at_ms) FROM market_sentiment_snapshot').fetchone()[0]
        if (isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < checked_at
                or retained_clock is not None and stamp < retained_clock):
            fail('系统时钟回退，暂停新留存以保留真实时点顺序；不会伪造递增时间', 409)
        item = {'snapshot_id': new_id('sentiment'), 'trade_date': day, 'recorded_at_ms': stamp,
                'record_mode': 'manual_retained_capture', 'model': model, 'rule_version': version,
                'input_fingerprint': actual, 'content_sha256': content_hash, 'payload_json': encoded}
        repository.append(connection, item)
    return {**_view(item), 'created': True, 'reused': False}


def list_snapshots(day=None, known_at_ms=None, limit=20, offset=0):
    if day is not None: day = _day(day)
    if known_at_ms is not None and (isinstance(known_at_ms, bool) or not isinstance(known_at_ms, int) or not 0 < known_at_ms <= 9007199254740991):
        fail('known_at_ms需要正整数毫秒时间')
    if (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100
            or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2147483647):
        fail('留存分页需要limit=1..100，offset=0..2147483647')
    with connect() as connection:
        connection.execute('BEGIN')
        items, total = repository.list_records(connection, day, known_at_ms, limit, offset)
    return {'date': day, 'known_at_ms': known_at_ms, 'items': [_view(row) for row in items],
            'total': total, 'limit': limit, 'offset': offset, 'notice': NOTICE}


def get_snapshot(snapshot_id):
    if not isinstance(snapshot_id, str) or not re.fullmatch(r'sentiment_[0-9a-f]{32}', snapshot_id):
        fail('未找到该复盘留存记录', 404)
    with connect() as connection:
        row = repository.by_id(connection, snapshot_id)
    if row is None: fail('未找到该复盘留存记录', 404)
    return _view(row)
