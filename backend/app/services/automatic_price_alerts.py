"""Opt-in automatic levels, evaluated from prior-session structure at the close.

This service reads immutable published quotes only. Configuration is independent
of chart annotations; reading a page neither computes levels nor queues work.
"""
from datetime import date
from contextlib import nullcontext
import json
import logging
import math
import re

from fastapi import HTTPException

from app.analytics.chart_price_volume import VERSION as ALGORITHM, price_volume_levels
from app.core import connect, digest, dumps, get_market, new_id, now_ms
from app.services import task_service as tasks
from app.services.workspace_common import current, pin

VERSION = 'automatic-price-alert-v1'
OPERATION = 'automatic_price_alert_check'
SOURCE = 'automatic_price_volume'
QUEUED_STAGE = 'automatic_price_alert_check_queued'
MODES = ('support_reclaim', 'close_breakout', 'support_breakdown', 'stop_breakdown')
TITLES = {'support_reclaim': '自动支撑回踩收回', 'close_breakout': '自动压力收盘突破',
          'support_breakdown': '自动支撑收盘失守', 'stop_breakdown': '参考止损位收盘跌破'}


class StaleCheck(Exception):
    pass


def _symbol(identifier):
    value = str(identifier).strip().upper()
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', value):
        raise HTTPException(422, '自动价位提醒需要明确的个股代码')
    return value


def _date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _quote(row):
    return (isinstance(row, dict) and row.get('complete') is not False and row.get('price_basis', 'none') == 'none'
            and all(_number(row.get(key)) for key in ('open', 'high', 'low', 'close'))
            and row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high'])


def _rule(row):
    value = dict(row)
    value['enabled'] = bool(value['enabled'])
    value['require_volume'] = bool(value['require_volume'])
    value['modes'] = json.loads(value.pop('modes_json'))
    return value


def _default():
    return {'rule_id': None, 'revision': 0, 'enabled': False, 'modes': [],
            'require_volume': True, 'algorithm': ALGORITHM, 'effective_after_date': None}


def _clock(connection):
    row = connection.execute('SELECT revision FROM automatic_price_alert_clock WHERE singleton=1').fetchone()
    return row[0] if row else 0


def _head(connection):
    row = connection.execute("SELECT s.snapshot_id,s.lineage_json FROM published_head h JOIN dataset_snapshot s USING(snapshot_id) WHERE h.module='daily_raw' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    if row is None:
        raise HTTPException(409, '请先发布真实行情再启用自动价位提醒')
    return {'primary_snapshot_id': row['snapshot_id'],
            'market_snapshot_id': json.loads(row['lineage_json']).get('market_snapshot_id') or row['snapshot_id']}


def get_config(identifier):
    """SQLite only; absent rules remain disabled, without loading chart data."""
    symbol = _symbol(identifier)
    with connect() as connection:
        connection.execute('BEGIN')
        row = connection.execute('SELECT * FROM automatic_price_alert_rule WHERE instrument_id=?', (symbol,)).fetchone()
        if row is None:
            return {'instrument_id': symbol, 'rule': _default(), 'latest_evaluation': None, 'latest_baseline': None}
        rule = _rule(row)
        saved = connection.execute('SELECT baseline_json FROM automatic_price_alert_baseline WHERE rule_id=? AND rule_revision=?',
                                   (rule['rule_id'], rule['revision'])).fetchone()
        evaluation = connection.execute('''SELECT job_id,trade_date,count(*) checked_count,
            sum(state IN ('triggered','invalidated')) trigger_count,sum(state='unknown') unknown_count,
            max(recorded_at_ms) recorded_at_ms FROM automatic_price_alert_evaluation
            WHERE rule_id=? AND rule_revision=? GROUP BY job_id,trade_date ORDER BY recorded_at_ms DESC LIMIT 1''',
            (rule['rule_id'], rule['revision'])).fetchone()
        view = dict(evaluation) if evaluation else None
        if view:
            view.update(target_date=view.pop('trade_date'), state='unknown' if view['unknown_count'] else 'completed',
                        reason='部分条件缺少可核验依据，未生成提醒' if view['unknown_count'] else '已按前一交易日冻结价位核验收盘')
            if view['target_date'] <= rule['effective_after_date']:
                view.update(state='prepared', reason='已准备下一交易日参考位，保存当日不追溯触发')
            elif view['unknown_count']:
                reasons = [row[0] for row in connection.execute('''SELECT DISTINCT reason FROM automatic_price_alert_evaluation
                    WHERE job_id=? AND rule_id=? AND state='unknown' AND reason IS NOT NULL LIMIT 3''',
                    (view['job_id'], rule['rule_id']))]
                if reasons:
                    view['reason'] = '；'.join(reasons)
            status = connection.execute('SELECT status FROM job WHERE job_id=?', (view['job_id'],)).fetchone()
            if status:
                view['task_status'] = status[0]
                if status[0] not in ('succeeded', 'no_change'):
                    view.update(state=status[0], reason='本次自动价位核验尚未成功完成，请查看任务状态')
        return {'instrument_id': symbol, 'rule': rule, 'latest_evaluation': view,
                'latest_baseline': json.loads(saved[0]) if saved else None}


def save_config(identifier, payload):
    symbol = _symbol(identifier)
    if not isinstance(payload, dict) or set(payload) != {'enabled', 'modes', 'require_volume', 'expected_revision'}:
        raise HTTPException(422, '请提供启用状态、提醒类型、突破量能确认和当前配置版本')
    enabled, modes, required, revision = (payload[key] for key in ('enabled', 'modes', 'require_volume', 'expected_revision'))
    if not isinstance(enabled, bool) or not isinstance(required, bool):
        raise HTTPException(422, '启用状态和量能确认需要是布尔值')
    if (not isinstance(modes, list) or any(not isinstance(mode, str) or mode not in MODES for mode in modes)
            or len(modes) != len(set(modes)) or enabled and not modes):
        raise HTTPException(422, '请选择不重复的自动提醒类型，启用时至少选择一项')
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise HTTPException(422, '配置版本需要是非负整数')
    # Metadata validation happens before the short configuration transaction.
    market = get_market()
    identity = next((row for row in market.instruments(query=symbol, limit=10000)
                     if row.get('instrument_id') == symbol), None)
    if not identity or identity.get('kind', identity.get('asset_type')) not in ('stock', 'equity', 'a_share', 'A'):
        raise HTTPException(422, '自动价位提醒仅支持已留存个股，不支持指数或板块')
    task_id = None
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        from .increment_access import require_business_unlocked
        require_business_unlocked(connection)
        row = connection.execute('SELECT * FROM automatic_price_alert_rule WHERE instrument_id=?', (symbol,)).fetchone()
        old = _rule(row) if row else _default()
        if old['revision'] != revision:
            raise HTTPException(409, '自动提醒配置已变更，请重新读取后保存')
        modes = sorted(modes)
        if (old['enabled'], old['modes'], old['require_volume']) != (enabled, modes, required):
            day = current(connection)
            _head(connection)
            stamp, identifier = now_ms(), old['rule_id'] or new_id('auto-price-rule')
            connection.execute('''INSERT INTO automatic_price_alert_rule
                (rule_id,instrument_id,revision,enabled,modes_json,require_volume,algorithm,effective_after_date,created_at_ms,updated_at_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(instrument_id) DO UPDATE SET revision=excluded.revision,
                enabled=excluded.enabled,modes_json=excluded.modes_json,require_volume=excluded.require_volume,
                effective_after_date=excluded.effective_after_date,updated_at_ms=excluded.updated_at_ms''',
                (identifier, symbol, revision + 1, int(enabled), dumps(modes), int(required), ALGORITHM,
                 day, old.get('created_at_ms', stamp), stamp))
            connection.execute('DELETE FROM automatic_price_alert_baseline WHERE rule_id=?', (identifier,))
            connection.execute("DELETE FROM retention_pin WHERE owner_kind='manual' AND owner_id=?",
                               ('auto-price-baseline:' + identifier,))
            if enabled:
                task_id = enqueue(connection, 'auto-price-save:' + identifier + ':' + str(revision + 1), 'configuration')
    result = get_config(symbol)
    result['task'] = tasks.task_view(task_id) if task_id else None
    return result


def enqueue(connection, request_key=None, origin='manual', *, no_rules_ok=False):
    """Caller owns BEGIN IMMEDIATE. No chart/source reads occur under this lock."""
    from .increment_access import require_business_unlocked
    require_business_unlocked(connection)
    key = request_key or new_id('auto-price-check-request')
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise HTTPException(422, '请求标识需要1至200字符')
    existing = connection.execute('SELECT * FROM job WHERE request_key=?', (key,)).fetchone()
    if existing:
        if json.loads(existing['parameters_json']).get('operation') != OPERATION:
            raise HTTPException(409, '请求标识已用于其他任务')
        return existing['job_id']
    rules = [_rule(row) for row in connection.execute('SELECT * FROM automatic_price_alert_rule WHERE enabled=1 ORDER BY rule_id')]
    if not rules and no_rules_ok:
        return None
    if not rules:
        raise HTTPException(409, '尚未启用个股自动价位提醒')
    head, day = _head(connection), current(connection)
    signature = digest(rules)
    parameters = {'operation': OPERATION, 'version': VERSION, 'origin': origin, 'business_date': day, **head,
                  'rules': rules, 'rules_signature': signature, 'rules_clock': _clock(connection)}
    logical = digest({'operation': OPERATION, 'version': VERSION, 'business_date': day, **head, 'rules_signature': signature})
    existing = connection.execute("SELECT job_id FROM job WHERE logical_key=? AND status IN ('queued','running','paused')", (logical,)).fetchone()
    if existing:
        return existing[0]
    identifier = new_id('job')
    connection.execute("""INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,
        parameters_hash,status,planned_batches,created_at_ms) VALUES(?,?,?,'source_check',?,?,?,'queued',100,?)""",
        (identifier, key, logical, day, dumps(parameters), digest(parameters), now_ms()))
    tasks.append_event(connection, identifier, 'queued', '已保存自动价位收盘核验任务；只读已发布行情，不联网采集')
    return identifier


def create_check(payload=None):
    if not isinstance(payload, dict) or set(payload) - {'request_key'}:
        raise HTTPException(422, '自动价位核验只接受请求标识')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        identifier = enqueue(connection, payload.get('request_key'))
    return tasks.task_view(identifier)


def _guard(connection, job, parameters):
    row = connection.execute('SELECT status,lease_owner FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
    if not row or row['status'] != 'running' or row['lease_owner'] != job['lease_owner']:
        raise tasks.TaskCancelled()
    from .sync_progress import chain_stop_action
    stopped = chain_stop_action(connection, job)
    if stopped == 'pause':
        raise tasks.TaskPaused()
    if stopped == 'cancel':
        raise tasks.TaskCancelled()
    if (current(connection) != parameters['business_date'] or
            _head(connection) != {key: parameters[key] for key in ('primary_snapshot_id', 'market_snapshot_id')}):
        raise StaleCheck('已发布业务日或行情代际已变化，旧自动核验不继续提交')
    if _clock(connection) != parameters['rules_clock']:
        raise StaleCheck('自动提醒配置已修改，旧版本不继续提交')


def build_baseline(symbol, rows, day, calendar, snapshot, primary_snapshot=None):
    """Clip BEFORE analysis; a day-T close can never set its own trigger level."""
    selected = [dict(row) for row in rows if _date(row.get('date')) and row['date'] <= day]
    chart = {'instrument': {'instrument_id': symbol, 'kind': 'stock'}, 'period': 'D', 'as_of': day,
             'snapshot_id': snapshot, 'price_basis': 'none', 'bars': selected[-120:]}
    levels = price_volume_levels(chart, calendar, expected_snapshot=snapshot)
    return {'as_of': day, 'snapshot_id': snapshot, 'primary_snapshot_id': primary_snapshot,
            'algorithm': ALGORITHM, 'price_basis': 'none', 'levels': levels, 'monitoring_baseline': True}


def _prior_baseline(connection, rule, target, previous):
    """Use only a persisted monitoring basis; never reconstruct it from day T.

    Re-running a completed day uses the original evaluation's frozen basis,
    because the replaceable next-session row has already advanced to day T.
    """
    row = connection.execute('''SELECT baseline_json FROM automatic_price_alert_baseline
        WHERE rule_id=? AND rule_revision=? AND level_date=?''',
        (rule['rule_id'], rule['revision'], previous)).fetchone()
    if row:
        return json.loads(row[0])
    row = connection.execute('''SELECT json_extract(evidence_json,'$.level_evidence')
        FROM automatic_price_alert_evaluation WHERE rule_id=? AND rule_revision=? AND trade_date=?
        AND json_extract(evidence_json,'$.level_evidence.as_of')=?
        AND json_extract(evidence_json,'$.level_evidence.monitoring_baseline')=1
        ORDER BY recorded_at_ms,job_id LIMIT 1''', (rule['rule_id'], rule['revision'], target, previous)).fetchone()
    return json.loads(row[0]) if row else None


def evaluate_mode(rule, mode, target, previous, rows, baseline, target_baseline):
    """Pure exact-session crossing test. Missing volume never hides a price risk."""
    evidence = {'level_date': previous, 'level_evidence': baseline, 'volume_evidence': target_baseline['levels']['volume'],
                'price_basis': 'none', 'anchor_date': None, 'line_price': None, 'comparison_line_price': None,
                'comparison_evidence': {'model': 'prior-complete-session-raw-threshold', 'comparison_anchor': target}}
    if target <= rule['effective_after_date']:
        return False, '配置保存当日不追溯触发，从下一已发布交易日开始核验', evidence
    if not previous or baseline['as_of'] != previous:
        return None, '缺少前一完整市场交易日，不能使用更早行情替代', evidence
    if baseline.get('monitoring_baseline') is not True:
        return None, '尚无前一交易日监控基准，本次已准备下一交易日价位', evidence
    today = [row for row in rows if row.get('date') == target]
    before = [row for row in rows if row.get('date') == previous]
    if len(today) != 1 or not _quote(today[0]) or len(before) != 1 or not _quote(before[0]):
        return None, '目标日或前一市场交易日缺少唯一有效日K，不回退更早报价', evidence
    raw, prior = today[0], before[0]
    evidence.update({key: raw[key] for key in ('open', 'high', 'low', 'close')})
    # A later publication can repair old quotes. Keep the pre-trigger basis and
    # its original close immutable; expose the new source quote separately.
    frozen_previous = baseline['levels'].get('reference_price')
    evidence.update(previous_close=frozen_previous, raw_previous_close=prior['close'])
    if raw.get('corporate_action_coverage_known') is not True or raw.get('corporate_action'):
        return None, '目标日公司行动发生或覆盖未知，未复权价位不可直接跨日比较', evidence
    levels = baseline['levels']
    if (levels.get('reference_date') != previous or not _number(frozen_previous)
            or levels.get('snapshot_id') != baseline['snapshot_id']):
        return None, levels.get('reason') or '前一交易日完整结构依据不足，未形成自动价位', evidence
    key = 'breakout' if mode == 'close_breakout' else 'stop' if mode == 'stop_breakdown' else 'support'
    level = levels.get(key)
    if not isinstance(level, dict) or not _number(level.get('price')):
        return None, '前一交易日尚未形成已确认的' + {'breakout': '突破位', 'stop': '参考止损位', 'support': '支撑位'}[key], evidence
    price = level['price']
    evidence.update(line_price=price, comparison_line_price=price, automatic_level=level)
    hit = (frozen_previous <= price < raw['close'] if mode == 'close_breakout' else
           frozen_previous >= price and raw['low'] <= price <= raw['close'] if mode == 'support_reclaim' else
           frozen_previous >= price > raw['close'])
    if hit and mode == 'close_breakout' and rule['require_volume']:
        volume = evidence['volume_evidence']
        if volume['state'] == 'unknown':
            return None, volume.get('reason') or '突破日量能不可核验，未确认放量突破', evidence
        if volume['state'] != 'expanded':
            return False, '价格已越过昨日突破位，成交量不足此前20日均量的1.5倍，未触发放量规则', evidence
    return hit, '按前一完整交易日冻结价位与本日完整日K核验' if hit else '完整日K未满足本次收盘触发条件', evidence


def execute_check(job):
    parameters = json.loads(job['parameters_json'])
    target = parameters['business_date']
    try:
        if parameters.get('operation') != OPERATION or parameters.get('version') != VERSION:
            raise ValueError('自动提醒任务版本或操作不受支持')
        with connect() as connection:
            _guard(connection, job, parameters)
        market = get_market()
        if market.status().get('snapshot_id') != parameters['market_snapshot_id']:
            raise StaleCheck('行情实例代际与自动提醒冻结任务不一致')
        calendar = market.calendar()
        if calendar != sorted(set(calendar)) or target not in calendar:
            raise ValueError('目标日不在已核验且有序的真实市场交易日历中')
        position = calendar.index(target)
        previous = calendar[position - 1] if position else None
        identities = {row['instrument_id']: row for row in market.instruments(limit=10000)}
        total = sum(len(rule['modes']) for rule in parameters['rules'])
        processed = 0
        for rule in parameters['rules']:
            with connect() as connection:
                _guard(connection, job, parameters)
                baseline = _prior_baseline(connection, rule, target, previous)
            symbol = rule['instrument_id']
            identity = identities.get(symbol, {})
            if identity.get('kind', identity.get('asset_type')) not in ('stock', 'equity', 'a_share', 'A'):
                rows = []
            else:
                rows = market.bars(symbol, as_of=target)
            baseline = baseline or {'as_of': previous, 'snapshot_id': None, 'primary_snapshot_id': None,
                                    'algorithm': ALGORITHM, 'price_basis': 'none', 'levels': {}, 'monitoring_baseline': False}
            next_baseline = build_baseline(symbol, rows, target, calendar, parameters['market_snapshot_id'], parameters['primary_snapshot_id'])
            for mode in rule['modes']:
                processed += 1
                with connect() as connection:
                    _guard(connection, job, parameters)
                    saved = connection.execute('''SELECT 1 FROM automatic_price_alert_evaluation
                        WHERE job_id=? AND rule_id=? AND rule_revision=? AND rule=? AND trade_date=?''',
                        (job['job_id'], rule['rule_id'], rule['revision'], mode, target)).fetchone()
                if saved:
                    continue
                hit, reason, evidence = evaluate_mode(rule, mode, target, previous, rows, baseline, next_baseline)
                state = ('invalidated' if mode in ('support_breakdown', 'stop_breakdown') else 'triggered') if hit else 'unknown' if hit is None else 'not_triggered'
                identifier = 'auto-price-event-' + digest({'rule_id': rule['rule_id'], 'revision': rule['revision'], 'rule': mode, 'trade_date': target})
                with connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    _guard(connection, job, parameters)
                    event_id = None
                    if hit:
                        event_id = identifier
                        stamp = now_ms()
                        event = {**evidence, 'event_id': identifier, 'instrument_id': symbol, 'code': symbol[:6],
                                 'name': identity.get('name') or symbol, 'rule_id': rule['rule_id'], 'revision': rule['revision'],
                                 'annotation_id': rule['rule_id'], 'annotation_revision': rule['revision'],
                                 'line_type': 'resistance' if mode == 'close_breakout' else 'support',
                                 'annotation_label': TITLES[mode], 'rule': mode, 'trade_date': target, 'state': state,
                                 'title': TITLES[mode], 'reason': reason, 'source_kind': SOURCE, 'created_at_ms': stamp,
                                 'require_volume': rule['require_volume'],
                                 'added_to_watchlist': False, 'watchlist_add_state': 'disabled',
                                 'market_snapshot_id': parameters['market_snapshot_id'],
                                 'input_snapshots': {'daily_raw': parameters['primary_snapshot_id'],
                                                     'level_daily_raw': baseline['primary_snapshot_id']}, 'algorithm': ALGORITHM}
                        connection.execute('''INSERT INTO automatic_price_alert_event VALUES(?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(rule_id,rule_revision,rule,trade_date) DO NOTHING''',
                            (identifier, rule['rule_id'], rule['revision'], symbol, mode, target, state, job['job_id'], dumps(event), stamp))
                        for source in {parameters['primary_snapshot_id'], baseline.get('primary_snapshot_id')} - {None}:
                            pin(connection, source, 'manual', 'auto-price-event:' + identifier)
                    connection.execute('''INSERT INTO automatic_price_alert_evaluation VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (job['job_id'], rule['rule_id'], rule['revision'], symbol, mode, target, state, reason, dumps(evidence), event_id, now_ms()))
                tasks.progress(job['job_id'], '核验自动价位收盘', round(processed / max(1, total) * 99),
                               details={'actual_completed_batches': processed, 'actual_total_batches': total}, lease_owner=job['lease_owner'])
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                _guard(connection, job, parameters)
                connection.execute('''INSERT INTO automatic_price_alert_baseline VALUES(?,?,?,?,?,?)
                    ON CONFLICT(rule_id) DO UPDATE SET rule_revision=excluded.rule_revision,level_date=excluded.level_date,
                    market_snapshot_id=excluded.market_snapshot_id,baseline_json=excluded.baseline_json,recorded_at_ms=excluded.recorded_at_ms''',
                    (rule['rule_id'], rule['revision'], target, parameters['market_snapshot_id'], dumps(next_baseline), now_ms()))
                owner = 'auto-price-baseline:' + rule['rule_id']
                connection.execute("DELETE FROM retention_pin WHERE owner_kind='manual' AND owner_id=?", (owner,))
                pin(connection, parameters['primary_snapshot_id'], 'manual', owner)
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            _guard(connection, job, parameters)
            counts = connection.execute('''SELECT count(*),coalesce(sum(state IN ('triggered','invalidated')),0),
                coalesce(sum(state='unknown'),0) FROM automatic_price_alert_evaluation WHERE job_id=?''', (job['job_id'],)).fetchone()
            connection.execute('INSERT OR REPLACE INTO automatic_price_alert_check VALUES(?,?,?,?,?,?,?,?)',
                (job['job_id'], target, parameters['market_snapshot_id'], parameters['rules_signature'], *counts, now_ms()))
        tasks.finish(job['job_id'], 'succeeded', '自动价位收盘核验完成；原事件证据保留，最新结构留作下一交易日基线', lease_owner=job['lease_owner'])
    except StaleCheck as error:
        tasks.finish(job['job_id'], 'no_change', str(error), lease_owner=job['lease_owner'])
    except (tasks.TaskCancelled, tasks.TaskPaused):
        raise
    except Exception as error:
        tasks.finish(job['job_id'], 'failed', '自动价位收盘核验未完成', str(getattr(error, 'detail', error))[:500], lease_owner=job['lease_owner'])
        raise


def _scope(connection, as_of, scope):
    row = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
    day = row[0] if row else None
    if scope not in ('all', 'today', 'history') or as_of is not None and (not _date(as_of) or not day or as_of > day):
        raise HTTPException(422, '提醒范围或观察日期无效')
    observed = as_of or day
    if scope == 'today' and observed != day:
        raise HTTPException(422, '今日提醒使用最新已发布业务日；回看请切换历史')
    predicate = 'trade_date=?' if scope == 'today' else 'trade_date<=?'
    values = [observed or '']
    if scope == 'history':
        predicate += ' AND trade_date<?'
        values.append(day or '')
    return day, observed, predicate, values


def list_alerts(as_of=None, limit=100, scope='all', *, _connection=None):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise HTTPException(422, '提醒条数需1至1000')
    with nullcontext(_connection) if _connection is not None else connect() as connection:
        if not connection.in_transaction:
            connection.execute('BEGIN')
        day, observed, predicate, values = _scope(connection, as_of, scope)
        items = [json.loads(row[0]) for row in connection.execute('SELECT event_json FROM automatic_price_alert_event WHERE ' + predicate + ' ORDER BY trade_date DESC,created_at_ms DESC,event_id DESC LIMIT ?', [*values, limit])]
        total = connection.execute('SELECT count(*) FROM automatic_price_alert_event WHERE ' + predicate, values).fetchone()[0]
        today_total = connection.execute('SELECT count(*) FROM automatic_price_alert_event WHERE trade_date=?', (day,)).fetchone()[0]
        history_total = connection.execute('SELECT count(*) FROM automatic_price_alert_event WHERE trade_date<? AND trade_date<=?', (day, observed)).fetchone()[0]
        latest = connection.execute('SELECT * FROM automatic_price_alert_check WHERE ' + predicate.replace('trade_date', 'business_date') + ' ORDER BY checked_at_ms DESC LIMIT 1', values).fetchone()
        latest_view, unknowns = None, []
        if latest:
            latest_view = dict(latest)
            job = connection.execute('SELECT parameters_json,status FROM job WHERE job_id=?', (latest['job_id'],)).fetchone()
            parameters = json.loads(job['parameters_json'])
            stale = ('本次核验任务未成功完成' if job['status'] != 'succeeded' else
                     '核验后业务行情日已更新' if latest['business_date'] != day else
                     '自动提醒配置已变化' if _clock(connection) != parameters['rules_clock'] else
                     '核验后行情代际已更新' if _head(connection) != {key: parameters[key] for key in ('primary_snapshot_id', 'market_snapshot_id')} else None)
            latest_view.update(is_current=stale is None, stale=stale is not None, stale_reason=stale)
            unknowns = [dict(row) for row in connection.execute('''SELECT instrument_id,rule_id annotation_id,trade_date,reason
                FROM automatic_price_alert_evaluation WHERE job_id=? AND state='unknown' ORDER BY rule_id,rule''', (latest['job_id'],))]
        count = connection.execute('SELECT coalesce(sum(json_array_length(modes_json)),0) FROM automatic_price_alert_rule WHERE enabled=1').fetchone()[0]
        return {'items': items, 'total': total, 'today_total': today_total, 'history_total': history_total,
                'business_date': day, 'as_of': observed, 'scope': scope, 'rules_total': count, 'latest_check': latest_view, 'unknowns': unknowns}


def alert_summary(*, _connection=None):
    with nullcontext(_connection) if _connection is not None else connect() as connection:
        if not connection.in_transaction:
            connection.execute('BEGIN')
        row = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
        day = row[0] if row else None
        total = connection.execute('SELECT count(*) FROM automatic_price_alert_event WHERE trade_date=?', (day or '',)).fetchone()[0]
        items = [json.loads(row[0]) for row in connection.execute('SELECT event_json FROM automatic_price_alert_event WHERE trade_date=? ORDER BY created_at_ms DESC,event_id DESC LIMIT 5', (day or '',))]
        fields = ('event_id', 'instrument_id', 'code', 'name', 'rule', 'title', 'state', 'trade_date', 'created_at_ms', 'source_kind')
        return {'business_date': day, 'total': total, 'items': [{key: item.get(key) for key in fields} for item in items], 'limit': 5}


def latest_by_stock(connection, identifiers, day):
    if not identifiers:
        return {}
    marks = ','.join('?' for _ in identifiers)
    rows = connection.execute('''SELECT event_json FROM (SELECT event_json,row_number() OVER
        (PARTITION BY instrument_id ORDER BY trade_date DESC,created_at_ms DESC,event_id DESC) n
        FROM automatic_price_alert_event WHERE trade_date<=? AND instrument_id IN (''' + marks + ')) WHERE n=1', [day, *identifiers])
    return {item['instrument_id']: item for item in (json.loads(row[0]) for row in rows)}


def after_sync(job):
    parameters = json.loads(job['parameters_json'])
    if job['kind'] not in ('initialise', 'increment') or parameters.get('automatic_price_alert_check_version') != VERSION:
        return None
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        saved = connection.execute('SELECT * FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        if not saved or saved['status'] not in ('succeeded', 'partial', 'no_change'):
            return None
        from .sync_progress import chain_stop_action
        if chain_stop_action(connection, saved):
            return None
        from .sync_workspace_followup import enabled, COMPLETE
        if enabled(parameters) and not connection.execute('SELECT 1 FROM job_event WHERE job_id=? AND stage=?', (job['job_id'], COMPLETE)).fetchone():
            return None
        if connection.execute('SELECT 1 FROM job_event WHERE job_id=? AND stage=?', (job['job_id'], QUEUED_STAGE)).fetchone():
            return None
        publication = connection.execute("SELECT data_end FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' ORDER BY published_at_ms DESC LIMIT 1", (job['job_id'],)).fetchone()
        target = publication[0] if publication else saved['target_date']
        if target is None:
            frozen = connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='chain_target_frozen' ORDER BY seq LIMIT 1", (job['job_id'],)).fetchone()
            target = json.loads(frozen[0]).get('target_date') if frozen else None
        if target is None:
            return None
        if target != current(connection):
            tasks.append_event(connection, job['job_id'], QUEUED_STAGE, '原同步业务日已被取代，不追溯创建旧自动核验', details={'skipped': True, 'target_date': target})
            return None
        identifier = enqueue(connection, 'auto-price-after-sync:' + job['job_id'], 'authorized-sync', no_rules_ok=True)
        tasks.append_event(connection, job['job_id'], QUEUED_STAGE, '已接续自动价位收盘核验；没有启用个股规则时不创建任务', details={'next_task_id': identifier})
        return identifier


def recover_sync_followups():
    with connect() as connection:
        saved = [dict(row) for row in connection.execute('''SELECT * FROM job j WHERE kind IN ('initialise','increment')
            AND status IN ('succeeded','partial','no_change')
            AND json_extract(parameters_json,'$.automatic_price_alert_check_version')=?
            AND NOT EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage=?) ORDER BY created_at_ms LIMIT 20''', (VERSION, QUEUED_STAGE))]
    for job in saved:
        try:
            after_sync(job)
        except Exception as error:
            logging.getLogger(__name__).warning('自动价位核验接续未完成 %s %s；保留原任务供下次检查', job['job_id'], type(error).__name__)
