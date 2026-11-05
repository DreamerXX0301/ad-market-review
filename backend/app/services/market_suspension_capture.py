"""Worker-side, bounded capture of dated public suspension records."""

from datetime import datetime, time
from hashlib import sha256
import json
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.infrastructure.storage import atomic_json
from app.services.market_suspensions import MAX_BYTES, STOCK_CODE, VERSION, _archive_item, _day, _path

CHINA = ZoneInfo('Asia/Shanghai')
PUBLIC_LIST = 'https://datapc.eastmoney.com/emdatacenter/tfg/list2'
MAX_RESPONSE = 256 * 1024
MAX_ROWS = 100
_ATTEMPTED = set()


def source_url(day):
    return PUBLIC_LIST + '?' + urlencode({'mkt': '1', 'st': 'SUSPEND_START_DATE', 'sr': '-1', 'fd': day})


def dated_records(body, day):
    """The date filter can return nearby sessions. Verify row times separately."""
    if (not _day(day) or not isinstance(body, dict) or type(body.get('code')) is not int
            or body['code'] != 0 or body.get('success') is not True):
        return None
    result = body.get('result')
    if not isinstance(result, dict):
        return None
    rows, count, pages = (result.get(k) for k in ('data', 'count', 'pages'))
    if (not isinstance(rows, list) or type(count) is not int or len(rows) != count
            or count > MAX_ROWS or type(pages) is not int or pages not in (0, 1) or count and pages != 1):
        return None
    opening = datetime.fromisoformat(f'{day} 09:30:00')
    closing = datetime.fromisoformat(f'{day} 15:00:00')
    output = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        symbol = row.get('SECUCODE')
        if (row.get('SECURITY_TYPE_CODE') != '058001001' or not isinstance(symbol, str)
                or not STOCK_CODE.fullmatch(symbol) or row.get('SECURITY_CODE') != symbol[:6]
                or row.get('SUSPEND_EXPIRE') not in ('连续停牌', '停牌一天', '全天停牌')):
            continue
        try:
            start = datetime.fromisoformat(row['SUSPEND_START_TIME'])
            end = datetime.fromisoformat(row['SUSPEND_END_TIME']) if row.get('SUSPEND_END_TIME') else None
            resume = datetime.fromisoformat(row['PREDICT_RESUME_DATE']) if row.get('PREDICT_RESUME_DATE') else None
            if (start > opening or (end is not None and end < closing)
                    or (resume is not None and resume <= closing)
                    or (end is None and (row['SUSPEND_EXPIRE'] != '连续停牌'
                                        or day != datetime.now(CHINA).date().isoformat()))):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if symbol in output:
            return None  # Never choose between inconsistent duplicate records.
        output[symbol] = {'instrument_id': symbol, 'state': 'suspended', 'full_session': True,
                          'start_date': day, 'end_date': day,
                          'source': 'eastmoney_choice', 'source_url': source_url(day)}
    return list(output.values())


def _document():
    path = _path()
    if not path.exists():
        return {'version': VERSION, 'items': [], 'captures': {}}
    with path.open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('停牌证据索引超出读取上限')
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get('version') != VERSION
            or not isinstance(data.get('items'), list) or not isinstance(data.get('captures', {}), dict)
            or any(_archive_item(row) is None for row in data['items'])):
        raise ValueError('停牌证据索引不一致，保留原文件')
    return data


def capture_for_date(day):
    """Write one dated receipt. Called only by the unique worker in production."""
    today = datetime.now(CHINA).date().isoformat()
    if not _day(day) or day > today:
        return False
    archive = _document()
    if day in archive.get('captures', {}):
        return True
    request = Request(source_url(day), headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urlopen(request, timeout=2) as response:
        if urlsplit(response.geturl()).netloc != 'datapc.eastmoney.com':
            return False
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        return False
    body = json.loads(raw)
    rows = dated_records(body, day)
    if rows is None:
        return False
    receipt = sha256(raw).hexdigest()
    archive.setdefault('captures', {})[day] = {'sha256': receipt, 'source_url': source_url(day),
        'captured_at': datetime.now(CHINA).isoformat(), 'count': len(rows)}
    existing = {(row['instrument_id'], row['start_date'], row['end_date'], row['source']) for row in archive['items']}
    archive['items'].extend(row for row in rows if (row['instrument_id'], day, day, row['source']) not in existing)
    if len(json.dumps(archive, ensure_ascii=False, separators=(',', ':')).encode()) >= MAX_BYTES:
        return False
    path = _path()
    atomic_json(path.parent / 'receipts' / f'{day}-{receipt}.json', {'sha256': receipt, 'body': raw.decode('utf-8')})
    atomic_json(path, archive)
    return True


def capture_after_market_snapshot(job, params):
    """Best effort once per session, outside the immutable snapshot's inputs."""
    now = datetime.now(CHINA)
    day = now.date().isoformat()
    if (params.get('suspension_evidence') != VERSION or job.get('target_date') != day
            or not time(9, 25) <= now.time() <= time(15, 5) or day in _ATTEMPTED):
        return False
    _ATTEMPTED.clear()
    _ATTEMPTED.add(day)
    try:
        return capture_for_date(day)
    except (OSError, ValueError, TypeError, RecursionError):
        return False
