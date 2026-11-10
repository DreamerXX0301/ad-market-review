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


@pytest.mark.parametrize('scenario', ['current', 'initialisation', 'old-head', 'legacy', 'stopped', 'warm-error'])
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
            if scenario in ('legacy', 'initialisation'):
                saved = connection.execute('SELECT parameters_json FROM job WHERE job_id=?',
                                           (chain.root['job_id'],)).fetchone()
                params = json.loads(saved[0])
                if scenario == 'legacy':
                    params.pop('data_chain_version')
                else:
                    params['data_chain_version'] = 'sector-charts-v1'
                    connection.execute("UPDATE job SET kind='initialise' WHERE job_id=?", (chain.root['job_id'],))
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
    assert calls == ['noon-close', 'workspace'] + (['warm'] if scenario in ('current', 'initialisation', 'warm-error') else [])
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

    from app.services import pane_read_cache
    monkeypatch.setattr(pane_read_cache, 'warm_noon', warm)
    worker.dispatch(job)
    assert calls == ['suspension-evidence', 'warm']
    worker._warm_pane_after_snapshot(job, {'snapshot_id': 'unpublished'}, kind='noon')
    assert calls == ['suspension-evidence', 'warm']


@pytest.mark.parametrize('recent', [False, True], ids=['closing-day', 'recent-days'])
def test_warm_steps_read_in_separate_units_and_stop_at_new_head(monkeypatch, tmp_path, recent):
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
    steps = (pane_read_cache.recent_pane_warm_steps() if recent
             else pane_read_cache.closed_day_warm_steps())
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
    assert list(pane_read_cache.recent_pane_warm_steps()) == []


@pytest.mark.parametrize('recent', [False, True], ids=['closing-day', 'recent-days'])
def test_warm_steps_reuse_verified_files_and_rebuild_a_missing_block(monkeypatch, tmp_path, recent):
    from app.services import (auction_feedback, auction_service, dragon_tiger, market_node_replay, market_service,
                              news_events, noon_service, pane_read_cache)
    from app.routers import intraday

    latest = '2026-11-09'
    recent_days = [latest, '2026-11-06', '2026-11-05', '2026-11-04',
                   '2026-11-03', '2026-11-02', '2026-10-30']
    calendar = ['2026-10-29', *reversed(recent_days), '2026-11-10']
    expected_days = recent_days if recent else [latest]
    market = SimpleNamespace(status=lambda: {'data_end': latest, 'snapshot_id': 'snapshot'},
                             calendar=lambda: calendar)
    monkeypatch.setattr(core, 'get_market', lambda: market)
    monkeypatch.setattr(pane_read_cache, '_root', lambda: tmp_path)
    monkeypatch.setattr(pane_read_cache, '_warm_source', lambda *_: 'verified')
    monkeypatch.setattr(pane_read_cache, '_memory', pane_read_cache.OrderedDict())
    monkeypatch.setattr(pane_read_cache, '_memory_bytes', 0)
    snapshot_reads = []
    for name, module in (('auction-feedback', auction_feedback), ('auction', auction_service),
                         ('dragon-tiger', dragon_tiger), ('noon', noon_service)):
        monkeypatch.setattr(module, 'read_view', lambda day, name=name: snapshot_reads.append((day, name)))
    monkeypatch.setattr(intraday, 'view', lambda day: snapshot_reads.append((day, 'intraday')))
    news_reads = []
    monkeypatch.setattr(news_events, 'read_view', lambda day: news_reads.append((day, 'news')))
    monkeypatch.setattr(news_events, 'read_calendar', lambda day, days: news_reads.append((day, days)))

    reads = []
    builds = []

    def read(day, *names):
        for name in names:
            reads.append((day, name))
            pane_read_cache.read_block(name, day, 'proof', lambda: builds.append((day, name)) or {},
                                       market=market)

    def page(kind, day, *_args, **_kwargs):
        read(day, *{'gainers': ('gainers-price-rank-v1', 'gainers-full-events-v1'),
                    'emotion': ('emotion-facts', 'emotion-broken-board-facts', 'emotion-ladder-feedback-facts',
                                'emotion-first-board-facts'), 'sectors': ('sector-overview',),
                    'ladder': ('closing-ladder',)}.get(kind, ()))

    monkeypatch.setattr(market_service, 'market_page', page)
    monkeypatch.setattr(market_service, 'index_overview', lambda day: read(day, 'index-summary'))
    monkeypatch.setattr(market_service, 'overview', lambda day: read(day, 'gainers-overview', 'overview-breadth'))
    monkeypatch.setattr(market_node_replay, 'read_nodes', lambda day, *, market: read(day, 'node-replay'))
    warm_steps = (pane_read_cache.recent_pane_warm_steps if recent
                  else pane_read_cache.closed_day_warm_steps)
    list(warm_steps())
    assert list(dict.fromkeys(day for day, _ in reads)) == expected_days
    assert set(snapshot_reads) == {(day, name) for day in expected_days
                                  for name in ('auction', 'auction-feedback', 'noon', 'intraday', 'dragon-tiger')}
    assert news_reads == ([(day, scope) for day in expected_days for scope in ('news', 30, 7)] if recent else [])
    expected_blocks = set(pane_read_cache._CLOSED_BLOCKS) | {
        'emotion-broken-board-facts', 'emotion-ladder-feedback-facts', 'emotion-first-board-facts', 'node-replay',
    }
    assert set(builds) == {(day, name) for day in expected_days for name in expected_blocks}
    assert {path.name for path in tmp_path.iterdir() if path.is_dir()} == set(expected_days)
    assert all((tmp_path / day / 'closed-day-warm.json').is_file() for day in expected_days)
    reads.clear()
    builds.clear()
    list(warm_steps())
    assert reads == [(day, 'node-replay') for day in expected_days]
    assert not builds
    target = expected_days[-1]
    (tmp_path / target / 'node-replay.json').unlink()
    reads.clear()
    list(warm_steps())
    assert reads == [(day, 'node-replay') for day in expected_days]
    assert builds == [(target, 'node-replay')]
    builds.clear()
    # A still-valid worker memory entry must not hide the missing shared file.
    (tmp_path / target / 'gainers-full-events-v1.json').unlink()
    list(warm_steps())
    assert builds == [(target, 'gainers-full-events-v1')]
