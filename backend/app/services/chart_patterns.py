"""Pure dated OHLC patterns, separate from closing-limit and strategy semantics.

All inputs are already-published local facts. A single-price limit day needs
positive turnover and an exact, independently confirmed closing-pool price;
neither a percentage change nor the currently displayed adjusted chart is proof.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re

VERSION = 'verified-source-close-pool-and-raw-ohlc-v1'
KINDS = {
    'earth_to_sky': '地天板',
    'one_price_limit_up': '一字涨停',
    'one_price_limit_down': '一字跌停',
    'broken_board': '炸板',
    'resealed_board': '回封',
}
BOARD_VERSION = 'verified-dated-close-pool-board-events-v1'
TICK = Decimal('0.01')
RATES = (Decimal('0.05'), Decimal('0.10'), Decimal('0.20'), Decimal('0.30'))
SCOPE = '逐日未复权有效成交OHLC与已核验同日收盘来源池；不是盘口封单或完整分时路径'


def _number(value):
    return float(value) if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None


def _tick(value):
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        exact = Decimal(str(number))
        rounded = exact.quantize(TICK, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    # Permit binary-float noise, never a full one-tick price discrepancy.
    return rounded if abs(exact - rounded) <= Decimal('0.0000001') else None


def _state(value, reason=None, status=None):
    return {'state': value, 'status': status or ('confirmed' if value is True else 'not_matched' if value is False else 'unknown'), 'reason': reason}


def _quote(row, symbol=None):
    if not isinstance(row, dict):
        return None, '该市场交易日没有原始成交报价'
    if symbol and any(key in row and (not isinstance(row[key], str) or row[key].upper() != symbol) for key in ('instrument_id', 'thscode')):
        return None, '原始报价显式证券标识与请求不符'
    prices = {key: _tick(row.get(key)) for key in ('open', 'high', 'low', 'close')}
    if any(value is None for value in prices.values()):
        return None, '原始OHLC缺失、非正数或不符合人民币分报价精度'
    if row.get('price_basis', 'none') != 'none':
        return None, '形态核验仅接受未复权原始报价'
    if prices['high'] < max(prices.values()) or prices['low'] > min(prices.values()):
        return None, '原始OHLC高低关系冲突'
    volume = _number(row.get('volume'))
    if volume is None or volume <= 0:
        return None, '缺少正的有效成交量，不据零成交报价认定一字或地天'
    return {'prices': prices, 'volume': volume, 'source': row.get('source'), 'row': row}, None


def _pool_proofs(closing, raw_close, *, day, symbol):
    """The existing close overlay permits 1-cent tolerance; patterns do not."""
    if closing.get('state') is not True:
        return [], closing.get('reason') or '同日收盘来源池状态尚未确认'
    positives = [proof for proof in closing.get('evidence', []) if proof.get('state') is True]
    if any(proof.get('pattern_validation', {}).get('instrument_id') != symbol or
           proof.get('pattern_validation', {}).get('trade_date') != day or
           proof.get('pattern_validation', {}).get('source_identity_valid') is not True or
           proof.get('pattern_validation', {}).get('source_dates_valid') is not True for proof in positives):
        return [], '来源池显式证券或日期字段不一致，不能认定同日形态'
    exact = [proof for proof in positives if proof.get('observed_prices') and all(_tick(value) == raw_close for value in proof['observed_prices'])]
    if not exact or len(exact) != len(positives):
        return [], '来源池原价未精确匹配同日收盘价，不能仅按旧收盘口径容差认定形态'
    return exact, None


def _one_price(quote, closing, *, day, symbol):
    prices = quote['prices']
    if closing.get('status') == 'conflict':
        return _state(None, closing.get('reason') or '收盘来源事件冲突'), []
    if len(set(prices.values())) != 1:
        return _state(False, '原始开高低收不全等，不是一字成交形态'), []
    if closing.get('state') is False:
        return _state(False, '完整已核验来源池明确未收盘入池'), []
    proofs, reason = _pool_proofs(closing, prices['close'], day=day, symbol=symbol)
    return (_state(True), proofs) if proofs else (_state(None, reason), [])


def _board_event(quote, states, *, day, symbol, resealed=False):
    up, broken = states.get('up', {}), states.get('broken', {})
    if (up.get('status') == 'conflict' or broken.get('status') == 'conflict'
            or up.get('state') is True and broken.get('state') is True):
        return _state(None, '同日封板/炸板来源或原价冲突，不认定炸板或回封'), []
    closing = up if resealed else broken
    if closing.get('state') is False:
        return _state(False, '完整原来源池明确未入对应收盘池'), []
    proofs, reason = _pool_proofs(closing, quote['prices']['close'], day=day, symbol=symbol)
    if reason:
        return _state(None, reason), []
    if not resealed:
        return _state(True), proofs
    counts = [value for proof in proofs for value in proof.get('opening_counts', ())]
    if any(proof.get('opening_counts_invalid') for proof in proofs) or (any(value > 0 for value in counts) and 0 in counts):
        return _state(None, '同日收盘封板来源的开板次数缺效或相互冲突，回封保持未知'), []
    if any(value > 0 for value in counts):
        return _state(True), proofs
    if counts:
        return _state(False, '同日封板来源明确开板次数为0，不标回封'), []
    return _state(None, '同日收盘封板已核实，但没有明确开板次数或先炸后封路径证据'), []


def attach_intraday_board_patterns(quote, snapshot, *, content_sha256, resealed=False):
    """Caller has verified the immutable body; only positive same-capture pool evidence is used."""
    from app.services.intraday_board_changes import _pools, _conflicts

    symbol, day = quote['instrument_id'], snapshot['date']
    pools = _pools(snapshot)
    conflict = symbol in _conflicts(snapshot)
    up, broken = symbol in pools['limit_up'][0], symbol in pools['limit_break'][0]
    down = symbol in pools['limit_down'][0]
    current_price = _tick(quote.get('latest_price'))
    for key in ('limit_up', 'limit_break', 'limit_down'):
        event = pools[key][0].get(symbol, {})
        if 'latest_price' in event and _tick(event['latest_price']) != current_price:
            conflict = True
    state = 'unknown' if conflict else 'sealed' if up else 'broken' if broken else None
    opening = []
    if up and not conflict:
        candidates = [pools['limit_up'][0][symbol], *snapshot.get('sealed_stocks', ())]
        candidates.extend(row for group in snapshot.get('ladder', {}).get('groups', ()) for row in group.get('items', ()))
        for row in candidates:
            if row.get('instrument_id') == symbol and 'open_times' in row and row['open_times'] is not None:
                opening.append(row['open_times'])
    valid_counts = all(type(value) is int and value >= 0 for value in opening)
    count_conflict = bool(opening and any(value == 0 for value in opening)
                          and (resealed or any(type(value) is int and value > 0 for value in opening)))
    recovered = up and not conflict and not count_conflict and valid_counts and (resealed or any(value > 0 for value in opening))
    price = current_price
    traded = ((_number(quote.get('volume')) or 0) > 0 or (_number(quote.get('amount')) or 0) > 0)
    events = []
    for kind, confirmed in (('broken_board', state == 'broken'), ('resealed_board', recovered)):
        if not confirmed or price is None or not traded:
            continue
        events.append({'date': day, 'kind': kind, 'label': '盘中' + KINDS[kind],
            'basis': 'verified-intraday-snapshot-board-events-v1', 'snapshot_id': snapshot.get('id'),
            'bar_date': day, 'chart_price': None, 'raw_close': float(price), 'raw_price_basis': 'none',
            'source': 'fuyao', 'sources': ['fuyao'], 'is_intraday': True,
            'evidence': {'snapshot_sha256': content_sha256, 'collected_end_at': snapshot.get('collected_end_at'),
                         'open_times': opening, 'confirmed_previous_broken_current_sealed': bool(resealed),
                         'scope': '仅所选盘中快照已核验来源状态，不代表最终收盘'}})
    quote.update(limit_up_state=state, limit_down_state='unknown' if conflict else 'sealed' if down else None,
                 pattern_events=events)
    if recovered:
        quote['board_transition'] = 'resealed'
    else:
        quote.pop('board_transition', None)
    return quote


def _earth(quote, closing, *, day, calendar, raw, instrument, snapshot_id):
    if closing.get('state') is False:
        return _state(False, '完整已核验来源池明确未收盘涨停'), [], None
    symbol = str(instrument.get('instrument_id') or instrument.get('thscode') or '').upper()
    proofs, reason = _pool_proofs(closing, quote['prices']['close'], day=day, symbol=symbol)
    if reason:
        return _state(None, reason), [], None
    if not isinstance(snapshot_id, str) or not snapshot_id or instrument.get('kind') != 'stock' or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol):
        return _state(None, '缺少确定的行情代际或原始股票身份，不能反推限价'), [], None
    listing = instrument.get('list_date')
    try:
        valid_listing = isinstance(listing, str) and date.fromisoformat(listing).isoformat() == listing
    except ValueError:
        valid_listing = False
    if not valid_listing:
        return _state(None, '原始上市日期未核验，不能排除上市初期无涨跌幅限制日'), [], None
    listed_before = [session for session in calendar if listing <= session < day]
    if len(listed_before) < 5:
        return _state(None, '上市后先前已留存市场交易日不足5个，不能排除上市前五日或日历窗口不足'), [], None
    position = calendar.index(day) if day in calendar else 0
    if position < 1:
        return _state(None, '缺少严格前一市场交易日，不能用上一条报价替代'), [], None
    previous_day = calendar[position - 1]
    if any(previous_day < quoted_day < day for quoted_day in raw):
        return _state(None, '市场日历遗漏已有原始报价日，不能据不完整日历认定严格前一市场日'), [], None
    previous, reason = _quote(raw.get(previous_day), symbol)
    if reason:
        return _state(None, '前一市场交易日原始有效成交报价未核验：' + reason), [], None
    row = quote['row']
    if row.get('corporate_action_coverage_known') is not True or row.get('corporate_action') is not False:
        return _state(None, '当日公司行动覆盖未知或存在公司行动，不能用前收盘反推限价'), [], None
    prior_close = previous['prices']['close']; close = quote['prices']['close']
    matches = []
    for rate in RATES:
        upper = (prior_close * (1 + rate)).quantize(TICK, rounding=ROUND_HALF_UP)
        lower = (prior_close * (1 - rate)).quantize(TICK, rounding=ROUND_HALF_UP)
        upper_min_tick = upper <= prior_close
        lower_min_tick = lower >= prior_close
        if upper_min_tick:
            upper = prior_close + TICK
        if lower_min_tick:
            lower = prior_close - TICK
        if upper == close and lower > 0:
            matches.append((rate, upper, lower, upper_min_tick or lower_min_tick))
    if len(matches) != 1:
        reason = '多个标准涨幅档舍入后同价，限价反推存在歧义' if matches else '已确认收盘涨停价不唯一匹配5/10/20/30%标准对称限价档'
        return _state(None, reason), [], None
    rate, upper, lower, minimum_tick_adjustment = matches[0]
    band = {'basis': 'derived-symmetric-band-from-verified-close-and-previous-session-v1',
            'notice': '由来源确认收盘涨停价和严格前一市场日原收盘唯一匹配标准对称档派生，不是交易所直接提供限价字段',
            'rate_pct': float(rate * 100), 'limit_up_price': float(upper), 'limit_down_price': float(lower),
            'previous_trade_date': previous_day, 'previous_raw_close': float(prior_close), 'observed_upper_close': float(close),
            'listing_date': listing, 'listed_market_sessions_before': len(listed_before),
            'corporate_action_coverage_known': True, 'corporate_action': False,
            'price_tick': float(TICK), 'rounding': 'ROUND_HALF_UP', 'minimum_tick_adjustment': minimum_tick_adjustment,
            'current_st_or_code_used_to_guess_rate': False}
    if quote['prices']['high'] > upper or quote['prices']['low'] < lower:
        return _state(None, '当日原始OHLC越过派生上下限价，限价证据冲突'), [], band
    if quote['prices']['low'] != lower:
        return _state(False, '当日最低成交价未触及派生跌停价'), [], band
    return _state(True), proofs, band


def _raw_quotes(rows, until):
    found = {}; conflicts = set()
    for row in rows:
        day = row.get('date')
        if not isinstance(day, str) or day > until:
            continue
        # Conflicting OHLC/volume cannot be hidden by identical closing prices.
        signature = tuple(row.get(key) for key in ('open', 'high', 'low', 'close', 'volume', 'price_basis', 'instrument_id', 'thscode', 'corporate_action', 'corporate_action_coverage_known'))
        if day in found and signature != found[day][0]:
            conflicts.add(day)
        found[day] = (signature, row)
    return {day: None if day in conflicts else row for day, (_, row) in found.items()}, conflicts


def _regular_board_rate(symbol, day):
    code, exchange = symbol.split('.')
    if exchange == 'SH' and code.startswith(('600', '601', '603', '605')):
        return Decimal('0.10')
    if exchange == 'SZ' and code.startswith(('000', '001', '002', '003')):
        return Decimal('0.10')
    if exchange == 'SH' and code.startswith(('688', '689')) and day >= '2019-07-22':
        return Decimal('0.20')
    if exchange == 'SZ' and code.startswith(('300', '301')) and day >= '2020-08-24':
        return Decimal('0.20')
    if exchange == 'BJ' and code.startswith(('4', '8', '920')) and day >= '2021-11-15':
        return Decimal('0.30')
    return None


def _down_price_reference(quote, previous, *, day, instrument, symbol, closing):
    pool_states = (closing.get(kind, {}) for kind in ('up', 'down', 'broken'))
    if previous is None or any(item.get('state') is True or item.get('status') == 'conflict' for item in pool_states):
        return None
    if instrument.get('is_st_date', instrument.get('identity_date')) == day and instrument.get('is_st') is True:
        return None
    row, prices = quote['row'], quote['prices']
    if row.get('corporate_action_coverage_known') is not True or row.get('corporate_action') is not False:
        return None
    rate = _regular_board_rate(symbol, day)
    if rate is None:
        return None
    prior_close = previous['prices']['close']
    lower = min((prior_close * (1 - rate)).quantize(TICK, rounding=ROUND_HALF_UP), prior_close - TICK)
    upper = max((prior_close * (1 + rate)).quantize(TICK, rounding=ROUND_HALF_UP), prior_close + TICK)
    if prices['close'] != lower or prices['high'] > upper:
        return None
    if any(other != rate and min((prior_close * (1 - other)).quantize(TICK, rounding=ROUND_HALF_UP),
                                 prior_close - TICK) == lower for other in RATES):
        return None
    return {'price': float(lower), 'rate_pct': float(rate * 100), 'previous_raw_close': float(prior_close)}


def attach_chart_patterns(chart, *, raw_rows=(), calendar=(), closing_states=None, evaluation_dates=None):
    """Attach to the caller-owned chart copy; never fetch, persist, or adjust prices."""
    bars = chart.get('bars', [])
    until = chart.get('as_of') or (bars[-1]['date'] if bars else None)
    instrument = chart.get('instrument', {})
    symbol = str(instrument.get('instrument_id') or instrument.get('thscode') or '').upper()
    applicable = instrument.get('kind') != 'index'
    base = {'applicable': applicable, 'scope': SCOPE, 'version': VERSION,
            'since': calendar[0] if calendar else (bars[0]['date'] if bars else None), 'until': until,
            'snapshot_id': chart.get('snapshot_id'), 'price_validation_basis': 'same-date-unadjusted-effective-ohlcv',
            'intraday_path_available': False, 'future_dates_excluded': True,
            'notice': '一字仅证明全天成交价相同且收盘入来源池；地天仅证明触及跌停价后收盘涨停。炸板须有同日来源池，回封须已核验封板及明确开板次数；均不推断首次触板时刻或完整分时路径。'}
    if evaluation_dates is not None:
        base.update(scope='observation_day', definition=SCOPE, evaluation_dates=list(evaluation_dates))
    if not applicable or not until or not bars:
        reason = '指数不适用个股涨跌停形态' if not applicable else '没有可核验日线'
        for bar in bars:
            bar.update(pattern_events=[], pattern_status={kind: _state(None, reason, 'not_applicable' if not applicable else 'unknown') for kind in KINDS})
        chart.update(pattern_events=[], pattern_coverage={**base, 'reason': reason})
        return chart
    full_calendar = sorted({day for day in calendar if isinstance(day, str) and day <= until})
    dates = full_calendar if evaluation_dates is None else sorted({day for day in evaluation_dates if isinstance(day, str) and day <= until and day in full_calendar})
    base['since'] = dates[0] if dates else base['since']
    raw, conflicts = _raw_quotes(raw_rows, until)
    closing_states = closing_states or {}
    identity_reason = None
    if (not isinstance(chart.get('snapshot_id'), str) or not chart['snapshot_id'].strip() or
            instrument.get('kind') != 'stock' or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol) or
            any(key in instrument and (not isinstance(instrument[key], str) or instrument[key].upper() != symbol) for key in ('instrument_id', 'thscode'))):
        identity_reason = '缺少确定的行情代际或明确一致的股票身份，形态保持未知'
    period = chart.get('period', 'D')
    reference_days = ({bar['date'] for bar in bars if not bar.get('noon_provisional') and not bar.get('intraday_provisional')}
                      if period == 'D' and evaluation_dates is None and not identity_reason else set())
    listing = instrument.get('list_date')
    try:
        valid_listing = isinstance(listing, str) and date.fromisoformat(listing).isoformat() == listing
    except ValueError:
        valid_listing = False
    raw_dates = sorted(raw) if reference_days and valid_listing else []
    daily = {}; events = []; references = {}
    for index, day in enumerate(dates):
        row = raw.get(day)
        quote, reason = _quote(row, symbol)
        if day in conflicts:
            reason = '同一交易日原始OHLC或成交量重复冲突'
        if identity_reason:
            reason = identity_reason
        if (day in reference_days and not reason and raw_dates and index >= 5 and listing <= dates[index - 5]
                and quote['prices']['close'] == quote['prices']['low']):
            previous_day = dates[index - 1]
            # A known raw quote omitted by the market calendar invalidates adjacency.
            if raw_dates[bisect_right(raw_dates, previous_day)] == day:
                previous, _ = _quote(raw.get(previous_day), symbol)
                reference = _down_price_reference(quote, previous, day=day, instrument=instrument, symbol=symbol,
                                                  closing=closing_states.get(day, {}))
                if reference is not None:
                    references[day] = reference
        states = {}; day_events = []
        for kind, label in KINDS.items():
            direction = 'down' if kind == 'one_price_limit_down' else 'up'
            closing = closing_states.get(day, {}).get(direction, {})
            proofs = []; band = None
            if reason:
                state = _state(None, reason)
            elif kind in ('broken_board', 'resealed_board'):
                state, proofs = _board_event(quote, closing_states.get(day, {}), day=day, symbol=symbol,
                                              resealed=kind == 'resealed_board')
            elif kind == 'earth_to_sky':
                state, proofs, band = _earth(quote, closing, day=day, calendar=full_calendar, raw=raw, instrument=instrument, snapshot_id=chart.get('snapshot_id'))
            else:
                state, proofs = _one_price(quote, closing, day=day, symbol=symbol)
            states[kind] = state
            if state['state'] is not True:
                continue
            prices = {key: float(value) for key, value in quote['prices'].items()}
            sources = list(dict.fromkeys(proof['source'] for proof in proofs if proof.get('source')))
            event_basis = BOARD_VERSION if kind in ('broken_board', 'resealed_board') else VERSION
            event = {'date': day, 'kind': kind, 'label': label, 'basis': event_basis, 'snapshot_id': chart.get('snapshot_id'),
                     'raw_ohlc': prices, 'raw_low': prices['low'], 'raw_close': prices['close'], 'raw_volume': quote['volume'],
                     'raw_price_basis': 'none', 'source': ' + '.join(sources), 'sources': sources,
                     'bar_date': None, 'chart_price': None,
                     'evidence': {'closing_pool': proofs, 'raw_quote_source': quote['source'], 'limit_band': band,
                                  'price_tick': float(TICK), 'calculation_version': event_basis}}
            events.append(event); day_events.append(event)
        daily[day] = {'states': states, 'events': day_events}
    if period == 'D':
        for bar in bars:
            day = bar['date']; item = daily.get(day, {'states': {kind: _state(None, '该日不在已留存市场交易日历中') for kind in KINDS}, 'events': []})
            attached = []
            for event in item['events']:
                # Display placement only; adjusted prices never enter proof.
                event.update(bar_date=day, chart_price=_number(bar.get('low' if event['kind'] == 'earth_to_sky' else 'close')))
                attached.append(dict(event))
            bar.update(pattern_events=attached, pattern_status=item['states'])
            if day in references:
                bar['limit_down_price_reference'] = references[day]
    else:
        monthly = period == 'M'
        key = (lambda day: day[:7]) if monthly else (lambda day: date.fromisoformat(day).isocalendar()[:2])
        for bar in bars:
            members = [item for day, item in daily.items() if key(day) == key(bar['date'])]
            attached = []
            for item in members:
                for event in item['events']:
                    event.update(bar_date=bar['date']); attached.append(dict(event))
            summary = {'scope': 'monthly_daily_patterns' if monthly else 'weekly_daily_patterns', 'observed_trade_days': len(members)}
            summary.update({kind: {'count': sum(item['states'][kind]['state'] is True for item in members),
                                   'unknown_days': sum(item['states'][kind]['state'] is None for item in members)} for kind in KINDS})
            bar.update(pattern_events=attached, pattern_status={kind: _state(None, '仅汇总周期内具体交易日形态，不认定整根周月K形态', 'daily_summary') for kind in KINDS}, pattern_summary=summary)
    coverage = {**base, 'expected_trade_days': len(dates), 'valid_ohlcv_days': sum(_quote(raw.get(day), symbol)[0] is not None and day not in conflicts for day in dates)}
    for kind in KINDS:
        coverage[kind] = {status + '_days': sum(item['states'][kind]['status'] == status for item in daily.values()) for status in ('confirmed', 'not_matched', 'unknown')}
    chart.update(pattern_events=[dict(event) for event in events if event['bar_date'] is not None], pattern_coverage=coverage)
    return chart
