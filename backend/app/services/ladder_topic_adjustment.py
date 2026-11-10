"""Editor for private dated assignments, bound to verified ladder samples."""
from fastapi import HTTPException

from app.services.market_mainline_synthesis import classify_source_reason, has_source_reason
from app.services.market_node_replay import read_nodes
from app.services.market_node_topics import manual_topic, read_overrides, save_override
from app.services.market_service import context, market_page
from app.services.read_revisions import observation_day
from app.services.search_matching import phonetic_index
from app.services.supplement_access import supplement_reader


def _choice(topic_id, name, kind='concept'):
    return {'topic_id': topic_id, 'name': name, 'kind': kind}


def _projection(day, ladder, node, overrides):
    system = ladder['system_mainlines']
    eligible = {member['instrument_id'] for group in (*system['groups'], *system['other_groups'])
                for member in group['members']}
    eligible.update(member['instrument_id'] for member in (*system['individual_cues'], *system['missing_reason']))
    mainboard = {row['instrument_id']: row for row in node['attribution_candidates']}
    stocks = {symbol: {**row, 'mainboard': True, 'in_scope': True} for symbol, row in mainboard.items()}
    topics = {row['topic_id']: _choice(row['topic_id'], row['name'], row['kind'])
              for row in node['topic_ladders']['items']}
    for group in (*system['groups'], *system['other_groups']):
        key = group['key']
        if group['kind'] == 'direction' and group.get('manual_count', 0) < group['count']:
            topic = _choice('reason:' + key, group['name'])
            topics.setdefault(topic['topic_id'], topic)
    for row in ladder['items']:
        symbol = row['instrument_id']
        if symbol in stocks or symbol not in eligible:
            continue
        reason = row.get('reason')
        primary, _ = classify_source_reason(reason)
        facts = (row.get('board_presentation') or {}).get('height') or {}
        stocks[symbol] = {'instrument_id': symbol, 'name': row['name'], 'mainboard': False, 'in_scope': True,
                          'height': facts['value'] if facts.get('state') == 'confirmed' else None,
                          'reason': reason if isinstance(reason, str) and has_source_reason(reason) else None,
                          'automatic': _choice('reason:' + primary[0], primary[1])
                          if primary and primary[2] == 'direction' else None}
    for symbol, override in overrides.items():
        topics.setdefault(override['topic_id'], _choice(override['topic_id'], override['name'], override['kind']))
        stocks.setdefault(symbol, {'instrument_id': symbol, 'name': symbol, 'height': None, 'reason': None,
                                   'automatic': None, 'mainboard': False, 'in_scope': False})
    return {'date': day,
            'stocks': [{**row, 'override': overrides.get(row['instrument_id'])}
                       for row in sorted(stocks.values(), key=lambda row: (-(row['height'] or 0), row['instrument_id']))],
            'topics': sorted(topics.values(), key=lambda row: (row['name'], row['topic_id'])),
            'search_names': phonetic_index(row['name'] for row in (*stocks.values(), *topics.values()))}


@supplement_reader
def read_adjustments(day):
    day = observation_day(day)
    if day is None:
        raise HTTPException(422, '请选择交易日')
    market, status, day = context(day)
    ladder = market_page('ladder', day, market=market)
    node = read_nodes(day, market=market)
    if {ladder['snapshot_id'], node['snapshot_id'], market.status().get('snapshot_id')} != {status['snapshot_id']}:
        raise HTTPException(409, '行情代际已变化，请刷新后调整')
    return _projection(day, ladder, node, read_overrides(day))


def write_adjustment(payload):
    fields = {'date', 'instrument_id', 'topic_id', 'kind', 'name', 'expected_revision'}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise HTTPException(422, '人工归类字段不完整')
    day = observation_day(payload['date'])
    if day is None:
        raise HTTPException(422, '请选择交易日')
    symbol, topic_id, kind, name = (payload[key] for key in ('instrument_id', 'topic_id', 'kind', 'name'))
    if topic_id is None and kind is None and name is None:
        if payload['expected_revision'] is None:
            raise HTTPException(422, '没有可恢复的人工归类')
        return save_override(day, symbol, None, expected_revision=payload['expected_revision'])
    custom = None
    if topic_id is None:
        custom = manual_topic(name, kind)
    elif not isinstance(topic_id, str) or not 1 <= len(topic_id) <= 200 or kind is not None or name is not None:
        raise HTTPException(422, '请选择已有题材，或填写新题材和类型')
    data = read_adjustments(day)
    if not any(row['instrument_id'] == symbol and row['in_scope'] for row in data['stocks']):
        raise HTTPException(422, '只能调整该日已核验的收盘涨停样本')
    if custom:
        matches = [row for row in data['topics'] if row['kind'] == custom['kind']
                   and row['name'].casefold() == custom['name'].casefold()]
        chosen = matches[0] if len(matches) == 1 else custom
    else:
        chosen = next((row for row in data['topics'] if row['topic_id'] == topic_id), None)
        if chosen is None:
            raise HTTPException(422, '该题材已不在所选日的候选中，请刷新后选择')
    return save_override(day, symbol, chosen, expected_revision=payload['expected_revision'])
