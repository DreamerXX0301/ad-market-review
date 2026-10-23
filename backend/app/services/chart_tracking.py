"""Bounded, read-only discovery of a stock's own frozen tracking references."""
from datetime import date
from math import isfinite
import re

from app.core import connect
from app.services.workspace_common import fail, money, obj

MAX_BASELINES = 100
MAX_CHECK_NODES = 100  # The saved condition contract allows 100 nodes.


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _price(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value) and value > 0
    except OverflowError:
        return False


def _reference_fields(spec):
    """Keep referenced raw OHLC fields, never percentage-adjusted thresholds."""
    order = ('close', 'high', 'low', 'open')
    fields, pending, visited, truncated = set(), [spec.get('then')], 0, False
    while pending and visited < MAX_CHECK_NODES:
        node = pending.pop()
        visited += 1
        if not isinstance(node, dict):
            continue
        field = node.get('right_event_field')
        if field in order:
            fields.add(field)
        for group in ('all', 'any'):
            children = node.get(group)
            if not isinstance(children, list):
                continue
            remaining = MAX_CHECK_NODES - visited - len(pending)
            truncated = truncated or len(children) > remaining
            pending.extend(reversed(children[:max(0, remaining)]))
    path = spec.get('price_path')
    reclaim = path.get('reclaim') if isinstance(path, dict) else None
    if isinstance(reclaim, dict) and reclaim.get('event_field') in order:
        fields.add(reclaim['event_field'])
    return [field for field in order if field in fields] or ['close'], truncated or bool(pending)


def _historical_event_checks(tree, as_of):
    """Project frozen event proofs, without evaluating rules or reading prices."""
    from app.domain.conditions import validate

    pending = [tree]
    items, visited, truncated, where_budget = [], 0, False, [0]
    while pending and visited < MAX_CHECK_NODES:
        node = pending.pop()
        visited += 1
        if not isinstance(node, dict):
            continue
        children = node.get('checks', [])
        children = list(children[:MAX_CHECK_NODES + 1]) if isinstance(children, list) else []
        if isinstance(node.get('event_checks'), dict):
            children.insert(0, node['event_checks'])
        remaining = MAX_CHECK_NODES - visited - len(pending)
        truncated = truncated or len(children) > remaining
        pending.extend(reversed(children[:max(0, remaining)]))
        event, proof, spec = node.get('selected_event'), node.get('evidence'), node.get('history_event')
        if (node.get('type') != 'history_event' or not isinstance(node.get('event_checks'), dict)
                or node['event_checks'].get('result') is not True
                or not all(isinstance(value, dict) for value in (event, proof, spec))):
            continue
        prices = [event.get(field) for field in ('open', 'high', 'low', 'close')]
        offset = event.get('offset')
        if (not _day(event.get('date')) or not _day(proof.get('as_of'))
                or not event['date'] < proof['as_of'] <= as_of
                or event.get('price_basis') != 'none' or proof.get('price_basis') != 'none'
                or not isinstance(offset, int) or isinstance(offset, bool) or not 1 <= offset <= 750
                or not all(_price(value) for value in prices)
                or event['high'] < max(prices) or event['low'] > min(prices)
                or spec.get('match') not in ('latest', 'earliest', 'any')
                or (spec.get('match') == 'any' and node.get('result') is not True)):
            continue
        summary = {'match': spec['match']}
        try:
            summary['where'] = validate(spec.get('where'), counter=where_budget, scope='event_where')
        except (ValueError, TypeError, OverflowError):
            pass  # A missing/old label definition never becomes a new signal.
        path = spec.get('price_path')
        reclaim = path.get('reclaim') if isinstance(path, dict) else None
        if isinstance(reclaim, dict) and reclaim.get('event_field') in ('open', 'high', 'low', 'close'):
            summary['price_path'] = {'reclaim': {'event_field': reclaim['event_field']}}
        references, references_truncated = _reference_fields(spec)
        truncated = truncated or references_truncated
        items.append({'type': 'history_event', 'result': node.get('result') if isinstance(node.get('result'), bool) else None,
                      'reference_fields': references,
                      'selected_event': {key: event[key] for key in ('date', 'open', 'high', 'low', 'close', 'offset', 'price_basis')},
                      'event_checks': {'result': True}, 'history_event': summary,
                      'evidence': {'as_of': proof['as_of'], 'price_basis': proof['price_basis']}})
    return items, truncated or bool(pending)


def tracking_baselines(instrument_id, as_of=None):
    symbol = instrument_id.strip().upper()
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol):
        fail('需要有效的个股代码')
    if as_of is not None:
        try:
            valid = isinstance(as_of, str) and date.fromisoformat(as_of).isoformat() == as_of
        except ValueError:
            valid = False
        if not valid:
            fail('观察日期应使用YYYY-MM-DD格式')
    with connect() as connection:
        clock = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        latest = clock[0] if clock else None
        if as_of and latest and as_of > latest:
            fail('观察日期不能晚于已发布行情日')
        day = as_of or latest
        if not day:
            return {'instrument_id': symbol, 'as_of': None, 'items': [], 'total': 0, 'default_round_id': None}
        total = connection.execute('SELECT count(*) FROM tracking_round WHERE instrument_id=? AND entry_business_date<=?', (symbol, day)).fetchone()[0]
        records = connection.execute('''SELECT r.round_id,r.entry_business_date,r.entry_price_e8,
            r.entry_basis_json,r.status,r.end_business_date,r.strategy_id,s.name AS strategy_name
            FROM tracking_round r JOIN strategy s USING(strategy_id)
            WHERE r.instrument_id=? AND r.entry_business_date<=?
            ORDER BY CASE WHEN r.end_business_date IS NULL OR r.end_business_date>? THEN 0 ELSE 1 END,
                r.entry_business_date DESC,r.created_at_ms DESC,r.round_id DESC LIMIT ?''',
            (symbol, day, day, MAX_BASELINES)).fetchall()
    items = []
    for record in records:
        row = dict(record)
        basis = obj(row.pop('entry_basis_json'), {})
        row['entry_price'] = money(row.pop('entry_price_e8'))
        row['entry_basis'] = {'price_basis': basis.get('price_basis', 'unknown')}
        events, truncated = _historical_event_checks(basis.get('entry_checks'), row['entry_business_date'])
        row.update(historical_event_checks=events, historical_event_checks_truncated=truncated)
        if truncated:
            row['historical_event_checks_reason'] = '冻结条件证据超过100节点，仅展示有界摘要'
        # A later exit is not exposed while viewing an earlier observation day.
        if row['end_business_date'] and row['end_business_date'] > day:
            row.update(status='tracking', end_business_date=None)
        items.append(row)
    return {'instrument_id': symbol, 'as_of': day, 'items': items, 'total': total,
            'truncated': total > MAX_BASELINES,
            'default_round_id': items[0]['round_id'] if items else None,
            'scope': '仅本股票已保存的跟踪轮次；默认最近的跟踪中轮次，否则最近历史轮次。读取不创建跟踪。'}
