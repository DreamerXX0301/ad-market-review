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



def validate_discovery(row):
    try:
        encoded, day = row['payload_json'], row['trade_date']
        if len(encoded.encode()) > 3 * 1024 * 1024 or sha256(encoded.encode()).hexdigest() != row['content_sha256']:
            raise ValueError('Auction discovery SHA or size differs')
        payload = json.loads(encoded)
        if (date.fromisoformat(day).isoformat() != day or payload.get('date') != day
                or payload.get('version') != 'auction-discovery-v1'
                or payload.get('state') not in ('available', 'partial', 'unavailable')):
            raise ValueError('Auction discovery date or version differs')
        sources, evidence, codes = (payload[key] for key in ('sources', 'evidence', 'requested'))
        if (any(not isinstance(value, list) for value in (sources, evidence, codes))
                or len(sources) > 4 or len(evidence) > 4 or len(codes) > 100
                or any(not isinstance(code, str) or not SYMBOL.fullmatch(code) for code in codes)
                or len(set(codes)) != len(codes)):
            raise ValueError('Auction discovery exceeds bounded scope')
        expected = {f'opportunities:{page}': ('/api/a-share/special-data/auction-stock-opportunities',
            {'market': 'a_share', 'sort_by': 'warn_time', 'sort_order': 'desc', 'page': page, 'size': 20})
            for page in range(1, 4)}
        expected['final_quotes'] = ('/api/a-share/auction/snapshot', {'thscodes': ','.join(codes), 'stage': 'final'})
        received, originals = {}, {}
        seen = set()
        keys = ('name', 'source', 'endpoint', 'params', 'requested_at', 'received_at', 'state', 'bytes', 'sha256')
        for item in sources:
            name = item['name']
            if (name in seen or expected.get(name) != (item.get('endpoint'), item.get('params'))
                    or item.get('source') != 'fuyao' or not _window(item.get('requested_at'), day)):
                raise ValueError('Auction discovery source request differs')
            seen.add(name)
            if item.get('state') == 'received':
                received[name] = {key: item.get(key) for key in keys}
        total = 0
        for item in evidence:
            name = item['name']
            body = item['body']
            raw = _canonical(body)
            total += len(raw)
            if (name in originals or not isinstance(body, dict) or len(raw) > 1024 * 1024
                    or item.get('bytes') != len(raw) or item.get('sha256') != sha256(raw).hexdigest()
                    or not _window(item.get('received_at'), day)):
                raise ValueError('Auction discovery source evidence differs')
            originals[name] = {key: item.get(key) for key in keys}
        if total > 2 * 1024 * 1024 or received != originals:
            raise ValueError('Auction discovery evidence closure differs')
        captured = payload.get('captured_at_ms')
        last_received = max((int(datetime.fromisoformat(item['received_at']).timestamp() * 1000)
                             for item in originals.values()), default=None)
        if captured != last_received or (captured is not None and captured > row['recorded_at_ms']):
            raise ValueError('Auction discovery capture time differs')
        stocks = payload['stocks']
        supplement = payload['supplement']
        coverage = payload['coverage']
        if (len(stocks) != coverage['valid'] or len(supplement['items']) != supplement['valid']
                or not 0 <= supplement['valid'] <= supplement['requested'] <= 40
                or not 0 <= coverage['valid'] <= coverage['candidates'] <= coverage['events'] <= 60
                or not 0 <= coverage['pages'] <= 3 or len(payload['directions']) > 12):
            raise ValueError('Auction discovery row counts differ')
        found = {item['instrument_id']: item for item in stocks}
        reported = [item['instrument_id'] for item in [*stocks, *supplement['items']]]
        if (len(set(reported)) != len(reported) or not set(reported) <= set(codes)
                or reported and 'final_quotes' not in originals):
            raise ValueError('Auction discovery stocks differ from quote evidence')
        for direction in payload['directions']:
            members = direction['stocks']
            if (len(members) != direction['stock_count'] or len(members) < 2
                    or len({item['instrument_id'] for item in members}) != len(members)
                    or any(found.get(item['instrument_id']) != item or item['auction_pct'] <= 0 for item in members)):
                raise ValueError('Auction discovery direction membership differs')
        return payload
    except (KeyError, TypeError, AttributeError, OverflowError, json.JSONDecodeError) as error:
        raise ValueError('Auction discovery snapshot or source evidence is invalid') from error


def validate_discoveries(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_auction_discovery'").fetchone():
        return
    cursor = connection.execute('''SELECT d.*, s.snapshot_id AS parent_id FROM market_auction_discovery d
        LEFT JOIN market_auction_snapshot s ON s.snapshot_id=d.primary_snapshot_id
        AND s.job_id=d.job_id AND s.trade_date=d.trade_date''')
    columns = [item[0] for item in cursor.description]
    for values in cursor:
        row = dict(zip(columns, values))
        if not row['parent_id']:
            raise ValueError('Auction discovery does not match a primary snapshot')
        validate_discovery(row)


def validate_snapshots(connection):
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_auction_snapshot'").fetchone():
        cursor = connection.execute('SELECT * FROM market_auction_snapshot')
        columns = [item[0] for item in cursor.description]
        for row in cursor:
            validate_snapshot(dict(zip(columns, row)))
    validate_discoveries(connection)
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
