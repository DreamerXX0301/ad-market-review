"""Post-close checks against the exact frozen auction cohort, with no fetching.

This separate read may access published immutable daily Parquet through
MarketData. It never opens the worker's active DuckDB and never rewrites the
pre-open snapshot. Price differences are observations, not executable returns.
"""
from __future__ import annotations

from datetime import date, datetime, time
import math
from statistics import median

from fastapi import HTTPException

from app.core import connect, get_market
from app.services.auction_analysis import CHINA, CODE, number

VERSION = 'auction-close-review-v1'
NOTICE = ('盘后核对使用原竞价冻结名单及同日已发布未复权报价，属于事后信息；'
          '价差不代表能以竞价价成交，也未计费用、滑点或交易限制，不是可实现收益或策略回测。')


def now_china():
    return datetime.now(CHINA)


def _average(values):
    values = [value for value in values if number(value) is not None]
    try:
        return number(math.fsum(value / len(values) for value in values)) if values else None
    except OverflowError:
        return None


def _base_item(row, reason):
    return {'instrument_id': row['instrument_id'], 'name': row.get('name') or row['instrument_id'],
            'state': 'unknown', 'reason': reason,
            'auction_price': row.get('auction_price'), 'auction_pct': row.get('auction_pct'),
            'pre_close_price': number(row.get('pre_close_price'), positive=True),
            'open': None, 'close': None, 'high': None, 'low': None,
            'auction_to_close_pct': None, 'open_check': 'unknown', 'close_pct': None,
            'close_pct_reason': '等待该日可比收盘报价。'}


def _close_pct(row):
    previous = row['pre_close_price']
    if previous is None:
        row.update(close_pct=None, close_pct_reason='原冻结竞价来源前收缺失或无效，不按竞价涨幅反推前收。')
    elif row['state'] != 'observed':
        row.update(close_pct=None, close_pct_reason=row['reason'] or '同日收盘报价未通过可比性核验，不纳入收盘涨幅统计。')
    else:
        value = number((row['close'] / previous - 1) * 100)
        row.update(close_pct=value, close_pct_reason=None if value is not None else '收盘价与冻结前收无法得到有限涨幅，保持未知。')
    return row


def _attach_groups(result, feedback, rows, quotes):
    """仅把同一批收盘行附在原盘前组；收盘结果不参与分组或排序。"""
    by_code = {row['instrument_id']: row for row in rows}
    assigned = set(); groups = []
    for original in feedback.get('groups', []):
        items = []
        auction_changes = []
        for member in original.get('items', []):
            code = member.get('instrument_id')
            if code not in by_code or code in assigned:
                continue
            assigned.add(code)
            items.append({**by_code[code], 'previous_state': member.get('previous_state', 'unknown'),
                          'previous_board_count': member.get('previous_board_count'),
                          'auction_price': number(member.get('auction_price'), positive=True),
                          'auction_pct': number(member.get('auction_pct')),
                          'auction_state': 'observed' if number(member.get('auction_pct')) is not None else 'unknown',
                          **({'opening_reference': dict(member['opening_reference'])}
                             if isinstance(member.get('opening_reference'), dict) else {})})
            auction_changes.append(member.get('auction_pct'))
        group = {key: original[key] for key in ('key', 'title', 'interpretation', 'state', 'reason',
                 'basis', 'confirmed_limit_price', 'checked_count') if key in original}
        group.update(items=items, count=len(items), average_auction_pct=_average(auction_changes))
        if group.get('key') == 'limit_up_open' and original.get('state') == 'unknown':
            group['count'] = None
        groups.append(group)
    remaining = [row for row in rows if row['instrument_id'] not in assigned]
    if remaining:
        items = []
        for row in remaining:
            quote = quotes[row['instrument_id']]
            change = (number(quote.get('auction_pct')) if quote.get('state') == 'observed'
                      and number(quote.get('auction_price'), positive=True) is not None else None)
            items.append({**row, 'previous_state': 'unknown', 'previous_board_count': None,
                          'auction_pct': change, 'auction_state': 'observed' if change is not None else 'unknown'})
        groups.append({'key': 'unclassified', 'title': '原盘前分组依据缺失',
            'interpretation': '原冻结分组日期、名单或事件依据无法完整对账；保留原报价名单，不用收盘结果重分强弱。',
            'reason': '缺少可核验原盘前分组依据，仅保留同日价格核对。',
            'count': len(remaining), 'average_auction_pct': _average([row['auction_pct'] for row in items]), 'items': items})
    for group in groups:
        items = group['items']
        comparable = [row for row in items if row['state'] == 'observed']
        closes = [row['close_pct'] for row in comparable if row['close_pct'] is not None]
        group.update(valid_count=len(comparable), missing_count=len(items) - len(comparable),
                     open_mismatch=sum(row['open_check'] == 'mismatch' for row in items),
                     close_valid_count=len(closes), close_missing_count=len(items) - len(closes),
                     average_close_pct=_average(closes),
                     average_auction_to_close_pct=_average([row['auction_to_close_pct'] for row in comparable]))
    known = {'limit_up_open', 'repair', 'weaken', 'continuation', 'weak_continuation',
             'flat_limit_up', 'flat_limit_break'}
    groups.sort(key=lambda group: (group['key'] != 'limit_up_open', group['key'] not in known,
                                  group['average_auction_pct'] is None if group['key'] in known else False,
                                  -(group['average_auction_pct'] or 0) if group['key'] in known else 0))
    result['groups'] = groups
    return result


def build_close_review(payload, market, *, at=None):
    day = payload['date']
    rows = (payload.get('quotes') or {}).get('items') or []
    result = {'version': VERSION, 'date': day, 'state': 'pending', 'items': [],
              'reason': '等待该日收盘后手动盘后增量发布真实行情。',
              'summary': None, 'groups': [], 'market_snapshot_id': None, 'source_sha256': None,
              'price_basis': 'none', 'price_unit': 'CNY', 'posthoc': True, 'notice': NOTICE}
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 100
            or any(not isinstance(row, dict) or not isinstance(row.get('instrument_id'), str)
                   or not CODE.fullmatch(row['instrument_id']) for row in rows)
            or len({row['instrument_id'] for row in rows}) != len(rows)):
        result.update(state='unknown', reason='原竞价冻结名单身份或数量异常，停止盘后核对。')
        return result
    from app.services.auction_feedback import build_feedback
    try:
        feedback = build_feedback(payload)
    except (TypeError, ValueError, OverflowError):
        feedback = {'groups': []}
    quotes = {row['instrument_id']: row for row in rows}
    pending = [_close_pct(_base_item(row, result['reason'])) for row in rows]
    grouped = lambda: _attach_groups(result, feedback, pending, quotes)
    current = (at or now_china()).astimezone(CHINA)
    if day > current.date().isoformat() or (day == current.date().isoformat() and current.time().replace(tzinfo=None) < time(15)):
        return grouped()
    status = market.status()
    if not status.get('ready') or not status.get('data_end') or status['data_end'] < day or day not in market.calendar():
        return grouped()
    if status.get('source') != 'fuyao' or status.get('price_basis') != 'none' or status.get('amount_unit') != 'CNY':
        result.update(state='unknown', reason='已发布行情来源、币种或未复权口径未通过核验，不能与竞价价格比较。')
        pending = [_close_pct(_base_item(row, result['reason'])) for row in rows]
        return grouped()
    codes = list(quotes)
    histories = market.history_for_instruments(codes, start=day, end=day)
    manifest = status.get('manifest') or {}
    result.update(market_snapshot_id=status.get('snapshot_id'), source_sha256=manifest.get('source_sha256'))
    for row in rows:
        code = row['instrument_id']
        value = _base_item(row, '未取得该证券唯一、同日、正成交量的未复权收盘报价。')
        daily = histories.get(code) or []
        exact = [bar for bar in daily if isinstance(bar, dict) and bar.get('date') == day
                 and bar.get('instrument_id') == code]
        price = number(row.get('auction_price'), positive=True) if row.get('state') == 'observed' else None
        if price is None:
            value['reason'] = '原竞价报价未知，不用开盘价回填竞价，也不参与价差统计。'
        elif len(exact) == 1:
            bar = exact[0]
            prices = {key: number(bar.get(key), positive=True) for key in ('open', 'high', 'low', 'close')}
            volume = number(bar.get('volume'), positive=True)
            if (bar.get('price_basis') == 'none' and bar.get('source') == 'fuyao'
                    and bar.get('currency', 'CNY') == 'CNY' and all(v is not None for v in prices.values())
                    and volume is not None and prices['low'] <= min(prices['open'], prices['close'])
                    and max(prices['open'], prices['close']) <= prices['high']):
                matches = abs(prices['open'] - price) <= .011
                difference = (prices['close'] / price - 1) * 100
                if number(difference) is not None:
                    value.update(**prices, auction_to_close_pct=difference,
                                 state='observed' if matches else 'mismatch',
                                 open_check='matched' if matches else 'mismatch',
                                 reason=None if matches else '同日开盘价与冻结竞价价偏差超过0.011元，单列核对，不纳入汇总。')
        result['items'].append(_close_pct(value))
    valid = [row for row in result['items'] if row['state'] == 'observed']
    changes = [row['auction_to_close_pct'] for row in valid]
    result['summary'] = {'requested': len(rows), 'valid': len(valid), 'missing': len(rows) - len(valid),
                         'above_auction': sum(value > 0 for value in changes) if changes else None,
                         'below_auction': sum(value < 0 for value in changes) if changes else None,
                         'flat': sum(value == 0 for value in changes) if changes else None,
                         'median_auction_to_close_pct': median(changes) if changes else None,
                         'open_mismatch': sum(row['open_check'] == 'mismatch' for row in result['items']),
                         'average_auction_to_close_pct': _average(changes),
                         'average_close_pct': _average([row['close_pct'] for row in valid]),
                         'close_valid_count': sum(row['close_pct'] is not None for row in valid),
                         'close_missing_count': len(rows) - sum(row['close_pct'] is not None for row in valid)}
    result.update(state='available' if len(valid) == len(rows) else 'partial' if valid else 'unknown',
                  reason=None if len(valid) == len(rows) else '部分同日报价缺失、无成交或竞价与开盘价不一致，未知项不计为0。')
    return _attach_groups(result, feedback, result['items'], quotes)


def read_close_review(day):
    try:
        valid = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, '竞价盘后核对日期须为YYYY-MM-DD形式的真实日期')
    with connect() as connection:
        row = connection.execute('SELECT * FROM market_auction_snapshot WHERE trade_date=?', (day,)).fetchone()
        from app.services.auction_retention import retention_status
        retention = retention_status(connection, day)
    if row is None:
        if retention['purged']:
            return {'version': VERSION, 'date': day, 'state': 'purged', 'items': [], 'groups': [], 'summary': None,
                    'reason': '竞价正文及内嵌原件已按当时的保留规则清理。',
                    'posthoc': True, 'notice': NOTICE, 'retention': retention}
        return {'version': VERSION, 'date': day, 'state': 'not_collected', 'items': [], 'groups': [],
                'summary': None, 'reason': '该日未留存盘前竞价，不使用开盘价倒造盘前记录。', 'posthoc': True, 'notice': NOTICE}
    from app.services.auction_integrity import validate_snapshot
    try:
        payload = validate_snapshot(row)
    except ValueError:
        raise HTTPException(409, '竞价正文或来源原件SHA核验失败，暂停盘后核对，请保留原记录检查存储') from None
    if payload.get('date') != day or payload.get('version') != 'auction-snapshot-v1':
        raise HTTPException(409, '竞价原文日期或版本不一致，暂停盘后核对')
    # Avoid even loading published inputs while the observed day is still open.
    current = now_china()
    if day > current.date().isoformat() or (day == current.date().isoformat() and current.time().replace(tzinfo=None) < time(15)):
        result = build_close_review(payload, None, at=current)
    else:
        result = build_close_review(payload, get_market(), at=current)
    return {**result, 'snapshot_id': row['snapshot_id'], 'auction_content_sha256': row['content_sha256']}
