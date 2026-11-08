"""只读派生竞价题材样本：仅使用快照明确指定且由SQLite锚定的原成员代际。

不读当前head、DuckDB、网络或全历史目录，不修改竞价正文或成员档案。
"""
from collections import Counter
from contextlib import nullcontext
from datetime import date, datetime
import json
import math
import re

from fastapi import HTTPException

from app.core import connect
from app.infrastructure.config import published_dir
from app.analytics.market_storage import safe_object, verify_immutable_file
from app.services import noon_membership_context as membership
from app.services.auction_integrity import validate_snapshot

VERSION = 'auction-topic-summary-v1'
SYMBOL = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)\Z')
SECTOR = re.compile(r'[0-9]{6}\.(?:TI|SH|SZ)\Z')
SCOPE = ('仅统计本份竞价最多100只冻结候选与原采集代际成员名单的交集；样本股数不是全板块家数，'
         '平均竞价涨幅只除以该组有效报价数，缺报不补零；一股可归属多个题材，各组不可相加当作去重股票总数。')


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _number(value):
    try:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    except OverflowError:
        return None


def _source(payload):
    day, previous = payload.get('date'), payload.get('previous_date')
    review = payload.get('previous_review')
    review = review if isinstance(review, dict) else {}
    if not _day(day) or not _day(previous) or previous >= day or review.get('date') not in (None, previous):
        raise ValueError('竞价与严格前日日期无法核验')
    facts_ids = set()
    for value in (review.get('payload'), review.get('background')):
        if isinstance(value, dict) and value.get('mode') == 'previous_session_price_facts':
            if value.get('date') != previous or not isinstance(value.get('source_snapshot_id'), str):
                raise ValueError('原前日事实来源身份不符')
            facts_ids.add(value['source_snapshot_id'])
    if len(facts_ids) > 1:
        raise ValueError('原前日事实存在冲突代际')
    context = payload.get('market_context')
    expected_end = None
    if context is not None:
        if (not isinstance(context, dict) or context.get('source') != 'fuyao'
                or not _day(context.get('data_end')) or context['data_end'] > previous):
            raise ValueError('冻结行情上下文的来源或截止日期不符')
        known = context.get('known_at_ms')
        start = datetime.fromisoformat(payload.get('collected_start_at', ''))
        if (not isinstance(known, int) or isinstance(known, bool) or known <= 0
                or start.tzinfo is None or known > int(start.timestamp() * 1000)):
            raise ValueError('冻结行情上下文不能晚于竞价开始时点')
        generation, expected_end = context.get('source_snapshot_id'), context['data_end']
        if facts_ids and facts_ids != {generation}:
            raise ValueError('冻结行情上下文与原前日事实代际冲突')
    else:
        generation = membership._source_id(payload)
    if not isinstance(generation, str) or not membership.GENERATION.fullmatch(generation):
        raise ValueError('本份竞价未冻结明确可核验的原行情代际')
    return generation, previous, expected_end


def _load_original_topics(generation, previous, expected_end, *, connection=None):
    """SQL artifact pins anchor bytes before consulting the named generation."""
    reference_key = generation + '/reference.json'
    with (connect() if connection is None else nullcontext(connection)) as reader:
        if not reader.in_transaction:
            reader.execute('BEGIN')
        rows = reader.execute('''SELECT s.snapshot_id,s.data_end,s.manifest_path,s.manifest_sha256,
            a.object_key,a.sha256,a.byte_count FROM dataset_snapshot s JOIN snapshot_artifact a USING(snapshot_id)
            WHERE s.module='topics' AND s.state='published'
              AND json_extract(s.lineage_json,'$.market_snapshot_id')=?
              AND a.object_key=? AND a.role='reference.json' LIMIT 3''', (generation, reference_key)).fetchall()
    if len(rows) != 1:
        raise ValueError('原题材代际缺少唯一SQLite发布锚点')
    anchor = dict(rows[0])
    if (not _day(anchor['data_end']) or anchor['data_end'] > previous
            or expected_end is not None and anchor['data_end'] != expected_end):
        raise ValueError('原发布锚点日期与冻结上下文不符')
    root = published_dir().resolve()
    catalog = safe_object(root, anchor['manifest_path'])
    size = catalog.stat().st_size
    if not 0 < size <= membership.MAX_MANIFEST_BYTES:
        raise ValueError('原发布清单超过读取容量')
    verify_immutable_file(catalog, anchor['manifest_sha256'], size)
    manifest = membership._read_json(catalog, membership.MAX_MANIFEST_BYTES)
    if (manifest.get('snapshot_id') != anchor['snapshot_id'] or manifest.get('module') != 'topics'
            or (manifest.get('lineage') or {}).get('market_snapshot_id') != generation):
        raise ValueError('原发布清单身份与SQLite锚点不符')
    entries = [item for item in manifest.get('artifacts', []) if isinstance(item, dict)
               and item.get('object_key') == reference_key and item.get('role') == 'reference.json']
    if len(entries) != 1 or any(entries[0].get(key) != anchor[key] for key in ('sha256', 'byte_count')):
        raise ValueError('原参考文件未与发布清单及SQLite双重对账')
    path = safe_object(root, reference_key)
    if (not isinstance(anchor['byte_count'], int) or isinstance(anchor['byte_count'], bool)
            or not 0 < anchor['byte_count'] <= membership.MAX_REFERENCE_BYTES):
        raise ValueError('原参考文件超过读取容量')
    verify_immutable_file(path, anchor['sha256'], anchor['byte_count'])
    reference = membership._read_json(path, membership.MAX_REFERENCE_BYTES)
    if reference.get('storage_version') in ('market-reference-parts-v1', 'market-reference-parts-v2'):
        part = (reference.get('fields') or {}).get('topics')
        if not isinstance(part, dict):
            raise ValueError('原参考文件没有题材成员分片')
        with (connect() if connection is None else nullcontext(connection)) as reader:
            parts = reader.execute('''SELECT sha256,byte_count FROM snapshot_artifact
                WHERE snapshot_id=? AND object_key=? LIMIT 2''',
                (anchor['snapshot_id'], part.get('object_key'))).fetchall()
        if len(parts) != 1 or parts[0]['sha256'] != part.get('sha256') or parts[0]['byte_count'] != part.get('bytes'):
            raise ValueError('原题材分片未与SQLite发布锚点对账')
    # The shared bounded loader reads only topics from split references, never
    # master columns, index history, a current head, or unrelated directories.
    topics, digest = membership._load_topics(generation, previous)
    verify_immutable_file(path, anchor['sha256'], anchor['byte_count'])
    return topics, digest


def _samples(payload):
    universe = payload.get('universe')
    items = universe.get('items') if isinstance(universe, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError('竞价候选数量超出冻结范围')
    codes = [item.get('instrument_id') if isinstance(item, dict) else None for item in items]
    if any(not isinstance(code, str) or not SYMBOL.fullmatch(code) for code in codes) or len(set(codes)) != len(codes):
        raise ValueError('竞价冻结候选身份无效或重复')
    rows = (payload.get('quotes') or {}).get('items')
    if not isinstance(rows, list) or [row.get('instrument_id') if isinstance(row, dict) else None for row in rows] != codes:
        raise ValueError('竞价报价与冻结候选身份无法对账')
    changes = {row['instrument_id']: _number(row.get('auction_pct')) for row in rows if row.get('state') == 'observed'}
    return codes, {code: value for code, value in changes.items() if value is not None}


def _empty(payload, codes=None, changes=None, generation=None, reason=None):
    codes, changes = codes or [], changes or {}
    return {'version': VERSION, 'date': payload.get('date'), 'state': 'unknown',
            'source_snapshot_id': generation, 'source_sha256': None, 'scope': SCOPE,
            'reason': reason, 'groups': [], 'stock_classifications': {
                code: {'industries': [], 'topics': [], 'state': 'unknown', 'reason': reason,
                       'source_snapshot_id': generation, 'source_sha256': None} for code in codes},
            'coverage': {'requested': len(codes), 'classified': 0, 'unclassified': len(codes),
                         'valid_quotes': len(changes), 'groups': 0, 'rejected_groups': 0}}


def build_topic_summary(payload, *, connection=None):
    """Read and classify a verified snapshot without changing its payload."""
    if not isinstance(payload, dict):
        return _empty({}, reason='未取得竞价正文，题材归属保持未知')
    codes, changes, generation = [], {}, None
    try:
        codes, changes = _samples(payload)
        generation, previous, expected_end = _source(payload)
        topics, digest = (_load_original_topics(generation, previous, expected_end)
                          if connection is None else
                          _load_original_topics(generation, previous, expected_end, connection=connection))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return _empty(payload, codes, changes, generation,
            '原采集代际成员资料未通过明确来源、SQLite发布锚点、日期、路径或SHA核验；不使用当前名单补齐')
    result = _empty(payload, codes, changes, generation)
    result['source_sha256'] = digest
    for value in result['stock_classifications'].values():
        value.update(source_sha256=digest, reason='原代际成员档案未确认该候选归属；不等于没有题材')
    counts = Counter((row.get('source'), row.get('kind'), row.get('topic_code')) for row in topics
                     if isinstance(row, dict) and all(isinstance(row.get(key), str) for key in ('source', 'kind', 'topic_code')))
    rejected = 0
    for row in topics:
        if not isinstance(row, dict):
            rejected += 1
            continue
        code, kind, observed, name = row.get('topic_code'), row.get('kind'), row.get('observed_date'), row.get('name')
        members = row.get('instrument_ids')
        identity = (row.get('source'), kind, code)
        if (row.get('source') != 'fuyao' or kind not in ('industry', 'concept')
                or not isinstance(code, str) or not SECTOR.fullmatch(code)
                or not isinstance(name, str) or not name.strip() or len(name) > 256
                or not _day(observed) or observed > previous or counts.get(identity) != 1
                or not isinstance(members, list) or len(members) > membership.MAX_MEMBERS
                or any(not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) for symbol in members)
                or len(set(members)) != len(members)):
            rejected += 1
            continue
        member_set = set(members)
        selected = [symbol for symbol in codes if symbol in member_set]
        if not selected:
            continue
        tag = {'topic_code': code, 'name': name, 'kind': kind, 'source': 'fuyao',
               'members_observed_date': observed}
        field = 'industries' if kind == 'industry' else 'topics'
        for symbol in selected:
            result['stock_classifications'][symbol][field].append(dict(tag))
        values = [changes[symbol] for symbol in selected if symbol in changes]
        try:
            average = math.fsum(values) / len(values) if values else None
        except OverflowError:
            average = None
        complete = row.get('members_complete') is True
        result['groups'].append({**tag, 'sample_count': len(selected), 'valid_count': len(values),
            'missing_count': len(selected) - len(values), 'average_auction_pct': average,
            'instrument_ids': selected, 'members_complete': complete,
            'state': 'observed_sample' if complete and len(values) == len(selected) else 'partial',
            'source_snapshot_id': generation, 'source_sha256': digest})
    classified = 0
    for value in result['stock_classifications'].values():
        if value['industries'] or value['topics']:
            classified += 1
            value.update(state='observed_sample', reason='原采集代际已核验成员归属样本，成员档案日单列；不是竞价上涨原因')
        for field in ('industries', 'topics'):
            value[field].sort(key=lambda item: (item['name'], item['topic_code']))
    result['groups'].sort(key=lambda item: (-item['sample_count'], item['average_auction_pct'] is None,
        -(item['average_auction_pct'] or 0), item['kind'], item['topic_code']))
    result['coverage'].update(classified=classified, unclassified=len(codes) - classified,
                              groups=len(result['groups']), rejected_groups=rejected)
    result['state'] = ('observed_sample' if classified == len(codes) and not rejected
                       and all(item['state'] == 'observed_sample' for item in result['groups'])
                       else 'partial' if result['groups'] else 'unknown')
    result['reason'] = ('按原采集代际成员档案派生的候选样本统计；成员日期与竞价日期分别保留，不改写原竞价正文。'
                        + (' 成员档案或报价覆盖不完整时仅为已确认关联样本，不代表完整方向。' if result['state'] == 'partial' else '')
                        + (f' {rejected}组成员身份、来源、日期或名单无效，已隔离。' if rejected else ''))
    return result


def read_view(day):
    if not _day(day):
        raise HTTPException(422, '竞价题材日期须为YYYY-MM-DD')
    with connect() as connection:
        row = connection.execute('SELECT * FROM market_auction_snapshot WHERE trade_date=?', (day,)).fetchone()
        from app.services.auction_retention import retention_status
        retention = retention_status(connection, day)
    if row is None:
        if retention['purged']:
            return {**_empty({'date': day}, reason='竞价正文及内嵌原件已按当时的保留规则清理。'),
                    'state': 'purged', 'retention': retention}
        return _empty({'date': day}, reason='所选日尚无已留存竞价，题材样本保持未知')
    try:
        payload = validate_snapshot(dict(row))
    except ValueError:
        raise HTTPException(409, '原竞价正文或来源原件未通过SHA核验，未派生题材统计') from None
    return build_topic_summary(payload)
