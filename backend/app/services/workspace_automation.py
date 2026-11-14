"""Opt-in local computations after a manual sync, separate from data fetching."""
import json
from fastapi import HTTPException
from app.core import connect,digest,now_ms,setting,dumps
from app.services import strategies,tracking,task_service as tasks

SECTOR_AUTOMATION_MARKER = 'sector_strategy_automation_version'
SECTOR_AUTOMATION_VERSION = 'sector-after-data-chain-v1'
SECTOR_AUTOMATION_PLAN = 'sector_strategy_automation'
SECTOR_SETTINGS_PREFIX = 'sector-strategy-execution:'
_FLAGS = ('refresh_after_sync', 'auto_track')


def _sector_settings(connection, strategy_id):
    row = connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (SECTOR_SETTINGS_PREFIX + strategy_id,)).fetchone()
    value = json.loads(row[0]) if row else {}
    return {'strategy_id': strategy_id, **{field: value.get(field) is True for field in _FLAGS}}


def get_sector_settings(strategy_id):
    with connect() as connection:
        connection.execute('BEGIN')
        _require_sector(connection, strategy_id)
        return _sector_settings(connection, strategy_id)


def _require_sector(connection, strategy_id):
    row = connection.execute('SELECT name,archived FROM sector_strategy WHERE strategy_id=?', (strategy_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '板块策略不存在')
    return row


def configure_sector(strategy_id, payload):
    if not isinstance(payload, dict) or set(payload) - set(_FLAGS) or any(type(value) is not bool for value in payload.values()):
        raise HTTPException(422, '执行设置只接受布尔值刷新和自动入池开关')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        from .increment_access import require_business_unlocked
        require_business_unlocked(connection)
        _require_sector(connection, strategy_id)
        config = {**_sector_settings(connection, strategy_id), **payload}
        if config['auto_track'] and not config['refresh_after_sync']:
            raise HTTPException(422, '自动入池需要同时开启同步后重新筛选')
        stamp = now_ms()
        connection.execute('''INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms''',
            (SECTOR_SETTINGS_PREFIX + strategy_id, dumps({**config, 'updated_at_ms': stamp}), stamp))
    return config


def freeze_sector_settings():
    """Capture only explicitly enabled, current-sector settings for a new root job."""
    with connect() as connection:
        return [{'strategy_id': row['strategy_id'], 'auto_track': json.loads(row['value_json']).get('auto_track') is True}
                for row in connection.execute('''SELECT s.strategy_id,p.value_json FROM sector_strategy s
                    JOIN platform_setting p ON p.key=?||s.strategy_id
                    WHERE s.archived=0 AND json_extract(p.value_json,'$.refresh_after_sync')=true
                    ORDER BY s.created_at_ms,s.strategy_id''', (SECTOR_SETTINGS_PREFIX,))]


class _SectorAutomationDisabled(Exception):
    """The currently frozen allowance was narrowed before execution."""


def _run_sector_after_sync(parent_job_id, parameters):
    from . import sector_strategies, sector_tracking
    from .sync_workspace_followup import guard

    results = []
    for frozen in parameters.get(SECTOR_AUTOMATION_PLAN, []):
        sid = frozen['strategy_id']
        result = {'strategy_id': sid, 'target_type': 'sector'}

        def authorized(connection, auto_track=False):
            guard(parent_job_id, connection)
            strategy = _require_sector(connection, sid)
            current = _sector_settings(connection, sid)
            if strategy['archived'] or not current['refresh_after_sync'] or (auto_track and not current['auto_track']):
                raise _SectorAutomationDisabled()
            return strategy['name']

        try:
            with connect() as connection:
                result['name'] = authorized(connection)
            run = sector_strategies.run_strategy(sid, {'request_key': parent_job_id + ':sector-selection:' + sid},
                response_decision='matched', transaction_guard=authorized)
            result.update(run_id=run['run_id'], matched=run['matched_count'], unknown=run['unknown_count'], added_count=0,
                          skipped_count=0, ignored_count=0)
            if frozen['auto_track']:
                ids = [item['target_id'] for item in run['items'] if item['decision'] == 'matched']
                for offset in range(0, len(ids), 800):
                    chunk = ids[offset:offset + 800]
                    signature = digest([sid, run['run_id'], chunk])
                    def receipt(connection, added):
                        details = {'signature': signature, 'added_count': added['added_count'],
                                   'skipped_count': len(added['skipped']),
                                   'ignored_count': sum(item.get('reason_code') == 'ignored' for item in added['skipped'])}
                        tasks.append_event(connection, parent_job_id, 'sector_workspace_tracking_chunk', '已处理板块策略命中', details=details)
                        return details

                    with connect() as connection:
                        authorized(connection, True)
                        saved = connection.execute("""SELECT details_json FROM job_event WHERE job_id=? AND stage='sector_workspace_tracking_chunk'
                            AND json_extract(details_json,'$.signature')=?""", (parent_job_id, signature)).fetchone()
                    if not saved:
                        committed = {}
                        sector_tracking.add_tracking({'run_id': run['run_id'], 'target_ids': chunk}, automatic=True,
                            transaction_guard=lambda connection: authorized(connection, True),
                            on_commit=lambda connection, added: committed.update(receipt(connection, added)))
                    else:
                        committed = json.loads(saved[0])
                    for key in ('added_count', 'skipped_count', 'ignored_count'):
                        result[key] += committed[key]
                with connect() as connection:
                    authorized(connection, True)
                    has_round = connection.execute("SELECT 1 FROM sector_tracking_round WHERE strategy_id=? AND status='tracking' LIMIT 1", (sid,)).fetchone()
                if has_round:
                    result['tracking_review'] = sector_tracking.review_tracking(
                        {'strategy_id': sid, 'request_key': parent_job_id + ':sector-tracking-review:' + sid},
                        transaction_guard=lambda connection: authorized(connection, True), current_day_only=True)
        except (tasks.TaskPaused, tasks.TaskCancelled):
            raise
        except _SectorAutomationDisabled:
            if 'run_id' not in result:
                continue
            result['tracking_skipped_reason'] = '板块策略执行设置已关闭'
        except Exception as error:
            result['error'] = str(getattr(error, 'detail', '本地板块策略计算未完成，请手动重试'))
        results.append(result)
    return results


def get_settings(strategy_id):
    strategies.get_strategy(strategy_id)
    value=setting('strategy-execution:'+strategy_id,{})
    return {'strategy_id':strategy_id,'refresh_after_sync':bool(value.get('refresh_after_sync',False)),'auto_track':bool(value.get('auto_track',False))}


def configure(strategy_id,payload):
    for field in ('refresh_after_sync','auto_track'):
        if field in payload and not isinstance(payload[field],bool):raise HTTPException(422,'执行开关必须为true或false')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        from .workspace_deletion import require_strategy_present
        require_strategy_present(connection,strategy_id)
        strategies.strategy_view(connection,strategy_id)
        key='strategy-execution:'+strategy_id
        row=connection.execute('SELECT value_json FROM platform_setting WHERE key=?',(key,)).fetchone()
        value=json.loads(row[0]) if row else {}
        current={'strategy_id':strategy_id,**{field:bool(value.get(field,False)) for field in ('refresh_after_sync','auto_track')}}
        current.update({field:payload[field] for field in ('refresh_after_sync','auto_track') if field in payload})
        if current['auto_track'] and not current['refresh_after_sync']:raise HTTPException(422,'自动入池需要同时开启同步后重新筛选')
        connection.execute('INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms',(key,dumps({**current,'updated_at_ms':now_ms()}),now_ms()))
    return current


def run_after_sync(parent_job_id):
    """Defaults do nothing. User configured flags are the authority for auto-joins."""
    results=[]
    with connect() as c:
        state=c.execute('SELECT * FROM job WHERE job_id=?',(parent_job_id,)).fetchone()
        count=c.execute("SELECT count(*) FROM tracking_round WHERE status='tracking'").fetchone()[0]
    if not state or state['status'] not in ('succeeded','no_change','partial'):return []
    from .sync_workspace_followup import enabled, guard
    parameters = json.loads(state['parameters_json'])
    deferred=enabled(parameters)
    def check():
        if deferred:guard(parent_job_id)
    check()
    for strategy in strategies.list_strategies()['items']:
        check()
        if strategy['archived']:continue
        result={'strategy_id':strategy['strategy_id'],'name':strategy['name']}
        try:
            config=get_settings(strategy['strategy_id'])
            if not config['refresh_after_sync']:continue
            run=strategies.run_strategy(strategy['strategy_id'],{'request_key':parent_job_id+':selection:'+strategy['strategy_id']})
            check()
            result.update(run_id=run['run_id'],matched=run['matched_count'],unknown=run['unknown_count'],added_count=0)
            if config['auto_track']:
                ids=[r['instrument_id'] for r in run['items'] if r['decision']=='matched']
                for offset in range(0,len(ids),500):
                    check()
                    joined=tracking.add_tracking({'run_id':run['run_id'],'instrument_ids':ids[offset:offset+500]})
                    result['added_count']+=joined['added_count']
                count+=result['added_count']
        except (tasks.TaskPaused,tasks.TaskCancelled):raise
        except Exception as error:
            result['error']=str(getattr(error,'detail','本地策略计算未完成，请手动重试'))
        results.append(result)
    if count:
        check()
        try:results.append({'tracking_review':tracking.review_tracking({'request_key':parent_job_id+':tracking-review'})})
        except Exception as error:results.append({'tracking_review_error':str(getattr(error,'detail','跟踪核对未完成，请手动重试'))})
    if deferred and parameters.get(SECTOR_AUTOMATION_MARKER) == SECTOR_AUTOMATION_VERSION:
        results.extend(_run_sector_after_sync(parent_job_id, parameters))
    if results:
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            tasks.append_event(c,parent_job_id,'workspace_followup','同步后的本地策略筛选与跟踪核对已执行',details={'results':results})
    return results
