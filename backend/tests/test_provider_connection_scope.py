"""No network: connection reuse keeps provider budgets and sanitized failures."""
import httpx
from contextvars import copy_context
import pytest

from app.adapters import providers


@pytest.fixture
def transport(monkeypatch):
    clock = {'now': 100.0, 'sleeps': []}
    created = []; calls = []; responses = []
    def sleep(seconds):
        clock['sleeps'].append(seconds); clock['now'] += seconds
    def handler(request):
        calls.append((clock['now'], request))
        clock['now'] += clock.get('latency', 0)
        item = responses.pop(0) if responses else {'code': 0, 'data': {'item': []}}
        if isinstance(item, BaseException): raise item
        if isinstance(item, tuple): return httpx.Response(item[0], json=item[1])
        return httpx.Response(200, json=item)
    def factory():
        result = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        created.append(result)
        return result
    monkeypatch.setattr(providers, '_http_client', factory)
    monkeypatch.setattr(providers.time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(providers.time, 'sleep', sleep)
    return clock, created, calls, responses


def test_one_task_710_requests_reuses_one_connection_and_keeps_709_intervals(transport):
    clock, clients, calls, _ = transport
    with providers.provider_connection_scope():
        for index in range(710):
            result = providers.ProviderClient('fuyao', 'fixture-key').request('/fixture', {'index': index, 'flag': True})
            assert result == {'code': 0, 'data': {'item': []}}
        assert len(clients) == 1 and not clients[0].is_closed
    assert clients[0].is_closed and len(calls) == 710
    assert all(b[0] - a[0] >= 1.2 - 1e-9 for a, b in zip(calls, calls[1:]))
    assert sum(clock['sleeps']) == pytest.approx(709 * 1.2)
    assert all(request.url.host == 'fuyao.aicubes.cn' for _, request in calls)
    assert calls[-1][1].url.params['flag'] == 'true'


def test_task_and_credential_scopes_do_not_retain_clients_or_authentication(transport):
    _, clients, calls, _ = transport
    with providers.provider_connection_scope():
        providers.ProviderClient('fuyao', 'first-fixture').request('/one')
        with providers.provider_connection_scope():
            providers.ProviderClient('fuyao', 'first-fixture').request('/two')
        assert len(clients) == 1 and not clients[0].is_closed
        providers.ProviderClient('fuyao', 'second-fixture').request('/three')
        assert len(clients) == 2 and all(not client.is_closed for client in clients)
    assert all(client.is_closed for client in clients)
    with providers.provider_connection_scope():
        providers.ProviderClient('fuyao', 'first-fixture').request('/four')
    assert len(clients) == 3 and all(client.is_closed for client in clients)
    assert [request.headers['X-api-key'] for _, request in calls] == ['first-fixture', 'first-fixture', 'second-fixture', 'first-fixture']


def test_unscoped_calls_close_each_transport_and_keep_adapter_spacing(transport):
    _, clients, calls, _ = transport
    adapter = providers.ProviderClient('tickflow-public')
    adapter.request('/one'); adapter.request('/two')
    assert len(clients) == 2 and all(client.is_closed for client in clients)
    assert calls[1][0] - calls[0][0] >= 6.2 - 1e-9
    assert all('X-api-key' not in request.headers for _, request in calls)


@pytest.mark.parametrize('statuses', [(429, 500), (500, 503)])
def test_rate_limits_and_server_errors_keep_bounded_retry_backoff(transport, statuses):
    _, clients, calls, responses = transport
    responses.extend([(status, {}) for status in statuses])
    with providers.provider_connection_scope():
        providers.ProviderClient('fuyao', 'fixture').request('/retry')
    assert len(clients) == 1 and clients[0].is_closed and len(calls) == 3
    assert [call[0] for call in calls] == pytest.approx([100, 102, 106])


@pytest.mark.parametrize('error', [httpx.ReadTimeout('https://secret.invalid?key=private'), ValueError('private-body')])
def test_transport_errors_are_limited_spaced_and_do_not_leak_details(transport, error):
    _, clients, calls, responses = transport
    responses.extend([error, error, error])
    with pytest.raises(providers.ProviderError) as raised:
        with providers.provider_connection_scope():
            providers.ProviderClient('fuyao', 'private-key').request('/retry')
    assert len(calls) == 3 and len(clients) == 1 and clients[0].is_closed
    assert 'private' not in str(raised.value) and 'secret' not in str(raised.value)
    assert all(b[0] - a[0] >= 1.2 - 1e-9 for a, b in zip(calls, calls[1:]))


def test_unauthorized_request_is_not_retried_and_closes_on_failure(transport):
    _, clients, calls, responses = transport
    responses.append((401, {}))
    with pytest.raises(providers.ProviderError) as raised:
        with providers.provider_connection_scope():
            providers.ProviderClient('fuyao', 'fixture').request('/denied')
    assert raised.value.status == 401 and len(calls) == 1 and clients[0].is_closed
    assert len(clients) == 1


def test_explicit_attempt_guard_stops_retry_after_window_expires(transport):
    from app.services.noon_collection import WindowExpired
    clock, clients, calls, responses = transport
    responses.append((429, {}))
    def guard():
        if clock['now'] >= 101:
            raise WindowExpired('fixture noon deadline')
    with pytest.raises(WindowExpired):
        with providers.provider_connection_scope():
            providers.ProviderClient('fuyao', 'fixture').request('/noon', before_attempt=guard)
    assert len(calls) == 1 and clients[0].is_closed


def test_empty_data_unknown_format_and_missing_key_never_become_valid_empty_results(transport):
    _, clients, calls, responses = transport
    with providers.provider_connection_scope():
        with pytest.raises(providers.ProviderError):
            providers.ProviderClient('fuyao').request('/no-key')
        assert not clients and not calls
        responses.append({'code': 0, 'data': None})
        with pytest.raises(providers.ProviderError):
            providers.ProviderClient('fuyao', 'fixture').request('/missing')
        responses.extend([[], [], []])
        with pytest.raises(providers.ProviderError):
            providers.ProviderClient('fuyao', 'fixture').request('/invalid-format')
    assert len(calls) == 4 and clients[0].is_closed


def test_all_task_connections_close_on_cancel_or_unexpected_exception(transport):
    _, clients, _, _ = transport
    with pytest.raises(KeyboardInterrupt):
        with providers.provider_connection_scope():
            providers.ProviderClient('fuyao', 'fixture').request('/one')
            providers.ProviderClient('tickflow-public').request('/two')
            raise KeyboardInterrupt()
    assert len(clients) == 2 and all(client.is_closed for client in clients)


def test_connection_scope_budget_and_decorator_cleanup(transport):
    _, clients, _, _ = transport
    @providers.provider_connection_scope()
    def task():
        for index in range(8):
            providers.ProviderClient('fuyao', 'fixture-'+str(index)).request('/one')
        with pytest.raises(providers.ProviderError):
            providers.ProviderClient('fuyao', 'fixture-extra').request('/one')
    task()
    assert len(clients) == 8 and all(client.is_closed for client in clients)
    task()
    assert len(clients) == 16 and all(client.is_closed for client in clients)


def test_copied_context_cannot_reopen_completed_task_transport(transport):
    _, clients, _, _ = transport
    with providers.provider_connection_scope():
        providers.ProviderClient('fuyao', 'fixture').request('/one')
        copied = copy_context()
    with pytest.raises(providers.ProviderError):
        copied.run(lambda: providers.ProviderClient('fuyao', 'fixture').request('/late'))
    assert len(clients) == 1 and clients[0].is_closed


SECTOR_HISTORY = '/api/a-share-index/prices/historical'


def test_new_frozen_tail_policy_keeps_710_serial_requests_and_global_start_budget(transport):
    clock, clients, calls, _ = transport
    clock['latency'] = .1
    with providers.provider_connection_scope(), providers.sector_tail_request_scope(providers.SECTOR_TAIL_PACING_VERSION) as policy:
        for index in range(710):
            providers.ProviderClient('fuyao', 'fixture').request(SECTOR_HISTORY, {'thscode':str(index), 'interval':'1d'})
        assert policy.attempts == 710
    assert len(clients)==1 and clients[0].is_closed and len(calls)==710
    assert all(b[0]-a[0]>=.6-1e-9 for a,b in zip(calls,calls[1:]))
    assert calls[-1][0]-calls[0][0] == pytest.approx(709*.6)


@pytest.mark.parametrize('status,code',[(401,None),(403,None),(429,None),(200,2001),(200,2003),(200,4001)])
def test_new_tail_policy_stops_auth_permission_and_rate_refusals_without_retry(transport,status,code):
    _, clients, calls, responses = transport
    responses.append((status, {'code':code,'data':None}))
    with providers.provider_connection_scope(), providers.sector_tail_request_scope(providers.SECTOR_TAIL_PACING_VERSION) as policy:
        for _ in range(2):
            with pytest.raises(providers.ProviderError):
                providers.ProviderClient('fuyao','fixture').request(SECTOR_HISTORY)
        assert policy.attempts==1
    assert len(calls)==len(clients)==1 and clients[0].is_closed


def test_new_tail_policy_preserves_three_transient_attempts_and_other_endpoint_policy(transport):
    _, _, calls, responses=transport
    responses.extend([(500,{}),(503,{})])
    with providers.provider_connection_scope():
        adapter=providers.ProviderClient('fuyao','fixture')
        with providers.sector_tail_request_scope(providers.SECTOR_TAIL_PACING_VERSION) as policy:
            adapter.request(SECTOR_HISTORY)
            assert policy.attempts==3
            adapter.request('/api/a-share/prices/historical')
            assert policy.attempts==3
        adapter.request(SECTOR_HISTORY)
    assert [call[0] for call in calls]==pytest.approx([100,102,106,107.2,108.4])


@pytest.mark.parametrize('error_type',[ValueError,RuntimeError])
@pytest.mark.parametrize('during_retry',[False,True])
def test_tail_wait_guard_propagates_and_scope_exit_restores_legacy_interval(transport,error_type,during_retry):
    clock, _, calls, responses=transport
    if during_retry:responses.append((503,{}))
    def guard():
        if clock['now']>=100.2:raise error_type('controlled guard')
    with providers.provider_connection_scope():
        adapter=providers.ProviderClient('fuyao','fixture')
        with pytest.raises(error_type,match='controlled guard'):
            with providers.sector_tail_request_scope(providers.SECTOR_TAIL_PACING_VERSION,guard):
                adapter.request(SECTOR_HISTORY)
                adapter.request(SECTOR_HISTORY)
        adapter.request(SECTOR_HISTORY)
    assert len(calls)==2 and calls[1][0]-calls[0][0]==pytest.approx(1.2)


def test_legacy_policy_and_late_copied_scope_cannot_gain_new_pacing(transport):
    _, _, calls, responses=transport
    with providers.provider_connection_scope():
        adapter=providers.ProviderClient('fuyao','fixture')
        with providers.sector_tail_request_scope(None):
            responses.extend([{'code':4001,'data':None},{'code':4001,'data':None}])
            adapter.request(SECTOR_HISTORY)
        assert len(calls)==3
        with providers.sector_tail_request_scope(providers.SECTOR_TAIL_PACING_VERSION):
            copied=copy_context()
        with pytest.raises(providers.ProviderError,match='范围已结束'):
            copied.run(lambda:adapter.request(SECTOR_HISTORY))
        assert len(calls)==3
    for version in ('future',False,0,{},[]):
        with pytest.raises(ValueError):
            with providers.sector_tail_request_scope(version):pass
