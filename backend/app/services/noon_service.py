"""每天一次的手动午评；读取不触发取数，不参与日线或策略发布。"""
from __future__ import annotations

from datetime import date, datetime, time
import gzip
from hashlib import sha256
import json
import re
import sqlite3

from fastapi import HTTPException

from app.core import connect, digest, dumps, get_market, new_id
from app.infrastructure.credentials import CredentialStore
from app.services import task_service as tasks
from app.services.noon_collection import (CHINA, VERSION, CAPTURE_START, CAPTURE_END, Collector, WindowExpired,
    STOCK_CODE, SECTOR_CODE, NOON_EVENT_POOLS, canonical, finite, in_window, items, paged, trading_days,
    verified_empty_noon_pool_page)
from app.services.noon_analysis import (INDEX_NAMES, board_summary, build_analysis, build_summary,
    normalize_pool, normalize_quotes, sector_members)
from app.services.noon_retention import WINDOW_DAYS

RETENTION = {'calendar_days': WINDOW_DAYS, 'automatic': True, 'trigger': 'after_increment',
             'notice': f'保留{WINDOW_DAYS}个自然日，到期随成功盘后增量自动清理，未运行增量时暂留。'}


def now_china():
    return datetime.now(CHINA)


def day_value(value):
    try:
        valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, '午评日期须为YYYY-MM-DD形式的真实日期')
    return value


def capture_window(day, at=None):
    now = (at or now_china()).astimezone(CHINA)
    target = date.fromisoformat(day)
    allowed = in_window(day, now) and target.weekday() < 5
    reason = None
    if day != now.date().isoformat():
        reason = '午评只可采集今天，历史日期不补造午间数据'
    elif target.weekday() >= 5:
        reason = '周末不创建午评；节假日由工作进程进一步核验来源交易日历'
    elif not allowed:
        reason = '午评需在本交易日北京时间11:31至13:00前手动采集'
    return {'allowed': allowed, 'reason': reason,
            'start_at': datetime.combine(target, CAPTURE_START, CHINA).isoformat(),
            'end_at': datetime.combine(target, CAPTURE_END, CHINA).isoformat(),
            'timezone': 'Asia/Shanghai', 'calendar_verified': False}


def _configured(connection):
    row = connection.execute('SELECT enabled FROM provider_connection WHERE connection_id=?', ('fuyao',)).fetchone()
    return bool(row and row['enabled'] and CredentialStore().exists('fuyao'))


def _state(connection, ledger, snapshot=None):
    if snapshot is not None:
        return 'available'
    if ledger is None:
        return 'not_collected'
    if connection.execute("SELECT 1 FROM market_noon_purge WHERE trade_date=? AND status='purged'", (ledger['trade_date'],)).fetchone():
        return 'purged'
    status = connection.execute('SELECT status FROM job WHERE job_id=?', (ledger['job_id'],)).fetchone()
    value = status['status'] if status else 'failed'
    return value if value in ('queued', 'running', 'paused', 'failed', 'cancelled') else 'failed'


def retained_empty_event_pools(snapshot, payload):
    """Restore explicit zero pools from their exact original noon receipts."""
    day = snapshot['trade_date']
    calendar, events = payload.get('calendar'), payload.get('events')
    if (payload.get('version') != VERSION or payload.get('date') != day
            or not isinstance(calendar, dict) or calendar.get('verified') is not True
            or not isinstance(events, dict) or not isinstance(payload.get('sources'), list)):
        return {}
    return {key: pool for key in NOON_EVENT_POOLS
            if (pool := _retained_empty_pool(snapshot, payload, key)) is not None}


def _retained_empty_pool(snapshot, payload, key):
    day = snapshot['trade_date']
    pool = payload['events'].get(key)
    if (not isinstance(pool, dict) or pool.get('state') != 'unknown' or pool.get('count') is not None
            or type(pool.get('declared_total')) is not int or pool['declared_total'] != 0
            or pool.get('items') != [] or type(pool.get('excluded_rows')) is not int or pool['excluded_rows'] != 0):
        return None
    first_page = f'{key}_page_1'
    candidates = [record for record in payload['sources'] if isinstance(record, dict)
                  and record.get('name') == first_page]
    if len(candidates) != 1:
        return None
    source = candidates[0]
    if source.get('path') != f'noon/{snapshot["job_id"]}/{first_page}.json.gz':
        return None
    # A verified empty provider envelope should be tiny. Never open arbitrary
    # receipts or iterate the historical raw directory during a page read.
    if not all(type(source.get(key)) is int and 0 < source[key] <= 16 * 1024 for key in ('bytes', 'raw_bytes')):
        return None
    from app.core import raw_dir
    from app.services.noon_retention import source_bytes
    try:
        start, end = (datetime.fromisoformat(snapshot[key]) for key in ('collected_start_at', 'collected_end_at'))
        started, received = (datetime.fromisoformat(source[key]) for key in ('started_at', 'received_at'))
        if (start.tzinfo is None or end.tzinfo is None or not in_window(day, start) or not in_window(day, end)
                or not start <= started <= received <= end
                or any(payload.get(key) != snapshot[key] for key in ('collected_start_at', 'collected_end_at'))):
            return None
        raw = gzip.decompress(source_bytes(raw_dir(), source))
        if len(raw) != source['raw_bytes'] or not verified_empty_noon_pool_page(json.loads(raw), day, source, key):
            return None
    except (OSError, ValueError, TypeError, KeyError, EOFError, RecursionError):
        return None
    result = {**normalize_pool([], 0, True, {}, key),
              'source_sha256': source['sha256'], 'source_timestamp': source['source_timestamp'],
              'scope': '扶摇本次午间来源池；已核验原始成功零结果回执。'}
    if key == 'limit_up':
        result['board_summary'] = board_summary(result)
    return result


def _snapshot_view(row):
    if row is None:
        return None
    value = dict(row)
    encoded = value.pop('payload_json')
    if sha256(encoded.encode()).hexdigest() != value['content_sha256']:
        raise HTTPException(409, '午评内容SHA核验失败，原记录保留，请检查存储')
    value['payload'] = json.loads(encoded)
    from app.services.market_suspensions import suspensions_for_date
    suspensions = suspensions_for_date(value['trade_date'])
    value['stock_suspensions'] = {
        stock['instrument_id']: proof for stock in value['payload'].get('stocks', [])
        if (proof := noon_stock_suspension(stock, suspensions)) is not None
    } if suspensions else {}
    from app.services.noon_workbench import build_workbench
    from app.services.noon_membership_context import build_member_limit_context
    from app.services.noon_prior_events import build_prior_event_context
    value['prior_event_context'] = build_prior_event_context(value['payload'])
    context_payload = value['payload']
    previous = context_payload.get('previous_review') or {}
    if value['prior_event_context'].get('date') == previous.get('date'):
        cohorts = dict(previous.get('event_cohorts') or {})
        for key, item in (value['prior_event_context'].get('event_cohorts') or {}).items():
            if key not in cohorts:
                cohorts[key] = item
        context_payload = {**context_payload, 'previous_review': {**previous, 'event_cohorts': cohorts}}
    value['membership_context'] = build_member_limit_context(context_payload)
    from app.services.noon_close_context import retained_close_classifications
    from app.services.pane_read_cache import read_block

    restored_pools = retained_empty_event_pools(value, value['payload'])
    close_classifications = retained_close_classifications(value['payload'])
    workbench_payload = value['payload']
    if restored_pools:
        workbench_payload = {**workbench_payload, 'events': {**workbench_payload['events'], **restored_pools}}

    def project_workbench():
        result = build_workbench(workbench_payload, membership_context=value['membership_context'],
            prior_event_context=value['prior_event_context'], close_classifications=close_classifications)
        if restored_pools:
            result['verified_empty_event_pools'] = restored_pools
            if 'limit_down' in restored_pools:
                result['limit_down_source_pool'] = restored_pools['limit_down']
        return result

    # Archive recovery and the exact zero-pool raw receipts are reverified above.
    # Their current projections also distinguish lost proof from a confirmed 0.
    evidence = (value['snapshot_id'], value['content_sha256'], value['membership_context'],
                value['prior_event_context'], restored_pools, close_classifications)
    value['workbench'] = read_block('noon-workbench', value['trade_date'], evidence,
                                    project_workbench, live_snapshot=True)
    value['date'] = value.pop('trade_date')
    value.pop('sources_json', None)
    return value


def noon_stock_suspension(stock, suspensions):
    """A real intraday transaction takes precedence over halt evidence."""
    proof = suspensions.get(stock.get('instrument_id'))
    if proof is not None and not any(finite(stock.get(key)) and stock[key] > 0
                                     for key in ('volume', 'amount')):
        return proof
    return None


def read_stock_quote(snapshot_id, instrument_id):
    """Read a single unadjusted stock quote from an immutable noon capture."""
    if not re.fullmatch(r'noon_[0-9a-f]{32}', snapshot_id):
        raise HTTPException(422, '午评快照身份无效')
    if not STOCK_CODE.fullmatch(instrument_id):
        raise HTTPException(422, '股票代码无效')
    with connect() as connection:
        row = connection.execute('''SELECT trade_date,collected_start_at,collected_end_at,
            content_sha256,payload_json FROM market_noon_snapshot WHERE snapshot_id=?''',
            (snapshot_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '午评正文未留存或已清理')
    encoded = row['payload_json'].encode()
    if len(encoded) > 8 * 1024 * 1024 or sha256(encoded).hexdigest() != row['content_sha256']:
        raise HTTPException(409, '午评内容SHA核验失败，原记录保留')
    try:
        payload = json.loads(encoded)
    except (ValueError, RecursionError) as error:
        raise HTTPException(409, '午评正文无法读取') from error
    if (not isinstance(payload, dict) or payload.get('version') != VERSION
            or payload.get('date') != row['trade_date']
            or any(payload.get(key) != row[key] for key in ('collected_start_at', 'collected_end_at'))):
        raise HTTPException(409, '午评日期或采集时间核验失败')
    calendar = payload.get('calendar')
    previous_day = calendar.get('previous_day') if isinstance(calendar, dict) and calendar.get('verified') is True else None
    result = {key: row[key] for key in ('trade_date', 'collected_start_at', 'collected_end_at')}
    result.update(snapshot_id=snapshot_id, previous_trade_date=previous_day, quote=None, reason=None)
    stocks = payload.get('stocks')
    matches = [quote for quote in stocks if isinstance(quote, dict)
               and quote.get('instrument_id') == instrument_id] if isinstance(stocks, list) else []
    if len(matches) != 1:
        return {**result, 'reason': '午评未留存该股票的唯一报价'}
    quote = matches[0]
    from app.services.market_suspensions import suspensions_for_date
    suspension = noon_stock_suspension(quote, suspensions_for_date(row['trade_date']))
    if suspension is not None:
        return {**result, 'suspension': suspension, 'reason': '该股票当日停牌，无午间临时日 K'}
    fields = ('open', 'high', 'low', 'latest_price')
    if (quote.get('price_basis') != 'none' or any(quote.get(key) != row['trade_date']
            for key in ('trade_date', 'quote_date', 'date') if key in quote)
            or any(not finite(quote.get(key)) or quote[key] <= 0 for key in fields)):
        return {**result, 'reason': '午间开盘、最高、最低或最新价未能核验'}
    if not quote['low'] <= min(quote['open'], quote['latest_price']) <= max(
            quote['open'], quote['latest_price']) <= quote['high']:
        return {**result, 'reason': '午间报价超出该快照记录的最高和最低价'}
    vetted = {key: quote[key] for key in ('instrument_id', 'price_basis', *fields)}
    for key in ('prev_close', 'volume', 'amount', 'change_pct'):
        value = quote.get(key)
        vetted[key] = value if finite(value) and (
            key == 'change_pct' or value > 0 or key in ('volume', 'amount') and value == 0
        ) else None
    from app.services.chart_patterns import attach_intraday_board_patterns
    attach_intraday_board_patterns(vetted, {**payload, 'id': snapshot_id, 'slot': 'noon'},
                                   content_sha256=row['content_sha256'])
    return {**result, 'quote': vetted}


def _dates(connection):
    rows = connection.execute('''SELECT c.trade_date,c.job_id,j.status,
       s.snapshot_id,p.status AS purge_status,p.finished_at_ms AS purged_at_ms
       FROM market_noon_capture c JOIN job j ON j.job_id=c.job_id
       LEFT JOIN market_noon_snapshot s ON s.trade_date=c.trade_date
       LEFT JOIN market_noon_purge p ON p.trade_date=c.trade_date
       ORDER BY c.trade_date DESC LIMIT 20000''').fetchall()
    return [{'date': row['trade_date'], 'state': 'available' if row['snapshot_id'] else 'purged'
             if row['purge_status'] == 'purged' else row['status']
             if row['status'] in ('queued', 'running', 'paused', 'failed', 'cancelled') else 'failed',
             'purged_at_ms': row['purged_at_ms']} for row in rows]


def list_dates():
    with connect() as connection:
        rows = _dates(connection)
    return {'today': now_china().date().isoformat(), 'items': rows,
            'dates': [row['date'] for row in rows], 'total': len(rows)}


def read_view(day=None):
    now = now_china(); day = day_value(day if day is not None else now.date().isoformat())
    window = capture_window(day, now)
    with connect() as connection:
        connection.execute('BEGIN')
        ledger = connection.execute('SELECT * FROM market_noon_capture WHERE trade_date=?', (day,)).fetchone()
        retained = connection.execute('SELECT * FROM market_noon_snapshot WHERE trade_date=?', (day,)).fetchone()
        state = _state(connection, ledger, retained)
        configured = _configured(connection)
        initialized = tasks.initialization_state(connection)['main_published']
        dates = _dates(connection)
    task = tasks.task_view(ledger['job_id']) if ledger else None
    reason = window['reason']
    if ledger:
        reason = '当天已提交过午评采集；失败、取消或到期清理也不释放每日名额'
    elif not initialized:
        reason = '请先完成行情初始化，午评不代替首次数据接入'
    elif not configured:
        reason = '请先配置并启用扶摇数据源，再手动采集午评'
    window['calendar_verified'] = retained is not None
    return {'date': day, 'today': now.date().isoformat(), 'now': now.isoformat(),
            'capture_window': window, 'capture_reason': reason,
            'can_capture': bool(window['allowed'] and not ledger and configured and initialized),
            'locked_for_day': ledger is not None, 'state': state, 'task': task,
            'snapshot': _snapshot_view(retained), 'available_dates': dates,
            'retention': dict(RETENTION)}


def create_capture(payload):
    if not isinstance(payload, dict) or set(payload) - {'date', 'request_key'}:
        raise HTTPException(422, '午评采集只接受日期与请求标识，时间和内容由服务器决定')
    day = day_value(payload['date'] if 'date' in payload else now_china().date().isoformat())
    request_key = payload.get('request_key')
    if request_key is None:
        request_key = new_id('noon-request')
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
        raise HTTPException(422, '请求标识须为1至200字符')
    identifier = None
    try:
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            from app.services.increment_access import require_business_unlocked
            require_business_unlocked(connection)
            prior = connection.execute('SELECT * FROM job WHERE request_key=?', (request_key,)).fetchone()
            if prior:
                parameters = json.loads(prior['parameters_json'])
                if prior['kind'] != 'source_check' or parameters.get('operation') != 'noon_snapshot' or prior['target_date'] != day:
                    raise HTTPException(409, '此请求标识已用于其他任务或日期')
                identifier = prior['job_id']
            else:
                ledger = connection.execute('SELECT job_id FROM market_noon_capture WHERE trade_date=?', (day,)).fetchone()
                if ledger:
                    raise HTTPException(409, {'message': '当天已提交过午评采集，每天只允许一次', 'task_id': ledger['job_id']})
                window = capture_window(day)
                if not window['allowed']:
                    raise HTTPException(409, window['reason'])
                if not tasks.initialization_state(connection)['main_published']:
                    raise HTTPException(409, '请先完成行情初始化')
                if not _configured(connection):
                    raise HTTPException(409, '请先配置并启用扶摇数据源')
                from app.services.market_suspensions import VERSION as SUSPENSION_VERSION

                params = {'operation': 'noon_snapshot', 'version': VERSION, 'source_id': 'fuyao',
                          'date': day, 'window_end_at': window['end_at'], 'daily_once': True,
                          'retention_calendar_days': WINDOW_DAYS, 'suspension_evidence': SUSPENSION_VERSION}
                identifier = new_id('job'); stamp = int(now_china().timestamp() * 1000)
                connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,
                    parameters_hash,status,planned_batches,created_at_ms) VALUES(?,?,?,'source_check',?,?,?,'queued',100,?)''',
                    (identifier, request_key, digest({'kind': 'source_check', 'target_date': day, 'parameters': params}),
                     day, dumps(params), digest(params), stamp))
                connection.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (day, identifier, request_key, stamp))
                tasks.append_event(connection, identifier, 'queued', '午评已手动提交；当天名额已占用，等待唯一工作进程采集',
                                   details={'operation': 'noon_snapshot', 'target_date': day, 'daily_once': True})
    except sqlite3.IntegrityError:
        raise HTTPException(409, '当天午评采集已由另一请求提交，请刷新查看原任务') from None
    return tasks.task_view(identifier)


def _guard_job(job):
    with connect() as connection:
        row = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
    if row is None or row['status'] == 'cancelled':
        raise tasks.TaskCancelled()
    if row['status'] in ('paused', 'queued'):
        raise tasks.TaskPaused()
    if row['status'] != 'running' or row['lease_owner'] != job.get('lease_owner'):
        raise tasks.TaskCancelled()


def _statistics(body, day):
    data = body.get('data') if isinstance(body, dict) else None
    if not isinstance(data, dict) or data.get('trade_date') != day:
        return {'state': 'unknown', 'reason': '统计缺失或所属交易日不符', 'trade_date': None,
                'limit_up_stock_count': None, 'open_stock_count': None, 'touched_limit_up_stock_count': None}
    result = {'state': 'observed', 'reason': None, 'trade_date': day}
    for key in ('limit_up_stock_count', 'open_stock_count', 'touched_limit_up_stock_count'):
        value = data.get(key)
        result[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    for key in ('seal_rate',):
        result[key] = data.get(key) if finite(data.get(key)) and 0 <= data[key] <= 1 else None
    for key in ('page_boards', 'exact_boards'):
        result[key] = data[key] if isinstance(data.get(key), list) else []
    if any(result[key] is None for key in ('limit_up_stock_count', 'open_stock_count', 'touched_limit_up_stock_count')):
        result.update(state='partial', reason='统计字段缺失，未补零')
    return result


def execute_capture(job, callback=None):
    if isinstance(job, str):
        with connect() as connection:
            saved = connection.execute('SELECT * FROM job WHERE job_id=?', (job,)).fetchone()
        if not saved:
            raise ValueError('午评任务不存在')
        job = dict(saved)
    params = json.loads(job.get('parameters_json', '{}'))
    day = day_value(job.get('target_date'))
    if job.get('kind') != 'source_check' or params.get('operation') != 'noon_snapshot' or params.get('version') != VERSION:
        raise ValueError('不是已冻结的午评采集任务')
    with connect() as connection:
        retained = connection.execute('SELECT snapshot_id,quality FROM market_noon_snapshot WHERE job_id=?', (job['job_id'],)).fetchone()
        ledger = connection.execute('SELECT * FROM market_noon_capture WHERE trade_date=?', (day,)).fetchone()
    if retained:
        return {'quality': retained['quality'], 'snapshot_id': retained['snapshot_id'], 'message': '午评已保存，复用原结果'}
    if not ledger or ledger['job_id'] != job['job_id']:
        raise ValueError('午评任务与每日名额台账不一致')
    _guard_job(job)
    if not capture_window(day)['allowed']:
        raise ValueError('原午评任务窗口已过期，不使用当前行情补造午评；当天名额保留')
    market = get_market()
    from app.services.source_service import client
    adapter = client('fuyao')
    collector = Collector(job['job_id'], day, adapter, lambda: _guard_job(job), now_china, callback)
    known_at_ms = int(datetime.fromisoformat(collector.started_at).timestamp() * 1000)
    calendar = trading_days(collector.request('calendar', '/api/a-share/calendar/trading-days'))
    if day not in calendar:
        raise ValueError('来源交易日历不含今天，未采集午评；节假日不能生成午评')
    previous = next((value for value in reversed(calendar) if value < day), None)
    raw_stocks, declared, full_stocks = [], None, False
    raw_indices, raw_sectors, catalog = [], [], {}
    catalog_complete = True
    pools = {}; statistics = None; hot = None
    midnight = int(datetime.combine(date.fromisoformat(day), time(), CHINA).timestamp() * 1000)
    try:
        raw_stocks, declared, full_stocks = paged(collector, 'stocks', '/api/a-share/prices/snapshot', market=True)
        raw_indices = items(collector.request('indices', '/api/a-share-index/prices/snapshot',
                    {'thscodes': ','.join(INDEX_NAMES)}, dated=True)) or []
        for kind, tag in (('industry', 'industry'), ('concept', 'cn_concept')):
            values = items(collector.request('catalog_' + kind, '/api/a-share-index/catalog/ths-index-list', {'tag': tag}))
            if not values:
                catalog_complete = False
                collector.gap('sectors', kind + '目录未取得，范围未知')
                continue
            for row in values:
                if not isinstance(row, dict) or not isinstance(row.get('thscode'), str) or not SECTOR_CODE.fullmatch(row['thscode']):
                    catalog_complete = False
                    collector.gap('sectors', '目录含无效来源代码，未按名称猜测')
                    continue
                code = row['thscode']
                if code in catalog:
                    catalog_complete = False
                    collector.gap('sectors', '目录存在重复代码或分类冲突，仅保留原明确身份')
                    continue
                catalog[code] = {'kind': kind, 'name': row.get('name') or code}
            if len(catalog) > 1500:
                catalog_complete = False
                collector.gap('sectors', '板块目录超过1500代码预算，只采集已冻结前1500代码')
                catalog = dict(list(catalog.items())[:1500])
        codes = list(catalog)
        for offset in range(0, len(codes), 50):
            raw_sectors.extend(items(collector.request('sectors_' + str(offset // 50 + 1), '/api/a-share-index/prices/snapshot',
                               {'thscodes': ','.join(codes[offset:offset + 50])}, dated=True)) or [])
        for key, endpoint in NOON_EVENT_POOLS.items():
            pools[key] = paged(collector, key, endpoint, {'date_ms': midnight})
        statistics = collector.request('statistics', '/api/a-share/special-data/limit-up-statistics', {'date': day}, dated=True)
        hot = collector.request('hot_hour', '/api/a-share/special-data/hot-stock-list', {'period': 'hour', 'limit': 30}, dated=True)
    except WindowExpired:
        collector.gap('capture_window', '午休窗口已结束，仅保留窗口内成功核验部分，未采集午后数据')
    _guard_job(job)
    try:
        identities = {row['instrument_id']: row.get('name') or row['instrument_id'] for row in market.instruments(limit=10000)}
    except (AttributeError, KeyError, TypeError, ValueError):
        identities = {}
        collector.gap('identity', '本地名称资料未完整取得，保留明确来源代码')
    stocks = normalize_quotes(raw_stocks, identities)
    quotes = {row['instrument_id']: row for row in stocks}
    indices = normalize_quotes(raw_indices, INDEX_NAMES)
    index_map = {row['instrument_id']: row for row in indices if row['instrument_id'] in INDEX_NAMES}
    raw_index_codes = [row.get('thscode') for row in raw_indices if isinstance(row, dict)]
    if (set(index_map) != set(INDEX_NAMES) or len(raw_index_codes) != len(set(
            code for code in raw_index_codes if isinstance(code, str))) or
            any(code not in INDEX_NAMES for code in raw_index_codes if isinstance(code, str)) or
            any(row['latest_price'] is None for row in index_map.values())):
        collector.gap('indices', '主要指数存在缺项、重复、无效身份或报价，只展示各自已核验部分')
    indices = [index_map[code] if code in index_map else {'instrument_id': code, 'name': name,
                'latest_price': None, 'change_pct': None, 'amount': None, 'quote_at': None,
                'missing_reason': '本次未取得该指数有效行情'} for code, name in INDEX_NAMES.items()]
    if not any(row['latest_price'] is not None for row in [*stocks, *indices]):
        raise ValueError('本次没有取得任何有效股票或指数报价，未生成有效午评；当天名额保留')
    try:
        topics = market.topics_data() if callable(getattr(market, 'topics_data', None)) else []
    except (AttributeError, TypeError, ValueError):
        topics = []
        collector.gap('members', '本地成员名单不可用，板块指数行情独立保留')
    events = {key: normalize_pool(*pools.get(key, ([], None, False)), quotes, key)
              for key in NOON_EVENT_POOLS}
    sectors = sector_members(normalize_quotes(raw_sectors, sectors=catalog), topics, quotes, day, events['limit_up'])
    actual_sector_codes = {row['instrument_id'] for row in sectors}
    if len(raw_sectors) != len(sectors):
        catalog_complete = False
        collector.gap('sectors', '来源板块行情含无效、重复或未请求代码，相关记录已隔离')
    if set(catalog) != actual_sector_codes:
        collector.gap('sectors', '部分目录代码没有取得行情，仅展示已取得的板块样本')
    priced_sector_count = sum(row['latest_price'] is not None for row in sectors)
    if priced_sector_count != len(sectors):
        collector.gap('sectors', '部分板块虽返回代码但缺少有效最新价，代码枚举不等于有效报价覆盖')
    events['statistics'] = _statistics(statistics, day)
    for key, event in events.items():
        if event['state'] != 'observed':
            collector.gap(key, event['reason'])
    events['limit_up']['board_summary'] = board_summary(events['limit_up'])
    events['board_summary'] = events['limit_up']['board_summary']
    hot_stocks = []
    for row in items(hot) or []:
        if isinstance(row, dict) and isinstance(row.get('thscode'), str) and STOCK_CODE.fullmatch(row['thscode']):
            hot_stocks.append({'instrument_id': row['thscode'], 'name': row.get('name') or identities.get(row['thscode']) or row['thscode'],
                               'rank': row.get('rank'), 'heat': row.get('heat'), 'rank_change': row.get('rank_change')})
    if not hot_stocks:
        collector.gap('hot_stocks', '本次小时热榜未取得可核验记录')
    try:
        from app.services.noon_context import build_previous_review
        previous_review = build_previous_review(market, day, previous, known_at_ms, quotes)
    except (ValueError, HTTPException):
        previous_review = {'date': previous, 'state': 'missing' if previous else 'calendar_unknown',
                           'snapshot_id': None, 'recorded_at_ms': None, 'content_sha256': None,
                           'payload': None, 'mainlines': [], 'observations': [],
                           'gaps': ['上个交易日复盘或背景未通过核验，未用更早日期替代']}
    for reason in previous_review.get('gaps', []):
        collector.gap('previous_review', reason)
    collector.gap('quote_time', '供应商未提供逐条行情时间，不能统一标为精确截至11:30')
    collector.gap('minutes', '当前已配置来源未验收分钟K与当日分时权限')
    collector.gap('fund_flow', '完整资金榜未验收可用，成交额不当作净流入')
    summary = build_summary(stocks, declared, full_stocks)
    summary['coverage'].update(sector_requested=len(catalog), sector_observed=len(sectors),
                               sector_priced=priced_sector_count,
                               sector_enumeration_complete=bool(catalog_complete and catalog and set(catalog) == actual_sector_codes),
                               sector_complete=bool(catalog_complete and catalog and set(catalog) == actual_sector_codes
                                                    and priced_sector_count == len(catalog)))
    if summary['coverage']['missing_price_count']:
        collector.gap('stocks', '部分证券最新价缺失或无效，原因待核验')
    sources = collector.sources()
    payload = {'date': day, 'version': VERSION, 'collected_start_at': collector.started_at,
               'collected_end_at': collector.last_accepted_at, 'quote_as_of': None,
               'calendar': {'verified': True, 'previous_day': previous}, 'summary': summary,
               'indices': indices, 'stocks': stocks, 'sectors': sectors, 'events': events,
               'hot_stocks': hot_stocks, 'previous_review': previous_review,
               'analysis': build_analysis(summary, indices, events, sectors, collector.gaps),
               'gaps': collector.gaps, 'sources': sources}
    from app.services.noon_membership_context import freeze_stock_classifications
    classifications = freeze_stock_classifications(payload, topics)
    budget = min(2 * 1024 * 1024, max(0, 8 * 1024 * 1024 - len(canonical(payload)) - 4096))
    payload['stock_classifications'] = {}
    for symbol, classification in sorted(classifications.items()):
        size = len(canonical({symbol: classification}))
        if size > budget:
            continue
        payload['stock_classifications'][symbol] = classification
        budget -= size
    from app.services.noon_sector_views import freeze_memberships
    payload['sector_memberships'] = freeze_memberships(payload, topics,
        budget=max(0, 8 * 1024 * 1024 - len(canonical(payload)) - 4096))
    encoded = canonical(payload).decode()
    if len(encoded.encode()) > 8 * 1024 * 1024:
        raise ValueError('午评正文超过8MiB预算，未保存不完整正文')
    identifier = new_id('noon'); quality = 'partial'
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        state = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        if not state or state['status'] != 'running' or state['lease_owner'] != job.get('lease_owner'):
            raise tasks.TaskCancelled()
        existing = connection.execute('SELECT snapshot_id,quality FROM market_noon_snapshot WHERE job_id=?', (job['job_id'],)).fetchone()
        if existing:
            return {'quality': existing['quality'], 'snapshot_id': existing['snapshot_id'], 'message': '午评已保存，复用原结果'}
        recorded = int(now_china().timestamp() * 1000)
        connection.execute('''INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (identifier, day, job['job_id'], collector.started_at, collector.last_accepted_at, recorded,
             quality, sha256(encoded.encode()).hexdigest(), encoded, dumps(sources)))
        tasks.append_event(connection, job['job_id'], 'noon_saved', '午评独立快照已保存，缺口保持未知',
                           details={'snapshot_id': identifier, 'date': day, 'quality': quality,
                                    'stock_rows': len(stocks), 'sector_rows': len(sectors)})
    return {'quality': quality, 'snapshot_id': identifier, 'message': '午评已保存；来源时间及数据缺口请查看快照说明'}
