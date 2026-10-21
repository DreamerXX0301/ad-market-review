"""Bounded, read-only discovery of a stock's own frozen tracking references."""
from datetime import date
import re

from app.core import connect
from app.services.workspace_common import fail, money, obj

MAX_BASELINES = 100


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
        # A later exit is not exposed while viewing an earlier observation day.
        if row['end_business_date'] and row['end_business_date'] > day:
            row.update(status='tracking', end_business_date=None)
        items.append(row)
    return {'instrument_id': symbol, 'as_of': day, 'items': items, 'total': total,
            'truncated': total > MAX_BASELINES,
            'default_round_id': items[0]['round_id'] if items else None,
            'scope': '仅本股票已保存的跟踪轮次；默认最近的跟踪中轮次，否则最近历史轮次。读取不创建跟踪。'}
