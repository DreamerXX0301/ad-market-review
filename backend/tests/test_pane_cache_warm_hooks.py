"""Cache warming follows committed local publications and cannot fail them."""
import json
import sys
from types import SimpleNamespace

import pytest

from app import core, worker
from app.services import market_supplement, task_service as tasks
from test_noon_snapshots import DAY, create as create_noon, noon
from test_sync_workspace_followup import chain, event
from test_workspace import ws


@pytest.mark.parametrize('scenario', ['current', 'old-head', 'legacy', 'stopped', 'warm-error'])
def test_closing_worker_warms_only_the_current_published_chain(chain, monkeypatch, scenario):
    tasks.finish(chain.sector['job_id'], 'succeeded')
    task = tasks.create_task('source_check', {'operation': 'market_supplement',
        'parent_sync_job_id': chain.root['job_id']}, chain.day)
    event(chain.sector, 'followup_queued', next_task_id=task['job_id'], target_date=chain.day)
    job = tasks.claim_next('fixture-worker')
    assert job['job_id'] == task['job_id']
    if scenario != 'old-head':
        with core.connect() as connection:
            connection.execute("UPDATE published_head SET snapshot_id='daily_raw-fixture-increment' WHERE module='daily_raw'")
            if scenario == 'legacy':
                saved = connection.execute('SELECT parameters_json FROM job WHERE job_id=?',
                                           (chain.root['job_id'],)).fetchone()
                params = json.loads(saved[0])
                params.pop('data_chain_version')
                connection.execute('UPDATE job SET parameters_json=?,parameters_hash=? WHERE job_id=?',
                                   (core.dumps(params), core.digest(params), chain.root['job_id']))
    monkeypatch.setattr(market_supplement, 'execute_supplement',
        lambda *_: {**chain.doc, 'job_id': job['job_id']})
    calls = []
    monkeypatch.setattr(worker, '_noon_close_after_supplement', lambda *_: calls.append('noon-close'))
    def workspace(*_):
        calls.append('workspace')
        if scenario == 'stopped':
            event(chain.root, 'increment_chain_stop_requested', action='cancel')

    monkeypatch.setattr(worker, '_workspace_after_data', workspace)

    def warm(day):
        assert day == chain.day
        assert tasks.task_view(job['job_id'])['status'] == 'partial'
        calls.append('warm')
        if scenario == 'warm-error':
            raise OSError('synthetic cache storage failure')

    monkeypatch.setitem(sys.modules, 'app.services.pane_read_cache', SimpleNamespace(warm_closed_day=warm))
    worker.dispatch(job)
    assert calls == ['noon-close', 'workspace'] + (['warm'] if scenario in ('current', 'warm-error') else [])
    assert tasks.task_view(job['job_id'])['status'] == 'partial'


def test_noon_worker_warms_after_snapshot_and_task_commit(noon, monkeypatch):
    response = create_noon(noon)
    assert response.status_code == 200
    job = tasks.claim_next('fixture-noon-worker')
    assert job['job_id'] == response.json()['task_id']
    calls = []
    monkeypatch.setattr(worker, '_capture_suspensions', lambda *_: calls.append('suspension-evidence'))

    def warm(day):
        with core.connect() as connection:
            row = connection.execute('SELECT trade_date FROM market_noon_snapshot WHERE job_id=?',
                                     (job['job_id'],)).fetchone()
        assert row and row['trade_date'] == day == DAY
        assert tasks.task_view(job['job_id'])['status'] == 'partial'
        calls.append('warm')

    monkeypatch.setitem(sys.modules, 'app.services.pane_read_cache', SimpleNamespace(warm_noon=warm))
    worker.dispatch(job)
    assert calls == ['suspension-evidence', 'warm']
    worker._warm_pane_after_noon(job, {'snapshot_id': 'unpublished'})
    assert calls == ['suspension-evidence', 'warm']


def test_startup_warm_steps_read_latest_in_separate_units_and_stop_at_new_head(monkeypatch, tmp_path):
    from app.services import market_service, pane_read_cache

    earlier, latest = '2026-11-05', '2026-11-06'
    status = {'ready': True, 'data_end': latest, 'snapshot_id': 'initial'}
    market = SimpleNamespace(status=lambda: status, calendar=lambda: [earlier, latest])
    monkeypatch.setattr(core, 'get_market', lambda: market)
    monkeypatch.setattr(pane_read_cache, '_root', lambda: tmp_path / 'cache')
    calls = []
    monkeypatch.setattr(market_service, 'index_overview', lambda day: calls.append(('index', day)))
    monkeypatch.setattr(market_service, 'market_page', lambda kind, day, *_args, **_kwargs: calls.append((kind, day)))

    assert list(pane_read_cache.closed_day_warm_steps(earlier)) == []
    steps = pane_read_cache.closed_day_warm_steps()
    assert calls == []
    next(steps)
    assert calls == [('index', latest)]
    next(steps)
    assert calls == [('index', latest), ('gainers', latest)]
    status['snapshot_id'] = 'changed'
    assert list(steps) == []
    assert calls == [('index', latest), ('gainers', latest)]


def test_startup_warm_skips_an_uninitialized_market(monkeypatch):
    from app.services import pane_read_cache

    market = SimpleNamespace(status=lambda: {'ready': False, 'data_end': None}, calendar=list)
    monkeypatch.setattr(core, 'get_market', lambda: market)
    assert list(pane_read_cache.closed_day_warm_steps()) == []


def test_startup_reuses_verified_files_and_rebuilds_a_missing_block(monkeypatch, tmp_path):
    from app.services import auction_feedback, auction_service, dragon_tiger, market_service, noon_service, pane_read_cache
    from app.routers import intraday

    day = '2026-11-06'
    market = SimpleNamespace(status=lambda: {'data_end': day, 'snapshot_id': 'snapshot'},
                             calendar=lambda: [day])
    monkeypatch.setattr(core, 'get_market', lambda: market)
    monkeypatch.setattr(pane_read_cache, '_root', lambda: tmp_path)
    monkeypatch.setattr(pane_read_cache, '_warm_source', lambda *_: 'verified')
    monkeypatch.setattr(pane_read_cache, '_memory', pane_read_cache.OrderedDict())
    monkeypatch.setattr(pane_read_cache, '_memory_bytes', 0)
    for module in (auction_feedback, auction_service, dragon_tiger, noon_service):
        monkeypatch.setattr(module, 'read_view', lambda *_: {})
    monkeypatch.setattr(intraday, 'view', lambda *_: {})

    reads = []
    builds = []

    def read(*names):
        for name in names:
            reads.append(name)
            pane_read_cache.read_block(name, day, 'proof', lambda: builds.append(name) or {}, market=market)

    def page(kind, *_args, **_kwargs):
        read(*{'gainers': ('gainers-price-rank-v1', 'gainers-full-events-v1'),
               'emotion': ('emotion-facts',), 'sectors': ('sector-overview',),
               'ladder': ('closing-ladder',)}.get(kind, ()))

    monkeypatch.setattr(market_service, 'market_page', page)
    monkeypatch.setattr(market_service, 'index_overview', lambda *_: read('index-summary'))
    monkeypatch.setattr(market_service, 'overview', lambda *_: read('gainers-overview', 'overview-breadth'))
    list(pane_read_cache.closed_day_warm_steps())
    assert set(builds) == set(pane_read_cache._CLOSED_BLOCKS)
    assert (tmp_path / day / 'closed-day-warm.json').is_file()
    reads.clear()
    builds.clear()
    pane_read_cache._memory.clear()
    list(pane_read_cache.closed_day_warm_steps())
    assert reads == builds == []
    (tmp_path / day / 'gainers-full-events-v1.json').unlink()
    list(pane_read_cache.closed_day_warm_steps())
    assert builds == ['gainers-full-events-v1']
