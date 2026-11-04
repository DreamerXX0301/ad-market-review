"""Compact evidence from one verified capture and its explicitly bound comparisons."""
from collections import Counter, defaultdict
from statistics import mean

from app.services.noon_analysis import INDEX_NAMES, board_summary, number
from app.services.noon_collection import STOCK_CODE

DETAIL_LIMIT = 10


def _by_symbol(rows):
    codes = Counter(
        row.get('instrument_id') for row in rows
        if isinstance(row, dict) and isinstance(row.get('instrument_id'), str)
    )
    return {
        row['instrument_id']: row for row in rows if isinstance(row, dict)
        and isinstance(row.get('instrument_id'), str)
        and STOCK_CODE.fullmatch(row['instrument_id']) and codes[row['instrument_id']] == 1
    }


def _change(row):
    price = number(row.get('latest_price'), positive=True)
    previous = number(row.get('prev_close'), positive=True)
    value = number(row.get('change_pct'))
    return value if (price is not None and previous is not None and value is not None
                     and abs((price / previous - 1) * 100 - value) <= .001) else None


def _counts(values):
    return {
        'up': sum(v > 0 for v in values) if values else None,
        'down': sum(v < 0 for v in values) if values else None,
        'flat': sum(v == 0 for v in values) if values else None,
    }


def _market_pulse(quotes, indices):
    source_indices = _by_symbol(indices)
    index_rows = []
    for code, name in INDEX_NAMES.items():
        row = source_indices.get(code, {})
        index_rows.append({'instrument_id': code, 'name': name,
                           'latest_price': number(row.get('latest_price'), positive=True),
                           'change_pct': _change(row)})
    changes = [value for row in quotes.values() if (value := _change(row)) is not None]
    amounts = [value for row in quotes.values()
               if row.get('currency') == 'CNY' and number(row.get('latest_price'), positive=True) is not None
               and (value := number(row.get('amount'), nonnegative=True)) is not None]
    return {
        'indices': index_rows,
        'breadth': {**_counts(changes), 'valid': len(changes), 'observed': len(quotes)},
        'turnover': {'amount': sum(amounts) if amounts else None, 'valid': len(amounts)},
    }


def _height(value):
    return value if type(value) is int and value > 0 else None


def _count(known, exact):
    return known if known or exact else None


def _pool(pool):
    if not isinstance(pool, dict) or pool.get('state') not in ('observed', 'partial'):
        return {}, False
    rows = pool.get('items')
    if not isinstance(rows, (list, tuple)):
        return {}, False
    items = _by_symbol(rows)
    exact = pool['state'] == 'observed' and len(items) == len(rows)
    return items, exact


def verified_board_pools(sealed_ids, pool_exact, break_pool):
    """Drop contradictory cross-pool observations without guessing the latest event."""
    sealed = set(sealed_ids)
    broken, break_exact = _pool(break_pool)
    conflicts = sealed & broken.keys()
    if conflicts:
        sealed.difference_update(conflicts)
        broken = {code: row for code, row in broken.items() if code not in conflicts}
    return sealed, pool_exact and not conflicts, broken, break_exact and not conflicts, conflicts


def stock_themes(sectors, members, symbols):
    """Return at most one verified industry and two specific concepts per stock."""
    if not members or not symbols:
        return {}
    wanted = set(symbols)
    topics = []
    for row in sectors:
        code, kind, name = row['instrument_id'], row.get('kind'), row.get('name')
        proof = members.get(code) or {}
        date, ids = proof.get('date'), proof.get('instrument_ids')
        if kind not in ('industry', 'concept') or not isinstance(name, str) or not name.strip() or not date or not ids:
            continue
        # A broad cross-market tag has little value as a compact membership hint.
        if kind == 'concept' and len(ids) > 200:
            continue
        topics.append((kind != 'industry', len(ids), code, name, date, ids))
    indexed = defaultdict(list)
    for is_concept, _, _, name, date, ids in sorted(topics, key=lambda row: row[:3]):
        kind = 'concept' if is_concept else 'industry'
        for symbol in wanted.intersection(ids):
            tags = indexed[symbol]
            matching = [tag for tag in tags if tag['kind'] == kind]
            if len(matching) < (2 if is_concept else 1) and all(tag['name'] != name for tag in matching):
                tags.append({'name': name, 'kind': kind, 'member_date': date})
    return indexed


def _state(exact, evidence):
    return 'observed' if exact else 'partial' if evidence else 'unknown'


def source_board_reason(event):
    reason = event.get('limit_up_reason')
    return (reason.strip()[:80] or None) if isinstance(reason, str) else None


def _sample(code, quotes, sealed, *, previous_height=None, state='sealed'):
    quote = quotes.get(code, {})
    event = sealed.get(code, {})
    return {
        'instrument_id': code,
        'name': quote.get('name') or event.get('name') or code,
        'change_pct': _change(quote),
        'previous_board_count': previous_height,
        'board_count': _height(event.get('continue_day_cnt')),
        'state': state,
    }


def _ladder(quotes, sealed, exact, conflicts, *, include_source_reason=False):
    facts = board_summary({'state': 'observed' if exact else 'partial', 'items': sealed.values()})
    unknown = facts['height_unknown_count']
    heights_exact = exact and not unknown
    group_items = defaultdict(list)
    for code in sorted(sealed):
        row = _sample(code, quotes, sealed)
        if row['board_count'] is not None:
            if include_source_reason and (reason := source_board_reason(sealed[code])):
                row['source_reason'] = reason
            group_items[row['board_count']].append(row)
    distribution = facts['distribution']
    return {
        'state': _state(exact, sealed or conflicts),
        'sealed_count': _count(len(sealed), exact),
        'height_state': 'observed' if heights_exact else 'partial' if group_items else 'unknown',
        'first_board_count': _count(facts['first_board_count'] or 0, heights_exact),
        'multi_board_count': _count(facts['multi_board_count'] or 0, heights_exact),
        'highest_board': facts['max_confirmed'],
        'unknown_height_count': _count(unknown, exact),
        'groups': [{**row, 'items': group_items[row['board_count']][:DETAIL_LIMIT]}
                   for row in distribution[:DETAIL_LIMIT]],
        'omitted_groups': max(0, len(distribution) - DETAIL_LIMIT),
    }


def _cohort_stats(rows, exact, pool_exact, *, promotions=True):
    changes = [row['change_pct'] for row in rows if row['change_pct'] is not None]
    sealed = [row for row in rows if row['state'] in ('sealed', 'promoted')]
    sealed_exact = exact and pool_exact
    promoted_exact = sealed_exact and all(
        row['previous_board_count'] is not None and row['board_count'] is not None for row in sealed
    )
    result = {
        'total': _count(len(rows), exact), 'priced': len(changes), **_counts(changes),
        'average_change_pct': round(mean(changes), 4) if changes else None,
        'sealed_count': _count(len(sealed), sealed_exact),
        'sealed_state': 'observed' if sealed_exact else 'partial' if sealed else 'unknown',
    }
    if promotions:
        promoted = sum(row['state'] == 'promoted' for row in rows)
        result.update(promotion_count=_count(promoted, promoted_exact),
                      promotion_state=_state(promoted_exact, promoted))
    return result


def _prior_session(quotes, sealed, pool_exact, prior_session, *, promotions=True):
    prior = prior_session or {}
    rows = []
    items = prior.get('items')
    has_items = isinstance(items, (list, tuple))
    prior_rows = _by_symbol(items) if has_items else {}
    for code, entry in prior_rows.items():
        previous_height = _height(entry.get('previous_board_count')) if promotions else None
        current_height = _height(sealed.get(code, {}).get('continue_day_cnt'))
        if code in sealed:
            state = 'promoted' if previous_height is not None and current_height == previous_height + 1 else 'sealed'
        else:
            state = 'not_in_verified_pool' if pool_exact and _change(quotes.get(code, {})) is not None else 'unknown'
        row = _sample(code, quotes, sealed, previous_height=previous_height, state=state)
        if row['name'] == code:
            row['name'] = entry.get('name') or code
        rows.append(row)
    rows.sort(key=lambda row: (row['state'] not in ('promoted', 'sealed'),
                              -(row['previous_board_count'] or 0), row['instrument_id']))
    complete = prior.get('complete') is True and has_items and len(prior_rows) == len(items)
    groups = {
        'first': ('前日首板', []), 'second': ('前日二板', []),
        'higher': ('前日三板及以上', []), 'unknown': ('前日高度待核验', []),
    }
    for row in rows:
        height = row['previous_board_count']
        key = 'unknown' if height is None else 'first' if height == 1 else 'second' if height == 2 else 'higher'
        groups[key][1].append(row)
    return {
        'date': prior.get('date'), 'state': 'observed' if complete else 'partial' if rows else 'unknown',
        **_cohort_stats(rows, complete, pool_exact, promotions=promotions),
        'groups': [{'key': key, 'label': label, **_cohort_stats(group, complete, pool_exact)}
                   for key, (label, group) in groups.items() if group and promotions],
        'items': rows[:DETAIL_LIMIT],
    }


def _same_day(quotes, sealed, exact, previous, previous_pool, *, individual_states=False):
    previous = previous or {}
    earlier, earlier_exact = _pool(previous_pool)
    held = sealed.keys() & earlier.keys()
    entered = sealed.keys() - earlier.keys() if earlier_exact else set()
    absent = {code for code in earlier.keys() - sealed.keys()
              if _change(quotes.get(code, {})) is not None} if exact else set()
    fully_compared = exact and earlier_exact and len(absent) + len(held) == len(earlier)
    result = {
        'state': 'observed' if fully_compared else 'partial' if held or entered or absent else 'unknown',
        'label': previous.get('label'), 'collected_end_at': previous.get('collected_end_at'),
        'held_count': _count(len(held), exact and earlier_exact),
        'entered_count': _count(len(entered), exact and earlier_exact),
        'absent_count': _count(len(absent), fully_compared),
        'held': [_sample(code, quotes, sealed) for code in sorted(held)[:DETAIL_LIMIT]],
        'entered': [_sample(code, quotes, sealed) for code in sorted(entered)[:DETAIL_LIMIT]],
        'absent': [_sample(code, quotes, sealed, state='not_in_verified_pool')
                   for code in sorted(absent)[:DETAIL_LIMIT]],
    }
    if individual_states:
        result.update(held_state=_state(exact and earlier_exact, held),
                      entered_state=_state(exact and earlier_exact, entered),
                      absent_state=_state(fully_compared, absent))
    return result


def build_intraday_feedback(stocks, *, indices=(), pool=None, previous=None, previous_pool=None,
                            prior_session=None, break_pool=None, prior_broken_session=None,
                            sectors=(), members=None):
    """All cross-session inputs must be selected and verified by the caller."""
    quotes = _by_symbol(stocks)
    sealed, exact = _pool(pool)
    sealed_ids, exact, broken, break_exact, conflicts = verified_board_pools(sealed, exact, break_pool)
    sealed = {code: row for code, row in sealed.items() if code in sealed_ids}
    result = {
        'market_pulse': _market_pulse(quotes, indices),
        'ladder': _ladder(quotes, sealed, exact, conflicts, include_source_reason=break_pool is not None),
        'relay_feedback': {
            'prior_session': _prior_session(quotes, sealed, exact, prior_session),
            'same_day': _same_day(quotes, sealed, exact, previous, previous_pool, individual_states=break_pool is not None),
        },
    }
    ladder = result['ladder']
    rows = [row for group in ladder['groups'] for row in group['items']]
    if break_pool is not None:
        ranked_breaks = []
        for code in sorted(broken):
            row = _sample(code, quotes, broken, state='broken')
            opened = broken[code].get('open_times')
            row.update(board_count=None, open_times=opened if type(opened) is int and opened >= 0 else None)
            if reason := source_board_reason(broken[code]):
                row['source_reason'] = reason
            ranked_breaks.append(row)
        ranked_breaks.sort(key=lambda row: (row['change_pct'] is None, -(row['change_pct'] or 0), row['instrument_id']))
        ladder['broken_board'] = {
            'state': _state(break_exact, broken or conflicts),
            'count': _count(len(broken), break_exact),
            'conflict_count': len(conflicts),
            'items': ranked_breaks[:DETAIL_LIMIT],
        }
        rows.extend(ladder['broken_board']['items'])
    if members is not None:
        themes = stock_themes(sectors, members, [row['instrument_id'] for row in rows])
        for row in rows:
            row['themes'] = themes.get(row['instrument_id'], [])
    if prior_broken_session is not None:
        result['relay_feedback']['prior_broken_session'] = _prior_session(
            quotes, sealed, exact, prior_broken_session, promotions=False)
    return result
