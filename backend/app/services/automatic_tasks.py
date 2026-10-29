"""Opt-in, bounded daily scheduling owned solely by the existing worker.

Settings/GET are local SQLite only. A due, idle worker checks the official
calendar at most once per Beijing day, then uses the ordinary task creators.
There is no startup history replay, scheduler thread, strategy run or migration.
"""
from __future__ import annotations

from datetime import datetime, time as day_time
from contextvars import ContextVar
import json
import re
import threading
import time

from fastapi import HTTPException

from app.core import connect, digest, dumps, new_id, now_ms
from app.services import task_service as tasks
from app.services.sync_clock import CHINA, local_now

VERSION = 'automatic-review-v1'
CONFIG_KEY = 'automatic-review-config-v1'
STATE_KEY = 'automatic-review-state-v1'
CALENDAR_KEY = 'automatic-review-calendar-v1'
CHECK_SECONDS = 60
CLAIM_SECONDS = 120
MAX_CALENDAR_DAYS = 4096
KINDS = ('noon', 'after_close')
_creation_guard = ContextVar('automatic_review_creation_guard', default=None)
DEFAULTS = {'noon': {'enabled': False, 'time': '12:05'},
            'after_close': {'enabled': False, 'time': '17:00'}}
POLICY = {'check_interval_seconds': CHECK_SECONDS, 'noon_window_start': '11:31',
          'noon_window_end': '13:00', 'after_close_not_before': '17:00',
          'catchup': '仅当天有效窗口补跑；跨零点不追历史，午评13:00后不补',
          'calendar_requests_per_day': 1, 'initialization': 'manual',
          'stopped_tasks': '失败、暂停或取消交由用户处理，不自动重试或继续',
          'result_confirmation': '盘后沿用完整增量锁，结果须用户确认；未确认会阻塞下一轮'}


def _json(connection, key, default):
    row = connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _save(connection, key, value):
    connection.execute('INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms',
                       (key, dumps(value), now_ms()))


def _settings(connection):
    value = _json(connection, CONFIG_KEY, None)
    if value is None:
        return {'revision': 0, **{key: dict(item) for key, item in DEFAULTS.items()}}
    # Corrupt/local manually edited settings fail closed, never enable a fallback.
    _validate({key: value.get(key) for key in KINDS})
    if type(value.get('revision')) is not int or value['revision'] < 0:
        raise ValueError('自动复盘配置版本无效，请重新保存配置')
    return value


def _validate(payload):
    if not isinstance(payload, dict) or set(payload) - {*KINDS, 'expected_revision', 'auction', 'expected_auction_revision'} or not set(KINDS) <= set(payload):
        raise HTTPException(422, '请提供午评和盘后两项完整配置')
    result = {}
    for kind in KINDS:
        value = payload[kind]
        if not isinstance(value, dict) or set(value) != {'enabled', 'time'} or type(value['enabled']) is not bool:
            raise HTTPException(422, '启用状态须为布尔，时间须为HH:mm')
        clock = value['time']
        if not isinstance(clock, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock):
            raise HTTPException(422, '时间须为北京时间HH:mm')
        if kind == 'noon' and not '11:31' <= clock < '13:00':
            raise HTTPException(422, '午评时间须在北京时间11:31至13:00前')
        if kind == 'after_close' and clock < '17:00':
            raise HTTPException(422, '盘后增量时间不能早于北京时间17:00')
        result[kind] = dict(value)
    expected = payload.get('expected_revision')
    if 'expected_revision' in payload and (type(expected) is not int or expected < 0):
        raise HTTPException(422, '配置版本须为非负整数')
    if 'auction' in payload:
        auction = payload['auction']
        if not isinstance(auction, dict) or set(auction) != {'enabled'} or type(auction['enabled']) is not bool:
            raise HTTPException(422, '竞价设置只接受enabled布尔开关，时间固定09:25:05且名单自动生成')
        revision = payload.get('expected_auction_revision')
        if type(revision) is not int or revision < 0:
            raise HTTPException(422, '保存竞价开关须提供当前expected_auction_revision')
    elif 'expected_auction_revision' in payload:
        raise HTTPException(422, '竞价配置版本须与竞价开关一起提供')
    return result


def read_settings():
    with connect() as connection:
        connection.execute('BEGIN')
        settings = _settings(connection)
        states = _json(connection, STATE_KEY, {})
        checks = {}
        for kind in KINDS:
            item = states.get(kind)
            if item is None:
                checks[kind] = None
                continue
            visible = {key: item.get(key) for key in ('state', 'reason', 'date', 'task_id', 'checked_at')}
            job = connection.execute('SELECT status FROM job WHERE job_id=?', (item.get('task_id'),)).fetchone() if item.get('task_id') else None
            visible['task_status'] = job[0] if job else None
            checks[kind] = visible
        from .auction_automation import settings_view
        auction = settings_view(connection)
    return {**settings, 'timezone': 'Asia/Shanghai', 'now': local_now().isoformat(),
            'last_checks': checks, 'policy': dict(POLICY), 'auction': auction}


def configure(payload):
    values = _validate(payload)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        from .increment_access import require_business_unlocked
        require_business_unlocked(connection)
        current = _settings(connection)
        if payload.get('expected_revision', current['revision']) != current['revision']:
            raise HTTPException(409, '自动复盘设置已在其他窗口修改，请刷新后保存')
        if 'auction' in payload:
            from .auction_automation import configure_in_transaction
            configure_in_transaction(connection, enabled=payload['auction']['enabled'],
                                     expected_revision=payload['expected_auction_revision'])
        if any(values[key] != current[key] for key in KINDS):
            _save(connection, CONFIG_KEY, {'revision': current['revision'] + 1, **values})
    return read_settings()


def enabled():
    """Legacy status projection: local configuration, never calendar or tasks."""
    with connect() as connection:
        settings = _settings(connection)
        from .auction_automation import _config, _legacy_configuration
        auction = _config(connection)
    return any(settings[kind]['enabled'] for kind in KINDS) or auction['mode'] == 'daily' and not _legacy_configuration(auction)


def request_key(kind, day):
    return f'{VERSION}:{kind}:{day}'


def due_state(kind, config, observed):
    """Pure clock rule. Formal calendar and current queue are checked separately."""
    now = local_now(observed)
    if not config['enabled']:
        return 'disabled'
    clock = now.strftime('%H:%M')
    if kind == 'noon' and clock >= '13:00':
        return 'missed'
    if clock < config['time']:
        return 'waiting_time'
    return 'due'


def _state(connection, kind, day, state, reason, observed, *, task_id=None, claim_token=None, **extra):
    states = _json(connection, STATE_KEY, {})
    item = {'date': day, 'state': state, 'reason': reason, 'task_id': task_id, **extra}
    previous = states.get(kind, {})
    # A concurrent preflight/calendar status cannot erase the submitter's live
    # claim between its reservation and the actual task creation transaction.
    if (previous.get('date') == day and previous.get('state') == 'claiming'
            and previous.get('lease_until_ms', 0) > int(observed.timestamp() * 1000)
            and previous.get('token') != claim_token):
        return previous
    # Do not write a periodic timestamp for an unchanged disabled/blocked state.
    if {key: value for key, value in previous.items() if key != 'checked_at'} != item:
        states[kind] = {**item, 'checked_at': observed.isoformat()}
        _save(connection, STATE_KEY, states)
    return states.get(kind)


def _existing(connection, kind, day):
    if kind == 'noon':
        return connection.execute('SELECT j.job_id,j.status FROM market_noon_capture c JOIN job j ON j.job_id=c.job_id WHERE c.trade_date=?', (day,)).fetchone()
    begin = int(datetime.combine(datetime.fromisoformat(day).date(), day_time(), CHINA).timestamp() * 1000)
    # Legacy failed requests may not have frozen a target; they still consume
    # this day's automatic attempt rather than being silently retried.
    return connection.execute('''SELECT j.job_id,j.status FROM job j WHERE j.kind='increment'
        AND (j.target_date=? OR json_extract(j.parameters_json,'$.sync_completion_date')=?
          OR EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='chain_target_frozen'
                    AND json_extract(e.details_json,'$.target_date')=?)
          OR j.target_date IS NULL AND json_extract(j.parameters_json,'$.sync_completion_date') IS NULL
             AND j.created_at_ms>=? AND j.created_at_ms<?)
        ORDER BY j.created_at_ms DESC,j.rowid DESC LIMIT 1''', (day, day, day, begin, begin + 86400000)).fetchone()


def _preflight(connection, kind, day, now):
    initialized = tasks.initialization_state(connection)
    if not initialized['main_published']:
        _state(connection, kind, day, 'blocked', '请先手动完成首次初始化；自动计划不会初始化行情', now)
        return False
    existing = _existing(connection, kind, day)
    if existing:
        _state(connection, kind, day, 'existing', '当天已有任务，沿用原结果；失败、暂停或取消请手动处理', now, task_id=existing['job_id'])
        return False
    if kind == 'after_close' and initialized.get('data_end') and initialized['data_end'] >= day:
        _state(connection, kind, day, 'existing', '当天主行情已发布，不重复创建盘后增量', now)
        return False
    from .sync_progress import lock_record
    locked = lock_record(connection)
    if locked:
        _state(connection, kind, day, 'blocked', '等待上次盘后增量完整结果确认；自动计划不会替你确认解锁', now, task_id=locked['root_task_id'])
        return False
    if connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone():
        _state(connection, kind, day, 'waiting', '已有任务排队或运行，空闲后在当天窗口内再检查', now)
        return False
    return True


def _calendar(kind, day, observed):
    """Reserve today's one request before I/O; a crash/failure cannot repeat it."""
    with connect() as connection:
        saved = _json(connection, CALENDAR_KEY, {})
        if saved.get('date') == day:
            return saved
    from . import source_service
    adapter = source_service.client('fuyao')
    if not adapter.key:
        raise ValueError('请先配置扶摇数据源')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        saved = _json(connection, CALENDAR_KEY, {})
        if saved.get('date') == day:
            return saved
        _save(connection, CALENDAR_KEY, {'date': day, 'state': 'checking', 'source_id': 'fuyao', 'days': []})
    attempts = 0
    def before_attempt():
        nonlocal attempts
        if attempts:
            raise ValueError('正式日历当天只自动请求一次，不连续重试')
        attempts += 1
    try:
        from .noon_collection import items, trading_days
        body = adapter.request('/api/a-share/calendar/trading-days', before_attempt=before_attempt)
        rows = items(body)
        if rows is None or len(rows) > MAX_CALENDAR_DAYS:
            raise ValueError('正式交易日历为空或超出有界范围')
        days = trading_days(body)
        if not days or len(days) > MAX_CALENDAR_DAYS:
            raise ValueError('正式交易日历为空或超出有界范围')
        saved = {'date': day, 'state': 'ready', 'days': days, 'source_id': 'fuyao', 'source_sha256': digest(body), 'fetched_at': observed.isoformat()}
    except Exception:
        saved = {'date': day, 'state': 'failed', 'days': [], 'source_id': 'fuyao', 'reason': '正式交易日历核验失败，当天不自动重试；可使用手动入口核验'}
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _save(connection, CALENDAR_KEY, saved)
    return saved


def _claim(kind, day, observed, revision):
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        config = _settings(connection)
        if config['revision'] != revision or due_state(kind, config[kind], observed) != 'due':
            return None
        if not _preflight(connection, kind, day, observed):
            return None
        previous = _json(connection, STATE_KEY, {}).get(kind, {})
        stamp = int(observed.timestamp() * 1000)
        if previous.get('date') == day and (previous.get('state') in ('submitted', 'failed') or previous.get('state') == 'claiming' and previous.get('lease_until_ms', 0) > stamp):
            return None
        token = new_id('automatic')
        _state(connection, kind, day, 'claiming', '自动计划正在提交当天任务', observed,
               token=token, lease_until_ms=stamp + CLAIM_SECONDS * 1000)
        return token


def guard_creation(connection):
    """Recheck opt-in and time in the real task's SQLite write transaction."""
    guard = _creation_guard.get()
    if guard is None:
        return
    current = _settings(connection); now = local_now()
    if (current['revision'] != guard['revision'] or now.date().isoformat() != guard['date']
            or due_state(guard['kind'], current[guard['kind']], now) != 'due'):
        raise HTTPException(409, {'code': 'automatic_schedule_changed', 'message': '自动计划已关闭、调整或错过当天窗口，未提交迟到任务'})
    existing = _existing(connection, guard['kind'], guard['date'])
    if existing:
        saved = connection.execute('SELECT request_key FROM job WHERE job_id=?', (existing['job_id'],)).fetchone()
        if saved[0] != request_key(guard['kind'], guard['date']):
            raise HTTPException(409, {'code': 'automatic_day_already_submitted', 'message': '当天已由其他请求提交任务，请查看原任务'})
        return  # Replaying this exact request cannot enqueue additional work.
    if not tasks.initialization_state(connection)['main_published']:
        raise HTTPException(409, {'code': 'automatic_state_changed', 'message': '主行情状态已变化，请先手动完成初始化'})
    if connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone():
        raise HTTPException(409, {'code': 'automatic_worker_busy', 'message': '已有其他任务提交，空闲后在当天窗口内再检查'})


def _create(kind, day, revision):
    # Apply the identical transaction-local HTTP guards even though creation is
    # worker-owned: races with manual submission/completion must not bypass it.
    from .increment_access import business_http_request
    token = business_http_request.set(True)
    guard_token = _creation_guard.set({'kind': kind, 'date': day, 'revision': revision})
    try:
        if kind == 'noon':
            from .noon_service import create_capture
            return create_capture({'date': day, 'request_key': request_key(kind, day)})
        from .sync_requests import create_sync_request
        return create_sync_request({'mode': 'increment', 'request_key': request_key(kind, day)})
    finally:
        _creation_guard.reset(guard_token)
        business_http_request.reset(token)


class AutomaticReviewScheduler:
    """One monotonic check per minute, including when configuration is disabled."""
    def __init__(self):
        self.next_check = 0.0
        self.lock = threading.Lock()

    def tick(self, *, at=None, monotonic=None):
        clock = time.monotonic() if monotonic is None else monotonic
        if clock < self.next_check or not self.lock.acquire(blocking=False):
            return {'checked': False, 'created': []}
        self.next_check = clock + CHECK_SECONDS
        try:
            observed = local_now(at); day = observed.date().isoformat(); created = []
            with connect() as connection:
                config = _settings(connection)
            for kind in KINDS:
                state = due_state(kind, config[kind], observed)
                if state in ('disabled', 'waiting_time'):
                    continue
                with connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if state == 'missed':
                        previous = _existing(connection, kind, day)
                        _state(connection, kind, day, 'existing' if previous else 'missed',
                               '当天已有午评任务，请查看原进度和结果' if previous else '已错过13:00午评窗口，不补造午间行情',
                               observed, task_id=previous['job_id'] if previous else None)
                        continue
                    if not _preflight(connection, kind, day, observed):
                        continue
                try:
                    calendar = _calendar(kind, day, observed)
                except Exception:
                    calendar = {'state': 'failed', 'days': [], 'reason': '请检查并启用扶摇数据源，自动计划暂未提交'}
                days = calendar.get('days', [])
                if calendar.get('state') != 'ready' or day not in days:
                    known_closed = bool(days and days[0] <= day <= days[-1])
                    with connect() as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        _state(connection, kind, day, 'not_trading' if known_closed else 'calendar_unknown',
                               '正式交易日历确认当天休市，不创建任务' if known_closed else calendar.get('reason', '正式交易日历尚未覆盖当天，未创建任务'), observed)
                    continue
                claim = _claim(kind, day, observed, config['revision'])
                if not claim:
                    continue
                try:
                    task = _create(kind, day, config['revision'])
                    with connect() as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        current = _json(connection, STATE_KEY, {}).get(kind, {})
                        if current.get('token') == claim:
                            _state(connection, kind, day, 'submitted', '自动计划已关联当天任务，沿用原进度和结果确认', observed, task_id=task['job_id'], claim_token=claim)
                        tasks.append_event(connection, task['job_id'], 'automatic_schedule', '已启用的每日自动计划关联本任务；原冻结数据链和策略设置保持不变',
                                           details={'version': VERSION, 'schedule': kind, 'date': day,
                                                    'configuration_revision': config['revision'], 'timezone': 'Asia/Shanghai', 'scheduled_time': config[kind]['time'],
                                                    'calendar_source_id': calendar['source_id'], 'calendar_source_sha256': calendar['source_sha256']})
                    created.append(task['job_id'])
                except Exception as error:
                    with connect() as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        # A commit-to-response failure may already have saved
                        # the real task; discover it instead of trying again.
                        existing = _existing(connection, kind, day)
                        code = error.detail.get('code') if isinstance(error, HTTPException) and isinstance(error.detail, dict) else None
                        if existing:
                            state, reason = 'existing', '当天已有任务，请查看原任务'
                        elif code == 'automatic_schedule_changed':
                            state, reason = 'settings_changed', '计划已调整或窗口已结束，未提交迟到任务'
                        elif code in ('automatic_worker_busy', 'automatic_state_changed'):
                            state, reason = 'waiting', '任务或行情状态已变化，空闲后在当天窗口内再检查'
                        else:
                            state, reason = 'failed', '当天自动提交未完成，不自动反复重试；请使用手动入口检查'
                        _state(connection, kind, day, state, reason, observed,
                               task_id=existing['job_id'] if existing else None, claim_token=claim)
            return {'checked': True, 'created': created}
        finally:
            self.lock.release()
