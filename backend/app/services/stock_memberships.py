"""Shared current memberships for every stock detail entry and the watchlist.

This reads the existing local topic catalog and manual overrides only. The
business date describes current research membership, never historical causation.
"""
import re

from app.core import connect
from app.services import topics, topic_members
from app.services.workspace_common import fail


CURRENT_TOPICS_SCOPE = ('当前业务日有效的本地行业/题材及人工归属；来源采集日独立，'
                        '不代表历史成员或当日上涨原因。缺少归属不能推断不属于。')


def current_memberships(market, symbols, *, expected_day=None, connection_factory=None,
                        include_unregistered_provider=False, include_classifications=False):
    selected = set(symbols)
    catalog = topics._catalog_map(market)
    # AI readers inject a fixed query-only business connection; normal API
    # callers retain the existing connection lifecycle and current-day guard.
    with (connection_factory or connect)() as connection:
        connection.execute('BEGIN')
        clock = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        day = clock[0] if clock else None
        if expected_day and day != expected_day:
            fail('行情业务日已更新，请重新打开个股资料', 409)
        state = topic_members._read_state(connection, day, catalog) if day else {}
    memberships = {symbol: [] for symbol in selected}
    classifications = []
    for entry in state.values():
        definition = entry['public']
        if definition['archived']:
            continue
        tag = {key: definition.get(key) for key in
               ('topic_id', 'name', 'kind', 'source_observed_date', 'members_complete')}
        source = catalog.get(definition.get('provider_topic_key'), {})
        tag.update(source='local-manual' if definition['origin'] == 'manual' else source.get('source') or 'local-provider',
                   source_code=source.get('topic_code') or source.get('code') or definition.get('provider_topic_key'),
                   classification_level=source.get('classification_level', source.get('level')))
        if include_classifications:
            tag.update({key: source[key] for key in ('egress_allowed', 'ai_processing_allowed') if key in source})
            if definition['origin'] != 'manual':
                tag['members_complete'] = (
                    tag['members_complete'] is True
                    and isinstance(source.get('instrument_ids', source.get('members')), list)
                    and all(re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', symbol) for symbol in entry['provider'])
                )
            classifications.append(tag)
        for symbol in entry['members'] & selected:
            memberships[symbol].append({**tag,**topic_members._membership(entry,symbol)})
    if include_unregistered_provider:
        # A rank already displayed source categories before the editable topic
        # library was initialized. Preserve those dated source facts, while a
        # registered category (including archived/excluded) is governed solely
        # by its current local state rather than being reintroduced here.
        represented = {entry['public'].get('provider_topic_key') for entry in state.values()}
        visited = set()
        for source in catalog.values():
            keys = {source.get(key) for key in ('topic_id', 'topic_code', 'code') if isinstance(source.get(key), str) and source[key]}
            ident = source.get('topic_id') or source.get('topic_code') or source.get('code')
            if (not isinstance(ident, str) or not ident or ident in visited or represented.intersection(keys)
                    or source.get('kind') not in ('concept', 'industry') or not isinstance(source.get('name'), str)):
                continue
            visited.add(ident)
            members = source.get('instrument_ids', [])
            if not isinstance(members, list):
                continue
            tag = {'topic_id': ident, 'name': source.get('name'), 'kind': source.get('kind'),
                   'source_observed_date': source.get('observed_date'), 'members_complete': source.get('members_complete'),
                   'source': source.get('source') or 'local-provider',
                   'source_code': source.get('topic_code') or source.get('code') or ident,
                   'classification_level': source.get('classification_level', source.get('level')),
                   'origin': 'provider', 'manual_override': None, 'provider_member': True}
            if include_classifications:
                tag.update({key: source[key] for key in ('egress_allowed', 'ai_processing_allowed') if key in source})
                tag['members_complete'] = tag['members_complete'] is True and all(
                    isinstance(symbol, str) and re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', symbol)
                    for symbol in members)
                classifications.append(tag)
            for symbol in selected.intersection(value for value in members if isinstance(value, str)):
                memberships[symbol].append(dict(tag))
    for tags in memberships.values():
        tags.sort(key=lambda item: (item['kind'], item['name'], item['topic_id']))
    return {'business_date': day, 'memberships': memberships, 'scope': CURRENT_TOPICS_SCOPE,
            **({'classifications': classifications} if include_classifications else {})}


def stock_membership_fields(market, symbol):
    data = current_memberships(market, [symbol])
    tags = data['memberships'].get(symbol, [])
    return {'current_topics': tags,
            'current_industries': [tag for tag in tags if tag['kind'] == 'industry'],
            'current_topics_observed_date': data['business_date'],
            'current_topics_scope': data['scope']}
