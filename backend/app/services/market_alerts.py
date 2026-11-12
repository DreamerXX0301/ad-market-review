"""Read-only union of independent manual and automatic price reminders."""
from fastapi import HTTPException
from app.core import connect

from . import line_alerts, automatic_price_alerts


def _source(items, kind):
    return [{**item, 'source_kind': kind} for item in items]


def _ordered(items):
    return sorted(items, key=lambda item: (item.get('trade_date') or '',
                                          item.get('created_at_ms') or 0,
                                          item.get('event_id') or ''), reverse=True)


def list_alerts(as_of=None, limit=100, scope='all'):
    # Each side is indexed and bounded. At most 2*limit rows are merged; the
    # persistent counts are independent of the list truncation.
    with connect() as connection:
        connection.execute('BEGIN')
        manual = line_alerts.list_alerts(as_of, limit, scope, _connection=connection)
        automatic = automatic_price_alerts.list_alerts(as_of, limit, scope, _connection=connection)
        if (manual['business_date'], manual['as_of']) != (automatic['business_date'], automatic['as_of']):
            raise HTTPException(409, '读取提醒期间业务日已更新，请重新读取')
    return {
        **manual,
        'items': _ordered([*_source(manual['items'], 'manual_line'),
                           *_source(automatic['items'], 'automatic_price_volume')])[:limit],
        **{key: manual.get(key, 0) + automatic.get(key, 0)
           for key in ('total', 'today_total', 'history_total', 'rules_total')},
        'latest_check': manual['latest_check'] or automatic['latest_check'],
        'latest_checks': {'manual_line': manual['latest_check'],
                          'automatic_price_volume': automatic['latest_check']},
        'unknowns': [*_source(manual['unknowns'], 'manual_line'),
                     *_source(automatic['unknowns'], 'automatic_price_volume')],
    }


def alert_summary():
    with connect() as connection:
        connection.execute('BEGIN')
        manual = line_alerts.alert_summary(_connection=connection)
        automatic = automatic_price_alerts.alert_summary(_connection=connection)
        if manual['business_date'] != automatic['business_date']:
            raise HTTPException(409, '读取提醒期间业务日已更新，请重新读取')
    return {'business_date': manual['business_date'],
            'total': manual['total'] + automatic['total'], 'limit': 5,
            'items': _ordered([*_source(manual['items'], 'manual_line'),
                               *_source(automatic['items'], 'automatic_price_volume')])[:5]}


def latest_by_stock(connection, identifiers, day):
    manual = line_alerts.latest_by_stock(connection, identifiers, day)
    automatic = automatic_price_alerts.latest_by_stock(connection, identifiers, day)
    combined = _ordered([*_source(manual.values(), 'manual_line'),
                         *_source(automatic.values(), 'automatic_price_volume')])
    result = {}
    for event in combined:
        result.setdefault(event['instrument_id'], event)
    return result
