"""Immutable board-index selections. Reads local receipts; never requests data."""
from collections import OrderedDict
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
import time

from app.core import connect, get_market, new_id, now_ms
from app.analytics.indicators import WINDOWS, enrich_bars, aggregate_weekly, INDICATOR_VERSION
from app.analytics.monthly import aggregate_monthly, enrich_monthly_bars
from app.domain.conditions import metrics_from_enriched
from app.domain.sector_conditions import (FIELDS, PERIODS, EVALUATOR_VERSION, evaluate,
    finite, periods_for, policy_normalized, specs, validate)
from app.services import sector_charts as charts, task_service as tasks
from app.services.sector_history import _catalog
from app.infrastructure.config import work_dir
from app.infrastructure.storage import atomic_json, dumps
from app.services.workspace_common import (current, freeze_inputs, digest, js, obj, insert,
    one, rows, fail, e8, money, pin, api_lease_owner, start_api_heartbeat, check_api_task, log)

MAX_SUMMARIES = 1024
MAX_SUMMARY_FILE_REFS = 16384
_SUMMARIES = OrderedDict()
_SUMMARY_LOCK = RLock()
SUMMARY_CONTRACT = 'sector-strategy-metrics-verified-pair-v4'
MAX_PERSISTED_SUMMARY_BYTES = 256*1024
MAX_PERSISTED_CACHE_BYTES = 512*1024*1024
_PERSIST_LOCK = RLock()
_PERSIST_SIZES = OrderedDict()


def _summary_scope(plan, calendar, include_higher_lows=False, boll_configs_by_period=None):
    """All immutable input scopes, plus the local derived-algorithm contract."""
    return {'contract': SUMMARY_CONTRACT, 'evaluator': EVALUATOR_VERSION,
        'indicators': INDICATOR_VERSION, 'normalization': charts.NORMALIZATION_VERSION,
        'source_root': str(charts._root().resolve()), 'snapshot_id': plan['snapshot_id'],
        'topic_code': plan['topic_code'], 'as_of': plan['as_of'],
        'catalog_identity': plan['instrument'], 'request_signature': plan['request_signature'],
        'calendar_sha256': digest(calendar),
        **({'higher_lows_version':'higher-lows-v1'} if include_higher_lows else {}),
        **({'boll_contract':_boll_scope(boll_configs_by_period)} if boll_configs_by_period else {})}


def _boll_scope(configs):
    from app.analytics.boll_conditions import VERSION, config_key
    return {'version':VERSION,'parameters':{period:sorted({config_key(value) for value in values})
            for period,values in configs.items() if values}}


def _summary_paths(scope, create=False):
    # No path from a stored receipt is ever used to locate derived files.
    base = work_dir().resolve(); root = base/'sector-strategy-metrics-v1'
    if root.is_symlink(): raise ValueError('板块派生摘要目录不可为符号链接')
    key = digest(scope); shard = root/key[:2]
    if shard.is_symlink(): raise ValueError('板块派生摘要分片不可为符号链接')
    if not shard.resolve().is_relative_to(base): raise ValueError('板块派生摘要路径越界')
    if create: shard.mkdir(parents=True, exist_ok=True)
    content = shard/(key+'.json'); receipt = shard/(key+'.receipt.json')
    if content.is_symlink() or receipt.is_symlink(): raise ValueError('板块派生摘要文件不可为符号链接')
    return root, content, receipt


def _load_summary(scope, plan):
    """Derived files are an acceleration only; source SHAs remain mandatory."""
    try:
        _, content, receipt = _summary_paths(scope)
        if (not receipt.is_file() or not content.is_file() or receipt.stat().st_size > 4096
                or content.stat().st_size > MAX_PERSISTED_SUMMARY_BYTES): return None
        proof = json.loads(receipt.read_text())
        if (not isinstance(proof, dict) or proof.get('contract') != SUMMARY_CONTRACT
                or proof.get('scope_sha256') != digest(scope)): return None
        raw = content.read_bytes()
        if len(raw) != proof.get('bytes') or sha256(raw).hexdigest() != proof.get('sha256'): return None
        saved = json.loads(raw)
        if (not isinstance(saved, dict) or saved.get('scope') != scope
                or not isinstance(saved.get('entry'), dict)): return None
        entry = saved['entry']; value = entry.get('value')
        if (not isinstance(value, dict) or value.get('coverage', {}).get('available') is not True
                or value.get('coverage', {}).get('observation_date') != plan['as_of']
                or set(value.get('metrics_by_period', {})) != set(PERIODS)): return None
        if any(not isinstance(row, dict) or row.get('observed_date') and row['observed_date'] > plan['as_of']
               for row in value['metrics_by_period'].values()): return None
        if not _receipt_matches(entry, plan): return None
        # Access time applies to local, disposable derivatives only.
        try: os.utime(receipt, None)
        except OSError: pass
        return entry
    except (ValueError, OSError, TypeError, AttributeError, KeyError):
        return None


def _prune_summaries(root, changed):
    """Bound disposable files; never traverse or delete a source-cache path."""
    marker = str(root)
    state = _PERSIST_SIZES.get(marker)
    now = time.monotonic()
    if state is None or now-state['scanned_at'] > 30:
        files = {}
        if root.is_dir() and not root.is_symlink():
            for shard in root.iterdir():
                if not shard.is_dir() or shard.is_symlink(): continue
                for path in shard.iterdir():
                    if path.is_file() and not path.is_symlink() and path.suffix == '.json':
                        stat = path.stat(); files[str(path)] = (stat.st_size, stat.st_mtime_ns)
        state = {'files': files, 'scanned_at': now}
        _PERSIST_SIZES[marker] = state
    else:
        for path in changed:
            stat = path.stat(); state['files'][str(path)] = (stat.st_size, stat.st_mtime_ns)
    _PERSIST_SIZES.move_to_end(marker)
    while len(_PERSIST_SIZES) > 4: _PERSIST_SIZES.popitem(last=False)
    files = state['files']; size = sum(item[0] for item in files.values())
    # Receipt mtime is updated on persistent reads. Sorting refreshes it only
    # when eviction is necessary, so normal calls do not rescan the directory.
    if size > MAX_PERSISTED_CACHE_BYTES:
        groups = {}
        for relative, info in list(files.items()):
            path = Path(relative)
            stem = path.name.removesuffix('.receipt.json').removesuffix('.json')
            groups.setdefault((str(path.parent), stem), []).append(path)
        ordered = []
        for paths in groups.values():
            existing = [path for path in paths if path.exists() and not path.is_symlink()]
            if existing: ordered.append((max(path.stat().st_mtime_ns for path in existing), existing))
        for _, paths in sorted(ordered, key=lambda item: item[0]):
            if size <= MAX_PERSISTED_CACHE_BYTES: break
            for path in paths:
                tracked = files.pop(str(path), None)
                if tracked: size -= tracked[0]
                if path.resolve().is_relative_to(root.resolve()) and not path.is_symlink(): path.unlink(missing_ok=True)


def _save_summary(scope, entry):
    """An unwritable work cache never makes real, verified prices unavailable."""
    try:
        payload = {'scope': scope, 'entry': entry}
        serialized = (dumps(payload)+'\n').encode()
        if len(serialized) > MAX_PERSISTED_SUMMARY_BYTES: return False
        with _PERSIST_LOCK:
            root, content, receipt = _summary_paths(scope, create=True)
            atomic_json(content, payload)
            atomic_json(receipt, {'contract': SUMMARY_CONTRACT, 'scope_sha256': digest(scope),
                'sha256': sha256(serialized).hexdigest(), 'bytes': len(serialized)})
            _prune_summaries(root, (content, receipt))
        return True
    except (ValueError, OSError, TypeError):
        return False


def _remember_summary(key, entry):
    with _SUMMARY_LOCK:
        _SUMMARIES[key] = entry; _SUMMARIES.move_to_end(key)
        references = sum(len(item['value']['cache_refs']['files']) for item in _SUMMARIES.values())
        while len(_SUMMARIES) > MAX_SUMMARIES or references > MAX_SUMMARY_FILE_REFS:
            _, removed = _SUMMARIES.popitem(last=False)
            references -= len(removed['value']['cache_refs']['files'])


def _period(value):
    if value not in PERIODS: fail('板块策略只支持日K、周K、月K')
    return value


def _kind(value):
    if value not in ('all', 'industry', 'concept'): fail('请选择全部板块、行业或概念')
    return value


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100: fail('板块策略名称需要1至100字')
    return value.strip()


def _normalized(payload, old=None):
    if not isinstance(payload, dict): fail('板块策略参数需要对象')
    if set(payload)-{'name', 'period', 'sector_kind', 'definition', 'policy', 'archived'}:
        fail('板块策略包含不支持的参数')
    old = old or {}
    period = _period(payload.get('period', old.get('period', 'D')))
    try:
        definition = validate(payload.get('definition', old.get('definition', {})), period)
        policy = policy_normalized(payload.get('policy', old.get('policy')), period)
    except ValueError as error: fail(str(error))
    archived = payload.get('archived', bool(old.get('archived', False)))
    if not isinstance(archived, bool): fail('归档状态需要是/否')
    return {'name': _name(payload.get('name', old.get('name'))), 'period': period,
            'sector_kind': _kind(payload.get('sector_kind', old.get('sector_kind', 'all'))),
            'definition': definition, 'policy': policy, 'archived': archived}


def _view(connection, sid):
    strategy = one(connection, 'SELECT * FROM sector_strategy WHERE strategy_id=?', (sid,), '板块策略不存在')
    version = one(connection, 'SELECT * FROM sector_strategy_version WHERE strategy_id=? AND version=?', (sid, strategy['current_version']))
    latest = connection.execute('SELECT run_id,business_date,status FROM sector_selection_run WHERE strategy_id=? ORDER BY created_at_ms DESC,rowid DESC LIMIT 1', (sid,)).fetchone()
    return {**strategy, 'id': sid, 'target_type': 'sector', 'version': version['version'],
            'period': version['period'], 'sector_kind': version['sector_kind'],
            'definition': obj(version['definition_json'], {}), 'policy': obj(version['policy_json'], {}),
            'evaluator_version': version['evaluator_version'], 'latest_run': dict(latest) if latest else None}


def list_strategies():
    with connect() as connection:
        data = [_view(connection, row[0]) for row in connection.execute('SELECT strategy_id FROM sector_strategy ORDER BY created_at_ms,strategy_id')]
    return {'items': data, 'total': len(data), 'target_type': 'sector'}


def get_strategy(sid):
    with connect() as connection: return _view(connection, sid)


def _version(connection, sid, version, value):
    insert(connection, 'sector_strategy_version', {'strategy_id': sid, 'version': version,
        'period': value['period'], 'sector_kind': value['sector_kind'],
        'definition_json': js(value['definition']), 'definition_sha256': digest(value['definition']),
        'policy_json': js(value['policy']), 'evaluator_version': _evaluator(value), 'recorded_at_ms': now_ms()})
    connection.execute('UPDATE sector_strategy SET current_version=? WHERE strategy_id=?', (version, sid))


def _evaluator(value):
    from app.domain.conditions import names
    trees=[value['definition']]+value.get('policy',{}).get('alerts',[])+value.get('policy',{}).get('exits',[])
    if any('boll_signal' in names(tree) for tree in trees):return 'sector-index-dwm-boll-signal-v3'
    return 'sector-index-dwm-higher-lows-v2' if any('higher_lows' in names(tree) for tree in trees) else EVALUATOR_VERSION


def create_strategy(payload):
    value = _normalized(payload); sid = new_id('sector-strategy')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        insert(connection, 'sector_strategy', {'strategy_id': sid, 'name': value['name'],
            'archived': int(value['archived']), 'current_version': 1, 'created_at_ms': now_ms()})
        _version(connection, sid, 1, value)
        return _view(connection, sid)


def update_strategy(sid, payload):
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = _view(connection, sid); value = _normalized(payload, old)
        if any(value[key] != old[key] for key in ('period', 'sector_kind', 'definition', 'policy')) or old['evaluator_version'] != _evaluator(value):
            _version(connection, sid, old['version']+1, value)
        connection.execute('UPDATE sector_strategy SET name=?,archived=? WHERE strategy_id=?', (value['name'], int(value['archived']), sid))
        return _view(connection, sid)


def _hash_relative(relative, expected):
    root = charts._root().resolve()
    if not isinstance(relative, str) or not isinstance(expected, str): raise ValueError('板块输入缺少SHA依据')
    target = Path(relative)
    if target.is_absolute() or '..' in target.parts: raise ValueError('板块输入路径越界')
    path = (root/target).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > charts.MAX_ARTIFACT_BYTES:
        raise ValueError('板块输入文件缺失或越界')
    hasher = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): hasher.update(chunk)
    if hasher.hexdigest() != expected: raise ValueError('板块输入SHA校验失败')


def _proofs(pointer, code, as_of):
    """Freeze every immutable reference in a thin or full raw chain."""
    pending = [(pointer['artifact'], pointer['sha256'])]; seen = set(); result = []
    while pending:
        relative, checksum = pending.pop()
        if (relative, checksum) in seen: continue
        seen.add((relative, checksum))
        if len(seen) > charts.MAX_LINEAGE_SEGMENTS*4: raise ValueError('板块来源链超出保护范围')
        value = charts._verified_json(relative, checksum)
        result.append({'artifact': relative, 'sha256': checksum})
        if not isinstance(value, dict): continue
        if 'raw_artifact' in value:
            pending.append((value.get('raw_artifact'), value.get('raw_sha256')))
        if value.get('contract') == charts.INCREMENT_CONTRACT:
            for key in ('previous', 'tail'):
                ref = value.get(key) or {}
                pending.append((ref.get('artifact'), ref.get('sha256')))
    return {'topic_code': code, 'as_of': as_of, 'source': 'fuyao', 'price_basis': 'none',
            'artifact': pointer['artifact'], 'sha256': pointer['sha256'],
            'files': sorted(result, key=lambda row: row['artifact'])}


def _receipt_matches(entry, plan):
    root = charts._root().resolve()
    receipt = root/'accepted/fuyao/sector-chart'/(entry['cache_as_of']+'-'+plan['topic_code']+'.json')
    exact = root/'accepted/fuyao/sector-chart'/(plan['as_of']+'-'+plan['topic_code']+'.json')
    if exact != receipt and exact.is_file(): return False
    if not receipt.resolve().is_relative_to(root) or not receipt.is_file() or receipt.stat().st_size > 64*1024: return False
    if sha256(receipt.read_bytes()).hexdigest() != entry['pointer_sha256']: return False
    for ref in entry['value']['cache_refs']['files']: _hash_relative(ref['artifact'], ref['sha256'])
    return True


def _raw_metrics(row=None, previous=None, period='D', reason=None):
    result = {field: None for field in FIELDS}
    if row:
        converted = metrics_from_enriched(row, previous)
        result.update({key: (finite(converted.get(key)) if spec['type'] == 'number'
                            else converted.get(key) if isinstance(converted.get(key), bool) else None)
                       for key, spec in FIELDS.items()})
        for key in ('breakout_valid', 'breakout_previous_sessions', 'breakout_blocking_distance', 'new_high_current_close'):
            result[key] = row.get(key)
        result.update(observed_date=row['date'], indicator_warmup_bars=row.get('indicator_warmup_bars'))
    else:
        result.update(observed_date=None, indicator_warmup_bars=0)
    if result.get('volume_ratio') is None:
        result['volume_expand'] = result['volume_shrink'] = None
    result.update(indicator_period=period, price_basis='none', reason=reason)
    return result


def _traded(row):
    values = [finite(row.get(key)) for key in ('open', 'high', 'low', 'close', 'volume')]
    return all(value is not None and value > 0 for value in values) and values[1] >= max(values[0], values[2], values[3]) and values[2] <= min(values[0], values[3])


def _indicators(record, calendar, day, include_higher_lows=False, boll_configs_by_period=None):
    daily = [{**row, 'corporate_action': False, 'corporate_action_coverage_known': True,
              'volume': finite(row.get('volume')), 'amount': finite(row.get('amount'))}
             for row in record['bars'] if row['date'] <= day]
    valid = [row for row in daily if _traded(row)]
    # Missing/zero-volume days are not backfilled with an older observation.
    computed = enrich_bars([{**row, 'amount': row['amount'] if row['amount'] is not None else float('nan')} for row in valid], calendar, tail=2)
    current = computed[-1] if computed and computed[-1]['date'] == day else None
    result = {'D': _raw_metrics(current, computed[-2] if current and len(computed)>1 else None,
                               reason=None if current else '观察日缺少唯一有效成交日K，不能回退其它日期')}
    # Aggregate all real dates; invalid traded days invalidate the entire
    # period rather than silently shrinking it to a smaller set of prices.
    aggregate_rows = [{**row, 'volume': row['volume'] if row['volume'] is not None else float('nan'),
                       'amount': row['amount'] if row['amount'] is not None else float('nan')} for row in daily]
    invalid_dates = {row['date'] for row in daily if not _traded(row)}
    weeks = aggregate_weekly(aggregate_rows, calendar, day)
    source_first = daily[0]['date'] if daily else None
    first_source_week = date.fromisoformat(source_first).isocalendar()[:2] if source_first else None
    first_week_unquoted = bool(source_first and any(session < source_first
        and date.fromisoformat(session).isocalendar()[:2] == first_source_week for session in calendar))
    eligible = []; pending = None
    for row in weeks:
        start = date.fromisoformat(row['date'])-timedelta(days=date.fromisoformat(row['date']).weekday())
        observed_week = date.fromisoformat(row['date']).isocalendar()[:2]
        invalid = any(date.fromisoformat(d).isocalendar()[:2] == observed_week for d in invalid_dates)
        if (not row['complete'] or not calendar or calendar[0] > start.isoformat() or invalid
                or observed_week == first_source_week and first_week_unquoted):
            pending = '周周期形成中、首周边界或日成交报价不完整'
            continue
        if pending: row = {**row, 'weekly_continuity_reset_reason': pending}
        eligible.append(row); pending = None
    weekly = enrich_bars(eligible, tail=2)
    ended_weeks = []
    for position, session in enumerate(calendar):
        if session > day: break
        key = date.fromisoformat(session).isocalendar()[:2]
        following = calendar[position+1] if position+1 < len(calendar) else None
        if date.fromisoformat(session).weekday() == 4 or following and date.fromisoformat(following).isocalendar()[:2] != key:
            ended_weeks.append(session)
    latest_week = weekly[-1] if weekly and ended_weeks and weekly[-1]['date'] == ended_weeks[-1] else None
    result['W'] = _raw_metrics(latest_week, weekly[-2] if latest_week and len(weekly)>1 else None,
                               'W', None if weekly else '尚无完整、可核验的已结束周K')
    months = aggregate_monthly(aggregate_rows, calendar, day)
    for row in months:
        if any(d[:7] == row['date'][:7] for d in invalid_dates):
            row.update(complete=False, monthly_continuity_reset_reason='月内存在无效或零量成交日，不能视为完整月')
    monthly = [row for row in enrich_monthly_bars(months) if row.get('monthly_indicator_eligible') is True]
    ended_months = sorted({session[:7] for session in calendar if session <= day and session[:7] < day[:7]})
    if months and months[-1].get('month_closed'):
        ended_months = sorted(set(ended_months+[months[-1]['date'][:7]]))
    latest_month = monthly[-1] if monthly and ended_months and monthly[-1]['date'][:7] == ended_months[-1] else None
    result['M'] = _raw_metrics(latest_month, monthly[-2] if latest_month and len(monthly)>1 else None,
                               'M', None if monthly else '尚无完整、可核验的已结束月K')
    # These always use the exact daily window, even in a mixed W/M strategy.
    for period in ('W', 'M'):
        for key in ('close_breakout_high', 'intraday_high_change_pct', 'breakout_valid',
                    'breakout_previous_sessions', 'breakout_blocking_distance', 'new_high_current_close'):
            result[period][key] = None
    if include_higher_lows:
        from app.analytics.higher_lows import latest as higher_lows_latest
        for period in PERIODS:
            result[period]['higher_lows_state']=higher_lows_latest(daily,calendar,day,period)
    if boll_configs_by_period:
        from app.analytics.boll_conditions import latest_states
        for period,configs in boll_configs_by_period.items():
            if configs:result[period]['boll_states']=latest_states(daily,calendar,day,period,configs)
    return result, current


def _entry_from_record(record, pointer, plan, calendar, include_higher_lows=False,
                       boll_configs_by_period=None, proofs=None):
    """One canonical indicator projection; private entry may also keep two rows."""
    day = plan['as_of']; code = plan['topic_code']
    proofs = {**(_proofs(pointer, code, day) if proofs is None else proofs),
        'normalization_version': charts.NORMALIZATION_VERSION,
        'source_request': record.get('cache_source_request', record.get('source_request')),
        'cache_as_of': record.get('cache_as_of', day), 'raw_lineage': record.get('raw_lineage')}
    metrics, current_row = _indicators(record, calendar, day, include_higher_lows, boll_configs_by_period)
    value = {'metrics_by_period': metrics, 'close_price': finite(current_row.get('close')) if current_row else None,
        'cache_refs': proofs, 'coverage': {**record['coverage'], 'available': True,
            'observation_date': day, 'price_basis': 'none', 'membership_independent': True,
            'notice': '只用已核验板块指数点位；日窗严格交易日，周/月取最后完整周期，不代表历史成员或资金净流入。'}}
    cache_as_of = record.get('cache_as_of', day)
    receipt = charts._root()/'accepted/fuyao/sector-chart'/(cache_as_of+'-'+code+'.json')
    entry = {'value': deepcopy(value), 'cache_as_of': cache_as_of,
             'pointer_sha256': sha256(receipt.read_bytes()).hexdigest()}
    position = calendar.index(day) if day in calendar else 0
    previous = calendar[position-1] if position else None
    pair = [dict(row) for row in record['bars'] if row['date'] in (previous, day)]
    if cache_as_of == day and previous and len(pair) == 2 and {row['date'] for row in pair} == {previous, day}:
        # Derivative only: public metrics, coverage and source refs stay exact.
        entry['recent_pair'] = pair
        entry['recent_pair_sha256'] = charts.digest(pair)
        entry['recent_pair_source'] = {key: record.get(key) for key in
                                       ('fetched_at', 'raw_artifact', 'raw_sha256')}
    return entry


def prewarm_collected_sector(market, plan, result, guard=None):
    """Worker-only one-record derivative; no provider calls or source writes."""
    if guard: guard()
    calendar = market.calendar()
    prepared = charts.verified_collected_input(result, plan, calendar)
    if prepared is None:
        return None  # Old/stub collectors retain the ordinary post-batch path.
    if market.status().get('snapshot_id') != plan['snapshot_id']:
        raise ValueError('板块预热期间行情代际变化')
    record, pointer, proofs = prepared
    entry = _entry_from_record(record, pointer, plan, calendar, proofs=proofs)
    scope = _summary_scope(plan, calendar); key = (str(work_dir().resolve()), digest(scope))
    if guard: guard()
    _remember_summary(key, entry)
    persisted = _save_summary(scope, entry)
    if guard: guard()
    return {'topic_code': plan['topic_code'], 'available': entry['value']['coverage'].get('available') is True,
            'persistent': persisted}


def verified_recent_pair(market, topic_code, day, previous):
    """Return only a same-day derivative after actual source SHA revalidation."""
    try:
        plan = charts.plan_sector_chart(market, topic_code, day)
        scope = _summary_scope(plan, market.calendar()); key = (str(work_dir().resolve()), digest(scope))
        with _SUMMARY_LOCK: entry = _SUMMARIES.get(key)
        if entry is None or not _receipt_matches(entry, plan):
            entry = _load_summary(scope, plan)
            if entry is not None: _remember_summary(key, entry)
        if entry is None or entry.get('cache_as_of') != day:
            return None
        pair = entry.get('recent_pair')
        if (not isinstance(pair, list) or len(pair) != 2
                or {row.get('date') for row in pair if isinstance(row, dict)} != {previous, day}
                or charts.digest(pair) != entry.get('recent_pair_sha256')):
            return None
        return {'bars': deepcopy(pair), **deepcopy(entry['recent_pair_source']),
                'pointer': {key: entry['value']['cache_refs'][key] for key in ('artifact', 'sha256')}}
    except (ValueError, OSError, TypeError, AttributeError, KeyError):
        return None  # Recompute from original raw chain; never hide source damage.


def metrics_for_sector(market, topic_code, day, periods=('D',), *, include_higher_lows=False, boll_configs_by_period=None):
    """One verified daily input, all-period summaries, bounded warm reuse.

    Warm calls hash every frozen normalized/raw input again, never merely trust
    mtime. Only small summaries are retained; no 710-board history collection.
    """
    if any(period not in PERIODS for period in periods): raise ValueError('板块指标周期无效')
    plan = charts.plan_sector_chart(market, topic_code, day)
    identity = {'target_type': 'sector', 'target_id': plan['topic_code'], 'topic_code': plan['topic_code'],
        'topic_id': plan['instrument']['topic_id'], 'name': plan['instrument']['name'],
        'sector_kind': plan['instrument']['sector_kind']}
    scope = _summary_scope(plan, market.calendar(),include_higher_lows,boll_configs_by_period)
    key = (str(work_dir().resolve()), digest(scope))
    with _SUMMARY_LOCK: entry = _SUMMARIES.get(key)
    if entry:
        try: valid = _receipt_matches(entry, plan)
        except (ValueError, OSError, TypeError): valid = False
        if valid:
            with _SUMMARY_LOCK:
                if key in _SUMMARIES: _SUMMARIES.move_to_end(key)
            return {**deepcopy(entry['value']), **identity}
        with _SUMMARY_LOCK: _SUMMARIES.pop(key, None)
    persisted = _load_summary(scope, plan)
    if persisted is not None:
        _remember_summary(key, persisted)
        return {**deepcopy(persisted['value']), **identity}
    try:
        cached = charts._read_chart_cache(market, plan, market.calendar())
        if cached is None: raise ValueError('板块所选日日K尚未留存，请先在数据中心补齐')
        record, pointer = cached
        entry = _entry_from_record(record, pointer, plan, market.calendar(), include_higher_lows, boll_configs_by_period)
        _remember_summary(key, entry)
        _save_summary(scope, entry)
        return {**deepcopy(entry['value']), **identity}
    except (ValueError, OSError, TypeError, AttributeError) as error:
        reason = str(error) or '板块缓存或来源依据未通过核验'
        return {**identity, 'metrics_by_period': {p: _raw_metrics(period=p, reason=reason) for p in PERIODS},
                'close_price': None, 'cache_refs': {}, 'coverage': {'available': False,
                    'observation_date': day, 'reason': reason, 'price_basis': 'none'}}


def prewarm_sector_metrics(market, topic_codes=None, day=None, guard=None, progress=None):
    """Prime disposable summaries after an authorized local cache collection.

    No source requests or business records are created. Missing/corrupt inputs
    remain explicit failures, rather than cached zero-valued observations.
    """
    if guard: guard()
    day = day or market.status().get('data_end')
    catalog, invalid, _, ambiguous = _catalog(market.reference())
    if invalid or ambiguous: raise ValueError('板块目录未通过预热身份校验')
    codes = sorted(catalog) if topic_codes is None else list(dict.fromkeys(topic_codes))
    if not codes or len(codes) > 800 or any(code not in catalog for code in codes):
        raise ValueError('板块指标预热仅接受当前目录内1至800个明确代码')
    if day not in market.calendar() or day > market.status().get('data_end', ''):
        raise ValueError('板块指标预热日期不是已发布的市场交易日')
    failures = []; available = 0; persistent_available = 0; persistent_failures = []
    from .sector_read_context import planning_scope
    with planning_scope(market, day, catalog={code: catalog[code] for code in codes}):
        for count, code in enumerate(codes, 1):
            if guard: guard()
            value = metrics_for_sector(market, code, day, PERIODS)
            if guard: guard()
            if value['coverage'].get('available') is True:
                available += 1
                plan = charts.plan_sector_chart(market, code, day)
                scope = _summary_scope(plan, market.calendar())
                if _load_summary(scope, plan) is not None:
                    persistent_available += 1
                else:
                    persistent_failures.append({'topic_code': code,
                        'reason': '派生指标摘要未持久留存或复验未通过，使用时可从已留存行情重新计算'})
            else: failures.append({'topic_code': code, 'reason': value['coverage'].get('reason')})
            if guard: guard()
            if progress: progress(count, len(codes), code)
    if guard: guard()
    return {'date': day, 'snapshot_id': market.status().get('snapshot_id'),
        'total_count': len(codes), 'available_count': available,
        'failed_count': len(failures), 'failures': failures,
        'persistent_expected_count': available, 'persistent_available_count': persistent_available,
        'persistent_failed_count': len(persistent_failures), 'persistent_failures': persistent_failures,
        'complete': available == len(codes), 'contract': SUMMARY_CONTRACT}


def _candidate(row):
    value = obj(row['metrics_json'], {})
    return {'target_type': 'sector', 'target_id': row['topic_code'], 'topic_code': row['topic_code'],
        'topic_id': 'fuyao-'+row['topic_code'], 'name': row['name'], 'sector_kind': row['sector_kind'],
        'decision': row['decision'], 'close_price': money(row['close_price_e8']),
        'metrics_by_period': value.get('metrics_by_period', {}), 'coverage': value.get('coverage', {}),
        'checks': obj(row['checks_json'], {}), 'cache_refs': obj(row['cache_refs_json'], {})}


def get_run(rid, decision=None):
    if decision not in (None, 'matched', 'unmatched', 'unknown'): fail('板块结果筛选无效')
    with connect() as connection:
        run = one(connection, 'SELECT r.*,s.name strategy_name FROM sector_selection_run r JOIN sector_strategy s USING(strategy_id) WHERE run_id=?', (rid,), '选板运行不存在')
        counts = {key: 0 for key in ('matched', 'unmatched', 'unknown')}
        counts.update({row['decision']: row['n'] for row in rows(connection,
            'SELECT decision,count(*) n FROM sector_selection_candidate WHERE run_id=? GROUP BY decision', (rid,))})
        records = rows(connection, 'SELECT * FROM sector_selection_candidate WHERE run_id=?'+
            (' AND decision=?' if decision is not None else '')+' ORDER BY topic_code',
            (rid, decision) if decision is not None else (rid,))
        version = one(connection, 'SELECT * FROM sector_strategy_version WHERE strategy_id=? AND version=?', (run['strategy_id'], run['strategy_version']))
    return {**{key: value for key, value in run.items() if not key.endswith('_json')},
        'target_type': 'sector', 'period': version['period'], 'sector_kind': version['sector_kind'],
        'definition': obj(version['definition_json'], {}), 'policy': obj(version['policy_json'], {}),
        'evaluator_version': version['evaluator_version'], 'cache_refs': obj(run['cache_refs_json'], {}),
        'counts': counts, **{key+'_count': value for key, value in counts.items()},
        'total': sum(counts.values()), 'items': [_candidate(record) for record in records]}


def list_runs(sid=None, limit=50):
    with connect() as connection:
        if sid: _view(connection, sid)
        data = rows(connection, 'SELECT r.run_id,r.job_id,r.strategy_id,r.strategy_version,r.business_date,r.status,r.created_at_ms,s.name strategy_name FROM sector_selection_run r JOIN sector_strategy s USING(strategy_id)'+
            (' WHERE r.strategy_id=?' if sid else '')+' ORDER BY r.created_at_ms DESC,r.rowid DESC LIMIT ?',
            ((sid,) if sid else ())+(max(1, min(int(limit), 200)),))
        for row in data:
            row['target_type'] = 'sector'
            row['counts'] = {value['decision']: value['n'] for value in rows(connection,
                'SELECT decision,count(*) n FROM sector_selection_candidate WHERE run_id=? GROUP BY decision', (row['run_id'],))}
    return {'items': data, 'total': len(data), 'target_type': 'sector'}


def run_strategy(sid, payload=None, *, queued_job=None, response_decision=None):
    if response_decision not in (None, 'matched', 'unmatched', 'unknown'): fail('板块结果筛选无效')
    payload = payload or {}
    if not isinstance(payload, dict) or set(payload)-{'request_key', 'date', 'business_date'}: fail('选板只接受请求标识和当前业务日')
    request_key = payload.get('request_key') or new_id('sector-selection-request')
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200: fail('请求标识需要1至200字符')
    owner = queued_job['lease_owner'] if queued_job else api_lease_owner(); job_id = queued_job['job_id'] if queued_job else new_id('job'); rid = new_id('sector-run')
    market = get_market(); state = market.status()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        from .increment_access import require_business_unlocked
        require_business_unlocked(connection)
        old_job = connection.execute('SELECT * FROM job WHERE request_key=?', (request_key,)).fetchone()
        if old_job and not queued_job:
            old = connection.execute('SELECT run_id,strategy_id FROM sector_selection_run WHERE job_id=?', (old_job['job_id'],)).fetchone()
            if old and old['strategy_id'] == sid: return get_run(old['run_id'], response_decision)
            fail('请求标识已使用或选板仍在运行，请查看原任务', 409)
        st = _view(connection, sid)
        if st['archived']: fail('板块策略已归档，不能运行', 409)
        if st['evaluator_version'] != _evaluator(st):
            _version(connection, sid, st['version']+1, st); st = _view(connection, sid)
        day = current(connection)
        if any(payload.get(key, day) != day for key in ('date', 'business_date')): fail('选板只按当前业务日运行，盘面历史日期不影响策略', 409)
        if not state.get('ready') or state.get('data_end') != day: fail('板块策略需要当前业务日已发布主行情', 409)
        deps, primary = freeze_inputs(connection)
        if queued_job:
            from .strategy_batches import validate_item
            validate_item(connection, queued_job, st, day, deps, primary)
        else:
            try:
                insert(connection, 'job', {'job_id': job_id, 'request_key': request_key, 'logical_key': new_id('sector-selection'),
                    'kind': 'selection', 'target_date': day, 'parameters_json': js({'target_type': 'sector', 'strategy_id': sid, 'version': st['version']}),
                    'parameters_hash': digest([sid, st['version'], day, deps]), 'status': 'running', 'phase': 'evaluating',
                    'lease_owner': owner, 'lease_expires_at_ms': now_ms()+120000, 'created_at_ms': now_ms()})
            except sqlite3.IntegrityError:
                tasks.active_task_conflict(connection)
        log(connection, job_id, 'info', 'evaluating', '正在核验本地板块K线并计算选板条件')
    stop_lease = (lambda: None) if queued_job else start_api_heartbeat(job_id, owner)
    def check_task(connection=None):
        if queued_job:
            from .strategy_batches import check_item_lease
            if connection is not None: check_item_lease(queued_job, connection)
            else:
                with connect() as checked: check_item_lease(queued_job, checked)
        else: check_api_task(job_id, connection)
    try:
        catalog, failures, _, _ = _catalog(market.reference())
        selected = {code: item for code, item in catalog.items() if st['sector_kind'] == 'all' or item['kind'] == st['sector_kind']}
        if not selected: fail('当前板块目录为空或没有所选类型，未保存空选板结果', 409)
        catalog_signature = digest(selected); candidates = []; refs = {}
        periods = periods_for(st['definition'], st['period'])
        from app.analytics.boll_conditions import configs_for
        boll_by_period=configs_for(st['definition'],st['period'])
        for index, (code, item) in enumerate(sorted(selected.items()), 1):
            check_task()
            from app.domain.conditions import names
            structured='higher_lows' in names(st['definition'])
            prepared = metrics_for_sector(market, code, day, periods,
                **({'include_higher_lows':True} if structured else {}),
                **({'boll_configs_by_period':boll_by_period} if any(boll_by_period.values()) else {}))
            check = evaluate(st['definition'], prepared['metrics_by_period'], st['period'])
            decision = 'matched' if check['result'] is True else 'unmatched' if check['result'] is False else 'unknown'
            refs[code] = prepared['cache_refs']
            candidates.append({'run_id': rid, 'topic_code': code, 'name': item['name'], 'sector_kind': item['kind'],
                'decision': decision, 'close_price_e8': e8(prepared['close_price']) if prepared['close_price'] is not None else None,
                'metrics_json': js({'metrics_by_period': prepared['metrics_by_period'], 'coverage': prepared['coverage']}),
                'checks_json': js(check), 'cache_refs_json': js(prepared['cache_refs'])})
            if index == 1 or index % 10 == 0 or index == len(selected):
                tasks.progress(job_id, '核验板块条件', min(99, round(index/len(selected)*99)),
                    f'已核验 {index}/{len(selected)} 个板块', {'completed_count': index, 'total_count': len(selected),
                    'actual_completed_batches': index, 'actual_total_batches': len(selected)}, lease_owner=owner if queued_job else None)
        final_market_snapshot = get_market().status().get('snapshot_id')
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE'); check_task(connection)
            if current(connection) != day or freeze_inputs(connection) != (deps, primary) or final_market_snapshot != state.get('snapshot_id'):
                fail('主行情业务日或代际已变化，本次未保存混合输入结果，请重新运行', 409)
            insert(connection, 'sector_selection_run', {'run_id': rid, 'job_id': job_id, 'strategy_id': sid,
                'strategy_version': st['version'], 'business_date': day, 'market_snapshot_id': state['snapshot_id'],
                'catalog_signature': catalog_signature, 'input_sha256': digest([state['snapshot_id'], selected, refs, st['definition'], day]),
                'cache_refs_json': js({'items': refs, 'catalog_failures': failures}), 'status': 'completed',
                'created_at_ms': now_ms(), 'completed_at_ms': now_ms()})
            for candidate in candidates: insert(connection, 'sector_selection_candidate', candidate)
            for snapshot in set(deps.values()): pin(connection, snapshot, 'selection', rid)
            connection.execute("UPDATE job SET status='succeeded',phase='completed',finished_at_ms=?,accepted_batches=100,planned_batches=100 WHERE job_id=?", (now_ms(), job_id))
            log(connection, job_id, 'info', 'completed', '板块策略已完成；缺少缓存或预热的结果保持待核验',
                {key: sum(item['decision'] == key for item in candidates) for key in ('matched', 'unmatched', 'unknown')})
        return get_run(rid, response_decision)
    except Exception as error:
        message = str(getattr(error, 'detail', error))[:500]
        with connect() as connection:
            connection.execute("UPDATE job SET status='failed',phase='failed',finished_at_ms=?,error_summary=? WHERE job_id=? AND status='running' AND lease_owner=?", (now_ms(), message, job_id, owner))
            if connection.execute('SELECT status FROM job WHERE job_id=?', (job_id,)).fetchone()[0] == 'failed':
                log(connection, job_id, 'error', 'failed', '板块策略运行失败', {'reason': message})
        if isinstance(error, (tasks.TaskCancelled, tasks.TaskPaused)):
            fail('板块策略任务已取消或停止，未保存结果', 409)
        raise
    finally:
        stop_lease()
