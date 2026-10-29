"""Post-close checks against the exact frozen auction cohort, with no fetching.

This separate read may access published immutable daily Parquet through
MarketData. It never opens the worker's active DuckDB and never rewrites the
pre-open snapshot. Price differences are observations, not executable returns.
"""
from __future__ import annotations

from datetime import date, datetime, time
from statistics import median

from fastapi import HTTPException

from app.core import connect, get_market
from app.services.auction_analysis import CHINA, CODE, number

VERSION = 'auction-close-review-v1'
NOTICE = ('盘后核对使用原竞价冻结名单及同日已发布未复权报价，属于事后信息；'
          '价差不代表能以竞价价成交，也未计费用、滑点或交易限制，不是可实现收益或策略回测。')


def now_china():
    return datetime.now(CHINA)


def build_close_review(payload, market, *, at=None):
    day = payload['date']
    rows = (payload.get('quotes') or {}).get('items') or []
    result = {'version': VERSION, 'date': day, 'state': 'pending', 'items': [],
              'reason': '等待该日收盘后手动盘后增量发布真实行情。',
              'summary': None, 'market_snapshot_id': None, 'source_sha256': None,
              'price_basis': 'none', 'price_unit': 'CNY', 'posthoc': True, 'notice': NOTICE}
    current = (at or now_china()).astimezone(CHINA)
    if day > current.date().isoformat() or (day == current.date().isoformat() and current.time().replace(tzinfo=None) < time(15)):
        return result
    status = market.status()
    if not status.get('ready') or not status.get('data_end') or status['data_end'] < day or day not in market.calendar():
        return result
    if status.get('source') != 'fuyao' or status.get('price_basis') != 'none' or status.get('amount_unit') != 'CNY':
        result.update(state='unknown', reason='已发布行情来源、币种或未复权口径未通过核验，不能与竞价价格比较。')
        return result
    codes = [row['instrument_id'] for row in rows if isinstance(row, dict)
             and isinstance(row.get('instrument_id'), str) and CODE.fullmatch(row['instrument_id'])]
    if not codes or len(codes) > 100 or len(codes) != len(set(codes)):
        result.update(state='unknown', reason='原竞价冻结名单身份或数量异常，停止盘后核对。')
        return result
    histories = market.history_for_instruments(codes, start=day, end=day)
    manifest = status.get('manifest') or {}
    result.update(market_snapshot_id=status.get('snapshot_id'), source_sha256=manifest.get('source_sha256'))
    for row in rows:
        code = row['instrument_id']
        value = {'instrument_id': code, 'name': row.get('name') or code, 'state': 'unknown',
                 'reason': '未取得该证券唯一、同日、正成交量的未复权收盘报价。',
                 'auction_price': row.get('auction_price'), 'auction_pct': row.get('auction_pct'),
                 'open': None, 'close': None, 'high': None, 'low': None,
                 'auction_to_close_pct': None, 'open_check': 'unknown'}
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
        result['items'].append(value)
    valid = [row for row in result['items'] if row['state'] == 'observed']
    changes = [row['auction_to_close_pct'] for row in valid]
    result['summary'] = {'requested': len(rows), 'valid': len(valid), 'missing': len(rows) - len(valid),
                         'above_auction': sum(value > 0 for value in changes) if changes else None,
                         'below_auction': sum(value < 0 for value in changes) if changes else None,
                         'flat': sum(value == 0 for value in changes) if changes else None,
                         'median_auction_to_close_pct': median(changes) if changes else None,
                         'open_mismatch': sum(row['open_check'] == 'mismatch' for row in result['items'])}
    result.update(state='available' if len(valid) == len(rows) else 'partial' if valid else 'unknown',
                  reason=None if len(valid) == len(rows) else '部分同日报价缺失、无成交或竞价与开盘价不一致，未知项不计为0。')
    return result


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
            return {'version': VERSION, 'date': day, 'state': 'purged', 'items': [], 'summary': None,
                    'reason': '竞价已超过28自然日保留窗口，正文及内嵌原件已清理；不倒造盘前记录。',
                    'posthoc': True, 'notice': NOTICE, 'retention': retention}
        return {'version': VERSION, 'date': day, 'state': 'not_collected', 'items': [],
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
