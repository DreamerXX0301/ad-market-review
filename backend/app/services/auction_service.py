"""自动竞价终态快照；API只读SQLite，唯一worker负责固定时点采集。"""
from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
import json
import sqlite3

from fastapi import HTTPException

from app.core import connect, digest, dumps, get_market, new_id
from app.infrastructure.credentials import CredentialStore
from app.services import task_service as tasks
from app.services.auction_collection import (CHINA, VERSION, CAPTURE_START, CAPTURE_END,
    STOCK_CODE, Collector, WindowExpired, canonical, contains_secret, in_window)
from app.services.noon_collection import trading_days

MAX_SYMBOLS = 100


def now_china():
    return datetime.now(CHINA)


def day_value(value):
    try:
        valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, '竞价日期须为YYYY-MM-DD形式的真实日期')
    return value


def instrument_values(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_SYMBOLS or any(
            not isinstance(item, str) or not STOCK_CODE.fullmatch(item) for item in value):
        raise HTTPException(422, '竞价名单须为最多100个完整股票代码的数组，例如600519.SH；不猜测市场后缀')
    return list(dict.fromkeys(value))


def capture_window(day, at=None):
    now = (at or now_china()).astimezone(CHINA)
    target = date.fromisoformat(day)
    allowed = in_window(day, now)
    reason = None
    if day != now.date().isoformat():
        reason = '竞价只可采集今天，历史日期不能补造盘前行情'
    elif target.weekday() >= 5:
        reason = '周末不创建竞价；节假日由工作进程核验来源交易日历'
    elif not allowed:
        reason = '自动竞价仅在本交易日北京时间09:25:30至09:30前执行，错过不补采'
    from .auction_automation import START
    allowed = allowed and now.time().replace(tzinfo=None) >= START
    if not allowed and reason is None:
        reason = '自动竞价固定从北京时间09:25:30开始执行'
    return {'allowed': allowed, 'reason': reason,
            'start_at': datetime.combine(target, START, CHINA).isoformat(),
            'end_at': datetime.combine(target, CAPTURE_END, CHINA).isoformat(),
            'timezone': 'Asia/Shanghai', 'calendar_verified': False}


def _configured(connection):
    row = connection.execute('SELECT enabled FROM provider_connection WHERE connection_id=?', ('fuyao',)).fetchone()
    return bool(row and row['enabled'] and CredentialStore().exists('fuyao'))


def _dates(connection):
    return [{'date': row['trade_date'], 'state': 'available' if row['snapshot_id'] else 'purged' if row['purged'] else row['status']}
            for row in connection.execute('''SELECT c.trade_date,j.status,s.snapshot_id,p.snapshot_id AS purged
              FROM market_auction_capture c JOIN job j ON j.job_id=c.job_id
              LEFT JOIN market_auction_snapshot s ON s.trade_date=c.trade_date
              LEFT JOIN market_auction_purge p ON p.trade_date=c.trade_date AND p.status='purged'
              ORDER BY c.trade_date DESC LIMIT 20000''')]


def list_dates():
    with connect() as connection:
        rows = _dates(connection)
        from app.services.auction_retention import retention_status
        retention = retention_status(connection)
    return {'today': now_china().date().isoformat(), 'items': rows,
            'dates': [row['date'] for row in rows], 'total': len(rows), 'retention': retention}


def preparation_view(day, now):
    """今天采集前只看SQLite留存判断；严格前日仍待worker日历核实。"""
    if day != now.date().isoformat():
        return None
    with connect() as connection:
        row = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
    if not row or not isinstance(row['business_date'], str) or row['business_date'] >= day:
        return None
    previous = row['business_date']
    from app.services.noon_context import _prior_material
    retained, mainlines, gaps = _prior_material(previous, int(now.timestamp() * 1000))
    payload = retained.get('payload') if retained else None
    watch = payload.get('watchlist') if isinstance(payload, dict) and isinstance(payload.get('watchlist'), dict) else {}
    observations = []
    seen = set()
    for item in [*(watch.get('stocks') if isinstance(watch.get('stocks'), list) else []), *mainlines]:
        if not isinstance(item, dict):
            continue
        symbol = item.get('instrument_id') or item.get('thscode')
        if isinstance(symbol, str) and STOCK_CODE.fullmatch(symbol) and symbol not in seen:
            seen.add(symbol)
            observations.append({'instrument_id': symbol, 'name': item.get('name') or item.get('instrument_name') or symbol,
                                 'mainline': item.get('display_mainline'), 'source_note': '最近已发布日留存关注或主线；待核验严格前日。'})
    stamp = retained['recorded_at_ms'] if retained else max((row['recorded_at_ms'] for row in mainlines), default=None)
    return {'date': previous, 'state': 'retained' if retained or mainlines else 'missing',
            'snapshot_id': retained.get('snapshot_id') if retained else None,
            'recorded_at_ms': stamp, 'content_sha256': retained.get('content_sha256') if retained else
                sha256(canonical(mainlines)).hexdigest() if mainlines else None,
            'payload': payload, 'mainlines': mainlines, 'observations': observations,
            'summary': ['采集前先核对最近已发布日已保存的关注与计划；本次尚未生成竞价报价。'],
            'gaps': gaps, 'frozen': False, 'relation': 'last_published_day_pending_calendar_check',
            'notice': '采集前准备：最近已发布日材料，严格前一交易日由worker日历核实；尚未冻结。'}


def _snapshot_view(row):
    if row is None:
        return None
    value = dict(row)
    value.pop('payload_json')
    from app.services.auction_integrity import validate_snapshot
    try:
        payload = validate_snapshot(row)
    except ValueError:
        raise HTTPException(409, '竞价正文或来源原件SHA核验失败，原记录保留，请检查存储') from None
    evidence = payload.pop('evidence', [])
    value.update(payload=payload, evidence_count=len(evidence), date=value.pop('trade_date'))
    return value


def read_view(day=None):
    now = now_china()
    day = day_value(day if day is not None else now.date().isoformat())
    window = capture_window(day, now)
    with connect() as connection:
        connection.execute('BEGIN')
        ledger = connection.execute('SELECT * FROM market_auction_capture WHERE trade_date=?', (day,)).fetchone()
        retained = connection.execute('SELECT * FROM market_auction_snapshot WHERE trade_date=?', (day,)).fetchone()
        job = connection.execute('SELECT status FROM job WHERE job_id=?', (ledger['job_id'],)).fetchone() if ledger else None
        configured = _configured(connection)
        initialized = tasks.initialization_state(connection)['main_published']
        dates = _dates(connection)
        from app.services.auction_automation import activity_fields
        automatic = activity_fields(connection, ledger['job_id']) if ledger else {}
        from app.services.auction_retention import retention_status
        retention = retention_status(connection, day)
    state = 'available' if retained else 'purged' if retention['purged'] else job['status'] if job else 'not_collected'
    waiting_retry = automatic.get('automation_state') == 'waiting_retry' and automatic.get('automatic_control') is True
    reason = window['reason']
    if ledger and retained:
        reason = '当天竞价终态已留存，不再次采集'
    elif state == 'purged':
        reason = '该日竞价已超过28自然日保留窗口，正文及内嵌原件已随成功增量清理；采集日账保留，不补造历史竞价。'
    elif state == 'cancelled':
        reason = '当天竞价任务已显式取消，不自动继续或创建第二个任务'
    elif ledger:
        reason = '当天已有原任务；不支持手动重试或继续，显式暂停和取消不会自动恢复'
        if waiting_retry:
            reason = '自动计划正在等待来源终态后有界重试原任务；可显式暂停或取消，不重复创建'
    elif not initialized:
        reason = '请先完成行情初始化，以便冻结严格前日背景'
    elif not configured:
        reason = '请先配置并启用扶摇数据源'
    elif window['allowed']:
        reason = '新竞价仅由已开启的每天自动计划在09:25:30至09:30前登记，不提供手动采集'
    window['calendar_verified'] = retained is not None
    snapshot = _snapshot_view(retained)
    # Classification is a read projection, never part of the immutable quote body.
    # validate_snapshot has already checked both the identities and the 100-row bound.
    from app.services.market_broken_boards import _board_fields
    stock_boards = {item['instrument_id']: _board_fields(item['instrument_id'])
                    for item in snapshot['payload']['quotes']['items']} if snapshot else {}
    return {'date': day, 'today': now.date().isoformat(), 'now': now.isoformat(),
            'capture_window': window, 'capture_reason': reason,
            'can_capture': False, 'can_retry': False, 'can_resume': False,
            'locked_for_day': ledger is not None, 'state': state,
            **automatic,
            'task': tasks.task_view(ledger['job_id']) if ledger else None,
            'snapshot': snapshot, 'stock_boards': stock_boards, 'available_dates': dates,
            'preparation': preparation_view(day, now) if retained is None and state != 'purged' else None,
            'retention': retention,
            'scope': '已留存的最多100只观察股票竞价样本；新采集仅来自每天自动计划，不代表全市场或策略跟踪信号。'}


def create_capture(payload, *, automation=None):
    from .auction_automation import require_worker_creation
    require_worker_creation(automation)
    if not isinstance(payload, dict) or set(payload) - {'date', 'request_key'}:
        raise HTTPException(422, '自动竞价只接受日期与请求标识，观察名单由worker自动冻结')
    day = day_value(payload.get('date', now_china().date().isoformat()))
    instruments = []
    request_key = payload.get('request_key', new_id('auction-request'))
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
        raise HTTPException(422, '请求标识须为1至200字符')
    identifier = None
    try:
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            from app.services.increment_access import require_business_unlocked
            require_business_unlocked(connection)
            automatic = None
            if automation is not None:
                from app.services.auction_automation import creation_authorization
                automatic = creation_authorization(connection, automation, day)
            prior = connection.execute('SELECT * FROM job WHERE request_key=?', (request_key,)).fetchone()
            if prior:
                parameters = json.loads(prior['parameters_json'])
                if (prior['kind'] != 'source_check' or parameters.get('operation') != 'auction_snapshot'
                        or prior['target_date'] != day or parameters.get('instrument_ids', []) != instruments):
                    raise HTTPException(409, '此请求标识已用于其他任务、日期或竞价名单')
                identifier = prior['job_id']
            else:
                ledger = connection.execute('SELECT job_id FROM market_auction_capture WHERE trade_date=?', (day,)).fetchone()
                if ledger:
                    raise HTTPException(409, {'message': '当天已有竞价原任务，请查看或在原窗口手动重试该任务', 'task_id': ledger['job_id']})
                window = capture_window(day)
                if not window['allowed']:
                    raise HTTPException(409, window['reason'])
                if not tasks.initialization_state(connection)['main_published']:
                    raise HTTPException(409, '请先完成行情初始化')
                if not _configured(connection):
                    raise HTTPException(409, '请先配置并启用扶摇数据源')
                params = {'operation': 'auction_snapshot', 'version': VERSION, 'source_id': 'fuyao',
                          'date': day, 'window_end_at': window['end_at'], 'stage': 'final',
                          'instrument_ids': instruments, 'max_symbols': MAX_SYMBOLS}
                if automatic:
                    params.update(automatic=True, automation=automatic)
                identifier = new_id('job')
                stamp = int(now_china().timestamp() * 1000)
                logical = digest({'kind': 'source_check', 'target_date': day, 'parameters': params})
                connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,
                    parameters_hash,status,planned_batches,created_at_ms) VALUES(?,?,?,'source_check',?,?,?,'queued',100,?)''',
                    (identifier, request_key, logical, day, dumps(params), digest(params), stamp))
                connection.execute('INSERT INTO market_auction_capture VALUES(?,?,?,?)', (day, identifier, request_key, stamp))
                if automatic:
                    from app.services.auction_automation import bind_created
                    connection.execute("UPDATE job SET requested_by='auction_automation' WHERE job_id=?", (identifier,))
                    bind_created(connection, identifier, automatic, day)
                tasks.append_event(connection, identifier, 'queued', '已开启的自动计划登记本日竞价，等待唯一worker核验终态'
                                   if automatic else '盘前竞价已手动提交，等待唯一工作进程核验终态',
                                   details={'operation': 'auction_snapshot', 'target_date': day,
                                            'authorization': 'auction_automation' if automatic else 'manual',
                                            'automation_revision': automatic['revision'] if automatic else None})
    except sqlite3.IntegrityError:
        raise HTTPException(409, '当天竞价已由另一请求提交，请刷新查看原任务') from None
    return tasks.task_view(identifier)


def requeue_capture(identifier, action):
    """A user cannot restart an old request; only bounded worker retries remain."""
    raise HTTPException(403, '竞价不支持手动重试或继续；仅自动计划可在固定窗口内有界重试原任务')


def _guard_job(job, *, require_window=False):
    with connect() as connection:
        row = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
    if row is None or row['status'] == 'cancelled':
        raise tasks.TaskCancelled()
    if row['status'] in ('paused', 'queued'):
        raise tasks.TaskPaused()
    if row['status'] != 'running' or row['lease_owner'] != job.get('lease_owner'):
        raise tasks.TaskCancelled()
    if require_window and not capture_window(job['target_date'])['allowed']:
        raise WindowExpired('竞价来源请求仅可在当天北京时间09:25:30至09:30前执行')


def _previous_context(market, day, previous, known_at):
    try:
        from app.services.noon_context import build_previous_review
        return build_previous_review(market, day, previous, known_at, {})
    except (ValueError, HTTPException):
        return {'date': previous, 'state': 'missing', 'known_at_ms': known_at,
                'mainlines': [], 'event_cohorts': {}, 'payload': None,
                'gaps': ['严格前日材料未通过核验，未读取更早日期或今天题材替代。']}


def _universe(market, explicit, previous, known_at):
    try:
        names = {row['instrument_id']: row.get('name') or row['instrument_id']
                 for row in market.instruments(limit=10000) if isinstance(row, dict) and isinstance(row.get('instrument_id'), str)}
    except (AttributeError, ValueError, KeyError, TypeError):
        names = {}
    candidates = {}
    def add(symbol, label, name=None, height=None):
        if not isinstance(symbol, str) or not STOCK_CODE.fullmatch(symbol):
            return
        item = candidates.setdefault(symbol, {'instrument_id': symbol, 'name': names.get(symbol, name or symbol),
                                             'reasons': [], 'previous_board_count': None})
        if label not in item['reasons']:
            item['reasons'].append(label)
        if isinstance(height, int) and not isinstance(height, bool) and height >= 1:
            item['previous_board_count'] = height
    payload = previous.get('payload') if isinstance(previous.get('payload'), dict) else {}
    watch = payload.get('watchlist') if isinstance(payload.get('watchlist'), dict) else {}
    original_watch = watch.get('stocks') if isinstance(watch.get('stocks'), list) else []
    previous_rows = [*(('严格前日已留存复盘关注股', row) for row in original_watch),
                     *(('严格前日已留存观察', row) for row in (previous.get('observations') or [])),
                     *(('严格前日已留存逐股主线', row) for row in (previous.get('mainlines') or []))]
    if explicit:
        for symbol in explicit:
            add(symbol, '用户明确指定')
        for label, row in previous_rows:
            if isinstance(row, dict):
                symbol = row.get('instrument_id') or row.get('thscode')
                if symbol in candidates:
                    add(symbol, label, row.get('name') or row.get('instrument_name'))
    else:
        for label, row in previous_rows:
            if isinstance(row, dict):
                add(row.get('instrument_id') or row.get('thscode'), label, row.get('name') or row.get('instrument_name'))
        with connect() as connection:
            watched = connection.execute('''SELECT instrument_id FROM watchlist_item WHERE created_at_ms<=?
                ORDER BY created_at_ms DESC,instrument_id''', (known_at,)).fetchall()
        for row in watched:
            symbol = row['instrument_id']
            add(symbol, '采集开始前已加入自选')
    for key, label in (('limit_up', '严格前日已核验涨停'), ('limit_break', '严格前日已核验炸板')):
        cohort = (previous.get('event_cohorts') or {}).get(key) or {}
        if cohort.get('date') != previous.get('date'):
            continue
        rows = [row for row in cohort.get('items', []) if isinstance(row, dict)
                and isinstance(row.get('instrument_id'), str) and STOCK_CODE.fullmatch(row['instrument_id'])]
        rows.sort(key=lambda row: (-(row.get('previous_board_count') if isinstance(row.get('previous_board_count'), int)
                     and not isinstance(row.get('previous_board_count'), bool) else 0), row['instrument_id']))
        for row in rows:
            symbol = row['instrument_id']
            if not explicit or symbol in candidates:
                add(symbol, label, row.get('name'), row.get('previous_board_count') if key == 'limit_up' else None)
    values = list(candidates.values())
    return {'mode': 'explicit' if explicit else 'watchlist_previous_events', 'items': values[:MAX_SYMBOLS],
            'requested': min(len(values), MAX_SYMBOLS), 'total_candidates': len(values),
            'omitted': max(0, len(values) - MAX_SYMBOLS),
            'scope': '显式名单只使用所列代码；默认按前日留存复盘关注、前日观察及逐股主线、自选、前日涨停高度、前日炸板顺序冻结，最多100只。未纳入部分不请求、不推断。'}


def _compact_previous(previous, universe):
    symbols = {row['instrument_id'] for row in universe['items']}
    fields = ('date', 'state', 'snapshot_id', 'recorded_at_ms', 'content_sha256', 'known_at_ms',
              'payload', 'background', 'mainlines', 'summary', 'gaps')
    result = {key: previous.get(key) for key in fields}
    result['observations'] = [{key: row.get(key) for key in ('instrument_id', 'name', 'mainline',
        'source_note', 'previous_close', 'previous_date')} for row in (previous.get('observations') or []) if isinstance(row, dict)]
    result['notice'] = '严格前日且采集开始前已留存的材料；只显示冻结观察名单相关事件，不使用当天题材回填历史。'
    result['event_cohorts'] = {}
    for key in ('limit_up', 'limit_break'):
        cohort = (previous.get('event_cohorts') or {}).get(key) or {}
        result['event_cohorts'][key] = {**cohort, 'items': [row for row in cohort.get('items', [])
            if isinstance(row, dict) and row.get('instrument_id') in symbols], 'items_scope': '仅冻结观察名单交集；来源统计仍为原已核验样本范围'}
    if len(canonical(result)) > 768 * 1024:
        original = result['payload'] if isinstance(result.get('payload'), dict) else {}
        result['payload'] = {key: original[key] for key in ('date', 'watchlist', 'plan', 'summary', 'outlook') if key in original}
        result['payload_projection'] = {'state': 'selected_fields', 'original_content_sha256': previous.get('content_sha256'),
            'notice': '正文超过容量，只保留复盘日期、关注名单、计划和摘要字段；此投影不是原始完整正文，原始SHA仅作身份。'}
        result['gaps'] = list(result.get('gaps') or []) + ['前日原文超过竞价768KiB容量，已明确仅冻结关注名单、计划等选定字段。']
    if len(canonical(result)) > 768 * 1024:
        raise ValueError('竞价前日背景超过冻结容量，未保存不完整背景')
    return result


def _freeze_inputs(job, market, previous, known_at, adapter):
    params = json.loads(job['parameters_json'])
    if 'frozen_inputs' in params:
        frozen = params['frozen_inputs']
        if (not isinstance(frozen, dict) or frozen.get('previous_date') != previous
                or not isinstance(frozen.get('universe'), dict)):
            raise ValueError('原竞价冻结输入与严格前日不符')
        return frozen
    context = _previous_context(market, job['target_date'], previous, known_at)
    universe = _universe(market, params.get('instrument_ids', []), context, known_at)
    frozen = {'previous_date': previous, 'known_at_ms': known_at, 'universe': universe,
              'previous_review': _compact_previous(context, universe)}
    status_reader = getattr(market, 'status', None)
    status = status_reader() if callable(status_reader) else {}
    if isinstance(status, dict) and isinstance(status.get('snapshot_id'), str):
        frozen['market_context'] = {'source_snapshot_id': status['snapshot_id'],
            'data_end': status.get('data_end'), 'source': status.get('source'), 'known_at_ms': known_at}
    if contains_secret(frozen, getattr(adapter, 'key', None)):
        raise ValueError('竞价背景包含凭据回显，未冻结输入')
    params['frozen_inputs'] = frozen
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT status,lease_owner,parameters_json FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        if not row or row['status'] != 'running' or row['lease_owner'] != job.get('lease_owner'):
            raise tasks.TaskCancelled()
        existing = json.loads(row['parameters_json']).get('frozen_inputs')
        if existing is not None:
            return existing
        connection.execute('UPDATE job SET parameters_json=?,parameters_hash=?,logical_key=? WHERE job_id=?',
            (dumps(params), digest(params), digest({'kind': 'source_check', 'target_date': job['target_date'], 'parameters': params}), job['job_id']))
        tasks.append_event(connection, job['job_id'], 'auction_inputs_frozen', '竞价观察名单与严格前日证据已冻结，原任务重试保持不变',
                           details={'requested': universe['requested'], 'omitted': universe['omitted'], 'previous_date': previous})
    return frozen


def execute_capture(job, callback=None):
    """Automatic attempts reuse the exact manual collector with explicit origin."""
    if isinstance(job, str):
        with connect() as connection:
            saved = connection.execute('SELECT * FROM job WHERE job_id=?', (job,)).fetchone()
        if not saved:
            raise ValueError('竞价任务不存在')
        job = dict(saved)
    with connect() as connection:
        retained = connection.execute('SELECT 1 FROM market_auction_snapshot WHERE job_id=?', (job['job_id'],)).fetchone()
        purged = connection.execute("SELECT 1 FROM market_auction_purge WHERE source_job_id=? AND status='purged'", (job['job_id'],)).fetchone()
    if purged:
        raise ValueError('该日竞价正文已到期清理，永久日账保留，不补采历史竞价')
    if retained:
        return _execute_capture(job, callback)
    from app.services import auction_automation as automatic
    enabled = automatic.begin_attempt(job)
    try:
        result = _execute_capture(job, callback)
    except automatic.AuctionNonTradingDay:
        if not enabled:
            raise
        automatic.record_outcome(job, 'skipped')
        return {'status': 'no_change', 'quality': 'partial', 'message': '来源交易日历确认今天非交易日，自动竞价已跳过'}
    except automatic.AuctionNotReady:
        if enabled:
            automatic.record_outcome(job, 'not_ready')
        raise
    except Exception:
        if enabled:
            automatic.record_outcome(job, 'failed')
        raise
    if enabled:
        automatic.record_outcome(job, 'available')
    return result


def _record_quote_rejection(job, quotes, reason_code, message):
    """Keep bounded validation metadata, never failed raw bodies or identities."""
    def short(value, maximum):
        return ' '.join(value.split())[:maximum] if isinstance(value, str) else 'unknown'
    summary = quotes.get('summary') or {}
    details = {'reason_code': reason_code,
               'auction_phase': short(quotes.get('auction_phase'), 40),
               'data_status': short(quotes.get('data_status'), 40),
               'source_timestamp_state': short((quotes.get('source_timestamp') or {}).get('state'), 40),
               'requested': summary.get('requested'), 'valid': summary.get('valid'),
               'gaps': [short(value, 160) for value in (quotes.get('gaps') or [])[:5] if isinstance(value, str)]}
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        state = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        if state and state['status'] == 'running' and state['lease_owner'] == job.get('lease_owner'):
            tasks.append_event(connection, job['job_id'], 'auction_quote_rejected', message, 'warning', details)


def _execute_capture(job, callback=None):
    if isinstance(job, str):
        with connect() as connection:
            saved = connection.execute('SELECT * FROM job WHERE job_id=?', (job,)).fetchone()
        if not saved:
            raise ValueError('竞价任务不存在')
        job = dict(saved)
    params = json.loads(job.get('parameters_json', '{}'))
    day = day_value(job.get('target_date'))
    if job.get('kind') != 'source_check' or params.get('operation') != 'auction_snapshot' or params.get('version') != VERSION:
        raise ValueError('不是已冻结的竞价采集任务')
    with connect() as connection:
        retained = connection.execute('SELECT snapshot_id,quality FROM market_auction_snapshot WHERE job_id=?', (job['job_id'],)).fetchone()
        ledger = connection.execute('SELECT * FROM market_auction_capture WHERE trade_date=?', (day,)).fetchone()
        purged = connection.execute("SELECT 1 FROM market_auction_purge WHERE source_job_id=? AND status='purged'", (job['job_id'],)).fetchone()
    if purged:
        raise ValueError('该日竞价正文已到期清理，永久日账保留，不补采历史竞价')
    if retained:
        return {'quality': retained['quality'], 'snapshot_id': retained['snapshot_id'], 'message': '竞价终态已留存，复用原结果'}
    if not ledger or ledger['job_id'] != job['job_id']:
        raise ValueError('竞价任务与原始日台账不一致')
    _guard_job(job)
    if not capture_window(day)['allowed']:
        raise ValueError('原竞价采集窗口已结束，不使用当前行情补造盘前终态')
    from app.services.source_service import client
    adapter = client('fuyao')
    collector = Collector(day, adapter, lambda: _guard_job(job, require_window=True), now_china, callback)
    calendar_body = collector.request('calendar', '/api/a-share/calendar/trading-days')
    if not isinstance(calendar_body, dict) or calendar_body.get('code') != 0:
        raise ValueError('竞价来源交易日历请求失败或不可核验，未请求竞价报价')
    try:
        calendar = trading_days(calendar_body)
    except ValueError:
        raise ValueError('竞价来源交易日历结构或日期未通过核验，未请求竞价报价') from None
    if day not in calendar:
        if not calendar or not any(value < day for value in calendar) or not any(value > day for value in calendar):
            raise ValueError('来源交易日历不含今天且无法证明覆盖今天，保留日历缺口，不认定为非交易日或请求报价')
        from app.services.auction_automation import AuctionNonTradingDay
        raise AuctionNonTradingDay('来源交易日历不含今天，未采集竞价；节假日不能生成盘前快照')
    previous = next((value for value in reversed(calendar) if value < day), None)
    if previous is None:
        raise ValueError('来源日历无法确定严格前一交易日，未生成竞价快照')
    known_at = int(datetime.fromisoformat(collector.started_at).timestamp() * 1000)
    frozen = _freeze_inputs(job, None if 'frozen_inputs' in params else get_market(), previous, known_at, adapter)
    universe = frozen['universe']
    symbols = [row['instrument_id'] for row in universe['items']]
    if not symbols:
        raise ValueError('没有可冻结的自选或严格前日已核验事件样本，请在原窗口前准备观察名单')
    from app.services.auction_analysis import FINAL_SOURCE_STATES, normalize_snapshot, normalize_benchmark, build_analysis
    body = collector.request('auction', '/api/a-share/auction/snapshot', {'thscodes': ','.join(symbols), 'stage': 'final'})
    received = collector.sources[-1]['received_at']
    quotes = normalize_snapshot(body, symbols, day, received)
    if not (quotes.get('summary') or {}).get('valid'):
        data = body.get('data') if isinstance(body, dict) and body.get('code') == 0 else None
        phase = data.get('auction_phase') if isinstance(data, dict) else None
        status = data.get('data_status') if isinstance(data, dict) else None
        pending = isinstance(data, dict) and (status == 'not_ready' or phase in ('live', 'closed') and status == 'ready')
        if data is None:
            reason_code, message = 'response_unavailable', '没有取得可核验竞价响应，未保存成功快照；缺口见本次核验摘要'
        elif (phase, status) not in FINAL_SOURCE_STATES and not pending:
            reason_code, message = 'unsupported_final_markers', '来源竞价终态标记不支持，未采用响应中的报价；具体标记见本次核验摘要'
        elif quotes.get('source_timestamp', {}).get('state') != 'observed':
            reason_code, message = 'invalid_timestamp', '来源竞价响应时点未通过核验，未保存成功快照；时点状态见本次核验摘要'
        elif pending:
            reason_code, message = 'not_ready', '来源竞价终态尚未就绪，未保存成功快照；仅已开启的自动计划可在原窗口有界重试'
        else:
            reason_code, message = 'no_valid_quotes', '来源竞价终态没有有效报价，未保存成功快照；缺口见本次核验摘要'
        _record_quote_rejection(job, quotes, reason_code, message)
        if reason_code == 'not_ready':
            from app.services.auction_automation import AuctionNotReady
            raise AuctionNotReady(message)
        raise ValueError(message)
    for item in quotes.get('gaps') or []:
        collector.gap('auction', item if isinstance(item, str) else item.get('reason', '竞价报价存在缺口'))
    benchmark_body = None
    try:
        benchmark_body = collector.request('benchmark', '/api/a-share/auction/short-term-benchmark', {'date': day})
    except WindowExpired:
        collector.gap('benchmark', '已到09:30，基准未取得，仅保存此前窗口内已核验竞价终态')
    benchmark = normalize_benchmark(benchmark_body, day)
    payload = {'version': VERSION, 'date': day, 'collected_start_at': collector.started_at,
               'collected_end_at': collector.last_accepted_at, 'previous_date': frozen['previous_date'],
               'universe': universe, 'quotes': quotes, 'benchmark': benchmark,
               'market_context': frozen.get('market_context'),
               'previous_review': frozen['previous_review'], 'analysis': build_analysis(quotes, universe) + [
                   '金额为集合竞价成交额，不称资金净流入；没有连续竞价过程，不推断撤单、抢筹或封单稳定。'],
               'gaps': collector.gaps, 'sources': collector.sources, 'evidence': collector.evidence}
    encoded = canonical(payload).decode()
    if len(encoded.encode()) > 4 * 1024 * 1024:
        raise ValueError('竞价正文超过4MiB预算，未保存不完整正文')
    if contains_secret(payload, getattr(adapter, 'key', None)):
        raise ValueError('竞价内容包含凭据回显，未保存正文')
    quality = 'complete' if quotes.get('state') == 'observed' and benchmark.get('state') == 'observed' and not collector.gaps else 'partial'
    identifier = new_id('auction')
    _guard_job(job)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        state = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        if not state or state['status'] != 'running' or state['lease_owner'] != job.get('lease_owner'):
            raise tasks.TaskCancelled()
        existing = connection.execute('SELECT snapshot_id,quality FROM market_auction_snapshot WHERE job_id=?', (job['job_id'],)).fetchone()
        if existing:
            return {'quality': existing['quality'], 'snapshot_id': existing['snapshot_id'], 'message': '竞价终态已保存，复用原结果'}
        recorded = int(now_china().timestamp() * 1000)
        connection.execute('INSERT INTO market_auction_snapshot VALUES(?,?,?,?,?,?,?,?,?)',
            (identifier, day, job['job_id'], collector.started_at, collector.last_accepted_at, recorded,
             quality, sha256(encoded.encode()).hexdigest(), encoded))
        tasks.append_event(connection, job['job_id'], 'auction_saved', '盘前竞价终态与有界来源原件已独立留存',
                           details={'snapshot_id': identifier, 'date': day, 'quality': quality,
                                    'requested': len(symbols), 'valid': quotes['summary']['valid']})
    return {'quality': quality, 'snapshot_id': identifier, 'message': '盘前竞价已留存；名单范围与来源缺口见详情'}
