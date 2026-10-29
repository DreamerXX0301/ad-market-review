"""Read-only daily stock-selection admission over verified frozen inputs.

No market adapter, candidate materialization or analytical database is consulted.
Pool-dependent rules also verify the bounded supplement index/CAS references;
an unchanged SQLite market head alone cannot prove those external inputs stable.
The write path pre-verifies files outside its admission transaction; the short
transaction checks only those file identities and SQLite control facts.
Existing request-key replay remains a separate idempotency rule.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path
import re
import stat
from threading import RLock

from fastapi import HTTPException

from app.services.workspace_common import current, freeze_inputs

CONTRACT = 'stock-daily-once-v2'
_PREVIOUS_CONTRACT = 'stock-daily-once-v1'
_POOL_FIELDS = ('public_pools', 'annual_pools', 'annual_down_pools')
_SUPPLEMENT_CONTRACT = 'verified-stock-supplement-v1'
_MAX_PROOF_FILES = 8192
_MAX_PROOF_BYTES = 64 * 1024 * 1024
_proof_cache = OrderedDict()
_proof_lock = RLock()
_MAX_CACHED_FILES = 16384


class _ProofFiles(dict):
    byte_count = 0


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


def _needs_supplement(strategy):
    from app.domain.conditions import names
    return (strategy.get('hot_universe') is not None
            or bool(names(strategy['definition']) & {
                'limit_up', 'prev_limit_up', 'limit_up_count_1y', 'limit_up_1y'}))


def _identity(path):
    info = path.lstat()
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _files_match(files):
    for path, identity in files.items():
        try:
            actual = _identity(Path(path))
        except FileNotFoundError:
            actual = None
        except OSError:
            return False
        if actual != identity:
            return False
    return True


def _cached_proof(key, files, proof=None):
    """Disposable bounded summaries; every hit verifies every real source file."""
    with _proof_lock:
        if proof is not None:
            frozen = _ProofFiles(files); frozen.byte_count = files.byte_count
            _proof_cache[key] = (dict(proof), frozen)
            _proof_cache.move_to_end(key)
            while len(_proof_cache) > 16 or sum(len(item[1]) for item in _proof_cache.values()) > _MAX_CACHED_FILES:
                _proof_cache.popitem(last=False)
            return None
        found = _proof_cache.get(key)
        if found is not None:
            _proof_cache.move_to_end(key)
    if found is None or not _files_match(found[1]):
        return None
    files.update(found[1]); files.byte_count = found[1].byte_count
    return dict(found[0])


def _remember(files, path, identity):
    if files is None:
        return
    key = str(path)
    old = files.get(key)
    files.byte_count += (identity[3] if identity else 0) - (old[3] if old else 0)
    files[key] = identity
    if len(files) > _MAX_PROOF_FILES or files.byte_count > _MAX_PROOF_BYTES:
        raise ValueError('补充复用证明超过有界核验预算')


def _small_json(path, maximum, expected=None, *, verified_files=None):
    """Bounded local proof, without waiting for a file lock inside a SQL txn."""
    before = _identity(path)
    if not stat.S_ISREG(before[2]) or before[3] > maximum:
        raise ValueError('补充索引体积或类型无效')
    content = path.read_bytes()
    after = _identity(path)
    if before != after or len(content) > maximum:
        raise ValueError('补充索引核验期间已变化')
    actual = hashlib.sha256(content).hexdigest()
    if expected is not None and actual != expected:
        raise ValueError('补充索引SHA不一致')
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError('补充索引结构无效')
    # Bind the bytes to the identity actually checked around this read. A fresh
    # stat here could bind old bytes to a replacement that arrived afterwards.
    _remember(verified_files, path, after)
    return value, actual


def _supplement_input(connection, dependencies, *, verified_files=None):
    """Verify only input pools, without expanding their historical quote trees.

    Missing/wrong-generation input is a stable absence, not a zero count. Bad
    bytes never get a reusable proof. CAS metadata hits still check the actual
    file identity; cold hits verify SHA, including nested payload objects. This
    follows explicit index references only (max 4096), never a history walk.
    A retention/publication race fails closed rather than acquiring its lock
    while the caller holds SQLite's short admission transaction.
    """
    from app.core import published_dir
    from .supplement_storage import FIELDS, STORAGE_VERSION, _read_metadata, _object_identity
    if verified_files is None:
        verified_files = _ProofFiles()

    def metadata(pointer):
        path, _ = _object_identity(root, pointer)
        identity = _identity(path)
        _remember(verified_files, path, identity)
        result = _read_metadata(root, pointer)
        if _identity(path) != identity:
            raise ValueError('补充对象核验期间已变化')
        return result

    def verify_record(pointer):
        record = metadata(pointer)
        references = record['references']
        if references is None:
            return
        if not isinstance(references, dict) or len(references) > 64:
            raise ValueError('补充数据分字段引用无效')
        for key, reference in references.items():
            if not isinstance(key, str) or key in record['keys'] or key == '__supplement_fields_v1':
                raise ValueError('补充数据分字段引用与内容冲突')
            if set(metadata(reference)['keys']) != {'__supplement_payload_v1'}:
                raise ValueError('补充数据分字段对象格式无效')
    row = connection.execute(
        "SELECT snapshot_id,lineage_json FROM dataset_snapshot WHERE snapshot_id=? AND state='published'",
        (dependencies.get('daily_raw') or dependencies.get('analysis_bars'),)).fetchone()
    if row is None:
        return None
    lineage = _json(row['lineage_json'], {})
    if not isinstance(lineage, dict):
        return None
    generation = lineage.get('market_snapshot_id') or row['snapshot_id']
    proof = {'contract': _SUPPLEMENT_CONTRACT, 'market_snapshot_id': generation}
    root = published_dir() / 'market-supplement'
    head_path = root / 'head.json'
    try:
        if not head_path.exists():
            _remember(verified_files, head_path, None)
            return {**proof, 'state': 'absent'}
        if head_path.is_symlink():
            return None
        head, head_sha = _small_json(head_path, 64 * 1024, verified_files=verified_files)
        if head.get('snapshot_id') != generation:
            return {**proof, 'state': 'other_generation', 'head_sha256': head_sha}
        cache_key = (str(root.resolve()), generation, head_sha)
        cached = _cached_proof(cache_key, verified_files)
        if cached is not None:
            return cached
        relative, expected = head.get('artifact'), head.get('sha256')
        if (not isinstance(relative, str) or not re.fullmatch(r'(?:indexes|artifacts)/[0-9a-f]{64}\.json', relative)
                or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)):
            return None
        path = root / relative
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            return None
        document, _ = _small_json(path, 8 * 1024 * 1024, expected, verified_files=verified_files)
        if (document.get('version') != 'market-supplement-v1'
                or document.get('snapshot_id') != generation
                or document.get('storage_version') not in (None, STORAGE_VERSION)):
            return None
        # The actual evaluators read these three pool families. Fund-flow and
        # sector-index objects are not stock-rule inputs and are not expanded.
        selected = {key: value for key, value in document.items() if key not in FIELDS}
        count = 0
        for field in _POOL_FIELDS:
            if field not in document:
                continue
            records = document[field]
            if not isinstance(records, dict):
                return None
            selected[field] = records
            for day, pointer in records.items():
                count += 1
                if count > 4096 or not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                    return None
                if document.get('storage_version') == STORAGE_VERSION and isinstance(pointer, dict):
                    verify_record(pointer)
        signature = hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        result = {**proof, 'state': 'verified', 'sha256': signature}
        _cached_proof(cache_key, verified_files, result)
        return result
    except (OSError, ValueError, TypeError):
        return None


def prepare_inputs(strategy_ids=None):
    """Cold CAS hashing is read-only and happens before BEGIN IMMEDIATE."""
    from app.core import connect
    with connect() as connection:
        try:
            dependencies, primary = freeze_inputs(connection)
        except HTTPException:
            return None
        condition, parameters = '', []
        if strategy_ids is not None:
            identifiers = list(dict.fromkeys(strategy_ids))
            condition = ' AND s.strategy_id IN (' + ','.join('?' for _ in identifiers) + ')' if identifiers else ' AND 0'
            parameters = identifiers
        strategies = connection.execute(
            '''SELECT v.definition_json,v.hot_universe_json FROM strategy s JOIN strategy_version v
                 ON v.strategy_id=s.strategy_id AND v.version=s.current_version
               WHERE s.archived=0''' + condition, parameters).fetchall()
        if not any(_needs_supplement({'definition': _json(row['definition_json'], {}),
                                     'hot_universe': _json(row['hot_universe_json'])}) for row in strategies):
            return None
        files = _ProofFiles()
        value = _supplement_input(connection, dependencies, verified_files=files)
        return {'dependencies': dependencies, 'primary_snapshot_id': primary,
                'supplement_input': value, 'files': files}


def _prepared_matches(prepared, dependencies, primary):
    if prepared['dependencies'] != dependencies or prepared['primary_snapshot_id'] != primary:
        return False
    return _files_match(prepared['files'])


def supplemental_inputs(connection, strategy, context):
    """Small persisted dependency evidence, shared once by list/batch admission."""
    if not _needs_supplement(strategy):
        return {}
    if 'supplement_input' not in context:
        # Mutation responses without a preflight may conservatively show ready;
        # never turn their write transaction into a cold historical SHA scan.
        context['supplement_input'] = (None if connection.in_transaction else
                                       _supplement_input(connection, context['dependencies']))
    return {'supplement_input': context['supplement_input']}


def require_unchanged_inputs(connection, strategy, context, prepared):
    if _needs_supplement(strategy) and (prepared is None or
            not _prepared_matches(prepared, context['dependencies'], context['primary_snapshot_id']) or
            prepared['supplement_input'] != context.get('supplement_input')):
        raise HTTPException(409, '涨跌停池或年内补充数据已变化，本次未保存混合输入；请重新运行策略。')


def require_batch_inputs(connection, parameters, context):
    # Old queued jobs keep their original contract. New items bind the actual
    # pool proof (including explicit unknown) rather than silently rebasing.
    if ('selection_supplement_input' in parameters and
            context.get('supplement_input') != parameters['selection_supplement_input']):
        raise HTTPException(409, '涨跌停池或年内补充数据已变化，本批次未运行混合输入；请新建批次。')


def read_context(connection, *, day=None, dependencies=None,
                 primary_snapshot_id=None, strategy_ids=None, prepared_inputs=None):
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
                  json_extract(r.universe_definition_json,'$.selection_guard.topic_revision_signature') guard_topic_signature,
                  json_extract(r.universe_definition_json,'$.selection_guard.supplement_input') guard_supplement_input
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
        if item['guard_contract'] in (CONTRACT, _PREVIOUS_CONTRACT):
            if item['guard_topic_signature'] != topic_signature:
                continue
        elif latest_revision_ms > item['created_at_ms']:
            # Legacy runs have no local-revision signature. A later retained
            # topic edit proves inputs changed; do not modify their old evidence.
            continue
        completed.setdefault(item['strategy_id'], []).append({
            'run_id': item['run_id'], 'configuration': _configuration(item, stored=True),
            'supplement_input': _json(item['guard_supplement_input'])})
    result = {
        'business_date': day, 'dependencies': dependencies,
        'primary_snapshot_id': primary_snapshot_id,
        'topic_revision_signature': topic_signature,
        'completed': completed, 'unavailable_reason': None,
    }
    if prepared_inputs is not None:
        if not _prepared_matches(prepared_inputs, dependencies, primary_snapshot_id):
            result['unavailable_reason'] = '行情或盘面补充数据已变化，请刷新后重新运行策略。'
        else:
            result['supplement_input'] = prepared_inputs['supplement_input']
    return result


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
    matching = [completed for completed in context['completed'].get(strategy['strategy_id'], [])
                if completed['configuration'] == configuration]
    if not matching:
        return {**result, 'can_run': True, 'state': 'ready', 'reason': None}
    supplementary = supplemental_inputs(connection, strategy, context)
    for completed in matching:
        if (not supplementary or
                (supplementary['supplement_input'] is not None
                 and completed['supplement_input'] == supplementary['supplement_input'])):
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
    return {'contract': CONTRACT, 'topic_revision_signature': context['topic_revision_signature'],
            **({'supplement_input': context['supplement_input']} if 'supplement_input' in context else {})}
