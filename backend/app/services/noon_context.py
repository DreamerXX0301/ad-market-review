"""Freeze prior-session material as known at capture start; never invent a review.

Only retained SQLite revisions and fixed-generation published prices are read.
The worker calls this before appending the independent noon snapshot. Historical
GETs use the already frozen result, not this builder or today's topic membership.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import re

from app.core import connect
from app.services.market_mainlines import _view as mainline_view
from app.services.noon_comparison import index_source

SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
MAX_MAINLINES = 100
MAINLINE_BYTES = 192 * 1024
MAX_OBSERVATIONS = 40
MAX_EVENT_SAMPLES = 1000
MAX_SECTORS = 1500
MAX_THEME_LENGTH = 2000
EVENT_POOLS = {'limit_up': 'limitup', 'limit_break': 'breakpool', 'limit_down': 'limitdown'}
EVENT_LABELS = {'limit_up': '涨停', 'limit_break': '炸板', 'limit_down': '跌停'}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _quote_index(quotes):
    values = quotes.values() if isinstance(quotes, dict) else quotes if isinstance(quotes, list) else []
    output = {}
    for row in values:
        if not isinstance(row, dict):
            continue
        symbol = row.get('instrument_id') or row.get('thscode')
        if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
            output[symbol] = row
    return output


def _prior_material(previous_day, known_at_ms):
    """Read a bounded exact-date closure in one short SQLite read transaction."""
    gaps = []
    with connect() as connection:
        connection.execute('BEGIN')
        record = connection.execute('''SELECT * FROM market_sentiment_snapshot
            WHERE trade_date=? AND recorded_at_ms<=?
            ORDER BY recorded_at_ms DESC,rowid DESC LIMIT 1''', (previous_day, known_at_ms)).fetchone()
        rows = connection.execute('''SELECT record.* FROM market_mainline_revision record
            JOIN (SELECT instrument_id,max(revision) revision FROM market_mainline_revision
              WHERE trade_date=? AND recorded_at_ms<=? GROUP BY instrument_id) head
            ON record.trade_date=? AND record.instrument_id=head.instrument_id AND record.revision=head.revision
            ORDER BY record.instrument_id LIMIT ?''',
            (previous_day, known_at_ms, previous_day, MAX_MAINLINES + 1)).fetchall()
    retained = None
    if record:
        raw = record['payload_json']
        if sha256(raw.encode()).hexdigest() != record['content_sha256']:
            gaps.append('上个交易日留存复盘的SHA校验失败，未纳入午评。')
        else:
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and payload.get('date') == previous_day:
                    retained = {'snapshot_id': record['snapshot_id'], 'recorded_at_ms': record['recorded_at_ms'],
                                'content_sha256': record['content_sha256'], 'payload': payload}
                else:
                    gaps.append('留存复盘正文日期或结构与上个交易日不符，未纳入午评。')
            except (TypeError, ValueError):
                gaps.append('留存复盘正文无法读取，未纳入午评。')
    mainlines = []
    budget = 0
    for row in rows[:MAX_MAINLINES]:
        if row['action'] == 'revoke':
            continue
        try:
            item = mainline_view(row)
            size = len(_encoded(item).encode())
        except (TypeError, ValueError):
            gaps.append('存在无法读取的前日主线修订，未按当前归类替代。')
            continue
        if budget + size > MAINLINE_BYTES:
            gaps.append('前日主线原文超过午评有界留存容量，未纳入其余记录。')
            break
        mainlines.append(item)
        budget += size
    if len(rows) > MAX_MAINLINES:
        gaps.append(f'前日主线超过{MAX_MAINLINES}条，本次只冻结有界原文样本，不能视为全部关注项。')
    return retained, mainlines, gaps


def _background(market, previous_day):
    """Only exact-session facts; a stale retained latest date is never yesterday."""
    try:
        calendar = market.calendar()
        if previous_day not in calendar:
            return None, {}, ['上个市场交易日的盘后行情尚未留存，未退回更早日期。']
        rows = market.panel(previous_day)
        dated = {row['instrument_id']: row for row in rows if isinstance(row, dict)
                 and isinstance(row.get('instrument_id'), str)
                 and SYMBOL.fullmatch(row['instrument_id'])
                 and row.get('date', row.get('trade_date')) == previous_day}
        if not dated:
            return None, {}, ['上个市场交易日没有可核验的同日收盘记录，未使用最近有价日替代。']
        values = [row['change_pct'] for row in dated.values() if _number(row.get('change_pct')) is not None
                  and (_number(row.get('close')) or 0) > 0]
        stamp = market.status() if callable(getattr(market, 'status', None)) else {}
        facts = {'date': previous_day, 'source_snapshot_id': stamp.get('snapshot_id'),
                 'mode': 'previous_session_price_facts', 'observed': len(dated), 'comparable': len(values),
                 'up': sum(v > 0 for v in values) if values else None,
                 'down': sum(v < 0 for v in values) if values else None,
                 'flat': sum(v == 0 for v in values) if values else None,
                 'notice': '本次采集时读取的前日收盘事实背景，不是昨天已保存的判断。'}
        return facts, dated, []
    except (OSError, ValueError, KeyError, TypeError):
        return None, {}, ['前日已发布行情无法核验，保留缺口，不重新计算或补造昨日判断。']


def _watchlist(retained):
    if not retained:
        return []
    payload = retained['payload']
    watch = payload.get('watchlist')
    rows = watch.get('stocks', []) if isinstance(watch, dict) else []
    return [r for r in rows[:MAX_OBSERVATIONS] if isinstance(r, dict)] if isinstance(rows, list) else []


def _previous_noon(previous_day, known_at_ms):
    """Freeze only a compact exact-day noon baseline known before this capture."""
    result = {'date': previous_day, 'state': 'unavailable', 'snapshot_id': None,
              'content_sha256': None, 'recorded_at_ms': None, 'collected_start_at': None,
              'collected_end_at': None, 'quote_as_of': None, 'calendar_verified': False,
              'source': None, 'indices': [],
              'reason': '采集开始前未留存严格前一交易日午评，昨日同期成交额不可用。'}
    with connect() as connection:
        connection.execute('BEGIN')
        record = connection.execute('''SELECT snapshot_id,trade_date,recorded_at_ms,
            collected_start_at,collected_end_at,content_sha256,payload_json
            FROM market_noon_snapshot WHERE trade_date=? AND recorded_at_ms<=? LIMIT 1''',
            (previous_day, known_at_ms)).fetchone()
    if record is None:
        return result
    encoded = record['payload_json']
    if sha256(encoded.encode()).hexdigest() != record['content_sha256']:
        result['reason'] = '严格前一交易日午评正文SHA校验失败，未纳入成交额对照。'
        return result
    try:
        payload = json.loads(encoded)
        if (not isinstance(payload, dict) or payload.get('date') != previous_day
                or payload.get('version') != 'noon-snapshot-v1'
                or record['trade_date'] != previous_day
                or payload.get('collected_start_at') != record['collected_start_at']
                or payload.get('collected_end_at') != record['collected_end_at']):
            result['reason'] = '严格前一交易日午评身份、日期或采集区间不一致，未纳入成交额对照。'
            return result
        rows = payload.get('indices')
        if not isinstance(rows, list) or len(rows) > 64:
            result['reason'] = '昨日午评指数列表缺失或超出有界摘要容量，未纳入成交额对照。'
            return result
        indices = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = row.get('instrument_id')
            if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
                continue
            amount = _number(row.get('amount'))
            dated = not any(key in row and row[key] != previous_day for key in ('date', 'trade_date', 'quote_date'))
            indices.append({'instrument_id': symbol,
                            'amount': amount if dated and amount is not None and amount >= 0 else None,
                            'currency': row.get('currency') if isinstance(row.get('currency'), str) else None,
                            'quote_at': row.get('quote_at') if isinstance(row.get('quote_at'), str)
                            else None if row.get('quote_at') is None else '',
                            'source': index_source(payload, row)})
        calendar = payload.get('calendar')
        calendar = calendar if isinstance(calendar, dict) else {}
        sources = {row['source'] for row in indices if row['source'] is not None}
        result.update(state='available', snapshot_id=record['snapshot_id'],
                      content_sha256=record['content_sha256'], recorded_at_ms=record['recorded_at_ms'],
                      collected_start_at=record['collected_start_at'], collected_end_at=record['collected_end_at'],
                      quote_as_of=payload.get('quote_as_of') if isinstance(payload.get('quote_as_of'), str) else None,
                      calendar_verified=calendar.get('verified') is True,
                      source=next(iter(sources)) if len(sources) == 1 else None, indices=indices,
                      reason='仅冻结采集开始前已保存的严格前日午评指数小摘要；单条时间缺失时不称昨日同期。')
        return result
    except (TypeError, ValueError):
        result['reason'] = '严格前一交易日午评正文结构无法核验，未纳入成交额对照。'
        return result


def _source_inputs(market, previous_day):
    """Bind one published generation and expand only the selected day's facts."""
    gaps = []
    try:
        reference = market.reference() if callable(getattr(market, 'reference', None)) else {}
        reference = reference if isinstance(reference, dict) else {}
        status = market.status() if callable(getattr(market, 'status', None)) else {}
        snapshot_id = status.get('snapshot_id') if isinstance(status, dict) else None
        supplement = reference.get('_supplement')
        fingerprint = None
        if not isinstance(supplement, dict) or not supplement:
            supplement = {}
            # Fixtures without a generation never fall through to a local store.
            if isinstance(snapshot_id, str) and snapshot_id:
                from app.services.market_supplement import load_supplement_with_fingerprint
                supplement, fingerprint = load_supplement_with_fingerprint(
                    snapshot_id, dates=(previous_day,),
                    fields=('annual_pools', 'annual_down_pools', 'public_pools', 'sector_index_history'))
        if not isinstance(supplement, dict) or supplement.get('snapshot_id') not in (None, snapshot_id):
            supplement = {}
            gaps.append('前日盘面补充与固定行情代际不符，未纳入午评。')
        if supplement.get('failure_reason'):
            gaps.append('前日盘面补充未通过本地来源校验，相关样本保持未知。')
        return {**reference, '_supplement': supplement}, snapshot_id, fingerprint, gaps
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, None, None, ['前日事件与板块背景读取失败，未联网补取或使用其他日期。']


def _event_rows(reference, previous_day, key):
    """Preserve competing source rows instead of letting a merge hide conflicts."""
    pool = EVENT_POOLS[key]
    history = reference.get('pool_history')
    record = reference if reference.get('pool_trade_date') == previous_day else (
        history.get(previous_day, {}) if isinstance(history, dict) else {})
    rows, available = [], False
    if isinstance(record, dict) and isinstance(record.get(pool), list):
        available = True
        rows.extend((row, 'reference/' + previous_day + '/' + pool) for row in record[pool])
    supplement = reference.get('_supplement', {})
    for field in ({'limit_up': ('annual_pools', 'public_pools'),
                   'limit_down': ('annual_down_pools',), 'limit_break': ()}[key]):
        sources = supplement.get(field)
        source = sources.get(previous_day) if isinstance(sources, dict) else None
        if (not isinstance(source, dict) or source.get('date', previous_day) != previous_day
                or not isinstance(source.get('items'), list)):
            continue
        available = True
        rows.extend((row, field + '/' + previous_day) for row in source['items'])
    return rows, available


def _verified_event(event, symbol, previous_day, quote):
    if (not isinstance(event, dict) or not isinstance(quote, dict)
            or any(key in event and event[key] != symbol for key in ('instrument_id', 'thscode'))
            or any(key in event and event[key] != previous_day for key in ('date', 'trade_date'))
            or any(key in quote and quote[key] != symbol for key in ('instrument_id', 'thscode'))
            or quote.get('date', quote.get('trade_date')) != previous_day
            or any(key in quote and quote[key] != previous_day for key in ('date', 'trade_date'))
            or quote.get('price_basis', 'none') != 'none'
            or (_number(quote.get('close')) or 0) <= 0
            or (_number(quote.get('volume')) or 0) <= 0):
        return False
    prices = [event[key] for key in ('last_price', 'close') if key in event]
    return bool(prices) and all(_number(value) is not None
                               and abs(value - quote['close']) < 1e-7 for value in prices)


def _source_height(events):
    values = [row[key] for row in events for key in ('continue_day_cnt', 'board_count', 'board_num')
              if row.get(key) is not None]
    if (not values or any(_number(value) is None or value < 1 or value != int(value) for value in values)
            or len(set(values)) != 1):
        return None
    return int(values[0])


def _source_theme(events, previous_day):
    texts = {row['limit_up_reason'] for row in events
             if isinstance(row.get('limit_up_reason'), str) and row['limit_up_reason'].strip()}
    valid = len(texts) == 1 and len(next(iter(texts))) <= MAX_THEME_LENGTH
    return {'theme_text': next(iter(texts)) if valid else None,
            'theme_date': previous_day, 'theme_source': 'previous_session_pool',
            'theme_reason': (None if valid else '前日多个来源涨停理由原文不一致，未擅选或拼接归因。'
                             if len(texts) > 1 else '前日来源理由超出有界原文容量，保持未知。'
                             if texts else '前日来源未留存理由原文，不用当前成员补写昨日题材。')}


def _event_cohorts(reference, previous_day, previous_quotes):
    output = {}
    for key, label in EVENT_LABELS.items():
        raw, available = _event_rows(reference, previous_day, key)
        grouped, invalid = {}, 0
        for row, path in raw:
            symbol = (row.get('thscode') or row.get('instrument_id')) if isinstance(row, dict) else None
            if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
                invalid += 1
                continue
            grouped.setdefault(symbol, []).append((row, path))
        items = []
        for symbol in sorted(grouped)[:MAX_EVENT_SAMPLES]:
            evidence = grouped[symbol]
            quote = previous_quotes.get(symbol, {})
            matched = [(row, path) for row, path in evidence if _verified_event(row, symbol, previous_day, quote)]
            # A contradictory priced source is not silently replaced by a favorable one.
            contradicted = any(not _verified_event(row, symbol, previous_day, quote)
                               and any(field in row for field in ('last_price', 'close')) for row, _ in evidence)
            if not matched or contradicted:
                continue
            valid_rows = [row for row, _ in matched]
            height = _source_height(valid_rows) if key == 'limit_up' else None
            items.append({'instrument_id': symbol, 'name': valid_rows[0].get('name') or symbol,
                          'date': previous_day, 'previous_board_count': height,
                          **_source_theme(valid_rows, previous_day),
                          'previous_close': quote['close'], 'source_paths': sorted({path for _, path in matched}),
                          'height_reason': ('来源同日连续板数字段一致；不代表午间收盘晋级'
                                            if height is not None else '未核验一致的前日收盘涨停高度，不从文字或跌停连续数猜测'),
                          'source_note': '来源池证券、明确日期、未复权正成交量收盘与事件价已核对；仅为已留存样本。'})
        source_count = len(grouped) + invalid if available else None
        unverified = source_count - len(items) if source_count is not None else None
        output[key] = {'date': previous_day,
                       'state': 'partial' if items and unverified else 'observed_sample' if items else 'unknown',
                       'source_count': source_count, 'raw_source_row_count': len(raw),
                       'verified_count': len(items), 'unverified_count': unverified, 'items': items,
                       'reason': ('只冻结严格前日已核验样本；缺失不按零反馈处理。' if items else
                                  '未取得可与严格前日未复权正成交量收盘核验的' + label + '样本。'),
                       'scope': '严格前一交易日已留存' + label + '来源池样本；跨源同证券合并核验，不代表全市场完整事件集合。'}
    # The same closing price cannot prove both a closing up-limit and down-limit.
    conflict = ({row['instrument_id'] for row in output['limit_up']['items']}
                & {row['instrument_id'] for row in output['limit_down']['items']})
    if conflict:
        for key in ('limit_up', 'limit_down'):
            pool = output[key]
            pool['items'] = [row for row in pool['items'] if row['instrument_id'] not in conflict]
            pool['verified_count'] = len(pool['items'])
            pool['unverified_count'] += len(conflict)
            pool['state'] = 'partial' if pool['items'] else 'unknown'
            pool['reason'] += ' 同日涨跌停来源冲突样本未纳入。'
    return output


def _sector_background(reference, previous_day, snapshot_id, fingerprint):
    result = {'date': previous_day, 'state': 'unknown', 'items': [],
              'source_snapshot_id': snapshot_id,
              'source_index_sha256': fingerprint[3] if fingerprint is not None else None,
              'reason': '未取得严格前日已核验的同来源代码板块指数行情；不以成员涨幅或当前同名方向替代。',
              'scope': '固定行情代际已留存的指定日来源板块指数；历史成员缺失不清空指数行情，目录名称不是历史成分证明。'}
    document = reference.get('_supplement', {})
    if not isinstance(document.get('sector_index_history'), dict) or previous_day not in document['sector_index_history']:
        return result
    try:
        from app.services.sector_history import sector_prices_for_date
        prices = sector_prices_for_date(document, previous_day)
        rows = prices.get('items', [])
        items = []
        for row in rows[:MAX_SECTORS]:
            item = {key: row.get(key) for key in (
                'topic_code', 'name', 'kind', 'date', 'quote_date', 'previous_date', 'source',
                'close', 'previous_close', 'change_pct', 'amount', 'normalization_version', 'request_signature')}
            item['instrument_id'] = row['topic_code']
            items.append(item)
        result.update(items=items, observed_count=len(items),
                      state='observed_sample' if items else 'unknown',
                      reason='只按同来源代码与本次午间板块报价对照；无历史成员时不推断板块内部承接。' if items else result['reason'])
        if len(rows) > MAX_SECTORS:
            result.update(state='partial', reason='严格前日板块指数超过有界留存容量，其余保持缺失。')
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return result


def _prior_cohort(previous_day, source, quotes):
    result = {'date': previous_day, 'state': 'unknown', 'source_count': None, 'observed_count': 0,
              'current_valid': 0, 'up': None, 'down': None, 'flat': None, 'items': [],
              'scope': '严格前一交易日来源涨停样本与本次午间报价交集，不等同全部昨日强势股',
              'reason': '未取得可与严格前日收盘核验的涨停样本；不按涨幅阈值猜测涨停'}
    result['source_count'] = source['source_count']
    for row in source['items']:
        symbol = row['instrument_id']
        close = row['previous_close']
        current = quotes.get(symbol, {})
        latest = _number(current.get('latest_price', current.get('last_price')))
        pct = _number(current.get('change_pct', current.get('price_change_ratio_pct')))
        comparable = latest is not None and latest > 0 and pct is not None
        result['items'].append({'instrument_id': symbol, 'name': row.get('name') or symbol,
            'previous_close': close, 'latest_price': latest if latest is not None and latest > 0 else None,
            'change_pct': pct if comparable else None, 'quote_at': None,
            'previous_board_count': row['previous_board_count'],
            **{key: row.get(key) for key in ('theme_text', 'theme_date', 'theme_source', 'theme_reason')},
            'reason': None if comparable else '本次午间报价缺失或不可比，不计作零收益或断板',
            'source_note': '前日事件价已与同日收盘核对；当日涨幅采用来源前收口径，不判收盘晋级。'})
    valid = [r['change_pct'] for r in result['items'] if r['change_pct'] is not None]
    result.update(observed_count=len(result['items']), current_valid=len(valid),
                  state='observed_sample' if valid else 'unknown',
                  up=sum(v > 0 for v in valid) if valid else None,
                  down=sum(v < 0 for v in valid) if valid else None,
                  flat=sum(v == 0 for v in valid) if valid else None,
                  reason='只统计已核验样本与本次有效报价交集；缺失保留未知')
    return result


def build_previous_review(market, day, previous_day, known_at_ms, quotes):
    result = {'date': previous_day, 'state': 'calendar_unknown', 'snapshot_id': None,
              'recorded_at_ms': None, 'content_sha256': None, 'payload': None,
              'mainlines': [], 'observations': [], 'summary': [], 'gaps': [],
              'known_at_ms': known_at_ms,
              'notice': '复盘原文与当时记录时间随午评固定保存；午间表现不等于收盘兑现。'}
    if not isinstance(previous_day, str) or previous_day >= day:
        result['gaps'].append('来源日历未能确定严格前一市场交易日，昨日对照保持未知。')
        return result
    retained, mainlines, gaps = _prior_material(previous_day, known_at_ms)
    from app.services.noon_close_context import freeze_previous_close_summary
    result['close_review_summary'] = freeze_previous_close_summary(previous_day, known_at_ms)
    result['previous_noon'] = _previous_noon(previous_day, known_at_ms)
    facts, previous_quotes, fact_gaps = _background(market, previous_day)
    result.update(mainlines=mainlines, gaps=gaps + fact_gaps)
    if retained:
        result.update(retained, state='retained')
        result['summary'].append('引用采集开始前已保存的上个交易日复盘版本，实际保存时点单独标注。')
        atmosphere = retained['payload'].get('atmosphere', {})
        if isinstance(atmosphere, dict) and isinstance(atmosphere.get('summary'), str):
            result['summary'].append(atmosphere['summary'])
    elif mainlines:
        result['state'] = 'retained'
        result['recorded_at_ms'] = max(row['recorded_at_ms'] for row in mainlines)
        result['content_sha256'] = sha256(_encoded(mainlines).encode()).hexdigest()
        result['summary'].append('未留存整份复盘；只引用当时已经保存的逐股主线原文，不能视为完整昨日计划。')
    elif facts:
        result.update(state='background_only', payload=facts)
        result['summary'].append('没有留存上个交易日复盘，仅列严格前日的收盘事实背景。')
    else:
        result['state'] = 'missing'
        result['gaps'].append('未找到采集开始前已保存的上个交易日复盘或同日盘后事实。')
    if facts and retained:
        result['background'] = facts
    indexed = _quote_index(quotes)
    reference, source_snapshot, source_fingerprint, source_gaps = _source_inputs(market, previous_day)
    result['gaps'].extend(source_gaps)
    result['event_cohorts'] = _event_cohorts(reference, previous_day, previous_quotes)
    result['sector_background'] = _sector_background(reference, previous_day, source_snapshot, source_fingerprint)
    result['cohort_feedback'] = _prior_cohort(previous_day, result['event_cohorts']['limit_up'], indexed)
    candidates = {row['instrument_id']: {'instrument_id': row['instrument_id'],
                  'name': row['instrument_name'], 'mainline': row.get('display_mainline'),
                  'source_note': '前日用户主线修订原文；不代表已验证上涨因果。'} for row in mainlines}
    for row in _watchlist(retained):
        symbol = row.get('instrument_id') or row.get('thscode')
        if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
            candidates.setdefault(symbol, {'instrument_id': symbol, 'name': row.get('name') or symbol,
                                          'mainline': None, 'source_note': '前日留存复盘中的明确观察股票。'})
    for symbol, candidate in list(candidates.items())[:MAX_OBSERVATIONS]:
        quote = indexed.get(symbol, {})
        latest = _number(quote.get('latest_price', quote.get('last_price')))
        pct = _number(quote.get('change_pct', quote.get('price_change_ratio_pct')))
        previous = previous_quotes.get(symbol, {})
        close = _number(previous.get('close'))
        priced = latest is not None and latest > 0
        valid = priced and pct is not None
        result['observations'].append({**candidate, 'latest_price': latest if priced else None,
            'change_pct': pct if valid else None, 'previous_close': close if close is not None and close > 0 else None,
            'date': day, 'previous_date': previous_day, 'quote_at': None,
            'reason': None if valid else '本次午评缺少该股可核验报价，不填0或当作下跌。',
            'comparison_note': '当日涨幅使用本次来源前收口径；不直接以未复权前日收盘计算跨日收益。'})
    if len(candidates) > MAX_OBSERVATIONS:
        result['gaps'].append(f'观察股票超过{MAX_OBSERVATIONS}只，本次显示有界对照样本。')
    result['summary'].append('自由文字保留原文，不自动判定兑现或证伪；当前成员和今天归因不回填昨天。')
    return result
