"""Serve paginated member details using evidence pinned to the selected slot."""
from hashlib import sha256
import json
import re

from fastapi import HTTPException

from app.core import connect, data_dir, get_market, published_dir
from app.services import intraday_service as intraday
from app.services import noon_membership_context as membership
from app.services.intraday_stock_detail import history_for_symbols
from app.services.market_suspensions import suspensions_for_date
from app.services.noon_analysis import number
from app.services.strategy_limits import quote_index
from app.services.stock_limit_reference import attach_intraday_references
from app.services.supplement_access import supplement_reader

ANCHOR_TABLES = {'auction': 'market_auction_snapshot', 'noon': 'market_noon_snapshot'}
MEMBER_BOARDS = {'all', 'unknown', *membership.BOARDS}
HISTORY_SORTS = {'change_30d_pct', 'return_ytd_pct', 'last_limit_up_date', 'limit_up_1y'}
MEMBER_SORTS = {'rank', 'name', 'change_pct', 'amount', *HISTORY_SORTS}
AUCTION_BASIS = '与题材名单匹配的本次竞价冻结候选'
CLOSE_BASIS = '正式收盘代际的已留存成分与当日收盘行情'
LIVE_BASIS = '采集时冻结代际的已留存成分与该时点报价'


def _result(base, *, state='unknown', reason=None, **fields):
    return {**base, 'state': state, 'items': [], 'total': None, 'matched': 0,
            'pages': 1, 'quote_coverage': None, 'members_complete': False,
            'membership_as_of': None, 'membership_source': None, 'membership_basis': None,
            'unavailable_reason': reason, **fields}


def _page(base, rows, *, complete, observed, basis, limited=False):
    count = len(rows)
    valid = sum(row['quote_available'] for row in rows)
    filtered = [row for row in rows if base['board'] == 'all' or base['board'] == row['board']]
    filtered.sort(key=lambda row: (row['change_pct'] is None, -(row['change_pct'] or 0), row['instrument_id']))
    filtered = [{**row, 'rank': rank} for rank, row in enumerate(filtered, 1)]
    field = base['sort']
    history = history_for_symbols(base['date'], base['slot'], [row['instrument_id'] for row in filtered]) if filtered and field in HISTORY_SORTS else None
    if field != 'change_pct' or base['direction'] != 'desc':
        known, unknown = [], []
        for row in filtered:
            values = history['items'].get(row['instrument_id'], {}) if history else row
            value = values.get(field)
            if field == 'limit_up_1y' and value is None:
                value = values.get('limit_up_1y_observed') or None
            if value is None or value == '':
                unknown.append(row)
            else:
                known.append((value, row))
        known.sort(key=lambda item: item[0], reverse=base['direction'] == 'desc')
        filtered = [row for _, row in known] + unknown
    size = base['limit']
    pages = max(1, (len(filtered) + size - 1) // size)
    page = min(base['page'], pages)
    items = filtered[(page - 1) * size:page * size]
    suspension = suspensions_for_date(base['date'])
    for row in items:
        if not row['quote_available'] and (evidence := suspension.get(row['instrument_id'])):
            row['trading_status'] = 'suspended'
            row['suspension'] = evidence
            row['missing_reason'] = '本日已核验停牌，没有当日成交行情'
    history_page = None if history is None else {
        **history, 'items': {row['instrument_id']: history['items'][row['instrument_id']]
                             for row in items if row['instrument_id'] in history['items']}}
    return _result(base, state='available' if complete and count == valid and not limited else 'partial',
                   items=items, total=count, matched=len(filtered), history=history_page,
                   page=page, pages=pages, quote_coverage={'valid': valid, 'total': count},
                   members_complete=complete and not limited, membership_as_of=observed,
                   membership_source='fuyao', membership_basis=basis)


def _height(value):
    return value if type(value) is int and value > 0 else None


def _stock(symbol, quote=None, *, name=None, change_field='change_pct', price_field='latest_price',
           amount_field='amount', valid=None, state=None, height=None):
    quote = quote or {}
    price = number(quote.get(price_field), positive=True)
    valid = price is not None if valid is None else valid
    board = membership._display_board({'instrument_id': symbol, 'board': quote.get('board')})
    return {'instrument_id': symbol, 'name': name or quote.get('name') or symbol,
            'board': board, 'change_pct': number(quote.get(change_field)) if valid else None,
            'latest_price': price if valid else None, 'amount': number(quote.get(amount_field), nonnegative=True) if valid else None,
            'quote_available': valid, 'limit_up_state': state if valid else None,
            'continue_day_cnt': _height(height) if valid else None,
            'missing_reason': None if valid else '该成员没有可核验的同一时点报价'}


def _snapshot(base):
    slot = base['slot']
    table = ANCHOR_TABLES.get(slot, 'market_intraday_capture')
    column = ' AND slot=?' if slot not in ANCHOR_TABLES else ''
    args = (base['date'], slot) if column else (base['date'],)
    with connect() as connection:
        return connection.execute(
            f'SELECT * FROM {table} WHERE trade_date=?{column} AND snapshot_id IS NOT NULL', args).fetchone()


def _auction(base, row):
    from app.services.auction_integrity import validate_snapshot
    from app.services.auction_topic_summary import build_topic_summary

    try:
        payload = validate_snapshot(dict(row))
    except ValueError:
        return _result(base, reason='竞价快照或来源原件未通过核验')
    summary = build_topic_summary(payload)
    groups = [item for item in summary['groups'] if item['topic_code'] == base['topic_code']]
    if len(groups) != 1:
        return _result(base, reason=summary['reason'] if not summary['groups'] else '本次竞价候选未能核验该题材成员',
                       membership_basis=AUCTION_BASIS)
    group = groups[0]
    quotes = {item['instrument_id']: item for item in payload['quotes']['items']}
    rows = [_stock(symbol, quotes.get(symbol), change_field='auction_pct', price_field='auction_price',
                   amount_field='auction_amount',
                   valid=quotes[symbol].get('state') == 'observed' and number(quotes[symbol].get('auction_pct')) is not None)
            for symbol in group['instrument_ids']]
    return _page(base, rows, complete=group.get('members_complete') is True,
                 observed=group['members_observed_date'], basis=AUCTION_BASIS, limited=True)


def _noon(base, row):
    from app.services.noon_sector_views import sector_member_rows

    raw = row['payload_json'].encode()
    if len(raw) > 8 * 1024 * 1024 or sha256(raw).hexdigest() != row['content_sha256']:
        return _result(base, reason='午评内容未通过SHA核验')
    payload = json.loads(raw)
    if payload.get('date') != base['date'] or payload.get('version') != 'noon-snapshot-v1':
        return _result(base, reason='午评来源身份或日期不符')
    try:
        sector = sector_member_rows(payload, base['topic_code'])
    except HTTPException as error:
        if error.status_code == 404:
            return _result(base, reason=error.detail)
        raise
    if sector['state'] == 'unknown':
        return _result(base, reason=sector['unavailable_reason'], membership_basis=sector['membership_basis'])
    heights = {item['instrument_id']: item.get('continue_day_cnt')
               for item in payload['events'].get('limit_up', {}).get('items', ())}
    items = []
    for item in sector['items']:
        events = set(item['current_events'])
        state = {'limit_up': 'sealed', 'limit_break': 'broken'}.get(next(iter(events))) if len(events) == 1 else None
        items.append(_stock(item['instrument_id'], item, valid=item['quote_available'], state=state,
                            height=heights.get(item['instrument_id']) if state == 'sealed' else None))
    return _page(base, items, complete=sector['members_complete'],
                 observed=sector['membership_as_of'], basis=sector['membership_basis'])


def _topic_members(generation, expected_end, code, *, observed=None, reference_sha=None):
    from app.services.auction_topic_summary import _load_original_topics

    if reference_sha is not None:
        from app.analytics.market_storage import safe_object, verify_immutable_file

        path = safe_object(published_dir(), generation + '/reference.json')
        size = path.stat().st_size
        if not 0 < size <= membership.MAX_REFERENCE_BYTES:
            raise ValueError('原代际参考文件超出读取边界')
        verify_immutable_file(path, reference_sha, size)
    # The legacy evidence records the previous session as an upper bound,
    # while the explicit member_reference records the generation's own end.
    topics, _ = _load_original_topics(generation, expected_end,
                                      expected_end if reference_sha is None else None)
    matches = [topic for topic in topics if isinstance(topic, dict) and topic.get('topic_code') == code
               and topic.get('kind') in ('industry', 'concept') and topic.get('source') == 'fuyao'
               and membership._day(topic.get('observed_date')) and topic['observed_date'] <= expected_end
               and (observed is None or topic['observed_date'] == observed)]
    if len(matches) != 1:
        raise ValueError('冻结代际未找到唯一的同来源板块名单')
    topic = matches[0]
    symbols = topic.get('instrument_ids')
    if (not isinstance(symbols, list) or len(symbols) > membership.MAX_MEMBERS
            or any(not isinstance(symbol, str) or not intraday.STOCK_CODE.fullmatch(symbol) for symbol in symbols)
            or len(symbols) != len(set(symbols))):
        raise ValueError('来源成员代码重复、不可识别或超过读取边界')
    return symbols, topic['observed_date'], topic.get('members_complete') is True


def _bound_market(generation):
    current = get_market()
    if current.status().get('snapshot_id') == generation:
        return current
    from app.analytics.market import MarketData

    return MarketData(data_dir(), snapshot_id=generation)


def _closing_quotes(market, symbols, day, previous):
    """Read two trading days for just the requested symbols."""
    history = market.history_for_instruments(symbols, start=previous or day, end=day)
    actions = market.actions_for_instruments(symbols)
    quotes = {}
    for symbol, rows in history.items():
        indexed = quote_index(rows)
        current = indexed.get(day)
        if not _raw_close(current):
            continue
        prior = indexed.get(previous)
        change = None
        if (_raw_close(prior) and actions is not None
                and not any(action.get('date') in (None, day) for action in actions[symbol])):
            change = (current['close'] / prior['close'] - 1) * 100
        quotes[symbol] = {**current, 'change_pct': change}
    return quotes


def _raw_close(row):
    return (row and row.get('source') == 'fuyao' and row.get('price_basis') == 'none'
            and number(row.get('close'), positive=True) is not None)


def _close(base):
    from app.services.intraday_brief import read_brief_view
    from app.services.market_supplement import load_supplement
    from app.services.noon_context import _event_cohorts

    day, generation, code = base['date'], base['snapshot_id'], base['topic_code']
    anchors = read_brief_view(day)['anchors']
    anchor = next((item for item in anchors if item['slot'] == 'close'), None)
    if anchor is None:
        return _result(base, state='missing', reason='所选交易日没有留存正式收盘复盘')
    if anchor['id'] != generation:
        return _result(base, reason='收盘复盘快照已变化，请刷新观察页')
    if not any(item['instrument_id'] == code for item in anchor['sectors']):
        return _result(base, reason='本份收盘复盘未留存该板块')
    try:
        symbols, observed, complete = _topic_members(generation, day, code)
        market = _bound_market(generation)
        status = market.status()
        calendar = market.calendar()
        if status.get('snapshot_id') != generation or status.get('data_end') != day or day not in calendar:
            return _result(base, reason='本份复盘的原收盘行情代际不可核验')
        position = calendar.index(day)
        names = {row['instrument_id']: row.get('name') for row in market.instruments(limit=10000)}
        quotes = _closing_quotes(market, symbols, day, calendar[position - 1] if position else None)
        reference = {**market.reference(), '_supplement': load_supplement(
            generation, dates=(day,), fields=('public_pools', 'annual_pools', 'annual_down_pools'))}
        events = _event_cohorts(reference, day, quotes)
    except (OSError, ValueError, TypeError, KeyError):
        return _result(base, reason='原收盘代际的同来源成员名单或同日收盘报价未通过核验',
                       membership_basis=CLOSE_BASIS)
    sealed = {item['instrument_id']: item['previous_board_count'] for item in events['limit_up']['items']}
    broken = {item['instrument_id'] for item in events['limit_break']['items']} - sealed.keys()
    rows = [_stock(symbol, quotes.get(symbol), name=names.get(symbol), price_field='close',
                   state='sealed' if symbol in sealed else 'broken' if symbol in broken else None,
                   height=sealed.get(symbol)) for symbol in symbols]
    return _page(base, rows, complete=complete, observed=observed, basis=CLOSE_BASIS)


def _member_provenance(snapshot):
    source = snapshot.get('member_reference')
    if source is not None:
        if not isinstance(source, dict):
            return None
        generation, end = source.get('generation'), source.get('date')
        reference_sha = None
    elif snapshot['version'] == intraday.VERSION:
        # The v5 collector used a fixed market object for both membership
        # discovery and its accepted prior-session source check.
        checks = [row for row in snapshot.get('source_checks', ())
                  if isinstance(row, dict) and row.get('domain') == 'prior_session' and row.get('state') == 'accepted']
        if len(checks) != 1:
            return None
        generation = checks[0].get('generation')
        reference_sha = checks[0].get('reference_sha256')
        end = snapshot.get('relay_feedback', {}).get('prior_session', {}).get('date')
        if not membership.SHA.fullmatch(str(reference_sha)):
            return None
    else:
        return None
    if not membership.GENERATION.fullmatch(str(generation)) or not membership._day(end) or end >= snapshot['date']:
        return None
    return generation, end, reference_sha


def _live(base, row):
    snapshot = intraday.decode_snapshot(row)
    board = snapshot.get('board_stocks', {}).get(base['topic_code'])
    provenance = _member_provenance(snapshot)
    if not board or not provenance:
        return _result(base, reason='此时点未留存可核验的成员名单凭证，只能查看快照中的个股样本',
                       membership_basis=LIVE_BASIS)
    generation, end, reference_sha = provenance
    try:
        symbols, observed, complete = _topic_members(generation, end, base['topic_code'],
                                                     observed=board['member_date'], reference_sha=reference_sha)
        market = _bound_market(generation)
        names = {row['instrument_id']: row.get('name') for row in market.instruments(limit=10000)}
    except (OSError, ValueError, TypeError, KeyError):
        return _result(base, reason='该时点冻结的原成员名单未留存或未通过校验', membership_basis=LIVE_BASIS)
    quotes = membership._quotes(snapshot.get('comparison_input', {}))
    ladder = snapshot.get('ladder', {})
    sealed = {item['instrument_id']: None for item in snapshot.get('sealed_pool_input', {}).get('items', ())}
    for group in ladder.get('groups', ()):
        sealed.update({item['instrument_id']: item.get('board_count') for item in group['items']})
    broken = {item['instrument_id'] for item in ladder.get('broken_board', {}).get('items', ())}
    samples = {item['instrument_id']: item for item in board.get('items', ())}
    for item in samples.values():
        if item.get('limit_up_state') == 'sealed':
            sealed[item['instrument_id']] = item.get('continue_day_cnt')
        elif item.get('limit_up_state') == 'broken':
            broken.add(item['instrument_id'])
    rows = [_stock(symbol, quotes.get(symbol) or samples.get(symbol),
                   name=names.get(symbol),
                   valid=(number(quotes[symbol].get('latest_price'), positive=True) is not None if symbol in quotes
                          else number(samples.get(symbol, {}).get('change_pct')) is not None),
                   state='sealed' if symbol in sealed else 'broken' if symbol in broken else None,
                   height=sealed.get(symbol)) for symbol in symbols]
    result = _page(base, rows, complete=complete, observed=observed, basis=LIVE_BASIS)
    try:
        reference = market.reference()
        if market.status().get('data_end') == end:
            attach_intraday_references(snapshot, result['items'], reference.get('master', {}), reference.get('calendar', ()))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result


@supplement_reader
def read_members(day, slot, snapshot_id, topic_code, *, board='all', page=1, limit=30,
                 sort='change_pct', direction='desc'):
    day = intraday.day_value(day)
    live_slots = {item['key'] for item in (*intraday.SLOTS, *intraday.LEGACY_SLOTS)}
    if (slot not in live_slots | {'auction', 'noon', 'close'} or board not in MEMBER_BOARDS
            or not membership.SECTOR.fullmatch(topic_code)
            or not 1 <= page <= 10000 or not 1 <= limit <= 32
            or sort not in MEMBER_SORTS or direction not in ('asc', 'desc')):
        raise HTTPException(422, '盘中板块、时点、排序或分页参数无效')
    if slot == 'close':
        valid_id = membership.GENERATION.fullmatch(snapshot_id)
    else:
        prefix = slot if slot in ANCHOR_TABLES else 'intraday'
        valid_id = re.fullmatch(prefix + r'_[a-f0-9]{32}', snapshot_id)
    if not valid_id:
        raise HTTPException(422, '盘中快照身份无效')
    base = {'date': day, 'slot': slot, 'snapshot_id': snapshot_id, 'topic_code': topic_code,
            'board': board, 'page': page, 'limit': limit, 'sort': sort, 'direction': direction}
    if slot == 'close':
        return _close(base)
    row = _snapshot(base)
    if row is None:
        return _result(base, state='missing', reason='该时点没有留存行情快照')
    if row['snapshot_id'] != snapshot_id:
        return _result(base, reason='所选时点的快照已变化，请刷新观察页')
    read = {'auction': _auction, 'noon': _noon}.get(slot, _live)
    return read(base, row)
