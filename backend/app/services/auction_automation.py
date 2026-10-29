"""Explicitly enabled, single-worker auction scheduling with bounded retries.

Reads are SQLite-only. The idle worker registers the same daily capture used
by manual requests; actual source access remains in auction_service.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
import json

from fastapi import HTTPException

from app.core import connect, digest, dumps
from app.services import task_service as tasks
from app.services.auction_collection import CHINA, VERSION as SOURCE_VERSION

VERSION = 'auction-automation-v1'
START = time(9, 25, 5)
END = time(9, 30)
MAX_ATTEMPTS = 3


class AuctionNotReady(ValueError):
    pass


class AuctionNonTradingDay(ValueError):
    pass


def now_china():
    return datetime.now(CHINA)


def stamp(at=None):
    return int((at or now_china()).timestamp() * 1000)


def in_window(day, at=None):
    current = (at or now_china()).astimezone(CHINA)
    return current.date().isoformat() == day and current.weekday() < 5 and START <= current.time().replace(tzinfo=None) < END


def _config(connection):
    return dict(connection.execute('SELECT * FROM market_auction_automation WHERE singleton=1').fetchone())


def _matches(config, revision, day):
    return (config['revision'] == revision and config['mode'] != 'off'
            and (config['mode'] != 'once' or config['trade_date'] == day))


def _eligibility(connection):
    from app.services.auction_service import _configured
    if not tasks.initialization_state(connection)['main_published']:
        return False, '请先完成行情初始化，再开启自动竞价'
    if not _configured(connection):
        return False, '请先配置并启用扶摇数据源，再开启自动竞价'
    return True, None


def _worker(connection, at):
    row = connection.execute("SELECT value_json FROM platform_setting WHERE key='worker_heartbeat'").fetchone()
    try:
        heartbeat = json.loads(row[0]) if row else {}
        value = heartbeat.get('at_ms')
        valid = isinstance(value, int) and not isinstance(value, bool)
    except (TypeError, ValueError, AttributeError):
        value, valid = None, False
    return {'alive': bool(valid and 0 <= stamp(at) - value <= 45000),
            'last_heartbeat_at_ms': value if valid else None}


def _run_view(connection, row):
    if not row:
        return None
    result = dict(row)
    state = result['state']
    if result['job_id']:
        job = connection.execute('SELECT status FROM job WHERE job_id=?', (result['job_id'],)).fetchone()
        retained = connection.execute('SELECT 1 FROM market_auction_snapshot WHERE job_id=?', (result['job_id'],)).fetchone()
        if retained:
            state = 'available'
        elif job and job['status'] in ('queued', 'running', 'paused', 'cancelled'):
            state = job['status']
        elif job and state != 'waiting_retry' and state not in ('skipped', 'expired'):
            state = 'failed'
    return {'date': result['trade_date'], 'job_id': result['job_id'], 'state': state,
            'attempts': result['attempts'], 'max_attempts': MAX_ATTEMPTS,
            'retry_after_at_ms': result['retry_after_at_ms'], 'reason': result['reason'],
            'automatic_control': bool(result['automatic_control']), 'schedule_revision': result['schedule_revision']}


def _next_day(day):
    value = day + timedelta(days=1)
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def read_view():
    current = now_china(); today = current.date().isoformat()
    with connect() as connection:
        connection.execute('BEGIN')
        config = _config(connection)
        can_enable, reason = _eligibility(connection)
        worker = _worker(connection, current)
        last = _run_view(connection, connection.execute('SELECT * FROM market_auction_automation_run ORDER BY trade_date DESC LIMIT 1').fetchone())
        today_run = _run_view(connection, connection.execute('SELECT * FROM market_auction_automation_run WHERE trade_date=?', (today,)).fetchone())
    enabled = config['mode'] != 'off'
    if config['mode'] == 'once' and (config['trade_date'] < today or
            today_run and today_run['state'] in ('available', 'failed', 'paused', 'cancelled', 'skipped', 'expired')):
        enabled = False
    next_at = None
    if enabled:
        if config['mode'] == 'once':
            next_day = datetime.fromisoformat(config['trade_date']).date()
            if current.time().replace(tzinfo=None) >= END or next_day.weekday() >= 5:
                next_day = None
        else:
            next_day = current.date()
            if next_day.weekday() >= 5 or current.time().replace(tzinfo=None) >= END or today_run:
                next_day = _next_day(next_day)
        if next_day is not None:
            next_at = datetime.combine(next_day, START, CHINA).isoformat()
        if reason is None and not worker['alive']:
            reason = '计划已保存；唯一worker需保持运行且电脑在采集窗口内唤醒，未运行或错过窗口不会补采'
    return {'version': VERSION, 'revision': config['revision'], 'mode': config['mode'],
            'date': config['trade_date'], 'instrument_ids': json.loads(config['instrument_ids_json']),
            'today': today, 'now': current.isoformat(), 'timezone': 'Asia/Shanghai',
            'start_time': '09:25:05', 'end_time': '09:30:00', 'enabled': enabled,
            'next_run_at': next_at, 'can_enable': can_enable, 'reason': reason,
            'last_run': last, 'worker': worker}


def update_config(payload):
    if not isinstance(payload, dict) or set(payload) - {'mode', 'date', 'instrument_ids', 'expected_revision'}:
        raise HTTPException(422, '自动竞价仅接受模式、单次日期、观察名单与当前配置版本')
    mode, expected = payload.get('mode'), payload.get('expected_revision')
    if mode not in ('off', 'once', 'daily') or not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        raise HTTPException(422, 'mode须为off/once/daily，expected_revision须为非负整数')
    from app.services.auction_service import instrument_values, day_value
    current = now_china(); today = current.date().isoformat()
    day = day_value(payload.get('date', today)) if mode == 'once' else None
    if mode == 'once' and day != today:
        raise HTTPException(422, '单次自动竞价只可安排今天，不补采历史或预约其他日期')
    if mode == 'once' and (current.weekday() >= 5 or current.time().replace(tzinfo=None) >= END):
        raise HTTPException(409, '今天已过竞价窗口或为周末，不能安排今日一次；可开启每天计划等待下一窗口')
    if mode != 'once' and payload.get('date') is not None:
        raise HTTPException(422, '只有once模式接受日期')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        from app.services.increment_access import require_business_unlocked
        require_business_unlocked(connection)
        config = _config(connection)
        if expected != config['revision']:
            raise HTTPException(409, {'message': '自动竞价配置已变化，请重新读取后保存', 'revision': config['revision']})
        instruments = instrument_values(payload['instrument_ids']) if 'instrument_ids' in payload else json.loads(config['instrument_ids_json'])
        if mode != 'off':
            allowed, reason = _eligibility(connection)
            if not allowed:
                raise HTTPException(409, reason)
        connection.execute('''UPDATE market_auction_automation SET revision=revision+1,mode=?,trade_date=?,
            instrument_ids_json=?,updated_at_ms=? WHERE singleton=1 AND revision=?''',
            (mode, day, dumps(instruments), stamp(current), expected))
        # Queued work has not started source access. Configuration changes
        # stop it; an already-running original attempt retains its authority.
        pending = connection.execute('''SELECT r.job_id FROM market_auction_automation_run r JOIN job j USING(job_id)
            WHERE r.automatic_control=1 AND j.status='queued' ''').fetchall()
        for row in pending:
            connection.execute("UPDATE job SET status='paused',phase='automation_configuration_changed',lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (row['job_id'],))
            tasks.append_event(connection, row['job_id'], 'automation_disabled', '自动计划配置已改变，尚未执行的原竞价任务暂停；需明确手动继续')
        connection.execute("""UPDATE market_auction_automation_run SET automatic_control=0,retry_after_at_ms=NULL,
            state=CASE WHEN state='waiting_retry' THEN 'failed' WHEN job_id IN
              (SELECT job_id FROM job WHERE status='paused') THEN 'paused' ELSE state END,
            reason='自动计划配置已改变，后续自动创建和重试授权已停止',updated_at_ms=?
            WHERE automatic_control=1 AND state<>'available'""", (stamp(current),))
    return read_view()


def creation_authorization(connection, context, day):
    """Called within capture's exact short transaction, before any job exists."""
    current = now_china(); config = _config(connection)
    if (not isinstance(context, dict) or not _matches(config, context.get('revision'), day)
            or not in_window(day, current)):
        raise HTTPException(409, '自动竞价授权已关闭、改变或不在本日采集窗口')
    from app.services.sync_progress import require_unlocked
    require_unlocked(connection)
    if connection.execute("SELECT 1 FROM job WHERE status IN ('running','queued') LIMIT 1").fetchone():
        raise HTTPException(409, '唯一worker已有其他任务，自动竞价只在空闲时登记')
    if connection.execute('SELECT 1 FROM market_auction_automation_run WHERE trade_date=?', (day,)).fetchone():
        raise HTTPException(409, '当天自动竞价已登记，沿用原任务或回执')
    row = connection.execute("SELECT value_json FROM platform_setting WHERE key='source-credential-revision:fuyao'").fetchone()
    revision = json.loads(row[0]) if row else 'legacy'
    return {'version': VERSION, 'revision': config['revision'], 'mode': config['mode'], 'date': day,
            'source_version': SOURCE_VERSION, 'credential_revision': revision, 'manual_takeover': False}


def bind_created(connection, job_id, context, day):
    value = stamp()
    connection.execute('''INSERT INTO market_auction_automation_run
        (trade_date,job_id,schedule_revision,state,attempts,automatic_control,created_at_ms,updated_at_ms)
        VALUES(?,?,?,'queued',0,1,?,?)''', (day, job_id, context['revision'], value, value))


def _receipt(connection, day, revision, state, reason, *, job_id=None, control=False):
    value = stamp()
    connection.execute('''INSERT INTO market_auction_automation_run
        (trade_date,job_id,schedule_revision,state,attempts,automatic_control,reason,created_at_ms,updated_at_ms)
        VALUES(?,?,?,?,0,?,?,?,?) ON CONFLICT(trade_date) DO NOTHING''',
        (day, job_id, revision, state, int(control), reason, value, value))


def tick():
    """Nonblocking idle-worker scheduling; no source request or sleep here."""
    current = now_china(); today = current.date().isoformat(); create = None
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        config = _config(connection)
        if config['mode'] == 'off':
            return None
        day = config['trade_date'] if config['mode'] == 'once' else today
        if day > today:
            return None
        row = connection.execute('SELECT * FROM market_auction_automation_run WHERE trade_date=?', (day,)).fetchone()
        if (config['mode'] == 'daily' and row is None
                and config['updated_at_ms'] >= stamp(datetime.combine(current.date(), END, CHINA))):
            # Enabling a future daily plan after today's cutoff is not a
            # missed authorized collection and must not create a fault receipt.
            return None
        if row and row['job_id']:
            _reconcile(connection, row, config, current)
            return _run_view(connection, connection.execute('SELECT * FROM market_auction_automation_run WHERE trade_date=?', (day,)).fetchone())
        if day == today and current.weekday() >= 5 and current.time().replace(tzinfo=None) >= START:
            _receipt(connection, day, config['revision'], 'skipped', '周末不请求竞价；其他节假日由来源交易日历核实')
            return None
        if day < today or current.time().replace(tzinfo=None) >= END:
            _receipt(connection, day, config['revision'], 'expired', '服务未在本日09:25:05至09:30前开始采集，已跳过，不补采')
            return None
        if current.time().replace(tzinfo=None) < START:
            if config['mode'] == 'daily':
                prior = current.date() - timedelta(days=1)
                while prior.weekday() >= 5:
                    prior -= timedelta(days=1)
                if config['updated_at_ms'] <= stamp(datetime.combine(prior, END, CHINA)):
                    _receipt(connection, prior.isoformat(), config['revision'], 'expired',
                             '最近一个已授权工作日未在窗口内开始采集；只保留错过回执，不补造历史竞价')
            return None
        if current.weekday() >= 5:
            _receipt(connection, day, config['revision'], 'skipped', '周末不请求竞价；其他节假日由来源交易日历核实')
            return None
        if row:
            return None
        # API-side increment locking is contextual; the worker must explicitly
        # respect an increment result awaiting acknowledgement as well.
        from app.services.sync_progress import lock_record
        if lock_record(connection):
            return None
        existing = connection.execute('''SELECT c.job_id,j.status FROM market_auction_capture c JOIN job j USING(job_id)
            WHERE c.trade_date=?''', (day,)).fetchone()
        if existing:
            state = 'available' if connection.execute('SELECT 1 FROM market_auction_snapshot WHERE job_id=?', (existing['job_id'],)).fetchone() else existing['status']
            _receipt(connection, day, config['revision'], state if state in ('queued','running','paused','cancelled','available') else 'failed',
                     '当天已有手动竞价原任务，仅观察其结果，不取得自动重试授权', job_id=existing['job_id'])
            return None
        if connection.execute("SELECT 1 FROM job WHERE status IN ('running','queued') LIMIT 1").fetchone():
            return None
        allowed, reason = _eligibility(connection)
        if not allowed:
            _receipt(connection, day, config['revision'], 'failed', reason)
            return None
        create = {'date': day, 'request_key': f'auction-auto:{config["revision"]}:{day}',
                  'instrument_ids': json.loads(config['instrument_ids_json'])}
        authorization = {'revision': config['revision']}
    from app.services.auction_service import create_capture
    try:
        return create_capture(create, automation=authorization)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        # A user or setting update won the race. A later idle tick observes
        # the exact retained job rather than manufacturing a second one.
        return None


def _reconcile(connection, run, config, current):
    job = connection.execute('SELECT * FROM job WHERE job_id=?', (run['job_id'],)).fetchone()
    if not job:
        return
    retained = connection.execute('SELECT 1 FROM market_auction_snapshot WHERE job_id=?', (run['job_id'],)).fetchone()
    if retained:
        connection.execute("UPDATE market_auction_automation_run SET state='available',retry_after_at_ms=NULL,reason='竞价终态已留存，请查看结果',updated_at_ms=? WHERE trade_date=?", (stamp(current), run['trade_date']))
        return
    if job['status'] in ('paused','cancelled'):
        connection.execute('UPDATE market_auction_automation_run SET state=?,automatic_control=0,retry_after_at_ms=NULL,updated_at_ms=? WHERE trade_date=?',
                           (job['status'], stamp(current), run['trade_date']))
        return
    if (not run['automatic_control'] or not _matches(config, run['schedule_revision'], run['trade_date'])
            or run['state'] != 'waiting_retry' or job['status'] != 'failed'):
        return
    if not in_window(run['trade_date'], current):
        connection.execute("UPDATE market_auction_automation_run SET state='expired',retry_after_at_ms=NULL,reason='原竞价等待重试时已过采集窗口，不补采',updated_at_ms=? WHERE trade_date=?", (stamp(current), run['trade_date']))
        return
    if run['outcome'] != 'not_ready' or run['attempts'] >= MAX_ATTEMPTS or not run['retry_after_at_ms'] or stamp(current) < run['retry_after_at_ms']:
        return
    from app.services.sync_progress import lock_record
    if lock_record(connection):
        return
    connection.execute("UPDATE job SET status='queued',phase='queued',accepted_batches=0,error_summary=NULL,finished_at_ms=NULL,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=? AND status='failed'", (job['job_id'],))
    connection.execute("UPDATE market_auction_automation_run SET state='queued',retry_after_at_ms=NULL,updated_at_ms=? WHERE trade_date=?", (stamp(current), run['trade_date']))
    tasks.append_event(connection, job['job_id'], 'auction_auto_retry', '来源终态尚未就绪，按已开启计划有界重试原任务',
                       details={'automation_revision': run['schedule_revision'], 'next_attempt': run['attempts'] + 1, 'max_attempts': MAX_ATTEMPTS})


def begin_attempt(job):
    params = json.loads(job['parameters_json']); authorization = params.get('automation')
    if not isinstance(authorization, dict) or authorization.get('manual_takeover'):
        return False
    current = now_china()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        run = connection.execute('SELECT * FROM market_auction_automation_run WHERE job_id=?', (job['job_id'],)).fetchone()
        state = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        if not run or not state or state['status'] != 'running' or state['lease_owner'] != job.get('lease_owner'):
            raise tasks.TaskCancelled()
        if run['last_attempt_owner'] == job.get('lease_owner') and run['state'] == 'running':
            return True
        config = _config(connection)
        row = connection.execute("SELECT value_json FROM platform_setting WHERE key='source-credential-revision:fuyao'").fetchone()
        credential_revision = json.loads(row[0]) if row else 'legacy'
        allowed = (run['automatic_control'] and _matches(config, run['schedule_revision'], run['trade_date'])
                   and authorization.get('version') == VERSION and authorization.get('source_version') == SOURCE_VERSION
                   and authorization.get('revision') == run['schedule_revision']
                   and authorization.get('credential_revision') == credential_revision
                   and in_window(run['trade_date'], current) and run['attempts'] < MAX_ATTEMPTS)
        if not allowed:
            connection.execute("UPDATE job SET status='paused',phase='automatic_authorization_stopped',lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (job['job_id'],))
            connection.execute("UPDATE market_auction_automation_run SET state='paused',automatic_control=0,retry_after_at_ms=NULL,reason='自动配置、来源版本或采集窗口已改变，原任务等待明确手动操作',updated_at_ms=? WHERE job_id=?", (stamp(current), job['job_id']))
            tasks.append_event(connection, job['job_id'], 'auction_auto_stopped', '自动采集授权已停止，不继续来源请求')
        else:
            connection.execute("""UPDATE market_auction_automation_run SET state='running',attempts=attempts+1,
                last_attempt_owner=?,last_attempt_at_ms=?,outcome=NULL,retry_after_at_ms=NULL,reason=NULL,updated_at_ms=? WHERE job_id=?""",
                (job.get('lease_owner'), stamp(current), stamp(current), job['job_id']))
            tasks.append_event(connection, job['job_id'], 'auction_auto_attempt', '唯一worker执行已开启计划的竞价尝试',
                               details={'attempt': run['attempts'] + 1, 'max_attempts': MAX_ATTEMPTS, 'automation_revision': run['schedule_revision']})
    if not allowed:
        raise tasks.TaskPaused()
    return True


def record_outcome(job, outcome):
    current = now_china()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        run = connection.execute('SELECT * FROM market_auction_automation_run WHERE job_id=?', (job['job_id'],)).fetchone()
        if not run or run['last_attempt_owner'] != job.get('lease_owner'):
            return
        status = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        if not status:
            return
        if status['status'] not in ('paused', 'cancelled') and (
                status['status'] != 'running' or status['lease_owner'] != job.get('lease_owner')):
            return
        if status['status'] in ('paused', 'cancelled'):
            state, reason, retry_at, control = status['status'], '原任务已显式暂停或取消，自动尝试已停止', None, 0
        elif outcome == 'available':
            state, reason, retry_at, control = 'available', '竞价终态已留存，请查看结果', None, run['automatic_control']
        elif outcome == 'skipped':
            state, reason, retry_at, control = 'skipped', '来源交易日历确认今天非交易日，已跳过竞价报价', None, 0
        elif (outcome == 'not_ready' and run['automatic_control'] and run['attempts'] < MAX_ATTEMPTS
                and _matches(_config(connection), run['schedule_revision'], run['trade_date']) and in_window(run['trade_date'], current)):
            state, reason, retry_at, control = 'waiting_retry', '来源竞价终态尚未就绪，窗口内等待有界重试原任务', stamp(current) + (10000 if run['attempts'] == 1 else 20000), 1
        else:
            state, retry_at, control = 'failed', None, 0
            reason = ('来源终态仍未就绪，已用完最多3次自动尝试，不再重试' if outcome == 'not_ready' and run['attempts'] >= MAX_ATTEMPTS else
                      '来源尚未就绪，但自动授权或采集窗口已停止，不再自动重试' if outcome == 'not_ready' else
                      '本次自动竞价未完成；缺口、权限或无效报价不自动重试')
        connection.execute('''UPDATE market_auction_automation_run SET state=?,reason=?,retry_after_at_ms=?,
            automatic_control=?,outcome=?,updated_at_ms=? WHERE job_id=?''',
            (state, reason, retry_at, control, outcome, stamp(current), job['job_id']))


def manual_takeover(connection, identifier):
    row = connection.execute('SELECT parameters_json,target_date FROM job WHERE job_id=?', (identifier,)).fetchone()
    params = json.loads(row['parameters_json'])
    if not isinstance(params.get('automation'), dict):
        return
    params['automation']['manual_takeover'] = True
    connection.execute('UPDATE job SET parameters_json=?,parameters_hash=?,logical_key=? WHERE job_id=?',
        (dumps(params), digest(params), digest({'kind': 'source_check', 'target_date': row['target_date'], 'parameters': params}), identifier))
    connection.execute("UPDATE market_auction_automation_run SET automatic_control=0,retry_after_at_ms=NULL,outcome='stopped',reason='用户明确手动接管原任务，不再自动重试',updated_at_ms=? WHERE job_id=?", (stamp(), identifier))


def stop_waiting(identifier, action):
    if action not in ('pause','cancel'):
        return None
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('''SELECT r.*,j.status FROM market_auction_automation_run r JOIN job j USING(job_id)
            WHERE r.job_id=?''', (identifier,)).fetchone()
        if not row or row['state'] != 'waiting_retry' or row['status'] != 'failed':
            return None
        state = 'paused' if action == 'pause' else 'cancelled'
        connection.execute('UPDATE job SET status=?,phase=?,finished_at_ms=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?',
                           (state, state, None if action == 'pause' else tasks.now_ms(), identifier))
        connection.execute("UPDATE market_auction_automation_run SET state=?,automatic_control=0,retry_after_at_ms=NULL,outcome='stopped',reason='用户停止等待中的自动竞价尝试',updated_at_ms=? WHERE job_id=?", (state, stamp(), identifier))
        tasks.append_event(connection, identifier, state, '用户暂停自动竞价等待，需明确手动继续' if action == 'pause' else '用户取消自动竞价等待，本日不再继续')
    return tasks.task_view(identifier)


def activity_fields(connection, identifier, *, parameters=None):
    run = connection.execute('SELECT * FROM market_auction_automation_run WHERE job_id=?', (identifier,)).fetchone()
    if not run:
        return {}
    if parameters is None:
        job = connection.execute('SELECT parameters_json FROM job WHERE job_id=?', (identifier,)).fetchone()
        parameters = json.loads(job['parameters_json']) if job else {}
    return {'automatic': parameters.get('automatic') is True, 'automatic_control': bool(run['automatic_control']),
            'automation_state': run['state'], 'retry_after_at_ms': run['retry_after_at_ms'],
            'attempts': run['attempts'], 'max_attempts': MAX_ATTEMPTS}
