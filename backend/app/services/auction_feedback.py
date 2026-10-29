"""纯计算冻结名单的前日事件→本次竞价对照；不读取当前行情或外部资料。"""
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re

from fastapi import HTTPException

from app.core import connect
from app.services.auction_integrity import validate_snapshot

VERSION = 'auction-feedback-v1'
SYMBOL = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)\Z')
SCOPE = ('仅对本份竞价最多100只冻结候选，按严格前日已核验事件和本次有效竞价报价作事实对照；'
         '高开/低开仅以0%为界，不作综合评分。高开不代表全天走强，低开不代表全天走弱，后续承接待核对；'
         '缺事件、缺报价或事件冲突保持未知，不补造昨日预期或上涨下跌原因。')
LIMIT_OPEN_REASON = '缺少可计算常规涨停参考价的有效竞价、前收、股票身份或正成交量。'
LIMIT_OPEN_BASIS = 'auction-standard-band-price-reference-v1'
SPECS = [
    ('repair', '昨日炸板→今日高开', '前日炸板后本次高开，作为修复观察；后续承接与能否回封仍待核对。'),
    ('weaken', '昨日涨停→今日低开', '前日涨停后本次低开，作为转弱观察；后续承接与全天结果仍待核对。'),
    ('continuation', '昨日涨停→今日高开', '前日涨停后本次高开，作为延续观察；不能把高开直接视为继续涨停。'),
    ('weak_continuation', '昨日炸板→今日低开', '前日炸板后本次低开，作为弱势延续观察；是否修复仍待后续核对。'),
    ('flat_limit_up', '昨日涨停→今日平开待确认', '已确认前日涨停与本次平开，强弱方向尚待后续承接核对。'),
    ('flat_limit_break', '昨日炸板→今日平开待确认', '已确认前日炸板与本次平开，修复或继续转弱均待后续核对。'),
    ('event_conflict', '前日事件冲突待核验', '同一候选同时出现在严格前日涨停与炸板已核验样本中，未擅选事件标签。'),
    ('unknown_quote', '本次竞价报价未知', '本次有效报价缺失、重复或无效，不按平开或零涨幅处理。'),
    ('unknown_event', '前日事件未知', '本份快照没有可唯一核验的严格前日涨停或炸板事件，不从涨幅、名称或当前板数倒推。'),
]


def _limit_open_unknown():
    return {'key': 'limit_up_open', 'title': '昨日涨停/炸板→今日涨停开参考', 'state': 'unknown',
            'interpretation': '按冻结代码所属市场的常规10/20/30%档，用来源前收按分取整计算参考价，并与本次有效竞价价精确比较；'
                              '不是来源直接确认的今日限价，未核验当日无涨跌幅限制等例外，不代表全天一字、封单稳定或可成交。',
            'reason': LIMIT_OPEN_REASON, 'items': [], 'count': None, 'valid_count': 0,
            'missing_count': 0, 'average_auction_pct': None, 'median_auction_pct': None,
            'basis': LIMIT_OPEN_BASIS, 'confirmed_limit_price': False, 'checked_count': 0}


def _price_tick(value):
    number = _number(value, positive=True)
    if number is None:
        return None
    try:
        exact = Decimal(str(number))
        rounded = exact.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        return rounded if rounded > 0 and abs(exact - rounded) <= Decimal('.0000001') else None
    except InvalidOperation:
        return None


def _opening_reference(quote, symbol):
    """A labelled price-reference screen, never proof of today's actual limit."""
    name = str(quote.get('name') or '').strip().upper()
    # No dated risk-warning regime is frozen. Do not guess it from a name.
    if not name or name.startswith(('ST', '*ST', 'SST', 'S*ST', 'N', 'C')):
        return None
    code, exchange = symbol.split('.')
    rate = (20 if (exchange == 'SH' and code.startswith(('688', '689'))
                    or exchange == 'SZ' and code.startswith(('300', '301')))
            else 10 if (exchange == 'SH' and code.startswith(('600', '601', '603', '605'))
                        or exchange == 'SZ' and code.startswith(('000', '001', '002', '003')))
            else 30 if exchange == 'BJ' and code.startswith(('4', '8', '9')) else None)
    price, previous = _price_tick(quote.get('auction_price')), _price_tick(quote.get('pre_close_price'))
    if (rate is None or quote.get('state') != 'observed' or price is None or previous is None
            or _number(quote.get('auction_pct')) is None
            or _number(quote.get('auction_volume'), positive=True) is None
            or _number(quote.get('auction_amount'), positive=True) is None
            or not isinstance(quote.get('unit_check'), dict) or quote['unit_check'].get('state') != 'verified'):
        return None
    expected_change = (float(price / previous) - 1) * 100
    if not math.isfinite(expected_change) or abs(expected_change - quote['auction_pct']) > .06:
        return None
    upper = (previous * (1 + Decimal(rate) / 100)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    upper = max(upper, previous + Decimal('.01'))
    return {'matched': price == upper, 'rate_pct': rate, 'reference_price': float(upper),
            'basis': LIMIT_OPEN_BASIS, 'confirmed_limit_price': False}


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _number(value, *, positive=False, nonnegative=False):
    try:
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
                or positive and value <= 0 or nonnegative and value < 0):
            return None
        return value
    except OverflowError:
        return None


def _height(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _text(value, fallback):
    return value[:200] if isinstance(value, str) and value.strip() else fallback


def _events(payload, previous, gaps):
    review = payload.get('previous_review')
    if not isinstance(review, dict) or review.get('date') != previous:
        gaps.append('前日材料日期无法与严格前一交易日对账，事件保持未知。')
        return {}, set()
    cohorts = review.get('event_cohorts')
    cohorts = cohorts if isinstance(cohorts, dict) else {}
    pools, ambiguous = {}, set()
    for kind in ('limit_up', 'limit_break'):
        cohort = cohorts.get(kind)
        if (not isinstance(cohort, dict) or cohort.get('date') != previous
                or cohort.get('state') not in ('observed_sample', 'observed', 'partial')
                or not isinstance(cohort.get('items'), list)):
            pools[kind] = {}
            continue
        rows = cohort['items']
        if len(rows) > 1000:
            gaps.append('前日事件样本超过冻结容量，未采用该事件池。')
            pools[kind] = {}
            continue
        counts = Counter(row.get('instrument_id') for row in rows
                         if isinstance(row, dict) and isinstance(row.get('instrument_id'), str))
        selected = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = row.get('instrument_id')
            if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
                continue
            if counts[symbol] != 1:
                ambiguous.add(symbol)
                continue
            if (row.get('date') != previous or row.get('trade_date', previous) != previous
                    or row.get('thscode', symbol) != symbol):
                continue
            selected[symbol] = row
        pools[kind] = selected
    conflicts = set(pools['limit_up']) & set(pools['limit_break'])
    if ambiguous:
        gaps.append('部分前日事件记录重复，不能唯一核验，未选择其中一条。')
    if conflicts:
        gaps.append('部分候选同时出现在严格前日涨停和炸板样本中，保留事件冲突。')
    events = {symbol: (kind, row) for kind, rows in pools.items() for symbol, row in rows.items()
              if symbol not in conflicts and symbol not in ambiguous}
    return events, conflicts


def _market_background(payload, previous):
    notice = ('仅为采集时已冻结的前日市场广度背景；与个股表现的同日相关性不能证明“被大盘拖累”等因果，'
              '未留存时序或原预期时不补写原因。')
    result = {'date': previous, 'state': 'unknown', 'up': None, 'down': None, 'flat': None,
              'observed': None, 'comparable': None, 'notice': notice}
    review = payload.get('previous_review')
    review = review if isinstance(review, dict) else {}
    if review.get('date') != previous or not _day(previous):
        return result
    values = []
    for value in (review.get('payload'), review.get('background')):
        if (not isinstance(value, dict) or value.get('date') != previous
                or value.get('mode') != 'previous_session_price_facts'):
            continue
        counts = {key: value.get(key) for key in ('up', 'down', 'flat', 'observed', 'comparable')}
        if any(isinstance(counts[key], int) and not isinstance(counts[key], bool) and counts[key] >= 0
               for key in ('up', 'down', 'flat')):
            values.append({key: count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
                           for key, count in counts.items()})
    if values and all(value == values[0] for value in values):
        result.update(values[0], state='observed_background')
    return result


def _statistics(group):
    if group['key'] == 'limit_up_open' and group['state'] == 'unknown':
        return
    values = sorted(item['auction_pct'] for item in group['items'] if item['auction_pct'] is not None)
    count = len(values)
    middle = count // 2
    mean = math.fsum(value / count for value in values) if count else None
    median = (values[middle] if count % 2 else values[middle - 1] / 2 + values[middle] / 2) if count else None
    group.update(count=len(group['items']), valid_count=count, missing_count=len(group['items']) - count,
                 median_auction_pct=median, average_auction_pct=mean)


def build_feedback(payload):
    """No IO: every displayed event, price and name comes from this payload."""
    payload = payload if isinstance(payload, dict) else {}
    day, previous = payload.get('date'), payload.get('previous_date')
    result = {'version': VERSION, 'date': day, 'previous_date': previous, 'scope': SCOPE,
              'groups': [_limit_open_unknown()] + [{'key': key, 'title': title, 'interpretation': text, 'items': []}
                         for key, title, text in SPECS],
              'coverage': {'requested': 0, 'classified': 0, 'unknown': 0}, 'gaps': [],
              'market_background': _market_background(payload, previous)}
    universe = payload.get('universe')
    rows = universe.get('items') if isinstance(universe, dict) else None
    codes = [row.get('instrument_id') if isinstance(row, dict) else None for row in rows] if isinstance(rows, list) else []
    if (not 1 <= len(codes) <= 100 or len(set(code for code in codes if isinstance(code, str))) != len(codes)
            or any(not isinstance(code, str) or not SYMBOL.fullmatch(code) for code in codes)):
        result['gaps'].append('未取得1至100只身份唯一的原冻结观察名单，未扩展或重建样本。')
        for group in result['groups']:
            _statistics(group)
        return result
    quotes = payload.get('quotes')
    quotes = quotes.get('items') if isinstance(quotes, dict) else None
    quotes = quotes if isinstance(quotes, list) else []
    counts = Counter(row.get('instrument_id') for row in quotes
                     if isinstance(row, dict) and isinstance(row.get('instrument_id'), str))
    indexed = {row['instrument_id']: row for row in quotes if isinstance(row, dict)
               and isinstance(row.get('instrument_id'), str) and counts[row['instrument_id']] == 1
               and row['instrument_id'] in codes}
    valid_previous = _day(day) and _day(previous) and previous < day
    if valid_previous:
        events, conflicts = _events(payload, previous, result['gaps'])
    else:
        events, conflicts = {}, set()
        result['gaps'].append('竞价与严格前一交易日无法核验，前日事件保持未知。')
        result['market_background'] = _market_background({}, previous)
    groups = {group['key']: group for group in result['groups']}
    for symbol, candidate in zip(codes, rows):
        quote = indexed.get(symbol, {})
        opening = _opening_reference(quote, symbol)
        if opening is not None:
            groups['limit_up_open'].update(state='reference', reason=None)
            groups['limit_up_open']['checked_count'] += 1
        change = _number(quote.get('auction_pct')) if quote.get('state') == 'observed' else None
        price = _number(quote.get('auction_price'), positive=True) if change is not None else None
        if price is None:
            change = None
        event = events.get(symbol)
        kind, prior = event if event else ('unknown', {})
        if symbol in conflicts:
            key, kind = 'event_conflict', 'conflict'
        elif change is None:
            key = 'unknown_quote'
        elif event is None:
            key = 'unknown_event'
        elif change == 0:
            key = 'flat_' + kind
        elif kind == 'limit_break':
            key = 'repair' if change > 0 else 'weak_continuation'
        else:
            key = 'continuation' if change > 0 else 'weaken'
        if key in ('repair', 'continuation') and opening is not None and opening['matched']:
            key = 'limit_up_open'
        groups[key]['items'].append({'instrument_id': symbol,
            'name': _text(quote.get('name') or candidate.get('name') or prior.get('name'), symbol),
            'previous_state': kind, 'previous_board_count': _height(prior.get('previous_board_count')) if kind == 'limit_up' else None,
            'auction_price': price, 'auction_pct': change,
            'auction_amount': _number(quote.get('auction_amount'), nonnegative=True) if change is not None else None,
            'auction_volume_ratio': _number(quote.get('auction_volume_ratio'), nonnegative=True) if change is not None else None,
            **({'opening_reference': opening} if key == 'limit_up_open' else {})})
    for group in result['groups']:
        _statistics(group)
    unknown = sum(groups[key]['count'] for key in ('event_conflict', 'unknown_quote', 'unknown_event'))
    result['coverage'].update(requested=len(codes), classified=len(codes) - unknown, unknown=unknown)
    if groups['unknown_event']['count']:
        result['gaps'].append('部分候选没有本份快照可核验的严格前日事件；未将其视为没有发生事件。')
    if groups['unknown_quote']['count']:
        result['gaps'].append('部分候选本次竞价报价未知，不按零涨幅或平开归组。')
    return result


def read_view(day):
    if not _day(day):
        raise HTTPException(422, '竞价对照日期须为YYYY-MM-DD')
    with connect() as connection:
        row = connection.execute('SELECT * FROM market_auction_snapshot WHERE trade_date=?', (day,)).fetchone()
        from app.services.auction_retention import retention_status
        retention = retention_status(connection, day)
    if row is None:
        if retention['purged']:
            result = build_feedback({'date': day})
            result.update(state='purged', retention=retention, reason='竞价已超过28自然日保留窗口，原正文及内嵌原件已清理。')
            result['gaps'] = ['该日竞价已到期清理，不重建昨日与今日强弱对照。']
            return result
        return build_feedback({'date': day})
    try:
        payload = validate_snapshot(dict(row))
    except ValueError:
        raise HTTPException(409, '原竞价正文或来源原件未通过SHA核验，未派生前日事件对照') from None
    return build_feedback(payload)
