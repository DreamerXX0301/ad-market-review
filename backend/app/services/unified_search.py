"""Bounded identity matching shared by global and security-only pickers."""
from collections import Counter
import re

from app.core import get_market
from app.services.sector_history import _catalog
from app.services.workspace_common import fail
from app.services.search_matching import identity_match

MAX_LIMIT = 100
KINDS = ('industry', 'concept', 'stock', 'index')
SCOPE = '已留存股票和指数身份、当前已核验行业概念目录；支持名称、代码、全拼和首字母，轻度错字单列建议，代码不纠错；不代表历史目录或历史成员。'


def search(query='', limit=20, market=None, *, stocks_only=False, include_sectors=True):
    if not isinstance(query, str) or len(query) > 512: fail('搜索内容需要文字，原始长度最多512字')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        fail('搜索数量需要1至100的整数')
    cleaned = query.strip()
    if len(cleaned) > 100: fail('搜索内容去除首尾空格后最多100字')
    base = {'query': cleaned, 'limit': limit, 'scope': SCOPE,
            'current_catalog': True, 'historical_membership': False, 'stocks_only': stocks_only}
    if not cleaned: return {**base, 'items': [], 'total': 0, 'matched_by_kind': {kind: 0 for kind in KINDS}}
    market = market or get_market(); found = {}
    # Metadata only, bounded to the existing master limit. The quote panel and
    # any provider adapter are intentionally absent from this service.
    for row in market.instruments('', limit=10000):
        symbol = row.get('instrument_id') or row.get('thscode')
        if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol.upper()): continue
        symbol = symbol.upper(); original_kind = row.get('kind', row.get('asset_type', 'stock'))
        kind = 'stock' if original_kind in ('stock', 'equity', 'a_share', 'A') else 'index' if original_kind == 'index' else None
        if kind is None or (stocks_only and kind != 'stock'): continue
        name = row.get('name') or row.get('display_name') or symbol
        code = str(row.get('code') or symbol[:6])
        match = identity_match(cleaned, name, symbol, code)
        if match is not None:
            rank, evidence = match
            found[(kind, symbol)] = ({'instrument_id': symbol, 'code': code, 'name': name,
                'kind': kind, 'target_type': kind, **evidence}, rank)
    reference = market.reference() if include_sectors and not stocks_only and callable(getattr(market, 'reference', None)) else {}
    catalog, failures, _, _ = _catalog(reference)
    for code, topic in catalog.items():
        match = identity_match(cleaned, topic['name'], code, code.split('.')[0])
        if match is None: continue
        rank, evidence = match
        found[('sector', code)] = ({'instrument_id': code, 'code': code.split('.')[0],
            'name': topic['name'], 'kind': topic['kind'], 'sector_kind': topic['kind'],
            'target_type': 'sector', 'topic_code': code, 'topic_id': topic['topic_id'], **evidence}, rank)
    buckets = {kind: [] for kind in KINDS}
    for item, rank in found.values(): buckets[item['kind']].append((rank, item))
    for values in buckets.values(): values.sort(key=lambda value: (value[0], value[1]['name'].casefold(), value[1]['instrument_id']))
    output = []
    # Exact identity wins first. Within each relevance tier, round-robin by
    # type keeps a long stock list from burying an equally relevant board.
    for rank in range(7):
        queues = {kind: [item for priority, item in buckets[kind] if priority == rank] for kind in KINDS}
        position = 0
        while len(output) < limit and any(position < len(values) for values in queues.values()):
            for kind in KINDS:
                if position < len(queues[kind]): output.append(queues[kind][position])
                if len(output) == limit: break
            position += 1
        if len(output) == limit: break
    counts = Counter(item['kind'] for item, _ in found.values())
    return {**base, 'items': output, 'total': len(found),
            'matched_by_kind': {kind: counts[kind] for kind in KINDS},
            'catalog_warning_count': len(failures)}
