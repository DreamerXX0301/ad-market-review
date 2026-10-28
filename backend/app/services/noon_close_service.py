"""Persisted, append-only noon-to-close reviews and bounded SQLite-only reads."""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import math
import re
import time

from fastapi import HTTPException

from app.core import connect, dumps, digest, new_id, now_ms
from app.services import task_service as tasks
from app.services.noon_collection import CHINA

VERSION = 'noon-close-v1'
MAX_DAYS = 28
MAX_ITEMS = 20000
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
SECTIONS = {'all', 'events', 'relay', 'watch', 'leaders', 'tomorrow'}
TRANSITIONS = {'all', 'kept', 'resealed', 'opened', 'new_limit_up', 'down_opened', 'new_limit_down', 'not_resealed', 'unchanged', 'unknown'}
BOARDS = {'all', 'main', 'chinext', 'star', 'beijing'}
SORTS = {'final_board_count', 'afternoon_change_pct', 'close_change_pct', 'noon_change_pct', 'amount', 'name'}
_SYMBOL = re.compile(r'\d{6}\.(?:SH|SZ|BJ)\Z')
_REVIEW_FIELDS = '''r.review_id,r.snapshot_id,r.trade_date,r.revision,r.rule_version,r.generated_at_ms,
 r.source_job_id,r.source_snapshot_id,r.noon_sha256,r.close_input_sha256,r.result_sha256,
 r.state,r.backfilled,r.result_json,s.collected_start_at,s.collected_end_at,s.content_sha256 AS parent_sha'''


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, '日期须为YYYY-MM-DD形式的真实日期') from None
    return value


def _parameters(job):
    value = job.get('parameters')
    return value if isinstance(value, dict) else json.loads(job.get('parameters_json') or '{}')


def _checked_json(encoded, expected, label):
    if sha256(encoded.encode()).hexdigest() != expected:
        raise HTTPException(409, label + 'SHA核验失败，记录保留')
    return json.loads(encoded)


def _review(row):
    if row is None:
        return None
    value = dict(row)
    if value['parent_sha'] != value['noon_sha256']:
        raise HTTPException(409, '收盘核验与原午评身份不符')
    result = _checked_json(value.pop('result_json'), value['result_sha256'], '收盘核验摘要')
    return {**result, 'review_id': value['review_id'], 'snapshot_id': value['snapshot_id'],
            'date': value['trade_date'], 'version': value['rule_version'], 'revision': value['revision'],
            'generated_at_ms': value['generated_at_ms'], 'source_job_id': value['source_job_id'],
            'source_snapshot_id': value['source_snapshot_id'], 'noon_sha256': value['noon_sha256'],
            'close_input_sha256': value['close_input_sha256'], 'backfilled': bool(value['backfilled']),
            'noon_collected_start_at': value['collected_start_at'], 'noon_collected_end_at': value['collected_end_at']}


def _select_review(connection, day, review_id=None):
    condition, args = 'r.trade_date=?', [day]
    if review_id is not None:
        if not isinstance(review_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', review_id):
            raise HTTPException(422, '核验版本标识无效')
        condition += ' AND r.review_id=?'; args.append(review_id)
    return connection.execute('SELECT ' + _REVIEW_FIELDS + ''' FROM market_noon_close_review r
        JOIN market_noon_snapshot s ON s.snapshot_id=r.snapshot_id WHERE ''' + condition +
        ' ORDER BY r.revision DESC LIMIT 1', args).fetchone()


def read_view(day):
    day = _day(day)
    with connect() as connection:
        connection.execute('BEGIN')
        row = _select_review(connection, day)
        if row:
            review = _review(row)
            attempt = connection.execute('SELECT status,reason,checked_at_ms FROM market_noon_close_attempt WHERE snapshot_id=?', (row['snapshot_id'],)).fetchone()
            warning = attempt and attempt['status'] == 'warning' and attempt['checked_at_ms'] >= row['generated_at_ms']
            return {'date': day, 'snapshot_id': row['snapshot_id'], 'state': 'partial' if warning else row['state'],
                    'reason': '最新一次补核验未完成，当前展示上次已保存的核验版本。' if warning else None,
                    'last_checked_at_ms': attempt['checked_at_ms'] if attempt else None,
                    'last_check_status': attempt['status'] if attempt else None,
                    'last_check_reason': attempt['reason'] if attempt else None, 'review': review}
        capture = connection.execute('''SELECT c.job_id,j.status,s.snapshot_id FROM market_noon_capture c
            JOIN job j ON j.job_id=c.job_id LEFT JOIN market_noon_snapshot s ON s.trade_date=c.trade_date
            WHERE c.trade_date=?''', (day,)).fetchone()
        if capture is None:
            return {'date': day, 'snapshot_id': None, 'state': 'not_collected', 'reason': '当日未采集午评；仍可查看盘后复盘与正常昨日反馈，不能反造午间记录。', 'review': None}
        if capture['snapshot_id'] is None:
            purged = connection.execute("SELECT 1 FROM market_noon_purge WHERE trade_date=? AND status='purged'", (day,)).fetchone()
            return {'date': day, 'snapshot_id': None, 'state': 'purged' if purged else 'no_snapshot',
                    'reason': '当日午评已到期清理，收盘核验同步清理。' if purged else '当日午评尚未形成有效快照，不能补造收盘对照；当天名额规则保持不变。', 'review': None}
        attempt = connection.execute('SELECT status,reason,checked_at_ms FROM market_noon_close_attempt WHERE snapshot_id=?', (capture['snapshot_id'],)).fetchone()
        return {'date': day, 'snapshot_id': capture['snapshot_id'], 'state': 'waiting_close',
                'reason': attempt['reason'] if attempt and attempt['reason'] else '等待当日盘后增量成功后，按已保存的同日事实自动核验。',
                'last_checked_at_ms': attempt['checked_at_ms'] if attempt else None,
                'last_check_status': attempt['status'] if attempt else None,
                'last_check_reason': attempt['reason'] if attempt else None, 'review': None}


def _decode_cursor(token, signature, review_id, sort, direction):
    try:
        if not isinstance(token, str) or len(token) > 2048:
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
        if not isinstance(value, dict):
            raise ValueError()
        if (value.get('signature') != signature or value.get('review_id') != review_id
                or value.get('sort') != sort or value.get('direction') != direction
                or not isinstance(value.get('id'), str) or not _SYMBOL.fullmatch(value['id'])):
            raise ValueError()
        v = value.get('value')
        if v is not None and (not isinstance(v, str) if sort == 'name' else isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(422, '分页游标与当前核验版本或筛选不符，请从第一页重新查看') from None


def read_items(day, *, review_id=None, section='events', transition='all', board='all', sort='final_board_count',
               direction='desc', cursor=None, limit=50, classification=None, codes_only=False):
    day = _day(day)
    if section not in SECTIONS or transition not in TRANSITIONS or board not in BOARDS or sort not in SORTS or direction not in ('asc', 'desc'):
        raise HTTPException(422, '核验明细筛选或排序无效')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise HTTPException(422, '每页须为1至100条')
    if classification is not None and (not isinstance(classification, str) or not 0 < len(classification) <= 400):
        raise HTTPException(422, '行业概念归属标识无效')
    with connect() as connection:
        connection.execute('BEGIN')
        row = _select_review(connection, day, review_id)
        if not row:
            raise HTTPException(404, '该日核验版本不存在或已到期清理')
        _review(row)
        identifier = row['review_id']
        clauses, args = ['review_id=?'], [identifier]
        if section != 'all':
            clauses.append('EXISTS(SELECT 1 FROM json_each(sections_json) WHERE value=?)'); args.append(section)
        scope_where, scope_args = ' AND '.join(clauses), list(args)
        if transition != 'all':
            clauses.append("(transition=? OR EXISTS(SELECT 1 FROM json_each(item_json,'$.transition_flags') WHERE value=?))")
            args.extend([transition, transition])
        if board != 'all': clauses.append('board_key=?'); args.append(board)
        if classification is not None:
            clauses.append('EXISTS(SELECT 1 FROM json_each(classification_keys_json) WHERE value=?)'); args.append(classification)
        where = ' AND '.join(clauses)
        total = connection.execute('SELECT count(*) FROM market_noon_close_item WHERE ' + where, args).fetchone()[0]
        scope_total = connection.execute('SELECT count(*) FROM market_noon_close_item WHERE ' + scope_where, scope_args).fetchone()[0]
        order = f'({sort} IS NULL),{sort} {direction.upper()},instrument_id ASC'
        if codes_only:
            values = connection.execute('SELECT instrument_id FROM market_noon_close_item WHERE ' + where + ' ORDER BY ' + order, args).fetchall()
            return {'review_id': identifier, 'codes': list(dict.fromkeys(r[0][:6] for r in values)), 'total': total, 'scope_total': scope_total}
        optional = ('noon_change_pct', 'close_change_pct', 'afternoon_change_pct', 'noon_price', 'close_price',
                    'previous_board_count', 'final_board_count', 'close_amount')
        expressions = ["max(CASE WHEN json_type(item_json,'$." + key + "') IN ('integer','real') THEN 1 ELSE 0 END)" for key in optional]
        expressions += ["max(CASE WHEN json_type(item_json,'$." + key + "')='array' AND json_array_length(item_json,'$." + key + "')>0 THEN 1 ELSE 0 END)" for key in ('industries', 'topics')]
        available = connection.execute('SELECT ' + ','.join(expressions) + ' FROM market_noon_close_item WHERE ' + where, args).fetchone()
        available_fields = [key for key, present in zip((*optional, 'industries', 'topics'), available) if present]
        signature = digest({'section': section, 'transition': transition, 'board': board, 'classification': classification})
        if cursor:
            last = _decode_cursor(cursor, signature, identifier, sort, direction)
            if last['value'] is None:
                where += f' AND {sort} IS NULL AND instrument_id>?'; args.append(last['id'])
            else:
                comparator = '<' if direction == 'desc' else '>'
                where += f' AND ({sort} IS NULL OR {sort}{comparator}? OR ({sort}=? AND instrument_id>?))'
                args.extend([last['value'], last['value'], last['id']])
        selected = connection.execute('SELECT * FROM market_noon_close_item WHERE ' + where + ' ORDER BY ' + order + ' LIMIT ?', [*args, limit + 1]).fetchall()
        items = [_checked_json(r['item_json'], r['item_sha256'], '收盘核验明细') for r in selected[:limit]]
        following = None
        if len(selected) > limit:
            last = selected[limit - 1]
            value = {'review_id': identifier, 'signature': signature, 'sort': sort, 'direction': direction, 'id': last['instrument_id'], 'value': last[sort]}
            following = base64.urlsafe_b64encode(dumps(value).encode()).decode().rstrip('=')
        return {'review_id': identifier, 'items': items, 'total': total, 'scope_total': scope_total,
                'next_cursor': following, 'sort': sort, 'direction': direction, 'limit': limit,
                'available_fields': available_fields}


def _anchor(connection, job, result=None, *, allow_running=False):
    row = connection.execute('SELECT * FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
    if row is None or row['kind'] != 'increment' or _parameters(dict(row)).get('noon_close_policy') != VERSION:
        return None
    if row['status'] == 'paused': raise tasks.TaskPaused()
    if row['status'] == 'cancelled': raise tasks.TaskCancelled()
    allowed = ('running', 'succeeded', 'partial', 'no_change') if allow_running else ('succeeded', 'partial', 'no_change')
    if row['status'] not in allowed:
        return None
    publication = connection.execute("SELECT * FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' ORDER BY published_at_ms DESC LIMIT 1", (row['job_id'],)).fetchone()
    if publication:
        anchor = publication['data_end']
        if row['target_date'] is not None and row['target_date'] != anchor:
            return None
    else:
        is_no_change = row['status'] == 'no_change' or isinstance(result, dict) and (result.get('status') == 'no_change' or result.get('no_change') is True)
        if not is_no_change:
            return None
        publication = connection.execute("SELECT s.* FROM published_head h JOIN dataset_snapshot s USING(snapshot_id) WHERE h.module='daily_raw' AND s.state='published' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
        if publication is None: return None
        frozen = connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='chain_target_frozen' ORDER BY seq LIMIT 1", (row['job_id'],)).fetchone()
        evidence = (result or {}).get('data_status', {})
        anchor = row['target_date'] or (json.loads(frozen[0]).get('target_date') if frozen else None) or evidence.get('data_end')
        generation = json.loads(publication['lineage_json']).get('market_snapshot_id')
        if anchor != publication['data_end'] or evidence and (evidence.get('data_end') != anchor or evidence.get('snapshot_id') not in (publication['snapshot_id'], generation)):
            return None
    generation = json.loads(publication['lineage_json']).get('market_snapshot_id')
    if not isinstance(generation, str): return None
    return {'date': _day(anchor), 'generation': generation}


def _attempt(snapshot_id, job_id, status, reason=None):
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        sequence = connection.execute('SELECT coalesce(max(checked_sequence),0)+1 FROM market_noon_close_attempt').fetchone()[0]
        connection.execute('''INSERT INTO market_noon_close_attempt VALUES(?,?,?,?,?,?)
          ON CONFLICT(snapshot_id) DO UPDATE SET checked_sequence=excluded.checked_sequence,
          checked_at_ms=excluded.checked_at_ms,source_job_id=excluded.source_job_id,status=excluded.status,reason=excluded.reason''',
          (snapshot_id, sequence, now_ms(), job_id, status, reason))


def _observation_context(snapshot):
    cutoff = int(datetime.fromisoformat(snapshot['trade_date'] + 'T13:00:00+08:00').timestamp() * 1000)
    with connect() as connection:
        row = connection.execute('''SELECT note_id,revision,body,recorded_at_ms FROM market_noon_note
          WHERE snapshot_id=? AND recorded_at_ms<? ORDER BY revision DESC LIMIT 1''',
          (snapshot['snapshot_id'], min(cutoff, now_ms()))).fetchone()
    if row is None or not row['body']:
        return []
    return [{'label': '午间补充观察', 'note_id': row['note_id'], 'revision': row['revision'],
             'text': row['body'], 'recorded_at_ms': row['recorded_at_ms']}]


def _known_optional_facts(inputs):
    """Only positive evidence, keyed by exact identity; zero remains a fact."""
    facts = {}
    def put(path, value):
        if value is None or isinstance(value, str) and value in ('', 'unknown'):
            return
        if isinstance(value, bool):
            if value or path[-1] not in ('complete', 'amount_comparable'):
                facts[path] = value
        elif isinstance(value, (str, int, float)):
            facts[path] = value
        elif isinstance(value, dict):
            for key, child in value.items():
                # Source paths are provenance, not changed same-day facts.
                if key != 'source_paths': put((*path, key), child)
        elif isinstance(value, list):
            for index, child in enumerate(value): put((*path, index), child)
        else:
            raise ValueError('收盘派生证据结构无效')
    for key in ('amount_unit', 'amount_comparable'):
        put((key,), inputs.get(key))
    for field in ('indices', 'sectors'):
        seen = set()
        for row in inputs.get(field, []):
            identifier = row.get('instrument_id')
            if not isinstance(identifier, str) or identifier in seen:
                return None
            seen.add(identifier)
            put((field, identifier), row)
    for pool_name in ('limit_up', 'limit_down', 'limit_break'):
        pool = inputs.get('events', {}).get(pool_name, {})
        put(('events', pool_name, 'date'), pool.get('date'))
        seen = set()
        for row in pool.get('items', []):
            identifier = row.get('instrument_id')
            if not isinstance(identifier, str) or identifier in seen:
                return None
            seen.add(identifier)
            for key in ('name', 'date', 'previous_close', 'previous_board_count', 'theme_text', 'theme_date', 'theme_source'):
                put(('events', pool_name, identifier, key), row.get(key))
        if pool.get('complete') is True:
            # A complete final pool proves absence as well as membership.
            # Changing that complete set is a correction, not missing coverage.
            facts[('events', pool_name, 'complete_membership')] = dumps(sorted(seen))
    for key, value in inputs.get('pool_semantics', {}).items():
        put(('pool_semantics', key), value)
    return facts


def _is_coverage_regression(previous, current):
    # Stock prices/volumes/amounts and the actual original observation are not
    # optional coverage. Any change, including an explicit missing value, must
    # reach a fresh review rather than silently retaining an older answer.
    optional = {'indices', 'sectors', 'events', 'pool_semantics', 'amount_unit', 'amount_comparable'}
    for key in set(previous) | set(current):
        if key in optional: continue
        if previous.get(key) != current.get(key):
            return False
    old, new = _known_optional_facts(previous), _known_optional_facts(current)
    if old is None or new is None or not len(new) < len(old):
        return False
    return all(path in old and old[path] == value for path, value in new.items())


def _preserve_verified_coverage(snapshot, inputs):
    with connect() as connection:
        connection.execute('BEGIN')
        row = connection.execute('''SELECT review_id,input_json,close_input_sha256 FROM market_noon_close_review
          WHERE snapshot_id=? ORDER BY revision DESC LIMIT 1''', (snapshot['snapshot_id'],)).fetchone()
        if row is None:
            return False
        previous = _checked_json(row['input_json'], row['close_input_sha256'], '先前收盘派生证据')
        if not _is_coverage_regression(previous, inputs):
            return False
        from app.services.noon_close_integrity import validate_close_reviews
        validate_close_reviews(connection, row['review_id'])
        return True


def _save(snapshot, job, anchor, inputs, origin, result):
    input_json = dumps(inputs); input_sha = sha256(input_json.encode()).hexdigest()
    if len(input_json.encode()) > MAX_INPUT_BYTES:
        raise ValueError('收盘派生证据超过有界预算')
    source_items = result.get('items', [])
    if len({r.get('instrument_id') for r in source_items}) != len(source_items):
        raise ValueError('核验明细存在重复证券身份')
    encoded_items = [(item, dumps(item)) for item in sorted(source_items, key=lambda r: r.get('instrument_id', ''))]
    provenance_json = dumps(origin)
    output = {k: v for k, v in result.items() if k != 'items'}
    output.update(provenance_sha256=sha256(provenance_json.encode()).hexdigest(),
                  items_count=len(encoded_items),
                  items_sha256=sha256(('[' + ','.join(encoded for _, encoded in encoded_items) + ']').encode()).hexdigest())
    result_json = dumps(output)
    if len(result_json.encode()) > MAX_RESULT_BYTES or len(result.get('items', [])) > MAX_ITEMS:
        raise ValueError('收盘核验结果超过有界预算')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        current = _anchor(connection, job, allow_running=True, result={'status': 'no_change', 'data_status': {'data_end': anchor['date'], 'snapshot_id': anchor['generation']}})
        if current != anchor:
            raise ValueError('盘后授权或行情身份已改变，未保存收盘核验')
        parent = connection.execute('SELECT content_sha256 FROM market_noon_snapshot WHERE snapshot_id=?', (snapshot['snapshot_id'],)).fetchone()
        if parent is None or parent[0] != snapshot['content_sha256']:
            raise ValueError('原午评已清理或身份改变')
        old = connection.execute('''SELECT review_id,state FROM market_noon_close_review
            WHERE snapshot_id=? AND noon_sha256=? AND close_input_sha256=? AND rule_version=?''',
            (snapshot['snapshot_id'], snapshot['content_sha256'], input_sha, VERSION)).fetchone()
        if old:
            from app.services.noon_close_integrity import validate_close_reviews
            validate_close_reviews(connection, old['review_id'])
            return old['review_id'], False, 0
        identifier = new_id('noon-close'); stamp = now_ms()
        revision = connection.execute('SELECT coalesce(max(revision),0)+1 FROM market_noon_close_review WHERE snapshot_id=?', (snapshot['snapshot_id'],)).fetchone()[0]
        state = 'available' if result.get('state') == 'available' else 'partial'
        backfilled = int(datetime.fromtimestamp(stamp / 1000, CHINA).date().isoformat() > snapshot['trade_date'])
        connection.execute('INSERT INTO market_noon_close_review VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (identifier, snapshot['snapshot_id'], snapshot['trade_date'], revision, VERSION, stamp,
             job['job_id'], anchor['generation'], snapshot['content_sha256'], input_sha,
             sha256(result_json.encode()).hexdigest(), state, backfilled, input_json, provenance_json, result_json))
        stored = len(input_json.encode()) + len(result_json.encode()) + len(provenance_json.encode())
        for item, encoded in encoded_items:
            symbol = item.get('instrument_id')
            if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
                raise ValueError('核验明细证券身份无效')
            if len(encoded.encode()) > 65536:
                raise ValueError('核验单股明细超过有界预算')
            sections = sorted(set(item.get('sections', [])) & SECTIONS - {'all'})
            keys = item.get('classification_keys', [])
            connection.execute('INSERT INTO market_noon_close_item VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identifier, snapshot['snapshot_id'], symbol, item.get('name') or symbol,
                 item.get('board') if item.get('board') in BOARDS - {'all'} else 'unknown', item.get('transition', 'unknown'),
                 item.get('final_board_count'), item.get('afternoon_change_pct'), item.get('close_change_pct'),
                 item.get('noon_change_pct'), item.get('amount'), dumps(sections), dumps(keys), encoded, sha256(encoded.encode()).hexdigest()))
            stored += len(encoded.encode())
        from app.services.noon_close_integrity import validate_close_reviews
        validate_close_reviews(connection, identifier)
    return identifier, True, stored


def after_increment(job, result=None, *, allow_running=False, progress=None):
    """Only a newly frozen manual increment may derive existing local facts."""
    if job.get('kind') != 'increment' or _parameters(job).get('noon_close_policy') != VERSION:
        return {'status': 'skipped', 'reason': '旧冻结任务未扩大午评核验范围'}
    started = time.monotonic()
    with connect() as connection:
        anchor = _anchor(connection, job, result, allow_running=allow_running)
        if anchor is None: return {'status': 'skipped', 'reason': '盘后尚无对应已发布或已核验的同日行情'}
        cutoff = (date.fromisoformat(anchor['date']) - timedelta(days=27)).isoformat()
        snapshots = [dict(r) for r in connection.execute('''SELECT s.* FROM market_noon_snapshot s
          LEFT JOIN market_noon_close_attempt a USING(snapshot_id) WHERE s.trade_date BETWEEN ? AND ?
          ORDER BY coalesce(a.checked_sequence,0),s.trade_date DESC LIMIT ?''', (cutoff, anchor['date'], MAX_DAYS))]
    report = {'status': 'completed', 'created': 0, 'reused': 0, 'preserved': 0, 'checked': 0, 'stored_bytes': 0, 'warnings': [], 'anchor_date': anchor['date']}
    from app.services.noon_close_inputs import load_inputs
    from app.services.noon_close_analysis import build_close_review
    for index, snapshot in enumerate(snapshots):
        with connect() as connection:
            if _anchor(connection, job, result, allow_running=allow_running) != anchor:
                raise ValueError('收盘核验期间盘后授权发生变化')
        if progress: progress('noon_close_review', int(index / max(1, len(snapshots)) * 100), '正在核验午评收盘结果', {'date': snapshot['trade_date'], 'current': index + 1, 'total': len(snapshots)})
        try:
            payload = _checked_json(snapshot['payload_json'], snapshot['content_sha256'], '原午评')
            observations = _observation_context(snapshot)
            payload = {**payload, 'afternoon_observations': [*payload.get('afternoon_observations', []), *observations]}
            inputs, origin = load_inputs(anchor['generation'], snapshot['trade_date'], payload)
            inputs = {**inputs, 'noon_observation_context': observations}
            # Projection still verifies its named sources; unchanged content can
            # then reuse a fully checked result without rebuilding all groups.
            with connect() as connection:
                if _anchor(connection, job, result, allow_running=allow_running) != anchor:
                    raise ValueError('收盘核验期间盘后授权发生变化')
                retained = connection.execute('''SELECT review_id,state FROM market_noon_close_review
                  WHERE snapshot_id=? AND noon_sha256=? AND close_input_sha256=? AND rule_version=?''',
                  (snapshot['snapshot_id'], snapshot['content_sha256'], digest(inputs), VERSION)).fetchone()
                if retained:
                    from app.services.noon_close_integrity import validate_close_reviews
                    validate_close_reviews(connection, retained['review_id'])
            if retained:
                _attempt(snapshot['snapshot_id'], job['job_id'], retained['state'])
                report['reused'] += 1; report['checked'] += 1
                continue
            if _preserve_verified_coverage(snapshot, inputs):
                reason = '新来源仅丢失部分同日可选覆盖，已确认值没有新增或更正；保留先前已核验版本，未冒称本次已复核缺失来源。'
                _attempt(snapshot['snapshot_id'], job['job_id'], 'warning', reason)
                report['preserved'] += 1; report['checked'] += 1
                report['warnings'].append({'date': snapshot['trade_date'], 'reason': reason, 'reason_code': 'coverage_regression'})
                continue
            calculated = build_close_review(payload, inputs)
            _, created, stored = _save(snapshot, job, anchor, inputs, origin, calculated)
            _attempt(snapshot['snapshot_id'], job['job_id'], calculated['state'])
            report['created' if created else 'reused'] += 1; report['stored_bytes'] += stored
        except (tasks.TaskPaused, tasks.TaskCancelled):
            raise
        except Exception as error:
            reason = '同日收盘来源尚未留存，或路径、SHA、日期、口径未通过核验；下次成功盘后增量补做。'
            _attempt(snapshot['snapshot_id'], job['job_id'], 'warning', reason)
            report['warnings'].append({'date': snapshot['trade_date'], 'error_type': type(error).__name__, 'reason': reason})
        report['checked'] += 1
    report['status'] = 'partial' if report['warnings'] else 'completed'
    report['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        tasks.append_event(connection, job['job_id'], 'noon_close_review_complete', '午评收盘核验已完成' if not report['warnings'] else '午评收盘核验保留缺口，已发布行情不受影响', 'warning' if report['warnings'] else 'info', report)
    return report
