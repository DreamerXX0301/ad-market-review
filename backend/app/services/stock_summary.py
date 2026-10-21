"""One dated, offline stock statistics contract shared by detail surfaces.

Membership callers already batch-compute the statistics. Their enrichment below
only adds identity and compatible labels; it never recalculates per stock.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import date
import re
from threading import RLock

from app.core import get_market
from app.domain.universe import BOARDS, identity_flags
from app.services import topic_member_metrics
from app.services.market_supplement import load_supplement
from app.services.workspace_common import fail

MAX_SUMMARIES = 128
_CACHE = OrderedDict()
_LOCK = RLock()
STOCK_KINDS = ('stock', 'equity', 'a_share', 'A')


def summary_fields(item, identity=None, day=None):
    """Pure aliases and identity, preserving exact vs observed/unknown counts."""
    value = dict(item)
    known_identity = identity and identity.get('kind', identity.get('asset_type', 'stock')) in STOCK_KINDS
    board = identity_flags(identity, day)['board'] if known_identity else value.get('board', 'unknown')
    if board not in BOARDS: board = 'unknown'
    value.update(board=board, board_label=BOARDS.get(board, '未知'),
        board_scope='当前留存证券代码与交易所分类；不是历史身份档，不用于反推历史涨跌停制度',
        board_reason=None if board != 'unknown' else '没有可确认的证券身份，不默认归为主板')
    aliases = {'annual_return_pct': 'return_ytd_pct', 'month_return_pct': 'return_mtd_pct',
        'return_30d_pct': 'change_30d_pct', 'annual_limit_up_count': 'limit_up_ytd',
        'annual_limit_up_count_observed': 'limit_up_ytd_observed',
        'latest_limit_up_date': 'last_limit_up_date', 'latest_limit_up_complete': 'last_limit_up_complete',
        'sessions_since_latest_limit_up': 'sessions_since_limit_up'}
    for target, source in aliases.items(): value[target] = value.get(source)
    for target, source in (('annual_return', 'return_ytd'), ('month_return', 'return_mtd'),
                           ('return_30d', 'change_30d'), ('annual_limit_up_count', 'limit_up_ytd'),
                           ('latest_limit_up', 'last_limit_up')):
        for suffix in ('_reason', '_coverage'):
            value[target+suffix] = value.get(source+suffix)
    return value


def enrich_members(items, master, day):
    """One metadata mapping, no quote/history/source I/O or N+1 statistics."""
    return [summary_fields(item, master.get(item.get('instrument_id')), day) for item in items]


def _master(market):
    return {str(item.get('instrument_id') or item.get('thscode') or '').upper(): item
            for item in market.instruments(limit=10000)
            if item.get('kind', item.get('asset_type', 'stock')) in STOCK_KINDS}


def _day(market, requested):
    status = market.status(); calendar = market.calendar()
    if not status.get('ready'): fail('请先初始化已发布真实行情', 409)
    day = requested or status.get('data_end')
    try: canonical = date.fromisoformat(day).isoformat()
    except (ValueError, TypeError): fail('观察日需要YYYY-MM-DD格式')
    begin = status.get('data_begin') or (calendar[0] if calendar else None)
    end = status.get('data_end')
    if canonical != day or day not in calendar or not begin or not end or not begin <= day <= end:
        fail('观察日必须在已发布行情的完成市场交易日范围内')
    return day, status


def _symbol(identifier, master):
    if not isinstance(identifier, str): fail('请选择明确股票代码')
    symbol = identifier.strip().upper()
    if re.fullmatch(r'\d{6}', symbol):
        candidates = [key for key, identity in master.items() if identity.get('code', key[:6]) == symbol]
        if len(candidates) != 1: fail('股票代码缺失或身份不唯一', 404)
        symbol = candidates[0]
    if symbol not in master: fail('该标的不在已留存个股范围；指数与板块不使用个股统计', 404)
    return symbol


def summary_for(market, identifier, as_of=None):
    """Bounded batch reads only; no market.panel/chart or provider request."""
    day, status = _day(market, as_of)
    master = _master(market); symbol = _symbol(identifier, master)
    method = getattr(market, 'history_for_instruments', None)
    selected = method([symbol], start=day, end=day).get(symbol, []) if callable(method) else []
    exact = [row for row in selected if row.get('date') == day]
    quote = exact[0] if len(exact) == 1 and topic_member_metrics._number(exact[0].get('close')) is not None and exact[0]['close'] > 0 else None
    quotes = {symbol: quote} if quote is not None else {}
    supplement = load_supplement(status.get('snapshot_id')) if status.get('snapshot_id') else {}
    # Reuse the verified generation/candidate/quote/source-digest key. Fake or
    # mutable market objects have no reliable key and must never reuse results.
    key = topic_member_metrics._cache_key(market, day, [symbol], quotes, supplement)
    with _LOCK:
        cached = _CACHE.get(key) if key is not None else None
        if cached is not None: _CACHE.move_to_end(key)
    if cached is not None: return deepcopy(cached)
    statistics = topic_member_metrics.member_metrics(market, day, [symbol], master, quotes, supplement).get(symbol, {})
    value = summary_fields({**statistics, 'instrument_id': symbol, 'code': symbol[:6],
        'name': master[symbol].get('name') or symbol, 'data_date': day, 'as_of': day,
        'snapshot_id': status.get('snapshot_id'), 'quote_date': day if quote is not None else None,
        'quote_available': quote is not None,
        'quote_reason': None if quote is not None else '观察日缺少唯一有效收盘报价，不以旧报价代替',
        'close': quote['close'] if quote is not None else None,
        'return_scope': '年内以上年末已核验交易日收盘为基准；近30日按精确T/T-30市场交易日，公司行动需核验连续价格；本月单独以上月末为基准。',
        'limit_scope': '只统计观察日及之前逐股原始收盘核验的来源涨停事件；不完整次数保持未知，已确认次数仅为下界。'},
        master[symbol], day)
    if key is not None:
        with _LOCK:
            _CACHE[key] = deepcopy(value); _CACHE.move_to_end(key)
            while len(_CACHE) > MAX_SUMMARIES: _CACHE.popitem(last=False)
    return value


def get_summary(identifier, as_of=None):
    market = get_market(); snapshot = market.status().get('snapshot_id')
    result = summary_for(market, identifier, as_of)
    if get_market().status().get('snapshot_id') != snapshot:
        fail('行情代际已变化，请重新打开个股统计', 409)
    return result
