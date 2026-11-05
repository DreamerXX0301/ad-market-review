"""隔离SQLite与合成来源，验证午评一次性、窗口和日线隔离。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
import gzip
from hashlib import sha256
import json
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import core
from app.adapters.providers import ProviderError
from app.routers.noon import router
from app.services import noon_service as service, source_service, task_service as tasks, noon_context
from app.services.noon_collection import CHINA, Collector, in_window, paged
from app.services.noon_analysis import board_summary, build_summary, normalize_quotes
from test_workspace import ws

DAY = '2026-10-28'
NOW = datetime(2026, 10, 28, 11, 45, tzinfo=CHINA)


def quote(code='600001.SH', **changes):
    return {'thscode': code, 'last_price': 11, 'prev_price': 10, 'open_price': 10,
            'high_price': 11, 'low_price': 10, 'price_change': 1,
            'price_change_ratio_pct': 10, 'volume': 100, 'turnover': 1100, **changes}


class FixtureSource:
    key = 'fixture-private-key'

    def __init__(self, clock):
        self.clock, self.calls = clock, []
        self.hook = None

    def request(self, path, params=None, *, before_attempt=None):
        if before_attempt is not None:
            before_attempt()
        params = params or {}
        # 获取来源时不存在持有中的业务写事务。
        with core.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
        self.calls.append((path, deepcopy(params)))
        if self.hook:
            changed = self.hook(path, params)
            if changed is not None:
                return changed
        stamp = int(self.clock[0].timestamp() * 1000)
        data = {'timestamp': stamp}
        if path.endswith('/calendar/trading-days'):
            data['item'] = [{'date_ms': int(datetime(2026, 10, n, tzinfo=CHINA).timestamp() * 1000)} for n in (27, 28)]
        elif path == '/api/a-share/prices/snapshot':
            data.update(total=3, item=[quote(), quote('000001.SZ'), quote('301001.SZ', last_price=None)])
        elif path == '/api/a-share-index/prices/snapshot':
            data.update(total=len(params['thscodes'].split(',')), item=[quote(code) for code in params['thscodes'].split(',')])
        elif path.endswith('/ths-index-list'):
            code = '881101.TI' if params['tag'] == 'industry' else '886042.TI'
            data['item'] = [{'thscode': code, 'name': '合成测试行业' if params['tag'] == 'industry' else '合成测试概念'}]
        elif path.endswith('limit-up-statistics'):
            data.update(trade_date=DAY, seal_rate=.5, open_stock_count=2, limit_up_stock_count=2,
                        touched_limit_up_stock_count=4, page_boards=[], exact_boards=[])
        elif path.endswith('hot-stock-list'):
            data['item'] = [{'thscode': '600001.SH', 'name': '测试股', 'rank': 1, 'heat': '100'}]
        elif 'limit-' in path and path.endswith('-pool'):
            data.update(pagination={'page': 1, 'pages': 1, 'size': 200, 'total': 1},
                        item=[quote(name='测试股', continue_day_cnt=2, seal_money=10000)])
        else:
            raise AssertionError('unexpected fixture request: ' + path)
        return {'code': 0, 'request_id': 'fixture-request', 'data': data}


@pytest.fixture
def noon(ws, monkeypatch):
    market, _ = ws
    clock = [NOW]
    source = FixtureSource(clock)
    monkeypatch.setattr(service, 'now_china', lambda: clock[0])
    monkeypatch.setattr(service, 'get_market', lambda: market)
    monkeypatch.setattr(service, '_configured', lambda connection: True)
    monkeypatch.setattr(source_service, 'client', lambda identifier: source)
    context = {'date': '2026-10-27', 'state': 'retained', 'snapshot_id': 'fixture-saved-review',
               'recorded_at_ms': int((NOW - timedelta(days=1)).timestamp() * 1000),
               'content_sha256': 'a' * 64, 'payload': {'original': '昨天已保存的合成判断'},
               'mainlines': [], 'observations': [], 'gaps': []}
    seen = []
    def previous(market, day, previous_day, known_at_ms, quotes):
        seen.append((day, previous_day, known_at_ms, deepcopy(quotes)))
        return deepcopy(context)
    monkeypatch.setattr(noon_context, 'build_previous_review', previous)
    app = FastAPI(); app.include_router(router)
    with core.connect() as connection:
        before = {'heads': [tuple(row) for row in connection.execute('SELECT * FROM published_head')],
                  'clock': [tuple(row) for row in connection.execute('SELECT * FROM business_clock')],
                  'strategy_count': connection.execute('SELECT count(*) FROM strategy').fetchone()[0]}
    return market, TestClient(app), source, clock, context, seen, before


def create(noon, key='fixture-noon-request'):
    return noon[1].post('/api/v1/market/noon/jobs', json={'date': DAY, 'request_key': key})


def execute(noon):
    response = create(noon)
    assert response.status_code == 200, response.text
    claimed = tasks.claim_next('fixture-noon-worker')
    assert claimed['job_id'] == response.json()['task_id']
    return claimed, service.execute_capture(claimed)


@pytest.mark.parametrize(('key', 'component', 'value'), [
    ('limit_up', 'seal_sample', 0), ('limit_down', 'limit_balance', 100), ('limit_break', 'seal_sample', 100),
])
def test_explicit_empty_event_pool_is_observed_and_old_snapshot_is_read_only(noon, key, component, value):
    _, client, source, _, _, _, _ = noon
    endpoint = service.NOON_EVENT_POOLS[key]
    source.hook = lambda path, _: ({
        'code': 0,
        'data': {'timestamp': int(NOW.timestamp() * 1000), 'item': [],
                 'pagination': {'page': 1, 'pages': 0, 'size': 200, 'total': 0}},
    } if path == endpoint else None)
    job, _ = execute(noon)
    saved_calls = list(source.calls)
    view = client.get('/api/v1/market/noon', params={'date': DAY}).json()['snapshot']
    pool = view['payload']['events'][key]
    assert (pool['state'], pool['count'], pool['items']) == ('observed', 0, [])
    if key == 'limit_up':
        assert pool['board_summary']['first_board_count'] == pool['board_summary']['multi_board_count'] == 0
        assert pool['board_summary']['max_confirmed'] is None
    assert next(row for row in view['workbench']['temperature']['components']
                if row['key'] == component)['value'] == value

    old_payload = deepcopy(view['payload'])
    old_payload['events'][key].update(state='unknown', count=None, reason='来源为空')
    old_text = service.canonical(old_payload).decode()
    old_sha = sha256(old_text.encode()).hexdigest()
    archived = {'trade_date': DAY, 'job_id': job['job_id'], 'payload_json': old_text,
                'content_sha256': old_sha, 'collected_start_at': view['collected_start_at'],
                'collected_end_at': view['collected_end_at'], 'snapshot_id': view['snapshot_id'],
                'quality': view['quality']}
    restored = service._snapshot_view(archived)
    assert restored['content_sha256'] == old_sha
    assert restored['payload']['events'][key]['state'] == 'unknown'
    restored_pool = restored['workbench']['verified_empty_event_pools'][key]
    assert (restored_pool['state'], restored_pool['count'], restored_pool['items']) == ('observed', 0, [])
    if key == 'limit_down':
        assert restored['workbench']['limit_down_source_pool'] == restored_pool
    assert next(row for row in restored['workbench']['temperature']['components']
                if row['key'] == component)['value'] == value
    from app.services.intraday_brief import _noon_anchor
    if key == 'limit_up':
        ladder = _noon_anchor(archived)['ladder']
        assert (ladder['state'], ladder['sealed_count'], ladder['highest_board']) == ('observed', 0, None)
    if key == 'limit_break':
        assert _noon_anchor(archived)['ladder']['broken_board'] == {'state': 'observed', 'count': 0}
    from app.services.intraday_board_changes import noon_frame
    baseline = noon_frame(archived)
    assert baseline['events'][key]['state'] == 'observed' and baseline['events'][key]['count'] == 0
    assert json.loads(archived['payload_json'])['events'][key]['state'] == 'unknown'

    receipt = core.raw_dir() / 'noon' / job['job_id'] / f'{key}_page_1.json.gz'
    receipt.write_bytes(receipt.read_bytes() + b'invalid')
    unverified = service._snapshot_view(archived)
    unverified_baseline = noon_frame(archived)
    assert unverified_baseline['events'][key]['state'] == 'unknown'
    assert unverified_baseline['events'][key]['count'] is None
    assert 'limit_down_source_pool' not in unverified['workbench']
    assert 'verified_empty_event_pools' not in unverified['workbench']
    assert next(row for row in unverified['workbench']['temperature']['components']
                if row['key'] == component)['value'] is None
    assert source.calls == saved_calls


@pytest.mark.parametrize('key', ['limit_up', 'limit_down', 'limit_break'])
@pytest.mark.parametrize('broken_field', ['code', 'timestamp', 'pages', 'before_window'])
def test_inconsistent_empty_event_pool_page_remains_unknown(noon, key, broken_field):
    _, client, source, _, _, _, _ = noon

    def reply(path, _):
        if path != service.NOON_EVENT_POOLS[key]:
            return None
        pagination = {'page': 1, 'pages': 1 if broken_field == 'pages' else 0, 'size': 200, 'total': 0}
        stamp = NOW.replace(hour=11, minute=30) if broken_field == 'before_window' else NOW
        return {'code': 502 if broken_field == 'code' else 0,
                'data': {'timestamp': None if broken_field == 'timestamp' else int(stamp.timestamp() * 1000),
                         'item': [], 'pagination': pagination}}

    source.hook = reply
    execute(noon)
    snapshot = client.get('/api/v1/market/noon', params={'date': DAY}).json()['snapshot']
    assert snapshot['payload']['events'][key]['state'] == 'unknown'
    assert snapshot['payload']['events'][key]['count'] is None
    assert 'limit_down_source_pool' not in snapshot['workbench']
    assert 'verified_empty_event_pools' not in snapshot['workbench']


def test_empty_event_pools_have_no_temperature_denominator(noon):
    _, client, source, _, _, _, _ = noon
    source.hook = lambda path, _: ({
        'code': 0,
        'data': {'timestamp': int(NOW.timestamp() * 1000), 'item': [],
                 'pagination': {'page': 1, 'pages': 0, 'size': 200, 'total': 0}},
    } if path in service.NOON_EVENT_POOLS.values() else None)
    execute(noon)
    snapshot = client.get('/api/v1/market/noon', params={'date': DAY}).json()['snapshot']
    for key in service.NOON_EVENT_POOLS:
        pool = snapshot['payload']['events'][key]
        assert (pool['state'], pool['count'], pool['items']) == ('observed', 0, [])
    for row in snapshot['workbench']['temperature']['components']:
        if row['key'] in ('seal_sample', 'limit_balance'):
            assert row['value'] is None and '分母为零' in row['reason']


def test_roundtrip_keeps_missing_times_freezes_review_and_never_publishes_daily(noon, monkeypatch):
    _, client, source, _, context, seen, before = noon
    job, result = execute(noon)
    assert result['quality'] == 'partial'
    view = client.get('/api/v1/market/noon', params={'date': DAY}).json()
    payload = view['snapshot']['payload']
    assert view['snapshot']['workbench']['version'] == 'noon-workbench-v1'
    original_content_sha = view['snapshot']['content_sha256']
    assert view['state'] == 'available' and view['locked_for_day'] and not view['can_capture']
    assert payload['quote_as_of'] is None
    assert payload['summary']['breadth']['valid'] == 2
    assert payload['summary']['coverage']['missing_price_count'] == 1
    assert payload['events']['limit_up']['count'] == 1
    assert payload['events']['statistics']['limit_up_stock_count'] == 2
    assert any('分别列示' in x for x in payload['analysis']['risks'])
    assert len(payload['indices']) == 4 and len(payload['sectors']) == 2
    assert all(row['quote_at'] is None for row in payload['stocks'])
    assert seen[0][1:3] == ('2026-10-27', int(NOW.timestamp() * 1000))
    assert payload['previous_review']['payload'] == context['payload']
    context['payload']['original'] = '后来修改的合成判断'
    monkeypatch.setattr(source, 'request', lambda *_args, **_kwargs: pytest.fail('GET must not fetch'))
    monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('GET workbench must not read current market'))
    later = client.get('/api/v1/market/noon', params={'date': DAY}).json()
    assert later['snapshot']['payload'] == payload
    assert later['snapshot']['content_sha256'] == original_content_sha
    assert service.execute_capture(job)['snapshot_id'] == result['snapshot_id']
    listed = client.get('/api/v1/market/noon/dates').json()
    assert listed['today'] == DAY and listed['items'][0]['date'] == DAY
    for item in payload['sources']:
        if item.get('path'):
            data = (core.raw_dir() / item['path']).read_bytes()
            assert sha256(data).hexdigest() == item['stored_sha256']
            raw = gzip.decompress(data) if item['path'].endswith('.gz') else data
            assert sha256(raw).hexdigest() == item['sha256']
    with core.connect() as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM published_head')] == before['heads']
        assert [tuple(row) for row in connection.execute('SELECT * FROM business_clock')] == before['clock']
        assert connection.execute('SELECT count(*) FROM strategy').fetchone()[0] == before['strategy_count']
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE market_noon_snapshot SET quality='complete'")


def test_request_replay_and_daily_lock_survive_failure_and_cancel(noon):
    first = create(noon)
    assert first.status_code == 200
    same = create(noon)
    assert same.json()['task_id'] == first.json()['task_id']
    assert create(noon, 'another-key').status_code == 409
    identifier = first.json()['task_id']
    tasks.change_task(identifier, 'cancel')
    assert create(noon, 'after-cancel').status_code == 409
    assert noon[1].get('/api/v1/market/noon', params={'date': DAY}).json()['state'] == 'cancelled'
    assert create(noon).json()['task_id'] == identifier


def test_stock_quote_reads_only_the_retained_noon_snapshot(noon, monkeypatch):
    _, client, source, _, _, _, _ = noon
    _, result = execute(noon)
    monkeypatch.setattr(source, 'request', lambda *_args, **_kwargs: pytest.fail('GET must not fetch'))
    monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('GET must not read current market'))
    snapshot_id = result['snapshot_id']
    endpoint = f'/api/v1/market/noon/{snapshot_id}/stocks/'
    response = client.get(endpoint + '600001.SH/quote')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['snapshot_id'] == snapshot_id and body['trade_date'] == DAY
    assert body['previous_trade_date'] == '2026-10-27'
    assert body['collected_start_at'].startswith(DAY)
    assert body['collected_end_at'].startswith(DAY)
    assert body['reason'] is None
    assert body['quote'] == {'instrument_id': '600001.SH', 'price_basis': 'none',
                             'open': 10, 'high': 11, 'low': 10, 'latest_price': 11,
                             'prev_close': 10, 'volume': 100, 'amount': 1100, 'change_pct': 10,
                             'limit_up_state': 'unknown', 'limit_down_state': 'unknown', 'pattern_events': []}
    for code in ('301001.SZ', '600002.SH'):
        missing = client.get(endpoint + code + '/quote')
        assert missing.status_code == 200
        assert missing.json()['quote'] is None and missing.json()['reason']
    assert client.get(endpoint + '600001/quote').status_code == 422
    absent = client.get(f'/api/v1/market/noon/noon_{"0" * 32}/stocks/600001.SH/quote')
    assert absent.status_code == 404
    with core.connect() as connection:
        connection.execute('DROP TRIGGER retain_noon_snapshot_update')
        connection.execute("UPDATE market_noon_snapshot SET payload_json=payload_json || ' '")
    assert client.get(endpoint + '600001.SH/quote').status_code == 409


def test_parallel_create_consumes_one_permanent_daily_slot(noon):
    def submit(key):
        try:
            return service.create_capture({'date': DAY, 'request_key': key})['task_id']
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ['first-parallel', 'second-parallel']))
    assert results.count(409) == 1
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1


@pytest.mark.parametrize('at,day', [(NOW.replace(hour=11, minute=29), DAY),
    (NOW.replace(hour=11, minute=30), DAY),
    (NOW.replace(hour=11, minute=30, second=59, microsecond=999999), DAY),
    (NOW.replace(hour=13), DAY), (NOW, '2026-10-27'), (NOW.replace(day=31), '2026-10-31')])
def test_window_weekend_and_history_cannot_create_or_fetch(noon, at, day):
    noon[3][0] = at
    response = noon[1].post('/api/v1/market/noon/jobs', json={'date': day, 'request_key': 'wrong-window'})
    assert response.status_code == 409
    assert noon[2].calls == []
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 0


@pytest.mark.parametrize('at,allowed', [
    (NOW.replace(hour=11, minute=30, second=0), False),
    (NOW.replace(hour=11, minute=30, second=59, microsecond=999999), False),
    (NOW.replace(hour=11, minute=31, second=0), True),
    (NOW.replace(hour=12, minute=59, second=59, microsecond=999999), True),
    (NOW.replace(hour=13, minute=0, second=0), False),
])
def test_1131_capture_boundary_is_shared_by_view_and_collector(at, allowed):
    window = service.capture_window(DAY, at)
    assert in_window(DAY, at) is allowed and window['allowed'] is allowed
    assert window['start_at'] == DAY + 'T11:31:00+08:00'
    assert window['end_at'] == DAY + 'T13:00:00+08:00'


def test_1131_first_minute_allows_one_submission_without_implicit_fetch(noon):
    noon[3][0] = NOW.replace(hour=11, minute=31, second=0)
    before = noon[1].get('/api/v1/market/noon?date=' + DAY).json()
    assert before['capture_window']['allowed'] and before['can_capture']
    assert create(noon).status_code == 200
    assert create(noon, 'second-1131-request').status_code == 409
    assert noon[2].calls == []


def test_worker_before_1131_never_opens_a_remote_request(noon):
    assert create(noon).status_code == 200
    job = tasks.claim_next('fixture-early-noon-worker')
    noon[3][0] = NOW.replace(hour=11, minute=30, second=59)
    with pytest.raises(ValueError, match='窗口'):
        service.execute_capture(job)
    assert noon[2].calls == []
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_snapshot').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1


def test_unknown_fields_and_invalid_dates_are_rejected(noon):
    client = noon[1]
    assert client.post('/api/v1/market/noon/jobs', json={'date': DAY, 'payload': {}}).status_code == 422
    assert client.get('/api/v1/market/noon?date=2026-02-30').status_code == 422
    for value in (False, True, '', None):
        assert client.post('/api/v1/market/noon/jobs', json={'date': value, 'request_key': 'invalid-date'}).status_code == 422


def test_source_holiday_stops_before_prices_and_keeps_daily_lock(noon):
    def hook(path, _params):
        if path.endswith('/trading-days'):
            return {'code': 0, 'data': {'item': [{'date': '20261027'}]}}
    noon[2].hook = hook
    assert create(noon).status_code == 200
    job = tasks.claim_next('fixture-noon-worker')
    with pytest.raises(ValueError, match='交易日历'):
        service.execute_capture(job)
    tasks.finish(job['job_id'], 'failed', '合成节假日拒绝')
    assert len(noon[2].calls) == 1
    assert create(noon, 'holiday-second').status_code == 409
    assert noon[1].get('/api/v1/market/noon?date='+DAY).json()['snapshot'] is None


def test_crossing_1300_keeps_only_earlier_window_responses(noon):
    def hook(path, _params):
        if path == '/api/a-share-index/prices/snapshot':
            noon[3][0] = NOW.replace(hour=13, minute=0)
    noon[2].hook = hook
    _, _ = execute(noon)
    payload = noon[1].get('/api/v1/market/noon?date='+DAY).json()['snapshot']['payload']
    assert payload['summary']['breadth']['valid'] == 2
    assert all(row['latest_price'] is None for row in payload['indices'])
    assert datetime.fromisoformat(payload['collected_end_at']).hour < 13
    assert any(row['domain'] == 'capture_window' for row in payload['gaps'])
    rejected = [row for row in payload['sources'] if row.get('state') == 'outside_window']
    assert len(rejected) == 1 and 'path' not in rejected[0]


def test_stale_prices_are_not_counted_as_valid_noon_quotes(noon):
    def hook(path, _params):
        if path == '/api/a-share/prices/snapshot':
            return {'code': 0, 'data': {'timestamp': int((NOW-timedelta(days=1)).timestamp()*1000),
                                      'total': 1, 'item': [quote()]}}
    noon[2].hook = hook
    execute(noon)
    payload = noon[1].get('/api/v1/market/noon?date='+DAY).json()['snapshot']['payload']
    assert payload['stocks'] == []
    assert payload['summary']['breadth']['up'] is None
    assert any(row.get('state') == 'invalid_source_time' for row in payload['sources'])


def test_all_price_sources_failed_never_creates_empty_valid_snapshot(noon):
    def hook(path, _params):
        if '/prices/snapshot' in path:
            raise ProviderError('fixture unavailable', 503)
    noon[2].hook = hook
    assert create(noon).status_code == 200
    job = tasks.claim_next('fixture-noon-worker')
    with pytest.raises(ValueError, match='任何有效'):
        service.execute_capture(job)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_snapshot').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM market_noon_capture').fetchone()[0] == 1


def test_escaped_secret_echo_is_rejected_before_raw_or_payload_storage(noon):
    secret = 'fixture-"private\\key'
    noon[2].key = secret
    def hook(path, _params):
        if path == '/api/a-share/prices/snapshot':
            return {'code': 0, 'data': {'timestamp': int(NOW.timestamp()*1000), 'item': [quote()],
                                      'nested': [{secret: secret}]}}
    noon[2].hook = hook
    execute(noon)
    for file in (core.raw_dir()/'noon').rglob('*'):
        if file.is_file():
            data = file.read_bytes()
            if file.suffix == '.gz': data = gzip.decompress(data)
            assert secret.encode() not in data
            assert json.dumps(secret)[1:-1].encode() not in data


def test_pause_during_remote_request_prevents_snapshot_publication(noon):
    assert create(noon).status_code == 200
    job = tasks.claim_next('fixture-noon-worker')
    def hook(path, _params):
        if path == '/api/a-share/prices/snapshot':
            tasks.change_task(job['job_id'], 'pause')
    noon[2].hook = hook
    with pytest.raises(tasks.TaskPaused):
        service.execute_capture(job)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_snapshot').fetchone()[0] == 0


def test_same_worker_pause_resume_attempt_rejects_old_inflight_response(noon, monkeypatch):
    from app.services import sync_clock
    monkeypatch.setattr(sync_clock, 'local_now', lambda: NOW)
    assert create(noon).status_code == 200
    original = tasks.claim_next('fixture-same-worker')
    resumed = []
    def hook(path, _params):
        if path == '/api/a-share/prices/snapshot':
            tasks.change_task(original['job_id'], 'pause')
            tasks.change_task(original['job_id'], 'resume')
            resumed.append(tasks.claim_next('fixture-same-worker'))
    noon[2].hook = hook
    with pytest.raises(tasks.TaskCancelled):
        service.execute_capture(original)
    assert resumed[0]['job_id'] == original['job_id']
    assert resumed[0]['lease_owner'] != original['lease_owner']
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_noon_snapshot').fetchone()[0] == 0


def test_unstable_pagination_never_claims_complete():
    class Reader:
        def __init__(self): self.calls = 0; self.gaps = []
        def request(self, *_args, **_kwargs):
            self.calls += 1
            return {'data': {'total': 1000+self.calls, 'item': [quote()]}}
        def gap(self, domain, reason): self.gaps.append((domain, reason))
    reader = Reader()
    rows, total, complete = paged(reader, 'market', '/fixture', market=True)
    assert rows and total == 1001 and not complete
    assert any('变化' in reason for _, reason in reader.gaps)


def test_recovery_checks_saved_sha_without_refetching_same_window(noon):
    reader = Collector('job_'+'a'*32, DAY, noon[2], lambda: None, lambda: NOW)
    first = reader.request('quotes', '/api/a-share/prices/snapshot', {'limit': 1000})
    calls = len(noon[2].calls)
    replay = Collector('job_'+'a'*32, DAY, noon[2], lambda: None, lambda: NOW)
    assert replay.request('quotes', '/api/a-share/prices/snapshot', {'limit': 1000}) == first
    assert len(noon[2].calls) == calls
    file = core.raw_dir() / replay.records[0]['path']; file.write_bytes(b'broken fixture')
    with pytest.raises(ValueError, match='SHA'):
        replay.request('quotes', '/api/a-share/prices/snapshot', {'limit': 1000})


def test_quote_identity_conflicts_are_quarantined_and_missing_never_negative():
    rows = normalize_quotes([quote(), quote(), quote(thscode=[]), quote(thscode={}), quote('000001.SZ')])
    assert [row['instrument_id'] for row in rows] == ['000001.SZ']
    summary = build_summary(rows, 0, False)
    assert summary['breadth']['missing'] == 0 and summary['coverage']['missing_price_count'] == 0
    sectors = normalize_quotes([quote('881101.TI'), quote('886000.TI')], sectors={'881101.TI': {'name': '测试', 'kind': 'industry'}})
    assert [row['instrument_id'] for row in sectors] == ['881101.TI']


def test_board_height_uses_only_verified_integer_field_not_days_boards_text():
    value = board_summary({'state': 'partial', 'items': [{'continue_day_cnt': 1},
        {'continue_day_cnt': 2}, {'continue_day_cnt': 3}, {'continue_day_cnt': None, 'continue_day_text': '7天5板'},
        {'continue_day_cnt': True}, {'continue_day_cnt': -1}]})
    assert value['max_confirmed'] == 3
    assert value['first_board_count'] == 1 and value['multi_board_count'] == 2
    assert value['height_unknown_count'] == 3 and value['reason']
