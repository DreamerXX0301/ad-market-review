"""Shared current memberships for every stock detail entry and the watchlist.

This reads the existing local topic catalog and manual overrides only. The
business date describes current research membership, never historical causation.
"""
from app.core import connect
from app.services import topics, topic_members
from app.services.workspace_common import fail


CURRENT_TOPICS_SCOPE = ('当前业务日有效的本地行业/题材及人工归属；来源采集日独立，'
                        '不代表历史成员或当日上涨原因。缺少归属不能推断不属于。')


def current_memberships(market, symbols, *, expected_day=None):
    selected = set(symbols)
    catalog = topics._catalog_map(market)
    with connect() as connection:
        connection.execute('BEGIN')
        clock = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        day = clock[0] if clock else None
        if expected_day and day != expected_day:
            fail('行情业务日已更新，请重新打开个股资料', 409)
        state = topic_members._read_state(connection, day, catalog) if day else {}
    memberships = {symbol: [] for symbol in selected}
    for entry in state.values():
        definition = entry['public']
        if definition['archived']:
            continue
        tag = {key: definition.get(key) for key in
               ('topic_id', 'name', 'kind', 'source_observed_date', 'members_complete')}
        for symbol in entry['members'] & selected:
            memberships[symbol].append({**tag,**topic_members._membership(entry,symbol)})
    for tags in memberships.values():
        tags.sort(key=lambda item: (item['kind'], item['name'], item['topic_id']))
    return {'business_date': day, 'memberships': memberships, 'scope': CURRENT_TOPICS_SCOPE}


def stock_membership_fields(market, symbol):
    data = current_memberships(market, [symbol])
    tags = data['memberships'].get(symbol, [])
    return {'current_topics': tags,
            'current_industries': [tag for tag in tags if tag['kind'] == 'industry'],
            'current_topics_observed_date': data['business_date'],
            'current_topics_scope': data['scope']}
