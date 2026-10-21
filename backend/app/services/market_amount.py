"""Compare retained three-exchange A-share turnover on adjacent sessions."""
from __future__ import annotations

import math


def _amount(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def amount_change_metadata(current_amount, day, calendar, history):
    result = {'amount_change': None, 'amount_previous_date': None,
              'amount_change_reason': None, 'amount_change_pct': None,
              'amount_change_scope': '沪深京已留存A股股票成交额，与前一市场交易日同口径相减'}
    if day not in calendar or calendar.index(day) == 0:
        return {**result, 'amount_change_reason': '尚无前一市场交易日成交额'}
    previous_day = calendar[calendar.index(day) - 1]
    result['amount_previous_date'] = previous_day
    current = [row for row in history if row.get('date') == day]
    previous = [row for row in history if row.get('date') == previous_day]
    if len(current) != 1 or len(previous) != 1:
        return {**result, 'amount_change_reason': '相邻交易日成交额缺失或重复，不回溯其他日期比较'}
    a, b = current[0], previous[0]
    if not _amount(current_amount) or not _amount(a.get('amount')) or not _amount(b.get('amount')):
        return {**result, 'amount_change_reason': '相邻交易日有效成交额不足，不能计算增减'}
    if a.get('scope') != b.get('scope') or a.get('source') != b.get('source') or not math.isclose(current_amount, a['amount'], rel_tol=1e-12, abs_tol=.01):
        return {**result, 'amount_change_reason': '相邻交易日成交额口径不一致，不能计算增减'}
    result['amount_change'] = current_amount - b['amount']
    result['amount_change_pct'] = (current_amount / b['amount'] - 1) * 100 if b['amount'] > 0 else None
    return result
