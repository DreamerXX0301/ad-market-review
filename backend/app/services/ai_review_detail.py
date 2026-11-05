"""Search retained auction/noon evidence before applying the AI output bound.

Only the selected business SQLite and its original immutable member proofs are
read. No provider, current classification, completed-day fallback or task write
belongs on this path. Callers own date/argument validation and the outer 24 KiB
tool envelope; this module keeps its factual projection below 20 KiB.
"""
from collections import Counter
from datetime import datetime, time
import math
import sqlite3

from fastapi import HTTPException

from app.core import dumps
from app.services.noon_analysis import number
from app.services.noon_collection import CHINA

MAX_DATA_BYTES = 20 * 1024
MAX_STOCKS = 30
MAX_DIRECTIONS = 8
MAX_MEMBERS = 8
_SCOPE = '仅所选日期原已完成留存的时点资料；缺报、缺名单与未知池保持未知，不回退收盘或使用后续归属。'
_DIRECTION_FIELDS = ('instrument_id', 'name', 'kind', 'change_pct', 'latest_price', 'prev_close', 'amount',
                     'quote_source', 'quote_basis', 'quote_date', 'member_date', 'member_count',
                     'member_list_complete', 'member_reference_sha256', 'rankable_members', 'advancers',
                     'decliners', 'up_ratio', 'member_coverage_pct', 'breadth_state', 'breadth_reason',
                     'limit_up_count', 'limit_up_count_state', 'broken_count', 'broken_count_state',
                     'broken_count_reason', 'first_board_count', 'multi_board_count', 'board_height_count_state',
                     'sample_count', 'valid_count', 'missing_count', 'high_open', 'low_open', 'flat_open')


def _pick(row, fields):
    return {
        key: row[key] for key in fields if key in row and
        (not isinstance(row[key], float) or math.isfinite(row[key]))}


def _table(rows, *, limit, scope=None):
    result = {'matched_total': len(rows), 'returned_count': min(len(rows), limit),
              'truncated': len(rows) > limit, 'items': rows[:limit]}
    if scope:
        result['scope'] = scope
    return result


def _match(rows, query):
    if not query:
        return []
    query = query.casefold()
    exact = [row for row in rows if query in (str(row.get('instrument_id', '')).casefold(),
             str(row.get('instrument_id', '')).split('.')[0], str(row.get('name', '')).casefold())]
    return exact or [row for row in rows if query in str(row.get('name', '')).casefold()
                     or query in str(row.get('instrument_id', '')).casefold()]


def _classification(rows, *, state='observed_sample', reason=None):
    rows = [dict(row) for row in rows if isinstance(row, dict)]
    return {'state': state if rows else 'unknown', 'matched_total': len(rows),
            'returned_count': min(6, len(rows)), 'truncated': len(rows) > 6,
            'items': rows[:6], 'reason': reason if reason else
            '同份原代际成员关联，不是上涨原因' if rows else '原时点没有可核验归属；不以当前资料补写'}


def _ordered(rows):
    return sorted(rows, key=lambda row: (number(row.get('limit_up_count')) is None,
        -(number(row.get('limit_up_count')) or 0), number(row.get('change_pct')) is None,
        -(number(row.get('change_pct')) or 0), row['instrument_id']))


def _project(phase, query, stocks, directions, member_rows, pools):
    stock_matches, direction_matches = _match(stocks, query), _match(directions, query)
    # An exact stock identity must not silently become a similarly named sector.
    exact_stock = query and any(query.casefold() in (row['instrument_id'].casefold(),
        row['instrument_id'].split('.')[0], str(row.get('name', '')).casefold()) for row in stock_matches)
    if exact_stock:
        direction_matches = []
    projected = []
    for row in _ordered(direction_matches)[:MAX_DIRECTIONS]:
        item = _pick(row, _DIRECTION_FIELDS)
        try:
            members, membership = member_rows(row)
            item['members'] = _table(members, limit=MAX_MEMBERS, scope='原成员名单与本份时点报价；缺报价成员保留未知')
            item['membership'] = membership
        except (ValueError, TypeError, KeyError, AttributeError, OSError, HTTPException):
            item['members'] = {'matched_total': None, 'returned_count': 0, 'truncated': False, 'items': []}
            item['membership'] = {'state': 'unknown', 'reason': '原时点成员凭证未通过核验；独立指数行情仍保留'}
        projected.append(item)
    data = {'phase': phase, 'query': query, 'scope': _SCOPE,
            'units': {'price': 'CNY', 'amount': 'CNY', 'volume': '股' if phase == 'noon' else '手', 'change_pct': '%', 'count': '家'},
            'source_scope': {'stock_count': len(stocks), 'direction_count': len(directions),
                             'query_searched_full_retained_range': True},
            'stocks': _table(stock_matches, limit=MAX_STOCKS),
            'directions': {**_table(direction_matches, limit=MAX_DIRECTIONS), 'items': projected},
            'distributions': {}, 'pools': pools}
    if not query:
        data['stocks'] = _table(stocks, limit=0, scope='空查询只列方向分布，股票总数为原时点去重身份数；明确股票名或代码再查明细')
        data['directions'] = _table(directions, limit=0, scope='空查询完整匹配数；有界行业/概念分布见distributions')
        for kind in ('industry', 'concept'):
            rows = [_pick(row, _DIRECTION_FIELDS) for row in _ordered(directions) if row.get('kind') == kind]
            data['distributions'][kind] = _table(rows, limit=6,
                scope='完整已留存方向中按可核验封板家数、当时涨幅排序；概念可重叠，不能加总为股票总数')
    return _fit(data)


def _fit(data):
    # Whole facts are removed only after matching all retained identities. The
    # original matched_total never shrinks and each affected table says so.
    while len(dumps(data).encode()) > MAX_DATA_BYTES:
        tables = [data['stocks'], *data['distributions'].values(),
                  *(row['members'] for row in data['directions']['items'])]
        candidates = [table for table in tables if table['items']]
        if candidates:
            chosen = max(candidates, key=lambda table: len(dumps(table['items']).encode()))
        elif data['directions']['items']:
            chosen = data['directions']
        else:
            raise ValueError('时点详情必要来源与缺口超过容量')
        chosen['items'].pop()
        chosen.update(returned_count=len(chosen['items']), truncated=True)
        data['bounded_notice'] = '完整已存范围已检索；超出输出容量的整条记录未返回，不能称已展示全部匹配。'
    return data


def project_auction(payload, query, *, topics, discovery=None):
    """Original main candidates and a separately validated bound discovery union."""
    from app.services.auction_feedback import build_feedback, _opening_reference
    from app.services.noon_membership_context import _display_board

    raw_rows = [dict(row) for row in payload['quotes']['items']]
    if discovery:
        raw_rows += [{**row, 'state': 'observed'} for row in
                     [*discovery.get('stocks', []), *discovery.get('supplement', {}).get('items', [])]]
    grouped = {}
    for row in raw_rows:
        grouped.setdefault(row['instrument_id'], []).append(row)
    feedback = {row['instrument_id']: row for group in build_feedback(payload)['groups'] for row in group['items']}
    classifications, groups = {}, {}
    for summary in topics:
        for code, value in summary.get('stock_classifications', {}).items():
            classifications[code] = value
        for group in summary.get('groups', []):
            key = group['topic_code'], group['kind']
            current = groups.setdefault(key, {**group, 'instrument_ids': set()})
            if current.get('source_sha256') != group.get('source_sha256'):
                current['conflict'] = True
            current['instrument_ids'].update(group['instrument_ids'])
    stocks = []
    for code, values in grouped.items():
        quote = values[0]
        observed = [row for row in values if row.get('state') == 'observed']
        valid = [row for row in observed if number(row.get('auction_price'), positive=True) is not None
                 and number(row.get('pre_close_price'), positive=True) is not None
                 and number(row.get('auction_pct')) is not None and
                 abs((row['auction_price'] / row['pre_close_price'] - 1) * 100 - row['auction_pct']) <= .06]
        conflict = bool(valid and any(abs(row['auction_price'] - valid[0]['auction_price']) > .011
            or abs(row['auction_pct'] - valid[0]['auction_pct']) > .06 for row in valid[1:]))
        available = bool(valid and not conflict)
        quote = valid[0] if available else quote
        row = _pick(quote, ('instrument_id', 'name'))
        if available:
            row.update(_pick(quote, ('auction_price', 'pre_close_price', 'auction_pct', 'auction_amount',
                'auction_volume', 'auction_volume_ratio', 'auction_yesterday_ratio_pct')))
        row.update(quote_available=available, quote_reason='同股竞价终态来源冲突' if conflict else
                   None if available else '本份竞价报价无效或缺失', board=_display_board(row),
                   limit_up_state='unknown', limit_down_state='unknown', broken_state='unknown')
        row.update(_pick(feedback.get(code, {}), ('previous_state', 'previous_board_count')))
        if available and (reference := _opening_reference(quote, code)) and reference['matched']:
            row.update(opening_reference_price=reference['reference_price'], opening_reference_confirmed_limit=False)
        entry = classifications.get(code, {})
        row['classification'] = _classification([*entry.get('industries', []), *entry.get('topics', [])],
                                               state=entry.get('state', 'unknown'), reason=entry.get('reason'))
        stocks.append(row)
    indexed = {row['instrument_id']: row for row in stocks}
    directions = []
    for group in groups.values():
        if group.get('conflict'):
            continue
        rows = [indexed[code] for code in sorted(group['instrument_ids']) if code in indexed]
        valid = [row for row in rows if row['quote_available']]
        changes = [row['auction_pct'] for row in valid]
        directions.append({'instrument_id': group['topic_code'], 'name': group['name'], 'kind': group['kind'],
            'change_pct': math.fsum(changes) / len(changes) if changes else None,
            'quote_source': 'fuyao', 'quote_basis': 'retained_candidate_auction_average',
            'quote_date': payload['date'], 'member_date': group['members_observed_date'],
            'sample_count': len(rows), 'valid_count': len(valid), 'missing_count': len(rows) - len(valid),
            'high_open': sum(value > 0 for value in changes), 'low_open': sum(value < 0 for value in changes),
            'flat_open': sum(value == 0 for value in changes), '_ids': [row['instrument_id'] for row in rows]})
    def members(row):
        values = [indexed[code] for code in row['_ids']]
        values.sort(key=lambda value: (not value['quote_available'], -(value.get('auction_pct') or 0), value['instrument_id']))
        return values, {'state': 'observed_sample', 'scope': '竞价已存候选交集，非全板块成员',
                        'member_date': row['member_date']}
    return _project('auction', query, stocks, directions, members,
                    {'state': 'not_applicable', 'reason': '竞价价可给涨停参考，不能据此确认当日封板、炸板或收盘晋级'})


def project_noon(payload, query):
    from app.services import noon_membership_context as membership
    from app.services.noon_sector_views import sector_member_rows, close_classifications
    from app.services.intraday_rotation import noon_rotation_sectors, _metric_inputs
    from app.services.intraday_board_changes import _conflicts

    context = _metric_inputs({**payload, 'slot': 'noon', 'comparison_input': {'stocks': payload.get('stocks', [])}})
    quotes, pools, heights = context
    conflicts = _conflicts({**payload, 'slot': 'noon'})
    stocks = []
    original = {}
    for quote in payload.get('stocks', []):
        code = quote.get('instrument_id') if isinstance(quote, dict) else None
        if isinstance(code, str) and membership.SYMBOL.fullmatch(code):
            original.setdefault(code, quote)
    for code, quote in original.items():
        checked = quotes.get(code)
        row = _pick(quote, ('instrument_id', 'name'))
        row.update(quote_available=checked is not None, board=membership._display_board(row),
                   quote_reason=None if checked is not None else '本份午评价量或前收口径未通过核验')
        if checked:
            row.update(_pick(checked, ('latest_price', 'prev_close', 'change_pct', 'amount', 'volume')))
        for key, field, hit in (('limit_up', 'limit_up_state', 'sealed'),
                                ('limit_break', 'broken_state', 'broken'), ('limit_down', 'limit_down_state', 'sealed')):
            pool, complete = pools[key]
            row[field] = ('unknown' if code in conflicts or not checked else hit if code in pool
                          else 'not_in_verified_pool' if complete else 'unknown')
        if row['limit_up_state'] == 'sealed' and code in heights:
            row['board_count'] = heights[code]
        stocks.append(row)
    matches = _match(stocks, query)[:MAX_STOCKS]
    classifications = membership.frozen_stock_classifications(payload, [row['instrument_id'] for row in matches])
    derived = close_classifications(payload, [row['instrument_id'] for row in matches])
    for row in stocks:
        entry = classifications.get(row['instrument_id'], {})
        if entry.get('state') == 'unknown' and row['instrument_id'] in derived:
            entry = derived[row['instrument_id']]
        row['classification'] = _classification([*entry.get('industries', []), *entry.get('topics', [])],
                                               state=entry.get('state', 'unknown'), reason=entry.get('reason'))
    directions = noon_rotation_sectors(payload)
    indexed = {row['instrument_id']: row for row in stocks}
    def members(row):
        view = sector_member_rows(payload, row['instrument_id'])
        if view['state'] != 'available':
            raise ValueError(view['unavailable_reason'])
        values = [indexed.get(item['instrument_id'], {
            'instrument_id': item['instrument_id'], 'name': item.get('name') or item['instrument_id'],
            'quote_available': False, 'quote_reason': '本份午评未留存该成员报价'}) for item in view['items']]
        values.sort(key=lambda value: (not value['quote_available'], -(value.get('change_pct') or 0), value['instrument_id']))
        shown = values[:MAX_MEMBERS]
        classes = close_classifications(payload, [value['instrument_id'] for value in shown])
        for value in shown:
            entry = classes.get(value['instrument_id'], {})
            if entry.get('state') in ('observed_sample', 'partial'):
                value['classification'] = _classification([*entry.get('industries', []), *entry.get('topics', [])],
                    state=entry.get('state', 'unknown'), reason=entry.get('reason'))
        return values, {'state': 'observed' if view['members_complete'] else 'partial',
                        'source_sha256': view['membership_source_sha256'],
                        'member_date': row.get('member_date'), 'quote_coverage': view['quote_coverage']}
    pool_summary = {kind: {'state': 'observed' if complete else 'partial' if rows else 'unknown',
                           'count': len(rows) if complete or rows else None}
                    for kind, (rows, complete) in pools.items()}
    return _project('noon', query, stocks, directions, members, pool_summary)


def _ready(connection, row, phase, now):
    from app.services.auction_integrity import validate_snapshot
    from app.services.intraday_brief import _verified_noon_payload
    from app.services.intraday_board_changes import noon_frame
    from app.services.ai_read_tools import _source_denied

    payload = validate_snapshot(row) if phase == 'auction' else _verified_noon_payload(row)
    begin, end = (datetime.fromisoformat(payload[key]) for key in ('collected_start_at', 'collected_end_at'))
    if (begin.tzinfo is None or end.tzinfo is None or begin > end or end > now
            or begin.astimezone(CHINA).date().isoformat() != row['trade_date']
            or end.astimezone(CHINA).date().isoformat() != row['trade_date']):
        raise ValueError('采集日期、窗口或完成截止未核验')
    if phase == 'noon' and not time(11, 30) <= begin.astimezone(CHINA).time().replace(tzinfo=None) <= end.astimezone(CHINA).time().replace(tzinfo=None) < time(13):
        raise ValueError('不是该日午间窗口的已保存报价')
    recorded = row['recorded_at_ms']
    if type(recorded) is not int or not int(end.timestamp() * 1000) <= recorded <= int(now.timestamp() * 1000):
        raise ValueError('时点正文尚未完成留存')
    job = connection.execute('SELECT status,finished_at_ms FROM job WHERE job_id=?', (row['job_id'],)).fetchone()
    if (job is None or job['status'] not in ('succeeded', 'partial') or type(job['finished_at_ms']) is not int
            or not int(end.timestamp() * 1000) <= job['finished_at_ms'] <= int(now.timestamp() * 1000)):
        raise ValueError('所属采集任务未完成，不能读取未完成时点')
    if _source_denied(payload):
        raise ValueError('原时点来源明确禁止AI处理或外发')
    return payload if phase == 'auction' else noon_frame(row)


def _auction_topics(payload, rows, connection):
    from app.services.auction_topic_summary import build_topic_summary

    summaries = []
    for offset in range(0, len(rows), 100):
        chunk = rows[offset:offset + 100]
        summaries.append(build_topic_summary({**payload,
            'universe': {'items': [{'instrument_id': row['instrument_id']} for row in chunk]},
            'quotes': {'items': chunk}}, connection=connection))
    return summaries


def fill_review_detail(result, day, phase, query, now):
    """Append exactly one dated factual evidence block; never require close data."""
    from app.services.ai_read_tools import _paths, _connect, _gap
    from app.services.auction_integrity import validate_discovery

    if phase not in ('auction', 'noon'):
        raise ValueError('不支持的时点详情')
    table, maximum = ('market_auction_snapshot', 4 * 1024 * 1024) if phase == 'auction' else ('market_noon_snapshot', 8 * 1024 * 1024)
    for source, path in _paths():
        connection = _connect(path)
        if connection is None:
            continue
        try:
            if not connection.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone():
                continue
            connection.execute('BEGIN')
            row = connection.execute(f'SELECT * FROM {table} WHERE trade_date=? AND length(CAST(payload_json AS BLOB))<=?', (day, maximum)).fetchone()
            if row is None:
                exists = connection.execute(f'SELECT 1 FROM {table} WHERE trade_date=?', (day,)).fetchone()
                if exists:
                    raise ValueError('所选来源的原时点正文超过读取边界')
                continue
            payload = _ready(connection, row, phase, now)
            discovery_binding = None
            member_sources = []
            if phase == 'auction':
                discovery = None
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_auction_discovery'").fetchone():
                    saved = connection.execute('SELECT * FROM market_auction_discovery WHERE trade_date=? AND primary_snapshot_id=? AND job_id=? AND length(CAST(payload_json AS BLOB))<=?',
                        (day, row['snapshot_id'], row['job_id'], 3 * 1024 * 1024)).fetchone()
                    if saved and saved['recorded_at_ms'] <= int(now.timestamp() * 1000):
                        try:
                            from app.services.ai_read_tools import _source_denied
                            value = validate_discovery(saved)
                            if not _source_denied(value):
                                discovery = value
                                discovery_binding = {key: saved[key] for key in (
                                    'primary_snapshot_id', 'content_sha256', 'recorded_at_ms')}
                        except (ValueError, KeyError, TypeError):
                            _gap(result, 'auction_discovery_details', '补充发现原来源未通过SHA、日期或权限核验；主快照仍保留')
                merged = {item['instrument_id']: item for item in payload['quotes']['items']}
                for item in [*(discovery or {}).get('stocks', []), *(discovery or {}).get('supplement', {}).get('items', [])]:
                    merged.setdefault(item['instrument_id'], {**item, 'state': 'observed'})
                topics = _auction_topics(payload, list(merged.values()), connection)
                frozen = (discovery or {}).get('membership')
                if frozen and any(summary.get(key) != frozen.get(key) for summary in topics
                                  for key in ('source_snapshot_id', 'source_sha256')):
                    _gap(result, 'auction_discovery_members', '补充发现的原成员证明与主代际冲突；补充归属保持未知')
                    topics = _auction_topics(payload, payload['quotes']['items'], connection)
                data = project_auction(payload, query, topics=topics, discovery=discovery)
                for summary in topics:
                    source_identity = _pick(summary, ('source_snapshot_id', 'source_sha256'))
                    if source_identity.get('source_sha256') and source_identity not in member_sources:
                        member_sources.append(source_identity)
            else:
                if not payload:
                    raise ValueError('原午评未通过正文与事件凭证核验')
                data = project_noon(payload, query)
            binding = {key: row[key] for key in ('snapshot_id', 'trade_date', 'content_sha256', 'collected_start_at', 'collected_end_at', 'recorded_at_ms')}
            if discovery_binding:
                binding['auction_discovery'] = discovery_binding
            if member_sources:
                binding['member_sources'] = member_sources
            result['source_bindings']['review_detail'] = binding
            result['source'] = [source]
            result['summary'].update(phase=phase, query=query, source_scope=data['source_scope'],
                snapshot_id=row['snapshot_id'], capture_end=row['collected_end_at'])
            result['evidence'].append({'evidence_id': 'review_detail:' + phase,
                'kind': phase + '_details', 'source': 'saved_' + phase, 'title': '原竞价详情' if phase == 'auction' else '原午评详情',
                'dates': {'trade_date': day, 'collected_start_at': row['collected_start_at'], 'collected_end_at': row['collected_end_at']}, 'data': data})
            result['state'] = 'partial' if result['gaps'] or data['stocks']['truncated'] or data['directions']['truncated'] else 'available'
            return
        except (ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error) as error:
            _gap(result, phase + '_details', str(error) or '原时点详情无法核验')
            return
        finally:
            connection.close()
    due = datetime.combine(datetime.fromisoformat(day).date(), time(9, 25) if phase == 'auction' else time(11, 30), CHINA)
    state = 'not_due' if now < due else 'not_collected' if day == now.date().isoformat() else 'unknown'
    result['state'] = state
    _gap(result, phase + '_details', '该时点尚未到' if state == 'not_due' else '所选日没有已完成留存的该时点记录；不回退其他日期', state)
