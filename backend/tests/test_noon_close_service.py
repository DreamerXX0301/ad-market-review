"""Isolated worker/persistence/API tests; all market facts are explicit fixtures."""
from hashlib import sha256
from datetime import datetime
from copy import deepcopy
import json
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import core, worker
from app.routers import noon as router
from app.services import noon_close_service as service, noon_close_inputs as inputs
from app.services import task_service as tasks

DAY = '2026-10-28'


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for name in ('SHIXU_SQLITE_PATH', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR', 'SHIXU_PUBLISHED_DIR'):
        monkeypatch.delenv(name, raising=False)
    core.ensure_ready()
    return tmp_path


def job(identifier, kind='increment', status='succeeded', policy=True, target=DAY):
    parameters = {'noon_close_policy': service.VERSION} if policy else {}
    with core.connect() as connection:
        connection.execute('''INSERT INTO job(job_id,request_key,logical_key,kind,target_date,
          parameters_json,parameters_hash,status,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?)''',
          (identifier, identifier, identifier, kind, target, core.dumps(parameters), core.digest(parameters), status, core.now_ms()))
    return {'job_id': identifier, 'kind': kind, 'parameters_json': core.dumps(parameters)}


def publication(owner, generation='market-fixture'):
    with core.connect() as connection:
        now = core.now_ms(); identifier = 'raw-' + owner
        connection.execute('''INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,
          knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms)
          VALUES(?,'daily_raw','CN_A',?,?,?,?,'none','fixture',?,?,?,'staged','ready',?)''',
          (identifier, owner, DAY, DAY, now, core.dumps({'market_snapshot_id': generation}), identifier + '/manifest.json', 'a' * 64, now))
        connection.execute('INSERT INTO snapshot_artifact VALUES(?,?,?,?,?,?)', (identifier, identifier + '/data.parquet', 'fixture', 'a' * 64, 100, 1))
        connection.execute("INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,continuous_begin,continuous_end) VALUES(?,'daily_raw','{}','ready',1,?,?)", (identifier, DAY, DAY))
        connection.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?", (identifier,))
        connection.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id=?", (now, identifier))
        connection.execute('INSERT OR REPLACE INTO published_head VALUES(?,?,?,?)', ('daily_raw', 'CN_A', identifier, now))


def facts(count=7):
    quotes = [{'instrument_id': f'{600001+i}.SH', 'name': f'演示{i}', 'date': DAY, 'latest_price': 10+i/10,
               'prev_close': 10, 'change_pct': i, 'amount': 100+i, 'currency': 'CNY', 'price_basis': 'none'} for i in range(count)]
    closing = [{**r, 'close': 11+i/10, 'volume': 100, 'change_pct': 10+i, 'amount': 200+i} for i, r in enumerate(quotes)]
    def pool(rows): return {'date': DAY, 'state': 'observed', 'complete': True, 'source_count': len(rows), 'verified_count': len(rows), 'unverified_count': 0, 'items': rows}
    up = [{**closing[0], 'previous_close': closing[0]['close'], 'previous_board_count': 3}]
    payload = {'version': 'noon-snapshot-v1', 'date': DAY, 'stocks': quotes,
               'events': {'limit_up': pool([]), 'limit_break': pool([quotes[0]]), 'limit_down': pool([])},
               'summary': {'top_amount': quotes}, 'indices': [], 'stock_classifications': {}, 'previous_review': {}}
    close = {'date': DAY, 'source': 'fuyao', 'price_basis': 'none', 'amount_unit': 'CNY', 'amount_comparable': True,
             'stocks': closing, 'indices': [], 'events': {'limit_up': pool(up), 'limit_break': pool([]), 'limit_down': pool([])},
             'pool_semantics': {'limit_break': 'final_unsealed'}, 'max_focus_items': 20000}
    return payload, close


def capture(payload, identifier='noon_fixture'):
    owner = 'job_' + '1' * 32
    job(owner, kind='source_check')
    encoded = core.dumps(payload)
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (DAY, owner, owner, core.now_ms()))
        connection.execute('INSERT INTO market_noon_snapshot VALUES(?,?,?,?,?,?,?,?,?,?)',
          (identifier, DAY, owner, DAY+'T11:31:00+08:00', DAY+'T11:32:00+08:00', core.now_ms(), 'partial', sha256(encoded.encode()).hexdigest(), encoded, '[]'))
    return identifier


def populate(monkeypatch, count=7):
    payload, close = facts(count); capture(payload)
    owner = job('fixture-increment'); publication(owner['job_id'])
    monkeypatch.setattr(inputs, 'load_inputs', lambda generation, day, noon: (close, {'kind': 'derived-same-day-projection', 'source_snapshot_id': generation}))
    return owner, payload, close


def test_persist_idempotent_small_read_and_corrected_append(isolated, monkeypatch):
    owner, payload, close = populate(monkeypatch)
    first = service.after_increment(owner)
    assert first['created'] == 1 and not first['warnings']
    view = service.read_view(DAY); review = view['review']
    assert review['revision'] == 1 and review['noon_collected_start_at'].endswith('11:31:00+08:00')
    assert 'items' not in review and 'input_json' not in review and 'provenance_json' not in review
    assert service.after_increment(owner)['reused'] == 1
    close['stocks'][1]['close'] += .1
    assert service.after_increment(owner)['created'] == 1
    assert service.read_view(DAY)['review']['revision'] == 2
    with core.connect() as connection:
        assert connection.execute('SELECT payload_json FROM market_noon_snapshot').fetchone()[0] == core.dumps(payload)
        assert connection.execute('SELECT count(*) FROM market_noon_close_review').fetchone()[0] == 2
        row = connection.execute('SELECT * FROM market_noon_close_review ORDER BY revision LIMIT 1').fetchone()
        result = json.loads(row['result_json'])
        assert result['provenance_sha256'] == sha256(row['provenance_json'].encode()).hexdigest()
        items = [r[0] for r in connection.execute('SELECT item_json FROM market_noon_close_item WHERE review_id=? ORDER BY instrument_id', (row['review_id'],))]
        assert result['items_count'] == len(items)
        assert result['items_sha256'] == sha256(('['+','.join(items)+']').encode()).hexdigest()


@pytest.mark.parametrize('direction', ['asc', 'desc'])
def test_cursor_stable_null_last_and_filter_bound(isolated, monkeypatch, direction):
    owner, _, close = populate(monkeypatch, 11)
    close['stocks'][-1]['volume'] = 0
    service.after_increment(owner)
    review_id = service.read_view(DAY)['review']['review_id']; cursor = None; seen = []
    while True:
        page = service.read_items(DAY, review_id=review_id, section='all', sort='afternoon_change_pct', direction=direction, limit=3, cursor=cursor)
        seen.extend(page['items']); cursor = page['next_cursor']
        if not cursor: break
    assert len(seen) == 11 and len({r['instrument_id'] for r in seen}) == 11
    assert seen[-1]['afternoon_change_pct'] is None
    page = service.read_items(DAY, section='all', limit=2)
    with pytest.raises(HTTPException) as error:
        service.read_items(DAY, section='all', board='main', limit=2, cursor=page['next_cursor'])
    assert error.value.status_code == 422
    for invalid in ('W10', 'bnVsbA', 'dHJ1ZQ', 'not-a-cursor'):
        with pytest.raises(HTTPException) as error:
            service.read_items(DAY, section='all', cursor=invalid)
        assert error.value.status_code == 422


def test_codes_copy_all_matching_rows_and_flags_overlap(isolated, monkeypatch):
    owner, _, _ = populate(monkeypatch, 12); service.after_increment(owner)
    copied = service.read_items(DAY, section='all', codes_only=True, limit=1)
    assert copied['total'] == len(copied['codes']) == 12
    assert all(len(x) == 6 and x.isdigit() for x in copied['codes'])
    resealed = service.read_items(DAY, section='all', transition='resealed')
    added = service.read_items(DAY, section='all', transition='new_limit_up')
    assert resealed['total'] == added['total'] == 1
    assert added['items'][0]['transition'] == 'resealed'


def test_reads_never_project_inputs_or_follow_market_head(isolated, monkeypatch):
    owner, _, _ = populate(monkeypatch); service.after_increment(owner)
    monkeypatch.setattr(inputs, 'load_inputs', lambda *_: pytest.fail('GET cannot derive'))
    monkeypatch.setattr(core, 'get_market', lambda *_: pytest.fail('GET cannot access market files'))
    app = FastAPI(); app.include_router(router.router); client = TestClient(app)
    response = client.get('/api/v1/market/noon/close-review', params={'date': DAY})
    assert response.status_code == 200 and response.json()['review']['items_count'] == 7
    response = client.get('/api/v1/market/noon/close-review/items', params={'date': DAY, 'section': 'all', 'codes_only': True})
    assert response.status_code == 200 and len(response.json()['codes']) == 7
    assert client.get('/api/v1/market/noon/close-review', params={'date': 'bad'}).status_code == 422


def test_missing_no_snapshot_waiting_and_purged_are_distinct(isolated):
    assert service.read_view(DAY)['state'] == 'not_collected'
    owner = job('capture-without-body', kind='source_check', status='failed')
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_capture VALUES(?,?,?,?)', (DAY, owner['job_id'], owner['job_id'], core.now_ms()))
    assert service.read_view(DAY)['state'] == 'no_snapshot'
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_purge VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('missing-body', DAY, owner['job_id'], DAY, DAY, 'purged', '{}', 0, core.now_ms(), core.now_ms(), None))
    assert service.read_view(DAY)['state'] == 'purged'


def test_unavailable_input_keeps_gap_without_releasing_daily_slot(isolated, monkeypatch):
    owner, _, _ = populate(monkeypatch)
    monkeypatch.setattr(inputs, 'load_inputs', lambda *_: (_ for _ in ()).throw(ValueError('fixture SHA failed')))
    result = service.after_increment(owner)
    assert result['status'] == 'partial' and result['warnings']
    assert service.read_view(DAY)['state'] == 'waiting_close'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM market_noon_close_review').fetchone()[0] == 0


def test_immutable_rows_and_authorized_parent_cascade(isolated, monkeypatch):
    owner, _, _ = populate(monkeypatch); service.after_increment(owner)
    with core.connect() as connection:
        for query in ('UPDATE market_noon_close_review SET revision=99', 'DELETE FROM market_noon_close_review', 'DELETE FROM market_noon_close_item'):
            with pytest.raises(sqlite3.IntegrityError): connection.execute(query)
        connection.create_function('noon_purge_allowed', 1, lambda x: int(x == 'noon_fixture'))
        connection.execute("DELETE FROM market_noon_snapshot WHERE snapshot_id='noon_fixture'")
        for name in ('market_noon_close_review', 'market_noon_close_item', 'market_noon_close_attempt'):
            assert connection.execute('SELECT count(*) FROM '+name).fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1


def test_source_identity_only_change_reuses_original_result(isolated, monkeypatch):
    first, _, close = populate(monkeypatch); service.after_increment(first)
    old = service.read_view(DAY)['review']
    second = job('new-generation'); publication(second['job_id'], 'market-newgeneration')
    result = service.after_increment(second)
    assert result['reused'] == 1 and result['created'] == 0
    assert service.read_view(DAY)['review']['review_id'] == old['review_id']
    assert service.read_view(DAY)['review']['source_snapshot_id'] == 'market-fixture'


def optional_facts(close):
    close['indices'] = [{'instrument_id': '000001.SH', 'name': '合成指数', 'date': DAY,
                         'close': 100., 'change_pct': 0., 'price_basis': 'none', 'source': 'fuyao'}]
    close['sectors'] = [{'instrument_id': '881001.TI', 'name': '合成行业', 'kind': 'industry',
                         'date': DAY, 'close': 100., 'change_pct': 0., 'price_basis': 'none', 'source': 'fuyao'}]


def test_missing_optional_supplement_in_new_generation_preserves_verified_result_with_warning(isolated, monkeypatch):
    owner, _, close = populate(monkeypatch); optional_facts(close)
    assert service.after_increment(owner)['created'] == 1
    old = service.read_view(DAY)['review']
    second = job('missing-optional-generation'); publication(second['job_id'], 'market-without-supplement')
    close['sectors'] = []
    result = service.after_increment(second)
    assert result['preserved'] == 1 and result['created'] == result['reused'] == 0
    assert result['warnings'][0]['reason_code'] == 'coverage_regression'
    view = service.read_view(DAY)
    assert view['state'] == 'partial' and view['review']['review_id'] == old['review_id']
    assert view['review']['source_snapshot_id'] == 'market-fixture'
    assert view['review']['generated_at_ms'] == old['generated_at_ms']
    assert view['last_check_status'] == 'warning' and '未冒称本次' in view['last_check_reason']
    assert view['last_checked_at_ms'] >= old['generated_at_ms']
    with core.connect() as connection:
        attempt = connection.execute('SELECT * FROM market_noon_close_attempt').fetchone()
        assert attempt['status'] == 'warning' and attempt['source_job_id'] == second['job_id']
        assert connection.execute('SELECT count(*) FROM market_noon_close_review').fetchone()[0] == 1


@pytest.mark.parametrize('correction', ['stock', 'index', 'event_theme', 'new_known_zero'])
def test_known_same_day_correction_or_addition_appends_even_if_optional_coverage_lost(isolated, monkeypatch, correction):
    owner, _, close = populate(monkeypatch); optional_facts(close)
    close['events']['limit_up']['items'][0]['theme_text'] = '原事件理由'
    if correction == 'new_known_zero': close['indices'][0]['change_pct'] = None
    service.after_increment(owner)
    second = job('corrected-generation'); publication(second['job_id'], 'market-corrected')
    close['sectors'] = []
    if correction == 'stock': close['stocks'][0]['close'] = 11.2
    if correction == 'index': close['indices'][0]['close'] = 101.
    if correction == 'event_theme': close['events']['limit_up']['items'][0]['theme_text'] = '更正事件理由'
    if correction == 'new_known_zero': close['indices'][0]['change_pct'] = 0.
    result = service.after_increment(second)
    assert result['created'] == 1 and result['preserved'] == 0
    assert service.read_view(DAY)['review']['revision'] == 2


def test_corrupt_source_cannot_silently_reuse_old_result(isolated, monkeypatch):
    owner, _, close = populate(monkeypatch); optional_facts(close)
    service.after_increment(owner); old = service.read_view(DAY)['review']['review_id']
    second = job('corrupt-new-source'); publication(second['job_id'], 'market-corrupt')
    monkeypatch.setattr(inputs, 'load_inputs', lambda *_: (_ for _ in ()).throw(ValueError('synthetic object SHA mismatch')))
    result = service.after_increment(second)
    assert result['warnings'] and result['preserved'] == result['reused'] == result['created'] == 0
    view = service.read_view(DAY)
    assert view['state'] == 'partial' and view['review']['review_id'] == old


def test_real_event_coverage_loss_keeps_old_proof_but_complete_membership_change_appends(isolated, monkeypatch):
    from app.services.noon_context import _event_cohorts
    owner, _, close = populate(monkeypatch)
    old_pool = deepcopy(close['events']['limit_up'])
    service.after_increment(owner)
    indexed = {row['instrument_id']: row for row in close['stocks']}
    # The real normalizer produces unknown state, zero verification counts and
    # a different explanation when a later generation no longer has this pool.
    close['events']['limit_up'] = _event_cohorts({}, DAY, indexed)['limit_up']
    second = job('missing-final-pool'); publication(second['job_id'], 'market-missing-final-pool')
    result = service.after_increment(second)
    assert result['preserved'] == 1 and result['created'] == 0
    # A verified complete empty pool is different: it now proves that the old
    # up-limit member is absent, and that genuine same-day correction appends.
    close['events']['limit_up'] = {**old_pool, 'items': [], 'source_count': 0, 'verified_count': 0}
    result = service.after_increment(second)
    assert result['created'] == 1 and result['preserved'] == 0
    assert service.read_view(DAY)['review']['revision'] == 2


def test_real_partial_pool_loss_compares_verified_members_and_original_reason_not_coverage_counts():
    from app.services.noon_context import _event_cohorts
    _, old = facts()
    indexed = {row['instrument_id']: row for row in old['stocks']}
    rows = [{'thscode': row['instrument_id'], 'date': DAY, 'last_price': row['close'],
             'continue_day_cnt': 3, 'limit_up_reason': '原始已核验题材'} for row in old['stocks'][:2]]
    old['events'] = _event_cohorts({'pool_trade_date': DAY, 'limitup': rows}, DAY, indexed)
    newer = deepcopy(old)
    newer['events'] = _event_cohorts({'pool_trade_date': DAY, 'limitup': rows[:1]}, DAY, indexed)
    assert service._is_coverage_regression(old, newer)
    newer['events']['limit_up']['items'][0]['theme_text'] = '真实更正理由'
    assert not service._is_coverage_regression(old, newer)


def test_regression_comparator_is_field_exact_and_uses_zero_as_known_evidence():
    _, old = facts(); optional_facts(old)
    new = deepcopy(old); new['sectors'] = []
    assert service._is_coverage_regression(old, new)
    for change in ('zero', 'event', 'note', 'new_root', 'sector_correction'):
        changed = deepcopy(new)
        if change == 'zero': changed['indices'][0]['change_pct'] = 1.
        if change == 'event': changed['events']['limit_up']['items'][0]['previous_board_count'] = 4
        if change == 'note': changed['noon_observation_context'] = [{'text': '新观察', 'revision': 2}]
        if change == 'new_root': changed['future_evidence'] = {'count': 0}
        if change == 'sector_correction':
            changed['sectors'] = [{**old['sectors'][0], 'close': 99.}]
            changed['indices'] = []
        assert not service._is_coverage_regression(old, changed), change


def test_verified_no_change_may_fill_or_reuse_without_own_publication(isolated, monkeypatch):
    original, _, _ = populate(monkeypatch); service.after_increment(original)
    unchanged = job('unchanged-close', status='no_change', target=None)
    assert service.after_increment(unchanged)['status'] == 'skipped'
    result = service.after_increment(unchanged, {'status': 'no_change', 'data_status': {'data_end': DAY, 'snapshot_id': 'market-fixture'}})
    assert result['reused'] == 1 and not result['warnings']


def test_derived_self_rows_do_not_pin_parent_but_external_review_reference_does(isolated, monkeypatch):
    from app.services.noon_retention import _protected
    owner, _, _ = populate(monkeypatch); service.after_increment(owner)
    review_id = service.read_view(DAY)['review']['review_id']
    with core.connect() as connection:
        snapshots = [dict(row) for row in connection.execute('SELECT * FROM market_noon_snapshot')]
        assert _protected(connection, snapshots)[0] == set()
        connection.execute('CREATE TABLE fixture_external (close_review_id TEXT)')
        connection.execute('INSERT INTO fixture_external VALUES(?)', (review_id,))
        assert _protected(connection, snapshots)[0] == {'noon_fixture'}


def test_paused_and_cancelled_remain_stopped(isolated, monkeypatch):
    owner, _, _ = populate(monkeypatch)
    for status, exception in (('paused', tasks.TaskPaused), ('cancelled', tasks.TaskCancelled)):
        with core.connect() as connection:
            connection.execute('UPDATE job SET status=? WHERE job_id=?', (status, owner['job_id']))
        with pytest.raises(exception): service.after_increment(owner, allow_running=True)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_close_review').fetchone()[0] == 0


def test_legacy_task_does_not_open_database(monkeypatch):
    monkeypatch.setattr(service, 'connect', lambda: pytest.fail('old task cannot expand'))
    assert service.after_increment({'kind': 'increment', 'parameters_json': '{}'})['status'] == 'skipped'


def test_worker_new_close_stage_precedes_finish(isolated, monkeypatch):
    from app.services import sync_service, workspace_automation
    parameters = {'noon_close_policy': service.VERSION}
    created = tasks.create_task('increment', parameters, DAY, request_key='new-close-worker')
    claimed = tasks.claim_next('test-worker'); assert claimed['job_id'] == created['task_id']
    calls = []
    monkeypatch.setattr(sync_service, 'execute_sync', lambda *_: {'status': 'no_change'})
    def derive(job, result, *, allow_running=False, progress=None):
        assert tasks.task_view(job['job_id'])['status'] == 'running' and allow_running
        progress('noon_close_review', 99, 'fixture')
        calls.append('close')
    monkeypatch.setattr(service, 'after_increment', derive)
    monkeypatch.setattr(worker, '_queue_local_sync_checks', lambda *_: None)
    monkeypatch.setattr(worker, '_queue_followup', lambda *_: None)
    monkeypatch.setattr(workspace_automation, 'run_after_sync', lambda *_: None)
    worker.dispatch(claimed)
    assert calls == ['close'] and tasks.task_view(claimed['job_id'])['status'] == 'no_change'


def test_notes_freeze_real_pre_1300_revision_without_late_backfill(isolated, monkeypatch):
    owner, _, _ = populate(monkeypatch)
    def stamp(clock): return int(datetime.fromisoformat(DAY+'T'+clock+':00+08:00').timestamp()*1000)
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_note VALUES(?,?,?,?,?)', ('note-before','noon_fixture',1,'午间原观察',stamp('12:45')))
        connection.execute('INSERT INTO market_noon_note VALUES(?,?,?,?,?)', ('note-at-open','noon_fixture',2,'13点后判断',stamp('13:00')))
        connection.execute('INSERT INTO market_noon_note VALUES(?,?,?,?,?)', ('note-after','noon_fixture',3,'盘后新判断',stamp('18:00')))
    service.after_increment(owner)
    first = service.read_view(DAY)['review']
    assert first['observations'][0]['text'] == '午间原观察'
    assert first['observations'][0]['recorded_at_ms'] == stamp('12:45')
    assert first['observations'][0]['instrument_ids'] == []
    with core.connect() as connection:
        connection.execute('INSERT INTO market_noon_note VALUES(?,?,?,?,?)', ('note-later','noon_fixture',4,'事后另一版本',stamp('19:00')))
        frozen = json.loads(connection.execute('SELECT input_json FROM market_noon_close_review').fetchone()[0])
        assert frozen['noon_observation_context'][0]['revision'] == 1
    assert service.after_increment(owner)['reused'] == 1


def test_full_filter_optional_columns_keep_zero_and_do_not_shift_per_page(isolated, monkeypatch):
    owner, _, _ = populate(monkeypatch, 8); service.after_increment(owner)
    first = service.read_items(DAY, section='all', limit=1, sort='name')
    second = service.read_items(DAY, section='all', limit=1, sort='name', cursor=first['next_cursor'])
    assert first['available_fields'] == second['available_fields']
    assert 'noon_change_pct' in first['available_fields']
    assert 'industries' not in first['available_fields']


@pytest.mark.parametrize('action', ['pause','cancel','failure'])
def test_close_stage_does_not_override_stop_or_fail_main_publication(isolated, monkeypatch, action):
    from app.services import sync_service, workspace_automation
    created = tasks.create_task('increment', {'noon_close_policy': service.VERSION}, DAY, request_key='new-close-stop')
    claimed = tasks.claim_next('test-worker'); assert claimed['job_id'] == created['task_id']
    monkeypatch.setattr(sync_service, 'execute_sync', lambda *_: {'status': 'no_change'})
    def derive(job, *_a, **_k):
        if action == 'failure': raise RuntimeError('fixture compute failure')
        tasks.change_task(job['job_id'], action)
        raise tasks.TaskPaused() if action == 'pause' else tasks.TaskCancelled()
    monkeypatch.setattr(service, 'after_increment', derive)
    monkeypatch.setattr(worker, '_queue_local_sync_checks', lambda *_: None)
    monkeypatch.setattr(worker, '_queue_followup', lambda *_: None)
    monkeypatch.setattr(workspace_automation, 'run_after_sync', lambda *_: None)
    if action == 'failure':
        worker.dispatch(claimed)
        value = tasks.task_view(claimed['job_id'])
        assert value['status'] == 'no_change'
        assert any(r['stage']=='noon_close_review_warning' for r in value['logs'])
    else:
        with pytest.raises(tasks.TaskPaused if action=='pause' else tasks.TaskCancelled): worker.dispatch(claimed)
        assert tasks.task_view(claimed['job_id'])['status'] == ('paused' if action=='pause' else 'cancelled')
