"""Isolated HTTP/queue/worker integration; no provider or production data access."""
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app import core, worker
from app.services import market_service, sector_chart_jobs as jobs
from app.services import sector_charts as charts, source_service
from app.services import task_service as tasks
from test_sector_charts import Adapter, CODE, DAY, Market


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path/'data'))
    for variable in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(variable, raising=False)
    core.clear_market_cache(); core.ensure_ready()
    market = Market(start='2026-07-01')
    market.state['ready'] = True
    adapter = Adapter(market); adapter.key = 'isolated-stub-not-a-real-key'
    local = SimpleNamespace(market=market, current=market, adapter=adapter,
                            client_calls=[], root=tmp_path/'data')
    monkeypatch.setattr(jobs, 'get_market', lambda: local.current)
    monkeypatch.setattr(market_service, 'get_market', lambda: local.current)
    def client(source='fuyao'):
        local.client_calls.append(source)
        assert source == 'fuyao'
        return adapter
    monkeypatch.setattr(source_service, 'client', client)
    def forbidden_network(*args, **kwargs):
        raise AssertionError('Integration fixtures may not request a real provider')
    monkeypatch.setattr(source_service.ProviderClient, 'request', forbidden_network)
    yield local
    core.clear_market_cache()


@pytest.fixture
def http(local):
    from app.main import app
    with TestClient(app) as client:
        yield client


def queued(local, code=CODE, day=DAY, request_key=None):
    return jobs.create_chart_job(local.market, code, day, request_key)


def running(local, code=CODE):
    result = queued(local, code)
    claimed = tasks.claim_next('sector-chart-fixture-worker')
    assert claimed['job_id'] == result['job_id']
    return claimed


def cache(local, code=CODE):
    plan = charts.plan_sector_chart(local.market, code, DAY)
    return charts.collect_sector_chart(local.market, plan, local.adapter)


def test_get_literal_route_precedes_catchall_and_never_creates_source_or_task(http, local):
    from app.routers.platform import router
    paths = [route.path for route in router.routes]
    assert paths.index('/api/v1/market/sector-chart') < paths.index('/api/v1/market/{kind}')
    result = http.get('/api/v1/market/sector-chart', params={'topic_code': CODE, 'date': DAY})
    assert result.status_code == 200
    assert not result.json()['available'] and result.json()['bars'] == []
    assert result.json()['task'] is None
    assert local.client_calls == [] and local.adapter.calls == []
    assert tasks.list_tasks()['total'] == 0
    assert not (local.root/'published/market-supplement').exists()


def test_post_freezes_source_window_and_identity_without_downloading(http, local):
    response = http.post('/api/v1/market/sector-chart/jobs', json={'topic_code': CODE.lower(), 'date': DAY})
    assert response.status_code == 200
    result = response.json(); plan = charts.plan_sector_chart(local.market, CODE, DAY)
    assert result['kind'] == 'source_check' and result['status'] == 'queued'
    assert result['target_date'] == DAY
    assert result['parameters'] == {'operation': 'sector_chart', 'source_id': 'fuyao', **plan}
    assert local.adapter.calls == []


def test_same_scope_deduplicates_while_boards_and_dates_remain_separate(local):
    first = queued(local, request_key='first')
    assert queued(local, request_key='second')['job_id'] == first['job_id']
    other_board = queued(local, code='881101.TI')
    other_day = queued(local, day='2026-10-19')
    assert len({first['job_id'], other_board['job_id'], other_day['job_id']}) == 3
    assert len({item['parameters']['request_signature'] for item in (first, other_board, other_day)}) == 3
    assert jobs.chart_view(local.market, CODE, DAY)['task']['job_id'] == first['job_id']
    assert jobs.chart_view(local.market, '881101.TI', DAY)['task']['job_id'] == other_board['job_id']


def test_same_request_key_cannot_be_reused_for_another_board(local):
    queued(local, request_key='one-scope')
    with pytest.raises(HTTPException) as caught:
        queued(local, code='881101.TI', request_key='one-scope')
    assert caught.value.status_code == 409 and tasks.list_tasks()['total'] == 1


def test_task_lookup_does_not_mix_window_version_for_same_code_date_snapshot(local):
    first = queued(local)
    old_begin = local.market.state['data_begin']
    local.market.state['data_begin'] = '2026-08-03'
    second = queued(local)
    assert first['job_id'] != second['job_id']
    assert first['parameters']['request_signature'] != second['parameters']['request_signature']
    assert jobs.chart_view(local.market, CODE, DAY)['task']['job_id'] == second['job_id']
    local.market.state['data_begin'] = old_begin
    assert jobs.chart_view(local.market, CODE, DAY)['task']['job_id'] == first['job_id']


def test_task_lookup_does_not_attach_same_signature_from_another_source(local):
    plan = charts.plan_sector_chart(local.market, CODE, DAY)
    tasks.create_task('source_check', {'operation': 'sector_chart', 'source_id': 'other',
                                     **plan, 'source': 'other'}, DAY)
    assert jobs.chart_view(local.market, CODE, DAY)['task'] is None


@pytest.mark.parametrize('code', [CODE, '881101.TI'])
def test_cached_get_and_post_are_offline_and_do_not_enqueue(http, local, code):
    accepted = cache(local, code)
    calls = len(local.adapter.calls)
    for period in ('D', 'W', 'M'):
        response = http.get('/api/v1/market/sector-chart', params={'topic_code': code, 'date': DAY, 'period': period})
        assert response.status_code == 200 and response.json()['available']
        result = response.json()
        assert result['raw_sha256'] == accepted['record']['raw_sha256']
        lines = result['automatic_trendlines']
        assert (lines['instrument_id'], lines['period'], lines['price_basis']) == (code, period, 'none')
        assert lines['snapshot_id'] == result['snapshot_id'] == local.market.status()['snapshot_id']
        assert lines['as_of'] == DAY
        if period == 'D':
            assert lines['items']
        if period == 'M':
            assert not lines['items'] and lines['reason']
    response = http.post('/api/v1/market/sector-chart/jobs', json={'topic_code': code, 'date': DAY})
    assert response.status_code == 200 and response.json()['cache_hit'] is True
    assert tasks.list_tasks()['total'] == 0 and local.client_calls == []
    assert len(local.adapter.calls) == calls


def test_no_key_refused_before_any_queue_write(http, local):
    local.adapter.key = None
    response = http.post('/api/v1/market/sector-chart/jobs', json={'topic_code': CODE, 'date': DAY})
    assert response.status_code == 422 and 'Key' in response.json()['detail']
    assert tasks.list_tasks()['total'] == 0 and local.adapter.calls == []


@pytest.mark.parametrize('patch', [
    {'date': {}}, {'date': []}, {'date': 20261020},
    {'request_key': {}}, {'request_key': []}, {'request_key': 12},
    {'request_key': ''}, {'request_key': 'x'*201},
    {'topic_code': {}}, {'topic_code': None}, {'unsupported': True},
])
def test_http_invalid_payload_types_are_422_without_tasks(http, local, patch):
    response = http.post('/api/v1/market/sector-chart/jobs', json={'topic_code': CODE, 'date': DAY, **patch})
    assert response.status_code == 422
    assert tasks.list_tasks()['total'] == 0 and local.client_calls == []


@pytest.mark.parametrize('params', [
    {'topic_code': CODE, 'date': '2026-10-21'},
    {'topic_code': CODE, 'date': '2026-10-18'},
    {'topic_code': CODE, 'date': DAY, 'period': '1m'},
    {'topic_code': 'BK1234', 'date': DAY},
])
def test_get_invalid_scope_never_fetches(http, local, params):
    assert http.get('/api/v1/market/sector-chart', params=params).status_code == 422
    assert local.client_calls == [] and tasks.list_tasks()['total'] == 0


def test_worker_dispatch_collects_only_frozen_board_and_finishes(local):
    job = running(local, '881101.TI')
    worker.dispatch(job)
    result = tasks.task_view(job['job_id'])
    assert result['status'] == 'succeeded' and result['progress'] == 100
    assert len(local.adapter.calls) == 1 and local.adapter.calls[0][1]['thscode'] == '881101.TI'
    assert charts.read_sector_chart(local.market, '881101.TI', DAY)['available']
    assert not charts.read_sector_chart(local.market, CODE, DAY)['available']
    assert tasks.list_tasks()['total'] == 1
    assert any(event['stage'] == '板块K线已保存' for event in result['logs'])
    worker.dispatch(job)
    assert len(local.adapter.calls) == 1


def test_worker_partial_quality_is_not_reported_complete(local):
    local.adapter.transform = lambda payload: {**payload, 'data': {**payload['data'], 'item': payload['data']['item'][1:]}}
    job = running(local)
    worker.dispatch(job)
    assert tasks.task_view(job['job_id'])['status'] == 'partial'
    chart = charts.read_sector_chart(local.market, CODE, DAY)
    assert chart['available'] and chart['coverage']['missing_count'] == 1


@pytest.mark.parametrize(('action', 'exception'), [('pause', tasks.TaskPaused), ('cancel', tasks.TaskCancelled)])
def test_stopped_job_does_not_even_construct_provider(local, action, exception):
    job = running(local)
    tasks.change_task(job['job_id'], action)
    local.client_calls.clear()
    with pytest.raises(exception): jobs.execute_chart_job(job['job_id'])
    assert local.client_calls == [] and local.adapter.calls == []
    assert not (local.root/'published/market-supplement').exists()


def test_queued_job_cannot_execute_before_worker_claim(local):
    job = queued(local); local.client_calls.clear()
    with pytest.raises(ValueError, match='未运行'): jobs.execute_chart_job(job['job_id'])
    assert local.client_calls == [] and local.adapter.calls == []


def test_changed_generation_before_download_refuses_without_provider(local):
    job = running(local)
    local.current = deepcopy(local.market)
    local.current.state['snapshot_id'] = 'market-next'
    local.client_calls.clear()
    with pytest.raises(ValueError, match='代际已变化'): jobs.execute_chart_job(job['job_id'])
    assert local.client_calls == [] and local.adapter.calls == []


@pytest.mark.parametrize('action', ['pause', 'cancel', 'replace_generation'])
def test_state_changes_during_download_do_not_accept_cache(local, monkeypatch, action):
    job = running(local)
    request = local.adapter.request
    def interrupted(path, params):
        payload = request(path, params)
        if action == 'replace_generation':
            local.current = deepcopy(local.market)
            local.current.state['snapshot_id'] = 'market-next'
        else:
            tasks.change_task(job['job_id'], action)
        return payload
    monkeypatch.setattr(local.adapter, 'request', interrupted)
    expected = ValueError if action == 'replace_generation' else tasks.TaskPaused if action == 'pause' else tasks.TaskCancelled
    with pytest.raises(expected): jobs.execute_chart_job(job['job_id'])
    assert len(local.adapter.calls) == 1
    assert not (local.root/'published/market-supplement/accepted').exists()


def test_forged_frozen_window_is_rejected_before_downloading(local):
    job = running(local)
    with core.connect() as connection:
        params = json.loads(job['parameters_json']); params['start'] = '2020-01-01'
        connection.execute('UPDATE job SET parameters_json=? WHERE job_id=?', (json.dumps(params), job['job_id']))
    with pytest.raises(ValueError, match='冻结计划'): jobs.execute_chart_job(job['job_id'])
    assert local.adapter.calls == []


def test_non_chart_source_task_cannot_use_chart_executor(local):
    job = tasks.create_task('source_check', {'operation': 'market_supplement'}, DAY)
    with pytest.raises(ValueError, match='不是指定板块'): jobs.execute_chart_job(job['job_id'])
    assert local.client_calls == [] and local.adapter.calls == []
