"""Standalone auction integrity checks shared by readers and backup tooling.

Only the supplied row/connection is inspected. Importing this file has no app
configuration, filesystem, network, migration or credential side effects.
"""
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo

CHINA = ZoneInfo('Asia/Shanghai')
SYMBOL = re.compile(r'[0-9]{6}\.(?:SH|SZ|BJ)\Z')
LIMIT = 4 * 1024 * 1024


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _window(value, day):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            return False
        local = stamp.astimezone(CHINA)
        return local.date().isoformat() == day and time(9, 25) <= local.time().replace(tzinfo=None) < time(9, 30)
    except (TypeError, ValueError):
        return False


def validate_snapshot(row):
    try:
        encoded, day = row['payload_json'], row['trade_date']
        if not isinstance(encoded, str) or len(encoded.encode()) > LIMIT or sha256(encoded.encode()).hexdigest() != row['content_sha256']:
            raise ValueError('Auction snapshot content SHA or size differs')
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('Auction snapshot date is invalid')
        payload = json.loads(encoded)
        if (not isinstance(payload, dict) or payload.get('date') != day
                or payload.get('version') != 'auction-snapshot-v1'
                or payload.get('collected_start_at') != row['collected_start_at']
                or payload.get('collected_end_at') != row['collected_end_at']
                or not _window(row['collected_start_at'], day) or not _window(row['collected_end_at'], day)):
            raise ValueError('Auction snapshot identity or capture window differs')
        universe = payload.get('universe')
        items = universe.get('items') if isinstance(universe, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValueError('Auction frozen universe is invalid')
        codes = [item.get('instrument_id') if isinstance(item, dict) else None for item in items]
        if any(not isinstance(code, str) or not SYMBOL.fullmatch(code) for code in codes) or len(set(codes)) != len(codes):
            raise ValueError('Auction frozen universe identities differ')
        quotes = payload.get('quotes')
        normalized = quotes.get('items') if isinstance(quotes, dict) else None
        if not isinstance(normalized, list) or [item.get('instrument_id') if isinstance(item, dict) else None for item in normalized] != codes:
            raise ValueError('Auction quotes differ from frozen universe')
        sources, evidence = payload.get('sources'), payload.get('evidence')
        if not isinstance(sources, list) or not isinstance(evidence, list) or len(sources) > 3 or len(evidence) > 3:
            raise ValueError('Auction evidence is invalid')
        by_name = {}
        total = 0
        keys = ('name', 'source', 'endpoint', 'params', 'requested_at', 'received_at', 'state', 'bytes', 'sha256')
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get('body'), dict):
                raise ValueError('Auction source body is missing')
            name = item.get('name')
            if name not in ('calendar', 'auction', 'benchmark') or name in by_name or item.get('state') != 'received':
                raise ValueError('Auction source identity differs')
            raw = _canonical(item['body']); total += len(raw)
            if (len(raw) > 1024 * 1024 or item.get('bytes') != len(raw)
                    or item.get('sha256') != sha256(raw).hexdigest()
                    or not _window(item.get('requested_at'), day) or not _window(item.get('received_at'), day)):
                raise ValueError('Auction source SHA, size or window differs')
            by_name[name] = {key: item.get(key) for key in keys}
        if total > 2 * 1024 * 1024 or 'calendar' not in by_name or 'auction' not in by_name:
            raise ValueError('Auction source closure is incomplete')
        received = {}
        for item in sources:
            if not isinstance(item, dict) or item.get('name') not in ('calendar', 'auction', 'benchmark'):
                raise ValueError('Auction source metadata is invalid')
            if item.get('state') == 'received':
                name = item['name']
                if name in received:
                    raise ValueError('Auction source metadata is duplicated')
                received[name] = {key: item.get(key) for key in keys}
        if received != by_name:
            raise ValueError('Auction source metadata and original closure differ')
        expected = {'calendar': ('/api/a-share/calendar/trading-days', {}),
                    'auction': ('/api/a-share/auction/snapshot', {'stage': 'final', 'thscodes': ','.join(codes)}),
                    'benchmark': ('/api/a-share/auction/short-term-benchmark', {'date': day})}
        for name, item in by_name.items():
            endpoint, params = expected[name]
            if item['source'] != 'fuyao' or item['endpoint'] != endpoint or item['params'] != params:
                raise ValueError('Auction source request differs from frozen scope')
        return payload
    except (KeyError, TypeError, OverflowError, json.JSONDecodeError) as error:
        raise ValueError('Auction snapshot or source evidence is invalid') from error


def validate_snapshots(connection):
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_auction_snapshot'").fetchone():
        cursor = connection.execute('SELECT * FROM market_auction_snapshot')
        columns = [item[0] for item in cursor.description]
        for row in cursor:
            validate_snapshot(dict(zip(columns, row)))
    validate_purges(connection)


def validate_purges(connection):
    """清理回执没有正文；核验身份、自然日窗口及永久日账闭包。"""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_auction_purge'").fetchone():
        return
    cursor = connection.execute('SELECT * FROM market_auction_purge')
    columns = [item[0] for item in cursor.description]
    for values in cursor:
        row = dict(zip(columns, values))
        try:
            anchor = date.fromisoformat(row['anchor_date'])
            cutoff = (anchor - timedelta(days=27)).isoformat()
            if (anchor.isoformat() != row['anchor_date'] or cutoff != row['cutoff_date']
                    or date.fromisoformat(row['trade_date']).isoformat() != row['trade_date']
                    or row['trade_date'] >= cutoff
                    or not re.fullmatch('[0-9a-f]{64}', row['content_sha256'])
                    or not 0 < row['payload_bytes'] <= LIMIT or not 0 <= row['evidence_count'] <= 3):
                raise ValueError('Auction purge identity or retention window differs')
            capture = connection.execute('SELECT job_id FROM market_auction_capture WHERE trade_date=?', (row['trade_date'],)).fetchone()
            if not capture or capture[0] != row['source_job_id']:
                raise ValueError('Auction purge differs from permanent capture ledger')
            owner = connection.execute('SELECT kind,status,parameters_json FROM job WHERE job_id=?', (row['cleanup_job_id'],)).fetchone()
            if (not owner or owner[0] != 'increment' or owner[1] not in ('succeeded', 'partial', 'no_change')
                    or json.loads(owner[2]).get('auction_retention_policy') != 'auction-retention-28d-v1'):
                raise ValueError('Auction purge has no successful authorized increment')
            retained = connection.execute('SELECT snapshot_id,job_id,content_sha256 FROM market_auction_snapshot WHERE trade_date=?', (row['trade_date'],)).fetchone()
            if row['status'] == 'purged':
                if retained or row['purged_at_ms'] is None or row['reason'] is not None or row['evidence_count'] < 2:
                    raise ValueError('Auction purge tombstone conflicts with retained content')
            elif (row['status'] not in ('protected', 'warning') or row['purged_at_ms'] is not None
                    or not retained or tuple(retained) != (row['snapshot_id'], row['source_job_id'], row['content_sha256'])):
                raise ValueError('Auction pending purge receipt differs from retained content')
        except (KeyError, TypeError, OverflowError, json.JSONDecodeError) as error:
            raise ValueError('Auction purge receipt is invalid') from error
