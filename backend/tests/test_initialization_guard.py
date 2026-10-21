"""Main-data publication protects initialization independently of auxiliary jobs."""
import pytest
from fastapi import HTTPException
from app import core
from app.services import task_service as tasks


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    core.ensure_ready()
    return tmp_path


def publish_main(job_id, module='daily_raw'):
    path = core.published_dir() / 'fixture.parquet'
    path.write_bytes(b'isolated-publication-fixture')
    return core.publish_snapshot(module=module, job_id=job_id, start='2023-10-20', end='2026-10-20',
                                 artifacts=[{'object_key': path.name, 'role': module, 'sha256': core.hash_file(path), 'byte_count': path.stat().st_size, 'row_count': 1}],
                                 quality='partial', price_basis='none')


def test_published_main_disables_new_init_even_when_initial_task_failed_or_auxiliary_partial(database):
    initial = tasks.create_task('initialise', {'window': 'near-three-years'}, request_key='original')
    snapshot = publish_main(initial['job_id'])
    tasks.finish(initial['job_id'], 'failed', '主行情已发布，随后辅助阶段失败')
    state = tasks.initialization_state()
    assert state['main_published'] and not state['can_initialize']
    assert state['published_snapshot_id'] == snapshot and state['data_end'] == '2026-10-20'
    for create in (lambda: tasks.create_task('initialise', {}), lambda: tasks.change_task(initial['job_id'], 'retry')):
        with pytest.raises(HTTPException) as error: create()
        assert error.value.status_code == 409
    assert tasks.create_task('increment', {})['status'] == 'queued'
    assert tasks.create_task('source_check', {'operation': 'market_supplement'})['status'] == 'queued'


def test_same_request_replay_is_read_only_after_publication(database):
    initial = tasks.create_task('initialise', {}, request_key='same')
    publish_main(initial['job_id']); tasks.finish(initial['job_id'], 'partial')
    replay = tasks.create_task('initialise', {}, request_key='same')
    assert replay['job_id'] == initial['job_id'] and replay['status'] == 'partial'
    assert tasks.list_tasks()['total'] == 1


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_failed_or_cancelled_empty_initialization_can_be_explicitly_retried(database, status):
    initial = tasks.create_task('initialise', {})
    tasks.finish(initial['job_id'], status)
    assert tasks.initialization_state()['can_initialize']
    retry = tasks.change_task(initial['job_id'], 'retry')
    assert retry['retry_of_job_id'] == initial['job_id'] and retry['status'] == 'queued'


def test_paused_initialization_cannot_resume_over_an_already_published_main(database):
    initial = tasks.create_task('initialise', {})
    tasks.change_task(initial['job_id'], 'pause')
    writer = tasks.create_task('increment', {})
    publish_main(writer['job_id'])
    with pytest.raises(HTTPException) as error: tasks.change_task(initial['job_id'], 'resume')
    assert error.value.status_code == 409
    assert tasks.task_view(initial['job_id'])['status'] == 'paused'


def test_different_initialization_parameters_cannot_queue_two_initializations(database):
    first = tasks.create_task('initialise', {'window': 'near-three-years'})
    same = tasks.create_task('initialise', {'window': 'near-three-years'})
    assert same['job_id'] == first['job_id']
    with pytest.raises(HTTPException) as error: tasks.create_task('initialise', {'window': 'near-one-year'})
    assert error.value.status_code == 409 and tasks.list_tasks()['total'] == 1


def test_non_main_publication_does_not_prevent_first_main_import(database):
    topic = tasks.create_task('topic_refresh', {})
    publish_main(topic['job_id'], module='topics')
    assert tasks.initialization_state()['can_initialize']
    assert tasks.create_task('initialise', {})['status'] == 'queued'


def test_legacy_queued_initialization_cannot_overwrite_published_data_or_block_ordinary_work(database):
    initial = tasks.create_task('initialise', {})
    writer = tasks.create_task('increment', {})
    publish_main(writer['job_id'])
    tasks.finish(writer['job_id'], 'partial')
    child = tasks.create_task('source_check', {'operation': 'market_supplement'})
    assert tasks.claim_next('fixture-worker')['job_id'] == child['job_id']
    blocked = tasks.task_view(initial['job_id'])
    assert blocked['status'] == 'failed' and blocked['phase'] == 'initialization_blocked'
    assert tasks.initialization_state()['main_published']


def test_empty_paused_initialization_can_resume(database):
    initial = tasks.create_task('initialise', {})
    tasks.change_task(initial['job_id'], 'pause')
    assert tasks.change_task(initial['job_id'], 'resume')['status'] == 'queued'
