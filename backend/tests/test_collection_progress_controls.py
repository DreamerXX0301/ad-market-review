"""Synthetic receipts only: no provider and no runtime database."""
import pytest
from fastapi import HTTPException

from app.core import connect
from app.routers.platform import task_action, sync_session_control
from app.services import task_service as tasks, sync_progress
from app.services.collection_controls import estimate
from test_platform import isolated


@pytest.mark.parametrize('kind,parameters', [('increment', {}),
    ('source_check', {'operation': 'noon_snapshot'})])
@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_collection_http_stop_rejected_without_mutation(isolated, kind, parameters, action):
    task = tasks.create_task(kind, parameters)
    with connect() as c:
        before = c.execute('SELECT count(*) FROM job_event').fetchone()[0]
    with pytest.raises(HTTPException) as error:
        task_action(task['job_id'], action)
    assert error.value.status_code == 409
    assert tasks.task_view(task['job_id'])['status'] == 'queued'
    assert tasks.task_view(task['job_id'])['interactive_stop_allowed'] is False
    with connect() as c:
        assert before == c.execute('SELECT count(*) FROM job_event').fetchone()[0]


def test_increment_legacy_inline_selection_is_visible_not_waiting(isolated):
    root = tasks.create_task('increment', {})
    tasks.finish(root['job_id'], 'partial')
    with connect() as c:
        tasks.append_event(c, root['job_id'], 'chain_authorized', 'frozen')
    child = tasks.create_task('selection', {'strategy_id': 'strategy-a'}, request_key=root['job_id'] + ':selection:strategy-a')
    tasks.claim_next('test-worker')
    view = sync_progress.session(task_id=root['job_id'])
    assert view['status'] == 'running' and not view['can_acknowledge']
    assert view['workspace']['state'] == 'running'
    assert '策略筛选' in view['message'] and '等待' not in view['message']
    assert [row['job_id'] for row in view['tasks'] if row.get('workspace_child')] == [child['job_id']]
    with pytest.raises(HTTPException):
        task_action(child['job_id'], 'cancel')
    tasks.finish(child['job_id'])
    with connect() as c:
        tasks.append_event(c, root['job_id'], 'workspace_followup', 'done', details={'results': [
            {'strategy_id': 'strategy-a', 'name': '合成策略', 'matched': 0, 'unknown': 2, 'added_count': 0},
            {'tracking_review': {'reviewed': 0, 'skipped': 2}}]})
    view = tasks.task_view(root['job_id'])['workspace']
    assert view['completed_strategies'] == 1
    assert view['strategies'][0]['matched'] == 0 and view['tracking']['reviewed'] == 0


def test_unrelated_and_mismatched_jobs_never_join_increment(isolated):
    root = tasks.create_task('increment', {})
    tasks.finish(root['job_id'])
    tasks.create_task('selection', {'strategy_id': 'a'}, request_key='other:selection:a')
    tasks.create_task('selection', {'strategy_id': 'b'}, request_key=root['job_id'] + ':selection:wrong')
    assert len(sync_progress.session(task_id=root['job_id'])['tasks']) == 1


def test_legacy_stop_state_and_standalone_strategy_controls_remain(isolated):
    root = tasks.create_task('increment', {})
    tasks.change_task(root['job_id'], 'pause')  # Internal recovery/audit path.
    assert sync_progress.session(task_id=root['job_id'])['status'] == 'paused'
    standalone = tasks.create_task('selection', {'strategy_id': 'a'}, request_key='manual-a')
    assert task_action(standalone['job_id'], 'cancel')['status'] == 'cancelled'
    with pytest.raises(HTTPException) as error:
        sync_session_control({'task_id': root['job_id'], 'action': 'cancel'})
    assert error.value.status_code == 409


def test_estimate_uses_only_same_operation_completed_history(isolated):
    current = tasks.create_task('source_check', {'operation': 'noon_snapshot'}, request_key='current')
    with connect() as c:
        c.execute('UPDATE job SET created_at_ms=100000 WHERE job_id=?', (current['job_id'],))
    for i, elapsed in enumerate([5000, 7000, 9000]):
        old = tasks.create_task('source_check', {'operation': 'noon_snapshot', 'sample': i}, request_key=f'old-{i}')
        tasks.finish(old['job_id'])
        with connect() as c:
            c.execute('UPDATE job SET created_at_ms=?,finished_at_ms=? WHERE job_id=?', (1000+i,1000+i+elapsed,old['job_id']))
    with connect() as c:
        row = c.execute('SELECT * FROM job WHERE job_id=?', (current['job_id'],)).fetchone()
        result = estimate(c, row, 102000)
        assert result['sample_count'] == 3
        assert result['remaining_min_ms'] == 3000 and result['remaining_max_ms'] == 7000
        assert estimate(c, row, 120000)['remaining_max_ms'] is None
