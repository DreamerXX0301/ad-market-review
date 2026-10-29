"""Pure, bounded noon-to-close comparisons of explicitly supplied same-day facts.

The caller binds and verifies immutable source objects. This module performs no
I/O, never follows a current market head, and does not alter the noon body.
Closing event membership and a day's "ever broken" flag have separate meanings.
"""
from collections import Counter
from datetime import date
import math
import re
from statistics import mean, median

from app.services.noon_temperature import build_noon_temperature

VERSION = 'noon-close-v1'
POOLS = ('limit_up', 'limit_break', 'limit_down')
STOCK = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
SECTOR = re.compile(r'\d{6}\.(?:TI|SH|SZ)\Z')
MAX_FOCUS_ITEMS = 20000
MAX_NEXT_ITEMS = 2000
INDEX_NAMES = {'000001.SH': '上证指数', '399001.SZ': '深证成指',
               '399006.SZ': '创业板指', '000688.SH': '科创50'}
SCOPE = '同一份午评的固定事实与严格同日已发布收盘事实；不推断午后分时路径，不改变策略或次日正常收盘分组。'
TRANSITION_LABELS = {
    'kept': '午间封板→收盘仍封', 'resealed': '午间炸板→收盘回封',
    'opened': '午间封板→收盘未封', 'new_limit_up': '午间未封→收盘涨停',
    'down_opened': '午间跌停→收盘打开', 'new_limit_down': '午间未跌停→收盘跌停',
    'not_resealed': '午间炸板→收盘未封', 'unchanged': '未见可核验的封板转变',
    'unknown': '收盘结果待核验',
}


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _num(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _height(value):
    value = _num(value)
    return int(value) if value is not None and value > 0 and value == int(value) else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _symbol(row):
    value = row.get('instrument_id') or row.get('thscode')
    return value if isinstance(value, str) and STOCK.fullmatch(value) else None


def _index(rows, *, stock=True):
    rows = _rows(rows)
    identity = _symbol if stock else lambda row: row.get('instrument_id') or row.get('topic_code')
    counts = Counter(identity(row) for row in rows)
    return {identity(row): row for row in rows if isinstance(identity(row), str)
            and counts[identity(row)] == 1}


def _positive(value):
    value = _num(value)
    return value if value is not None and value > 0 else None


def _same_noon_basis(row, day):
    return row.get('price_basis') == 'none' and all(
        row[key] == day for key in ('date', 'trade_date', 'quote_date') if key in row)


def _nonnegative(value):
    value = _num(value)
    return value if value is not None and value >= 0 else None


def _quote(row, day, *, closing=False, stock=True):
    row = _object(row)
    reason = None
    if not row:
        return {'price': None, 'change_pct': None, 'amount': None, 'reason': '没有唯一同代码报价'}
    if any(row[key] != day for key in ('date', 'trade_date', 'quote_date') if key in row):
        reason = '报价所属日期不符'
    elif closing and row.get('date', row.get('trade_date')) != day:
        reason = '收盘报价没有明确同日日期'
    elif row.get('price_basis') != 'none':
        reason = '未核验同日未复权价格口径'
    elif stock and closing and (_num(row.get('volume')) or 0) <= 0:
        reason = '收盘正成交量尚未核验，不能把缺数认定为停牌'
    price = _positive(row.get('close') if closing else row.get('latest_price', row.get('last_price')))
    if price is None:
        reason = reason or '价格缺失或非正'
    low, high = _positive(row.get('low')), _positive(row.get('high'))
    if low is not None and high is not None and (low > high or price is not None and not low - .011 <= price <= high + .011):
        reason = reason or '价格超出同日报价范围'
    if reason:
        return {'price': None, 'change_pct': None, 'amount': None, 'reason': reason}
    change = _num(row.get('change_pct'))
    previous = _positive(row.get('prev_close'))
    if previous is not None and change is not None and abs((price / previous - 1) * 100 - change) > .001:
        change = None
    return {'price': price, 'change_pct': change, 'amount': _nonnegative(row.get('amount')), 'reason': None}


def _board(symbol):
    code, exchange = symbol.split('.')
    board = ('beijing' if exchange == 'BJ' and code.startswith(('4', '8', '92'))
             else 'star' if exchange == 'SH' and code.startswith(('688', '689'))
             else 'chinext' if exchange == 'SZ' and code.startswith(('300', '301'))
             else 'main' if exchange == 'SH' and code.startswith(('600', '601', '603', '605'))
             or exchange == 'SZ' and code.startswith(('000', '001', '002', '003'))
             else 'unknown')
    return {'board': board, 'board_label': {'main': '主板', 'chinext': '创业板', 'star': '科创板',
                                          'beijing': '北交所'}.get(board, '板别待核验')}


def _event_price(row):
    for key in ('previous_close', 'close', 'last_price', 'latest_price'):
        if key in row:
            return _positive(row.get(key))
    return None


def _pool_index(events, day, quotes, *, closing=False):
    result = {}
    for key in POOLS:
        pool = _object(_object(events).get(key))
        rows = _rows(pool.get('items'))
        available = pool.get('state') in ('observed', 'observed_sample', 'partial')
        indexed = _index(rows)
        accepted = {}
        if available and pool.get('date', day) == day:
            for symbol, row in indexed.items():
                if any(row[field] != day for field in ('date', 'trade_date') if field in row):
                    continue
                quote = _quote(quotes.get(symbol), day, closing=closing)
                if quote['price'] is None:
                    continue
                event_price = _event_price(row)
                if event_price is None and any(field in row for field in ('previous_close', 'close', 'last_price', 'latest_price')):
                    continue
                if event_price is not None and abs(event_price - quote['price']) > .011:
                    continue
                # Closing rows come from the caller's event/close verification.
                # A raw, undated/unpriced event is insufficient even with a code.
                if closing and event_price is None and row.get('price_verified') is not True:
                    continue
                accepted[symbol] = row
        raw_count = len(rows)
        declared = pool.get('source_count', pool.get('declared_total', pool.get('count', raw_count if available else None)))
        declared_ok = isinstance(declared, int) and not isinstance(declared, bool) and declared == raw_count
        complete = (available and pool.get('date', day) == day and len(accepted) == raw_count
                    and len(indexed) == raw_count and declared_ok
                    and _count(pool.get('unverified_count', 0)) == 0 and _count(pool.get('excluded_rows', 0)) == 0
                    and (pool.get('complete') is True or pool.get('complete') is not False and pool.get('state') == 'observed'))
        result[key] = {'items': accepted, 'raw_items': indexed if available and pool.get('date', day) == day else {},
                       'complete': complete, 'source_count': declared,
                       'reason': pool.get('reason'), 'state': pool.get('state', 'unknown')}
    return result


def _event_state(symbol, pools, *, broken_semantics='unknown'):
    membership = {key for key in POOLS if symbol in pools[key]['items']}
    if 'limit_up' in membership and 'limit_down' in membership:
        return 'conflict'
    if 'limit_break' in membership and len(membership) > 1:
        if broken_semantics != 'ever_broken':
            return 'conflict'
        return 'limit_up' if 'limit_up' in membership else 'limit_down'
    if membership == {'limit_break'}:
        if broken_semantics == 'final_unsealed':
            return 'limit_break'
        if broken_semantics == 'ever_broken' and pools['limit_up']['complete'] and pools['limit_down']['complete']:
            return 'limit_break'
        if broken_semantics == 'unknown' and pools['limit_up']['complete'] and pools['limit_down']['complete']:
            return 'not_listed'
        # A day's historical touch does not establish the final state.
        return 'unknown'
    if membership:
        return next(iter(membership))
    return 'not_listed' if pools['limit_up']['complete'] and pools['limit_down']['complete'] else 'unknown'


def _previous(payload):
    previous = _object(payload.get('previous_review'))
    calendar = _object(payload.get('calendar'))
    day = payload.get('date')
    prior_day = previous.get('date')
    if not (_day(prior_day) and prior_day < day and calendar.get('verified') is True
            and calendar.get('previous_day') == prior_day):
        return {}, {}
    pools = _object(previous.get('event_cohorts'))
    if not pools.get('limit_up'):
        old = _object(previous.get('cohort_feedback'))
        if old.get('date') == prior_day:
            pools = {**pools, 'limit_up': old}
    rows, heights = {}, {}
    for key in POOLS:
        pool = _object(pools.get(key))
        if pool.get('date') != prior_day or pool.get('state') not in ('observed', 'observed_sample', 'partial'):
            continue
        for symbol, row in _index(pool.get('items')).items():
            if row.get('date', prior_day) != prior_day:
                continue
            rows.setdefault(symbol, set()).add(key)
            if key == 'limit_up':
                heights[symbol] = _height(row.get('previous_board_count'))
    return rows, heights


def _watched(payload):
    previous = _object(payload.get('previous_review'))
    groups = []
    observations = _rows(previous.get('observations'))
    if observations:
        groups.append({'key': 'previous_observations', 'label': '昨日原关注股票',
                       'text': None, 'recorded_at_ms': previous.get('recorded_at_ms'),
                       'instrument_ids': sorted({symbol for row in observations if (symbol := _symbol(row))})})
    for index, line in enumerate(_rows(previous.get('mainlines'))):
        ids = set()
        symbol = _symbol(line)
        if symbol:
            ids.add(symbol)
        ids.update(value for value in line.get('instrument_ids', []) if isinstance(value, str) and STOCK.fullmatch(value))
        groups.append({'key': 'mainline_' + str(index), 'label': str(line.get('name') or line.get('mainline') or '昨日主线'),
                       'text': line.get('text') or line.get('content') or line.get('mainline'),
                       'recorded_at_ms': line.get('recorded_at_ms'), 'instrument_ids': sorted(ids)})
    for index, line in enumerate(_rows(payload.get('afternoon_observations'))):
        symbol = _symbol(line)
        groups.append({'key': 'noon_observation_' + str(index), 'label': str(line.get('label') or '午间原观察'),
                       'text': line.get('text') or line.get('condition'), 'recorded_at_ms': line.get('recorded_at_ms'),
                       'revision': line.get('revision'), 'note_id': line.get('note_id'),
                       'instrument_ids': [symbol] if symbol else []})
    from app.services.noon_workbench import build_workbench
    system_payload = {**payload, 'sectors': [{**row, 'name': row.get('name') or row.get('instrument_id')}
                                           for row in _rows(payload.get('sectors'))]}
    system = build_workbench(system_payload).get('afternoon', [])
    for index, line in enumerate(_rows(system)):
        symbol = line.get('object')
        if line.get('kind') == 'missing':
            continue
        stock = isinstance(symbol, str) and STOCK.fullmatch(symbol)
        groups.append({'key': 'noon_rule_' + str(index), 'label': '午间规则观察 · ' + str(line.get('label') or '观察对象'),
                       'text': line.get('condition'), 'evidence': line.get('evidence'),
                       'recorded_at_ms': None, 'rule_version': 'noon-workbench-v1',
                       'kind': 'stock' if stock else 'sector' if line.get('kind') == 'sector' else 'unknown',
                       'instrument_ids': [symbol] if stock else [],
                       'sector_code': symbol if line.get('kind') == 'sector' else None,
                       'observation_scope': '从该份午评冻结事实按所列规则派生的条件；不伪装成用户当时原文或分时过程'})
    return groups


def _focus(payload, noon_pools, close_pools, prior, observations):
    sections = {}
    def add(symbol, section):
        if isinstance(symbol, str) and STOCK.fullmatch(symbol):
            sections.setdefault(symbol, set()).add(section)
    for key in POOLS:
        for symbol in set(noon_pools[key]['raw_items']) | set(close_pools[key]['raw_items']):
            add(symbol, 'events')
    for symbol in prior:
        add(symbol, 'relay')
    for group in observations:
        for symbol in group['instrument_ids']:
            add(symbol, 'watch')
    summary = _object(payload.get('summary'))
    leader_ids = {}
    for key, values in (('amount', summary.get('top_amount')), ('gainers', summary.get('top_gainers')),
                        ('hot', payload.get('hot_stocks'))):
        ids = []
        for row in _rows(values)[:20]:
            symbol = _symbol(row)
            if symbol:
                add(symbol, 'leaders')
                ids.append(symbol)
        leader_ids[key] = list(dict.fromkeys(ids))
    return sections, leader_ids


def _transition(noon, close):
    if noon in ('unknown', 'conflict') or close in ('unknown', 'conflict'):
        return 'unknown'
    if noon == 'limit_up':
        return 'kept' if close == 'limit_up' else 'opened'
    if noon == 'limit_break':
        return 'resealed' if close == 'limit_up' else 'new_limit_down' if close == 'limit_down' else 'not_resealed'
    if noon == 'limit_down':
        return 'down_opened' if close != 'limit_down' else 'unchanged'
    if close == 'limit_up':
        return 'new_limit_up'
    if close == 'limit_down':
        return 'new_limit_down'
    return 'unchanged'


def _transition_flags(noon, close):
    if noon in ('unknown', 'conflict') or close in ('unknown', 'conflict'):
        return ['unknown']
    flags = []
    if noon == 'limit_up':
        flags.append('kept' if close == 'limit_up' else 'opened')
    if noon == 'limit_break' and close == 'limit_up':
        flags.append('resealed')
    if noon != 'limit_up' and close == 'limit_up':
        flags.append('new_limit_up')
    if noon == 'limit_down' and close != 'limit_down':
        flags.append('down_opened')
    if noon != 'limit_down' and close == 'limit_down':
        flags.append('new_limit_down')
    return flags or [_transition(noon, close)]


def _classifications(payload, symbol):
    value = _object(_object(payload.get('stock_classifications')).get(symbol))
    output = {'industries': [], 'topics': [], 'classification_keys': [],
              'membership_state': value.get('state', 'unknown')}
    if value.get('state') not in ('observed_sample', 'partial'):
        return output
    for field, kind in (('industries', 'industry'), ('topics', 'concept')):
        seen = set()
        for tag in _rows(value.get(field)):
            code, observed, source = tag.get('topic_code'), tag.get('members_observed_date'), tag.get('source')
            if (not isinstance(code, str) or not SECTOR.fullmatch(code) or tag.get('kind') != kind
                    or source != 'fuyao' or not _day(observed)
                    or observed > payload['date'] or not isinstance(tag.get('name'), str) or not tag['name'].strip()):
                continue
            identity = '|'.join((kind, source, code, observed))
            if identity in seen:
                continue
            seen.add(identity)
            output[field].append({key: tag.get(key) for key in ('topic_code', 'name', 'kind', 'source', 'members_observed_date')})
            output['classification_keys'].append(identity)
    return output


def _line(symbol, payload, close_inputs, noon_quotes, close_quotes, noon_pools, close_pools, prior, heights, sections):
    day = payload['date']
    no = _object(noon_quotes.get(symbol)); co = _object(close_quotes.get(symbol))
    nq, cq = _quote(no, day), _quote(co, day, closing=True)
    noon_state = _event_state(symbol, noon_pools, broken_semantics='final_unsealed') if nq['price'] is not None else 'unknown'
    semantics = _object(close_inputs.get('pool_semantics')).get('limit_break', 'unknown')
    close_state = _event_state(symbol, close_pools, broken_semantics=semantics) if cq['price'] is not None else 'unknown'
    price_pair = nq['price'] is not None and cq['price'] is not None
    afternoon = (cq['price'] / nq['price'] - 1) * 100 if price_pair else None
    close_pct = cq['change_pct']
    base = _positive(no.get('prev_close'))
    if no and not _same_noon_basis(no, day) and _positive(co.get('prev_close')) is None:
        close_pct = None
    if cq['price'] is not None and base is not None and _same_noon_basis(no, day) and co.get('price_basis') == 'none':
        computed = (cq['price'] / base - 1) * 100
        if close_pct is None or abs(close_pct - computed) <= .001:
            close_pct = computed
        else:
            close_pct = None
    points = close_pct - nq['change_pct'] if close_pct is not None and nq['change_pct'] is not None else None
    up_event = close_pools['limit_up']['items'].get(symbol, {})
    final = _height(up_event.get('previous_board_count', up_event.get('continue_day_cnt', up_event.get('board_count')))) if close_state == 'limit_up' else None
    height = heights.get(symbol)
    relay_state = ('promoted' if final == height + 1 else 'height_conflict' if final is not None else 'unknown') if height and close_state == 'limit_up' else (
        'broken' if height and close_state in ('limit_break', 'limit_down', 'not_listed') else 'unknown' if symbol in heights else None)
    transition = _transition(noon_state, close_state)
    day_events = []
    if symbol in close_pools['limit_break']['items']:
        day_events.append('ever_broken' if semantics == 'ever_broken' else 'closing_break' if semantics == 'final_unsealed' else 'break_event_semantics_unknown')
    if transition in ('resealed', 'opened', 'new_limit_up') or close_state == 'limit_up' and noon_state not in ('limit_up', 'unknown', 'conflict'):
        sections = {*sections, 'tomorrow'}
    amount_delta = None
    amount_reason = None
    if close_inputs.get('amount_comparable') is not True:
        amount_reason = '来源、币种或累计单位尚未共同核验，不计算差额'
    elif no.get('currency') != 'CNY' or co.get('currency') != 'CNY':
        amount_reason = '成交额币种未共同核验为CNY'
    elif co.get('amount_unit', close_inputs.get('amount_unit', 'CNY')) != 'CNY':
        amount_reason = '成交额单位未共同核验为CNY'
    elif nq['amount'] is None or cq['amount'] is None:
        amount_reason = '一端成交额或有效价格缺失'
    elif cq['amount'] < nq['amount']:
        amount_reason = '收盘累计成交额小于午间值，疑似口径或累计范围变化'
    else:
        amount_delta = cq['amount'] - nq['amount']
    reasons = [value for value in (nq['reason'], cq['reason']) if value]
    if close_state in ('unknown', 'conflict'):
        reasons.append('最终池状态缺失、覆盖不足或来源语义冲突，未把未采到当作收盘未封')
    if relay_state == 'height_conflict':
        reasons.append('收盘来源实际板数与昨日连续板数晋级关系冲突，保持待核验')
    return {'instrument_id': symbol, 'name': no.get('name') or co.get('name') or symbol,
            **_board(symbol), **_classifications(payload, symbol),
            'noon_state': noon_state, 'close_state': close_state, 'transition': transition,
            'transition_flags': _transition_flags(noon_state, close_state),
            'transition_label': TRANSITION_LABELS[transition], 'previous_board_count': height,
            'previous_event_states': sorted(prior.get(symbol, set())),
            'final_board_count': final, 'relay_state': relay_state,
            'noon_price': nq['price'], 'close_price': cq['price'], 'noon_change_pct': nq['change_pct'],
            'close_change_pct': close_pct, 'afternoon_change_pct': afternoon, 'change_pct_points': points,
            'noon_amount': nq['amount'], 'close_amount': cq['amount'], 'amount_delta': amount_delta,
            'amount_delta_reason': amount_reason, 'amount': cq['amount'],
            'source_reason': _object(noon_pools['limit_up']['items'].get(symbol)).get('limit_up_reason'),
            'sections': sorted(sections), 'day_events': day_events,
            'reason': '；'.join(dict.fromkeys(reasons)) or None}


def _counts(values, *, total=None):
    known = [value for value in values if value is not None]
    return {'up': sum(value > 0 for value in known) if known else None,
            'down': sum(value < 0 for value in known) if known else None,
            'flat': sum(value == 0 for value in known) if known else None,
            'valid': len(known), 'total': len(values) if total is None else total,
            'mean_pct': mean(known) if known else None, 'median_pct': median(known) if known else None}


def _metric(rows, source, predicate):
    known = [row for row in rows if row['close_state'] not in ('unknown', 'conflict')
             and row['noon_state'] not in ('unknown', 'conflict')]
    success = [row for row in known if predicate(row)]
    available = source is not None or bool(rows)
    return {'count': len(success) if available else None, 'source_count': source,
            'denominator': len(known) if available else None,
            'unknown_count': source - len(known) if source is not None else None,
            'rate_pct': len(success) / len(known) * 100 if known else None,
            'scope': '固定午间名单中收盘结果已核验的股票为分母；未知不算失败'}


def _metrics(rows, noon_pools):
    def members(key):
        symbols = noon_pools[key]['raw_items']
        selected = [row for row in rows if row['instrument_id'] in symbols]
        declared = noon_pools[key]['source_count']
        source_count = declared if isinstance(declared, int) and not isinstance(declared, bool) and declared >= len(selected) else None
        return selected, source_count
    up, up_count = members('limit_up')
    broken, broken_count = members('limit_break')
    down, down_count = members('limit_down')
    nonup = [row for row in rows if row['noon_state'] not in ('limit_up', 'unknown', 'conflict')]
    nondown = [row for row in rows if row['noon_state'] not in ('limit_down', 'unknown', 'conflict')]
    return {'kept': _metric(up, up_count, lambda row: row['close_state'] == 'limit_up'),
            'resealed': _metric(broken, broken_count, lambda row: row['close_state'] == 'limit_up'),
            'opened': _metric(up, up_count, lambda row: row['close_state'] != 'limit_up'),
            'new_limit_up': _metric(nonup, len(nonup), lambda row: row['close_state'] == 'limit_up'),
            'down_opened': _metric(down, down_count, lambda row: row['close_state'] != 'limit_down'),
            'new_limit_down': _metric(nondown, len(nondown), lambda row: row['close_state'] == 'limit_down')}


def _relay(rows):
    rows = [row for row in rows if 'limit_up' in row['previous_event_states']]
    result = {'source_count': len(rows), 'promoted_count': sum(row['relay_state'] == 'promoted' for row in rows),
              'broken_count': sum(row['relay_state'] == 'broken' for row in rows),
              'unknown_count': sum(row['relay_state'] in ('unknown', 'height_conflict') for row in rows)}
    result['valid_count'] = result['source_count'] - result['unknown_count']
    result['groups'] = []
    for key, label, height in (('board_5_plus', '昨日五板及以上', 5), ('board_4', '昨日四板', 4),
                               ('board_3', '昨日三板', 3), ('board_2', '昨日二板', 2), ('first', '昨日首板', 1)):
        selected = [row for row in rows if row['previous_board_count'] is not None and row['previous_board_count'] >= 5] if height == 5 else [
            row for row in rows if row['previous_board_count'] == height]
        result['groups'].append({'key': key, 'label': label, 'count': len(selected),
            'promoted_count': sum(row['relay_state'] == 'promoted' for row in selected),
            'broken_count': sum(row['relay_state'] == 'broken' for row in selected),
            'unknown_count': sum(row['relay_state'] in ('unknown', 'height_conflict') for row in selected),
            'instrument_ids': [row['instrument_id'] for row in selected]})
    unknown_height = [row for row in rows if row['previous_board_count'] is None]
    if unknown_height:
        result['groups'].append({'key': 'height_unknown', 'label': '昨日高度待核验', 'count': len(unknown_height),
            'promoted_count': 0, 'broken_count': 0, 'unknown_count': len(unknown_height),
            'instrument_ids': [row['instrument_id'] for row in unknown_height]})
    return result


def _classification_counts(rows):
    output = {'industries': [], 'topics': []}
    groups, classified = {}, set()
    for row in rows:
        for field in output:
            for tag in row[field]:
                key = '|'.join((tag['kind'], tag['source'], tag['topic_code'], tag['members_observed_date']))
                group = groups.setdefault((field, key), {**tag, 'key': key, '_items': {}})
                group['_items'][row['instrument_id']] = row
                classified.add(row['instrument_id'])
    for (field, _), group in groups.items():
        items = list(group.pop('_items').values())
        known = [row for row in items if row['close_state'] not in ('unknown', 'conflict')]
        valid = [row for row in items if row['afternoon_change_pct'] is not None]
        heights = [row['final_board_count'] for row in items if row['final_board_count'] is not None]
        group.update(count=len(items), valid_count=len(valid), unknown_count=len(items) - len(valid),
            unknown_close_state_count=len(items) - len(known),
            afternoon_up_count=sum(row['afternoon_change_pct'] > 0 for row in valid),
            afternoon_down_count=sum(row['afternoon_change_pct'] < 0 for row in valid),
            afternoon_flat_count=sum(row['afternoon_change_pct'] == 0 for row in valid),
            noon_limit_up_count=sum(row['noon_state'] == 'limit_up' for row in items),
            close_limit_up_count=sum(row['close_state'] == 'limit_up' for row in items),
            resealed_count=sum(row['transition'] == 'resealed' for row in items),
            opened_count=sum(row['transition'] == 'opened' for row in items),
            max_board_count=max(heights) if heights else None,
            mean_afternoon_change_pct=mean(row['afternoon_change_pct'] for row in valid) if valid else None,
            mean_noon_change_pct=mean(v) if (v := [row['noon_change_pct'] for row in items if row['noon_change_pct'] is not None]) else None,
            mean_close_change_pct=mean(v) if (v := [row['close_change_pct'] for row in items if row['close_change_pct'] is not None]) else None,
            instrument_ids=sorted(row['instrument_id'] for row in items),
            state='partial' if len(known) != len(items) or len(valid) != len(items)
                  or any(row['membership_state'] == 'partial' for row in items) else 'observed_sample')
        output[field].append(group)
    for field in output:
        output[field].sort(key=lambda row: (-row['close_limit_up_count'], -row['count'], -(row['max_board_count'] or 0), row['key']))
    state = 'unknown' if rows and not classified else 'partial' if len(classified) != len(rows) or any(
        group['state'] == 'partial' for groups in output.values() for group in groups) else 'observed_sample'
    return {**output, 'state': state,
            'classified_count': len(classified), 'unclassified_count': len(rows) - len(classified),
            'scope': '仅午间冻结归属的重点联合名单；同股多方向，不相加，不用收盘新成员改写午间归属'}


def _indices(payload, close_inputs):
    noon = _index(payload.get('indices'), stock=False); close = _index(close_inputs.get('indices'), stock=False)
    result = []
    for symbol, name in INDEX_NAMES.items():
        a, b = _quote(noon.get(symbol), payload['date'], stock=False), _quote(close.get(symbol), payload['date'], closing=True, stock=False)
        pair = a['price'] is not None and b['price'] is not None
        result.append({'instrument_id': symbol, 'name': name, 'noon_price': a['price'], 'close_price': b['price'],
            'noon_change_pct': a['change_pct'], 'close_change_pct': b['change_pct'],
            'afternoon_change_pct': (b['price'] / a['price'] - 1) * 100 if pair else None,
            'change_pct_points': b['change_pct'] - a['change_pct'] if a['change_pct'] is not None and b['change_pct'] is not None else None,
            'reason': '；'.join(v for v in (a['reason'], b['reason']) if v) or None})
    return result


def _sector_comparisons(payload, close_inputs):
    noon = _index(payload.get('sectors'), stock=False)
    closing = _index(close_inputs.get('sectors'), stock=False)
    output = {}
    for symbol, row in noon.items():
        old, new = row, _object(closing.get(symbol))
        if (not SECTOR.fullmatch(symbol) or old.get('kind') not in ('industry', 'concept')
                or new.get('kind') != old.get('kind')
                or old.get('source', 'fuyao') != 'fuyao'
                or new.get('source', close_inputs.get('source')) != 'fuyao'):
            continue
        a = _quote(old, payload['date'], stock=False)
        b = _quote(new, payload['date'], closing=True, stock=False)
        pair = a['price'] is not None and b['price'] is not None
        output[(old['kind'], symbol)] = {
            'index_noon_change_pct': a['change_pct'], 'index_close_change_pct': b['change_pct'],
            'index_afternoon_change_pct': (b['price'] / a['price'] - 1) * 100 if pair else None,
            'index_change_pct_points': b['change_pct'] - a['change_pct'] if a['change_pct'] is not None and b['change_pct'] is not None else None,
            'index_noon_price': a['price'], 'index_close_price': b['price'],
            'index_reason': '；'.join(v for v in (a['reason'], b['reason']) if v) or None}
    return output


def _market_summary(payload, close_inputs, noon_quotes, close_quotes):
    day = payload['date']
    noon_values, close_values, common, differences = [], [], [], []
    derived_close = {}
    total_noon = _object(_object(payload.get('summary')).get('breadth')).get('total')
    if not isinstance(total_noon, int) or isinstance(total_noon, bool) or total_noon < len(_rows(payload.get('stocks'))):
        total_noon = len(_rows(payload.get('stocks')))
    for symbol, row in noon_quotes.items():
        a, b = _quote(row, day), _quote(close_quotes.get(symbol), day, closing=True)
        if a['price'] is not None:
            noon_values.append(a['change_pct'])
        if a['price'] is not None and b['price'] is not None:
            close_pct = b['change_pct']
            base = _positive(row.get('prev_close'))
            if base is not None and _same_noon_basis(row, day) and _object(close_quotes.get(symbol)).get('price_basis') == 'none':
                derived = (b['price'] / base - 1) * 100
                close_pct = derived if close_pct is None or abs(derived - close_pct) <= .001 else None
            derived_close[symbol] = close_pct
            afternoon = (b['price'] / a['price'] - 1) * 100
            common.append((symbol, a, b, close_pct, afternoon))
            if (close_inputs.get('amount_comparable') is True and row.get('currency') == 'CNY'
                    and _object(close_quotes.get(symbol)).get('currency') == 'CNY'
                    and _object(close_quotes.get(symbol)).get('amount_unit', close_inputs.get('amount_unit', 'CNY')) == 'CNY'
                    and a['amount'] is not None and b['amount'] is not None and b['amount'] >= a['amount']):
                differences.append((a['amount'], b['amount']))
    for symbol, row in close_quotes.items():
        value = _quote(row, day, closing=True)
        change = derived_close.get(symbol, value['change_pct']) if value['price'] is not None else None
        original = noon_quotes.get(symbol)
        if original and not _same_noon_basis(original, day) and _positive(row.get('prev_close')) is None:
            change = None
        close_values.append(change)
    same = {'count': len(common), 'noon': _counts([row[1]['change_pct'] for row in common]),
            'close': _counts([row[3] for row in common]), 'afternoon': _counts([row[4] for row in common]),
            'scope': '同代码、同日、两端正价交集；日涨幅与快照后涨幅分别统计有效范围，不随板别筛选重算'}
    amount = {'noon_amount': sum(row[0] for row in differences) if differences else None,
              'close_amount': sum(row[1] for row in differences) if differences else None,
              'delta': sum(row[1] - row[0] for row in differences) if differences else None,
              'valid_count': len(differences), 'source_count': total_noon,
              'unknown_count': total_noon - len(differences), 'complete': bool(total_noon and len(differences) == total_noon),
              'state': 'observed_sample' if total_noon and len(differences) == total_noon else 'partial' if differences else 'unknown',
              'label': '快照后成交额增量',
              'reason': None if total_noon and len(differences) == total_noon else
                  '部分股票累计额或有效端点缺失，仅计算可核验同股范围' if differences else
                  '同来源、CNY单位、累计口径或有效端点尚未核验',
              'scope': '只对同股可核验累计额相减；非净流入，单条时刻未知时不称精确午后成交额'}
    return {'noon': _counts(noon_values, total=total_noon),
            'close': _counts(close_values, total=len(_rows(close_inputs.get('stocks')))), 'common': same}, amount


def _temperature(payload, close_inputs, rows, breadth, indices, noon_pools, close_pools):
    events = {}
    for key in POOLS:
        # The noon scoring rule requires mutually exclusive current-state pools.
        # An unverified "ever broken" pool is not silently recast as final-unsealed.
        pool = close_pools[key]
        ids = list(pool['items'])
        if key == 'limit_break' and _object(close_inputs.get('pool_semantics')).get('limit_break') != 'final_unsealed':
            events[key] = {'state': 'unknown', 'items': [], 'reason': '收盘炸板池口径不能证明与午间规则一致'}
        else:
            events[key] = {'state': 'observed' if pool['complete'] else 'partial',
                           'items': [{'instrument_id': symbol} for symbol in ids], 'count': len(ids), 'excluded_rows': 0}
    common = breadth['common']
    all_noon = _object(_object(payload.get('summary')).get('coverage')).get('enumeration_complete') is True
    comparable_total = len(_rows(payload.get('stocks')))
    close_payload = {'date': payload['date'], 'calendar': _object(payload.get('calendar')),
        'previous_review': _object(payload.get('previous_review')),
        'summary': {'breadth': {**common['close'], 'total': comparable_total},
                    'coverage': {'enumeration_complete': all_noon}},
        'events': events, 'indices': [{'instrument_id': row['instrument_id'], 'latest_price': row['close_price'],
                                      'change_pct': row['close_change_pct'], 'date': payload['date']} for row in indices]}
    prior = [row for row in rows if 'limit_up' in row['previous_event_states']]
    values = [row['close_change_pct'] for row in prior if row['close_change_pct'] is not None]
    previous = _object(payload.get('previous_review'))
    source = _object(_object(previous.get('event_cohorts')).get('limit_up'))
    if not source:
        source = _object(previous.get('cohort_feedback'))
    feedback = {'date': previous.get('date'), 'state': source.get('state'),
                'source_count': source.get('source_count'), 'verified_count': source.get('verified_count', source.get('observed_count')),
                'valid_count': len(values), **{key: sum((value > 0 if key == 'up' else value < 0 if key == 'down' else value == 0) for value in values)
                                              for key in ('up', 'down', 'flat')}}
    noon_values = [row['noon_change_pct'] for row in prior if row['noon_change_pct'] is not None]
    noon_feedback = {**feedback, 'valid_count': len(noon_values),
                     **{key: sum((value > 0 if key == 'up' else value < 0 if key == 'down' else value == 0) for value in noon_values)
                        for key in ('up', 'down', 'flat')}}
    frozen_feedback = _object(previous.get('cohort_feedback'))
    if all(key in frozen_feedback for key in ('source_count', 'valid_count', 'up', 'down', 'flat')):
        noon_feedback = frozen_feedback
    original = build_noon_temperature(payload, noon_feedback)
    closing = build_noon_temperature(close_payload, feedback)
    comparable = original['score'] is not None and closing['score'] is not None
    return {'noon': original, 'close': closing, 'delta': round(closing['score'] - original['score'], 1) if comparable else None,
            'reason': None if comparable else '同一规则的两端依据未全部核验，不计算温差；原午间温度保持不变'}


def build_close_review(noon_payload, close_inputs):
    """Join one fixed noon body and its exact-day closing evidence without I/O."""
    payload, close_inputs = _object(noon_payload), _object(close_inputs)
    day = payload.get('date')
    if not _day(day) or close_inputs.get('date') != day:
        raise ValueError('收盘核验仅接受午评同一明确交易日')
    noon_quotes = _index(payload.get('stocks')); close_quotes = _index(close_inputs.get('stocks'))
    noon_pools = _pool_index(payload.get('events'), day, noon_quotes)
    close_pools = _pool_index(close_inputs.get('events'), day, close_quotes, closing=True)
    prior, heights = _previous(payload)
    observations = _watched(payload)
    sections, leaders = _focus(payload, noon_pools, close_pools, prior, observations)
    # Include frozen observation rows even if they were not in a named event.
    all_rows = [_line(symbol, payload, close_inputs, noon_quotes, close_quotes, noon_pools, close_pools,
                      prior, heights, memberships) for symbol, memberships in sorted(sections.items())]
    all_rows.sort(key=lambda row: (row['final_board_count'] is None, -(row['final_board_count'] or 0),
        row['afternoon_change_pct'] is None, -(row['afternoon_change_pct'] or 0), row['instrument_id']))
    requested = len(all_rows)
    limit = close_inputs.get('max_focus_items', MAX_FOCUS_ITEMS)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_FOCUS_ITEMS:
        raise ValueError('重点名单容量须为明确的有界正整数')
    rows = all_rows[:limit]
    by_symbol = {row['instrument_id']: row for row in rows}
    all_by_symbol = {row['instrument_id']: row for row in all_rows}
    leader_rows = {}
    frozen_leader_rows = {'amount': _rows(_object(payload.get('summary')).get('top_amount')),
                          'gainers': _rows(_object(payload.get('summary')).get('top_gainers')),
                          'hot': _rows(payload.get('hot_stocks'))}
    for key, identities in leaders.items():
        source_rows = _index(frozen_leader_rows[key])
        leader_rows[key] = []
        for position, symbol in enumerate(identities, 1):
            item, original = all_by_symbol[symbol], source_rows.get(symbol, {})
            source_rank = original.get('rank')
            if not isinstance(source_rank, int) or isinstance(source_rank, bool) or source_rank <= 0:
                source_rank = None
            leader_rows[key].append({**{field: item.get(field) for field in (
                'instrument_id', 'name', 'noon_price', 'close_price', 'noon_change_pct', 'close_change_pct',
                'afternoon_change_pct', 'close_state', 'transition')},
                'relay_state': item['relay_state'], 'final_board_count': item['final_board_count'],
                'previous_board_count': item['previous_board_count'],
                'rank': source_rank if key == 'hot' else position, 'source_rank': source_rank,
                'rank_scope': '原午间供应商排名；缺失不补序号' if key == 'hot' else '原午间已保存列表的派生排序',
                'sections': item['sections']})
    for group in observations:
        group['objective'] = [{key: by_symbol[symbol].get(key) for key in ('instrument_id', 'name', 'noon_price', 'close_price',
            'noon_change_pct', 'close_change_pct', 'afternoon_change_pct', 'close_state', 'transition', 'final_board_count', 'previous_board_count', 'relay_state')}
            for symbol in group['instrument_ids'] if symbol in by_symbol]
        group['scope'] = '原关注及自由文字保持原样，仅并列客观结果，不自动判定兑现或证伪'
    next_groups = []
    for key, label in (('resealed', '昨日午间炸板、收盘回封'), ('opened', '昨日午间封板、收盘未封'),
                       ('new_limit_up', '昨日午间未封、收盘涨停')):
        selected = [row['instrument_id'] for row in all_rows if row['transition'] == key or key == 'new_limit_up'
                    and row['noon_state'] not in ('limit_up', 'unknown', 'conflict') and row['close_state'] == 'limit_up']
        next_groups.append({'key': key, 'label': label, 'count': len(selected), 'instrument_ids': selected[:MAX_NEXT_ITEMS],
                            'omitted_count': max(0, len(selected) - MAX_NEXT_ITEMS)})
    breadth, amount = _market_summary(payload, close_inputs, noon_quotes, close_quotes)
    indices = _indices(payload, close_inputs)
    known_states = sum(row['close_state'] not in ('unknown', 'conflict') for row in all_rows)
    gaps = []
    if requested != len(rows):
        gaps.append(f'重点联合名单共{requested}只，本轮保存{len(rows)}只，未保存{requested-len(rows)}只；统计仍按完整联合名单，明细为部分覆盖')
    if known_states != requested:
        gaps.append(f'重点名单收盘状态已核验{known_states}/{requested}只，未知或冲突不算未回封、断板或零涨幅')
    if any(not close_pools[key]['complete'] for key in POOLS):
        gaps.append('收盘来源池覆盖未全部核验，未入池不能统一视为收盘未封')
    if len(noon_quotes) != len(_rows(payload.get('stocks'))) or len(close_quotes) != len(_rows(close_inputs.get('stocks'))):
        gaps.append('报价含无效或重复证券身份，相关记录未选取其中有利值')
    classifications = _classification_counts(all_rows)
    sector_comparisons = _sector_comparisons(payload, close_inputs)
    frozen_sectors = _index(payload.get('sectors'), stock=False)
    for group in observations:
        if group.get('kind') != 'sector':
            continue
        symbol = group.get('sector_code')
        original = frozen_sectors.get(symbol, {})
        compared = sector_comparisons.get((original.get('kind'), symbol), {})
        group['sector_objective'] = {
            'topic_code': symbol, 'name': original.get('name') or symbol, 'kind': original.get('kind'),
            'source': 'fuyao', 'noon_price': compared.get('index_noon_price'), 'close_price': compared.get('index_close_price'),
            'noon_change_pct': compared.get('index_noon_change_pct'), 'close_change_pct': compared.get('index_close_change_pct'),
            'afternoon_change_pct': compared.get('index_afternoon_change_pct'), 'change_pct_points': compared.get('index_change_pct_points'),
            'reason': compared.get('index_reason') or (None if compared else '未取得同来源代码的同日收盘板块报价，不推断观察条件兑现')}
    for field in ('industries', 'topics'):
        for group in classifications[field]:
            group.update(sector_comparisons.get((group['kind'], group['topic_code']), {
                'index_noon_change_pct': None, 'index_close_change_pct': None, 'index_afternoon_change_pct': None,
                'index_change_pct_points': None, 'index_noon_price': None, 'index_close_price': None,
                'index_reason': '同来源代码同类别的两端板块指数价格尚未共同核验；成员表现独立保留'}))
    temperature = _temperature(payload, close_inputs, all_rows, breadth, indices, noon_pools, close_pools)
    if any(row['noon_price'] is None or row['close_price'] is None or row['noon_change_pct'] is None
           or row['close_change_pct'] is None for row in indices):
        gaps.append('四指数两端报价或同日前收口径未全部核验；逐项有效值独立展示')
    if classifications['state'] in ('partial', 'unknown'):
        gaps.append('部分重点股票的午间冻结行业/概念归属或结果尚未核验；不使用收盘新成员补写')
    if temperature['delta'] is None:
        gaps.append('午间与收盘同规则温度依据未全部可比，不计算温差')
    if amount['delta'] is None:
        gaps.append('股票累计成交额没有共同核验范围，不推算快照后增量或净流入')
    return {'version': VERSION, 'state': 'partial' if gaps else 'available',
        'summary': {'metrics': _metrics(all_rows, noon_pools), 'indices': indices, 'breadth': breadth, 'amount': amount,
                    'relay': _relay(all_rows), 'leaders': leader_rows,
                    'temperature': temperature},
        'coverage': {'noon_stock_count': len(noon_quotes), 'close_stock_count': len(close_quotes),
                     'common_quote_count': breadth['common']['count'], 'focus_requested_count': requested,
                     'saved_item_count': len(rows), 'omitted_item_count': requested - len(rows),
                     'verified_close_state_count': known_states, 'unknown_close_state_count': requested - known_states,
                     'pool_complete': {key: close_pools[key]['complete'] for key in POOLS}},
        'classification_counts': classifications, 'observations': observations,
        'tomorrow': {'groups': next_groups, 'scope': '次日附加观察名单；不重复计入昨日涨停总数，不影响按严格前日收盘事实独立生成正常昨日反馈'},
        'items': rows, 'gaps': gaps, 'scope': SCOPE}
