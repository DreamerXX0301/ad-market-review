"""Whole closing-limit ladders and explicit, three-state local classifications.

No price-change cutoff substitutes for closing-pool evidence. Topic membership
is resolved for the observation day rather than borrowed from today's library.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import math
import re

from app.services.market_history import resolve_reference
from app.services.strategy_limits import quote_index
from app.services.ladder_age import BASIS as AGE_BASIS, quote_age_evidence
from app.services.chart_patterns import attach_chart_patterns
from app.services.stock_board_presentation import build_board_presentation, down_state, pattern_fields, retained_proof, source_board_events
from app.services.market_mainline_synthesis import ALGORITHM_VERSION as MAINLINE_VERSION, build_system_mainlines

REGIME_SOURCES = [
    {'board': 'chinext', 'effective_since': '2020-08-24', 'url': 'https://www.szse.cn/aboutus/trends/news/t20200821_580924.html'},
    {'board': 'star', 'effective_since': '2019-07-22', 'url': 'https://www.sse.com.cn/star/media/news/c/c_20190719_4866789.shtml'},
]


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _symbol(event, by_code):
    formal = event.get('instrument_id') or event.get('thscode')
    if isinstance(formal, str) and re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', formal):
        return formal
    code = event.get('ticker') or event.get('code')
    return by_code.get(code) if isinstance(code, str) else None


def _time(value):
    if not isinstance(value, str): return None
    if re.fullmatch(r'\d{6}', value): value = ':'.join((value[:2], value[2:4], value[4:]))
    if not re.fullmatch(r'\d{2}:\d{2}(:\d{2})?', value): return None
    parts = [int(part) for part in value.split(':')]
    return value if parts[0] < 24 and parts[1] < 60 and (len(parts) == 2 or parts[2] < 60) else None


def _events(proof, day, by_code):
    """Keep source metadata separate from the independently checked event state."""
    found = {}
    for record, _, source, path, *_ in proof._records(day, 'up'):
        raw = record.get('items')
        for event in raw if isinstance(raw, list) else []:
            if not isinstance(event, dict): continue
            symbol = _symbol(event, by_code)
            if symbol:
                found.setdefault(symbol, {}).update({key: value for key, value in event.items() if value is not None})
                found[symbol].update(source=source, source_path=path)
    return found


def _pattern(*states):
    """AND of (observed, expected) pairs, with unknown preserved."""
    if any(observed is not None and observed is not expected for observed, expected in states): return False
    return None if any(observed is None for observed, _ in states) else True


def twenty_cm_state(symbol, day, identity, calendar, confirmed, age_evidence=None):
    """Confirmed closing event + dated board rule, excluding IPO no-limit days.

    Current ST names and rounded returns are deliberately unused. At least six
    retained market sessions after the verified listing date prove that the
    first-five-session exemption has elapsed, even when listing predates the
    retained calendar. With no listing date, five prior valid traded dates may
    prove that display-only lower bound without inventing the listing date.
    """
    if confirmed is not True: return None, '当日收盘涨停事件尚未核验，不能判断20cm涨停'
    if symbol.endswith('.SZ') and symbol.startswith(('300', '301')):
        effective = '2020-08-24'; board = '创业板'
    elif symbol.endswith('.SH') and symbol.startswith(('688', '689')):
        effective = '2019-07-22'; board = '科创板'
    elif identity and identity.get('kind', 'stock') == 'stock':
        return False, '证券代码所属板块不是本分类的创业板或科创板20%制度'
    else:
        return None, '缺少可确认的证券身份，不按涨幅推测20cm'
    if day < effective: return False, f'观察日早于{board}20%制度生效日期{effective}'
    listing = identity.get('list_date')
    try:
        listing = date.fromisoformat(listing).isoformat()
    except (TypeError, ValueError):
        proof=age_evidence or {};dates=proof.get('observed_trade_dates')
        if (identity.get('list_date') in (None,'') and proof.get('state')=='proven'
                and proof.get('basis')==AGE_BASIS and proof.get('instrument_id')==symbol
                and proof.get('observation_date')==day and isinstance(dates,list) and len(dates)==5
                and len(set(dates))==5 and all(item in calendar and item<day for item in dates)):
            return True, f'已核验当日收盘涨停；{board}20%制度已生效，且观察日前已有五个不同有效成交日；仅证明已过新股前五日，真实上市日期仍未知'
        return None, '上市日期未知，无法排除上市前五个交易日无涨跌幅限制'
    if listing > day: return None, '上市日期晚于观察日，证券身份存在冲突'
    sessions = [item for item in calendar if listing <= item <= day]
    if len(sessions) < 6:
        return None, '未能证明上市后已超过五个交易日，保留新股无涨跌幅限制例外'
    return True, f'已核验当日收盘涨停；{board}自{effective}执行20%制度，且已超过上市前五个交易日'


def _coverage(day, previous, before, states, reason):
    counts = Counter('confirmed' if state is True else 'known_false' if state is False else 'unknown' for state in states)
    return {'date': day, 'previous_date': previous, 'before_previous_date': before,
            'examined_count': len(states), 'confirmed_count': counts['confirmed'],
            'known_false_count': counts['known_false'], 'unknown_count': counts['unknown'],
            'complete': counts['unknown'] == 0, 'reason': reason}


def read_ladder(market, status, day, reference, rows, current_events, *, evidence=None):
    if evidence is None:
        return build_ladder(market, status, day, reference, rows, current_events)
    from app.infrastructure.storage import digest
    from app.services.pane_read_cache import read_block

    membership = resolve_reference('sectors', day, status, reference)
    return read_block('closing-ladder', day, (evidence, digest(membership), MAINLINE_VERSION),
                      lambda: build_ladder(market, status, day, reference, rows, current_events,
                                           membership=membership), market=market)


def build_ladder(market, status, day, reference, rows, current_events, *, include_patterns=True, membership=None):
    calendar = sorted(set(value for value in market.calendar() if value <= day))
    position = calendar.index(day)
    previous = calendar[position - 1] if position else None
    before = calendar[position - 2] if position > 1 else None
    master = {row['instrument_id']: row for row in market.instruments(limit=10000) if row.get('instrument_id')}
    by_code = {}
    for symbol in master:
        code = symbol[:6]
        by_code[code] = symbol if code not in by_code else None
    proof = retained_proof(reference, status.get('snapshot_id'))
    # panel() already read the exact generation and supplement. Reuse both.
    event_days = {target: _events(proof, target, by_code) for target in (day, previous, before) if target}
    event_days.setdefault(day, {})
    for event in current_events.get('limitup', []):
        symbol = _symbol(event, by_code)
        if symbol: event_days[day].setdefault(symbol, {}).update(event)
    # A ladder-only archive is still a useful candidate list, never proof of a
    # missing first board, raw close, or closing status.
    archive = next((item for item in reference.get('ladder', {}).get('item', []) if item.get('date') == day), {})
    for group in archive.get('boards', {}).values():
        for event in group:
            symbol = _symbol(event, by_code)
            if symbol: event_days[day].setdefault(symbol, dict(event))
    candidates = sorted(set(event_days[day]) | set(event_days.get(previous, {})))
    method = getattr(market, 'history_for_instruments', None)
    histories = method(candidates, start=before or previous or day, end=day) if candidates and callable(method) else {}
    quotes = {symbol: quote_index(row for row in histories.get(symbol, []) if row.get('date') in calendar[-3:]) for symbol in candidates}
    # Exact current-day panel quotes can only fill an absent history method,
    # never replace a duplicate/missing day in an available history dataset.
    current_rows = {}
    for row in rows:
        if row.get('date', row.get('trade_date')) != day: continue
        symbol = row.get('instrument_id')
        current_rows[symbol] = None if symbol in current_rows else row
    if not callable(method):
        for symbol in candidates: quotes.setdefault(symbol, {})[day] = current_rows.get(symbol)
    states = {}
    for symbol in candidates:
        states[symbol] = {target: proof._state(symbol, target, quotes.get(symbol, {}).get(target)) for target in (day, previous, before) if target}
    downs = {symbol: down_state(proof, symbol, day, quotes.get(symbol, {}).get(day), states[symbol][day]) for symbol in candidates}
    age_candidates=[symbol for symbol in event_days[day]
                    if states.get(symbol,{}).get(day,{}).get('state') is True
                    and master.get(symbol,{}).get('list_date') in (None,'')
                    and ((symbol.endswith('.SZ') and symbol.startswith(('300','301')))
                         or (symbol.endswith('.SH') and symbol.startswith(('688','689'))))]
    ages=quote_age_evidence(market,status,age_candidates,calendar,day)
    # Reuse the chart's pure pattern evaluator, but only for this observation
    # day and the already-batched three-day raw quotes. No per-stock chart or
    # full-history read is needed for a card badge. A generation change keeps
    # these additive display facts unknown rather than mixing publications.
    patterns = {}
    presentation_snapshot = status.get('snapshot_id')
    if proof.snapshot_id != presentation_snapshot or market.status().get('snapshot_id') != presentation_snapshot:
        presentation_snapshot = None
    if include_patterns:
        pattern_snapshot = status.get('snapshot_id')
        if proof.snapshot_id != pattern_snapshot or market.status().get('snapshot_id') != pattern_snapshot:
            pattern_snapshot = None
        for symbol in candidates:
            pattern_rows = [dict(value) for value in quotes.get(symbol, {}).values() if value is not None]
            quote = next((value for value in pattern_rows if value.get('date') == day), {})
            current = current_rows.get(symbol) or {}
            symbol_snapshot = pattern_snapshot
            if current.get('snapshot_id') is not None and current['snapshot_id'] != pattern_snapshot:
                symbol_snapshot = None
            # history_for_instruments intentionally returns raw Parquet columns
            # without joined action flags. The selected-day panel already has
            # those fixed-generation flags; copy only this metadata after all
            # source OHLCV values and the exact snapshot/date agree. Never use
            # the panel to replace an absent/duplicate historical quote.
            if (quote and current.get('snapshot_id') == pattern_snapshot
                    and current.get('date') == day
                    and all(current.get(key) == quote.get(key) for key in ('open', 'high', 'low', 'close', 'volume'))):
                for key in ('corporate_action', 'corporate_action_coverage_known'):
                    if key not in quote:
                        quote[key] = current.get(key)
                    elif key in current and quote[key] != current[key]:
                        quote['corporate_action_coverage_known'] = False
            chart = {'instrument': master.get(symbol, {}), 'snapshot_id': symbol_snapshot,
                     'as_of': day, 'period': 'D',
                     'bars': [{'date': day, 'low': quote.get('low'), 'close': quote.get('close')}]}
            evaluated = attach_chart_patterns(chart, raw_rows=pattern_rows,
                                             calendar=calendar, closing_states={day: {'up': states[symbol][day]}},
                                             evaluation_dates=[day])
            patterns[symbol] = {key: evaluated['bars'][0][key] for key in ('pattern_events', 'pattern_status')}
            patterns[symbol].update(pattern_date=day, pattern_as_of=day,
                                    pattern_coverage=evaluated['pattern_coverage'])
    local, membership_coverage = (membership if membership is not None
                                  else resolve_reference('sectors', day, status, reference))
    topics = local.get('topics', []) if membership_coverage.get('selected_date_available') else []
    by_member = {}
    for topic in topics:
        item = {key: topic.get(key) for key in ('topic_id', 'name', 'kind', 'observed_date', 'source')}
        item['member_lookup'] = {'topic_id': topic.get('topic_id'), 'kind': topic.get('kind', 'concept')}
        for symbol in set(topic.get('instrument_ids', [])): by_member.setdefault(symbol, []).append(item)

    def state(symbol, target):
        return states.get(symbol, {}).get(target, {'state': None, 'status': 'unknown', 'reason': '缺少前序市场交易日或当日原始报价', 'evidence': []})

    def row_for(symbol, target=day):
        event = event_days.get(target, {}).get(symbol, {})
        current = current_rows.get(symbol) or {}
        identity = master.get(symbol, {})
        first = _time(event.get('first_limit_time'))
        last = _time(event.get('last_limit_time') or event.get('limit_up_time'))
        row = {**current, **patterns.get(symbol, {}), 'snapshot_id': status.get('snapshot_id'),
                'instrument_id': symbol, 'code': symbol[:6], 'name': current.get('name') or event.get('name') or identity.get('name') or symbol,
                'date': day, 'board_count': event.get('continue_day_cnt', event.get('board_count', event.get('board_num'))),
                'limit_time': last, 'first_limit_time': first, 'last_limit_time': last,
                'reason': event.get('limit_up_reason'), 'seal_money': event.get('seal_money'),
                'break_count': event.get('break_count'), 'turnover_rate': event.get('turnover_ratio_pct', event.get('turnover_rate', current.get('turnover_rate'))),
                'details_source': event.get('details_source', event.get('source')),
                'source_industry': event.get('source_industry'), 'topics': by_member.get(symbol, []),
                'topic_membership_reason': None if by_member.get(symbol) else '该盘面日未留存可关联题材成员；未用当前题材回填历史',
                'core_role': '未评级', 'core_role_reason': '连板数不自动等同于板块龙头；尚未添加人工评级',
                'limit_up_state': state(symbol, day)['state'], 'limit_up_evidence': state(symbol, day),
                'previous_limit_up_state': state(symbol, previous)['state'], 'before_previous_limit_up_state': state(symbol, before)['state']}
        own = quotes.get(symbol, {})
        presentation_patterns = patterns.get(symbol, {})
        # The legacy pattern_* fields intentionally retain their original
        # up-only proof input. A new down shape belongs only to the additive
        # display object, evaluated from this same already-read raw batch.
        if include_patterns and downs[symbol].get('state') is True:
            presentation_patterns = pattern_fields(symbol, identity, day, presentation_snapshot,
                                                   calendar, own, current, state(symbol, day), downs[symbol])
        row['board_presentation'] = build_board_presentation(
            row, symbol=symbol, day=day, snapshot_id=presentation_snapshot, calendar=calendar,
            quotes=own, events=[event] if event else [],
            previous_events=[event_days[previous][symbol]] if symbol in event_days.get(previous, {}) else [],
            break_events=current_events.get('breakpool', []), up=state(symbol, day),
            previous_up=state(symbol, previous), before_previous_up=state(symbol, before),
            down=downs[symbol], patterns=presentation_patterns, board_text_events=source_board_events(proof,symbol,day))
        return row

    items = []; unverified = []; rebounds = []; failed = []; twenties = []
    rebound_states = []; failed_states = []; twenty_states = []
    for symbol in sorted(event_days[day]):
        row = row_for(symbol)
        if state(symbol, day)['state'] is not True:
            row['unverified_reason'] = state(symbol, day)['reason']; unverified.append(row); continue
        rebound = _pattern((True, True), (state(symbol, previous)['state'], False), (state(symbol, before)['state'], True))
        twenty, twenty_reason = twenty_cm_state(symbol, day, master.get(symbol, {}), calendar, True, ages.get(symbol))
        row.update(rebound_state=rebound, rebound_reason='T收盘涨停、T-1确认未涨停、T-2收盘涨停的反包候选；不等同于K线实体完全包住' if rebound is True else '前两交易日状态仍有未知，不能认定反包候选' if rebound is None else '不符合涨停—非涨停—涨停候选序列',
                   twenty_cm_state=twenty, twenty_cm_reason=twenty_reason,twenty_cm_age_evidence=ages.get(symbol))
        rebound_states.append(rebound); twenty_states.append(twenty)
        items.append(row)
        if rebound is True: rebounds.append(dict(row))
        if twenty is True: twenties.append(dict(row))
    for symbol in sorted(event_days.get(previous, {})):
        value = _pattern((state(symbol, previous)['state'], True), (state(symbol, day)['state'], False))
        failed_states.append(value)
        if value is True:
            row = row_for(symbol)
            prior_event = event_days[previous][symbol]
            row.update(previous_board_count=prior_event.get('continue_day_cnt', prior_event.get('board_count', prior_event.get('board_num'))),
                       previous_board_date=previous,
                       previous_limit_time=_time(prior_event.get('last_limit_time') or prior_event.get('limit_up_time')),
                       failed_state=True, failed_reason='上一交易日已确认收盘涨停；今日有效原始报价及完整来源池证明未涨停',
                       previous_limit_up_evidence=state(symbol, previous))
            failed.append(row)
    key = lambda row: (-(_number(row.get('board_count')) or 0), row.get('first_limit_time') or row.get('last_limit_time') or '99:99', row['instrument_id'])
    for collection in (items, rebounds, twenties): collection.sort(key=key)
    failed.sort(key=lambda row: (-(_number(row.get('previous_board_count')) or 0), row['instrument_id']))
    confirmed = {row['instrument_id']: row for row in items}
    theme_counts = []
    member_states = {}
    for topic in topics:
        members = set(topic.get('instrument_ids', [])); hits = [confirmed[symbol] for symbol in members if symbol in confirmed]
        if not hits: continue
        known = 0
        for symbol in members:
            if symbol not in member_states:
                member_states[symbol] = proof._count_state(symbol, day, current_rows.get(symbol))
            value, _ = member_states[symbol]
            known += value is not None
        complete = topic.get('members_complete') is True and known == len(members)
        first = [row['first_limit_time'] for row in hits if row.get('first_limit_time')]
        last = [row['last_limit_time'] for row in hits if row.get('last_limit_time')]
        theme_counts.append({'topic_id': topic.get('topic_id'), 'name': topic.get('name'), 'kind': topic.get('kind', 'concept'),
                             'limit_up_count': len(hits) if complete else None, 'limit_up_observed_count': len(hits),
                             'member_count': len(members), 'unknown_member_count': len(members) - known,
                             'members_complete': topic.get('members_complete') is True, 'complete': complete,
                             'membership_observed_date': topic.get('observed_date'),
                             'first_limit_time': min(first) if first else None, 'last_limit_time': max(last) if last else None,
                             'first_limit_time_complete': len(first) == len(hits), 'last_limit_time_complete': len(last) == len(hits),
                             'time_scope': '该题材已确认涨停样本中已留存的最早首次封板/最晚最后封板时间',
                             'member_lookup': {'topic_id': topic.get('topic_id'), 'kind': topic.get('kind', 'concept')},
                             'reason': None if complete else '成员或逐股来源池/有效报价未完整；已确认家数只为下界'})
    theme_counts.sort(key=lambda row: (-row['limit_up_observed_count'], str(row.get('topic_id'))))
    def complete_source(target):
        if not target: return False
        complete = False
        for record, _, _, _, granular, allow_absence in proof._records(target, 'up'):
            items = record.get('items')
            if not isinstance(items, list) or not items: continue
            symbols = [_symbol(item, by_code) if isinstance(item, dict) else None for item in items]
            # An unresolvable candidate cannot silently disappear from the
            # denominator, even if another source proves generic absence.
            if any(symbol is None for symbol in symbols): return False
            if (not allow_absence or record.get('date') not in (None, target)
                    or record.get('pool_kind') not in (None, 'limitup')
                    or record.get('complete') is not True or record.get('quarantine')
                    or record.get('rejected_codes')): continue
            if granular:
                raw = record.get('raw_codes')
                accepted = [symbol[:6] for symbol in symbols]
                if (record.get('pagination_complete') is not True
                        or record.get('ambiguous_code_rows') != 0
                        or not isinstance(raw, list)
                        or any(not isinstance(code, str) or not re.fullmatch(r'\d{6}', code) for code in raw)
                        or len(raw) != len(set(raw)) or len(accepted) != len(set(accepted))
                        or set(raw) != set(accepted)): continue
            complete = True
        return complete
    source_complete = complete_source(day)
    classifications = {
        'rebound': _coverage(day, previous, before, rebound_states, '当前确认涨停股中的T涨停/T-1确认非涨停/T-2涨停候选；不是K线实体包络判定'),
        'failed': _coverage(day, previous, before, failed_states, '昨日来源涨停候选中，昨日已确认涨停且今日已确认未入完整来源池；缺报价/不完整池保持未知'),
        'twenty_cm': {**_coverage(day, previous, before, twenty_states, '已确认涨停事件与当日创业板/科创板20%制度；排除无法证明已度过上市前五日者'), 'regime_sources': REGIME_SOURCES}}
    for kind, coverage in classifications.items():
        coverage['source_scope_complete'] = complete_source(previous) if kind == 'failed' else source_complete
        coverage['complete'] = coverage['complete'] and coverage['source_scope_complete']
        if not coverage['source_scope_complete']:
            coverage['reason'] += '；候选来源范围不完整，零条确认不等于全市场不存在该类股票'
    main_coverage = {'candidate_count': len(items) + len(unverified), 'confirmed_count': len(items), 'unknown_count': len(unverified),
                     'source_pool_complete': bool(source_complete), 'complete': bool(source_complete and not unverified),
                     'scope': '逐股同日原始收盘与有效成交量核验的来源收盘涨停池；全部返回，不截前N',
                     'reason': None if source_complete and not unverified else '来源池或逐股原始报价仍有缺口，不代表全A完整涨停全集'}
    system_mainlines = build_system_mainlines(
        [{**row, 'kind': master.get(row['instrument_id'], {}).get('kind', 'unknown')} for row in items],
        day=day, snapshot_id=presentation_snapshot, coverage=main_coverage,
        quotes={symbol: values.get(day) for symbol, values in quotes.items()})
    return {'items': items, 'total': len(items), 'unverified_items': unverified,
            'rebound_items': rebounds, 'failed_items': failed, 'twenty_cm_items': twenties,
            'summary': dict(Counter(str(row['board_count']) + '板' if _number(row.get('board_count')) is not None else '高度未知' for row in items)),
            'main_coverage': main_coverage, 'system_mainlines': system_mainlines,
            'classification_coverage': classifications,
            'theme_counts': theme_counts, 'theme_coverage': membership_coverage,
            'pagination_mode': 'all',
            'warnings': ['全部确认涨停行直接返回；来源或价格冲突另列待核验，不把缺数当未涨停。', '反包仅为涨停—非涨停—涨停序列候选；晋级失败必须有今日明确未涨停证据。', membership_coverage.get('notice'), '首末封板时间和换手率仅展示已留存的同日来源字段；龙头核心尚未人工评级。']}
