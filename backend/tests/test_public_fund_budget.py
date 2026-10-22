"""Frozen fund budgets with synthetic transports only; no provider calls."""
import asyncio
from collections import Counter
from datetime import date, timedelta
import time

import httpx
import pytest

from app.adapters.public_fund_budget import FUND_REQUEST_BUDGET_VERSION, FundRequestBudget
from app.adapters.public_fund_history import fetch_sector_fund_history
from app.adapters.public_market import PublicMarketClient


def calendar():
    days = []; day = date(2026, 1, 5)
    while len(days) < 20:
        if day.weekday() < 5: days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def directory(count):
    links = ''.join(f'<a href="/bkzj/BK{1000+i:04d}.html">行业{i}</a>' for i in range(count))
    return '<ul class="pop_slider"><li>行业</li><li>概念</li><li>地域</li></ul><div class="pop-cont">'+links+'</div><div class="pop-cont"></div><div class="pop-cont"></div>'


def history(code, days=None):
    return {'rc': 0, 'data': {'code': code, 'market': 90,
        'klines': [','.join([d, '10', '-4', '-6', '2', '8', '1', '-1', '-1', '1', '1', '10', '.2']) for d in days or calendar()]}}


def run(handler, *, count=128, budget=None, cache=None, guard=None):
    async def transport(request):
        if request.url.host == 'data.eastmoney.com':
            return httpx.Response(200, text=directory(count))
        return await handler(request)
    client = PublicMarketClient(httpx.MockTransport(transport))
    return fetch_sector_fund_history(client, calendar()[-1], calendar=calendar(),
        request_budget=budget or FundRequestBudget(), request_interval=0, cache_save=cache, guard=guard)


def test_healthy_full_directory_keeps_coverage_and_rolling_concurrency():
    calls = []; active = 0; peak = 0
    async def handler(request):
        nonlocal active, peak
        active += 1; peak = max(peak, active)
        try:
            await asyncio.sleep(.001)
            code = request.url.params['secid'].split('.')[1]; calls.append(code)
            return httpx.Response(200, json=history(code))
        finally: active -= 1
    result = run(handler)
    assert result['complete'] and len(result['items']) == 128
    assert len(calls) == 128 and peak == 3 and active == 0
    coverage = result['coverage']
    assert coverage['logical_request_count'] == coverage['actual_transport_attempt_count'] == 129
    assert coverage['max_logical_requests'] == 200 and coverage['max_transport_attempts'] == 600
    assert not coverage['source_circuit_open'] and coverage['peak_inflight_requests'] == 3


@pytest.mark.parametrize('failure', ['connection', 'schema', 'http_5xx'])
def test_same_source_failure_stops_new_submissions_and_records_real_attempts(monkeypatch, failure):
    calls = Counter()
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    async def handler(request):
        code = request.url.params['secid'].split('.')[1]; calls[code] += 1
        if failure == 'connection': raise httpx.ConnectError('synthetic disconnect', request=request)
        if failure == 'http_5xx': return httpx.Response(503)
        return httpx.Response(200, json=history('BK9999'))
    result = run(handler)
    coverage = result['coverage']
    assert not result['complete'] and result['items'] == []
    assert coverage['source_circuit_open'] and coverage['consecutive_source_failures'] == 5
    assert coverage['source_failure_kind'] == failure
    assert len(calls) <= 7 and coverage['logical_request_count'] == 1 + len(calls) <= 10
    assert coverage['actual_transport_attempt_count'] == 1 + sum(calls.values()) <= 30
    assert coverage['unrequested_count'] >= 121 and len(result['failures']) == 128
    assert coverage['retry_eligible'] is False
    assert any(f['stage'] == 'source_circuit_open' for f in result['failures'])


def test_success_and_different_error_kinds_reset_consecutive_failure_probe():
    budget = FundRequestBudget()
    for _ in range(4): budget.outcome('connection')
    budget.outcome('schema')
    assert budget.consecutive_failures == 1 and not budget.circuit_open
    for _ in range(3): budget.outcome('schema')
    budget.outcome()
    assert budget.consecutive_failures == 0 and budget.failure_kind is None
    for _ in range(4): budget.outcome('connection')
    assert not budget.circuit_open


class SlowBody(httpx.AsyncByteStream):
    def __init__(self, live): self.live = live; self.live.add(self)

    async def __aiter__(self):
        while True:
            # A trickling peer avoids a simple per-read inactivity timeout.
            await asyncio.sleep(.01)
            yield b' '

    async def aclose(self): self.live.discard(self)


def test_absolute_deadline_cancels_trickling_inflight_and_retains_success():
    live = set(); saved = []; calls = []
    async def handler(request):
        code = request.url.params['secid'].split('.')[1]; calls.append(code)
        if code == 'BK1000': return httpx.Response(200, json=history(code))
        return httpx.Response(200, stream=SlowBody(live))
    started = time.monotonic()
    result = run(handler, count=30, budget=FundRequestBudget(deadline_seconds=.08), cache=lambda *args: saved.append(args))
    elapsed = time.monotonic() - started
    coverage = result['coverage']
    assert elapsed < .8 and not live
    assert len(saved) == 1 and result['items'][0]['topic_code'] == 'BK1000'
    assert not result['complete'] and coverage['stop_reason'] == 'request_deadline'
    assert coverage['logical_request_count'] == coverage['actual_transport_attempt_count'] == len(calls) + 1
    assert coverage['unrequested_count'] > 0 and coverage['retry_eligible'] is False
    assert 'local cache/validation/publication measured separately' in coverage['request_deadline_scope']


def test_directory_response_body_is_also_inside_absolute_deadline():
    live = set(); calls = []
    async def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, stream=SlowBody(live))
    result = fetch_sector_fund_history(PublicMarketClient(httpx.MockTransport(handler)), calendar()[-1],
        calendar=calendar(), request_budget=FundRequestBudget(deadline_seconds=.04), request_interval=0)
    assert calls == ['data.eastmoney.com'] and not live
    assert result['coverage']['logical_request_count'] == result['coverage']['actual_transport_attempt_count'] == 1
    assert result['failures'][0]['stage'] == 'directory'
    assert result['failures'][0]['failure_kind'] == 'request_deadline'
    assert result['coverage']['directory_count'] is None and result['items'] == []


def test_pause_cancels_and_awaits_inflight_without_detached_background_transport():
    from app.services.task_service import TaskPaused
    live = set()
    async def handler(request): return httpx.Response(200, stream=SlowBody(live))
    def guard():
        if live: raise TaskPaused()
    with pytest.raises(TaskPaused):
        run(handler, count=10, budget=FundRequestBudget(deadline_seconds=1), guard=guard)
    assert not live


def test_new_policy_skips_second_round_on_circuit_and_keeps_legacy_200_behavior(tmp_path, monkeypatch):
    from app.services.market_supplement import fetch_history_for_market, SupplementCache
    calls = Counter()
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    async def handler(request):
        if request.url.host == 'data.eastmoney.com':
            calls['directory'] += 1
            return httpx.Response(200, text=directory(128))
        calls['history_attempts'] += 1
        raise httpx.ConnectError('synthetic failure', request=request)
    market = type('Market', (), {'calendar': lambda self: calendar()})()
    result = fetch_history_for_market(PublicMarketClient(httpx.MockTransport(handler)), market, calendar()[-1],
        SupplementCache(tmp_path), request_budget_version=FUND_REQUEST_BUDGET_VERSION)
    assert result['coverage']['round_count'] == 1 and calls['directory'] == 1
    assert result['coverage']['logical_request_count'] <= 10
    assert result['coverage']['actual_transport_attempt_count'] == sum(calls.values()) <= 30
    assert result['coverage']['request_budget_version'] == FUND_REQUEST_BUDGET_VERSION
    # Existing unmarked tasks' two-round/200 contract has separate integration
    # fixtures in test_market_repair, including all 594 history attempts.


def test_retry_eligible_second_round_reuses_success_and_shared_transport_counts(tmp_path, monkeypatch):
    from app.services.market_supplement import fetch_history_for_market, SupplementCache
    calls = Counter()
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    async def handler(request):
        if request.url.host == 'data.eastmoney.com':
            calls['directory'] += 1
            return httpx.Response(200, text=directory(3))
        code = request.url.params['secid'].split('.')[1]; calls[code] += 1
        if code == 'BK1001' and calls[code] <= 3: return httpx.Response(503)
        return httpx.Response(200, json=history(code))
    market = type('Market', (), {'calendar': lambda self: calendar()})()
    result = fetch_history_for_market(PublicMarketClient(httpx.MockTransport(handler)), market, calendar()[-1],
        SupplementCache(tmp_path), request_budget_version=FUND_REQUEST_BUDGET_VERSION)
    coverage = result['coverage']
    assert result['complete'] and len(result['items']) == 3 and coverage['round_count'] == 2
    assert coverage['logical_request_count'] == 6 and coverage['actual_transport_attempt_count'] == 8
    assert calls == {'directory': 2, 'BK1000': 1, 'BK1001': 4, 'BK1002': 1}
    assert result['rounds'][1]['cached_count'] == 2


def test_dated_data_gap_does_not_trip_source_circuit_or_automatically_retry(tmp_path):
    from app.services.market_supplement import fetch_history_for_market, SupplementCache
    calls = []
    async def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'data.eastmoney.com': return httpx.Response(200, text=directory(7))
        code = request.url.params['secid'].split('.')[1]
        return httpx.Response(200, json=history(code, calendar()[:-1]))
    market = type('Market', (), {'calendar': lambda self: calendar()})()
    result = fetch_history_for_market(PublicMarketClient(httpx.MockTransport(handler)), market, calendar()[-1],
        SupplementCache(tmp_path), request_budget_version=FUND_REQUEST_BUDGET_VERSION)
    assert len(calls) == 8 and result['coverage']['round_count'] == 1
    assert not result['coverage']['source_circuit_open'] and result['items'] == []
    assert {f['failure_kind'] for f in result['failures']} == {'data_gap'}


@pytest.mark.parametrize('version', ['unknown', True, 1, {}, []])
def test_unrecognized_frozen_version_is_rejected_before_network(version, tmp_path):
    from app.services.market_supplement import fetch_history_for_market, SupplementCache
    with pytest.raises(ValueError, match='预算版本'):
        fetch_history_for_market(None, None, calendar()[-1], SupplementCache(tmp_path), request_budget_version=version)


def test_transport_budget_counts_only_started_http_and_marks_unsent_tasks(monkeypatch):
    calls = []
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    async def handler(request):
        calls.append(request.url.params['secid'])
        return httpx.Response(503)
    result = run(handler, count=5, budget=FundRequestBudget(max_transport_attempts=2))
    coverage = result['coverage']
    assert len(calls) == 1
    assert coverage['logical_request_count'] == coverage['actual_transport_attempt_count'] == 2
    assert coverage['stop_reason'] == 'transport_budget'
    assert coverage['unrequested_count'] == 4 and len(result['failures']) == 5
    assert sum(f['provider_request_started'] for f in result['failures']) == 1


def test_deadline_before_first_history_transport_counts_all_unsent_codes():
    calls = []
    async def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'data.eastmoney.com': return httpx.Response(200, text=directory(3))
        return httpx.Response(200, json=history(request.url.params['secid'].split('.')[1]))
    result = fetch_sector_fund_history(PublicMarketClient(httpx.MockTransport(handler)), calendar()[-1],
        calendar=calendar(), request_budget=FundRequestBudget(deadline_seconds=.03), request_interval=.2)
    assert calls == ['data.eastmoney.com']
    assert result['coverage']['logical_request_count'] == result['coverage']['actual_transport_attempt_count'] == 1
    assert result['coverage']['unrequested_count'] == 3 and len(result['failures']) == 3
    assert all(not f['provider_request_started'] for f in result['failures'])


def test_second_round_shares_original_deadline_and_keeps_success_cache(tmp_path, monkeypatch):
    from app.services import market_supplement as module
    calls = Counter(); live = set(); budgets = []
    def budget():
        value = FundRequestBudget(deadline_seconds=.12); budgets.append(value); return value
    monkeypatch.setattr(module, 'FundRequestBudget', budget)
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    # Only the policy's request-spacing delay is suppressed; HTTP body reads and
    # the shared absolute timer continue to use the actual event loop clock.
    original_fetch = __import__('app.adapters.public_fund_history', fromlist=['fetch_sector_fund_history']).fetch_sector_fund_history
    def fetch(*args, **kwargs): return original_fetch(*args, **kwargs, request_interval=0)
    monkeypatch.setattr('app.adapters.public_fund_history.fetch_sector_fund_history', fetch)
    async def handler(request):
        if request.url.host == 'data.eastmoney.com':
            calls['directory'] += 1
            return httpx.Response(200, text=directory(3))
        code = request.url.params['secid'].split('.')[1]; calls[code] += 1
        if code == 'BK1001':
            if calls[code] <= 3:
                await asyncio.sleep(.015)
                return httpx.Response(503)
            return httpx.Response(200, stream=SlowBody(live))
        return httpx.Response(200, json=history(code))
    market = type('Market', (), {'calendar': lambda self: calendar()})()
    started = time.monotonic()
    result = module.fetch_history_for_market(PublicMarketClient(httpx.MockTransport(handler)), market, calendar()[-1],
        module.SupplementCache(tmp_path), request_budget_version=FUND_REQUEST_BUDGET_VERSION)
    assert time.monotonic() - started < .8 and not live and len(budgets) == 1
    assert result['coverage']['round_count'] == 2 and result['coverage']['stop_reason'] == 'request_deadline'
    assert len(result['items']) == 2 and result['rounds'][1]['cached_count'] == 2
    assert result['coverage']['actual_transport_attempt_count'] == sum(calls.values()) == 8
    assert result['coverage']['logical_request_count'] == 6 and not result['complete']


@pytest.mark.parametrize('version', [None, FUND_REQUEST_BUDGET_VERSION])
def test_execute_uses_only_frozen_task_version_and_does_not_rewrite_legacy(tmp_path, monkeypatch, version):
    from app import core
    from app.services import market_supplement as module, task_service as tasks
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path/'data'))
    core.clear_market_cache(); core.ensure_ready()
    parameters = {'operation': 'market_supplement', 'year_history': False, 'recent_days': 1}
    if version is not None: parameters['fund_request_budget_version'] = version
    task = tasks.create_task('source_check', parameters, calendar()[-1])
    calls = []
    market = type('Market', (), {'status': lambda self: {'snapshot_id': 'fixture', 'data_end': calendar()[-1]}})()
    monkeypatch.setattr(module, 'get_market', lambda: market)
    def refresh(current, target, **kwargs):
        calls.append(kwargs['fund_request_budget_version']); kwargs['guard'](); return {'fixture': True}
    monkeypatch.setattr(module, 'refresh_for_market', refresh)
    try:
        assert module.execute_supplement(task['job_id']) == {'fixture': True}
        assert calls == [version] and tasks.task_view(task['job_id'])['parameters'] == parameters
    finally: core.clear_market_cache()


def test_execute_rejects_unknown_policy_before_opening_market(tmp_path, monkeypatch):
    from app import core
    from app.services import market_supplement as module, task_service as tasks
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path/'data'))
    core.clear_market_cache(); core.ensure_ready()
    task = tasks.create_task('source_check', {'operation': 'market_supplement', 'fund_request_budget_version': 'future-budget'})
    monkeypatch.setattr(module, 'get_market', lambda: pytest.fail('Unknown frozen budget must fail before opening market/network'))
    try:
        with pytest.raises(ValueError, match='预算版本'): module.execute_supplement(task['job_id'])
    finally: core.clear_market_cache()


@pytest.mark.parametrize('permanent_kind', ['data_gap', 'schema'])
def test_mixed_temporary_and_nonretryable_failures_retry_only_explicit_codes(tmp_path, monkeypatch, permanent_kind):
    from app.services.market_supplement import fetch_history_for_market, SupplementCache
    calls = Counter()
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    async def handler(request):
        if request.url.host == 'data.eastmoney.com':
            calls['directory'] += 1
            return httpx.Response(200, text=directory(3))
        code = request.url.params['secid'].split('.')[1]; calls[code] += 1
        if code == 'BK1001' and calls[code] <= 3: return httpx.Response(503)
        if code == 'BK1002':
            value = history(code, calendar()[:-1]) if permanent_kind == 'data_gap' else history('BK9999')
            return httpx.Response(200, json=value)
        return httpx.Response(200, json=history(code))
    market = type('Market', (), {'calendar': lambda self: calendar()})()
    result = fetch_history_for_market(PublicMarketClient(httpx.MockTransport(handler)), market, calendar()[-1],
        SupplementCache(tmp_path), request_budget_version=FUND_REQUEST_BUDGET_VERSION)
    assert calls == {'directory': 2, 'BK1000': 1, 'BK1001': 4, 'BK1002': 1}
    assert result['coverage']['logical_request_count'] == 6 and result['coverage']['actual_transport_attempt_count'] == 8
    assert len(result['items']) == 2 and not result['complete'] and result['coverage']['round_count'] == 2
    failure, = result['failures']
    assert failure['topic_code'] == 'BK1002' and failure['failure_kind'] == permanent_kind
    assert failure['carried_from_previous_round'] and not failure['retryable'] and not failure['provider_request_started']


def test_temporary_directory_failure_can_recover_then_make_first_history_attempts(tmp_path, monkeypatch):
    from app.services.market_supplement import fetch_history_for_market, SupplementCache
    calls = Counter()
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    async def handler(request):
        if request.url.host == 'data.eastmoney.com':
            calls['directory'] += 1
            if calls['directory'] <= 3: return httpx.Response(503)
            return httpx.Response(200, text=directory(3))
        code = request.url.params['secid'].split('.')[1]; calls[code] += 1
        return httpx.Response(200, json=history(code))
    market = type('Market', (), {'calendar': lambda self: calendar()})()
    result = fetch_history_for_market(PublicMarketClient(httpx.MockTransport(handler)), market, calendar()[-1],
        SupplementCache(tmp_path), request_budget_version=FUND_REQUEST_BUDGET_VERSION)
    assert result['complete'] and len(result['items']) == 3 and result['coverage']['round_count'] == 2
    assert calls == {'directory': 4, 'BK1000': 1, 'BK1001': 1, 'BK1002': 1}
    assert result['coverage']['logical_request_count'] == 5 and result['coverage']['actual_transport_attempt_count'] == 7


def test_second_round_retains_valid_target_with_incomplete_historical_cache(tmp_path, monkeypatch):
    from app.services.market_supplement import fetch_history_for_market, SupplementCache
    calls = Counter()
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    async def handler(request):
        if request.url.host == 'data.eastmoney.com':
            calls['directory'] += 1
            return httpx.Response(200, text=directory(3))
        code = request.url.params['secid'].split('.')[1]; calls[code] += 1
        if code == 'BK1001' and calls[code] <= 3: return httpx.Response(503)
        return httpx.Response(200, json=history(code, calendar()[1:] if code == 'BK1002' else None))
    market = type('Market', (), {'calendar': lambda self: calendar()})()
    result = fetch_history_for_market(PublicMarketClient(httpx.MockTransport(handler)), market, calendar()[-1],
        SupplementCache(tmp_path), request_budget_version=FUND_REQUEST_BUDGET_VERSION)
    assert result['complete'] and len(result['items']) == 3
    assert not result['coverage']['history_20d_complete']
    assert result['coverage']['history_complete_count'] == 2
    assert result['coverage']['history_gaps'][0]['dates'] == [calendar()[0]]
    assert calls['BK1002'] == 1 and result['rounds'][1]['cached_count'] == 2


def test_pause_during_directory_cancels_and_awaits_directory_transport():
    from app.services.task_service import TaskPaused
    live = set(); calls = []
    async def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, stream=SlowBody(live))
    def guard():
        if live: raise TaskPaused()
    budget = FundRequestBudget(deadline_seconds=1)
    started = time.monotonic()
    with pytest.raises(TaskPaused):
        fetch_sector_fund_history(PublicMarketClient(httpx.MockTransport(handler)), calendar()[-1],
            calendar=calendar(), request_budget=budget, request_interval=0, guard=guard)
    assert time.monotonic() - started < .8 and not live and calls == ['data.eastmoney.com']
    assert budget.logical_request_count == budget.actual_transport_attempt_count == 1


def test_healthy_request_budget_exhaustion_remains_explicit_partial():
    calls = []
    async def handler(request):
        code = request.url.params['secid'].split('.')[1]; calls.append(code)
        return httpx.Response(200, json=history(code))
    result = run(handler, count=5, budget=FundRequestBudget(max_logical_requests=2))
    assert calls == ['BK1000'] and len(result['items']) == 1 and not result['complete']
    assert result['coverage']['stop_reason'] == 'request_budget'
    assert result['coverage']['logical_request_count'] == result['coverage']['actual_transport_attempt_count'] == 2
    assert result['coverage']['unrequested_count'] == 4 and len(result['failures']) == 4


@pytest.mark.parametrize('change', ['shrink', 'rename', 'move_kind'])
def test_second_round_directory_drift_keeps_first_scope_and_failures(tmp_path, monkeypatch, change):
    from app.services.market_supplement import fetch_history_for_market, SupplementCache
    calls = Counter()
    async def no_wait(_): pass
    monkeypatch.setattr('app.adapters.public_fund_budget._retry_wait', no_wait)
    async def handler(request):
        if request.url.host == 'data.eastmoney.com':
            calls['directory'] += 1
            html = directory(3)
            if calls['directory'] == 2:
                if change == 'shrink': html = directory(2)
                elif change == 'rename': html = html.replace('行业0', '行业改名')
                else:
                    link = '<a href="/bkzj/BK1002.html">行业2</a>'
                    html = html.replace(link, '').replace('<div class="pop-cont"></div>', '<div class="pop-cont">'+link+'</div>', 1)
            return httpx.Response(200, text=html)
        code = request.url.params['secid'].split('.')[1]; calls[code] += 1
        if code == 'BK1002' and calls[code] <= 3: return httpx.Response(503)
        return httpx.Response(200, json=history(code))
    market = type('Market', (), {'calendar': lambda self: calendar()})()
    result = fetch_history_for_market(PublicMarketClient(httpx.MockTransport(handler)), market, calendar()[-1],
        SupplementCache(tmp_path), request_budget_version=FUND_REQUEST_BUDGET_VERSION)
    coverage = result['coverage']
    assert not result['complete'] and not coverage['complete'] and not coverage['history_20d_complete']
    assert coverage['directory_count'] == coverage['selected_count'] == 3
    assert len(result['items']) == 2 and coverage['accepted_count'] == 2
    assert coverage['directory_scope_changed'] and coverage['stop_reason'] == 'directory_changed'
    assert coverage['frozen_directory_signature'] != coverage['observed_directory_signature']
    assert {f.get('topic_code') for f in result['failures'] if f.get('topic_code')} == {'BK1002'}
    assert any(f['stage'] == 'directory_changed' for f in result['failures'])
    assert len(result['raw_pages']) == 2 and all(r['pages'][0]['kind'] == 'directory' for r in result['raw_pages'])
