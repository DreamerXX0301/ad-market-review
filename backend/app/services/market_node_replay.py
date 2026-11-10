"""Close-based ladder transitions and fixed-cohort follow-up."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date as calendar_date

from fastapi import HTTPException

from app.infrastructure.storage import digest
from app.services.chart_patterns import _quote, _regular_board_rate
from app.services.market_history import retained_topics_for_market_day
from app.services.market_ladder import (
    LADDER_VERSION, _events, complete_ladder_source, guard_ladder_up_state, ladder_action_dates, overlay_closing_events,
)
from app.services.market_mainline_synthesis import (
    ALGORITHM_VERSION as MAINLINE_VERSION, MIN_MEMBERS, classify_source_reason, has_source_reason,
)
from app.services.market_node_topics import read_overrides
from app.services.market_outlook import NORMALIZED_NON_DIRECTION_LABELS, TOPIC_FILTER_VERSION
from app.services.market_service import context, dated_events
from app.services.market_supplement import load_supplement_with_fingerprint
from app.services.pane_read_cache import read_block, snapshot_evidence
from app.services.search_matching import phonetic_index
from app.services.sentiment_context import valid_market_calendar
from app.services.stock_board_presentation import VERSION as BOARD_VERSION, _confirmed, _height, retained_proof
from app.services.stock_limit_reference import standard_limit_rate, upper_limit_at_rate
from app.services.strategy_limits import quote_index
from app.services.supplement_access import supplement_reader

MAIN_BOARD_RATE = 10
MAX_FOLLOW_UP = 5
REPLAY_VERSION = 'node-replay-reason-first-v3'
NOTICE = (
    '按当前留存证据回看指定交易日，样本范围为普通主板（10%）。'
    '反馈最多追踪五个交易日，按节点日原名单逐日追加。历史回看不代表当时已生成判断。'
)


def _iso(value):
    try:
        return calendar_date.fromisoformat(value).isoformat() == value
    except (ValueError, TypeError):
        return False


def _facts(proof, symbol, day):
    return [event for _, codes, *_ in proof._records(day, 'up')
            for event in codes.get(symbol[:6], ())]


def _height_on(proof, symbol, day, quote, state):
    return _height(_facts(proof, symbol, day), symbol, day, quote,
                   _confirmed(state, quote, symbol, day))['value']


def _is_main_board(symbol, day):
    rate = _regular_board_rate(symbol, day)
    return rate is not None and rate * 100 == MAIN_BOARD_RATE


def _dated_name(symbol, event):
    name = event.get('name') or event.get('stock_name')
    return name.strip() if isinstance(name, str) and name.strip() not in ('', symbol, symbol[:6]) else None


def _matches_mainboard_limit(day, identity, calendar, position, prices, prior_prices):
    listed = identity.get('list_date')
    if (identity.get('kind') != 'stock'
            or (identity.get('end_date') and identity['end_date'] < day)
            or not _iso(listed) or position < 5 or listed > calendar[position - 5]
            or prices is None or prior_prices is None):
        return False
    return prices['close'] == prices['high'] == upper_limit_at_rate(prior_prices['close'], MAIN_BOARD_RATE)


def _topic_identity(topic):
    kind = topic.get('kind', 'concept')
    identifier = topic.get('topic_id')
    if kind in ('industry', 'concept') and isinstance(identifier, str) and identifier.strip():
        return kind, identifier
    return None


def _event_industry(event):
    label = event.get('source_industry')
    return label.strip() if isinstance(label, str) and has_source_reason(label) else None


def _archive_fallbacks(topics, candidates):
    if not candidates:
        return {}
    identities = Counter(_topic_identity(topic) for topic in topics)
    matches = defaultdict(lambda: defaultdict(list))
    for topic in topics:
        identity = _topic_identity(topic)
        name = topic.get('name')
        if identity is None or identities[identity] != 1 or not isinstance(name, str) or not name.strip():
            continue
        kind, identifier = identity
        name = name.strip()
        if not has_source_reason(name) or name.casefold() in NORMALIZED_NON_DIRECTION_LABELS:
            continue
        for symbol in {symbol for symbol in topic['instrument_ids'] if isinstance(symbol, str)} & candidates:
            matches[symbol][kind].append({'topic_id': f'archive:{kind}:{identifier}', 'name': name, 'kind': kind,
                                          'observed_date': topic['observed_date'],
                                          'members_complete': topic.get('members_complete') is True})
    return {symbol: preferred[0] for symbol, kinds in matches.items()
            if len(preferred := (kinds.get('industry') or kinds.get('concept', []))) == 1}


def _attributions(day, heights, events, topics):
    source_reasons = {symbol: events[symbol].get('limit_up_reason') for symbol in heights
                      if has_source_reason(events[symbol].get('limit_up_reason'))}
    classified = {symbol: classify_source_reason(reason) for symbol, reason in source_reasons.items()}
    primary_counts = Counter(primary[0] for primary, _ in classified.values() if primary and primary[2] == 'direction')
    shared = {primary[0]: primary[1] for primary, _ in classified.values()
              if primary and primary[2] == 'direction' and primary_counts[primary[0]] >= MIN_MEMBERS}
    fallback = _archive_fallbacks(topics, {symbol for symbol in heights
        if symbol not in source_reasons and not _event_industry(events[symbol])})
    details, groups = {}, {}
    for symbol in heights:
        event = events[symbol]
        topic, reason_match = None, None
        if symbol in classified:
            primary, directions = classified[symbol]
            if primary and primary[2] == 'direction':
                key, name = primary[:2]
                reason_match = 'primary'
                if key not in shared:
                    # Only an explicit secondary reason can join an already shared direction.
                    # Decide using the original primaries so addition order cannot grow a group.
                    matches = {entry[0] for entry in directions if entry[0] in shared}
                    if len(matches) == 1:
                        key, = matches
                        name, reason_match = shared[key], 'related'
                topic = {'topic_id': f'reason:{key}', 'name': name, 'kind': 'concept',
                         'observed_date': None, 'members_complete': None}
            basis = 'limit_up_reason'
            raw_reason = source_reasons[symbol]
            reason = raw_reason if isinstance(raw_reason, str) else None
            observed_date = day
        else:
            industry = _event_industry(event)
            topic = ({'topic_id': f'source-industry:{industry}', 'name': industry, 'kind': 'industry',
                      'observed_date': day, 'members_complete': None} if industry else fallback.get(symbol))
            basis = 'membership_fallback' if topic else 'unknown'
            reason = None
            observed_date = topic['observed_date'] if topic else None
        details[symbol] = {'basis': basis, 'labels': [topic['name']] if topic else [],
                           'reason': reason, 'reason_match': reason_match, 'observed_date': observed_date}
        if topic:
            groups[symbol] = {**topic, 'basis': basis}
    return details, groups


def _manual_placements(day, details, groups, overrides):
    effective = dict(details)
    topics = dict(groups)
    for symbol, placement in overrides.items():
        if symbol not in details or not placement.get('topic_id'):
            continue
        automatic = details[symbol]
        effective[symbol] = {'basis': 'manual', 'labels': [placement['name']], 'reason': automatic['reason'],
                             'reason_match': None, 'observed_date': day, 'automatic': automatic,
                             'manual_override': placement}
        topics[symbol] = {**{key: placement[key] for key in ('topic_id', 'name', 'kind')}, 'basis': 'manual',
                          'observed_date': day, 'members_complete': None}
    return effective, topics


def _topic_ladders(heights, events, details, groups, source_complete):
    by_topic = defaultdict(list)
    for symbol, topic in groups.items():
        by_topic[topic['topic_id']].append(symbol)
    items, stock_ids = [], set()
    for symbols in by_topic.values():
        manual_count = sum(groups[symbol]['basis'] == 'manual' for symbol in symbols)
        if len(symbols) < MIN_MEMBERS and not manual_count:
            continue
        buckets = defaultdict(list)
        for symbol in sorted(symbols):
            buckets[heights[symbol]].append(symbol)
        representative = next((symbol for symbol in symbols if groups[symbol]['basis'] != 'manual'), symbols[0])
        items.append({**groups[representative], **({'manual_count': manual_count} if manual_count else {}), 'buckets': [
            {'height': height, 'stock_ids': buckets[height]} for height in sorted(buckets, reverse=True)]})
        stock_ids.update(symbols)
    items.sort(key=lambda row: (row['basis'] != 'limit_up_reason', row['name'], row['topic_id']))
    stocks = [{'instrument_id': symbol, 'name': _dated_name(symbol, events[symbol]) or symbol,
               'height': heights[symbol], 'reason': details[symbol]['reason'], 'reason_match': details[symbol]['reason_match'],
               **({'manual_override': details[symbol]['manual_override']} if details[symbol]['basis'] == 'manual' else {})}
              for symbol in sorted(stock_ids)]
    counts = Counter(row.get('automatic', row)['basis'] for row in details.values())
    coverage = {'confirmed_count': len(heights), 'reason_count': counts['limit_up_reason'],
                'fallback_count': counts['membership_fallback'], 'unclassified_count': counts['unknown']}
    if not heights:
        state, reason = 'unknown', '当前节点没有可核验的主板涨停样本'
    elif counts['unknown'] == len(heights) and not items:
        state, reason = 'unknown', '已核验涨停样本缺少来源原因及可用归属'
    elif not source_complete or counts['unknown'] or any(row['members_complete'] is False for row in items):
        state, reason = 'partial', '部分涨停或归类资料待核验，表中仅列已确认样本'
    else:
        state, reason = 'observed', None
    return {'state': state, 'reason': reason, 'coverage': coverage, 'stocks': stocks, 'items': items,
            'search_names': phonetic_index(row['name'] for row in items)}


def _shape(prices):
    if prices is None:
        return 'unknown'
    return 'one_price' if len(set(prices.values())) == 1 else 'not_one_price'


def _feedback(state, observed_height, expected_height):
    if state['state'] is False:
        return 'broken'
    if state['state'] is True and observed_height == expected_height:
        return 'advanced'
    return 'unknown'


def _anchor_state(anchors, complete):
    if not complete:
        return 'unknown'
    outcomes = {row['state'] for row in anchors}
    if not outcomes:
        return 'no_highest'
    if {'advanced', 'broken'} <= outcomes:
        return 'highest_diverged'
    if outcomes == {'advanced'}:
        return 'highest_advanced'
    if outcomes == {'broken'}:
        return 'highest_broken'
    return 'unknown'


@supplement_reader
def read_nodes(day, cutoff=None, *, market=None, source_guard=None):
    market, status, day = context(day, market=market)
    cutoff = cutoff or day
    latest = status['data_end']
    if not _iso(cutoff) or not day <= cutoff <= latest:
        raise HTTPException(422, '反馈截止日须介于节点日与最新已发布交易日之间')
    calendar = market.calendar()
    if not valid_market_calendar(calendar, day) or cutoff not in calendar:
        raise HTTPException(422, '反馈截止日须为已留存的市场交易日')
    position = calendar.index(day)
    prior_days = calendar[max(0, position - 2):position]
    previous = prior_days[-1] if prior_days else None
    sessions = calendar[position + 1:min(calendar.index(cutoff) + 1, position + 1 + MAX_FOLLOW_UP)]
    snapshot_id = status['snapshot_id']
    result = {'date': day, 'as_of': cutoff, 'latest_date': latest, 'previous_date': previous,
              'snapshot_id': snapshot_id, 'state': 'unknown', 'reason': None, 'groups': [],
              'topic_ladders': _topic_ladders({}, {}, {}, {}, False), 'attribution_candidates': [],
              'sessions': sessions, 'notice': NOTICE}
    if previous is None:
        return {**result, 'reason': '未留存严格前一市场交易日，无法比较旧梯队'}

    days = [*prior_days, day, *sessions]
    supplement, fingerprint = load_supplement_with_fingerprint(
        snapshot_id, dates=days, fields=('public_pools', 'annual_pools', 'annual_down_pools'))
    reference = {**market.reference(), '_supplement': supplement,
                 '_supplement_cache_source': (supplement, fingerprint) if fingerprint is not None else None}
    proof = retained_proof(reference, snapshot_id)
    master = {row['instrument_id']: row for row in market.instruments(limit=10000) if row.get('instrument_id')}
    by_code = {}
    for symbol in master:
        code = symbol[:6]
        by_code[code] = symbol if code not in by_code else None
    events = {target: {symbol: event for symbol, event in overlay_closing_events(
        _events(proof, target, by_code), dated_events(reference, target), by_code).items()
        if _is_main_board(symbol, target)} for target in (*prior_days, day)}
    candidates = sorted({symbol for dated in events.values() for symbol in dated})
    topics = {target: retained_topics_for_market_day(target) for target in (previous, day)
              if any(not has_source_reason(event.get('limit_up_reason')) and not _event_industry(event)
                     for event in events[target].values())}
    histories = None
    if source_guard is not None:
        sources = [supplement, topics, *(reference.get('pool_history', {}).get(target, {}) for target in days)]
        if reference.get('pool_trade_date') in days:
            sources.extend(reference.get(key) for key in ('limitup', 'limitdown', 'pool_coverage'))
        # AI egress policies apply to the actual inputs even on an HTTP-warmed hit.
        source_guard(sources)
        histories = market.history_for_instruments(candidates, start=days[0], end=days[-1]) if candidates else {}
        source_guard([histories, [master[symbol] for symbol in candidates if symbol in master]])
    options = {'read_connection': market.business_read_connection} if hasattr(market, 'business_read_connection') else {}
    overrides = read_overrides(day, **options)
    source = snapshot_evidence(market, status, reference)
    evidence = ((source, REPLAY_VERSION, LADDER_VERSION, BOARD_VERSION, MAINLINE_VERSION, TOPIC_FILTER_VERSION,
                 cutoff, digest(topics), digest(overrides)) if source else None)
    block = 'node-replay' if cutoff == day else 'node-replay-follow-up'
    value = read_block(block, day, evidence,
                       lambda: _build_nodes(market, result, calendar, days, master, by_code, proof, events,
                                            topics, candidates, histories, overrides),
                       market=market)
    if market.status().get('snapshot_id') != snapshot_id:
        raise HTTPException(409, '行情代际已变化，请重新打开节点回放')
    return value


def _build_nodes(market, result, calendar, days, master, by_code, proof, events, topics, candidates, histories, overrides):
    day, previous, sessions, snapshot_id = (result[key] for key in ('date', 'previous_date', 'sessions', 'snapshot_id'))
    if not candidates:
        return {**result, 'reason': '节点日及前序日期未留存可用的普通主板收盘涨停样本'}

    if histories is None:
        histories = market.history_for_instruments(candidates, start=days[0], end=days[-1])
    quotes = {symbol: quote_index(histories.get(symbol, ())) for symbol in candidates}
    action_dates = ladder_action_dates(market, candidates)
    positions = {value: index for index, value in enumerate(calendar)}

    def action_free(symbol, target):
        dates = action_dates.get(symbol)
        return dates is not None and target not in dates

    def closing(symbol, target):
        index = positions[target]
        previous_day = calendar[index - 1] if index else None
        return guard_ladder_up_state(
            proof._state(symbol, target, quotes[symbol].get(target)), symbol=symbol,
            day=target, previous_day=previous_day, calendar=calendar, quotes=quotes[symbol],
            identity=master.get(symbol, {}), snapshot_id=snapshot_id, action_dates=action_dates.get(symbol),
        )

    def quote_pair(symbol, target):
        index = positions[target]
        current, _ = _quote(quotes[symbol].get(target), symbol)
        prior, _ = _quote(quotes[symbol].get(calendar[index - 1]) if index else None, symbol)
        if current is None or prior is None or current['source'] != prior['source'] or not action_free(symbol, target):
            return None, None
        return current, prior

    def mainboard_limit_on(symbol, target):
        current, prior = quote_pair(symbol, target)
        return _matches_mainboard_limit(target, master.get(symbol, {}), calendar, positions[target],
                                        current['prices'] if current else None, prior['prices'] if prior else None)

    def observations(target):
        heights = {}
        unresolved = False
        for symbol, event in events[target].items():
            name = _dated_name(symbol, event)
            if name is None:
                unresolved = True
                continue
            if standard_limit_rate(symbol, name) != MAIN_BOARD_RATE:
                continue
            state = closing(symbol, target)
            height = _height_on(proof, symbol, target, quotes[symbol].get(target), state)
            if height and mainboard_limit_on(symbol, target):
                heights[symbol] = height
            else:
                unresolved = True
        # Yesterday's confirmed candidates can expose a missing row in today's
        # source even when that source's own page count reconciles.
        index = positions[target]
        for symbol, event in events.get(calendar[index - 1], {}).items():
            name = _dated_name(symbol, event)
            if name is not None and standard_limit_rate(symbol, name) != MAIN_BOARD_RATE:
                continue
            if symbol not in events[target] and closing(symbol, target)['status'] == 'conflict':
                unresolved = True
        return heights, unresolved

    old, previous_unresolved = observations(previous)
    new, current_unresolved = observations(day)
    if not old and not new:
        return {**result, 'reason': '未找到能核验板高和普通主板涨跌幅制度的样本'}
    old_details, _ = _attributions(previous, old, events[previous], topics.get(previous, ()))
    automatic, auto_topics = _attributions(day, new, events[day], topics.get(day, ()))
    new_details, topic_groups = _manual_placements(day, automatic, auto_topics, overrides)
    result['attribution_candidates'] = [{
        'instrument_id': symbol, 'name': _dated_name(symbol, events[day][symbol]) or symbol,
        'height': height, 'reason': automatic[symbol]['reason'],
        'automatic': ({key: auto_topics[symbol][key] for key in ('topic_id', 'name', 'kind')}
                      if symbol in auto_topics else None),
    } for symbol, height in new.items()]
    current_complete = complete_ladder_source(proof, day, by_code) and not current_unresolved
    result['topic_ladders'] = _topic_ladders(new, events[day], new_details, topic_groups, current_complete)

    def feedback(symbol, target, expected):
        state = closing(symbol, target)
        height = _height_on(proof, symbol, target, quotes[symbol].get(target), state)
        progress = _feedback(state, height, expected)
        if progress == 'advanced':
            names = {_dated_name(symbol, event) for event in _facts(proof, symbol, target)} - {None}
            rates = {standard_limit_rate(symbol, name) for name in names}
            if rates != {MAIN_BOARD_RATE} or not mainboard_limit_on(symbol, target):
                return 'unknown', '该交易日无法确认属于普通主板（10%）涨跌幅制度'
        reason = (state['reason'] or '来源连板高度与连续晋级要求不一致') if progress == 'unknown' else None
        return progress, reason

    def anchor(symbol, height):
        progress, reason = feedback(symbol, day, height + 1)
        prior, earlier = quote_pair(symbol, previous)
        current, same_pair_prior = quote_pair(symbol, day)
        original, _ = _quote(quotes[symbol].get(previous), symbol)
        return {
            'instrument_id': symbol, 'name': events[previous][symbol].get('name') or symbol, 'height': height,
            'state': progress, 'reason': reason, 'attribution': old_details[symbol],
            'change_pct': round(float(current['prices']['close'] / same_pair_prior['prices']['close'] - 1) * 100, 4)
            if current and same_pair_prior else None,
            'previous_volume_ratio': round(prior['volume'] / earlier['volume'], 4) if prior and earlier else None,
            'current_volume_ratio': round(current['volume'] / same_pair_prior['volume'], 4) if current and same_pair_prior else None,
            'previous_board_shape': _shape(original['prices'] if original else None),
        }

    def cohort(height, members):
        counts = {target: Counter() for target in sessions}
        rows = []
        for symbol in members:
            outcomes = []
            state = 'advanced'
            for step, target in enumerate(sessions, 1):
                if state != 'broken':
                    observed, _ = feedback(symbol, target, height + step)
                    if state == 'advanced' or observed == 'broken':
                        state = observed
                counts[target][state] += 1
                outcomes.append({'date': target, 'state': state})
            rows.append({'instrument_id': symbol, 'name': events[day][symbol].get('name') or symbol,
                         'attribution': new_details[symbol], 'feedback': outcomes})
        return {'height': height, 'count': len(rows), 'members': rows,
                'feedback': [{'date': target, **{key: counts[target][key] for key in ('advanced', 'broken', 'unknown')}}
                             for target in sessions]}

    previous_complete = complete_ladder_source(proof, previous, by_code) and not previous_unresolved
    heights = sorted({height for height in old.values() if height >= 2}, reverse=True)
    highest = heights[0] if heights else None
    secondary = heights[1] if len(heights) > 1 else None
    anchors = [anchor(symbol, value) for symbol, value in old.items() if value == highest]
    secondary_rows = [anchor(symbol, value) for symbol, value in old.items() if value == secondary]
    state = _anchor_state(anchors, previous_complete)
    reason = ('前一日来源池或高度有缺口，仅展示已核验高位样本' if not previous_complete else
              '高位样本的收盘状态存在缺口或证据冲突' if state == 'unknown' else None)
    cohorts = [cohort(height, [symbol for symbol, value in new.items() if value == height])
               for height in (1, 2, 3) if height in new.values()]
    result['groups'].append({'regime': 'main_10', 'state': state, 'previous_height': highest,
                             'anchors': anchors, 'secondary_height': secondary, 'secondary': secondary_rows,
                             'reason': reason, 'cohorts': cohorts})
    partial = (not current_complete or state == 'unknown'
               or any(row['state'] == 'unknown' for row in (*anchors, *secondary_rows))
               or any(row['unknown'] for cohort in cohorts for row in cohort['feedback']))
    return {**result, 'state': 'partial' if partial else 'observed',
            'reason': '部分来源或样本待核验，名单仅代表已确认范围' if partial else None}
