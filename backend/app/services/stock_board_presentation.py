"""Dated stock-board display facts, separate from raw fields and strategy rules.

The pure builder consumes already-read dated quotes and source proofs. The small
collection adapter performs one bounded batch, never a per-stock history read.
"""
from __future__ import annotations

from decimal import Decimal
import math
import re

from app.services.chart_limits import _combine, _evidence
from app.services.chart_patterns import VERSION as PATTERN_VERSION, attach_chart_patterns, _pool_proofs, _quote
from app.services.strategy_limits import LimitConditionEvidence, quote_index

VERSION = 'dated-stock-board-presentation-v1'
REPEATED_BREAK_THRESHOLD = 3
LARGE_LOSS_THRESHOLD_PCT = -5
STOCK = re.compile(r'\d{6}\.(SH|SZ|BJ)')
SHAPES = {'earth_to_sky': '地天板', 'one_price_limit_up': '一字涨停', 'one_price_limit_down': '一字跌停'}


def retained_proof(reference, snapshot_id):
    """Bind the panel's immutable supplement without a second source read."""
    class RetainedInputs:
        def reference(self): return reference
    proof = LimitConditionEvidence(RetainedInputs())
    proof.snapshot_id = snapshot_id
    supplement = reference.get('_supplement', {})
    proof.supplement = supplement if supplement.get('snapshot_id') in (None, snapshot_id) else {}
    return proof


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _count(value):
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number == int(number) else None


def _raw(row, symbol, day):
    return (isinstance(row, dict) and row.get('date', row.get('trade_date')) == day
            and all(key not in row or row[key] == symbol for key in ('instrument_id', 'thscode'))
            and all(key not in row or row[key] == day for key in ('date', 'trade_date'))
            and row.get('price_basis', 'none') == 'none'
            and (_number(row.get('close')) or 0) > 0 and (_number(row.get('volume')) or 0) > 0)


def _event_matches(event, symbol, day, quote):
    if not isinstance(event, dict) or not quote: return False
    identity = event.get('instrument_id') or event.get('thscode')
    if identity != symbol or any(key in event and event[key] != symbol for key in ('instrument_id', 'thscode')): return False
    if any(key in event and event[key] != day for key in ('date', 'trade_date')): return False
    prices = [event[key] for key in ('last_price', 'close') if key in event]
    return bool(prices) and all(_number(value) is not None and abs(value-quote['close']) < 1e-7 for value in prices)


def _confirmed(state, quote, symbol, day):
    if not _raw(quote, symbol, day): return False
    proofs, _ = _pool_proofs(state or {}, Decimal(str(quote['close'])), day=day, symbol=symbol)
    return bool(proofs)


def down_state(proof, symbol, day, quote, up=None):
    """Reuse the already indexed closing sources; no source or quote I/O."""
    if not _raw(quote, symbol, day): return {'state': None, 'reason': '同日有效原始报价未齐', 'evidence': []}
    evidence = []
    for record, by_code, source, path, granular, allow_absence in proof._records(day, 'down'):
        narrowed = by_code.get(symbol[:6], [])
        view = {**record, 'items': narrowed or [{'instrument_id': 'unrelated-source-row'}]} if record.get('items') else record
        evidence.append(_evidence(view, symbol, day, quote['close'], source=source, path=path,
                                  kind='down', granular=granular, allow_absence=allow_absence))
    result = _combine(evidence)
    if (up or {}).get('status') == 'conflict' or result.get('status') == 'conflict' or ((up or {}).get('state') is True and result.get('state') is True):
        return {'state': None, 'status': 'conflict', 'reason': '同日涨跌停来源冲突', 'evidence': []}
    return result


def pattern_fields(symbol, identity, day, snapshot_id, calendar, quotes, panel, up, down):
    """The chart evaluator with only the existing bounded raw quote batch."""
    raw = [dict(row) for row in quotes.values() if isinstance(row, dict)]
    current = next((row for row in raw if row.get('date') == day), {})
    if (current and panel.get('snapshot_id') == snapshot_id and panel.get('date') == day
            and all(panel.get(key) == current.get(key) for key in ('open', 'high', 'low', 'close', 'volume'))):
        for key in ('corporate_action', 'corporate_action_coverage_known'):
            if key not in current: current[key] = panel.get(key)
            elif key in panel and current[key] != panel[key]: current['corporate_action_coverage_known'] = False
    chart = {'instrument': identity, 'snapshot_id': snapshot_id, 'as_of': day, 'period': 'D',
             'bars': [{'date': day, 'low': current.get('low'), 'close': current.get('close')}]}
    result = attach_chart_patterns(chart, raw_rows=raw, calendar=calendar,
                                   closing_states={day: {'up': up, 'down': down}}, evaluation_dates=[day])
    return {**{key: result['bars'][0][key] for key in ('pattern_events', 'pattern_status')},
            'pattern_date': day, 'pattern_as_of': day, 'pattern_coverage': result['pattern_coverage']}


def _height(events, symbol, day, quote, confirmed):
    unknown = {'value': None, 'label': None, 'state': 'unknown', 'source_date': day,
               'reason': '收盘涨停或同日来源连板高度未核验'}
    if not confirmed: return unknown
    valid = [event for event in events if _event_matches(event, symbol, day, quote)]
    values = [_count(event[key]) for event in valid for key in ('continue_day_cnt', 'board_count', 'board_num') if event.get(key) is not None]
    if not values or any(value is None or value < 1 for value in values) or len(set(values)) != 1: return unknown
    value = values[0]
    return {'value': value, 'label': '首板' if value == 1 else f'{value}连板', 'state': 'confirmed',
            'source_date': day, 'reason': '同日原始收盘与正成交量核验涨停；高度仅为该日来源连板数，不自动认定龙头'}


def source_board_events(proof, symbol, day):
    """Read the provider's own statistic text from its already indexed pool."""
    result=[]
    for record, by_code, source, path, *_ in proof._records(day,'up'):
        if source!='fuyao':continue
        for event in by_code.get(symbol[:6],[]):
            if isinstance(event,dict) and event.get('continue_day_text') is not None:
                result.append({**event,'source':'fuyao','source_path':path})
    return result


def board_text_for(events, symbol, day, quote, confirmed):
    """Preserve a dated source description; never infer N-day/N-board counts."""
    base={'label':None,'state':'unknown','source':'fuyao','source_date':day,
          'window_sessions':None,'limit_up_count':None,
          'reason':'同日来源未留存可核验的几天几板描述；不以连续连板数反推'}
    if not confirmed or not _raw(quote,symbol,day):return base
    valid=[]
    for event in events:
        if not _event_matches(event,symbol,day,quote):continue
        text=event.get('continue_day_text')
        if not isinstance(text,str):continue
        text=text.strip()
        if text=='首板' or re.fullmatch(r'[1-9]\d{0,3}连板',text):
            valid.append((text,None,None,event.get('source_path')))
            continue
        match=re.fullmatch(r'([1-9]\d{0,3})天([1-9]\d{0,3})板',text)
        if match:
            sessions,boards=map(int,match.groups())
            if boards<=sessions:valid.append((text,sessions,boards,event.get('source_path')))
    labels={item[0] for item in valid}
    if len(labels)>1:return {**base,'state':'conflict','reason':'同日扶摇来源的几天几板描述冲突，未选择其中一条'}
    if not valid:return base
    text,sessions,boards,path=valid[0]
    return {**base,'label':text,'state':'reported','window_sessions':sessions,'limit_up_count':boards,
            'source_path':path,'reason':'扶摇同日收盘池原始涨停统计文本，证券、日期及原始收盘已匹配；属于来源描述，不等同于连续涨停高度，未据此新增策略信号'}


def build_board_presentation(row, *, symbol, day, snapshot_id, calendar, quotes, events=(), previous_events=(),
                             break_events=(), up=None, previous_up=None, before_previous_up=None, down=None, patterns=None, board_text_events=()):
    """Return independent height, shape, volume and next-day feedback facts."""
    valid_calendar = isinstance(calendar, list) and calendar == sorted(set(calendar)) and day in calendar
    position = calendar.index(day) if valid_calendar else 0
    previous = calendar[position-1] if position else None
    current = quotes.get(day); prior = quotes.get(previous) if previous else None
    generation = (isinstance(snapshot_id, str) and bool(snapshot_id) and STOCK.fullmatch(str(symbol))
                  and row.get('snapshot_id', snapshot_id) == snapshot_id and row.get('date', day) == day)
    current_valid = bool(generation and _raw(current, symbol, day)
                         and (row.get('close') is None or row['close'] == current['close']))
    prior_valid = bool(generation and previous and _raw(prior, symbol, previous))
    up_confirmed = bool(current_valid and _confirmed(up, current, symbol, day))
    prior_confirmed = bool(prior_valid and _confirmed(previous_up, prior, symbol, previous))
    down_confirmed = bool(current_valid and _confirmed(down, current, symbol, day))
    height = _height(events, symbol, day, current, up_confirmed)
    board_text = board_text_for(board_text_events,symbol,day,current,up_confirmed)
    previous_height = _height(previous_events, symbol, previous, prior, prior_confirmed)
    types = []
    def tag(code, label, reason):
        types.append({'code': code, 'label': label, 'state': 'confirmed', 'source_date': day, 'reason': reason})
    if (up_confirmed and prior_valid and (previous_up or {}).get('state') is False
            and position >= 2 and _raw(quotes.get(calendar[position-2]), symbol, calendar[position-2])
            and _confirmed(before_previous_up, quotes[calendar[position-2]], symbol, calendar[position-2])):
        tag('rebound_board', '反包板候选', '严格相邻三交易日依次确认收盘涨停、未涨停、收盘涨停；属于梯队反包候选口径')
    patterns = patterns or {}
    shape_codes = set()
    if (generation and patterns.get('pattern_date') == day and patterns.get('pattern_as_of') == day
            and patterns.get('pattern_coverage', {}).get('snapshot_id') == snapshot_id):
        for item in patterns.get('pattern_events', []):
            kind = item.get('kind')
            if (kind in SHAPES and kind not in shape_codes and item.get('date') == day
                    and item.get('snapshot_id') == snapshot_id and item.get('basis') == PATTERN_VERSION
                    and item.get('raw_price_basis') == 'none' and current_valid
                    and item.get('raw_volume') == current['volume']
                    and item.get('raw_ohlc') == {key: current.get(key) for key in ('open', 'high', 'low', 'close')}
                    and patterns.get('pattern_status', {}).get(kind, {}).get('state') is True
                    and ((kind == 'one_price_limit_down' and down_confirmed) or (kind != 'one_price_limit_down' and up_confirmed))):
                shape_codes.add(kind); tag(kind, SHAPES[kind], '复用同日未复权OHLC与来源收盘池形态核验；不还原盘中路径或保证可成交')
    if down_confirmed and 'one_price_limit_down' not in shape_codes:
        tag('limit_down', '跌停', '同日原始收盘与有效成交量已核验跌停来源池')
    quote, _ = _quote(current, symbol) if current_valid else (None, None)
    if up_confirmed and quote and quote['prices']['high'] > quote['prices']['low'] and 'one_price_limit_up' not in shape_codes:
        tag('non_one_price_limit_up', '非一字涨停', '当日已确认涨停且原始成交价有价差；常见换手板观察，不代表充分换手或封板质量')
    matched_breaks = [event for event in break_events if current_valid and _event_matches(event, symbol, day, current)]
    if matched_breaks:
        if (up or {}).get('state') is False:
            tag('broken_board', '炸板', '同日来源炸板池匹配原始收盘，且已确认收盘未涨停')
        else:
            tag('broken_pool_source', '炸板来源样本', '同日来源炸板池证券、日期与原始收盘已匹配；不据此还原完整分时路径或保证收盘未封板')
    source_events = [event for event in [*events, *break_events] if current_valid and _event_matches(event, symbol, day, current)]
    opens = [_count(event[key]) for event in source_events for key in ('open_times', 'open_num', 'break_count') if event.get(key) is not None]
    if opens and None not in opens and len(set(opens)) == 1 and opens[0] > 0:
        count = opens[0]
        tag('open_count', f'开板{count}次', '明确同日来源的开板次数；不据此还原先后顺序')
        if count >= REPEATED_BREAK_THRESHOLD:
            tag('repeated_break', '反复开板', f'项目展示阈值：来源开板次数≥{REPEATED_BREAK_THRESHOLD}；不等同于交易所板型或大烂板评级')
    panel_matches = (current_valid and _raw(row, symbol, day)
                     and all(row.get(key) == current.get(key) for key in ('open', 'high', 'low', 'close', 'volume'))
                     and all(key not in current or current[key] == row.get(key) for key in ('corporate_action', 'corporate_action_coverage_known', 'price_continuity')))
    comparable = (panel_matches and prior_valid and row.get('corporate_action_coverage_known') is True
                  and row.get('price_continuity') is True and row.get('corporate_action') is not True)
    ratio = current['volume']/prior['volume'] if comparable else None
    volume_state = ('expanded' if ratio > 1 else 'contracted' if ratio < 1 else 'flat') if ratio is not None else 'unknown'
    volume = {'state': volume_state, 'label': {'expanded': '放量', 'contracted': '缩量', 'flat': '平量'}.get(volume_state),
              'ratio': ratio, 'previous_date': previous,
              'reason': '严格相邻市场交易日同证券正成交量之比，且公司行动可比；不推断锁量或筹码' if comparable else '相邻原始报价、正成交量或公司行动可比证据未齐，量能保持未知'}
    change = float((Decimal(str(current['close']))/Decimal(str(prior['close']))-1)*100) if comparable else None
    if change is not None and (_number(row.get('change_pct')) is None or abs(change-row['change_pct']) >= 1e-7):
        change = None
    feedback = {'code': None, 'label': None, 'state': 'unknown', 'source_date': day, 'cohort_date': previous,
                'reason': '前一日涨停队列、今日价格或事件证据未齐；不把未知记为晋级失败'}
    if prior_confirmed:
        code = ('limit_down' if down_confirmed else 'promoted' if up_confirmed else
                'large_loss' if change is not None and change <= LARGE_LOSS_THRESHOLD_PCT else
                'negative' if change is not None and change < 0 else 'positive' if change is not None and change > 0 else
                'flat' if change == 0 else None)
        if code:
            feedback.update(code=code, label={'limit_down': '跌停反馈', 'promoted': '晋级', 'large_loss': '大幅回落',
                            'negative': '回落', 'positive': '正反馈', 'flat': '平盘'}[code], state='confirmed',
                            reason='前日确认涨停队列的今日观察；大幅回落为项目收盘收益≤−5%阈值，正反馈不代表资金承接或可成交收益')
    if change is not None and _number(current.get('low')) is not None and current['low'] < prior['close'] <= current['close'] and quote:
        tag('pullback_reclaim', '回踩收回', '日线最低价低于前收且收盘不低于前收；仅日价关系，不还原盘中顺序或资金承接')
    return {'version': VERSION, 'date': day, 'snapshot_id': snapshot_id, 'instrument_id': symbol,
            'height': height, 'previous_height': previous_height, 'board_text':board_text, 'types': types, 'feedback': feedback, 'volume': volume,
            'limitations': ['高度、板型、量能和前日队列反馈分别核验；标签不改变策略条件。',
                            '未接入完整盘口，不能据此认定充分换手、锁量、封单稳定或明日可成交。']}


def decorate_board_collections(market, status, day, reference, collections, *, master=None):
    """Decorate supplied rows using one bounded three-session batch."""
    from app.services.market_service import dated_events
    calendar = market.calendar()
    valid = isinstance(calendar, list) and calendar == sorted(set(calendar)) and day in calendar
    position = calendar.index(day) if valid else 0
    previous = calendar[position-1] if position else None
    before = calendar[position-2] if position >= 2 else None
    symbols = sorted({row.get('instrument_id') for rows in collections.values() for row in rows
                      if isinstance(row.get('instrument_id'), str) and STOCK.fullmatch(row['instrument_id'])})
    if not symbols: return {key: list(rows) for key, rows in collections.items()}
    method = getattr(market, 'history_for_instruments', None)
    histories = method(symbols, start=before or previous or day, end=day) if valid and symbols and callable(method) else {}
    quotes = {symbol: quote_index(row for row in histories.get(symbol, []) if row.get('date', row.get('trade_date')) in (before, previous, day)) for symbol in symbols}
    proof = retained_proof(reference, status.get('snapshot_id'))
    if master is None:
        master = {row['instrument_id']: row for row in market.instruments(limit=10000)} if callable(getattr(market, 'instruments', None)) else {}
    current_events = dated_events(reference, day); past_events = dated_events(reference, previous) if previous else {}
    event_index = {}
    for key, entries in (('up', current_events.get('limitup', [])), ('prior', past_events.get('limitup', [])),
                         ('break', current_events.get('breakpool', []))):
        event_index[key] = {}
        for event in entries:
            identifier = event.get('instrument_id') or event.get('thscode')
            if isinstance(identifier, str):
                event_index[key].setdefault(identifier, []).append(event)

    generation = status.get('snapshot_id') if market.status().get('snapshot_id') == status.get('snapshot_id') else None
    result = {}
    for key, rows in collections.items():
        decorated = []
        for row in rows:
            symbol = row.get('instrument_id'); own = quotes.get(symbol, {})
            current = own.get(day); prior = own.get(previous)
            up = proof._state(symbol, day, current); prior_up = proof._state(symbol, previous, prior) if previous else {}
            before_up = proof._state(symbol, before, own.get(before)) if up.get('state') is True and prior_up.get('state') is False and before else None
            down = down_state(proof, symbol, day, current, up)
            shape = (pattern_fields(symbol, master.get(symbol, {}), day, generation, calendar, own, row, up, down)
                     if up.get('state') is True or down.get('state') is True else None)
            display = build_board_presentation(row, symbol=symbol, day=day, snapshot_id=generation, calendar=calendar,
                quotes=own, events=event_index['up'].get(symbol, ()), previous_events=event_index['prior'].get(symbol, ()),
                break_events=event_index['break'].get(symbol, ()), up=up, previous_up=prior_up, before_previous_up=before_up, down=down, patterns=shape,
                board_text_events=source_board_events(proof,symbol,day))
            decorated.append({**row, 'board_presentation': display})
        result[key] = decorated
    return result
