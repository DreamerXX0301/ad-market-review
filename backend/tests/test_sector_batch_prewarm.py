"""Worker-only prewarming preserves source receipts and user stop controls."""
from copy import deepcopy

import pytest

from app import core
from app.services import sector_chart_batch as batch, sector_strategies, task_service as tasks
from test_platform import isolated
from test_sector_chart_chain import local
from test_sector_charts import DAY


def running(local, mode='initial'):
    created = local.jobs.create_batch_job(local.market, DAY, mode=mode)
    claimed = tasks.claim_next('sector-prewarm-fixture')
    assert claimed['job_id'] == created['job_id']
    return {**claimed, 'parameters': created['parameters']}


def source_result(complete=True):
    return {'coverage': {'total_count': 2, 'available_count': 2 if complete else 1,
        'collection_complete': complete, 'complete': False, 'partial_count': 2,
        'failed_count': 0 if complete else 1, 'unrequested_count': 0},
        'quality': 'partial', 'message': '来源历史真实缺口仍保留', 'items': []}


def stub_collection(local, monkeypatch, result, calls=None):
    def collect(market, plan, adapter, guard=None, progress=None):
        guard()
        if calls is not None: calls.append(deepcopy(plan))
        if progress:
            progress('同步行业概念历史K线', 50, 'source-half', {'completed_count': 1, 'total_count': 2})
            progress('同步行业概念历史K线', 99, 'source-done', {'completed_count': 2, 'total_count': 2})
        return result
    monkeypatch.setattr(local.jobs, 'collect_sector_chart_batch', collect)


@pytest.mark.parametrize('mode', ['initial', 'increment'])
def test_initial_and_increment_prewarm_exact_frozen_codes_after_collection(local, monkeypatch, mode):
    job = running(local, mode); result = source_result(); original = deepcopy(result)
    plans = []; stub_collection(local, monkeypatch, result, plans)
    calls = []
    def prewarm(market, topic_codes, day=None, guard=None, progress=None):
        guard(); calls.append((market, topic_codes, day))
        progress(1, 2, topic_codes[0]); progress(2, 2, topic_codes[1]); guard()
        return {'date': day, 'total_count': 2, 'available_count': 2, 'failed_count': 0,
                'snapshot_id': market.state['snapshot_id'], 'contract': 'fixture', 'complete': True}
    monkeypatch.setattr(sector_strategies, 'prewarm_sector_metrics', prewarm)
    logs = []
    actual = local.jobs.execute_batch_job(job['job_id'], progress=lambda *a: logs.append(a))
    assert len(calls) == 1 and calls[0][0] is local.market
    assert calls[0][1] == [b['topic_code'] for b in job['parameters']['boards']]
    assert calls[0][2] == DAY and plans[0].get('mode', 'initial') == mode
    assert actual['metrics_prewarm']['state'] == 'completed'
    assert actual['coverage'] == original['coverage'] and actual['quality'] == original['quality']
    assert result == original and 'metrics_prewarm' not in result
    assert logs[0][1] <= 85 and logs[1][1] <= 85
    warm = [log for log in logs if log[3].get('phase') == 'metrics_prewarm']
    assert warm and min(log[1] for log in warm) >= 85 and max(log[1] for log in warm) <= 99
    assert logs[-1][1] == 100 and [log[1] for log in logs] == sorted(log[1] for log in logs)
    assert local.adapter.calls == []


def test_incomplete_collection_skips_metrics_without_changing_real_coverage(local, monkeypatch):
    job = running(local); result = source_result(False)
    stub_collection(local, monkeypatch, result)
    monkeypatch.setattr(sector_strategies, 'prewarm_sector_metrics',
        lambda *a, **k: pytest.fail('uncollected source must not prewarm'))
    actual = local.jobs.execute_batch_job(job['job_id'])
    assert actual['metrics_prewarm']['state'] == 'skipped'
    assert actual['coverage'] == result['coverage'] and local.adapter.calls == []


def test_empty_frozen_boards_skips_metrics_even_if_fixture_claims_complete(local, monkeypatch):
    job = running(local)
    with core.connect() as connection:
        import json
        params = deepcopy(job['parameters']); params['boards'] = []
        connection.execute('UPDATE job SET parameters_json=? WHERE job_id=?', (json.dumps(params), job['job_id']))
    stub_collection(local, monkeypatch, source_result())
    monkeypatch.setattr(sector_strategies, 'prewarm_sector_metrics',
        lambda *a, **k: pytest.fail('empty frozen board set must not prewarm'))
    assert local.jobs.execute_batch_job(job['job_id'])['metrics_prewarm']['state'] == 'skipped'


@pytest.mark.parametrize('error', [OSError('derived filesystem unavailable'), ValueError('derived shape invalid')])
def test_derived_failure_warns_but_preserves_successful_source_collection(local, monkeypatch, error):
    job = running(local); result = source_result(); original = deepcopy(result)
    stub_collection(local, monkeypatch, result)
    def prewarm(*args, **kwargs): raise error
    monkeypatch.setattr(sector_strategies, 'prewarm_sector_metrics', prewarm)
    logs = []
    actual = local.jobs.execute_batch_job(job['job_id'], progress=lambda *a: logs.append(a))
    assert actual['metrics_prewarm']['state'] == 'warning'
    assert actual['metrics_prewarm']['error_type'] == type(error).__name__
    assert actual['quality'] == original['quality'] and actual['coverage'] == original['coverage']
    assert result == original and logs[-1][1] == 100
    assert any(log[0] == '板块指标预热提示' for log in logs)


def test_summary_write_failure_count_warns_without_downgrading_source_coverage(local, monkeypatch):
    job = running(local); result = source_result(); original = deepcopy(result)
    stub_collection(local, monkeypatch, result)
    monkeypatch.setattr(sector_strategies, 'prewarm_sector_metrics', lambda *args, **kwargs:
        {'date': DAY, 'complete': True, 'available_count': 2, 'failed_count': 0,
         'persistent_expected_count': 2, 'persistent_available_count': 1,
         'persistent_failed_count': 1, 'persistent_failures': [{'topic_code': '885728.TI'}]})
    logs = []
    actual = local.jobs.execute_batch_job(job['job_id'], progress=lambda *a: logs.append(a))
    assert actual['metrics_prewarm']['state'] == 'warning'
    assert actual['metrics_prewarm']['persistent_failed_count'] == 1
    assert '持久' in actual['metrics_prewarm']['reason']
    assert actual['coverage'] == original['coverage'] and actual['quality'] == original['quality']
    assert logs[-1][1] == 100 and any(log[0] == '板块指标预热提示' for log in logs)


@pytest.mark.parametrize('change', ['pause', 'cancel', 'generation'])
def test_prewarm_guard_stop_is_not_swallowed_as_derived_warning(local, monkeypatch, change):
    job = running(local); stub_collection(local, monkeypatch, source_result())
    def prewarm(*args, **kwargs):
        if change == 'generation':
            local.current = deepcopy(local.market); local.current.state['snapshot_id'] = 'replaced-generation'
        else: tasks.change_task(job['job_id'], change)
        kwargs['guard']()
        raise AssertionError('guard must interrupt')
    monkeypatch.setattr(sector_strategies, 'prewarm_sector_metrics', prewarm)
    expected = tasks.TaskPaused if change == 'pause' else tasks.TaskCancelled if change == 'cancel' else ValueError
    logs = []
    with pytest.raises(expected):
        local.jobs.execute_batch_job(job['job_id'], progress=lambda *a: logs.append(a))
    assert not any(log[1] == 100 for log in logs) and local.adapter.calls == []


def test_fixture_without_source_cache_stays_unknown_and_never_fetches_for_prewarm(local, monkeypatch):
    job = running(local); stub_collection(local, monkeypatch, source_result())
    actual = local.jobs.execute_batch_job(job['job_id'])
    assert actual['metrics_prewarm']['state'] == 'partial'
    assert actual['metrics_prewarm']['available_count'] == 0
    assert actual['metrics_prewarm']['failed_count'] == 2
    assert actual['coverage']['collection_complete'] is True
    assert local.adapter.calls == []


def test_real_cached_initial_and_noop_increment_do_not_rewrite_source_receipt_or_refetch(local, monkeypatch):
    monkeypatch.setattr(batch, 'REQUEST_INTERVAL', 0)
    initial = running(local)
    original = sector_strategies.prewarm_sector_metrics; source_receipts = []
    def checked_prewarm(*args, **kwargs):
        mode = 'increment' if len(source_receipts) else 'initial'
        capability = 'sector-chart-increment-batch' if mode == 'increment' else 'sector-chart-batch'
        path = batch._root()/'accepted/fuyao'/capability/(DAY+'.json')
        before = path.read_bytes(); result = original(*args, **kwargs)
        assert path.read_bytes() == before
        source_receipts.append(before)
        return result
    monkeypatch.setattr(sector_strategies, 'prewarm_sector_metrics', checked_prewarm)
    first = local.jobs.execute_batch_job(initial['job_id'])
    assert first['metrics_prewarm']['complete'] is True
    tasks.finish(initial['job_id'], 'partial' if first['quality'] == 'partial' else 'succeeded')
    calls = len(local.adapter.calls)
    second = running(local, 'increment')
    after = local.jobs.execute_batch_job(second['job_id'])
    assert after['metrics_prewarm']['complete'] is True
    assert len(local.adapter.calls) == calls
    assert after['coverage']['request_count'] == 0 and len(source_receipts) == 2
