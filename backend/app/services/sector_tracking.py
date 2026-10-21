"""Independent sector-index rounds, immutable entry anchors and manual review.

No stocks enter the stock tracking pool. Index point comparisons have no
individual-stock adjustment semantics; missing exact-day quotes stay unknown.
"""
from __future__ import annotations

from copy import deepcopy
import json
import sqlite3

from fastapi import HTTPException

from app.core import connect, get_market, new_id, now_ms
from app.domain.sector_conditions import evaluate, finite, periods_for, policy_normalized
from app.domain.tracking import trading_age
from app.services.workspace_common import (current, e8, money, fail, insert, js, obj, one, rows,
    api_lease_owner, start_api_heartbeat, check_api_task, log, digest)

STATUSES = ('tracking', 'expired', 'condition_ended', 'manual_removed')
EVENT_TYPES = ('entry', 'review', 'alert', 'condition_end', 'expire', 'manual_remove', 'policy_change')


def _head(connection):
    row = connection.execute("SELECT s.lineage_json FROM published_head h JOIN dataset_snapshot s ON s.snapshot_id=h.snapshot_id WHERE h.module='daily_raw' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    return obj(row[0], {}).get('market_snapshot_id') if row else None


def _context():
    market = get_market()
    with connect() as connection:
        day = current(connection); head = _head(connection)
    status = market.status()
    if not status.get('ready') or not status.get('snapshot_id'):
        fail('请先初始化真实板块行情。', 409)
    if head and head != status['snapshot_id']:
        fail('行情发布已变化，请重新加载板块跟踪。', 409)
    return {'market': market, 'day': day, 'head': head, 'snapshot_id': status['snapshot_id'],
            'calendar': market.calendar()}


def _guard(connection, context):
    if current(connection) != context['day'] or _head(connection) != context['head']:
        fail('业务日或行情代际已变化，请重新操作板块跟踪。', 409)


def _metrics_for(market, code, day, periods=('D',)):
    from app.services.sector_strategies import metrics_for_sector
    try:
        return metrics_for_sector(market, code, day, periods)
    except (ValueError, OSError, HTTPException) as error:
        reason = str(getattr(error, 'detail', error))
        return {'metrics_by_period': {period: {'close': None, 'observed_date': None, 'reason': reason}
                                      for period in periods}, 'close_price': None,
                'cache_refs': {}, 'coverage': {'available': False, 'reason': reason}}


def _periods(policy, default):
    result = {'D', default}
    for node in policy['alerts']+policy['exits']: result.update(periods_for(node, default))
    return tuple(period for period in ('D', 'W', 'M') if period in result)


def _price(info, day):
    daily = info.get('metrics_by_period', {}).get('D', {})
    price = finite(info.get('close_price'))
    return price if price is not None and price > 0 and daily.get('observed_date') == day else None


def _entry_basis(candidate, run, version):
    value = obj(candidate['metrics_json'], {})
    daily = value.get('metrics_by_period', {}).get('D', {})
    price = money(candidate['close_price_e8'])
    high = finite(daily.get('high')); low = finite(daily.get('low')); close = finite(daily.get('close'))
    refs = obj(candidate['cache_refs_json'], {})
    known = (daily.get('observed_date') == run['business_date'] and close is not None
             and e8(close) == candidate['close_price_e8'] and high is not None and low is not None
             and 0 < low <= price <= high and isinstance(refs, dict)
             and isinstance(refs.get('sha256'), str) and len(refs['sha256']) == 64)
    return {'date': run['business_date'], 'price_basis': 'none', 'unit': '指数点位',
            'entry_price': price, 'entry_high': high if known else None, 'entry_low': low if known else None,
            'anchor_state': 'ready' if known else 'unknown',
            'anchor_reason': None if known else '运行时缺少同日有效日K高低点位或不可变缓存SHA证明',
            'market_snapshot_id': run['market_snapshot_id'], 'cache_refs': refs,
            'signal_period': version['period'], 'source_metrics': value,
            'source_checks': obj(candidate['checks_json'], {})}


def add_tracking(payload, *, automatic=False, transaction_guard=None, on_commit=None):
    if not isinstance(payload, dict) or set(payload)-{'run_id', 'target_ids'}:
        fail('板块入池只接受run_id与target_ids')
    ids = payload.get('target_ids')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 800
            or any(not isinstance(code, str) or not code.strip() or len(code) > 30 for code in ids)):
        fail('请选择1至800个已命中的板块')
    if not isinstance(payload.get('run_id'), str): fail('需要已完成的板块运行标识')
    codes = list(dict.fromkeys(code.strip().upper() for code in ids)); context = _context()
    added = []; skipped = []
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE'); _guard(connection, context)
        if transaction_guard: transaction_guard(connection)
        run = one(connection, 'SELECT * FROM sector_selection_run WHERE run_id=?', (payload['run_id'],), '板块运行不存在')
        strategy = one(connection, 'SELECT * FROM sector_strategy WHERE strategy_id=?', (run['strategy_id'],))
        if (run['status'] != 'completed' or run['business_date'] != context['day']
                or run['market_snapshot_id'] != context['snapshot_id']):
            fail('只能从当前业务日、当前行情代际的已完成板块运行入池，请重新运行。', 409)
        if strategy['archived'] or strategy['current_version'] != run['strategy_version']:
            fail('板块策略已修改或归档，请重新运行后入池。', 409)
        version = one(connection, 'SELECT * FROM sector_strategy_version WHERE strategy_id=? AND version=?',
                      (run['strategy_id'], run['strategy_version']))
        policy = policy_normalized(obj(version['policy_json'], {}), version['period'])
        for code in codes:
            candidate = connection.execute('SELECT * FROM sector_selection_candidate WHERE run_id=? AND topic_code=?',
                                           (run['run_id'], code)).fetchone()
            if not candidate or candidate['decision'] != 'matched' or not candidate['close_price_e8']:
                skipped.append({'target_id': code, 'reason': '未命中或没有当日入池点位', 'reason_code': 'invalid_candidate'}); continue
            if connection.execute("SELECT 1 FROM sector_tracking_round WHERE strategy_id=? AND target_id=? AND status='tracking'",
                                  (run['strategy_id'], code)).fetchone():
                skipped.append({'target_id': code, 'reason': '该策略已跟踪此板块，不重复入池', 'reason_code': 'already_tracking'}); continue
            latest = connection.execute('SELECT status FROM sector_tracking_round WHERE strategy_id=? AND target_id=? ORDER BY created_at_ms DESC,rowid DESC LIMIT 1', (run['strategy_id'], code)).fetchone() if automatic else None
            if latest and latest['status'] == 'manual_removed':
                skipped.append({'target_id': code, 'reason': '已手动移除此板块，自动批次保留忽略状态', 'reason_code': 'ignored'}); continue
            if automatic and connection.execute('SELECT 1 FROM sector_tracking_round WHERE origin_run_id=? AND target_id=?', (run['run_id'], code)).fetchone():
                skipped.append({'target_id': code, 'reason': '本次运行已有跟踪轮次，保留原结束状态', 'reason_code': 'already_recorded'}); continue
            candidate = dict(candidate); basis = _entry_basis(candidate, run, version)
            identifier = new_id('sector-round'); stamp = now_ms()
            row = {'round_id': identifier, 'strategy_id': run['strategy_id'], 'entry_strategy_version': run['strategy_version'],
                   'origin_run_id': run['run_id'], 'target_id': code, 'topic_code': code, 'topic_id': 'fuyao-'+code,
                   'name': candidate['name'], 'sector_kind': candidate['sector_kind'], 'entry_period': version['period'],
                   'entry_business_date': context['day'], 'entry_price_e8': candidate['close_price_e8'],
                   'entry_high_e8': e8(basis['entry_high']) if basis['entry_high'] is not None else None,
                   'entry_low_e8': e8(basis['entry_low']) if basis['entry_low'] is not None else None,
                   'entry_market_snapshot_id': run['market_snapshot_id'], 'entry_cache_refs_json': candidate['cache_refs_json'],
                   'entry_basis_json': js(basis), 'entry_policy_json': js(policy), 'policy_json': js(policy),
                   'duration_sessions': policy['duration_sessions'], 'created_at_ms': stamp, 'updated_at_ms': stamp}
            insert(connection, 'sector_tracking_round', row)
            _event(connection, {**row, 'policy_revision': 1}, 'entry', context['day'], 0,
                   'ready', 'not_applicable', {'basis': basis, 'policy': policy}, key=identifier+':entry')
            added.append({'round_id': identifier, 'target_id': code, 'topic_code': code,
                          'entry_business_date': context['day'], 'entry_price': money(candidate['close_price_e8']),
                          'status': 'tracking', 'target_type': 'sector'})
        result = {'items': added, 'added_count': len(added), 'skipped': skipped, 'business_date': context['day'], 'target_type': 'sector'}
        if on_commit: on_commit(connection, result)
    return result


def _event(connection, row, kind, day, age, state, outcome, evidence, key=None, revision=None):
    revision = row['policy_revision'] if revision is None else revision
    insert(connection, 'sector_tracking_event', {'event_id': new_id('sector-event'),
        'event_key': key or f"{row['round_id']}:{day}:{kind}:{revision}", 'round_id': row['round_id'],
        'event_type': kind, 'business_date': day, 'policy_revision': revision, 'data_state': state,
        'outcome': outcome, 'trading_age': age, 'evidence_json': js(evidence), 'recorded_at_ms': now_ms()})


def _event_view(row):
    return {key: value for key, value in row.items() if key != 'evidence_json'} | {'evidence': obj(row['evidence_json'], {})}


def _policy_at(connection, row, day):
    changed = connection.execute("SELECT * FROM sector_tracking_event WHERE round_id=? AND event_type='policy_change' AND business_date<=? ORDER BY business_date DESC,recorded_at_ms DESC,rowid DESC LIMIT 1", (row['round_id'], day)).fetchone()
    if changed:
        return obj(changed['evidence_json'], {})['policy'], changed['policy_revision']
    return obj(row['entry_policy_json'], {}), 1


def _with_anchors(row, info, day, age):
    by_period = deepcopy(info.get('metrics_by_period', {})); daily = by_period.setdefault('D', {})
    price = _price(info, day); entry = money(row['entry_price_e8'])
    high = money(row['entry_high_e8']); low = money(row['entry_low_e8'])
    values = {'tracking_change_pct': (price/entry-1)*100 if price is not None else None,
              'trading_age': age, 'close_below_entry': price < entry if price is not None else None,
              'close_above_entry_high': price > high if price is not None and high is not None else None,
              'close_below_entry_low': price < low if price is not None and low is not None else None}
    daily.update(values)
    basis = obj(row['entry_basis_json'], {})
    for key in values:
        daily[key+'_reason'] = None if values[key] is not None else '缺少当日有效点位、市场日历或本轮入池日高低点位'
        daily[key+'_evidence'] = {'date': row['entry_business_date'], 'entry_price': entry,
            'entry_high': high, 'entry_low': low, 'price_basis': 'none', 'unit': '指数点位',
            'entry_cache_refs': obj(row['entry_cache_refs_json'], {}), 'anchor_state': basis.get('anchor_state')}
    return by_period, price


def _group(conditions, logic, metrics, period):
    if not conditions: return {'result': False, 'checks': [], 'logic': logic}
    return evaluate({'all' if logic == 'ALL' else 'any': conditions}, metrics, period)


def _view(row, day, calendar, info, policy, events):
    valuation = row['end_business_date'] or day
    age = trading_age(calendar, row['entry_business_date'], valuation)
    by_period, price = _with_anchors(row, info, valuation, age)
    alerts = [_event_view(event) for event in events if event['event_type'] == 'alert' and event['business_date'] == valuation]
    last = max(events, key=lambda event: (event['business_date'], event['recorded_at_ms'], event.get('_order', 0))) if events else None
    latest_review = next((event for event in reversed(events) if event['event_type'] == 'review' and event['business_date'] == valuation), None)
    basis = obj(row['entry_basis_json'], {})
    public = {key: value for key, value in row.items() if key not in
              ('entry_price_e8','entry_high_e8','entry_low_e8','entry_cache_refs_json','entry_basis_json','entry_policy_json','policy_json')}
    public.update(target_type='sector', valuation_date=valuation, entry_price=money(row['entry_price_e8']),
        current_price=price, latest_price=price, today_change_pct=by_period.get('D', {}).get('change_pct'),
        tracking_change_pct=by_period['D']['tracking_change_pct'], trading_age=age, policy=policy,
        duration_sessions=policy['duration_sessions'], remaining_sessions=max(0, policy['duration_sessions']-age) if age is not None else None,
        latest_event=_event_view(last) if last else None, alerts=alerts,
        policy_review_pending=row['status']=='tracking' and (not latest_review or latest_review['policy_revision'] != row['policy_revision']),
        entry_basis=basis, entry_cache_refs=obj(row['entry_cache_refs_json'], {}),
        coverage=info.get('coverage', {}), comparison_state='ready' if price is not None else 'unknown',
        comparison_reason=None if price is not None else '该日没有有效板块指数报价，不回退旧价，不结束跟踪')
    for label in ('high', 'low'):
        value = money(row['entry_'+label+'_e8'])
        proof = {'state': 'ready' if value is not None else 'unknown', 'date': row['entry_business_date'],
                 'raw_'+label: value, 'unit': '指数点位', 'price_basis': 'none',
                 'cache_refs': obj(row['entry_cache_refs_json'], {}),
                 'reason': None if value is not None else basis.get('anchor_reason')}
        public.update({f'entry_{label}': value, f'entry_{label}_date': proof['date'],
            f'entry_{label}_state': proof['state'], f'entry_{label}_reason': proof['reason'], f'entry_{label}_basis': proof})
    return public


def list_tracking(strategy_id=None, status='tracking', triggered_only=False):
    if status not in (*STATUSES, 'all', 'ended', None): fail('不支持的板块跟踪状态')
    context = _context()
    with connect() as connection:
        sql = 'SELECT r.*,s.name strategy_name FROM sector_tracking_round r JOIN sector_strategy s USING(strategy_id) WHERE 1=1'; args = []
        if strategy_id: sql += ' AND r.strategy_id=?'; args.append(strategy_id)
        grouped = rows(connection, 'SELECT status,count(*) AS count FROM sector_tracking_round'+(' WHERE strategy_id=?' if strategy_id else '')+' GROUP BY status', [strategy_id] if strategy_id else [])
        counts = {'active': sum(row['count'] for row in grouped if row['status']=='tracking'), 'history': sum(row['count'] for row in grouped if row['status']!='tracking')}
        if status == 'ended': sql += " AND r.status<>'tracking'"
        elif status and status != 'all': sql += ' AND r.status=?'; args.append(status)
        data = rows(connection, sql+' ORDER BY r.created_at_ms DESC,r.round_id', args)
        prepared = [(row, _policy_at(connection, row, row['end_business_date'] or context['day'])[0],
                     rows(connection, 'SELECT *,rowid AS _order FROM sector_tracking_event WHERE round_id=? ORDER BY business_date,recorded_at_ms,rowid', (row['round_id'],))) for row in data]
    output = []; cached = {}
    for row, policy, events in prepared:
        valuation = row['end_business_date'] or context['day']; periods = _periods(policy, row['entry_period'])
        key = (row['target_id'], valuation, periods)
        frozen = _ended_info(row, events, valuation)
        if frozen is None and key not in cached: cached[key] = _metrics_for(context['market'], row['target_id'], valuation, periods)
        item = _view(row, context['day'], context['calendar'], frozen if frozen is not None else cached[key], policy, events)
        if not triggered_only or item['alerts']: output.append(item)
    return {'items': output, 'total': len(output), 'counts': counts, 'business_date': context['day'], 'target_type': 'sector'}


def get_tracking(identifier):
    context = _context()
    with connect() as connection:
        row = one(connection, 'SELECT r.*,s.name strategy_name FROM sector_tracking_round r JOIN sector_strategy s USING(strategy_id) WHERE round_id=?', (identifier,), '板块跟踪轮次不存在')
        day = row['end_business_date'] or context['day']; policy, _ = _policy_at(connection, row, day)
        events = rows(connection, 'SELECT *,rowid AS _order FROM sector_tracking_event WHERE round_id=? ORDER BY business_date,recorded_at_ms,rowid', (identifier,))
    info = _ended_info(row, events, day)
    if info is None: info = _metrics_for(context['market'], row['target_id'], day, _periods(policy, row['entry_period']))
    return {**_view(row, context['day'], context['calendar'], info, policy, events), 'events': [_event_view(event) for event in events]}


def _ended_info(row, events, day):
    if row['status'] == 'tracking': return None
    for event in reversed(events):
        if event['event_type'] == 'review' and event['business_date'] == day:
            info = obj(event['evidence_json'], {}).get('market_info')
            if isinstance(info, dict): return info
    return None


def change_tracking_policy(identifier, payload):
    if not isinstance(payload, dict) or set(payload)-{'policy','reason'} or 'policy' not in payload:
        fail('板块政策修改需要policy及可选reason')
    context = _context()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE'); _guard(connection, context)
        row = one(connection, 'SELECT * FROM sector_tracking_round WHERE round_id=?', (identifier,), '板块跟踪轮次不存在')
        if row['status'] != 'tracking': fail('历史板块轮次不能修改政策', 409)
        policy = policy_normalized(payload['policy'], row['entry_period'])
        if policy != obj(row['policy_json'], {}):
            revision = row['policy_revision']+1
            _event(connection, row, 'policy_change', context['day'], trading_age(context['calendar'], row['entry_business_date'], context['day']),
                'not_applicable', 'not_applicable', {'policy': policy, 'previous_policy_revision': row['policy_revision'],
                    'reason': str(payload.get('reason') or '用户修改本轮政策')[:500]}, revision=revision)
            connection.execute('UPDATE sector_tracking_round SET policy_json=?,policy_revision=?,duration_sessions=?,updated_at_ms=? WHERE round_id=?',
                               (js(policy), revision, policy['duration_sessions'], now_ms(), identifier))
    return get_tracking(identifier)


def remove_tracking(identifier, payload=None):
    payload = payload or {}
    if not isinstance(payload, dict) or set(payload)-{'reason'}: fail('移出板块跟踪只接受reason')
    context = _context()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE'); _guard(connection, context)
        row = one(connection, 'SELECT * FROM sector_tracking_round WHERE round_id=?', (identifier,), '板块跟踪轮次不存在')
        if row['status'] == 'tracking':
            reason = str(payload.get('reason') or '用户手动移出板块跟踪')[:500]
            _event(connection, row, 'manual_remove', context['day'], trading_age(context['calendar'], row['entry_business_date'], context['day']),
                   'not_applicable', 'not_applicable', {'reason': reason})
            connection.execute("UPDATE sector_tracking_round SET status='manual_removed',end_business_date=?,end_reason=?,updated_at_ms=? WHERE round_id=?",
                               (context['day'], reason, now_ms(), identifier))
    return get_tracking(identifier)


def list_events(strategy_id=None, event_type=None, business_date=None, limit=100, round_id=None):
    if event_type is not None and event_type not in EVENT_TYPES: fail('不支持的板块跟踪事件类型')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000: fail('事件条数需要1至1000')
    with connect() as connection:
        sql = 'SELECT e.*,r.target_id,r.topic_code,r.topic_id,r.name,r.sector_kind,r.strategy_id,s.name strategy_name FROM sector_tracking_event e JOIN sector_tracking_round r USING(round_id) JOIN sector_strategy s USING(strategy_id) WHERE 1=1'; args = []
        for column, value in (('r.strategy_id', strategy_id), ('e.event_type', event_type), ('e.business_date', business_date), ('e.round_id', round_id)):
            if value: sql += ' AND '+column+'=?'; args.append(value)
        total = connection.execute('SELECT count(*) FROM ('+sql+')', args).fetchone()[0]
        data = rows(connection, sql+' ORDER BY e.business_date DESC,e.recorded_at_ms DESC,e.rowid DESC LIMIT ?', args+[limit])
        day = current(connection)
    return {'items': [{**_event_view(row), 'target_type': 'sector'} for row in data], 'total': total,
            'business_date': day, 'target_type': 'sector'}


def _review_impl(payload, context, job_id):
    stats = {'reviewed': 0, 'alerts': 0, 'ended': 0, 'expired': 0, 'unknown': 0, 'skipped': 0}
    with connect() as connection:
        _guard(connection, context)
        sql = "SELECT * FROM sector_tracking_round WHERE status='tracking'"; args = []
        if payload.get('strategy_id'): sql += ' AND strategy_id=?'; args.append(payload['strategy_id'])
        if payload.get('round_ids'):
            sql += ' AND round_id IN ('+','.join('?' for _ in payload['round_ids'])+')'; args += payload['round_ids']
        active = rows(connection, sql, args)
    memo = {}
    for initial_row in active:
        dates = [day for day in context['calendar'] if initial_row['entry_business_date'] <= day <= context['day']]
        for day in dates:
            with connect() as connection:
                check_api_task(job_id, connection); _guard(connection, context)
                row = one(connection, 'SELECT * FROM sector_tracking_round WHERE round_id=?', (initial_row['round_id'],))
                if row['status'] != 'tracking': break
                policy, revision = _policy_at(connection, row, day)
            periods = _periods(policy, row['entry_period']); cache_key = (row['target_id'], day, periods)
            if cache_key not in memo:
                # Keep one round/day input at a time; do not retain750 histories
                # per board or compute prices while a write transaction is held.
                if len(memo) >= 64: memo.pop(next(iter(memo)))
                memo[cache_key] = _metrics_for(context['market'], row['target_id'], day, periods)
            info = memo[cache_key]
            age = trading_age(context['calendar'], row['entry_business_date'], day)
            metrics, price = _with_anchors(row, info, day, age)
            alert = _group(policy['alerts'], policy['alert_logic'], metrics, row['entry_period'])
            exit_ = _group(policy['exits'], policy['exit_logic'], metrics, row['entry_period'])
            if age == 0 and not policy['allow_entry_day_alerts']:
                alert = {'result': False, 'checks': [], 'reason': '事件日D0提醒未启用'}
            if age == 0 and not policy['allow_entry_day_exits']:
                exit_ = {'result': False, 'checks': [], 'reason': '事件日D0结束未启用'}
            state = 'ready' if price is not None else 'unknown'
            outcome = ('unknown' if price is None else 'satisfied' if alert['result'] is True or exit_['result'] is True
                       else 'unknown' if alert['result'] is None or exit_['result'] is None else 'not_satisfied')
            evidence = {'policy': policy, 'policy_revision': revision, 'alerts': alert, 'exits': exit_,
                        'metrics_by_period': metrics, 'market_info': info, 'entry_basis': obj(row['entry_basis_json'], {}),
                        'market_snapshot_id': context['snapshot_id'], 'quote_as_of': day,
                        'price_basis': 'none', 'unit': '指数点位'}
            input_key = digest({'cache_refs': info.get('cache_refs'), 'metrics': metrics,
                                'coverage': info.get('coverage'), 'market_snapshot_id': context['snapshot_id']})
            key = f"{row['round_id']}:{day}:review:{revision}:{input_key}"
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE'); check_api_task(job_id, connection); _guard(connection, context)
                live = one(connection, 'SELECT * FROM sector_tracking_round WHERE round_id=?', (row['round_id'],))
                if live['status'] != 'tracking': break
                live_policy, live_revision = _policy_at(connection, live, day)
                if live_revision != revision or live_policy != policy:
                    fail('板块政策在计算中已修改，请重新核对。', 409)
                if connection.execute('SELECT 1 FROM sector_tracking_event WHERE event_key=?', (key,)).fetchone():
                    stats['skipped'] += 1; continue
                _event(connection, row, 'review', day, age, state, outcome, evidence, key=key, revision=revision)
                stats['reviewed'] += 1
                if outcome == 'unknown': stats['unknown'] += 1
                # A missing daily quote blocks alerts, exits AND expiration.
                # Never turn a cache gap into a bearish decision or a zero price.
                if price is None: continue
                suffix = ':'+input_key
                if alert['result'] is True:
                    _event(connection, row, 'alert', day, age, state, 'satisfied', evidence,
                           key=f"{row['round_id']}:{day}:alert:{revision}"+suffix, revision=revision)
                    stats['alerts'] += 1
                ended = None
                if exit_['result'] is True:
                    ended = 'condition_ended'; kind = 'condition_end'; reason = '触发用户配置的板块结束条件'
                    stats['ended'] += 1
                elif age is not None and age > 0 and age >= policy['duration_sessions']:
                    ended = 'expired'; kind = 'expire'; reason = f"达到{policy['duration_sessions']}个市场交易日"
                    stats['expired'] += 1
                if ended:
                    _event(connection, row, kind, day, age, state,
                           'satisfied' if kind == 'condition_end' else 'not_applicable', evidence,
                           key=f"{row['round_id']}:{day}:{kind}:{revision}"+suffix, revision=revision)
                    connection.execute('UPDATE sector_tracking_round SET status=?,end_business_date=?,end_reason=?,updated_at_ms=? WHERE round_id=?',
                                       (ended, day, reason, now_ms(), row['round_id']))
    return {'business_date': context['day'], 'target_type': 'sector', **stats}


def review_tracking(payload=None):
    payload = {} if payload is None else payload
    if not isinstance(payload, dict) or set(payload)-{'strategy_id','round_ids','round_id','request_key'}:
        fail('板块核对只接受策略、轮次及请求标识')
    payload = dict(payload)
    if payload.get('round_id'):
        if payload.get('round_ids'): fail('round_id与round_ids不能同时指定')
        payload['round_ids'] = [payload.pop('round_id')]
    if 'round_ids' in payload and (not isinstance(payload['round_ids'], list) or not 1 <= len(payload['round_ids']) <= 800
            or any(not isinstance(value, str) or not value for value in payload['round_ids'])):
        fail('板块核对轮次需要1至800项')
    for key in ('strategy_id','request_key'):
        if key in payload and (not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 200):
            fail(key+'需要1至200字')
    context = _context(); identifier = new_id('job'); owner = api_lease_owner()
    request_key = payload.pop('request_key', None) or new_id('sector-review')
    parameters = {'target_type': 'sector', **payload}
    parameters_hash = digest([parameters, context['day'], context['snapshot_id']])
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE'); _guard(connection, context)
        old = connection.execute('SELECT * FROM job WHERE request_key=?', (request_key,)).fetchone()
        if old:
            if old['kind'] != 'tracking_review' or old['parameters_hash'] != parameters_hash:
                fail('此请求标识已用于其它参数或行情，请使用新的标识。', 409)
            if old['status'] == 'succeeded':
                completed = connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='completed' ORDER BY seq DESC LIMIT 1", (old['job_id'],)).fetchone()
                return obj(completed[0], {}) if completed else {}
            fail('该核对请求已经提交，请等待或使用新标识重试。', 409)
        try:
            insert(connection, 'job', {'job_id': identifier, 'request_key': request_key,
                'logical_key': new_id('sector-review'), 'kind': 'tracking_review', 'target_date': context['day'],
                'parameters_json': js(parameters), 'parameters_hash': parameters_hash, 'status': 'running',
                'phase': 'reviewing', 'lease_owner': owner, 'lease_expires_at_ms': now_ms()+120000, 'created_at_ms': now_ms()})
        except sqlite3.IntegrityError: fail('已有任务运行，请稍后手动核对板块跟踪。', 409)
        log(connection, identifier, 'info', 'reviewing', '开始核对板块指数跟踪；成员不会加入个股池')
    stop = start_api_heartbeat(identifier, owner)
    try:
        result = _review_impl(payload, context, identifier)
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE'); check_api_task(identifier, connection); _guard(connection, context)
            connection.execute("UPDATE job SET status='succeeded',phase='completed',finished_at_ms=?,accepted_batches=100 WHERE job_id=?", (now_ms(), identifier))
            log(connection, identifier, 'info', 'completed', '板块跟踪核对完成', result)
        return result
    except BaseException as error:
        with connect() as connection:
            connection.execute("UPDATE job SET status='failed',phase='failed',finished_at_ms=?,error_summary=? WHERE job_id=? AND status='running'",
                               (now_ms(), str(getattr(error,'detail',error))[:500], identifier))
        raise
    finally:
        stop()


def _sector_members(context, identity, as_of=None, membership_scope='latest'):
    if membership_scope not in ('latest', 'historical'): fail('成员范围仅支持latest或historical')
    row = identity
    day = as_of or context['day']
    status = context['market'].status()
    if (not isinstance(day, str) or day not in context['calendar']
            or status.get('data_begin') and day < status['data_begin']
            or status.get('data_end') and day > status['data_end']):
        fail('成员观察日须为已发布行情范围内的完成市场交易日')
    public_identity = {'topic_code': row['topic_code'], 'topic_id': row['topic_id'],
                       'name': row.get('name'), 'kind': row['sector_kind'], 'sector_kind': row['sector_kind']}
    if membership_scope == 'historical':
        from app.services.market_service import market_topic_members
        from app.services.stock_summary import summary_fields
        result = market_topic_members(day, source_code=row['topic_code'], kind=row['sector_kind'])
        with connect() as connection: _guard(connection, context)
        return {**result, 'items': [summary_fields(item, day=day) for item in result.get('items', [])],
                'target_type': 'sector', 'topic_code': row['topic_code'], 'sector_kind': row['sector_kind'],
                'topic': result.get('topic') or public_identity, 'membership_scope': 'historical',
                'membership_as_of': result.get('membership_observed_date'), 'quote_as_of': day,
                'stock_entry_baseline': None}
    if as_of is not None and as_of != context['day']:
        fail('latest成员使用当前业务日报价；查看指定日请显式选择historical成员范围')
    # Resolve current provider classifications by their explicit code identity,
    # not names, then reuse editable topic-library overrides and quote rules.
    from app.services import topics, topic_members
    catalog = topics._catalog_map(context['market'])
    source = catalog.get(row['topic_code']) or catalog.get(row['topic_id'])
    with connect() as connection:
        definitions = rows(connection, 'SELECT topic_id,provider_topic_key FROM topic WHERE origin=\'provider\'')
    allowed = {row['topic_code'], row['topic_id']}
    if source:
        allowed.update(str(source[key]) for key in ('topic_id','topic_code','code') if source.get(key))
    selected = [item['topic_id'] for item in definitions if item['provider_topic_key'] in allowed]
    if len(selected) != 1:
        with connect() as connection: _guard(connection, context)
        return {'items': [], 'total': 0, 'target_type': 'sector', 'topic': public_identity,
            'topic_code': row['topic_code'], 'sector_kind': row['sector_kind'],
            'membership_scope': 'latest', 'membership_as_of': source.get('observed_date') if source else None,
            'membership_observed_date': source.get('observed_date') if source else None,
            'quote_as_of': context['day'], 'members_complete': False,
            'unavailable_reason': '当前板块成员尚未导入题材库或身份不唯一，未按名称猜测成员',
            'stock_entry_baseline': None}
    result = topic_members.list_members(selected, sort_by='change_pct', sort_order='desc')
    from app.services.stock_summary import enrich_members
    master = {item.get('instrument_id') or item.get('thscode'): item
              for item in context['market'].instruments(limit=10000)}
    enriched = enrich_members(result.get('items', []), master, context['day'])
    with connect() as connection: _guard(connection, context)
    observed = result['selected_topics'][0].get('source_observed_date')
    return {**result, 'items': enriched, 'target_type': 'sector', 'topic': public_identity,
            'topic_code': row['topic_code'], 'sector_kind': row['sector_kind'], 'membership_scope': 'latest',
            'membership_as_of': observed, 'membership_observed_date': observed, 'quote_as_of': context['day'],
            'stock_entry_baseline': None,
            'membership_basis': '最新实际采集的当前来源成员及本地人工include/exclude；不是报价日的历史成员',
            'warnings': result.get('warnings', [])+['成员股票使用各自股价与K线，不套用板块入池点位或板块事件日基准。']}


def sector_members(topic_code, as_of=None, membership_scope='latest'):
    """An explicit catalog board needs no strategy or tracking round to read."""
    from app.services.sector_history import _catalog
    if not isinstance(topic_code, str) or not 1 <= len(topic_code.strip()) <= 30:
        fail('请选择明确的当前来源板块代码')
    context = _context(); code = topic_code.strip().upper()
    catalog, _, _, _ = _catalog(context['market'].reference())
    item = catalog.get(code)
    if item is None: fail('板块代码不在当前已核验行业概念目录，不能按名称猜测', 404)
    identity = {**item, 'sector_kind': item['kind']}
    return _sector_members(context, identity, as_of, membership_scope)


def members(identifier, as_of=None, membership_scope='latest'):
    """Legacy round endpoint adds its round identity to the common reader."""
    context = _context()
    with connect() as connection:
        row = one(connection, 'SELECT * FROM sector_tracking_round WHERE round_id=?', (identifier,), '板块跟踪轮次不存在')
    return {**_sector_members(context, row, as_of, membership_scope), 'round_id': identifier}
