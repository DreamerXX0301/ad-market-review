"""Read source constituents from the selected market day's retained reference.

This is deliberately separate from the editable current research topic library.
An Eastmoney industry may resolve to a uniquely named local Fuyao industry, but
that proxy never claims to be Eastmoney's own constituent definition.
"""
from __future__ import annotations

import math
import re

from fastapi import HTTPException

from app.core import json_clean
from app.domain.universe import identity_flags, BOARDS
from app.services.market_history import resolve_reference
from app.services.topic_member_metrics import member_metrics
from app.services.member_turnover import turnover_index, turnover_for
from app.services.stock_board_presentation import decorate_board_collections


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _validate_lookup(topic_id, source_code, kind, source_name):
    for value in (topic_id, source_code, source_name):
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
            raise HTTPException(422, '分类标识或名称需要1至200字')
    if kind not in (None, 'industry', 'concept'):
        raise HTTPException(422, '成员分类仅支持行业或概念')
    if not topic_id and not source_code:
        raise HTTPException(422, '需要topic_id或source_code，不能只按名称猜测分类')


def resolve_topic(topics, *, topic_id=None, source_code=None, kind=None, source_name=None):
    """Resolve an explicit source identity or a disclosed unique-name proxy."""
    candidates = [topic for topic in topics if isinstance(topic, dict) and (kind is None or topic.get('kind', 'concept') == kind)]
    if topic_id:
        selected = [topic for topic in candidates if topic.get('topic_id') == topic_id]
        match_type = 'source_topic_id'
    else:
        selected = [topic for topic in candidates if source_code and source_code in (topic.get('topic_code'), topic.get('source_code'), topic.get('code'))]
        match_type = 'source_code'
        if not selected and source_name and kind == 'industry':
            selected = [topic for topic in candidates if str(topic.get('name', '')).strip() == source_name.strip()]
            match_type = 'cross_provider_name_proxy'
    if len(selected) == 1:
        return selected[0], match_type, None
    reason = '该盘面日没有此分类的成员快照，不能用当前成员替代' if not selected else '该盘面日存在多个同名或同标识分类，无法唯一确定成员口径'
    return None, 'unavailable', reason


def membership_link(row, topics):
    """Keep fund-series IDs intact while attaching a dated member lookup."""
    code = row.get('topic_code')
    if isinstance(code, str) and code.startswith('BK'):
        lookup = {'source_code': code, 'source_name': row.get('name'), 'kind': row.get('kind', 'industry')}
    else:
        lookup = {'topic_id': row.get('topic_id'), 'kind': row.get('kind', 'concept')}
    topic, match_type, reason = resolve_topic(topics, **lookup)
    return {'member_lookup': lookup, 'membership_topic_id': topic.get('topic_id') if topic else None,
            'membership_match_type': match_type, 'membership_unavailable_reason': reason,
            'membership_basis': '本地同名行业（同花顺口径），不是东财行业精确成分' if match_type == 'cross_provider_name_proxy' else '选择日已留存的来源成员快照'}


def list_members(market, status, day, reference, rows, *, topic_id=None, source_code=None, kind=None, source_name=None):
    _validate_lookup(topic_id, source_code, kind, source_name)
    local, coverage = resolve_reference('sectors', day, status, reference)
    topic, match_type, unavailable = resolve_topic(local.get('topics', []), topic_id=topic_id, source_code=source_code, kind=kind, source_name=source_name)
    base = {'date': day, 'snapshot_id': status['snapshot_id'], 'items': [], 'total': 0,
            'topic': None, 'resolved_topic_id': None, 'match_type': match_type,
            'membership_source': None, 'membership_observed_date': None,
            'membership_snapshot_day': coverage.get('source_snapshot_day'),
            'source_snapshot_id': coverage.get('source_snapshot_id'),
            'members_complete': False, 'data_coverage': coverage,
            'membership_basis': '选择日已留存、当日所知的来源成员；不合并当前研究题材库人工修改',
            'sort_by': 'change_pct', 'sort_order': 'desc',
            'quote_coverage': {'known': 0, 'unknown': 0, 'total': 0, 'complete': False},
            'warnings': [coverage['notice']], 'unavailable_reason': unavailable}
    if topic is None:
        return json_clean(base)
    proxy = match_type == 'cross_provider_name_proxy'
    basis = ('本地同名行业（同花顺口径），不是东财行业精确成分；仅使用选择日已有存档' if proxy else base['membership_basis'])
    raw_members = topic.get('instrument_ids', [])
    valid = [value for value in raw_members if isinstance(value, str) and re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', value)]
    members = sorted(set(valid))
    invalid = len(raw_members) - len(valid)
    quotes = {}; duplicates = set()
    for row in rows:
        if str(row.get('date', row.get('trade_date', '')))[:10] != day:
            continue
        symbol = row.get('instrument_id')
        if symbol in quotes:
            duplicates.add(symbol)
        quotes[symbol] = row
    master = {row['instrument_id']: row for row in market.instruments(limit=10000) if row.get('instrument_id')}
    supplement = reference.get('_supplement', {})
    turnover = turnover_index(reference, supplement, day)
    valid_quotes = {symbol: row for symbol, row in quotes.items() if symbol not in duplicates and _number(row.get('close')) is not None and row['close'] > 0}
    historical = member_metrics(market, day, members, master, valid_quotes, supplement)
    quoted_members = [valid_quotes[symbol] for symbol in members if symbol in valid_quotes]
    board_views = {}
    if quoted_members and callable(getattr(market, 'history_for_instruments', None)) and callable(getattr(market, 'status', None)):
        decorated = decorate_board_collections(market, status, day, reference, {'members': quoted_members}, master=master)
        board_views = {row['instrument_id']: row['board_presentation'] for row in decorated['members']
                       if 'board_presentation' in row}
    output = []
    for symbol in members:
        candidate = quotes.get(symbol)
        quote = candidate if symbol not in duplicates and candidate and _number(candidate.get('close')) is not None and candidate['close'] > 0 else None
        identity = quote or master.get(symbol, {})
        amount = _number(quote.get('amount')) if quote else None
        own = master.get(symbol, {})
        board = identity_flags(own, day)['board'] if own and own.get('kind', own.get('asset_type', 'stock')) == 'stock' else 'unknown'
        output.append({'instrument_id': symbol, 'code': symbol[:6], 'name': identity.get('name') or symbol,
                       'close': _number(quote.get('close')) if quote else None,
                       'change_pct': _number(quote.get('change_pct', quote.get('pct_change'))) if quote else None,
                       'amount': amount if amount is not None and amount >= 0 else None,
                       **turnover_for(symbol, day, quote, turnover),
                       'board': board, 'board_label': BOARDS.get(board, '未知'),
                       'board_scope': '当前已留存证券代码与交易所分类；不是历史身份档，不用于推断历史涨跌停制度',
                       'board_reason': None if board != 'unknown' else '证券身份或代码分类未能确认，不默认归为主板',
                       'quote_date': day if quote else None, 'quote_available': quote is not None,
                       'quote_reason': None if quote else '该日存在重复报价，无法确认唯一行情' if symbol in duplicates else '该盘面日缺少有效报价，不使用更早或未来报价替代',
                       'price_basis': 'none', **historical.get(symbol, {}),
                       **({'board_presentation': board_views[symbol]} if symbol in board_views else {})})
    output.sort(key=lambda row: (row['change_pct'] is None, -row['change_pct'] if row['change_pct'] is not None else 0, row['instrument_id']))
    known = sum(row['quote_available'] for row in output)
    base.update(topic={'topic_id': topic['topic_id'], 'name': topic['name'], 'kind': topic.get('kind', 'concept'), 'source_code': topic.get('topic_code')},
                resolved_topic_id=topic['topic_id'], membership_source=topic.get('source', 'fuyao'),
                membership_observed_date=topic.get('observed_date'), membership_basis=basis,
                members_complete=topic.get('members_complete') is True and invalid == 0,
                items=output, total=len(output),
                quote_coverage={'known': known, 'unknown': len(output) - known, 'total': len(output), 'complete': known == len(output) and invalid == 0})
    base['metric_coverage'] = {'total': len(output), 'return_ytd_complete': sum(row.get('return_ytd_pct') is not None for row in output),
                               'return_mtd_complete': sum(row.get('return_mtd_pct') is not None for row in output),
                               'limit_up_ytd_complete': sum(row.get('limit_up_ytd') is not None for row in output),
                               'last_limit_up_complete': sum(row.get('last_limit_up_complete') is True for row in output),
                               'turnover_known': sum(row.get('turnover_rate') is not None for row in output),
                               'return_scope': '所选盘面日截止的可核验连续价格端点；本月涨幅以上月最后交易日为基准，非近30日',
                               'turnover_scope': '百分比；仅同日行情或可交叉核验原始收盘的来源事件池，不代表全市场覆盖'}
    if proxy:
        base['warnings'].append('当前展示本地同名行业（同花顺口径），不是东财行业精确成分；资金归属与成员归属来自不同供应商，不能据此认定每只股票的行业资金流。')
    if not base['members_complete']:
        base['warnings'].append('成员快照不完整，仅列已知成员；未出现的股票不能判定为不属于此分类。')
    if invalid:
        base['warnings'].append(f'{invalid}条成员缺少可识别的完整证券代码，已隔离，不能确认完整成员数。')
    if known < len(output):
        base['warnings'].append(f'{len(output) - known}只成员缺少该盘面日有效报价；未知行情排在末尾。')
    return json_clean(base)
