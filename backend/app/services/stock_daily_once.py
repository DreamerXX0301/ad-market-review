"""Read-only daily stock-selection admission, using small persisted control facts.

No market adapter, candidate materialization or analytical database is consulted.
The write path calls this inside its existing immediate transaction, before a job
is admitted.  Existing request-key replay remains a separate idempotency rule.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException

from app.services.workspace_common import current, freeze_inputs

CONTRACT = 'stock-daily-once-v1'


def _json(value, default=None):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _configuration(strategy, *, stored=False):
    if stored:
        return {
            'definition': _json(strategy['definition_json'], {}),
            'period': strategy['signal_period'] or strategy['period'],
            'price_basis': strategy['price_basis'],
            'hot_universe': _json(strategy['hot_universe_json']),
            'trade_rules': _json(strategy['trade_rules_json']),
            'evaluator_version': strategy['evaluator_version'],
        }
    # Import only the pure version selector; never recurse into strategy_view.
    from app.services.strategies import evaluator_for
    return {
        'definition': strategy['definition'],
        'period': strategy['period'],
        'price_basis': strategy.get('price_basis', 'none'),
        'hot_universe': strategy.get('hot_universe'),
        'trade_rules': strategy.get('trade_rules'),
        'evaluator_version': evaluator_for(
            strategy['definition'], strategy['period'], strategy.get('trade_rules'),
            strategy.get('hot_universe'), strategy.get('price_basis', 'none')),
    }


def revision_signature(connection):
    revisions = [connection.execute('SELECT max(rowid) FROM ' + table).fetchone()[0]
                 for table in ('topic_revision', 'topic_override')]
    return hashlib.sha256(json.dumps(revisions, separators=(',', ':')).encode()).hexdigest()


def read_context(connection, *, day=None, dependencies=None,
                 primary_snapshot_id=None, strategy_ids=None):
    """Read one list/batch admission context, independent of universe size."""
    try:
        day = day or current(connection)
        if dependencies is None or primary_snapshot_id is None:
            dependencies, primary_snapshot_id = freeze_inputs(connection)
    except HTTPException as error:
        return {'business_date': day, 'unavailable_reason': str(error.detail)}
    latest_revision_ms = 0
    for table in ('topic_revision', 'topic_override'):
        row = connection.execute(
            'SELECT max(recorded_at_ms) FROM ' + table).fetchone()
        latest_revision_ms = max(latest_revision_ms, row[0] or 0)
    topic_signature = revision_signature(connection)
    parameters = [day]
    condition = ''
    if strategy_ids is not None:
        strategy_ids = list(dict.fromkeys(strategy_ids))
        if strategy_ids:
            condition = ' AND r.strategy_id IN (' + ','.join('?' for _ in strategy_ids) + ')'
            parameters.extend(strategy_ids)
        else:
            condition = ' AND 0'
    records = connection.execute(
        '''SELECT r.run_id,r.strategy_id,r.created_at_ms,r.input_snapshots_json,
                  r.primary_snapshot_id,v.definition_json,v.period,v.signal_period,
                  v.price_basis,v.hot_universe_json,v.trade_rules_json,v.evaluator_version,
                  json_extract(r.universe_definition_json,'$.selection_guard.contract') guard_contract,
                  json_extract(r.universe_definition_json,'$.selection_guard.topic_revision_signature') guard_topic_signature
           FROM selection_run r JOIN strategy_version v
             ON v.strategy_id=r.strategy_id AND v.version=r.strategy_version
           WHERE r.business_date=? AND r.status='completed' ''' + condition +
        ' ORDER BY r.created_at_ms DESC,r.rowid DESC', parameters)
    completed = {}
    for row in records:
        item = dict(row)
        if (item['primary_snapshot_id'] != primary_snapshot_id
                or _json(item['input_snapshots_json']) != dependencies):
            continue
        if item['guard_contract'] == CONTRACT:
            if item['guard_topic_signature'] != topic_signature:
                continue
        elif latest_revision_ms > item['created_at_ms']:
            # Legacy runs have no local-revision signature. A later retained
            # topic edit proves inputs changed; do not modify their old evidence.
            continue
        completed.setdefault(item['strategy_id'], []).append({
            'run_id': item['run_id'], 'configuration': _configuration(item, stored=True)})
    return {
        'business_date': day, 'dependencies': dependencies,
        'primary_snapshot_id': primary_snapshot_id,
        'topic_revision_signature': topic_signature,
        'completed': completed, 'unavailable_reason': None,
    }


def availability(connection, strategy, *, context=None, day=None,
                 dependencies=None, primary_snapshot_id=None):
    if context is None:
        context = read_context(connection, day=day, dependencies=dependencies,
                               primary_snapshot_id=primary_snapshot_id,
                               strategy_ids=[strategy['strategy_id']])
    result = {
        'can_run': False, 'state': 'unavailable',
        'business_date': context.get('business_date'),
        'completed_run_id': None, 'reason': context.get('unavailable_reason'),
    }
    if strategy.get('archived'):
        return {**result, 'reason': '策略已归档，不能运行。'}
    if result['reason']:
        return result
    configuration = _configuration(strategy)
    for completed in context['completed'].get(strategy['strategy_id'], []):
        if completed['configuration'] == configuration:
            return {
                **result, 'state': 'completed', 'completed_run_id': completed['run_id'],
                'reason': f"已读取 {result['business_date']} 相同规则与数据的已保存结果，无需重复计算；修改选股条件或参数后会重新运行。",
            }
    return {**result, 'can_run': True, 'state': 'ready', 'reason': None}


def require_available(connection, strategy, *, context):
    state = availability(connection, strategy, context=context)
    if not state['can_run']:
        raise HTTPException(409, {
            'code': 'stock_strategy_already_completed' if state['state'] == 'completed' else 'stock_strategy_unavailable',
            'message': state['reason'], 'run_availability': state,
            'run_id': state['completed_run_id'],
        })
    return state


def evidence(context):
    return {'contract': CONTRACT, 'topic_revision_signature': context['topic_revision_signature']}
