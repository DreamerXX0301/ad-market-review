"""Compare the same verified discovery stocks at auction and the first intraday observation."""
from collections import Counter
from datetime import datetime, time
from math import isfinite
from statistics import median

from app.services.noon_collection import CHINA

SCOPE = ('同日竞价异动样本，按同代码、同昨收核对竞价终态和10:00开盘首轮。'
         '涨幅均相对昨收，变化至少0.10个百分点才计入扩大或回落。转涨与涨幅扩大可重叠。')
CHANGE_THRESHOLD_PP = 0.1


def discovery_metadata(connection, day):
    if not connection.execute("""SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='market_auction_discovery'""").fetchone():
        return None
    row = connection.execute('''SELECT primary_snapshot_id,content_sha256,recorded_at_ms
        FROM market_auction_discovery WHERE trade_date=?''', (day,)).fetchone()
    return dict(row) if row else None


def opening_metadata(connection, day):
    if not connection.execute("""SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='market_intraday_capture'""").fetchone():
        return None
    row = connection.execute('''SELECT c.snapshot_id,c.content_sha256,j.finished_at_ms
        FROM market_intraday_capture c JOIN job j USING(job_id)
        WHERE c.trade_date=? AND c.slot='1000' AND c.snapshot_id IS NOT NULL
          AND j.status IN ('succeeded','partial') AND j.finished_at_ms IS NOT NULL''', (day,)).fetchone()
    return dict(row) if row else None


def read_opening(connection, day, identity):
    from app.services.intraday_service import decode_snapshot

    if identity is None:
        return None
    row = connection.execute('''SELECT * FROM market_intraday_capture
        WHERE trade_date=? AND slot='1000' AND snapshot_id=? AND content_sha256=?''',
        (day, identity['snapshot_id'], identity['content_sha256'])).fetchone()
    if row is None:
        return None
    value = decode_snapshot(row)
    if (not model_input_allowed(value)
            or identity['finished_at_ms'] < int(_stamp(value['collected_end_at']).timestamp() * 1000)):
        return None
    return value


def model_input_allowed(value):
    return (value.get('egress_allowed') is not False and value.get('ai_processing_allowed') is not False
            and all(row.get('egress_allowed') is not False and row.get('ai_processing_allowed') is not False
                    for row in value.get('source_checks', ()) if isinstance(row, dict)))


def _valid_number(value):
    return type(value) in (int, float) and isfinite(value)


def _stamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('缺少时区')
    return parsed.astimezone(CHINA)


def _summary(rows):
    return {'comparable': len(rows),
            'strengthened': sum(row['change_pp'] >= CHANGE_THRESHOLD_PP for row in rows),
            'faded': sum(row['change_pp'] <= -CHANGE_THRESHOLD_PP for row in rows),
            'turned_positive': sum(row['auction_pct'] <= 0 < row['opening_pct'] for row in rows)}


def compare(discovery, opening):
    if (not discovery or discovery.get('state') not in ('available', 'partial')
            or not opening or opening.get('slot') != '1000'
            or opening.get('date') != discovery.get('date')):
        return None
    try:
        start = _stamp(opening['collected_start_at'])
        end = _stamp(opening['collected_end_at'])
        captured = datetime.fromtimestamp(discovery['captured_at_ms'] / 1000, CHINA)
        published = datetime.fromtimestamp(discovery['recorded_at_ms'] / 1000, CHINA)
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        return None
    day = discovery['date']
    if (any(value.date().isoformat() != day for value in (start, end, captured, published))
            or not time(9, 25, 30) <= captured.time() < time(9, 30)
            or not captured <= published <= start <= end
            or not time(10) <= start.time() <= end.time() < time(10, 3)):
        return None
    auction = discovery.get('stocks') or ()
    quotes = opening.get('comparison_input', {}).get('stocks', ())
    codes = {row['instrument_id'] for row in auction}
    matches = [row for row in quotes if row.get('instrument_id') in codes]
    occurrences = Counter(row['instrument_id'] for row in matches)
    by_code = {row['instrument_id']: row for row in matches if occurrences[row['instrument_id']] == 1}
    compared = {}
    for first in auction:
        code = first['instrument_id']
        current = by_code.get(code, {})
        before, after = first.get('auction_pct'), current.get('change_pct')
        previous_close = first.get('pre_close_price')
        if (current.get('price_basis') != 'none' or current.get('currency') != 'CNY'
                or not all(map(_valid_number, (previous_close, before, after, current.get('prev_close'))))
                or previous_close <= 0 or previous_close != current['prev_close']):
            continue
        compared[code] = {'instrument_id': code, 'name': first['name'], 'auction_pct': before,
                          'opening_pct': after, 'change_pp': round(after - before, 4)}
    groups = []
    for direction in discovery.get('directions', ()):
        rows = [compared[row['instrument_id']] for row in direction['stocks']
                if row['instrument_id'] in compared]
        if len(rows) < 2:
            continue
        groups.append({'instrument_id': direction['instrument_id'], 'name': direction['name'],
                       'members_observed_date': direction.get('members_observed_date'),
                       'sample_count': direction['stock_count'], **_summary(rows),
                       'median_change_pp': round(median(row['change_pp'] for row in rows), 4)})
    examples = sorted(compared.values(), key=lambda row: (-abs(row['change_pp']), row['instrument_id']))
    return {'scope': SCOPE, 'sample_count': len(codes), **_summary(list(compared.values())),
            'opening_at': opening['collected_end_at'], 'opening_snapshot_id': opening['id'],
            'directions': groups[:4], 'examples': examples[:3]}
