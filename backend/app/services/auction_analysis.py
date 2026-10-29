"""Pure auction validation: only verified final state pairs become observations.

The provider timestamp describes response assembly, never an individual quote.
Prices are unadjusted yuan, auction volume is lots of 100 shares, amounts yuan,
and percentages are percentage values (1.2 means 1.2%, not 120%).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
import math
import re
from statistics import median
from zoneinfo import ZoneInfo

CHINA = ZoneInfo('Asia/Shanghai')
CODE = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)\Z')
FIELDS = ('auction_price', 'auction_pct', 'auction_amount', 'auction_volume',
          'auction_turnover_pct', 'auction_yesterday_ratio_pct', 'auction_volume_ratio',
          'pre_close_price')
SCOPE = '仅为本次冻结候选样本，不代表全市场；缺报不认定为停牌。'
# Both pairs have independent source-shape evidence. Keep the original phase
# and status rather than broadening either value into a standalone whitelist.
FINAL_SOURCE_STATES = (('final', 'ready'), ('matched', 'final'))


def number(value, *, positive=False, nonnegative=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        if not math.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
            return None
    except OverflowError:
        return None
    return float(value)


def _text(value, fallback='', limit=200):
    return value[:limit] if isinstance(value, str) and value.strip() else fallback


def _date(value):
    try:
        return value if isinstance(value, str) and date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _timestamp(value, day, received_at):
    result = {'value_ms': number(value, positive=True), 'time': None,
              'state': 'unknown', 'meaning': 'response_assembly_time'}
    if result['value_ms'] is None:
        return result
    try:
        stamp = datetime.fromtimestamp(value / 1000, CHINA)
        received = datetime.fromisoformat(received_at) if isinstance(received_at, str) else received_at
        if not isinstance(received, datetime) or received.tzinfo is None:
            raise ValueError()
        received = received.astimezone(CHINA)
        result['time'] = stamp.isoformat()
        if stamp.date().isoformat() != day:
            result['state'] = 'different_date'
        elif stamp.timestamp() > received.timestamp() + 5:
            result['state'] = 'future'
        elif not time(9, 25) <= stamp.time().replace(tzinfo=None) < time(9, 30):
            result['state'] = 'outside_window'
        elif received.date().isoformat() != day or not time(9, 25) <= received.time().replace(tzinfo=None) < time(9, 30):
            result['state'] = 'outside_window'
        else:
            result['state'] = 'observed'
    except (ValueError, TypeError, OverflowError, OSError):
        result['state'] = 'invalid'
    return result


def _blank(code, reason, name=None):
    return {'instrument_id': code, 'name': _text(name, code), 'state': 'unknown',
            'reason': reason, **dict.fromkeys(FIELDS), 'quote_at': None,
            'unit_check': {'state': 'unknown', 'reason': '无可核验竞价量额。'}}


def build_summary(rows):
    valid = [row for row in rows if row['state'] == 'observed' and number(row['auction_pct']) is not None]
    changes = [row['auction_pct'] for row in valid]
    amounts = [row['auction_amount'] for row in valid if number(row['auction_amount'], nonnegative=True) is not None]
    try:
        total_amount = math.fsum(amounts) if amounts else None
    except OverflowError:
        total_amount = None
    return {'requested': len(rows), 'valid': len(valid), 'missing': len(rows) - len(valid),
            'up': sum(value > 0 for value in changes) if changes else None,
            'down': sum(value < 0 for value in changes) if changes else None,
            'flat': sum(value == 0 for value in changes) if changes else None,
            'median_pct': median(changes) if changes else None,
            'amount': total_amount, 'amount_valid': len(amounts), 'scope': SCOPE}


def _row(value, code):
    row = _blank(code, '来源报价缺失、无效或与前收盘价不一致。', value.get('name'))
    if value.get('ticker') is not None and value['ticker'] != code[:6]:
        row['reason'] = '来源完整代码与纯代码冲突，未采用该行。'
        return row
    price = number(value.get('auction_price'), positive=True)
    previous = number(value.get('pre_close_price'), positive=True)
    change = number(value.get('auction_pct'))
    if price is None or previous is None or change is None:
        return row
    expected = (price / previous - 1) * 100
    if not math.isfinite(expected) or abs(expected - change) > .06:
        return row
    row.update(state='observed', reason=None, auction_price=price,
               pre_close_price=previous, auction_pct=change)
    for key in ('auction_turnover_pct', 'auction_yesterday_ratio_pct', 'auction_volume_ratio'):
        row[key] = number(value.get(key), nonnegative=True)
    volume = number(value.get('auction_volume'), nonnegative=True)
    amount = number(value.get('auction_amount'), nonnegative=True)
    if volume is None or amount is None:
        row['unit_check'] = {'state': 'unknown', 'reason': '来源量或额缺失，未计算成交强弱。'}
    else:
        expected_amount = price * volume * 100
        # Provider amounts/volumes may be rounded. Reject a share/lot mix-up
        # without fabricating a conversion for an inconsistent response.
        if math.isfinite(expected_amount) and abs(expected_amount - amount) <= max(1, abs(expected_amount) * .01):
            row.update(auction_volume=volume, auction_amount=amount,
                       unit_check={'state': 'verified', 'reason': None})
        else:
            row['unit_check'] = {'state': 'mismatch', 'reason': '量额未通过价格×成交量(手)×100的1%容差核验，相关数值保留未知。'}
    return row


def normalize_snapshot(body, instrument_ids, day, received_at):
    """Preserve every requested identity, including missing and rejected rows."""
    if not _date(day) or not isinstance(instrument_ids, list) or not 1 <= len(instrument_ids) <= 100:
        raise ValueError('竞价规范化需要有效日期及1至100个明确股票代码')
    if any(not isinstance(code, str) or not CODE.fullmatch(code) for code in instrument_ids) or len(set(instrument_ids)) != len(instrument_ids):
        raise ValueError('竞价候选代码无效或重复')
    data = body.get('data') if isinstance(body, dict) and body.get('code') == 0 else None
    data = data if isinstance(data, dict) else {}
    timestamp = _timestamp(data.get('timestamp'), day, received_at)
    result = {'state': 'unknown', 'items': [], 'summary': {}, 'gaps': [],
              'source_timestamp': timestamp, 'auction_phase': _text(data.get('auction_phase'), 'unknown', 40),
              'data_status': _text(data.get('data_status'), 'unknown', 40),
              'units': {'auction_price': 'CNY', 'auction_amount': 'CNY', 'auction_volume': 'lot_100_shares',
                        'auction_pct': 'percent', 'auction_turnover_pct': 'percent',
                        'auction_yesterday_ratio_pct': 'percent', 'auction_volume_ratio': 'ratio'},
              'quote_as_of': None}
    reason = None
    if (data.get('auction_phase'), data.get('data_status')) not in FINAL_SOURCE_STATES:
        reason = '来源状态组合尚非已核验终态；即使字段有数值也不能当作本日有效竞价。'
    elif timestamp['state'] != 'observed':
        reason = '来源响应组装日期或时段未通过核验，未据此认定逐票行情新鲜。'
    elif not isinstance(data.get('item'), list) or len(data['item']) > 100:
        reason = '来源明细结构或数量超出本次最多100只的范围。'
    if reason:
        result['gaps'].append(reason)
        result['items'] = [_blank(code, reason) for code in instrument_ids]
    else:
        values = data['item']
        counts = Counter(value.get('thscode') for value in values
                         if isinstance(value, dict) and isinstance(value.get('thscode'), str))
        requested = set(instrument_ids)
        indexed = {value['thscode']: value for value in values if isinstance(value, dict)
                   and isinstance(value.get('thscode'), str) and value['thscode'] in requested
                   and counts[value['thscode']] == 1}
        if any(not isinstance(value, dict) or not isinstance(value.get('thscode'), str)
               or value['thscode'] not in requested for value in values):
            result['gaps'].append('来源含未请求或身份无效的记录，已隔离。')
        declared = data.get('total')
        if not isinstance(declared, int) or isinstance(declared, bool) or declared != len(values):
            result['gaps'].append('来源声明数量与实际明细不能对账，覆盖仅按已核验候选统计。')
        for code in instrument_ids:
            if counts[code] > 1:
                result['items'].append(_blank(code, '来源同一证券重复返回，未选择其中一条。'))
            elif code not in indexed:
                result['items'].append(_blank(code, '本次来源未返回该候选，缺报不认定为停牌。'))
            else:
                result['items'].append(_row(indexed[code], code))
        if any(row['state'] != 'observed' for row in result['items']):
            result['gaps'].append('部分候选报价缺失、重复或价幅不一致，未补零。')
        if any(row['state'] == 'observed' and row['unit_check']['state'] != 'verified' for row in result['items']):
            result['gaps'].append('部分竞价量额缺失或单位对账未通过，样本成交额只累加可核验部分。')
        if any(row['state'] == 'observed' and any(row[key] is None for key in
               ('auction_turnover_pct', 'auction_yesterday_ratio_pct', 'auction_volume_ratio')) for row in result['items']):
            result['gaps'].append('部分换手、昨日量占比或来源量比缺失，保留未知。')
    result['summary'] = build_summary(result['items'])
    if result['summary']['valid']:
        result['state'] = 'partial' if result['gaps'] else 'observed'
    return result


def normalize_benchmark(body, day):
    """The provider's short-term cohort is separate from our frozen sample."""
    result = {'state': 'unknown', 'date': day, 'items': [],
              'reason': '来源未提供可核验的同日短线基准；不回退到更早一天。',
              'scope': '来源短线风向标样本及原始标签，不是全市场基准或买卖信号。'}
    if not _date(day):
        return result
    data = body.get('data') if isinstance(body, dict) and body.get('code') == 0 else None
    if not isinstance(data, dict) or data.get('date') != day:
        return result
    midnight = int(datetime.combine(date.fromisoformat(day), time(), CHINA).timestamp() * 1000)
    if isinstance(data.get('date_ms'), bool) or data.get('date_ms') != midnight:
        return result
    values = data.get('item')
    if not isinstance(values, list) or not values or len(values) > 1000:
        return result
    counts = Counter(row.get('thscode') for row in values if isinstance(row, dict) and isinstance(row.get('thscode'), str))
    for value in values:
        if not isinstance(value, dict):
            continue
        code = value.get('thscode')
        if not isinstance(code, str) or not CODE.fullmatch(code) or counts[code] != 1:
            continue
        if value.get('ticker') is not None and value['ticker'] != code[:6]:
            continue
        change = number(value.get('auction_pct'))
        if change is None:
            continue
        tags = value.get('tags')
        result['items'].append({'instrument_id': code, 'name': _text(value.get('name'), code),
                                'auction_pct': change,
                                'tags': [_text(tag, limit=120) for tag in tags[:10] if isinstance(tag, str) and tag.strip()] if isinstance(tags, list) else []})
    if result['items']:
        complete = len(result['items']) == len(values)
        result.update(state='observed' if complete else 'partial',
                      reason=None if complete else '部分基准记录身份、涨幅或重复核验未通过。')
    return result


def build_analysis(quotes, universe=None):
    summary = quotes['summary']
    if not summary['valid']:
        return ['本次没有可核验终态报价，不生成竞价强弱判断。']
    lines = [f"本次冻结{summary['requested']}只候选，有效{summary['valid']}只、未知{summary['missing']}只。",
             f"有效样本高开{summary['up']}只、低开{summary['down']}只、平开{summary['flat']}只，中位竞价涨幅{summary['median_pct']:+.2f}%。"]
    if universe and universe.get('omitted'):
        lines.append(f"候选超出100只上限，另有{universe['omitted']}只未纳入，不能推断其竞价表现。")
    lines.append('高开与来源量比只描述本次样本，仍需开盘后核对承接；不自动选股、跟踪或下单。')
    return lines
