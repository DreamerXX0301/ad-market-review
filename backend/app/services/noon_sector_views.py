"""同份午评的板块名单与股票报价；只读，不把盘中价格写入日K。"""
from collections import Counter
from hashlib import sha256
import json
import re

from fastapi import HTTPException

from app.core import connect
from app.services import noon_membership_context as membership
from app.services.noon_collection import canonical

VERSION = 'noon-sector-memberships-v1'
MAX_BYTES = 2 * 1024 * 1024
MAX_LINKS = 100000
SORTS = {'name', 'instrument_id', 'change_pct', 'latest_price', 'amount', 'volume'}


def freeze_memberships(payload, topics, *, budget=MAX_BYTES):
    """Freeze existing, reconciled lists once; no extra provider requests/DB tables."""
    budget = min(MAX_BYTES, max(0, budget))
    result = {'version': VERSION, 'items': {}, 'omitted_count': 0,
              'source_snapshot_id': membership._source_id(payload),
              'reason': '采集时已有名单与本次报价汇总核验；不是午间重新采集成员'}
    try:
        proofs = membership._member_proofs(payload, topics, membership._quotes(payload))
    except (ValueError, TypeError, KeyError):
        result['reason'] = '采集时成员资料无法核验，不使用后续名单补齐'
        return result
    used, links = len(canonical(result)), 0
    sectors = payload.get('sectors') or []
    priority = []
    # Preserve the existing workbench's six leaders/six laggards before filling
    # the rest, so a large directory cannot crowd out the displayed weak side.
    for kind in ('industry', 'concept'):
        known = [row for row in sectors if row.get('kind') == kind
                 and membership._number(row.get('change_pct')) is not None
                 and (membership._number(row.get('latest_price')) or 0) > 0]
        priority.extend(sorted(known, key=lambda row: (-row['change_pct'], row['instrument_id']))[:3])
        priority.extend(sorted(known, key=lambda row: (row['change_pct'], row['instrument_id']))[:3])
    seen = set()
    for row in [*priority, *sectors]:
        code = membership._code(row)
        if code in seen:
            continue
        seen.add(code)
        proof, error = proofs.get(code, (None, '成员未核验'))
        if error or proof is None:
            continue
        topic = proof['topic']
        entry = {'kind': row['kind'], 'members_observed_date': topic['observed_date'],
                 'members_complete': topic.get('members_complete') is True,
                 'instrument_ids': list(proof['members']), 'source': 'fuyao'}
        entry['sha256'] = sha256(canonical(entry)).hexdigest()
        size = len(canonical({code: entry})) + 1
        if used + size > budget or links + len(entry['instrument_ids']) > MAX_LINKS:
            result['omitted_count'] += 1
            continue
        result['items'][code] = entry
        used += size
        links += len(entry['instrument_ids'])
    return result


def _members(payload, sector, quotes):
    code = membership._code(sector)
    if 'sector_memberships' in payload:
        saved = payload['sector_memberships']
        saved_items = saved.get('items') if isinstance(saved, dict) else None
        entry = saved_items.get(code) if isinstance(saved_items, dict) else None
        if (not isinstance(saved, dict) or saved.get('version') != VERSION
                or saved.get('source_snapshot_id') != membership._source_id(payload) or not isinstance(entry, dict)):
            return None, None, '本份午评未冻结该板块名单或已超出容量；不读取当前成员补齐'
        unsigned = {key: value for key, value in entry.items() if key != 'sha256'}
        if sha256(canonical(unsigned)).hexdigest() != entry.get('sha256'):
            return None, None, '冻结成员SHA未通过核验'
        topic = {'topic_code': code, 'name': sector.get('name'), 'kind': entry.get('kind'), 'source': entry.get('source'),
                 'observed_date': entry.get('members_observed_date'),
                 'members_complete': entry.get('members_complete'), 'instrument_ids': entry.get('instrument_ids')}
        candidates = {code: [topic]}
        source_sha = entry['sha256']
    else:
        # Compatibility is limited to the body's original checksummed generation.
        generation = membership._source_id(payload)
        if generation is None:
            return None, None, '旧午评没有可核验的原采集代际名单，不使用当前名单'
        try:
            topics, source_sha = membership._load_topics(generation, payload['previous_review']['date'])
            candidates = {}
            for topic in topics:
                if isinstance(topic, dict):
                    candidates.setdefault(topic.get('topic_code'), []).append(topic)
        except (OSError, ValueError, TypeError, KeyError):
            return None, None, '原采集代际名单未留存或SHA核验失败，不使用当前名单'
    proof, error = membership._verified_members(sector, candidates, quotes, payload['date'])
    return proof, source_sha, error


def close_classifications(payload, symbols):
    """Project additional closing targets from the original frozen noon member lists."""
    symbols = set(sorted(set(symbols))[:membership.MAX_CLASSIFIED_STOCKS])
    saved = payload.get('sector_memberships')
    if not symbols or not isinstance(saved, dict) or not isinstance(saved.get('items'), dict):
        return {}
    sectors = [row for row in (payload.get('sectors') or []) if isinstance(row, dict)]
    identities = Counter(membership._code(row) for row in sectors)
    quotes = membership._quotes(payload)
    proofs = {}
    for sector in sectors:
        code = membership._code(sector)
        entry = saved['items'].get(code)
        members = entry.get('instrument_ids') if isinstance(entry, dict) else None
        if (code is None or identities[code] != 1 or not isinstance(members, list)
                or symbols.isdisjoint(row for row in members if isinstance(row, str))):
            continue
        proof, _, error = _members(payload, sector, quotes)
        proofs[code] = (proof, error)
    return membership._classifications_from_proofs(
        payload, proofs, membership._source_id(payload), None, target=sorted(symbols))


def sector_member_rows(payload, topic_code):
    """Read the verified member rows before the caller's sorting and pagination."""
    sectors = [row for row in payload.get('sectors') or []
               if isinstance(row, dict) and membership._code(row) == topic_code]
    if len(sectors) != 1:
        raise HTTPException(404, '本份午评没有唯一的该来源板块')
    sector = sectors[0]
    quotes = membership._quotes(payload)
    proof, source_sha, error = _members(payload, sector, quotes)
    items = []
    states = {}
    for key in ('limit_up', 'limit_down', 'limit_break'):
        pool = (payload.get('events') or {}).get(key) or {}
        if pool.get('state') in ('observed', 'partial'):
            for row in pool.get('items') or []:
                if isinstance(row, dict):
                    states.setdefault(row.get('instrument_id'), []).append(key)
    for symbol in proof['members'] if proof else []:
        quote = quotes.get(symbol)
        value = {**(quote or {}), 'instrument_id': symbol, 'code': symbol.split('.')[0],
                 'name': (quote or {}).get('name') or symbol,
                 'quote_date': payload['date'], 'quote_at': (quote or {}).get('quote_at'),
                 'quote_scope': 'noon_snapshot', 'current_events': states.get(symbol, [])}
        value['board'] = membership._display_board(value)
        value['quote_available'] = membership._valid_quote(value)
        if quote is None:
            value.update(latest_price=None, change_pct=None, amount=None, volume=None,
                         missing_reason='本份午评没有该股报价；不回退昨日收盘价')
        items.append(value)
    total, valid = len(items), sum(row['quote_available'] for row in items)
    return {'sector_quote': sector, 'items': items, 'membership_as_of': sector.get('members_observed_date') if proof else None,
            'membership_source_sha256': source_sha, 'membership_source': 'fuyao',
            'membership_basis': '午评采集时已留存的名单；股票价格仅来自同份午评，不等同同秒行情',
            'members_complete': proof['topic'].get('members_complete') is True if proof else False,
            'state': 'available' if proof else 'unknown', 'unavailable_reason': error,
            'total': total if proof else None, 'quote_coverage': {'valid': valid, 'total': total} if proof else None}


def build_sector_view(payload, topic_code, *, snapshot_id, content_sha256, board='all', query='',
                      sort='change_pct', direction='desc', page=1, limit=50):
    """Join/deduplicate locally; a missing quote is never replaced by a close quote."""
    if (not isinstance(topic_code, str) or not membership.SECTOR.fullmatch(topic_code)
            or board not in {'all', 'unknown', *membership.BOARDS} or sort not in SORTS or direction not in ('asc', 'desc')
            or type(page) is not int or not 1 <= page <= 10000
            or type(limit) is not int or not 1 <= limit <= 100 or not isinstance(query, str) or len(query) > 100):
        raise HTTPException(422, '午评板块、筛选或分页参数无效')
    view = sector_member_rows(payload, topic_code)
    items = view['items']
    needle = query.strip().casefold()
    filtered = [row for row in items if (board == 'all' or row['board'] == board)
                and (not needle or needle in f"{row['name']} {row['instrument_id']}".casefold())]
    known, unknown = [], []
    for row in filtered:
        valid_sort = (isinstance(row.get(sort), str) and row[sort].strip()
                      if sort in ('name', 'instrument_id') else membership._number(row.get(sort)) is not None)
        (known if valid_sort else unknown).append(row)
    known.sort(key=lambda row: row[sort], reverse=direction == 'desc')
    ordered = known + unknown
    pages = max(1, (len(ordered) + limit - 1) // limit)
    page = min(page, pages)
    visible = ordered[(page - 1) * limit:page * limit]
    from app.services.market_suspensions import suspensions_for_date
    from app.services.noon_service import noon_stock_suspension
    suspensions = suspensions_for_date(payload['date'])
    if suspensions:
        for stock in visible:
            if (suspension := noon_stock_suspension(stock, suspensions)) is not None:
                stock['suspension'] = suspension
    previous = (payload.get('calendar') or {}).get('previous_day')
    background_day = previous if membership._day(previous) and previous < payload['date'] else None
    return {'version': 'noon-sector-view-v1', 'snapshot_id': snapshot_id, 'content_sha256': content_sha256,
            'date': payload['date'], 'collected_start_at': payload.get('collected_start_at'),
            'collected_end_at': payload.get('collected_end_at'), 'quote_as_of': payload.get('quote_as_of'),
            **view, 'quote_scope': 'noon_snapshot', 'formal_chart_as_of': background_day,
            'board_counts': dict(Counter(row['board'] for row in items)), 'matched': len(ordered), 'page': page, 'pages': pages,
            'limit': limit, 'items': visible,
            'notice': '午间形成中报价不写入正式日K、策略、跟踪或回测；历史图最晚截至前一交易日。'}


def read_sector_view(snapshot_id, topic_code, **filters):
    if not isinstance(snapshot_id, str) or not re.fullmatch(r'noon_[0-9a-f]{32}', snapshot_id):
        raise HTTPException(422, '午评快照身份无效')
    with connect() as connection:
        row = connection.execute('SELECT payload_json,content_sha256 FROM market_noon_snapshot WHERE snapshot_id=?',
                                 (snapshot_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '午评正文未留存或已按保留期限清理')
    encoded = row['payload_json']
    if len(encoded.encode()) > 8 * 1024 * 1024 or sha256(encoded.encode()).hexdigest() != row['content_sha256']:
        raise HTTPException(409, '午评内容SHA核验失败，原记录保留')
    return build_sector_view(json.loads(encoded), topic_code, snapshot_id=snapshot_id,
                             content_sha256=row['content_sha256'], **filters)
