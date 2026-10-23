"""Exact offline GC, reference roots and recovery ledger; all inputs are fixtures."""
import json
from pathlib import Path
import time

import duckdb
import pytest

from app import core
from app.services import market_storage_gc as gc,task_service as tasks
from app.services.publication_validation import authorized_removed_files


@pytest.fixture
def archive(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path));monkeypatch.delenv('SHIXU_SQLITE_PATH',raising=False);monkeypatch.delenv('SHIXU_PUBLISHED_DIR',raising=False)
    core.clear_market_cache();job=tasks.create_task('initialise',{'source':'fixture'})['job_id']
    with core.connect() as connection:connection.execute("UPDATE job SET status='partial' WHERE job_id=?",(job,))
    identifiers=[]
    for number in range(3):
        generation='market-'+str(number+1)*24;folder=tmp_path/'published'/generation;folder.mkdir()
        path=folder/'daily.parquet';con=duckdb.connect(':memory:')
        try:con.execute('COPY (SELECT ? AS close) TO ? (FORMAT PARQUET)',[str(path),10.+number])
        finally:con.close()
        (folder/'reference.json').write_text('{}')
        files=[{'role':item.name,'path':'published/'+generation+'/'+item.name,'sha256':core.hash_file(item),'bytes':item.stat().st_size} for item in (path,folder/'reference.json')]
        (folder/'manifest.json').write_text(json.dumps({'summary':{'snapshot_id':generation,'files':files}}))
        sid=core.publish_snapshot(module='analysis_bars',job_id=job,start='2026-10-23',end='2026-10-23',artifacts=[{'object_key':generation+'/daily.parquet','sha256':core.hash_file(path),'byte_count':path.stat().st_size,'role':'daily.parquet'}],lineage={'market_snapshot_id':generation},snapshot_id='analysis-'+str(number),activate=True)
        identifiers.append((sid,generation));time.sleep(.003)
    (tmp_path/'published/market-head.json').write_text(json.dumps({'snapshot_id':identifiers[-1][1]}))
    return tmp_path,identifiers


def test_gc_protects_pins_arbitrary_business_origins_and_shared_heads(archive):
    root,ids=archive
    with core.connect() as connection:
        plan=gc.preview(connection,root/'published',keep_latest=1)
        assert plan['candidate_generations']==2
        connection.execute('CREATE TABLE extra_evidence(id TEXT,origin_json TEXT)')
        connection.execute('INSERT INTO extra_evidence VALUES(?,?)',('saved-auto-line',json.dumps({'snapshot_id':ids[0][1]})))
        connection.execute('INSERT INTO retention_pin VALUES(?,?,?,?,?,NULL)',('manual',ids[1][0],'manual','keep',core.now_ms()))
    with core.connect() as connection:
        assert gc.preview(connection,root/'published',keep_latest=1)['candidate_generations']==0
        with pytest.raises(ValueError,match='离线'):gc.collect(connection,root/'published',plan)
        with pytest.raises(ValueError,match='变化'):gc.collect(connection,root/'published',plan,offline=True)


def test_gc_seals_exact_unlinks_and_generic_validate_accepts_only_ledger(archive):
    root,ids=archive
    with core.connect() as connection:plan=gc.preview(connection,root/'published',keep_latest=1)
    before=sum(path.stat().st_size for path in (root/'published').rglob('*') if path.is_file())
    with core.connect() as connection:result=gc.collect(connection,root/'published',plan,offline=True)
    after=sum(path.stat().st_size for path in (root/'published').rglob('*') if path.is_file())
    assert result['status']=='completed' and before-after==plan['logical_bytes']
    assert not (root/'published'/ids[0][1]).exists() and (root/'published'/ids[-1][1]).is_dir()
    with core.connect() as connection:
        accepted=authorized_removed_files(connection)
        assert (ids[0][0],ids[0][1]+'/daily.parquet') in accepted
        assert gc.collect(connection,root/'published',plan,offline=True,collection_id=result['collection_id'])['already_completed']
        with pytest.raises(Exception,match='immutable'):connection.execute("UPDATE market_storage_collection SET scope_sha256=? WHERE collection_id=?",('0'*64,result['collection_id']))
    from app.cli import validate
    assert validate()['ok']


def test_gc_refuses_unregistered_file_or_changed_input(archive):
    root,ids=archive
    unknown=root/'published'/ids[0][1]/'untracked.json';unknown.write_text('{}')
    with core.connect() as connection:
        with pytest.raises(ValueError,match='未登记'):gc.preview(connection,root/'published',keep_latest=1)
    unknown.unlink()
    with core.connect() as connection:plan=gc.preview(connection,root/'published',keep_latest=1)
    target=root/'published'/ids[0][1]/'daily.parquet';target.write_bytes(target.read_bytes()+b'changed')
    with core.connect() as connection:
        with pytest.raises(ValueError,match='SHA'):gc.collect(connection,root/'published',plan,offline=True)


def test_gc_audit_previous_generation_is_not_a_file_dependency_but_real_inputs_are(archive):
    root, ids = archive
    with core.connect() as connection:
        job = connection.execute('SELECT job_id FROM job LIMIT 1').fetchone()[0]
        artifact = dict(connection.execute('SELECT * FROM snapshot_artifact WHERE snapshot_id=?', (ids[-1][0],)).fetchone())
    # A complete newly published generation references its predecessor in the
    # calendar provenance for audit, not as a reader dependency on old files.
    core.publish_snapshot(module='calendar', job_id=job, start='2026-10-23', end='2026-10-23',
                          artifacts=[artifact], snapshot_id='calendar-audit-link',
                          lineage={'market_snapshot_id': ids[-1][1], 'coverage': {
                              'calendar_provenance': {'recent_extension': {'previous_generation': ids[0][1]}}}}, activate=True)
    with core.connect() as connection:
        plan = gc.preview(connection, root/'published', keep_latest=1)
        assert plan['candidate_generations'] == 2
        assert ids[0][1] in plan['scope']['generations']
    # An actual derived-view input still protects the exact older snapshot.
    core.publish_snapshot(module='indicators', job_id=job, start='2026-10-23', end='2026-10-23',
                          artifacts=[artifact], snapshot_id='indicators-real-input',
                          lineage={'market_snapshot_id': ids[-1][1], 'input_snapshots': {'analysis_bars': ids[0][0]}}, activate=True)
    with core.connect() as connection:
        plan = gc.preview(connection, root/'published', keep_latest=1)
        assert plan['candidate_generations'] == 1
        assert ids[0][1] not in plan['scope']['generations']


@pytest.mark.parametrize('reference_kind', ['saved_origin', 'derived_input'])
def test_gc_resume_refuses_new_business_reference_after_an_interrupted_unlink(archive, monkeypatch, reference_kind):
    root, ids = archive
    with core.connect() as connection:
        plan = gc.preview(connection, root/'published', keep_latest=1)
    original, calls = Path.unlink, [0]
    def interrupt(path, *args, **kwargs):
        if path.is_relative_to(root/'published'):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError('controlled power interruption')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(Path, 'unlink', interrupt)
        with core.connect() as connection:
            with pytest.raises(OSError, match='interruption'):
                gc.collect(connection, root/'published', plan, offline=True)
    with core.connect() as connection:
        collection = connection.execute("SELECT collection_id FROM market_storage_collection WHERE status='deleting'").fetchone()[0]
        assert connection.execute("SELECT COUNT(*) FROM market_storage_collection_file WHERE status='removed'").fetchone()[0] == 1
        # Model a service restart and newly saved provenance before maintenance
        # is resumed. A frozen old plan is no longer sufficient authorization.
        if reference_kind == 'saved_origin':
            connection.execute('CREATE TABLE newly_saved_evidence(id TEXT,origin_json TEXT)')
            connection.execute('INSERT INTO newly_saved_evidence VALUES(?,?)', ('new-reference', json.dumps({'snapshot_id': ids[0][1]})))
        else:
            job = connection.execute('SELECT job_id FROM job LIMIT 1').fetchone()[0]
            artifact = dict(connection.execute('SELECT * FROM snapshot_artifact WHERE snapshot_id=?', (ids[-1][0],)).fetchone())
    if reference_kind == 'derived_input':
        core.publish_snapshot(module='indicators', job_id=job, start='2026-10-23', end='2026-10-23',
                              artifacts=[artifact], snapshot_id='new-derived-live-input',
                              lineage={'market_snapshot_id': ids[-1][1], 'input_snapshots': {'analysis_bars': ids[0][0]}}, activate=True)
    remaining = {str(path): core.hash_file(path) for path in (root/'published').rglob('*') if path.is_file()}
    with core.connect() as connection:
        with pytest.raises(ValueError, match='业务引用'):
            gc.collect(connection, root/'published', plan, offline=True, collection_id=collection)
        assert not authorized_removed_files(connection)
    assert {str(path): core.hash_file(path) for path in (root/'published').rglob('*') if path.is_file()} == remaining


@pytest.mark.parametrize('kind,checkpoint_name', [
    ('increment', 'sync'), ('initialise', 'sync'), ('topic_init', 'topics'), ('topic_refresh', 'topics'),
])
def test_gc_protects_exact_paused_filesystem_checkpoint_without_scanning_raw_tree(archive, monkeypatch, kind, checkpoint_name):
    root, ids = archive
    monkeypatch.setenv('SHIXU_RAW_DIR', str(root/'raw'))
    monkeypatch.setenv('SHIXU_WORK_DIR', str(root/'work'))
    # Initialisation is normally forbidden once market data exists. The
    # controlled paused job models a pre-upgrade already-authorized request.
    parameters = '{}'
    job = 'paused-' + kind
    with core.connect() as connection:
        connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES(?,?,?,?,?,?,'paused',?)",
                           (job, job, job, kind, parameters, core.digest({}), core.now_ms()))
    if checkpoint_name == 'sync':
        checkpoint = root/'raw/sync'/job/'checkpoint.json'
        value = {'version': 1, 'plan': {'previous_generation': ids[0][1]}, 'files': {}}
    else:
        checkpoint = root/'work'/job/'topic-plan.json'
        value = {'snapshot_id': ids[0][1]}
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps(value))
    with core.connect() as connection:
        plan = gc.preview(connection, root/'published', keep_latest=1)
        assert ids[0][1] not in plan['scope']['generations']
        assert ids[1][1] in plan['scope']['generations']
    checkpoint.unlink()
    with core.connect() as connection:
        assert gc.preview(connection, root/'published', keep_latest=1)['candidate_generations'] == 0
    checkpoint.write_text('{invalid-json')
    with core.connect() as connection:
        assert gc.preview(connection, root/'published', keep_latest=1)['candidate_generations'] == 0
