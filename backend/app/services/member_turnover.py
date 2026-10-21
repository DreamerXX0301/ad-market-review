"""Same-day turnover percentages from retained, price-checked event pools."""
from __future__ import annotations

import math


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def turnover_index(reference, supplement, day):
    """Index the small saved pools once; never fetch or use a newer snapshot."""
    result = {}
    public = supplement.get('public_pools', {}).get(day, {})
    pools = []
    if public.get('date') == day:
        pools.append((public.get('items', []), 'turnover_rate', 'close', public.get('source') or 'eastmoney-public', 'public_pools.' + day, public.get('fetched_at'), True))
    historical = reference.get('pool_history', {}).get(day, {})
    for kind in ('limitup', 'limitdown', 'breakpool'):
        if reference.get('pool_trade_date') == day and isinstance(reference.get(kind), list):
            pools.append((reference[kind], 'turnover_ratio_pct', 'last_price', 'fuyao', 'reference.' + kind, reference.get('pool_fetched_at'), False))
        if isinstance(historical.get(kind), list):
            pools.append((historical[kind], 'turnover_ratio_pct', 'last_price', 'fuyao', 'reference.pool_history.' + day + '.' + kind, historical.get('fetched_at'), False))
    for rows, field, price_field, source, path, fetched, explicit_date in pools:
        for row in rows:
            if not isinstance(row, dict) or (row.get('date') != day if explicit_date else row.get('date') not in (None, day)):
                continue
            symbol = row.get('instrument_id') or row.get('thscode')
            ratio = _number(row.get(field)); price = _number(row.get(price_field))
            if not symbol or ratio is None or ratio < 0 or price is None or price <= 0:
                continue
            result.setdefault(symbol, []).append({'value': ratio, 'close': price, 'source': source, 'date': day, 'path': path, 'fetched_at': fetched, 'unit': '%'})
    return result


def turnover_for(symbol, day, quote, indexed):
    """A verified native ratio wins; otherwise require a same-day raw close."""
    unknown = {'turnover_rate': None, 'turnover_rate_source': None, 'turnover_rate_date': None,
               'turnover_rate_unit': '%', 'turnover_rate_reason': '日线包未提供换手率；该股票未命中同日、可核验原始收盘的换手率补充，不能用成交额或成交量替代',
               'turnover_rate_evidence': None}
    if not quote or str(quote.get('date', quote.get('trade_date', '')))[:10] != day:
        return {**unknown, 'turnover_rate_reason': '缺少该盘面日有效报价，无法交叉核验换手率来源'}
    close = _number(quote.get('close'))
    if close is None or close <= 0:
        return unknown
    proofs = [item for item in indexed.get(symbol, []) if item['date'] == day and abs(item['close'] - close) <= .011]
    native = _number(quote.get('turnover_rate'))
    if native is None: native = _number(quote.get('turnover_ratio_pct'))
    if native is not None and native >= 0:
        matched = next((item for item in proofs if abs(item['value'] - native) <= 1e-9), None)
        return {'turnover_rate': native, 'turnover_rate_source': matched['source'] if matched else quote.get('turnover_rate_source') or '已发布同日行情',
                'turnover_rate_date': day, 'turnover_rate_unit': '%', 'turnover_rate_reason': None,
                'turnover_rate_evidence': matched or {'date': day, 'source': '已发布同日行情', 'unit': '%'}}
    if not proofs:
        return unknown
    # Do not average suppliers with different floating-share definitions.
    public = [item for item in proofs if item['source'] == 'eastmoney-public']
    selected = public or proofs
    if any(abs(item['value'] - selected[0]['value']) > 1e-9 for item in selected):
        return {**unknown, 'turnover_rate_reason': '同日同来源换手率记录冲突，不能确认唯一比率'}
    item = selected[0]
    return {'turnover_rate': item['value'], 'turnover_rate_source': item['source'],
            'turnover_rate_date': day, 'turnover_rate_unit': '%', 'turnover_rate_reason': None,
            'turnover_rate_evidence': {**item, 'price_check': '同日未复权收盘价再次交叉核验'}}
