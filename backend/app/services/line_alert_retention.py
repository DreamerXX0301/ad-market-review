"""First alert + 60 market sessions; cleanup only in an authorized sync child.

Saved lines without an event have no deadline. GET never deletes. The worker
uses exact event IDs on its own connection, as with workspace purging; ordinary
connections remain unable to delete alert evidence. Batch receipts retain only
counts, dates and the rule-clock checkpoint, not a second alert history.
"""
import json

from app.core import connect
from app.services import task_service as tasks

VERSION = 'first-alert-plus-60-sessions-v1'
SESSIONS = 60
BATCH_SIZE = 100
STAGE = 'line_alert_retention_progress'


def first_alerts(connection, identifiers=None):
    if identifiers is None:
        rows = connection.execute('SELECT annotation_id,min(trade_date) first_date FROM line_alert_event GROUP BY annotation_id')
        return {row['annotation_id']: row['first_date'] for row in rows}
    identifiers = list(dict.fromkeys(identifiers))
    result = {}
    for offset in range(0, len(identifiers), 400):
        batch = identifiers[offset:offset + 400]
        marks = ','.join('?' for _ in batch)
        result.update({row['annotation_id']: row['first_date'] for row in connection.execute(
            f'SELECT annotation_id,min(trade_date) first_date FROM line_alert_event WHERE annotation_id IN ({marks}) GROUP BY annotation_id', batch)})
    return result


def positions(calendar, target):
    if not calendar or calendar != sorted(set(calendar)) or target not in calendar:
        raise ValueError('提醒保留期需要完整有序且包含业务日的真实交易日历')
    return {day: index for index, day in enumerate(calendar) if day <= target}


def expired(first_date, day, sessions):
    """Trigger day is day zero; retain the following 60 completed sessions."""
    if first_date not in sessions or day not in sessions:
        return None
    return sessions[day] - sessions[first_date] > SESSIONS


def checkpoint(connection, identifier):
    row = connection.execute('SELECT details_json FROM job_event WHERE job_id=? AND stage=? ORDER BY seq DESC LIMIT 1', (identifier, STAGE)).fetchone()
    return json.loads(row[0]) if row else None


def guard_parameters(connection, job, parameters):
    saved = checkpoint(connection, job['job_id']) if parameters.get('line_alert_retention_version') == VERSION else None
    return {**parameters, 'rules_clock': saved['rules_clock']} if saved else parameters


def _authorized(connection, job, parameters):
    parent_id = parameters.get('retention_sync_job_id')
    parent = connection.execute('SELECT * FROM job WHERE job_id=?', (parent_id,)).fetchone()
    if (parameters.get('line_alert_retention_version') != VERSION or parameters.get('origin') != 'manual-sync'
            or not parent or parent['kind'] != 'increment' or parent['status'] not in ('succeeded', 'partial', 'no_change')
            or json.loads(parent['parameters_json']).get('line_alert_retention_version') != VERSION):
        raise ValueError('提醒清理只能接续明确冻结60交易日规则的盘后增量')
    receipt = connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='line_alert_check_queued' AND json_extract(details_json,'$.next_task_id')=?", (parent_id, job['job_id'])).fetchone()
    from app.services.sync_progress import chain_stop_action
    if not receipt or chain_stop_action(connection, job):
        raise tasks.TaskCancelled()


def start(connection, job, parameters):
    _authorized(connection, job, parameters)
    if checkpoint(connection, job['job_id']):
        return
    value = {'version': VERSION, 'business_date': parameters['business_date'], 'rules_clock': parameters['rules_clock'],
             'removed_lines': 0, 'removed_alerts': 0, 'removed_evaluations': 0, 'complete': False}
    tasks.append_event(connection, job['job_id'], STAGE, '划线核验完成，开始清理首次提醒后超过60个交易日的线与提醒', details=value)


def cleanup(job, parameters, calendar, guard):
    sessions = positions(calendar, parameters['business_date'])
    while True:
        with connect() as connection:
            _authorized(connection, job, parameters)
            guard(connection)
            progress = checkpoint(connection, job['job_id'])
            if progress is None:
                raise ValueError('提醒清理缺少已完成核验的检查点')
            if progress['complete']:
                return progress
            dates = first_alerts(connection)
        unknown = sum(expired(first, parameters['business_date'], sessions) is None for first in dates.values())
        batch = [identifier for identifier, first in dates.items() if expired(first, parameters['business_date'], sessions) is True][:BATCH_SIZE]
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            _authorized(connection, job, parameters)
            guard(connection)
            # The worker is the only alert writer, but re-read under the write
            # lock so a line edited/deleted by the API cannot slip past the guard.
            actual = first_alerts(connection, batch)
            if actual != {identifier: dates[identifier] for identifier in batch}:
                raise ValueError('首次提醒日期在清理前变化，停止本次清理')
            lines = alerts = evaluations = 0
            if batch:
                marks = ','.join('?' for _ in batch)
                events = list(connection.execute(f'SELECT annotation_id,event_id FROM line_alert_event WHERE annotation_id IN ({marks})', batch))
                allowed = {(row['annotation_id'], row['event_id']) for row in events}
                connection.create_function('line_alert_expiry_allowed', 2, lambda annotation_id, event_id: int((annotation_id, event_id) in allowed))
                try:
                    evaluations = connection.execute(f'DELETE FROM line_alert_evaluation WHERE annotation_id IN ({marks})', batch).rowcount
                    for row in events:
                        connection.execute("DELETE FROM retention_pin WHERE owner_kind='manual' AND owner_id=?", ('line-alert:' + row['event_id'],))
                    alerts = connection.execute(f'DELETE FROM line_alert_event WHERE annotation_id IN ({marks})', batch).rowcount
                    lines = connection.execute(f'DELETE FROM chart_annotation WHERE annotation_id IN ({marks})', batch).rowcount
                finally:
                    connection.create_function('line_alert_expiry_allowed', 2, lambda *_: 0)
            clock = connection.execute('SELECT revision FROM line_alert_rule_clock WHERE singleton=1').fetchone()
            progress = {**progress, 'rules_clock': clock[0] if clock else 0,
                        'removed_lines': progress['removed_lines'] + lines, 'removed_alerts': progress['removed_alerts'] + alerts,
                        'removed_evaluations': progress['removed_evaluations'] + evaluations,
                        'calendar_unknown_lines': unknown, 'complete': not batch}
            # The latest visible check describes retained records, with cleanup
            # totals separately available in this small receipt.
            counts = connection.execute("SELECT count(*),coalesce(sum(state IN ('triggered','invalidated')),0),coalesce(sum(state='unknown'),0) FROM line_alert_evaluation WHERE job_id=?", (job['job_id'],)).fetchone()
            connection.execute('UPDATE line_alert_check SET checked_rules=?,trigger_count=?,unknown_count=? WHERE job_id=?', (*counts, job['job_id']))
            tasks.append_event(connection, job['job_id'], STAGE,
                               '划线到期清理完成' if progress['complete'] else '已清理一批到期划线及关联提醒', details=progress)
        if progress['complete']:
            return progress
