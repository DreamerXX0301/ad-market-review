"""Bounded, descriptive closing-pool failure research from retained facts only.

The paired Fuyao source pools share their declared A-share endpoint universe.
Public/annual up-only supplements cannot complete a missing broken-pool universe.
One local batch supplies at most seven sessions of event candidates. The latest
UI report can separately receive a local provider classification lookup.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
import json
import math
import re
from statistics import mean, median
from zoneinfo import ZoneInfo

from app.domain.universe import BOARDS, identity_flags
from app.services import stock_memberships
from app.services.market_history import resolve_reference

VERSION = 'broken-board-analysis-v1'
PRIOR_EVENT_VERSION = 'broken-board-prior-events-v1'
SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
SOURCE_SCOPE = 'fuyao-a-share-source-pools'
SCOPE = '扶摇同日涨停与炸板来源池，按证券去重及原始收盘核验；不是交易所全市场事件全集'
MAX_EVENT_ROWS = 3000
MAX_DAYS = 7


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _day(value):
    try:
        return value if isinstance(value, str) and date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _dates_match(row, day):
    if any(key in row and row[key] != day for key in ('date', 'trade_date')):
        return False
    for key in ('date_ms', 'trade_date_ms'):
        if key not in row:
            continue
        value = _number(row[key])
        try:
            if value is None or datetime.fromtimestamp(value / 1000, ZoneInfo('Asia/Shanghai')).date().isoformat() != day:
                return False
        except (ValueError, OverflowError, OSError):
            return False
    return True


def _identity(row):
    symbol = row.get('instrument_id') or row.get('thscode')
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        return None
    if any(key in row and row[key] != symbol for key in ('instrument_id', 'thscode')):
        return None
    if any(key in row and row[key] != symbol[:6] for key in ('ticker', 'code')):
        return None
    return symbol


def _container(reference, day, published_source=None):
    history = _mapping(reference.get('pool_history')).get(day)
    current = None
    if reference.get('pool_trade_date') == day:
        current = {key: reference[key] for key in ('limitup', 'limitdown', 'breakpool') if key in reference}
        source = reference.get('source') or (published_source if published_source == 'fuyao' else None)
        current.update(source=source, scope=reference.get('pool_scope'),
                       source_evidence='reference.source' if reference.get('source') else 'published-market-source' if source else None,
                       coverage=reference.get('pool_coverage', {}), quarantine=reference.get('pool_quarantine', {}))
    preferred_current = current and any(key in current for key in ('limitup', 'breakpool'))
    chosen = (history if preferred_current and current.get('source') is None and isinstance(history, dict)
              and history.get('source') else current if preferred_current else history)
    if not isinstance(chosen, dict):
        return {}, False
    # Two retained declarations for the same date must agree on priced events.
    conflict = False
    if current and isinstance(history, dict):
        def declaration(container, pool):
            coverage = _mapping(_mapping(container.get('coverage')).get(pool))
            source = coverage.get('source') or container.get('source')
            # An omitted source is not a contradictory provider declaration.
            if source is None and container is current and chosen is history:
                source = _mapping(_mapping(history.get('coverage')).get(pool)).get('source') or history.get('source')
            scope = coverage.get('scope') or container.get('scope') or (SOURCE_SCOPE if source == 'fuyao' else None)
            encoded = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
            rows = container.get(pool)
            # Harmless row ordering and exact repeated copies are not conflicts.
            events = sorted({encoded(row) for row in rows}) if isinstance(rows, list) else encoded(rows)
            verified = _record({**container, 'source': source}, day, pool)
            return events, source, scope, verified['complete'], verified['usable']
        for key in ('limitup', 'breakpool', 'limitdown'):
            if key in current and key in history:
                conflict |= declaration(current, key) != declaration(history, key)
    return chosen, conflict


def _record(container, day, pool, container_conflict=False):
    coverage = _mapping(_mapping(container.get('coverage')).get(pool))
    raw = container.get(pool)
    # This endpoint pair's scope is a declared application contract, never
    # borrowed from a separately collected public or annual up-only pool.
    source = coverage.get('source') or container.get('source')
    scope = coverage.get('scope') or container.get('scope') or (SOURCE_SCOPE if source == 'fuyao' else None)
    reason = None
    if not isinstance(raw, list):
        raw = []; reason = '该日来源池未留存'
    elif len(raw) > MAX_EVENT_ROWS:
        reason = '来源池超出有界核验容量，不能声称完整'
    elif container_conflict:
        reason = '同日已留存来源池声明相互冲突'
    elif not _dates_match(container, day) or not _dates_match(coverage, day):
        reason = '来源池日期与所选交易日不一致'
    elif container.get('date_semantics') not in (None, 'verified_trade_date') or coverage.get('date_semantics') not in (None, 'verified_trade_date'):
        reason = '来源日期语义未核验为交易日'
    elif not isinstance(source, str) or not source.strip() or not isinstance(scope, str) or not scope.strip():
        reason = '来源或统计范围未明确，不能确认同源分母'
    elif source != 'fuyao':
        reason = '当前研究仅核验扶摇配对事件池，其他来源不拼接分母'
    elif coverage.get('pool_kind', pool) != pool:
        reason = '来源池事件类型与当前统计不符'
    elif coverage.get('complete') is not True:
        reason = '来源池分页或完整性未核验'
    elif coverage.get('state') not in (None, 'complete'):
        reason = '来源完整性状态相互矛盾'
    elif coverage.get('pagination_complete') is False:
        reason = '来源分页未完成'
    elif (_mapping(container.get('quarantine')).get(pool) or coverage.get('quarantine')
            or coverage.get('quarantined_rows') or coverage.get('rejected_codes')
            or coverage.get('rejected_rows') or coverage.get('invalid_rows') or coverage.get('ambiguous_code_rows')):
        reason = '来源池含隔离或拒绝记录'
    elif coverage.get('declared_total') is not None and (not isinstance(coverage['declared_total'], int)
            or isinstance(coverage['declared_total'], bool) or coverage['declared_total'] != len(raw)):
        reason = '来源声明总行数与留存记录不符'
    elif coverage.get('observed_rows') is not None and coverage['observed_rows'] != len(raw):
        reason = '来源已观察总行数与留存记录不符'
    elif coverage.get('identified_rows') is not None and coverage['identified_rows'] != len(raw):
        reason = '来源未能识别全部证券身份'
    groups = {}; invalid = 0
    for row in raw[:MAX_EVENT_ROWS]:
        if not isinstance(row, dict) or not (symbol := _identity(row)):
            invalid += 1
            continue
        groups.setdefault(symbol, []).append(row)
    if invalid and not reason:
        reason = '来源记录证券身份未完全核验'
    if coverage.get('raw_codes') is not None and (not isinstance(coverage['raw_codes'], list)
            or any(not isinstance(code, str) or re.fullmatch(r'\d{6}', code) is None for code in coverage['raw_codes'])
            or set(coverage['raw_codes']) != {symbol[:6] for symbol in groups}):
        reason = '来源原始代码集合与可接受记录不一致'
    if len(raw) > MAX_EVENT_ROWS:
        invalid += len(raw) - MAX_EVENT_ROWS
    usable = (source == 'fuyao' and isinstance(scope, str) and bool(scope.strip())
              and _dates_match(container, day) and _dates_match(coverage, day) and not container_conflict
              and container.get('date_semantics') in (None, 'verified_trade_date')
              and coverage.get('date_semantics') in (None, 'verified_trade_date')
              and coverage.get('pool_kind', pool) == pool)
    return {'date': day, 'source': source, 'source_evidence': container.get('source_evidence') or 'retained-pool-source',
            'scope': scope, 'available': pool in container, 'usable': usable,
            'complete': reason is None, 'groups': groups, 'invalid': invalid,
            'raw_count': len(raw), 'reason': reason}


def _raw_quote(row, symbol, day):
    return (isinstance(row, dict) and _dates_match(row, day)
            and row.get('date', row.get('trade_date')) == day
            and all(key not in row or row[key] == symbol for key in ('instrument_id', 'thscode'))
            and row.get('price_basis', 'none') == 'none'
            and row.get('source', 'fuyao') == 'fuyao'
            and (_number(row.get('close')) or 0) > 0 and (_number(row.get('volume')) or 0) > 0)


def _event_matches(row, symbol, day, quote, source):
    prices = [row[key] for key in ('last_price', 'close') if key in row]
    return (_identity(row) == symbol and _dates_match(row, day)
            and row.get('source', source) == source and bool(prices)
            and all(_number(value) is not None and abs(value - quote['close']) <= .011 for value in prices))


def _positive(record, quotes):
    if not record['usable']:
        return {}, record['invalid'] + len(record['groups'])
    found = {}; unverified = record['invalid']
    for symbol, events in record['groups'].items():
        quote = quotes.get(symbol, {}).get(record['date'])
        if not _raw_quote(quote, symbol, record['date']) or not all(
                _event_matches(row, symbol, record['date'], quote, record['source']) for row in events):
            unverified += 1
            continue
        found[symbol] = events[0]
    return found, unverified


def _board_fields(symbol):
    """Classify an already-validated code without fetching historical identity."""
    board = 'unknown'
    if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
        code, exchange = symbol.split('.')
        candidate = identity_flags({'instrument_id': symbol, 'code': code, 'exchange': exchange})['board']
        expected = ('BJ' if candidate == 'beijing' else 'SH' if candidate == 'star'
                    else 'SZ' if candidate == 'chinext'
                    else 'SH' if candidate == 'main' and code.startswith('6')
                    else 'SZ' if candidate == 'main' else None)
        code_matches = candidate != 'beijing' or code.startswith(('4', '8', '92'))
        if candidate in BOARDS and exchange == expected and code_matches:
            board = candidate
    return {'board': board, 'board_label': BOARDS.get(board, '未知'),
            'board_scope': '当前已留存证券代码与交易所分类；不是历史身份档，不用于推断历史涨跌停制度',
            'board_reason': None if board != 'unknown' else '证券代码与交易所未能确认上市板块，不默认归为主板'}


def _item(symbol, event, quote, day, snapshot_id, outcome, source):
    high = _number(quote.get('high'))
    drawdown = (quote['close'] / high - 1) * 100 if high and high >= quote['close'] else None
    return {'instrument_id': symbol, 'code': symbol[:6], 'name': quote.get('name') or event.get('name') or symbol,
            **_board_fields(symbol),
            'date': day, 'snapshot_id': snapshot_id, 'outcome': outcome,
            'close': quote['close'], 'change_pct': _number(quote.get('change_pct')),
            'drawdown_from_high_pct': drawdown,
            'source': event.get('source') or source,
            'scope': '当日来源池与原始正成交量收盘匹配；日高至收盘回落不还原分时顺序'}


def _panel_change(symbol, day, quote, panel, before=None):
    """Reuse a dated, comparable panel percentage without borrowing pool returns."""
    if not (_raw_quote(quote, symbol, day) and _raw_quote(panel, symbol, day)
            and all(panel.get(key) == quote.get(key) for key in ('close', 'volume'))
            and panel.get('corporate_action_coverage_known') is True
            and panel.get('price_continuity') is True and panel.get('corporate_action') is not True):
        return None
    change = _number(panel.get('change_pct'))
    if before is not None:
        previous = before.get('date') if isinstance(before, dict) else None
        if not _raw_quote(before, symbol, previous):
            return None
        calculated = (quote['close'] / before['close'] - 1) * 100
        if change is not None and abs(change - calculated) > .02:
            return None
        return calculated
    return change


def _price_feedback(change):
    code = ('unknown' if change is None else 'large_drop' if change <= -5
            else 'negative' if change < 0 else 'positive' if change > 0 else 'flat')
    return {'code': code, 'label': {'positive': '正反馈', 'negative': '负反馈',
                                  'large_drop': '明显回落', 'flat': '平盘', 'unknown': '待核验'}[code],
            'change_pct': change, 'threshold_pct': -5,
            'reason': '今日收盘较昨日收盘的可比变化；明显回落采用≤−5%观察阈值，不代表实际交易亏损'
                      if change is not None else '缺少可比收盘收益，不能判正负反馈'}


def _cohort_memberships(items, *, membership_day, sample_day, status, reference, broken_only=False,
                        latest_memberships=None):
    """Resolve a dated archive, optionally filling missing kinds for the latest UI report."""
    scope = f'按{membership_day or "未知日期"}已留存行业与概念归属；不是涨跌原因，多重关联家数不可相加'
    ids = {row['instrument_id'] for row in items if not broken_only or row.get('outcome') == 'broken'}
    local, coverage = resolve_reference('sectors', membership_day, status, reference) if _day(membership_day) and ids else ({}, {})
    archive_day = coverage.get('source_snapshot_day')
    archive_valid = (coverage.get('selected_date_available') is True and _day(archive_day)
                     and archive_day <= membership_day)
    by_identity = {}; conflicts = set()
    for topic in local.get('topics', []) if archive_valid else []:
        if not isinstance(topic, dict):
            continue
        kind, source, code = topic.get('kind'), topic.get('source'), topic.get('topic_code')
        observed = topic.get('observed_date'); topic_id = topic.get('topic_id'); name = topic.get('name')
        if (kind not in ('industry', 'concept') or not isinstance(source, str) or not source.strip()
                or not isinstance(topic_id, str) or not topic_id.strip() or not isinstance(name, str) or not name.strip()
                or (code is not None and (not isinstance(code, str) or not code.strip()))
                or not isinstance(topic.get('instrument_ids'), list)
                or not _day(observed) or observed > membership_day):
            continue
        level = topic.get('classification_level', topic.get('level'))
        key = (source, kind, code or topic_id, str(level) if level is not None else None)
        members = {symbol for symbol in topic.get('instrument_ids', [])
                   if isinstance(symbol, str) and SYMBOL.fullmatch(symbol)}
        tag = {'topic_id': topic_id, 'topic_code': code, 'name': name, 'kind': kind, 'source': source,
               'classification_level': level, 'observed_date': observed,
               'members_complete': topic.get('members_complete') is True}
        value = (tag, members)
        if key in by_identity and by_identity[key] != value:
            conflicts.add(key)
        else:
            by_identity[key] = value
    for key in conflicts:
        by_identity.pop(key, None)
    tags_by_stock = {symbol: [] for symbol in ids}
    for tag, members in by_identity.values():
        for symbol in members & ids:
            tags_by_stock[symbol].append(tag)
    missing = sorted(symbol for symbol, tags in tags_by_stock.items()
                     if len({tag['kind'] for tag in tags}) < 2) if latest_memberships is not None else []
    classification_reference = None
    if missing:
        live = latest_memberships(missing)
        tags_by_stock, classification_reference = stock_memberships.merge_latest_provider_memberships(
            tags_by_stock, {symbol: live.get(symbol, []) for symbol in missing}, membership_day)
    if classification_reference:
        scope = (f'优先使用{membership_day}留存归属，缺项使用本机最新来源分类参考。'
                 '最新分类来源日单独标注，归属不代表涨跌原因，多重关联家数不可相加。')
    changes = {}; price_conflicts = set()
    for row in items:
        symbol = row['instrument_id']
        if symbol not in ids:
            continue
        change = _number(row.get('change_pct')) if row.get('date') == sample_day else None
        if symbol in changes and changes[symbol] != change:
            price_conflicts.add(symbol)
        else:
            changes[symbol] = change
    for symbol in price_conflicts:
        changes[symbol] = None
    associations = {}
    for symbol, tags in tags_by_stock.items():
        for tag in tags:
            level = tag.get('classification_level')
            key = (tag['source'], tag['kind'], tag.get('topic_code') or tag.get('source_code') or tag['topic_id'],
                   str(level) if level is not None else None)
            associations.setdefault(key, {})[symbol] = tag
    groups = {'industry': [], 'concept': []}
    for associated in associations.values():
        fallback_tags = [tag for tag in associated.values() if tag.get('classification_reference') is True]
        tag = max(fallback_tags or associated.values(),
                  key=lambda value: (value['observed_date'], value['topic_id'], value['name']))
        count = len(associated)
        valid = [changes[symbol] for symbol in sorted(associated) if changes.get(symbol) is not None]
        groups[tag['kind']].append({**tag, 'topics_date': membership_day,
            'observed_count': count, 'membership_date': tag['observed_date'],
            'membership_snapshot_day': None if fallback_tags else archive_day, 'cohort_count': len(ids),
            'sample_share_pct': count / len(ids) * 100,
            'sample_avg_change_pct': mean(valid) if valid else None,
            'sample_valid_count': len(valid), 'sample_unknown_count': count-len(valid),
            'sample_date': sample_day, 'sample_basis': 'cohort_equal_weight',
            'sample_scope': '本组关联炸板队列个股在观察日的可比涨跌等权平均；不代表行业或概念指数、实际交易收益',
            'sample_reason': None if len(valid) == count else
                '本组关联样本没有可比收盘涨跌，未知不按0计入' if not valid else
                f'仅{len(valid)}/{count}只关联样本可比，未知不按0计入均值',
            'share_scope': '已知关联数÷本页已核验炸板队列；非行业内炸板率，概念重叠不可相加'})
    for row in items:
        symbol = row['instrument_id']
        if symbol not in ids:
            continue
        tags = sorted(tags_by_stock[symbol], key=lambda tag: (tag['kind'], tag['source'], tag['name'], tag['topic_id']))
        has_reference = any(tag.get('classification_reference') is True for tag in tags)
        row.update(topics=[dict(tag) for tag in tags if tag['kind'] == 'concept'],
                   industries=[dict(tag) for tag in tags if tag['kind'] == 'industry'],
                   topics_date=membership_day,
                   topics_state='reference_partial' if has_reference else 'dated_partial' if archive_valid else 'unknown',
                   topics_scope=scope, membership_snapshot_day=archive_day if archive_valid else None)
    reason = (None if archive_valid or classification_reference else '该日行业与概念成员档案未留存，未用今日归属补历史')
    if missing and reason:
        reason = '该日行业与概念成员档案未留存，本机最新来源也没有可核验归属'
    with_industry = sum(any(tag['kind'] == 'industry' for tag in tags_by_stock[symbol]) for symbol in ids)
    with_concept = sum(any(tag['kind'] == 'concept' for tag in tags_by_stock[symbol]) for symbol in ids)
    return {'concept_distribution': sorted(groups['concept'], key=lambda row: (-row['observed_count'], row['source'], row['name'], row['topic_id']))[:10],
            'industry_distribution': sorted(groups['industry'], key=lambda row: (-row['observed_count'], row['source'], str(row['classification_level']), row['name'], row['topic_id']))[:10],
            'topics_date': membership_day, 'membership_reason': reason, 'membership_scope': scope,
            'membership_snapshot_day': archive_day if archive_valid else None,
            **({'classification_reference': classification_reference} if classification_reference else {}),
            'membership_summary': {'cohort_count': len(ids), 'industry_count': len(groups['industry']),
                'concept_count': len(groups['concept']), 'stocks_with_industry': with_industry,
                'stocks_with_concept': with_concept,
                'unclassified_count': sum(not tags_by_stock[symbol] for symbol in ids),
                'topics_date': membership_day, 'reason': reason,
                'scope': '当前完整返回的已核验炸板队列关联统计；不受排序、筛选、分页或前10标签截断影响'}}


def _summary(day, records, quotes, snapshot_id):
    up, up_unknown = _positive(records['limitup'], quotes)
    broken, broken_unknown = _positive(records['breakpool'], quotes)
    down, down_unknown = _positive(records['limitdown'], quotes)
    raw_up, raw_broken, raw_down = (set(records[key]['groups']) for key in ('limitup', 'breakpool', 'limitdown'))
    overlap = (raw_up & raw_broken) | (raw_up & raw_down) | (raw_broken & raw_down)
    for symbol in overlap:
        up.pop(symbol, None); broken.pop(symbol, None); down.pop(symbol, None)
    pair = [records['limitup'], records['breakpool']]
    same_scope = bool(pair[0]['source'] and pair[0]['scope'] and pair[0]['source'] == pair[1]['source'] and pair[0]['scope'] == pair[1]['scope'])
    complete = same_scope and all(record['complete'] for record in pair) and not (up_unknown or broken_unknown or overlap)
    observed = len(up) + len(broken)
    reason = (None if complete else '涨停与炸板来源或统计范围不一致' if all(record['available'] for record in pair) and not same_scope
              else '同日涨停、炸板或跌停来源冲突，未将重叠证券计入成功或失败' if overlap
              else '来源事件与当日原始收盘或成交量未完全核验' if up_unknown or broken_unknown
              else next((record['reason'] for record in pair if record['reason']), '来源完整性未知'))
    items = [_item(symbol, event, quotes[symbol][day], day, snapshot_id, 'limit_up', records['limitup']['source']) for symbol, event in up.items()]
    items += [_item(symbol, event, quotes[symbol][day], day, snapshot_id, 'broken', records['breakpool']['source']) for symbol, event in broken.items()]
    retracements = [row['drawdown_from_high_pct'] for row in items if row['outcome'] == 'broken' and row['drawdown_from_high_pct'] is not None]
    result = {'date': day, 'snapshot_id': snapshot_id, 'state': 'complete' if complete else 'partial' if observed else 'unknown',
              'source': pair[0]['source'] if same_scope else None, 'scope_key': pair[0]['scope'] if same_scope else None, 'scope': SCOPE,
              'limit_up_count': len(up) if complete else None, 'broken_count': len(broken) if complete else None,
              'touch_count': observed if complete else None,
              'broken_rate_pct': len(broken) / observed * 100 if complete and observed else None,
              'observed_limit_up_count': len(up), 'observed_broken_count': len(broken),
              'observed_touch_count': observed, 'overlap_count': len(overlap),
              'unverified_count': len((raw_up | raw_broken) - (set(up) | set(broken)))
                                  + records['limitup']['invalid'] + records['breakpool']['invalid'],
              'drawdown_valid_count': len(retracements),
              'avg_drawdown_from_high_pct': mean(retracements) if retracements else None,
              'large_drawdown_count': sum(value <= -5 for value in retracements) if retracements else None,
              'reason': reason, 'items': sorted(items, key=lambda row: (row['outcome'], row['instrument_id'])),
              'coverage': {key: {field: record[field] for field in ('source', 'source_evidence', 'scope', 'available', 'complete', 'raw_count', 'reason')}
                           for key, record in records.items()}}
    internal = {'up': up, 'broken': broken, 'down': down, 'overlap': overlap,
                'up_complete': records['limitup']['complete'] and up_unknown == 0 and not overlap,
                'down_complete': records['limitdown']['complete'] and down_unknown == 0 and not overlap,
                'broken_complete': records['breakpool']['complete'] and broken_unknown == 0 and not overlap,
                'records': records}
    return result, internal


def _previous_height(events):
    """All retained declarations must agree; missing height is never first board."""
    values = set()
    for event in events:
        own = [event[key] for key in ('continue_day_cnt', 'board_count', 'board_num') if event.get(key) is not None]
        if not own or any(_number(value) is None or value < 1 or int(value) != value for value in own):
            return None
        values.update(int(value) for value in own)
    return next(iter(values)) if len(values) == 1 else None


def _previous_fact(symbol, previous, prior, quotes):
    """Strict P close-verified facts for observation D, without inferring an attempt."""
    result = {'previous_event_version': PRIOR_EVENT_VERSION, 'previous_event_date': previous,
              'previous_state': 'unknown', 'previous_board_count': None, 'previous_event_source': None,
              'previous_event_reason': '严格前一交易日来源事件或原始收盘证据未齐'}
    if not previous or not prior:
        return result
    if symbol in prior['overlap']:
        return {**result, 'previous_state': 'conflict', 'previous_event_reason': '前一交易日涨跌停或炸板来源冲突，不标记板型'}
    if symbol in prior['up']:
        record = prior['records']['limitup']
        height = _previous_height(record['groups'].get(symbol, []))
        return {**result, 'previous_state': 'limit_up', 'previous_board_count': height,
                'previous_event_source': record['source'],
                'previous_event_reason': None if height is not None else '前一交易日收盘涨停已核验，但具体高度缺失或冲突'}
    if symbol in prior['broken']:
        return {**result, 'previous_state': 'limit_break', 'previous_event_source': prior['records']['breakpool']['source'],
                'previous_event_reason': None}
    up, broken = prior['records']['limitup'], prior['records']['breakpool']
    if (prior['up_complete'] and prior['broken_complete'] and up['source'] == broken['source']
            and up['scope'] == broken['scope'] and _raw_quote(quotes.get(symbol, {}).get(previous), symbol, previous)):
        return {**result, 'previous_state': 'none', 'previous_event_source': up['source'],
                'previous_event_reason': '前一交易日完整同源池未列为收盘涨停或炸板，不添加板型标记'}
    return result


def _feedback(day, previous, current, prior, quotes, current_rows, snapshot_id):
    previous = previous or None
    known = prior['broken'] if prior else {}
    complete = bool(prior and prior['broken_complete'])
    prior_record = prior['records']['breakpool'] if prior else {}
    # Each direction has its own proof. A missing down pool cannot erase a
    # complete up pool's evidence, and price-return comparability is separate.
    same_scope = {key: bool(prior_record.get('source') and prior_record.get('scope')
                           and record['source'] == prior_record['source']
                           and record['scope'] == prior_record['scope'])
                  for key, record in current['records'].items()}
    items = []
    for symbol, event in known.items():
        quote = quotes.get(symbol, {}).get(day)
        before = quotes.get(symbol, {}).get(previous)
        panel = current_rows.get(symbol)
        same = (_raw_quote(quote, symbol, day) and _raw_quote(before, symbol, previous)
                and _raw_quote(panel, symbol, day)
                and all(panel.get(key) == quote.get(key) for key in ('close', 'volume')))
        comparable = (same and panel.get('corporate_action_coverage_known') is True
                      and panel.get('price_continuity') is True and panel.get('corporate_action') is not True)
        change = (quote['close'] / before['close'] - 1) * 100 if comparable else None
        if change is not None and _number(panel.get('change_pct')) is not None and abs(change - panel['change_pct']) > .02:
            change = None
        quote_valid = _raw_quote(quote, symbol, day)
        conflicting = symbol in current['overlap']
        up_state = (None if conflicting or not same_scope['limitup'] else True if symbol in current['up']
                    else False if current['up_complete'] and quote_valid else None)
        down_state = (None if conflicting or not same_scope['limitdown'] else True if symbol in current['down']
                      else False if current['down_complete'] and quote_valid else None)
        state = ('conflict' if conflicting else 'limit_up' if up_state is True
                 else 'limit_down' if down_state is True else 'broken' if same_scope['breakpool'] and symbol in current['broken']
                 else 'not_listed' if up_state is False and down_state is False
                 and same_scope['breakpool'] and current['broken_complete']
                 else 'not_limit_up' if up_state is False else 'unknown')
        items.append({'instrument_id': symbol, 'code': symbol[:6], **_board_fields(symbol),
                      'name': (panel or {}).get('name') or event.get('name') or symbol,
                      'date': day, 'snapshot_id': snapshot_id, 'previous_date': previous,
                      'previous_close': before['close'] if before else None,
                      'close': quote['close'] if _raw_quote(quote, symbol, day) else None,
                      'change_pct': change, 'current_state': state,
                      'price_feedback': _price_feedback(change),
                      'limit_up_state': up_state, 'limit_down_state': down_state,
                      'reason': None if change is not None else '缺少严格前后交易日可比原始报价或公司行动证据'})
    gains = [row['change_pct'] for row in items if row['change_pct'] is not None]
    up = sum(row['limit_up_state'] is True for row in items)
    down = sum(row['limit_down_state'] is True for row in items)
    up_unknown = sum(row['limit_up_state'] is None for row in items)
    down_unknown = sum(row['limit_down_state'] is None for row in items)
    exact = complete and len(gains) == len(items) and not up_unknown and not down_unknown
    return {'date': day, 'snapshot_id': snapshot_id, 'previous_date': previous,
            'state': 'complete' if exact else 'partial' if items else 'unknown',
            'source_count': len(known) if complete else None, 'observed_source_count': len(known),
            'source': prior_record.get('source'), 'scope_key': prior_record.get('scope'),
            'source_unverified_count': (len(prior['records']['breakpool']['groups'])
                                        + prior['records']['breakpool']['invalid'] - len(known)) if prior else None,
            'verified_count': len(known), 'valid_count': len(gains), 'unknown_count': len(items) - len(gains),
            'event_up_unknown_count': up_unknown, 'event_down_unknown_count': down_unknown,
            'up_count': sum(value > 0 for value in gains) if gains else None,
            'down_count': sum(value < 0 for value in gains) if gains else None,
            'flat_count': sum(value == 0 for value in gains) if gains else None,
            'avg_change_pct': mean(gains) if gains else None, 'median_change_pct': median(gains) if gains else None,
            'large_drop_count': sum(value <= -5 for value in gains) if gains else None,
            'limit_up_count': up if complete and not up_unknown else None,
            'limit_down_count': down if complete and not down_unknown else None,
            'observed_limit_up_count': up, 'observed_limit_down_count': down,
            'return_limit_rate_pct': up / len(items) * 100 if complete and not up_unknown and items else None,
            'items': sorted(items, key=lambda row: (row['change_pct'] is None, row['change_pct'] or 0, row['instrument_id'])),
            'reason': None if exact else '严格前一交易日炸板全集或今日可比报价/事件状态未齐；不把缺失当作零收益',
            'scope': '昨日已核验炸板来源队列的今日可比收盘反馈，不代表实际买入可成交收益'}


def _history_item(summary):
    return {key: value for key, value in summary.items()
            if key not in ('items', 'concept_distribution', 'industry_distribution')}


def _analysis_facts(market, status, day, reference, rows, *, history_days, include_feedback,
                    with_previous_events):
    calendar_method = getattr(market, 'calendar', None)
    calendar_values = calendar_method() if callable(calendar_method) else None
    if not isinstance(calendar_values, Iterable) or isinstance(calendar_values, (str, bytes, dict)):
        calendar_values = ()
    # A missing capability is unknown, while a real calendar/read exception
    # must still propagate so an operational failure is never hidden as a gap.
    calendar = sorted({_day(value) for value in calendar_values if _day(value) and value <= day})
    calendar_reason = None if day in calendar else '未提供可核验的观察日市场交易日历，无法确认当日及前一交易日的炸板统计'
    days = calendar[-max(1, min(history_days, MAX_DAYS)):] if day in calendar else []
    calendar_previous = calendar[-2] if len(calendar) > 1 and calendar[-1] == day else None
    previous = calendar_previous if include_feedback else None
    event_previous = calendar_previous if with_previous_events else None
    required_previous = previous or event_previous
    if required_previous and required_previous not in days:
        read_days = [required_previous, *days]
    else:
        read_days = days
    records = {}
    candidates = set()
    for target in read_days:
        container, conflict = _container(reference, target, status.get('source'))
        conflict = conflict or status.get('source') not in (None, 'fuyao')
        records[target] = {key: _record(container, target, key, conflict) for key in ('limitup', 'breakpool', 'limitdown')}
        candidates.update(symbol for record in records[target].values() for symbol in record['groups'])
    method = getattr(market, 'history_for_instruments', None)
    histories = method(sorted(candidates), start=read_days[0], end=day) if candidates and read_days and callable(method) else {}
    quotes = {}
    for symbol in candidates:
        own = {}
        for row in histories.get(symbol, []):
            target = row.get('date', row.get('trade_date'))
            if target not in read_days:
                continue
            own[target] = None if target in own else row
        quotes[symbol] = own
    current_rows = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('date', row.get('trade_date')) != day:
            continue
        symbol = row.get('instrument_id')
        if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
            current_rows[symbol] = None if symbol in current_rows else row
    if not callable(method):
        for symbol in candidates:
            quotes[symbol][day] = current_rows.get(symbol)
    summaries = {}; internal = {}
    for target in read_days:
        summaries[target], internal[target] = _summary(target, records[target], quotes, status.get('snapshot_id'))
    empty_records = {key: _record({}, day, key) for key in ('limitup', 'breakpool', 'limitdown')}
    current, own = (summaries[day], internal[day]) if day in summaries else _summary(day, empty_records, {}, status.get('snapshot_id'))
    if calendar_reason:
        current.update(state='unknown', observed_limit_up_count=None, observed_broken_count=None,
                       observed_touch_count=None, overlap_count=None, unverified_count=None,
                       drawdown_valid_count=None, reason=calendar_reason)
    for row in current['items']:
        symbol = row['instrument_id']
        row['change_pct'] = _panel_change(symbol, day, quotes.get(symbol, {}).get(day), current_rows.get(symbol),
                                         quotes.get(symbol, {}).get(previous) if previous else None)
    feedback = (_feedback(day, previous, own, internal.get(previous), quotes, current_rows, status.get('snapshot_id'))
                if include_feedback else {})
    if calendar_reason and include_feedback:
        feedback['reason'] = calendar_reason
    current.update(previous_event_version=PRIOR_EVENT_VERSION, previous_event_date=calendar_previous)
    if with_previous_events:
        prior = internal.get(event_previous)
        for row in [*current['items'], *feedback.get('items', [])]:
            row.update(_previous_fact(row['instrument_id'], event_previous, prior, quotes))
    history = [_history_item(summaries[target]) for target in days]
    prior_summary = summaries.get(previous, {})
    prior_rate = prior_summary.get('broken_rate_pct')
    same_scope = (current.get('source') is not None and current.get('source') == prior_summary.get('source')
                  and current.get('scope_key') is not None and current.get('scope_key') == prior_summary.get('scope_key'))
    delta = current['broken_rate_pct'] - prior_rate if same_scope and current['broken_rate_pct'] is not None and prior_rate is not None else None
    observations = ([calendar_reason] if calendar_reason else
                    [f"当日已核验涨停{current['observed_limit_up_count']}只、炸板{current['observed_broken_count']}只；每只证券分别去重。"])
    if current['broken_rate_pct'] is not None:
        observations.append(f"同源完整触板样本中，收盘未封住比例{current['broken_rate_pct']:.1f}%。")
    else:
        observations.append('完整触板分母未齐或为零，不计算炸板率。')
    if delta is not None:
        observations.append(f'较严格前一交易日变化{delta:+.1f}个百分点；只描述封板结果，不单独判定情绪阶段。')
    if feedback.get('valid_count'):
        observations.append(f"昨日炸板队列今日{feedback['valid_count']}只报价可比，中位涨幅{feedback['median_change_pct']:+.2f}%。")
    return {'version': VERSION, 'date': day, 'snapshot_id': status.get('snapshot_id'), 'scope': SCOPE,
            'current': current, 'history': history, 'previous_feedback': feedback,
            'comparison': {'previous_date': previous, 'broken_rate_delta_pp': delta}, 'observations': observations,
            'limits': ['完整炸板率=收盘未封住证券数÷同源已核验涨停与炸板证券并集；数据不齐保留未知。',
                       '盘中开板后回封与收盘未封住分开，开板次数不是炸板家数；当前没有完整开板时序。',
                       '日高至收盘回落只描述价格；不推断洗盘、出货、资金净流出、明日方向或开仓权限。',
                       '这些为可核验描述统计，尚未形成经过历史检验的预测模型。']}


def build_analysis(market, status, day, reference, rows, *, history_days=MAX_DAYS, include_feedback=True,
                   with_memberships=False, latest_memberships=None, evidence=None):
    def facts():
        return _analysis_facts(market, status, day, reference, rows, history_days=history_days,
                               include_feedback=include_feedback, with_previous_events=with_memberships)

    if evidence is None:
        result = facts()
    else:
        from app.services.pane_read_cache import read_block

        name = 'emotion-broken-board-facts' if include_feedback else 'broken-board-day-facts'
        result = read_block(name, day, (VERSION, evidence, history_days, include_feedback, with_memberships),
                            facts, market=market)
    current, feedback = result['current'], result['previous_feedback']
    if with_memberships:
        # Classification always follows the actual dated archive or today's
        # live local reference. Only verified closing-price/event facts persist.
        memberships = _cohort_memberships(
            current['items'], membership_day=day, sample_day=day, status=status, reference=reference, broken_only=True,
            latest_memberships=latest_memberships if day == status.get('data_end') else None)
        classification_reference = memberships.pop('classification_reference', None)
        current.update(memberships)
        if classification_reference:
            result['classification_reference'] = classification_reference
        result['history'] = [_history_item(current) if row['date'] == day else row for row in result['history']]
        if include_feedback:
            feedback.update(_cohort_memberships(feedback['items'], membership_day=result['comparison']['previous_date'],
                                                sample_day=day, status=status, reference=reference))
    final_status = market.status() if callable(getattr(market, 'status', None)) else status
    if (final_status.get('snapshot_id') != status.get('snapshot_id')
            or final_status.get('source') != status.get('source')):
        return {'version': VERSION, 'date': day, 'snapshot_id': status.get('snapshot_id'), 'scope': SCOPE,
                'current': {**current, 'state': 'unknown', 'limit_up_count': None, 'broken_count': None, 'touch_count': None,
                            'broken_rate_pct': None, 'observed_limit_up_count': None, 'observed_broken_count': None,
                            'observed_touch_count': None, 'overlap_count': None, 'unverified_count': None,
                            'drawdown_valid_count': 0, 'avg_drawdown_from_high_pct': None, 'large_drawdown_count': None,
                            'concept_distribution': [], 'industry_distribution': [],
                            'membership_summary': {},
                            'membership_reason': '读取期间行情代际变化，行业与概念归属未返回',
                            'items': [], 'reason': '读取期间行情代际变化，未返回混合来源统计'},
                'history': [], 'previous_feedback': {}, 'observations': [], 'limits': ['行情代际变化，请重新读取。']}
    return result


def decorate_sectors(items, local, membership, analysis):
    """Intersect only the already-resolved selected-day constituent archive."""
    day = analysis['date']; snapshot_id = analysis['snapshot_id']; current = analysis['current']
    facts = {row['instrument_id']: row for row in current.get('items', [])}
    archive_day = membership.get('source_snapshot_day')
    archive_valid = (membership.get('selected_date_available') is True and _day(archive_day) is not None
                     and archive_day <= day)
    topics = local.get('topics', []) if archive_valid else []
    result = []
    for row in items:
        topic_id = row.get('membership_topic_id') or row.get('topic_id')
        code = row.get('topic_code')
        row_source = row.get('source') or row.get('quote_source')
        if row_source == 'archived_members':
            row_source = None
        source_conflict = (row.get('source') is not None and row.get('quote_source') not in (None, 'archived_members')
                           and row['source'] != row['quote_source'])
        row_level = row.get('classification_level', row.get('level'))
        matches = [topic for topic in topics if topic.get('kind', 'concept') == row.get('kind', 'concept')
                   and not source_conflict
                   and bool(topic_id or code)
                   and (not topic_id or topic.get('topic_id') == topic_id)
                   and (not code or topic.get('topic_code') == code)
                   and topic.get('source') == 'fuyao' and row_source in (None, 'fuyao')
                   and (row.get('kind', 'concept') != 'industry'
                        or (row_level is None and topic.get('classification_level', topic.get('level')) is None)
                        or (row_level is not None and topic.get('classification_level', topic.get('level')) is not None
                            and str(row_level) == str(topic.get('classification_level', topic.get('level')))))]
        topic = matches[0] if len(matches) == 1 else None
        raw_ids = topic.get('instrument_ids', []) if topic else []
        ids = {symbol for symbol in raw_ids if isinstance(symbol, str) and SYMBOL.fullmatch(symbol)}
        member_date = topic.get('observed_date') if topic else None
        dated = bool(topic and _day(member_date) and member_date <= day)
        details = [facts[symbol] for symbol in sorted(ids & facts.keys())] if dated else []
        up = sum(item['outcome'] == 'limit_up' for item in details)
        broken = sum(item['outcome'] == 'broken' for item in details)
        complete = (dated and topic.get('members_complete') is True and len(ids) == len(raw_ids)
                    and current['state'] == 'complete' and row_source == 'fuyao'
                    and row.get('membership_match_type') != 'cross_provider_name_proxy')
        reason = (None if complete else '该日没有可唯一定位的同源成员档案，不用当前归属回填' if not dated
                  else '成员快照不完整，只展示已知成员中的观察数' if not topic.get('members_complete')
                  else current.get('reason') or '成员身份、跨源映射或完整触板分母尚未核验')
        broken_board = {'version': VERSION, 'date': day, 'snapshot_id': snapshot_id,
                        'state': 'complete' if complete else 'partial' if details else 'unknown',
                        'limit_up_count': up if complete else None, 'broken_count': broken if complete else None,
                        'touch_count': up + broken if complete else None,
                        'broken_rate_pct': broken / (up + broken) * 100 if complete and up + broken else None,
                        'observed_limit_up_count': up if dated else None, 'observed_broken_count': broken if dated else None,
                        'observed_touch_count': up + broken if dated else None,
                        'membership_date': member_date, 'member_count': len(ids) if dated else None,
                        'reason': reason, 'details': details,
                        'scope': '所选日已留存成员与同源涨停/炸板池交集；多题材归属可重叠，各题材家数不可相加作全市场家数'}
        result.append({**row, 'broken_board': broken_board})
    return result
