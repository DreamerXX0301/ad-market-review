"""Synthetic supplement DAGs validate bounded deletion, recovery and real guards."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import core
from app.services import supplement_retention as retention
from app.services.task_service import TaskPaused
from test_workspace import ws


def calendar():
    day, result = date(2025, 1, 1), []
    while day <= date(2026, 12, 31):
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def cas(root, category, value):
    key = category+'/'+core.digest(value)+'.json'
    path = root/key
    path.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_json(path, value)
    return {'artifact': key, 'sha256': core.hash_file(path)}


def accepted(root, day, *, capability='limitup', payload=None):
    raw = cas(root, 'raw', payload or {'source_rows': [day]})
    record = cas(root, 'artifacts', {'date': day, 'raw_artifact': raw['artifact'], 'raw_sha256': raw['sha256']})
    key = 'accepted/fuyao/'+capability+'/'+day+'.json'
    core.atomic_json(root/key, {'version': 'market-supplement-v1', **record})
    return key, record, raw


def index(root, day, created, *, snapshot='market-current', references=None, head=False):
    value = {'version': 'market-supplement-v1', 'storage_version': 'supplement-index-v2',
             'target_date': day, 'snapshot_id': snapshot, 'created_at_ms': created,
             'public_pools': references or {}}
    pointer = cas(root, 'indexes', value)
    if head:
        core.atomic_json(root/'head.json', {'version': 'market-supplement-v1', 'snapshot_id': snapshot, **pointer})
    return pointer


def tree(root):
    days = calendar()
    old_key, old, old_raw = accepted(root, days[0])
    recent_key, recent, recent_raw = accepted(root, days[-2])
    index(root, days[0], 1, snapshot='market-old', references={days[0]: old})
    index(root, days[-2], 2)
    index(root, days[-1], 3, references={days[-2]: recent}, head=True)
    return days, old_key, old, old_raw, recent_key, recent, recent_raw


def candidates(plan):
    return {row['object_key'] for row in plan['scope']['files']}


def publish_old_snapshot(connection, days):
    connection.execute("INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms) VALUES('saved-old','analysis_bars','old','fixture-job',?,?,?,'none','fixture',?,'old/manifest.json',?,'staged','ready',?)", (days[0], days[-1], core.now_ms(), json.dumps({'market_snapshot_id': 'market-old'}), 'd'*64, core.now_ms()))
    connection.execute("INSERT INTO snapshot_artifact VALUES('saved-old','old/data.parquet','fixture',?,100,100)", ('d'*64,))
    connection.execute("INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) VALUES('saved-old','fixture','{}','ready',100,?,?)", (days[0], days[-1]))
    connection.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id='saved-old'")
    connection.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id='saved-old'", (core.now_ms(),))


def test_pure_trim_keeps_exact_250_sessions_and_current_annual_exception():
    days = calendar(); target = days[-1]; cutoff = days[-250]
    early_current = next(day for day in days if day[:4] == target[:4])
    assert early_current < cutoff
    values = {day: {'date': day, 'close': 10} for day in (days[0], early_current, days[-251], cutoff, target)}
    document = {field: deepcopy(values) for field in ('public_pools', 'annual_pools', 'annual_down_pools', 'fund_flows', 'sector_index_history')}
    original = deepcopy(document)
    result = retention.trim_document(document, days, target)
    assert result['retention']['window_sessions'] == 250
    assert result['retention']['cutoff_date'] == cutoff
    for field in ('public_pools', 'fund_flows', 'sector_index_history'):
        assert set(result[field]) == {cutoff, target}
    for field in ('annual_pools', 'annual_down_pools'):
        assert early_current in result[field]
    assert result['retention']['main_history_policy'] == 'separate-five-year-window'
    assert document == original
    assert result['public_pools'][target] is document['public_pools'][target]


@pytest.mark.parametrize('broken', ['short', 'duplicate', 'out_of_order', 'invalid', 'missing_target'])
def test_uncertain_calendar_does_not_trim_any_data(broken):
    days = calendar(); target = days[-1]
    if broken == 'short': days = days[-10:]
    if broken == 'duplicate': days = days + [target]
    if broken == 'out_of_order': days[0], days[1] = days[1], days[0]
    if broken == 'invalid': days[0] = '2025-02-30'
    if broken == 'missing_target': days = days[:-1]
    document = {'public_pools': {calendar()[0]: {'value': 'must survive'}}}
    result = retention.trim_document(document, days, target)
    assert result['public_pools'] == document['public_pools']
    assert result['retention']['trim_applied'] is False


def test_plan_dag_protects_current_recent_shared_objects_and_business_generations(tmp_path):
    root = tmp_path/'supplement'; days, old_key, old, raw, recent_key, recent, _ = tree(root)
    plan = retention.plan_retention(root, days, days[-1])
    keys = candidates(plan)
    assert old_key in keys and old['artifact'] in keys and raw['artifact'] in keys
    assert recent_key not in keys and recent['artifact'] not in keys
    assert 'head.json' not in keys
    protected = retention.plan_retention(root, days, days[-1], protected_generations=['market-old'])
    assert old['artifact'] not in candidates(protected), 'saved old-market business references preserve its full supplement index closure'
    shared = index(root, days[-1], 4, references={days[-1]: old}, head=True)
    plan = retention.plan_retention(root, days, days[-1])
    assert old['artifact'] not in candidates(plan) and raw['artifact'] not in candidates(plan)
    assert shared['artifact'] not in candidates(plan)


def test_sector_full_base_tail_and_compact_field_dependencies_are_preserved(tmp_path):
    root = tmp_path/'supplement'; days, *_ = tree(root)
    base_raw = cas(root, 'raw', {'bars': [{'date': days[0], 'close': 10}]})
    base = cas(root, 'artifacts', {'raw_artifact': base_raw['artifact'], 'raw_sha256': base_raw['sha256']})
    tail_raw = cas(root, 'raw', {'bars': [{'date': days[-1], 'close': 11}]})
    tail = cas(root, 'artifacts', {'raw_artifact': tail_raw['artifact'], 'raw_sha256': tail_raw['sha256']})
    thin_raw = cas(root, 'raw', {'previous': base, 'tail': tail})
    thin = cas(root, 'artifacts', {'raw_artifact': thin_raw['artifact'], 'raw_sha256': thin_raw['sha256']})
    core.atomic_json(root/'accepted/fuyao/sector-chart'/f'{days[0]}-886001.TI.json', {'version': 'market-supplement-v1', **thin})
    payload = cas(root, 'artifacts', {'__supplement_payload_v1': ['retained data']})
    compact = cas(root, 'artifacts', {'__supplement_fields_v1': {'items': {'record_ref': payload['artifact'], 'sha256': payload['sha256']}}})
    index(root, days[-1], 4, references={days[-1]: {'record_ref': compact['artifact'], 'sha256': compact['sha256']}}, head=True)
    plan = retention.plan_retention(root, days, days[-1])
    for pointer in (base_raw, base, tail_raw, tail, thin_raw, thin, payload, compact):
        assert pointer['artifact'] in plan['protected_keys']


def test_pointer_free_payload_skips_tree_walk_but_escaped_keys_cannot_hide_references(tmp_path, monkeypatch):
    pointer = cas(tmp_path, 'artifacts', {'__supplement_payload_v1': {'rows': [{'close': 10, 'volume': 100}]}})
    original = retention._references
    def unexpected(value):
        raise AssertionError('pointer-free canonical numerical payload must skip Python traversal')
    monkeypatch.setattr(retention, '_references', unexpected)
    assert retention._node(tmp_path, pointer['artifact'])['references'] == ()
    monkeypatch.setattr(retention, '_references', original)
    child = cas(tmp_path, 'raw', {'data': 'real dependency'})
    value = {'artifact': child['artifact'], 'sha256': child['sha256']}
    key = 'artifacts/'+core.digest(value)+'.json'
    content = json.dumps(value, sort_keys=True, separators=(',', ':')).replace('artifact', 'arti\\u0066act')+'\n'
    (tmp_path/key).write_text(content)
    assert retention._node(tmp_path, key)['references'] == ((child['artifact'], child['sha256']),)
    escaped = cas(tmp_path, 'artifacts', {'prefix\\_artifact': child['artifact'], 'prefix\\_sha256': child['sha256']})
    assert retention._node(tmp_path, escaped['artifact'])['references'] == ((child['artifact'], child['sha256']),)


def test_bounded_plan_orders_parents_before_children_and_does_not_break_survivors(tmp_path):
    root = tmp_path/'supplement'; days, *_ = tree(root)
    all_candidates = retention.plan_retention(root, days, days[-1])
    small = retention.plan_retention(root, days, days[-1], max_files=2)
    assert len(small['scope']['files']) == 2 and small['scope']['backlog_files'] > 0
    for row in small['scope']['files']:
        (root/row['object_key']).unlink()
    # Every surviving pointer must still resolve even when a bounded batch ends.
    again = retention.plan_retention(root, days, days[-1])
    assert again['scope']['eligible_files'] == all_candidates['scope']['eligible_files'] - 2
    assert small['scope']['selected_bytes'] <= retention.MAX_DELETE_BYTES


@pytest.mark.parametrize('damage', ['sha', 'missing', 'symlink', 'unknown_path', 'unknown_storage'])
def test_damage_or_unknown_references_refuse_entire_plan_without_deleting(tmp_path, damage):
    root = tmp_path/'supplement'; days, old_key, old, raw, *_ = tree(root)
    if damage == 'sha': (root/raw['artifact']).write_text('{}')
    if damage == 'missing': (root/raw['artifact']).unlink()
    if damage == 'symlink':
        path = root/raw['artifact']; path.unlink(); path.symlink_to(tmp_path/'outside')
    if damage == 'unknown_path':
        bad = cas(root, 'artifacts', {'artifact': '../outside.json', 'sha256': 'a'*64})
    if damage == 'unknown_storage':
        cas(root, 'artifacts', {'storage_version': 'supplement-index-v99'})
    before = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file() or path.is_symlink()}
    with pytest.raises((ValueError, OSError)):
        retention.plan_retention(root, days, days[-1])
    assert before == {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file() or path.is_symlink()}


@pytest.fixture
def collected(ws):
    _, _ = ws
    root = core.published_dir()/'market-supplement'
    days, *details = tree(root)
    with core.connect() as c:
        c.execute("INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms) VALUES('cal-retention','calendar','retention','fixture-job',?,?,?,'none','fixture','{}','calendar-retention/manifest.json',?,'staged','ready',?)", (days[0], days[-1], core.now_ms(), 'e'*64, core.now_ms()))
        c.execute("INSERT INTO snapshot_artifact VALUES('cal-retention','calendar-retention/data.parquet','calendar',?,100,100)", ('e'*64,))
        c.execute("INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) VALUES('cal-retention','calendar','{}','ready',100,?,?)", (days[0], days[-1]))
        c.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id='cal-retention'")
        c.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id='cal-retention'", (core.now_ms(),))
        c.execute("UPDATE business_clock SET business_date=?,calendar_snapshot_id='cal-retention' WHERE singleton=1", (days[-1],))
        c.execute('UPDATE job SET parameters_json=?,target_date=? WHERE job_id=\'fixture-job\'', (json.dumps({'retention_policy_version': retention.VERSION}), days[-1]))
    market = SimpleNamespace(calendar=lambda: days, status=lambda: {'snapshot_id': 'market-current', 'data_end': days[-1]})
    document = retention.trim_document({'snapshot_id': 'market-current', 'target_date': days[-1]}, days, days[-1])
    return root, days, details, market, document


def test_worker_collection_records_exact_receipts_and_is_idempotent(collected, monkeypatch):
    root, days, details, market, document = collected
    before = retention.plan_retention(root, days, days[-1])
    result = retention.collect_after_publication('fixture-job', market, document)
    assert result['status'] == 'completed', result
    assert result['removed_files'] == len(before['scope']['files'])
    assert result['removed_bytes'] == before['scope']['selected_bytes']
    with core.connect() as c:
        run = dict(c.execute('SELECT * FROM supplement_retention_run').fetchone())
        files = [dict(row) for row in c.execute('SELECT * FROM supplement_retention_file')]
        assert run['status'] == 'completed' and core.digest(json.loads(run['scope_json'])) == run['scope_sha256']
        assert all(row['status'] == 'removed' and row['removed_bytes'] == row['byte_count'] for row in files)
    def no_second_scan(*args, **kwargs):
        raise AssertionError('completed collection must not rescan the whole supplement DAG')
    monkeypatch.setattr(retention, 'plan_retention', no_second_scan)
    again = retention.collect_after_publication('fixture-job', market, document)
    assert again['already_completed'] is True and again['removed_files'] == 0


@pytest.mark.parametrize('blocker', ['old_job', 'paused', 'backup'])
def test_old_frozen_jobs_and_other_recovery_or_backup_work_block_collection(collected, blocker):
    root, days, details, market, document = collected
    with core.connect() as c:
        if blocker == 'old_job': c.execute("UPDATE job SET parameters_json='{}' WHERE job_id='fixture-job'")
        if blocker == 'paused':
            c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('paused-job','paused-request','paused-logical','initialise','{}',?,'paused',?)", ('b'*64, core.now_ms()))
        if blocker == 'backup':
            c.execute("INSERT INTO retention_pin VALUES('backup-pin','bars','backup','one',?,NULL)", (core.now_ms(),))
    before = {path.as_posix() for path in root.rglob('*.json')}
    result = retention.collect_after_publication('fixture-job', market, document)
    assert result['status'] == 'skipped', result
    assert before == {path.as_posix() for path in root.rglob('*.json')}
    with core.connect() as c: assert c.execute('SELECT count(*) FROM supplement_retention_run').fetchone()[0] == 0


@pytest.mark.parametrize('change', ['queued_job', 'backup_pin', 'business_root'])
def test_protection_change_after_long_plan_stops_before_ledger_or_delete(collected, monkeypatch, change):
    root, days, details, market, document = collected
    if change == 'business_root':
        with core.connect() as c: publish_old_snapshot(c, days)
    original = retention.plan_retention
    def plan(*args, **kwargs):
        result = original(*args, **kwargs)
        with core.connect() as c:
            if change == 'queued_job':
                c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('new-job','new-request','new-logical','increment','{}',?,'queued',?)", ('b'*64, core.now_ms()))
            elif change == 'backup_pin':
                c.execute("INSERT INTO retention_pin VALUES('backup-pin','bars','backup','one',?,NULL)", (core.now_ms(),))
            else:
                # Even an existing published snapshot becoming a new semantic
                # root requires a fresh frozen plan; the DB timestamp is not it.
                c.execute('CREATE TABLE new_business_evidence(snapshot_id TEXT)')
                c.execute("INSERT INTO new_business_evidence VALUES('saved-old')")
        return result
    monkeypatch.setattr(retention, 'plan_retention', plan)
    result = retention.collect_after_publication('fixture-job', market, document)
    assert result['status'] == 'warning' and result['removed_files'] == 0
    with core.connect() as c: assert c.execute('SELECT count(*) FROM supplement_retention_run').fetchone()[0] == 0


def test_heartbeat_and_current_lease_during_long_plan_allow_same_protection_scope(collected, monkeypatch):
    root, days, details, market, document = collected
    original = retention.plan_retention
    def plan(*args, **kwargs):
        result = original(*args, **kwargs)
        core.set_setting('worker_heartbeat', {'pid': 123, 'at_ms': core.now_ms()})
        with core.connect() as c:
            c.execute("UPDATE job SET lease_owner='same-worker',lease_expires_at_ms=? WHERE job_id='fixture-job'", (core.now_ms()+60000,))
        return result
    monkeypatch.setattr(retention, 'plan_retention', plan)
    result = retention.collect_after_publication('fixture-job', market, document)
    assert result['status'] == 'completed', result
    assert result['benign_database_rechecks'] == 1
    assert result['removed_files'] > 0


def test_pause_mid_collection_keeps_original_plan_and_resume_handles_unlink_before_receipt(collected, monkeypatch):
    root, days, details, market, document = collected
    def guard():
        with core.connect() as c:
            removed = c.execute("SELECT count(*) FROM supplement_retention_file WHERE status='removed'").fetchone()[0]
        if removed == 1: raise TaskPaused()
    with pytest.raises(TaskPaused): retention.collect_after_publication('fixture-job', market, document, guard)
    with core.connect() as c:
        run = dict(c.execute('SELECT * FROM supplement_retention_run').fetchone())
        assert run['status'] == 'deleting'
        pending = [dict(row) for row in c.execute("SELECT * FROM supplement_retention_file WHERE status='planned' ORDER BY rowid")]
    assert pending
    # Simulate crash after unlink and before updating its exact SQL receipt.
    (root/pending[0]['object_key']).unlink()
    result = retention.collect_after_publication('fixture-job', market, document)
    assert result['status'] == 'completed', result
    with core.connect() as c:
        assert c.execute("SELECT count(*) FROM supplement_retention_file WHERE status='absent'").fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM supplement_retention_run').fetchone()[0] == 1


def test_800_sector_fallback_day_collects_2400_objects_with_short_receipts_and_resume(collected):
    root, days, details, market, document = collected
    for number in range(800):
        accepted(root, days[0], capability='sector-index-'+str(number), payload={'sector': number, 'date': days[0]})
    with retention.plan_retention(root, days, days[-1]) as plan:
        expected_files = len(plan['scope']['files'])
        expected_bytes = plan['scope']['selected_bytes']
        assert expected_files >= 2400 and plan['scope']['backlog_files'] == 0
    def pause_after_one_batch():
        with core.connect() as c:
            removed = c.execute("SELECT count(*) FROM supplement_retention_file WHERE status='removed'").fetchone()[0]
        if removed == 320: raise TaskPaused()
    with pytest.raises(TaskPaused):
        retention.collect_after_publication('fixture-job', market, document, pause_after_one_batch)
    with core.connect() as c:
        assert c.execute("SELECT count(*) FROM supplement_retention_file WHERE status='removed'").fetchone()[0] == 320
        assert c.execute("SELECT status FROM supplement_retention_run").fetchone()[0] == 'deleting'
    result = retention.collect_after_publication('fixture-job', market, document)
    assert result['status'] == 'completed' and result['backlog_files'] == 0, result
    with core.connect() as c:
        assert c.execute("SELECT count(*),sum(removed_bytes) FROM supplement_retention_file WHERE status='removed'").fetchone()[:] == (expected_files, expected_bytes)
        assert c.execute('SELECT count(*) FROM supplement_retention_run').fetchone()[0] == 1
    # Surviving live/rooted evidence remains a fully resolvable graph.
    with retention.plan_retention(root, days, days[-1]) as final:
        assert final['scope']['eligible_files'] == 0


def test_escaped_business_identifier_conservatively_protects_old_generation(collected):
    root, days, details, market, document = collected
    with core.connect() as c:
        # Fixture snapshots have no market lineage. Insert a published immutable
        # snapshot through the existing controlled state transition for this test.
        publish_old_snapshot(c, days)
        c.execute('CREATE TABLE custom_business(id INTEGER PRIMARY KEY,evidence_json TEXT)')
        c.execute('INSERT INTO custom_business(evidence_json) VALUES(?)', ('{"anchor":"market-\\u006fld"}',))
    state, reason = retention._database_scope('fixture-job')
    assert reason is None and state['unicode_reference_fallback'] is True
    assert 'market-old' in state['protected_generations']
    plan = retention.plan_retention(root, days, days[-1], protected_generations=state['protected_generations'])
    assert details[1]['artifact'] in plan['protected_keys']
