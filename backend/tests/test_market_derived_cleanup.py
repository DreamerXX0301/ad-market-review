"""Finite post-publication derivative release, with original response protection."""
import json
from pathlib import Path

import duckdb
import pytest

from app import core
from app.analytics.market_storage import write_partitions, VERSION as STORAGE_VERSION
from app.services import market_derived_cleanup as cleanup, task_service as tasks


@pytest.fixture(params=['sync', 'topics'])
def derived(tmp_path, monkeypatch, request):
    for key in ('SHIXU_SQLITE_PATH','SHIXU_PUBLISHED_DIR','SHIXU_RAW_DIR','SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    source_kind = request.param
    job = tasks.create_task('increment' if source_kind == 'sync' else 'topic_refresh', {'source': 'fixture'})['job_id']
    directory = tmp_path/('raw/sync' if source_kind == 'sync' else 'work')/job
    (directory/'raw').mkdir(parents=True)
    (directory/'accepted').mkdir()
    original = directory/('accepted/response.json' if source_kind == 'sync' else 'raw/members-test.json')
    original.write_text('{"source":"controlled fixture"}')
    if source_kind == 'sync':
        plan = {'plan': {'storage_layout': STORAGE_VERSION}, 'files': {'response.json': {'sha256': core.hash_file(original)}}}
    else:
        plan = {'storage_layout': STORAGE_VERSION, 'files': {'members-test': core.hash_file(original)}}
    canonical = directory/'raw/fuyao_stock_master.json'
    canonical.write_text('{"derived":"normalised stock master"}')
    (directory/'raw/manual-research.json').write_text('{"retain":"manual"}')
    (directory/'notes.txt').write_text('retain unregistered manual material')
    (directory/'bundle.json').write_text(json.dumps({'daily_file': 'year.parquet', 'storage_layout': STORAGE_VERSION}))
    published = tmp_path/'published'; generation = 'market-'+'e'*24
    stage = published/generation; stage.mkdir()
    con = duckdb.connect(':memory:')
    try:
        con.execute("CREATE TABLE source AS SELECT '600001.SH' thscode,1767571200000::BIGINT date_ms,10.0 open_price,11.0 high_price,9.0 low_price,10.5 close_price,100.0 volume,1000.0 turnover,'none' adjusted,'1d' AS \"interval\",'CNY' currency")
        con.execute('COPY source TO ? (FORMAT PARQUET)', [str(directory/'year.parquet')])
        files, _ = write_partitions(con, 'source', 'raw-daily.parquet', published, stage, generation)
    finally:
        con.close()
    supplemental = {canonical.name: core.hash_file(canonical)}
    if source_kind == 'topics':
        supplemental[original.name] = core.hash_file(original)
    doc = json.loads((stage/'raw-daily.manifest.json').read_text())
    status = {'snapshot_id': generation, 'storage_layout': STORAGE_VERSION, 'rows': doc['rows'], 'data_begin': doc['data_begin'], 'data_end': doc['data_end'], 'files': files}
    (stage/'manifest.json').write_text(json.dumps({'summary': status, 'source_sha256': core.hash_file(directory/'year.parquet'), 'supplemental_hashes': supplemental}))
    artifacts = [{'object_key': item['path'].removeprefix('published/'), 'role': item['role'], 'sha256': item['sha256'], 'byte_count': item['bytes']} for item in files]
    core.publish_snapshot(module='daily_raw', job_id=job, start=doc['data_begin'], end=doc['data_end'], artifacts=artifacts,
                          lineage={'market_snapshot_id': generation}, snapshot_id='daily_raw-fixture', activate=True)
    with core.connect() as connection:
        connection.execute("UPDATE job SET status='partial',phase='published' WHERE job_id=?", (job,))
    return {'job': job, 'directory': directory, 'status': status, 'plan': plan, 'source_kind': source_kind, 'original': original, 'canonical': canonical}


def run(value):
    return cleanup.cleanup_published_derivatives(value['job'], value['directory'], value['status'], value['plan'], source_kind=value['source_kind'])


def test_cleanup_only_removes_sealed_derivatives_and_retains_exact_originals(derived):
    value = derived
    original = value['original'].read_bytes()
    result = run(value)
    assert result['status'] == 'completed' and result['released_logical_bytes'] > 0
    assert not (value['directory']/'year.parquet').exists()
    assert not value['canonical'].exists()
    assert value['original'].read_bytes() == original
    assert (value['directory']/'raw/manual-research.json').is_file()
    assert (value['directory']/'notes.txt').is_file()
    assert (value['directory']/'bundle.json').is_file()
    receipt = json.loads((value['directory']/'derived-cleanup.json').read_text())
    assert receipt['status'] == 'completed' and all(item['status'] == 'removed' for item in receipt['files'])
    assert core.digest(receipt['scope']) == receipt['scope_sha256']
    assert receipt['scope']['accepted_sources'][0]['sha256'] == core.hash_file(value['original'])
    assert run(value)['already_completed'] is True


@pytest.mark.parametrize('state', ['paused','failed','cancelled','running'])
def test_nonpublished_or_stopped_job_never_releases_recovery_files(derived, state):
    with core.connect() as connection:
        connection.execute('UPDATE job SET status=? WHERE job_id=?', (state, derived['job']))
    assert run(derived)['status'] == 'skipped'
    assert (derived['directory']/'year.parquet').is_file()
    assert derived['canonical'].is_file()


def test_legacy_frozen_plan_and_changed_original_response_fail_closed(derived):
    original_plan = derived['plan']
    derived['plan'] = {'files': original_plan['files']}
    assert run(derived)['status'] == 'skipped'
    derived['plan'] = original_plan
    derived['original'].write_text('{"changed":"source"}')
    with pytest.raises(ValueError, match='原始响应'):
        run(derived)
    assert (derived['directory']/'year.parquet').is_file()


def test_changed_derived_sha_and_symlink_never_reach_unlink(derived):
    derived['canonical'].write_text('{"changed":"derived"}')
    with pytest.raises(ValueError, match='派生文件'):
        run(derived)
    assert (derived['directory']/'year.parquet').is_file()


def test_partial_unlink_is_resumable_with_original_frozen_scope(derived, monkeypatch):
    original_unlink = Path.unlink
    calls = [0]
    def interrupt(path, *args, **kwargs):
        if path.is_relative_to(derived['directory']):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError('controlled interruption')
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', interrupt)
        with pytest.raises(OSError):
            run(derived)
    pending = json.loads((derived['directory']/'derived-cleanup.json').read_text())
    assert pending['status'] == 'deleting'
    assert pending['files'][0]['status'] == 'removed'
    assert run(derived)['status'] == 'completed'
    assert derived['original'].is_file()


def test_crash_between_unlink_and_receipt_is_resumed_as_authorized_absence(derived, monkeypatch):
    original_atomic, writes = cleanup.atomic_json, [0]
    def interrupt(path, value):
        writes[0] += 1
        if writes[0] == 2:
            raise OSError('after unlink before checkpoint')
        return original_atomic(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(cleanup, 'atomic_json', interrupt)
        with pytest.raises(OSError, match='checkpoint'):
            run(derived)
    assert not (derived['directory']/'year.parquet').exists()
    assert run(derived)['status'] == 'completed'
    receipt = json.loads((derived['directory']/'derived-cleanup.json').read_text())
    assert receipt['files'][0]['status'] == 'absent'
    assert derived['original'].is_file()


def test_original_response_symlinks_and_forged_receipt_scope_never_authorize_deletion(derived):
    source = derived['original']
    moved = source.with_suffix('.retained')
    source.rename(moved)
    source.symlink_to(moved)
    with pytest.raises(ValueError, match='符号链接'):
        run(derived)
    assert (derived['directory']/'year.parquet').is_file()


def test_cleanup_warning_preserves_published_success_state(derived):
    derived['original'].write_text('tampered source')
    result = cleanup.release_after_publication(derived['job'], derived['directory'], derived['status'], derived['plan'], source_kind=derived['source_kind'])
    assert result['status'] == 'warning'
    with core.connect() as connection:
        assert connection.execute('SELECT status FROM job WHERE job_id=?', (derived['job'],)).fetchone()[0] == 'partial'
        assert connection.execute("SELECT COUNT(*) FROM job_event WHERE job_id=? AND stage='derived_storage' AND level='warning'", (derived['job'],)).fetchone()[0] == 1


def test_no_change_merge_does_not_materialize_another_full_history_copy(derived):
    from app.services.sync_service import merge_daily
    source = derived['directory']/'year.parquet'
    begin, end = derived['status']['data_begin'], derived['status']['data_end']
    master = [{'thscode': '600001.SH', 'list_date': '2020-01-01', 'end_date': None}]
    skipped = derived['directory']/'unchanged.parquet'
    result = merge_daily(source, str(source), skipped, begin, end, [begin], master, unchanged_rows=1)
    assert result['revised_overlap_rows'] == 0 and result['derived_file_written'] is False
    assert not skipped.exists()
    # Legacy callers keep their original materialization contract.
    legacy = derived['directory']/'legacy.parquet'
    result = merge_daily(source, str(source), legacy, begin, end, [begin], master)
    assert result['derived_file_written'] is True and legacy.is_file()


def test_storage_corruption_and_unregistered_generation_never_release_derived_source(derived):
    value = {**derived, 'status': {**derived['status'], 'snapshot_id': 'market-'+'f'*24}}
    with pytest.raises(ValueError, match='未登记'):
        run(value)
    manifest = core.published_dir()/derived['status']['snapshot_id']/'raw-daily.manifest.json'
    value = json.loads(manifest.read_text())
    object_path = core.published_dir()/value['partitions'][0]['object_key']
    object_path.write_bytes(object_path.read_bytes()+b'broken')
    with pytest.raises(ValueError, match='大小'):
        run(derived)
    assert (derived['directory']/'year.parquet').is_file()
