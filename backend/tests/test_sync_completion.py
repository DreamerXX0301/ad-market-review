"""Repeat guards use isolated SQLite; no production task or provider is touched."""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from test_platform import isolated
from app.services import sync_completion as completion, sync_clock, task_service as tasks
from app.services.increment_access import business_http_request
from app.core import connect

DAY = '2026-10-20'


def submit(kind, parameters, key):
    token = business_http_request.set(True)
    try:
        return tasks.create_task(kind, parameters, request_key=key)
    finally:
        business_http_request.reset(token)


@pytest.mark.parametrize('status', ['succeeded', 'partial', 'no_change'])
def test_published_main_is_not_redownloaded_but_next_day_is_available(isolated, monkeypatch, status):
    params = {'source_id': 'fixture', 'sync_completion_date': DAY}
    first = tasks.create_task('increment', params, request_key='first')
    tasks.finish(first['job_id'], status)
    assert completion.read_view('snapshot', DAY)['increment']['completed']
    # Lost-response retries return the original task rather than another run.
    assert submit('increment', params, 'first')['job_id'] == first['job_id']
    with pytest.raises(HTTPException) as error:
        submit('increment', params, 'repeat')
    assert error.value.status_code == 409
    monkeypatch.setattr(sync_clock, 'now_china', lambda: datetime(2026, 10, 21, 18, tzinfo=sync_clock.CHINA))
    assert not completion.read_view('snapshot', DAY)['increment']['completed']
    assert submit('increment', {**params, 'sync_completion_date': '2026-10-21'}, 'next')['job_id'] != first['job_id']


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'paused'])
def test_unsuccessful_main_remains_manually_recoverable(isolated, status):
    params = {'sync_completion_date': DAY}
    first = tasks.create_task('increment', params)
    tasks.finish(first['job_id'], status)
    assert not completion.read_view('snapshot', DAY)['increment']['completed']


def test_pre_upgrade_frozen_target_is_recognized_without_writing_on_read(isolated):
    first = tasks.create_task('increment', {})
    with connect() as db:
        tasks.append_event(db, first['job_id'], 'chain_target_frozen', 'fixture', details={'target_date': DAY})
    tasks.finish(first['job_id'], 'partial')
    with connect() as db:
        before = db.total_changes
        assert completion._increment(db, DAY)['job_id'] == first['job_id']
        assert db.total_changes == before


def supplement(snapshot='generation-a', day=DAY, **scope):
    return {'operation': 'market_supplement', 'market_snapshot_id': snapshot,
            'completion_target_date': day, 'year_history': True, 'sector_history': True,
            'recent_days': 1, **scope}


def test_supplement_receipt_requires_terminal_success_generation_day_and_covered_scope(isolated):
    params = supplement()
    first = tasks.create_task('source_check', params, target_date=DAY)
    result = {'snapshot_id': 'generation-a', 'target_date': DAY, 'quality': 'complete'}
    completion.remember_supplement(first['job_id'], result, params)
    assert not completion.read_view('generation-a', DAY)['supplement']['completed'], 'a running/unreleased receipt is insufficient'
    tasks.finish(first['job_id'])
    assert completion.read_view('generation-a', DAY)['supplement']['completed']
    with pytest.raises(HTTPException):
        submit('source_check', params, 'repeated-supplement')
    assert not completion.read_view('generation-b', DAY)['supplement']['completed']
    assert not completion.read_view('generation-a', '2026-10-21')['supplement']['completed']
    assert submit('source_check', supplement('generation-b'), 'new-generation')
    assert submit('source_check', supplement(recent_days=2), 'larger-scope')
    # An incomplete new publication keeps the explicit repair action available.
    completion.remember_supplement(first['job_id'], {**result, 'quality': 'partial'}, params)
    assert not completion.read_view('generation-a', DAY)['supplement']['completed']


def test_concurrent_repeated_submissions_cannot_bypass_completed_guard(isolated):
    params = {'sync_completion_date': DAY}
    first = tasks.create_task('increment', params)
    tasks.finish(first['job_id'])
    def attempt(key):
        with pytest.raises(HTTPException) as error:
            submit('increment', params, key)
        return error.value.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt, ['race-a', 'race-b'])) == [409, 409]
    assert tasks.list_tasks()['total'] == 1


def test_pre_upgrade_complete_supplement_is_recognized_and_request_replay_is_exact(isolated,monkeypatch):
    from app.services import market_supplement
    params={'operation':'market_supplement','year_history':True,'sector_history':True,'recent_days':1}
    first=tasks.create_task('source_check',params,target_date=DAY,request_key='legacy-supplement')
    tasks.finish(first['job_id'])
    doc={'snapshot_id':'legacy-generation','target_date':DAY,'job_id':first['job_id'],'quality':'complete'}
    monkeypatch.setattr(market_supplement,'load_supplement_status',lambda *_:doc)
    assert completion.read_view('legacy-generation',DAY)['supplement']['completed']
    with pytest.raises(HTTPException):completion.require_published_supplement_available('legacy-generation',DAY,{})
    assert completion.replay_supplement_request('legacy-supplement',params)['job_id']==first['job_id']
    with pytest.raises(HTTPException):completion.replay_supplement_request('legacy-supplement',{**params,'recent_days':2})
    assert completion._published_supplement('next-generation',DAY,{}) is None
    assert completion._published_supplement('legacy-generation','2026-10-21',{}) is None
