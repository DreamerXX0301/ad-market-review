"""Derive noon observations exclusively from a retained snapshot's JSON facts.

This module has no database, market reader, network or strategy dependency. A
new display version can explain an old snapshot without changing its evidence
or consulting facts that became available after that capture.
"""
from collections import Counter
from datetime import date
import math
import re
from statistics import mean, median

VERSION = 'noon-workbench-v1'
POOLS = ('limit_up', 'limit_break', 'limit_down')
STATES = (*POOLS, 'not_listed', 'unknown', 'conflict')
SECTOR_CODE = re.compile(r'\d{6}\.(?:TI|SH|SZ)\Z')
LABELS = {'limit_up': '本次涨停池', 'limit_break': '本次炸板池',
          'limit_down': '本次跌停池', 'not_listed': '本次三池均未列入',
          'unknown': '池状态未知', 'conflict': '来源池状态冲突'}


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _height(value):
    number = _num(value)
    return int(number) if number is not None and number > 0 and number == int(number) else None


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _index(rows):
    counts = Counter(row.get('instrument_id') for row in rows if isinstance(row.get('instrument_id'), str))
    return {row['instrument_id']: row for row in rows
            if isinstance(row.get('instrument_id'), str) and counts[row['instrument_id']] == 1}


def _pool_states(events, day=None, previous=None, classifications=None):
    """Index each pool once; thousands of cohort rows then use constant-time lookups."""
    membership, themes = {}, {}
    for key in POOLS:
        source = events.get(key, {})
        if source.get('state') not in ('observed', 'partial'):
            continue
        for row in _rows(source.get('items')):
            symbol = row.get('instrument_id')
            if isinstance(symbol, str):
                membership.setdefault(symbol, set()).add(key)
                text = row.get('limit_up_reason')
                if isinstance(text, str) and text.strip():
                    themes.setdefault(symbol, set()).add(text.strip())
    states = {symbol: next(iter(keys)) if len(keys) == 1 else 'conflict'
              for symbol, keys in membership.items()}
    states[None] = 'not_listed' if all(events.get(key, {}).get('state') == 'observed' for key in POOLS) else 'unknown'
    states['_themes'] = {symbol: next(iter(texts)) for symbol, texts in themes.items() if len(texts) == 1}
    states['_theme_day'] = day
    states['_manual_themes'] = {row.get('instrument_id'): row['mainline'] for row in _rows((previous or {}).get('observations'))
        if isinstance(row.get('mainline'), str) and row['mainline'].strip()
        and row.get('previous_date') == (previous or {}).get('date')}
    states['_previous_day'] = (previous or {}).get('date')
    states['_classifications'] = classifications or {}
    return states


def _pool_state(symbol, states):
    return states.get(symbol, states[None])


def _classification(symbol, states):
    value = states.get('_classifications', {}).get(symbol, {})
    return {'industries': _rows(value.get('industries')), 'topics': _rows(value.get('topics')),
            'membership_state': value.get('state', 'unknown'),
            'membership_reason': value.get('reason') or '原采集代际行业/概念归属尚未核验'}


def _feedback_row(prior, quotes, events, *, allow_frozen_noon=True):
    symbol = prior.get('instrument_id')
    quote = quotes.get(symbol, prior if allow_frozen_noon else {})
    latest = _num(quote.get('latest_price'))
    latest = latest if latest is not None and latest > 0 else None
    change = _num(quote.get('change_pct')) if latest is not None else None
    theme = {'theme_text': None, 'theme_date': None, 'theme_source': None}
    if (isinstance(prior.get('theme_text'), str) and prior['theme_text'].strip()
            and prior.get('theme_date') == events.get('_previous_day')
            and prior.get('theme_source') == 'previous_session_pool'):
        theme = {key: prior.get(key) for key in theme}
    elif symbol in events.get('_manual_themes', {}):
        theme = {'theme_text': events['_manual_themes'][symbol], 'theme_date': events['_previous_day'], 'theme_source': 'previous_manual'}
    elif symbol in events.get('_themes', {}):
        theme = {'theme_text': events['_themes'][symbol], 'theme_date': events.get('_theme_day'), 'theme_source': 'current_noon_pool'}
    return {'instrument_id': symbol, 'name': prior.get('name') or quote.get('name') or symbol,
            'previous_board_count': _height(prior.get('previous_board_count')),
            'previous_close': _num(prior.get('previous_close')),
            'latest_price': latest, 'change_pct': change,
            **theme,
            **_classification(symbol, events),
            **{key: _num(quote.get(key)) for key in ('open', 'high', 'low', 'amount')},
            'current_state': _pool_state(symbol, events),
            'reason': None if change is not None else '本次快照没有可比报价，不计作零涨幅或断板'}


def _order(row):
    height = _height(row.get('previous_board_count'))
    return (height is None, -(height or 0), str(row.get('instrument_id') or ''))


def _stats(rows, *, available=True):
    valid = [_num(row.get('change_pct')) for row in rows]
    values = [value for value in valid if value is not None]
    return {'count': len(rows) if available else None, 'valid_count': len(values) if available else None,
            'unknown_count': len(rows) - len(values) if available else None,
            'up': sum(value > 0 for value in values) if values else None,
            'down': sum(value < 0 for value in values) if values else None,
            'flat': sum(value == 0 for value in values) if values else None,
            'mean_pct': mean(values) if values else None,
            'median_pct': median(values) if values else None,
            'large_drop_count': sum(value <= -5 for value in values) if values else None,
            'pool_counts': {key: sum(row.get('current_state') == key for row in rows) if available else None for key in STATES},
            'items': sorted(rows, key=_order)}


def _source(previous, key):
    source = previous.get('event_cohorts', {}).get(key)
    if isinstance(source, dict) and source.get('date') == previous.get('date'):
        return source
    # Older snapshots already retained a verified limit-up cohort. Use only
    # those frozen rows; the other historical pools cannot be reconstructed.
    if key == 'limit_up' and isinstance(previous.get('cohort_feedback'), dict):
        old = previous['cohort_feedback']
        if old.get('date') != previous.get('date'):
            return {'state': 'unknown', 'source_count': None, 'items': [], 'reason': '昨日事件样本日期不匹配'}
        return {**old, 'verified_count': old.get('observed_count'),
                'state': 'partial' if old.get('source_count') is not None else 'unknown'}
    return {'date': previous.get('date'), 'state': 'unknown', 'source_count': None,
            'verified_count': None, 'items': [], 'reason': '本份快照未冻结昨日该事件池，不能用今天的资料补写'}


def _cohort(previous, key, quotes, events):
    source = _source(previous, key)
    available = source.get('state') != 'unknown' and source.get('source_count') is not None
    event_source = previous.get('event_cohorts', {}).get(key)
    # Only the legacy limit-up cohort froze this capture's noon quotes inside
    # each prior row. Event-cohort rows are prior-day evidence, never a quote
    # fallback when the current noon stock list has no unique valid record.
    legacy_noon = key == 'limit_up' and not (
        isinstance(event_source, dict) and event_source.get('date') == previous.get('date'))
    rows = [_feedback_row(row, quotes, events, allow_frozen_noon=legacy_noon)
            for row in _index(_rows(source.get('items'))).values()]
    return {**_stats(rows, available=available), 'key': key, 'date': previous.get('date'),
            'state': source.get('state', 'unknown'), 'source_count': source.get('source_count'),
            'verified_count': len(rows) if available else None,
            'unverified_count': source.get('unverified_count'),
            'classification_counts': _classification_counts(rows, source, events.get('_theme_day'),
                height_field='previous_board_count', previous_day=previous.get('date')),
            'reason': source.get('reason'), 'scope': '前日已核验事件样本与本次午间报价交集；全部统计先于分页计算'}


def _previous_feedback(previous, quotes, events):
    result = _cohort(previous, 'limit_up', quotes, events)
    rows = result['items']
    heights = [row['previous_board_count'] for row in rows if row['previous_board_count'] is not None]
    definitions = [('first', '昨日首板', 1), ('board_2', '昨日二板', 2),
                   ('board_3', '昨日三板', 3), ('board_4', '昨日四板', 4),
                   ('board_5_plus', '昨日五板及以上', 5),
                   ('height_unknown', '高度未核验样本', None)]
    result['groups'] = [{'key': key, 'label': label, 'board_count': height,
        **_stats([row for row in rows if
            ((row['previous_board_count'] or 0) >= 5 if key == 'board_5_plus'
             else row['previous_board_count'] == height)], available=result['count'] is not None)}
        for key, label, height in definitions]
    result['max_confirmed_height'] = max(heights) if heights else None
    result['highest_items'] = [row for row in rows if row['previous_board_count'] == result['max_confirmed_height']]
    if result['max_confirmed_height'] is None:
        result['highest_items'] = []
    return result


def _pool_classification_rows(source, quotes, pool_states, *, limit_up=False):
    rows = []
    for row in _index(_rows(source.get('items'))).values():
        quote = quotes.get(row['instrument_id'], {})
        latest = _num(quote.get('latest_price'))
        latest = latest if latest is not None and latest > 0 else None
        rows.append({**row, 'name': row.get('name') or quote.get('name') or row['instrument_id'],
                     'board_count': _height(row.get('continue_day_cnt')) if limit_up else None,
                     **_classification(row['instrument_id'], pool_states),
                     **{key: _num(quote.get(key)) for key in ('amount', 'open', 'high', 'low')},
                     'latest_price': latest,
                     'change_pct': _num(quote.get('change_pct')) if latest is not None else None,
                     'current_state': _pool_state(row['instrument_id'], pool_states)})
    return rows


def _red_ratio(rows):
    """Positive noon quotes divided by all valid noon quotes, including flat."""
    values = [_num(row.get('change_pct')) for row in rows]
    values = [value for value in values if value is not None]
    up = sum(value > 0 for value in values) if values else None
    return {'up_count': up, 'valid_count': len(values),
            'red_ratio_pct': up / len(values) * 100 if values else None,
            'mean_change_pct': mean(values) if values else None}


def _classification_counts(rows, source, day, *, height_field='board_count', previous_day=None):
    """Count dated member samples, preserving each code and overlapping labels."""
    period = ('严格前一交易日已核验来源池样本的已留存行业/概念归属；板数仅为该前日已核验高度，'
              '不使用本次连板数，也不表示昨日涨停原因。' if previous_day is not None
              else '仅为本次已核验来源池样本的已留存行业/概念归属；')
    scope = (period + '同一股票可同时归属多个方向，'
             '各组家数不能相加，不代表全板块涨停家数或上涨因果。名单日期与午评日期分别保留；'
             '部分归属只列已核验范围，未知高度不归入首板，非涨停池不套用连续跌停数。'
             '平均涨跌幅为当前午评有效可比报价涨跌幅的算术均值，缺失不补0。'
             '红盘率为当前午评涨幅大于0的家数/有效可比报价家数；平盘计入分母。')
    if (source.get('state') not in ('observed', 'observed_sample', 'partial')
            or previous_day is not None and source.get('source_count') is None):
        return {'state': 'unknown', 'sample_count': None, 'classified_count': None,
                'unclassified_count': None, 'partial_classification_count': None,
                'up_count': None, 'valid_count': None, 'red_ratio_pct': None,
                'mean_change_pct': None,
                'industries': [], 'topics': [], 'scope': scope,
                'reason': source.get('reason') or '本次来源池缺少已核验样本，不能按零家统计'}
    groups, classified, partial = {}, set(), set()
    for row in rows:
        symbol = row['instrument_id']
        if row.get('membership_state') not in ('observed_sample', 'partial'):
            continue
        for field, kind in (('industries', 'industry'), ('topics', 'concept')):
            for tag in _rows(row.get(field)):
                code, observed, provider = tag.get('topic_code'), tag.get('members_observed_date'), tag.get('source')
                name = tag.get('name')
                if (not isinstance(code, str) or not SECTOR_CODE.fullmatch(code)
                        or tag.get('kind') != kind or provider != 'fuyao'
                        or not isinstance(name, str) or not name.strip()
                        or not _day(observed) or not _day(day) or observed > day):
                    continue
                identity = (field, provider, code, observed)
                group = groups.setdefault(identity, {
                    'topic_code': code, 'name': name, 'kind': kind, 'source': provider,
                    'members_observed_date': observed, '_items': {}, '_partial': False})
                # Member identity is the source code, never a same-name match;
                # duplicate tags or repeated pool identities count once.
                group['_items'][symbol] = {**{key: row.get(key) for key in (
                    'instrument_id', 'name', 'change_pct', 'current_state')}, 'board_count': _height(row.get(height_field))}
                group['_partial'] |= row.get('membership_state') == 'partial'
                classified.add(symbol)
                if row.get('membership_state') == 'partial':
                    partial.add(symbol)
    output = {'industries': [], 'topics': []}
    for (field, _, _, _), group in groups.items():
        items = sorted(group.pop('_items').values(), key=lambda row: (
            row['board_count'] is None, -(row['board_count'] or 0),
            row['change_pct'] is None, -(row['change_pct'] or 0), row['instrument_id']))
        heights = Counter(row['board_count'] for row in items if row['board_count'] is not None)
        group.update(count=len(items), max_board_count=max(heights) if heights else None,
                     board_counts=[{'board_count': height, 'count': heights[height]}
                                   for height in sorted(heights, reverse=True)], items=items,
                     unknown_board_count=sum(row['board_count'] is None for row in items),
                     **_red_ratio(items),
                     state='partial' if group.pop('_partial') or source['state'] == 'partial' else 'observed_sample')
        output[field].append(group)
    for field in output:
        output[field].sort(key=lambda group: (-group['count'], -(group['max_board_count'] or 0),
            group['topic_code'], group['members_observed_date'], group['name']))
    sample_count = len(rows)
    unclassified = sample_count - len(classified)
    state = ('partial' if source['state'] == 'partial' or partial or unclassified else 'observed_sample')
    if sample_count and not classified:
        state = 'unknown'
    return {**output, 'state': state, 'sample_count': sample_count, 'classified_count': len(classified),
            'unclassified_count': unclassified, 'partial_classification_count': len(partial),
            **_red_ratio(rows),
            'scope': scope,
            'reason': ('部分样本成员归属未核验或容量未覆盖，已列家数只描述可核验范围。'
                       if partial or unclassified else '来源池覆盖未完整核验，仅为已核验样本。'
                       if source['state'] == 'partial' else None)}


def _ladder(events, quotes, pool_states):
    source = events.get('limit_up', {})
    rows = _pool_classification_rows(source, quotes, pool_states, limit_up=True)
    heights = sorted({row['board_count'] for row in rows if row['board_count'] is not None}, reverse=True)
    return {'state': source.get('state', 'unknown'), 'items': rows,
            'classification_counts': _classification_counts(rows, source, pool_states.get('_theme_day')),
            'distribution': [{'board_count': height, 'count': sum(row['board_count'] == height for row in rows),
                              'items': [row for row in rows if row['board_count'] == height]} for height in heights],
            'unknown_count': sum(row['board_count'] is None for row in rows)
                             if source.get('state') != 'unknown' or rows else None,
            'reason': source.get('reason'),
            'scope': '本次已核验涨停池的来源连续板数；不代表收盘确认晋级，未知高度不归入首板'}


def _direction_state(previous, current):
    if previous is None or current is None:
        return 'unknown'
    if current == 0 or previous == 0:
        return 'flat'
    return ('both_up' if previous > 0 else 'turned_strong') if current > 0 else (
        'turned_weak' if previous > 0 else 'both_down')


def _watched_prior_facts(previous):
    """Join watched names only to this exact day's already verified limit-ups.

    An observation does not itself repeat event metadata. The previous cohort
    is independent evidence retained in the same snapshot, while today's pool
    and membership labels cannot establish yesterday's height or cause.
    """
    source = _source(previous, 'limit_up')
    if (source.get('state') not in ('observed', 'observed_sample', 'partial')
            or source.get('source_count') is None):
        return {}
    return _index([row for row in _rows(source.get('items'))
                   if row.get('date', previous.get('date')) == previous.get('date')])


def _watched_row(row, prior_facts, previous_day):
    """Fill omitted metadata; contradictory retained fields remain unknown."""
    proof = prior_facts.get(row.get('instrument_id'))
    if proof is None or row.get('previous_date', previous_day) != previous_day:
        return row
    result = dict(row)
    own_height, proven_height = _height(row.get('previous_board_count')), _height(proof.get('previous_board_count'))
    result['previous_board_count'] = (None if own_height is not None and proven_height is not None
                                      and own_height != proven_height else own_height or proven_height)
    if _num(row.get('previous_close')) is None:
        result['previous_close'] = proof.get('previous_close')
    valid_theme = (isinstance(proof.get('theme_text'), str) and proof['theme_text'].strip()
                   and proof.get('theme_date') == previous_day
                   and proof.get('theme_source') == 'previous_session_pool')
    if valid_theme:
        own_theme = (isinstance(row.get('theme_text'), str) and row['theme_text'].strip()
                     and row.get('theme_date') == previous_day
                     and row.get('theme_source') == 'previous_session_pool')
        if own_theme and row['theme_text'] != proof['theme_text']:
            result['theme_text'] = None
        elif not own_theme:
            result.update({key: proof.get(key) for key in ('theme_text', 'theme_date', 'theme_source')})
    return result


def _directions(payload, previous, quotes, events, membership_context=None):
    counts = (membership_context or {}).get('counts', {})
    sectors = [{**row, **counts.get(row['instrument_id'], {})} for row in _index(_rows(payload.get('sectors'))).values()]
    valid = [row for row in sectors if _num(row.get('change_pct')) is not None
             and (_num(row.get('latest_price')) or 0) > 0]
    leaders, laggards = [], []
    for kind in ('industry', 'concept'):
        subset = sorted((row for row in valid if row.get('kind') == kind),
                        key=lambda row: (-row['change_pct'], row['instrument_id']))
        leaders.extend(subset[:3])
        laggards.extend(sorted(subset, key=lambda row: (row['change_pct'], row['instrument_id']))[:3])
    background = previous.get('sector_background', {})
    prior = _index(_rows(background.get('items'))) if background.get('date') == previous.get('date') else {}
    comparison = []
    for row in sectors:
        old = prior.get(row['instrument_id'])
        if old is None or old.get('kind') != row.get('kind'):
            continue
        old_pct = _num(old.get('change_pct'))
        current = _num(row.get('change_pct')) if (_num(row.get('latest_price')) or 0) > 0 else None
        comparison.append({**row, 'previous_change_pct': old_pct,
                           'state': _direction_state(old_pct, current)})
    comparison.sort(key=lambda row: (row['state'] == 'unknown',
                    -abs(row.get('previous_change_pct') or 0), row['instrument_id']))
    watched, prior_facts = {}, _watched_prior_facts(previous)
    for row in _rows(previous.get('observations')):
        name = row.get('mainline')
        if isinstance(name, str) and name.strip():
            enriched = _watched_row(row, prior_facts, previous.get('date'))
            watched.setdefault(name, {})[row.get('instrument_id')] = _feedback_row(enriched, quotes, events)
    groups = [{'mainline': name, **_stats(list(rows.values()))} for name, rows in sorted(watched.items())]
    return {'state': 'partial' if comparison else 'unknown', 'previous_date': previous.get('date'),
            'comparison': comparison, 'current_leaders': leaders, 'current_laggards': laggards,
            'watched': groups,
            'reason': ('同来源代码及类别对照；两日涨幅分别是当日来源前收口径，不是两日累计收益。'
                       if comparison else '本份快照没有可核验的同来源代码昨日板块行情；当日强弱榜独立展示。')
                      + '人工主线只统计昨日原关注样本，不能据此声称全板块涨跌或自动判定原判断兑现。'}


def _afternoon(payload, feedback, directions, quotes):
    observations, chosen = [], set()
    def stock(row, kind, label):
        symbol = row.get('instrument_id')
        if symbol in chosen or _num(row.get('change_pct')) is None:
            return
        chosen.add(symbol)
        quote = quotes.get(symbol, row)
        name = row.get('name') or symbol
        height = row.get('previous_board_count')
        evidence = (f'昨日{height}板；' if height else '昨日关注样本；') + f'本次涨幅{row["change_pct"]:+.2f}%，{LABELS.get(row.get("current_state"), "池状态未知")}。'
        levels = [f'{title}{value:.2f}' for title, value in (
            ('午间价', _num(quote.get('latest_price'))), ('当日开盘', _num(quote.get('open'))),
            ('已见最高', _num(quote.get('high'))), ('已见最低', _num(quote.get('low')))) if value is not None and value > 0]
        observations.append({'kind': kind, 'object': symbol, 'label': f'{label} · {name}',
            'evidence': evidence, 'condition': '以' + '、'.join(levels) + '为对照，午后核对是否保持、回落或修复，并在收盘核验最终池状态。',
            'scope': '本次快照可见价格参照，不构成新增支撑压力线或策略信号；当日最高最低不是完整分时路径'})
    drops = sorted((row for row in feedback['items'] if (_height(row.get('previous_board_count')) or 0) >= 2
                    and _num(row.get('change_pct')) is not None and row['change_pct'] <= -5),
                   key=lambda row: (row['change_pct'], _order(row)))
    for row in drops[:1]:
        stock(row, 'negative_feedback', '高位回落观察')
    for row in [row for row in feedback['items'] if (_height(row.get('previous_board_count')) or 0) >= 2][:2]:
        stock(row, 'prior_ladder', '昨日接力观察')
    for group in directions['watched'][:2]:
        for row in group['items'][:1]:
            stock(row, 'prior_watch', group['mainline'])
    sector_chosen = set()
    sector_targets = [next((row for row in directions['current_leaders'] if row.get('kind') == kind), None)
                      for kind in ('industry', 'concept')]
    for row in [row for row in sector_targets if row is not None] + directions['current_laggards'][:1]:
        if row['instrument_id'] in sector_chosen:
            continue
        sector_chosen.add(row['instrument_id'])
        pct = row['change_pct']
        coverage = row.get('member_coverage') or {}
        breadth = (f'已覆盖成员上涨{row.get("member_up")}、下跌{row.get("member_down")}，'
                   if _num(row.get('member_up')) is not None and _num(row.get('member_down')) is not None else '成员广度缺失，')
        observations.append({'kind': 'sector', 'object': row['instrument_id'], 'label': f'方向观察 · {row["name"]}',
            'evidence': f'本次板块涨幅{pct:+.2f}%；{breadth}成员日期{row.get("members_observed_date") or "未知"}。',
            'condition': f'以本次{row["latest_price"]:.2f}及{pct:+.2f}%为对照，午后核对板块走势与已留存成员广度是否同向；覆盖{coverage.get("valid", "未知")}/{coverage.get("total", "未知")}。',
            'scope': '指数涨幅和成员样本分别核验，不把成交额解释为净流入'})
    if not observations:
        observations.append({'kind': 'missing', 'object': None, 'label': '观察依据不足',
            'evidence': '本次没有足够的昨日接力样本或有效板块报价。',
            'condition': '等待收盘已发布事实核验；不以缺失值生成具体对象或判断。', 'scope': '仅使用本份午评留存事实'})
    return observations[:8]


def _previous_close_feedback(previous, quotes, events, day, calendar, classifications):
    """Match a previously frozen small list, never a newly queried old review."""
    value = previous.get('close_review_summary')
    empty = {'date': previous.get('date'), 'state': 'unavailable', 'groups': [],
             'reason': '未留存前日收盘转变名单；正常昨日涨停、连板反馈独立展示',
             'scope': '严格前日午间→收盘的附加观察名单；各组可能重叠，不加入昨日涨停总数'}
    if not isinstance(value, dict) or value.get('state') not in ('retained', 'partial'):
        return {**empty, 'reason': value.get('reason') or empty['reason']} if isinstance(value, dict) else empty
    known = previous.get('known_at_ms')
    generated = value.get('generated_at_ms')
    if (not _day(day) or value.get('date') != previous.get('date') or not _day(value.get('date'))
            or value['date'] >= day or calendar.get('verified') is not True
            or calendar.get('previous_day') != value['date']
            or not isinstance(known, int) or isinstance(known, bool)
            or not isinstance(generated, int) or isinstance(generated, bool) or not 0 < generated <= known):
        return {**empty, 'reason': '前日回看日期或实际生成时间未通过采集时点核验，不使用事后结果'}
    originals = _index(_rows(value.get('items')))
    groups = []
    for group in _rows(value.get('groups')):
        identities = group.get('instrument_ids')
        source = group.get('source_count')
        if (group.get('key') not in ('resealed', 'opened', 'new_limit_up') or not isinstance(identities, list)
                or len(identities) > 2000 or len(set(v for v in identities if isinstance(v, str))) != len(identities)
                or not isinstance(source, int) or isinstance(source, bool) or source < len(identities)):
            return {**empty, 'reason': '前日回看小摘要的名单与数量结构未通过核验'}
        rows = []
        for symbol in identities:
            old = originals.get(symbol)
            if old is None:
                continue
            item = _feedback_row({'instrument_id': symbol, 'name': old.get('name'),
                'previous_board_count': old.get('final_board_count'), 'previous_close': old.get('close_price')},
                quotes, events, allow_frozen_noon=False)
            classification = old if old.get('industries') or old.get('topics') else classifications.get(symbol, {})
            item.update(industries=_rows(classification.get('industries')), topics=_rows(classification.get('topics')),
                        membership_state=classification.get('membership_state', classification.get('state', 'unknown')),
                        membership_reason=('前日午评已冻结的成员归属，不用今日成员改写'
                            if classification.get('industries') or classification.get('topics') else '前日快照未留存该股可核验的归属，不使用今日成员补写'),
                        previous_transition=old.get('transition'), previous_close_change_pct=old.get('close_change_pct'),
                        previous_close_review_id=value.get('review_id'))
            rows.append(item)
        statistics = _stats(rows)
        valid = statistics['valid_count']
        statistics.update(count=source, unknown_count=source - valid, retained_count=len(rows),
                          omitted_count=source - len(rows))
        if source == 0:
            statistics.update(up=0, down=0, flat=0)
        groups.append({**statistics, 'key': group['key'], 'label': group.get('label') or group['key'],
                       'source_count': source, 'state': 'partial' if source != valid else 'observed_sample',
                       'reason': '未留存或缺少当前报价的股票计为未知；只对有效交集计算均值，不把缺失当零'})
    return {'date': value['date'], 'state': 'partial' if value['state'] == 'partial' or any(
        group['unknown_count'] for group in groups) else 'available',
        'review_id': value.get('review_id'), 'generated_at_ms': generated, 'result_sha256': value.get('result_sha256'),
        'groups': groups, 'reason': value.get('reason'), 'scope': empty['scope']}


def build_workbench(payload, *, membership_context=None, prior_event_context=None, close_classifications=None):
    previous = payload.get('previous_review') or {}
    # A malformed or mismatched prior date must not relabel unrelated rows as
    # yesterday. The collector normally supplies this exact trading-day link.
    expected = (payload.get('calendar') or {}).get('previous_day')
    prior_day = previous.get('date')
    valid_date = _day(prior_day) and _day(payload.get('date')) and prior_day < payload['date']
    if not valid_date or expected is not None and expected != prior_day:
        previous = {'date': expected, 'state': 'missing'}
    elif isinstance(prior_event_context, dict) and prior_event_context.get('date') == prior_day:
        frozen = dict(previous.get('event_cohorts') or {})
        for key, value in (prior_event_context.get('event_cohorts') or {}).items():
            if key not in frozen:
                frozen[key] = value
        previous = {**previous, 'event_cohorts': frozen}
    quotes = _index(_rows(payload.get('stocks')))
    events = payload.get('events') or {}
    pool_states = _pool_states(events, payload.get('date'), previous,
                               (membership_context or {}).get('stock_classifications', {}))
    feedback = _previous_feedback(previous, quotes, pool_states)
    negative = [_cohort(previous, key, quotes, pool_states) for key in ('limit_break', 'limit_down')]
    for row, label in zip(negative, ('昨日炸板样本反馈', '昨日跌停样本反馈')):
        row['label'] = label
    directions = _directions(payload, previous, quotes, pool_states, membership_context)
    ladder = _ladder(events, quotes, pool_states)
    event_classifications = {'limit_up': ladder['classification_counts']}
    for key in ('limit_down', 'limit_break'):
        source = events.get(key, {})
        rows = _pool_classification_rows(source, quotes, pool_states)
        event_classifications[key] = _classification_counts(rows, source, payload.get('date'))
    gaps = []
    if feedback['source_count'] is None:
        gaps.append('昨日涨停事件样本未留存或未核验；首板、连板反馈保持未知。')
    for cohort in negative:
        if cohort['source_count'] is None:
            gaps.append(cohort['label'] + '缺少已冻结历史样本，不用当前池补昨天。')
    if not directions['comparison']:
        gaps.append('缺少同来源代码的昨日板块行情，不能判定跨日延续或新主线。')
    from app.services.noon_comparison import build_index_comparisons
    from app.services.noon_temperature import build_noon_temperature
    return {'version': VERSION, 'scope': '基于本份不可变午评及其原采集代际成员证据派生；不联网、不用当前成员回填，不改变盘后及策略信号。',
            'previous_feedback': feedback,
            'previous_close_feedback': _previous_close_feedback(previous, quotes, pool_states, payload.get('date'), payload.get('calendar') or {}, close_classifications or {}),
            'negative_feedback': {'cohorts': negative, 'high_position_drops': [row for row in feedback['items']
                if (_height(row.get('previous_board_count')) or 0) >= 2
                and _num(row.get('change_pct')) is not None and row['change_pct'] <= -5],
                'reason': '明显回落采用本次涨幅≤-5%的展示分组；盘中状态不等同收盘断板或修复确认。'},
            'today_ladder': ladder, 'directions': directions,
            'event_classification_counts': event_classifications,
            'index_comparisons': build_index_comparisons(payload),
            'temperature': build_noon_temperature(payload, feedback),
            'afternoon': _afternoon(payload, feedback, directions, quotes), 'gaps': gaps}
