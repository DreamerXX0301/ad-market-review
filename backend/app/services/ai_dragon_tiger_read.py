"""Read bounded dragon-tiger facts from one retained immutable day.

The day page and, for one exact stock identity, its two disclosed seat periods
reuse existing read-only services. No collector, provider or history scan runs.
Single-day and three-day values remain separate; anonymous seats remain rows.
"""
from fastapi import HTTPException

from app.services import dragon_tiger
from app.services.supplement_access import supplement_access

MAX_ROWS = 15
MAX_DISCLOSURES = 2
ROW_FIELDS = ('instrument_id', 'code', 'name', 'source_instrument_id', 'identity_state',
              'identity_reason', 'range_days', 'source_range_days', 'range_state', 'range_reason',
              'change_pct', 'buy_value', 'sell_value', 'net_value', 'raw_net_value',
              'amount_check', 'amount_check_evidence', 'org_net_value', 'hot_money_net_value',
              'org_buy_num', 'org_sell_num', 'label_net_value', 'label_name',
              'source_concepts_basis', 'source_limit_reason', 'source', 'date')
BOARD_FIELDS = ('source_count', 'source_stock_count', 'source_count_basis',
                'source_stock_count_basis', 'statistics_state', 'observed_label_count',
                'observed_label_row_count', 'observed_stock_count', 'observed_stock_range_count',
                'excluded_range_row_count', 'amount_conflict_row_count', 'state', 'source', 'date')
SEAT_FIELDS = ('name', 'source_id', 'rank', 'buy', 'sell', 'net', 'raw_net',
               'amounts_complete', 'amount_check', 'amount_check_evidence', 'source')


def _pick(value, fields):
    return {key: value[key] for key in fields if key in value}


def _denied(value):
    if isinstance(value, dict):
        return (value.get('egress_allowed') is False or value.get('ai_processing_allowed') is False
                or any(_denied(item) for item in value.values()))
    return isinstance(value, (list, tuple)) and any(_denied(item) for item in value)


def _table(rows, maximum=MAX_ROWS, *, scope):
    return {'items': rows[:maximum], 'observed_count': len(rows),
            'returned_count': min(len(rows), maximum), 'truncated': len(rows) > maximum,
            'scope': scope}


def _matches(row, query):
    return not query or query in ' '.join(
        str(row.get(key) or '') for key in ('instrument_id', 'code', 'name')).casefold()


def _board_rows(board, kind):
    if kind != 'hot_money':
        return [row for row in board.get('items', []) if isinstance(row, dict)]
    return [{**row, 'label_name': group.get('name')} for group in board.get('items', [])
            if isinstance(group, dict) for row in group.get('rows', []) if isinstance(row, dict)]


def _stock(row):
    value = _pick(row, ROW_FIELDS)
    concepts = [item for item in row.get('source_concepts', []) if isinstance(item, dict)]
    value['source_concepts'] = [_pick(item, ('name',)) for item in concepts[:3]]
    value['source_concepts_truncated'] = len(concepts) > 3
    return value


def project_page(page, query=''):
    """Query the complete loaded page first; never add cross-period amounts."""
    query = query.strip().casefold()
    boards = {}
    periods = {str(period): {} for period in (1, 3)}
    for kind in ('all', 'org', 'hot_money'):
        board = page.get('boards', {}).get(kind) or {}
        rows = [row for row in _board_rows(board, kind) if _matches(row, query)]
        boards[kind] = {**_pick(board, BOARD_FIELDS), 'query_matched_rows': len(rows),
                        'query_unclassified_rows': sum(row.get('range_days') not in (1, 3) for row in rows)}
        for period in (1, 3):
            periods[str(period)][kind] = _table(
                [_stock(row) for row in rows if row.get('range_days') == period],
                scope='同一指定日、同一区间的来源行；游资标签可重叠，不按标签行数推断去重股票数')
    coverage = page.get('coverage') or {}
    return {
        'state': page.get('state', 'unknown'), 'query': query,
        'availability': _pick(page.get('availability') or {}, ('available', 'reason', 'sources', 'date')),
        'coverage': _pick(coverage, ('state', 'complete', 'anchor_date', 'retained_trading_days', 'latest_market_date', 'reason')),
        'periods': periods, 'source_board_statistics': boards,
        'units': {'change_pct': '%', 'amount': 'CNY'},
        'scope': '仅指定已发布日的已留存龙虎榜；单日(1)和三日(3)各自列示，不能相加或混作同一交易日金额。'
                 '买卖额与来源净额分别保留；unknown不是0，来源冲突不凑平。'
                 '游资标签与股票归属可重叠，原来源题材不是历史成分股名单。',
    }


def _identity(market, query):
    if not query:
        return None
    identities = {row['instrument_id']: row for row in market.instruments(query=query, limit=10000)
                  if isinstance(row, dict) and row.get('kind') == 'stock' and row.get('instrument_id')
                  and query in (str(row.get('instrument_id', '')).casefold(),
                                str(row.get('code', '')).casefold(), str(row.get('name', '')).casefold())}
    return next(iter(identities.values())) if len(identities) == 1 else None


def _seats(value, day, snapshot, symbol, period):
    if ((value.get('date'), value.get('snapshot_id'), value.get('instrument_id'), value.get('range_days'))
            != (day, snapshot, symbol, period)):
        raise ValueError('龙虎榜席位的日期、股票、区间或发布代际不一致')
    records = []
    for row in value.get('records', []):
        if not isinstance(row, dict) or row.get('range_days') != period:
            raise ValueError('龙虎榜席位混入不同披露区间')
        source = row.get('source') or {}
        if source.get('date') != day or source.get('instrument_id') != symbol:
            raise ValueError('龙虎榜席位原来源股票或日期不一致')
        records.append({**_pick(row, ('record_id', 'reason', 'range_days', 'range_evidence',
                                      'summary', 'source', 'coverage', 'reconciliation')),
                        **{side: _table([_pick(seat, SEAT_FIELDS) for seat in row.get(side, [])
                                         if isinstance(seat, dict)], maximum=5,
                                         scope='该披露记录的来源方向前五席位；同名机构专用逐行保留，不合并为同一机构')
                           for side in ('buy', 'sell')}})
    return {**_pick(value, ('state', 'availability', 'unclassified_record_count')),
            'records': _table(records, maximum=MAX_DISCLOSURES,
                              scope='同股票日期区间，披露原因分别保留；超出两份记录时仅有界返回'),
            'warnings': value.get('warnings', [])}


def read_data(day, *, market, query=''):
    """Return a normal page envelope with its safe AI projection and CAS binding."""
    status = market.status()
    snapshot = status.get('snapshot_id')
    if not status.get('ready') or day not in market.calendar() or day > status.get('data_end', ''):
        raise ValueError('龙虎榜只读取已发布完成交易日')
    if not isinstance(query, str) or len(query) > 80:
        raise ValueError('龙虎榜股票查询须为不超过80字符的名称或代码')
    query = query.strip().casefold()
    with supplement_access():
        store = dragon_tiger.Store()
        # The same SH lease prevents a retention cleanup between page and seat
        # reads. A changed selected-day pointer is still rejected explicitly.
        before = store.index().get('days', {}).get(day)
        record = store.object(before) if before else None
        if record is not None and _denied(record):
            raise ValueError('所选龙虎榜原来源明确禁止外发或AI处理')
        sources = record.get('source_objects', []) if record else []
        if not isinstance(sources, list) or len(sources) > 6:
            raise ValueError('龙虎榜原始来源引用超出固定六批次预算')
        source_shas = []
        for pointer in sources:
            original = store.object(pointer)
            if original.get('date') != day or _denied(original):
                raise ValueError('龙虎榜原始来源日期或AI权限不符')
            source_shas.append(pointer['sha256'])
        page = dragon_tiger.read_view(day, market=market)
        if (page.get('date'), page.get('snapshot_id')) != (day, snapshot):
            raise ValueError('龙虎榜页面日期或发布代际不一致')
        if _denied(page):
            raise ValueError('所选龙虎榜页面明确禁止外发或AI处理')
        for kind, board in page.get('boards', {}).items():
            if (not isinstance(board, dict) or board.get('date') != day
                    or any(row.get('date') != day for row in _board_rows(board, kind))):
                raise ValueError('龙虎榜榜单来源实际日期与所选日不一致')
        projection = project_page(page, query)
        identity = _identity(market, query)
        if page.get('availability', {}).get('available') and identity:
            symbol = identity['instrument_id']
            values = {str(period): dragon_tiger.read_seats(symbol, day, period, market=market) for period in (1, 3)}
            if _denied(values):
                raise ValueError('所选龙虎榜席位明确禁止外发或AI处理')
            projection['stock_seats'] = {'instrument_id': symbol, 'name': identity.get('name'),
                'periods': {str(period): _seats(values[str(period)], day, snapshot, symbol, period) for period in (1, 3)}}
        else:
            projection['stock_seats'] = {'state': 'not_selected' if not query else 'unknown',
                'reason': '股票唯一精确名称或代码命中后读取单日及三日席位；本次未选择或未唯一确认，不推断身份'}
        after = store.index().get('days', {}).get(day)
        if before != after or market.status().get('snapshot_id') != snapshot:
            raise HTTPException(409, '读取期间龙虎榜逐日引用或行情代际变化，请重试')
    binding = {'date': day, 'market_snapshot_id': snapshot,
               'collection_snapshot_id': page.get('collection_snapshot_id'),
               'day_record_sha256': before.get('sha256') if before else None,
               'source_object_sha256': source_shas}
    reason = (page.get('availability') or {}).get('reason')
    return {'date': day, 'snapshot_id': snapshot, 'quality': page.get('state'),
            'source': 'local_published_dragon_tiger', 'projection': projection,
            'dragon_tiger_binding': binding, 'warnings': page.get('warnings', []),
            **({'unavailable_reason': reason} if reason else {})}
