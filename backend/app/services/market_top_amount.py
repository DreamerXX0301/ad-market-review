"""Seven exact market-session TOP10 appearances, without writes or fetching."""
from collections import OrderedDict
from copy import deepcopy
from datetime import date
import threading
import weakref

from app.analytics.amount_rank import amount_ranking_fact, ranked_amount_rows

VERSION = 'amount-top10-active-seven-sessions-v1'
CACHE_LIMIT = 32
_cache = OrderedDict()
_lock = threading.RLock()
SCOPE = '含观察日的最近7个市场交易日；同一已留存报价样本、成交额降序及同额原序TOP10榜事实。样本不完整时仅计当前观察次数，补齐可能改变名次和次数；不代表完整历史全市场证券覆盖或资金净流入'


def clear_amount_activity_cache():
    with _lock:
        _cache.clear()


def _instance_ref(market):
    # The normal MarketData supports weak references: do not retain its large
    # full-market frame merely to cache 70 tiny rank rows. A legacy slotted
    # adapter may require a bounded strong identity reference instead.
    try:
        return weakref.ref(market)
    except TypeError:
        return lambda: market


def _day(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _window(market, day):
    source = market.calendar()
    if not isinstance(source, list) or not all(_day(value) for value in source) or source != sorted(set(source)) or day not in source:
        return [day] if _day(day) else [], False
    end = source.index(day)
    return source[max(0, end - 6):end + 1], True


def _facts(market, status, day, current_rows):
    dates, calendar_known = _window(market, day)
    key = (id(market), status.get('snapshot_id'), day, tuple(dates), VERSION)
    with _lock:
        found = _cache.get(key)
        if found is not None and found['instance']() is market:
            _cache.move_to_end(key)
            facts = deepcopy(found['facts'])
        else:
            facts = None
    if facts is None:
        if hasattr(market, 'amount_top10_history'):
            supplied = market.amount_top10_history(dates)
            valid_generation = supplied.get('snapshot_id') == status.get('snapshot_id')
            facts = supplied.get('days', {}) if valid_generation else {}
        else:
            facts = {}
            if callable(getattr(market, 'panel', None)):
                for observed_day in dates:
                    if observed_day == day:
                        continue
                    try:
                        rows = market.panel(observed_day)
                    except (KeyError, ValueError):
                        rows = []
                    rows = [row for row in rows if row.get('date', observed_day) == observed_day]
                    facts[observed_day] = amount_ranking_fact(rows, observed_day)
        facts = {observed_day: deepcopy(facts[observed_day]) for observed_day in dates
                 if observed_day <= day and observed_day in facts and facts[observed_day].get('date') == observed_day}
        with _lock:
            _cache[key] = {'instance': _instance_ref(market), 'facts': deepcopy(facts)}
            _cache.move_to_end(key)
            while len(_cache) > CACHE_LIMIT:
                _cache.popitem(last=False)
    # The visible table is authoritative for today's appearances. Its metadata
    # is not cached with historical topic labels or mutable display rows.
    facts[day] = amount_ranking_fact(current_rows, day)
    observed = [value for value in dates if facts.get(value, {}).get('items')]
    missing = [value for value in dates if not facts.get(value, {}).get('items')]
    incomplete = [value for value in dates if facts.get(value, {}).get('complete') is not True]
    complete = calendar_known and len(dates) == 7 and not incomplete
    reasons = []
    if not calendar_known:
        reasons.append('市场交易日历未核验，不能确认完整7日窗口')
    elif len(dates) < 7:
        reasons.append(f'本次仅留存{len(dates)}个窗口交易日，未凑成7日')
    if incomplete:
        reasons.append(f'{len(incomplete)}个日期的本地TOP10榜缺失或未完整核验；次数为当前已观察样本，补齐可能改变历史名次和次数')
    summary = {'algorithm_version': VERSION, 'window_start': dates[0] if dates else None,
               'window_end': day, 'expected_days': 7, 'observed_days': len(observed),
               'complete': complete, 'available_dates': observed, 'missing_dates': missing,
               'incomplete_dates': incomplete, 'scope': SCOPE,
               'reason': '；'.join(reasons) or None}
    return facts, summary


def _directions(day, top, reference, status, resolved_membership):
    active = [row for row in top if (row['active_top10_7d']['count'] or 0) >= 2]
    scope = '仅把本日TOP10中近7日当前样本内重复上榜个股，按观察日可核验成员样本归组；样本补齐可能改变次数，不把成员回填各历史上榜日，不代表上涨归因或板块资金流'
    base = {'state': 'unknown', 'items': [], 'scope': scope, 'reason': None}
    if not active:
        return {**base, 'reason': '本日榜暂无已观察到的重复上榜个股，不推断活跃方向'}
    if resolved_membership is None:
        from app.services.market_history import resolve_reference
        local, coverage = resolve_reference('sectors', day, status, reference)
    else:
        local, coverage = resolved_membership
    if coverage.get('selected_date_available') is not True:
        return {**base, 'reason': '观察日没有可核验成员快照，未用未来或日期未知成员分组'}
    groups = []
    covered = set()
    topics = local.get('topics', [])
    identities = [topic['topic_id'] for topic in topics if isinstance(topic, dict) and isinstance(topic.get('topic_id'), str)]
    duplicate_ids = {value for value in identities if identities.count(value) > 1}
    for topic in topics:
        if not isinstance(topic, dict) or not isinstance(topic.get('topic_id'), str) or topic['topic_id'] in duplicate_ids:
            continue
        observed = topic.get('observed_date')
        if not _day(observed) or observed > day or not isinstance(topic.get('instrument_ids'), list) or not isinstance(topic.get('name'), str) or not topic['name'].strip():
            continue
        members = set(value for value in topic['instrument_ids'] if isinstance(value, str))
        matching = [row for row in active if row['instrument_id'] in members]
        if not matching:
            continue
        covered.update(row['instrument_id'] for row in matching)
        groups.append({'topic_id': topic['topic_id'], 'name': topic['name'], 'kind': topic.get('kind', 'concept'),
                       'observed_date': observed, 'stock_count': len(matching),
                       'instrument_ids': [row['instrument_id'] for row in matching],
                       'items': [{'instrument_id': row['instrument_id'], 'name': row.get('name'),
                                  'count': row['active_top10_7d']['count'], 'dates': row['active_top10_7d']['dates'],
                                  'complete': row['active_top10_7d']['complete']} for row in matching],
                       'members_complete': topic.get('members_complete') is True,
                       'source_snapshot_id': coverage.get('source_snapshot_id'),
                       'membership_snapshot_day': coverage.get('source_snapshot_day'), 'scope': scope})
    groups.sort(key=lambda row: (-row['stock_count'], row['name'], row['topic_id']))
    complete = bool(groups) and len(covered) == len(active) and all(row['members_complete'] for row in groups)
    return {**base, 'state': 'available' if complete else 'partial' if groups else 'unknown',
            'items': groups, 'reason': None if complete else '仅列已核验成员关联；未覆盖股票或缺失成员不强行归类',
            'unmapped_instrument_ids': [row['instrument_id'] for row in active if row['instrument_id'] not in covered]}


def decorate_top_amount(market, status, day, current_rows, reference=None, *, resolved_membership=None):
    if not ranked_amount_rows(current_rows):
        dates, _ = _window(market, day)
        return [], {'algorithm_version': VERSION, 'window_start': dates[0] if dates else None,
                    'window_end': day, 'expected_days': 7, 'observed_days': 0, 'complete': False,
                    'available_dates': [], 'missing_dates': dates, 'incomplete_dates': dates,
                    'scope': SCOPE, 'reason': '观察日没有可列示成交额榜，不加载历史榜或推断活跃方向',
                    'directions': {'state': 'unknown', 'items': [], 'reason': '观察日没有成交额TOP10样本'}}
    facts, summary = _facts(market, status, day, current_rows)
    top = []
    for row in ranked_amount_rows(current_rows):
        symbol = row.get('instrument_id')
        appearances = []
        for observed_day in sorted(facts):
            if not summary['window_start'] or not summary['window_start'] <= observed_day <= day:
                continue
            matches = [entry for entry in facts[observed_day]['items'] if entry['instrument_id'] == symbol]
            if len(matches) == 1:
                appearances.append({'date': observed_day, 'rank': matches[0]['rank'], 'amount': matches[0]['amount']})
        count = len(appearances) if appearances else 0 if summary['complete'] else None
        state = 'complete' if summary['complete'] else 'observed_partial' if appearances else 'unknown'
        if not summary['complete'] and count is not None:
            label = ('持续活跃 · ' if count >= 2 else '')+f'已观察{count}次 · 样本不完整'
        else:
            label = (f'持续活跃 · {count}次' if count is not None and count >= 2 else
                     f'近7日 · {count}次' if count is not None else '近7日活跃待核验')
        annotation = {'count': count, 'dates': [item['date'] for item in appearances], 'ranks': appearances,
                      'observed_days': summary['observed_days'], 'expected_days': 7,
                      'complete': summary['complete'], 'state': state, 'label': label,
                      'reason': summary['reason'], 'window_start': summary['window_start'], 'window_end': day,
                      'scope': SCOPE}
        top.append({**row, 'active_top10_7d': annotation})
    summary['directions'] = _directions(day, top, reference or {}, status, resolved_membership)
    return top, summary
