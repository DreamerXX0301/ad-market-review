"""Disposable board projections reuse work, never immutable-source trust."""
from copy import deepcopy
import json

import pytest

from app import core
from app.services import sector_charts as charts, sector_chart_batch as batch
from app.services import sector_history as history, sector_strategies as strategies
from app.services import sector_read_context as context, task_service as tasks
from test_platform import isolated
from test_sector_strategies import workspace, cached
from test_sector_chart_chain import local
from test_sector_batch_prewarm import running, source_result
from test_sector_charts import CODE, DAY


def verified(workspace):
    cached(workspace)
    plan = charts.plan_sector_chart(workspace.market, CODE, DAY)
    value = charts.collect_sector_chart(workspace.market, plan, workspace.adapter)
    return plan, value


def topic(workspace):
    return history._catalog(workspace.market.reference())[0][CODE]


def test_verified_read_has_exact_original_source_refs_and_public_metrics(workspace):
    plan, value = verified(workspace)
    expected = strategies.metrics_for_sector(workspace.market, CODE, DAY, strategies.PERIODS)
    record, pointer, proofs = charts.verified_collected_input(value, plan, workspace.market.calendar())
    assert proofs == strategies._proofs(pointer, CODE, DAY)
    with strategies._SUMMARY_LOCK: strategies._SUMMARIES.clear()
    result = strategies.prewarm_collected_sector(workspace.market, plan, value)
    assert result['available'] and result['persistent']
    assert strategies.metrics_for_sector(workspace.market, CODE, DAY, strategies.PERIODS) == expected
    assert set(value) == {'available', 'record', 'cache_hit', 'artifact', 'sha256', 'coverage', 'quality', 'message'}
    assert 'verified' not in json.loads(json.dumps(value))
    assert record is value['record']


@pytest.mark.parametrize('part', ['record', 'calendar', 'raw', 'pointer', 'pointer_value'])
def test_handoff_rehashes_actual_source_and_rejects_changed_inputs(workspace, part):
    plan, value = verified(workspace); calendar = workspace.market.calendar()
    if part == 'record': value['record']['bars'][-1]['close'] += 1
    elif part == 'calendar': calendar.pop(0)
    elif part == 'raw': (charts._root()/value['record']['raw_artifact']).write_text('{}')
    elif part == 'pointer_value': value.verified[1]['artifact'] = 'forged.json'
    else:
        path = charts._root()/'accepted/fuyao/sector-chart'/(DAY+'-'+CODE+'.json')
        path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError): charts.verified_collected_input(value, plan, calendar)


def test_private_pair_equals_original_full_replay_and_survives_process_cache_clear(workspace, monkeypatch):
    plan, value = verified(workspace); previous = workspace.market.days[-2]
    expected = history.full_sector_pair_from_cache(workspace.market, topic(workspace), DAY, previous)
    strategies.prewarm_collected_sector(workspace.market, plan, value)
    with strategies._SUMMARY_LOCK: strategies._SUMMARIES.clear()
    monkeypatch.setattr(charts, '_read_cached', lambda *a, **k: pytest.fail('pair must not replay full history'))
    actual = history.full_sector_pair_from_cache(workspace.market, topic(workspace), DAY, previous)
    assert actual == expected
    assert set(actual) == set(expected)


def test_increment_handoff_keeps_complete_lineage_proof_and_exact_pair(workspace, monkeypatch):
    cached(workspace)
    target = '2026-10-21'; workspace.market.days.append(target)
    workspace.market.state['data_end'] = target
    monkeypatch.setattr(charts, '_source_today', lambda: target)
    plan = charts.plan_sector_chart(workspace.market, CODE, target)
    value = charts.collect_sector_chart_incremental(workspace.market, plan, workspace.adapter)
    assert value['request_kind'] == 'tail' and value['downloaded_rows'] == 1
    _, pointer, proofs = charts.verified_collected_input(value, plan, workspace.market.calendar())
    assert proofs == strategies._proofs(pointer, CODE, target)
    expected = history.full_sector_pair_from_cache(workspace.market, topic(workspace), target, DAY)
    strategies.prewarm_collected_sector(workspace.market, plan, value)
    monkeypatch.setattr(charts, '_read_cached', lambda *a, **k: pytest.fail('no second full replay'))
    assert history.full_sector_pair_from_cache(workspace.market, topic(workspace), target, DAY) == expected


def test_corrupt_private_pair_falls_back_to_raw_chain(workspace, monkeypatch):
    plan, value = verified(workspace); previous = workspace.market.days[-2]
    expected = history.full_sector_pair_from_cache(workspace.market, topic(workspace), DAY, previous)
    strategies.prewarm_collected_sector(workspace.market, plan, value)
    with strategies._SUMMARY_LOCK:
        for entry in strategies._SUMMARIES.values(): entry['recent_pair'][0]['close'] += 1
    calls = []; original = charts._read_cached
    def read(*args, **kwargs):
        calls.append(1); return original(*args, **kwargs)
    monkeypatch.setattr(charts, '_read_cached', read)
    assert history.full_sector_pair_from_cache(workspace.market, topic(workspace), DAY, previous) == expected
    assert calls == [1]


@pytest.mark.parametrize('part', ['raw', 'normalized', 'pointer'])
def test_pair_does_not_hide_source_damage_or_fetch_network(workspace, part):
    plan, value = verified(workspace)
    strategies.prewarm_collected_sector(workspace.market, plan, value)
    path = (charts._root()/value['record']['raw_artifact'] if part == 'raw' else
            charts._root()/value['artifact'] if part == 'normalized' else
            charts._root()/'accepted/fuyao/sector-chart'/(DAY+'-'+CODE+'.json'))
    path.write_text('{}')
    with pytest.raises(ValueError):
        history.full_sector_pair_from_cache(workspace.market, topic(workspace), DAY, workspace.market.days[-2])


def test_request_plan_is_private_bounded_and_invalidates_on_catalog_change(workspace, monkeypatch):
    market = workspace.market; calls = []; original = charts._catalog
    def counted(reference): calls.append(1); return original(reference)
    monkeypatch.setattr(charts, '_catalog', counted)
    expected = charts.plan_sector_chart(market, CODE, DAY); calls.clear()
    with context.planning_scope(market, DAY):
        first = charts.plan_sector_chart(market, CODE, DAY)
        first['instrument']['name'] = 'edited caller copy'
        assert charts.plan_sector_chart(market, CODE, DAY) == expected
        assert calls == []
        market.ref['concept_catalog'][0]['name'] = 'changed current catalog'
        assert charts.plan_sector_chart(market, CODE, DAY)['instrument']['name'] == 'changed current catalog'
        assert len(calls) == 1
    assert context._planning.get() is None


def test_serial_source_interval_accounts_for_inline_work_and_keeps_report_small(workspace, monkeypatch):
    clock = {'now': 0.0}; requests = []; sleeps = []; consumed = []
    monkeypatch.setattr(batch.time, 'monotonic', lambda: clock['now'])
    def sleep(seconds): sleeps.append(seconds); clock['now'] += seconds
    monkeypatch.setattr(batch.time, 'sleep', sleep)
    original = workspace.adapter.request
    def request(*args): requests.append(clock['now']); return original(*args)
    monkeypatch.setattr(workspace.adapter, 'request', request)
    def consume(plan, value): consumed.append(plan['topic_code']); clock['now'] += .4
    with context.verified_consumer(consume):
        result = batch.collect_sector_chart_batch(workspace.market,
            batch.plan_sector_chart_batch(workspace.market, DAY), workspace.adapter)
    assert len(requests) == 2 and requests[1]-requests[0] == pytest.approx(batch.REQUEST_INTERVAL)
    assert sleeps == pytest.approx([batch.REQUEST_INTERVAL-.4])
    assert len(consumed) == 2 and context._consumer.get() is None
    assert result['coverage']['request_count'] == 2
    assert all('bars' not in item for item in result['items'])


def test_unaccepted_source_result_is_not_given_to_derivative_consumer(workspace, monkeypatch):
    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    def reject(*args): raise ValueError('invalid source summary')
    monkeypatch.setattr(batch, '_item_summary', reject)
    with context.verified_consumer(lambda *args: pytest.fail('source summary was rejected')):
        result = batch.collect_sector_chart_batch(workspace.market,
            batch.plan_sector_chart_batch(workspace.market, DAY), workspace.adapter)
    assert result['coverage']['failed_count'] == 2
    assert result['coverage']['available_count'] == 0
    assert not result['coverage']['collection_complete']


@pytest.mark.parametrize('change', ['pause', 'cancel', 'generation', 'derived_failure'])
def test_inline_derivative_preserves_control_errors_and_source_quality(local, monkeypatch, change):
    job = running(local); result = source_result()
    def collect(market, plan, adapter, guard=None, progress=None):
        context.consume_verified(plan['boards'][0], {})
        guard(); return result
    monkeypatch.setattr(local.jobs, 'collect_sector_chart_batch', collect)
    def inline(*args, **kwargs):
        if change == 'generation':
            local.current = deepcopy(local.market); local.current.state['snapshot_id'] = 'changed'
        elif change != 'derived_failure': tasks.change_task(job['job_id'], change)
        kwargs['guard']()
        raise OSError('optional derivative unavailable')
    monkeypatch.setattr(strategies, 'prewarm_collected_sector', inline)
    monkeypatch.setattr(strategies, 'prewarm_sector_metrics', lambda *a, **k:
        {'complete': True, 'available_count': 2, 'failed_count': 0})
    if change == 'derived_failure':
        actual = local.jobs.execute_batch_job(job['job_id'])
        assert actual['coverage'] == result['coverage'] and actual['quality'] == result['quality']
    else:
        expected = tasks.TaskPaused if change == 'pause' else tasks.TaskCancelled if change == 'cancel' else ValueError
        with pytest.raises(expected): local.jobs.execute_batch_job(job['job_id'])
    assert context._consumer.get() is None
