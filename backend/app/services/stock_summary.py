"""One dated, offline stock statistics contract shared by detail surfaces.

Membership callers already batch-compute the statistics. Their enrichment below
only adds identity and compatible labels; it never recalculates per stock.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import date
import re
from threading import RLock

from app.core import dumps, get_market
from app.domain.conditions import metrics_for
from app.domain.universe import BOARDS, identity_flags
from app.services import topic_member_metrics
from app.services.chart_read_cache import recent_chart_day
from app.services.market_supplement import load_supplement_with_fingerprint
from app.services.workspace_common import fail

MAX_SUMMARIES = 128
MAX_SUMMARY_BYTES = 64 * 1024
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


def _daily_change(market, symbol, day, previous_day, calendar, selected, quote):
    """Use the canonical daily algorithm on the exact two market sessions.

    Raw Parquet has no source daily-return field.  Do not turn an older visible
    quote into yesterday, or treat absent action evidence as no corporate action.
    """
    if quote is None:
        return None, '观察日缺少唯一有效收盘报价，不以旧报价代替'
    if previous_day is None:
        return None, '未留存前一市场交易日，不能核验当日涨幅'
    previous = [row for row in selected if row.get('date') == previous_day]
    if len(previous) != 1:
        return None, '前一市场交易日缺少唯一报价，不以前一条可见报价替代'
    pair = [previous[0], quote]
    if any(topic_member_metrics._number(row.get(field)) is None or row[field] <= 0
           for row in pair for field in ('open', 'high', 'low', 'close')):
        return None, '观察日或前一市场交易日价格无效，当日涨幅待核验'
    reader = getattr(market, 'actions_for_instruments', None)
    actions = reader([symbol]) if callable(reader) else None
    evidence = actions.get(symbol) if isinstance(actions, dict) else None
    if not isinstance(evidence, list) or any(
            not isinstance(action, dict) or not action.get('date')
            or topic_member_metrics._date(action['date']) != action['date']
            for action in evidence):
        return None, '未留存可核验公司行动覆盖，当日原始收盘比较保持未知'
    bars = [{**row, 'corporate_action_coverage_known': True,
             'corporate_action': any(action['date'] == row['date'] for action in evidence)}
            for row in pair]
    if bars[-1]['corporate_action']:
        return None, '观察日有未复权公司行动，原始收盘价不可直接与前收比较'
    value = topic_member_metrics._number(metrics_for(bars, day, calendar).get('change_pct'))
    return value, None if value is not None else '前后两日价格连续性未通过日K同口径核验'


def summary_for(market, identifier, as_of=None):
    """Bounded batch reads only; no market.panel/chart or provider request."""
    day, status = _day(market, as_of)
    master = _master(market); symbol = _symbol(identifier, master)
    calendar = market.calendar(); position = calendar.index(day)
    previous_day = calendar[position-1] if position > 0 else None
    before = calendar[position-2] if position >= 2 else None
    method = getattr(market, 'history_for_instruments', None)
    selected = method([symbol], start=before or previous_day or day, end=day).get(symbol, []) if callable(method) else []
    exact = [row for row in selected if row.get('date') == day]
    quote = exact[0] if len(exact) == 1 and topic_member_metrics._number(exact[0].get('close')) is not None and exact[0]['close'] > 0 else None
    quotes = {symbol: quote} if quote is not None else {}
    # Returns and closing-limit statistics use the dated close pools only.
    # Keep every past retained session for the latest confirmed event search,
    # without decoding unrelated board-price and money-flow CAS histories.
    supplement, fingerprint = load_supplement_with_fingerprint(status.get('snapshot_id'),
        dates=[value for value in calendar if value <= day],
        fields=('public_pools', 'annual_pools', 'annual_down_pools')) if status.get('snapshot_id') else ({}, None)
    # Reuse the verified generation/candidate/quote/source-digest key. Fake or
    # mutable market objects have no reliable key and must never reuse results.
    key = topic_member_metrics._cache_key(market, day, [symbol], quotes, supplement,
        supplement_fingerprint=fingerprint) if recent_chart_day(calendar, status.get('data_end'), day) else None
    with _LOCK:
        cached = _CACHE.get(key) if key is not None else None
        if cached is not None: _CACHE.move_to_end(key)
    if cached is not None: return deepcopy(cached)
    statistics = topic_member_metrics.member_metrics(market, day, [symbol], master, quotes, supplement,
        supplement_fingerprint=fingerprint).get(symbol, {})
    suspended = 'suspension' in statistics
    if suspended:
        quote = None
        selected = [row for row in selected if row.get('date') != day]
    change, change_reason = _daily_change(market, symbol, day, previous_day, calendar,
        [row for row in selected if row.get('date') in (previous_day, day)], quote)
    quote_reason = '已核实观察日全天停牌' if suspended else change_reason if quote is None else None
    value = summary_fields({**statistics, 'instrument_id': symbol, 'code': symbol[:6],
        'name': master[symbol].get('name') or symbol, 'data_date': day, 'as_of': day,
        'snapshot_id': status.get('snapshot_id'), 'quote_date': day if quote is not None else None,
        'quote_available': quote is not None,
        'quote_reason': quote_reason,
        'close': quote['close'] if quote is not None else None,
        'change_pct': change, 'change_pct_reason': quote_reason if suspended else change_reason,
        'change_pct_date': day, 'change_pct_previous_date': previous_day,
        'return_scope': '年内以上年末已核验交易日收盘为基准；近30日按精确T/T-30市场交易日，公司行动需核验连续价格；本月单独以上月末为基准。',
        'limit_scope': '只统计观察日及之前逐股原始收盘核验的来源涨停事件；不完整次数保持未知，已确认次数仅为下界。'},
        master[symbol], day)
    # Reuse this summary's existing P/T quote batch and dated pool closure.
    # Source text is additive display evidence, never a reconstructed history.
    from app.services.stock_board_presentation import build_board_presentation, down_state, pattern_fields, retained_proof, source_board_events
    from app.services.market_service import dated_events
    from app.services.strategy_limits import quote_index
    reference_method=getattr(market,'reference',None)
    reference=reference_method() if callable(reference_method) else {}
    proof=retained_proof({**reference,'_supplement':supplement},status.get('snapshot_id'))
    source_events=source_board_events(proof,symbol,day)
    own_quotes=quote_index(selected)
    up=proof._state(symbol,day,quote)
    row = {**(quote or {}), 'instrument_id': symbol, 'date': day, 'snapshot_id': status.get('snapshot_id')}
    down = down_state(proof, symbol, day, quote, up)
    patterns = pattern_fields(symbol, master[symbol], day, status.get('snapshot_id'), calendar, own_quotes, row, up, down)
    value['board_presentation']=build_board_presentation(
        row,
        symbol=symbol,day=day,snapshot_id=status.get('snapshot_id'),calendar=calendar,
        quotes=own_quotes, events=source_events, up=up, down=down, patterns=patterns,
        previous_up=proof._state(symbol, previous_day, own_quotes.get(previous_day)),
        before_previous_up=proof._state(symbol, before, own_quotes.get(before)),
        break_events=dated_events({**reference, '_supplement': supplement}, day).get('breakpool', []),
        board_text_events=source_events)
    if key is not None and len(dumps(value).encode()) <= MAX_SUMMARY_BYTES:
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
