"""隔离业务库及合成来源，验证竞价时点、授权与原件留存边界。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import core, worker
from app.adapters.providers import ProviderError
from app.routers import auction, platform
from app.services import auction_service as service, source_service, task_service as tasks
from app.services.auction_collection import CHINA, Collector, WindowExpired, canonical, in_window
from test_workspace import ws

DAY = '2026-10-29'
PREVIOUS = '2026-10-28'
NOW = datetime(2026, 10, 29, 9, 26, tzinfo=CHINA)


def quote(code='600001.SH', **changes):
    return {'thscode': code, 'ticker': code[:6], 'name': '合成测试股', 'auction_price': 11,
            'pre_close_price': 10, 'auction_pct': 10, 'auction_volume': 100,
            'auction_amount': 110000, 'auction_turnover_pct': .5,
            'auction_yesterday_ratio_pct': 20, 'auction_volume_ratio': 2, **changes}


class FixtureSource:
    key = 'fixture-private-key'

    def __init__(self, clock):
        self.clock, self.calls, self.hook = clock, [], None

    def request(self, path, params=None, *, before_attempt=None):
        if before_attempt:
            before_attempt()
        # 来源请求不得包在SQLite写事务内。
        with core.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
        params = params or {}
        self.calls.append((path, deepcopy(params)))
        if self.hook:
            result = self.hook(path, params)
            if result is not None:
                return result
        data = {'timestamp': int(self.clock[0].timestamp() * 1000)}
        if path.endswith('/calendar/trading-days'):
            data['item'] = [{'date': day.replace('-', '')} for day in (PREVIOUS, DAY)]
        elif path.endswith('/auction/snapshot'):
            codes = params['thscodes'].split(',')
            data.update(auction_phase='final', data_status='ready', total=len(codes), item=[quote(code) for code in codes])
        elif path.endswith('/auction/short-term-benchmark'):
            data.update(date=DAY, date_ms=int(NOW.replace(hour=0, minute=0).timestamp() * 1000),
                        item=[{'thscode': '600001.SH', 'auction_pct': 10, 'tags': ['合成测试标签']}])
        else:
            raise AssertionError('unexpected fixture endpoint ' + path)
        return {'code': 0, 'data': data}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    clock = [NOW]
    source = FixtureSource(clock)
    market = SimpleNamespace(instruments=lambda **_kwargs: [
        {'instrument_id': code, 'name': '合成测试' + code} for code in ('600001.SH', '000001.SZ')])
    previous = {'date': PREVIOUS, 'state': 'retained', 'snapshot_id': 'fixture-saved-review',
                'recorded_at_ms': int((NOW - timedelta(days=1)).timestamp() * 1000),
                'content_sha256': 'a' * 64, 'payload': {'original': '昨天已保存的合成判断'},
                'summary': [], 'mainlines': [], 'gaps': [], 'event_cohorts': {
                    'limit_up': {'date': PREVIOUS, 'state': 'observed_sample', 'verified_count': 1,
                                 'items': [{'instrument_id': '600001.SH', 'name': '合成涨停样本', 'previous_board_count': 3}]},
                    'limit_break': {'date': PREVIOUS, 'state': 'observed_sample', 'verified_count': 1,
                                    'items': [{'instrument_id': '000001.SZ', 'name': '合成炸板样本'}]}}}
    monkeypatch.setattr(service, 'now_china', lambda: clock[0])
    monkeypatch.setattr(tasks, 'now_ms', lambda: int(clock[0].timestamp() * 1000))
    monkeypatch.setattr(service, 'get_market', lambda: market)
    monkeypatch.setattr(service, '_configured', lambda _connection: True)
    monkeypatch.setattr(service, '_previous_context', lambda *_args: deepcopy(previous))
    monkeypatch.setattr(tasks, 'initialization_state', lambda *_args: {'main_published': True})
    monkeypatch.setattr(source_service, 'client', lambda _identifier: source)
    app = FastAPI(); app.include_router(auction.router); app.include_router(platform.router)
    return SimpleNamespace(client=TestClient(app), clock=clock, source=source, previous=previous, market=market, monkeypatch=monkeypatch)


def create(isolated, key='fixture-auction', **changes):
    return isolated.client.post('/api/v1/market/auction/jobs', json={'date': DAY, 'request_key': key, **changes})


def claim(isolated, **changes):
    response = create(isolated, **changes)
    assert response.status_code == 200, response.text
    claimed = tasks.claim_next('fixture-auction-worker')
    assert claimed['job_id'] == response.json()['task_id']
    return claimed


def test_roundtrip_preserves_sha_and_sqlite_originals_but_get_only_returns_metadata(isolated):
    with core.connect() as connection:
        before = {table: connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                  for table in ('published_head', 'business_clock', 'strategy', 'tracking_round')}
    job = claim(isolated)
    worker.dispatch(job)
    view = isolated.client.get('/api/v1/market/auction', params={'date': DAY}).json()
    assert view['state'] == 'available' and not view['can_capture'] and not view['can_retry']
    snapshot = view['snapshot']; payload = snapshot['payload']
    assert snapshot['quality'] == 'complete' and snapshot['evidence_count'] == 3
    assert 'evidence' not in payload
    assert payload['quotes']['summary'] == {'requested': 2, 'valid': 2, 'missing': 0,
        'up': 2, 'down': 0, 'flat': 0, 'median_pct': 10, 'amount': 220000,
        'amount_valid': 2, 'scope': payload['quotes']['summary']['scope']}
    assert payload['previous_date'] == PREVIOUS and payload['quotes']['items'][0]['quote_at'] is None
    assert [item['instrument_id'] for item in payload['universe']['items']] == ['600001.SH', '000001.SZ']
    assert all('body' not in item for item in payload['sources'])
    with core.connect() as connection:
        row = connection.execute('SELECT payload_json,content_sha256 FROM market_auction_snapshot').fetchone()
        stored = json.loads(row['payload_json'])
        assert sha256(row['payload_json'].encode()).hexdigest() == row['content_sha256']
        assert all(sha256(canonical(item['body'])).hexdigest() == item['sha256'] for item in stored['evidence'])
        assert before == {table: connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in before}
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE market_auction_snapshot SET quality='partial'")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute('DELETE FROM market_auction_snapshot')
    assert list(core.raw_dir().iterdir()) == []
    isolated.monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('GET cannot read current market'))
    isolated.monkeypatch.setattr(source_service, 'client', lambda *_args: pytest.fail('GET cannot fetch'))
    again = isolated.client.get('/api/v1/market/auction', params={'date': DAY}).json()
    assert again['snapshot'] == snapshot
    assert isolated.client.get('/api/v1/market/auction/dates').json()['dates'] == [DAY]
    assert service.execute_capture(job)['snapshot_id'] == snapshot['snapshot_id']


def test_same_key_replay_and_concurrent_new_keys_never_create_second_job(isolated):
    first = create(isolated)
    assert create(isolated).json()['task_id'] == first.json()['task_id']
    assert create(isolated, instrument_ids=['600001.SH']).status_code == 409
    assert create(isolated, key='different-key').status_code == 409
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_capture').fetchone()[0] == 1
    # 新的空库下两个并发键也由独立日台账唯一约束守住。


def test_parallel_daily_submission_only_accepts_one(isolated):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda key: create(isolated, key=key), ['a', 'b']))
    assert sorted(response.status_code for response in responses) == [200, 409]


@pytest.mark.parametrize('instruments', ['600001.SH', ['600001'], ['600001.HK'], ['６００００１.SH'], [True], ['600001.SH'] * 101])
def test_explicit_input_rejects_invalid_type_market_or_raw_token_count(isolated, instruments):
    assert create(isolated, instrument_ids=instruments).status_code == 422


def test_explicit_list_never_expands_to_previous_events(isolated):
    job = claim(isolated, instrument_ids=['000001.SZ', '000001.SZ'])
    service.execute_capture(job)
    payload = service.read_view(DAY)['snapshot']['payload']
    assert payload['universe']['mode'] == 'explicit'
    assert payload['universe']['requested'] == 1
    assert isolated.source.calls[1][1]['thscodes'] == '000001.SZ'


def test_saved_yesterday_watchlist_observations_and_mainlines_precede_current_watchlist(isolated):
    isolated.previous['payload']['watchlist'] = {'stocks': [{'instrument_id': '000008.SZ', 'trigger': '原计划触发条件',
                                                          'invalidation': '原计划证伪条件'}]}
    isolated.previous['observations'] = [{'instrument_id': '000009.SZ', 'name': '原关注', 'latest_price': 999,
                                         'change_pct': 99, 'reason': '午评临时缺口措辞'}]
    isolated.previous['mainlines'] = [{'instrument_id': '000010.SZ', 'instrument_name': '原主线股', 'recorded_at_ms': 1}]
    with core.connect() as connection:
        connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
            added_business_date,event_basis_json,created_at_ms) VALUES('000002.SZ','stock','合成测试',?,'{}',?)''',
            (PREVIOUS, int(NOW.timestamp() * 1000) - 1))
    job = claim(isolated); service.execute_capture(job)
    payload = service.read_view(DAY)['snapshot']['payload']
    assert [row['instrument_id'] for row in payload['universe']['items']] == [
        '000008.SZ', '000009.SZ', '000010.SZ', '000002.SZ', '600001.SH', '000001.SZ']
    assert payload['previous_review']['payload']['watchlist']['stocks'][0]['trigger'] == '原计划触发条件'
    assert 'latest_price' not in payload['previous_review']['observations'][0]
    assert 'reason' not in payload['previous_review']['observations'][0]


def test_explicit_candidate_links_yesterday_height_and_reasons_without_adding_names(isolated):
    isolated.previous['payload']['watchlist'] = {'stocks': [{'instrument_id': '600001.SH'}, {'instrument_id': '000008.SZ'}]}
    job = claim(isolated, instrument_ids=['600001.SH']); service.execute_capture(job)
    universe = service.read_view(DAY)['snapshot']['payload']['universe']
    assert universe['requested'] == 1 and universe['total_candidates'] == 1
    assert universe['items'][0]['previous_board_count'] == 3
    assert universe['items'][0]['reasons'] == ['用户明确指定', '严格前日已留存复盘关注股', '严格前日已核验涨停']


def test_watchlist_precedes_events_and_late_watchlist_is_excluded(isolated):
    with core.connect() as connection:
        for code, stamp in [('000002.SZ', int(NOW.timestamp() * 1000) - 1), ('000003.SZ', int(NOW.timestamp() * 1000) + 1)]:
            connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
                added_business_date,event_basis_json,created_at_ms) VALUES(?,'stock','合成测试',?,'{}',?)''', (code, PREVIOUS, stamp))
    job = claim(isolated)
    service.execute_capture(job)
    values = service.read_view(DAY)['snapshot']['payload']['universe']['items']
    assert [row['instrument_id'] for row in values] == ['000002.SZ', '600001.SH', '000001.SZ']
    assert values[0]['reasons'] == ['采集开始前已加入自选']


def test_default_universe_freezes_top100_and_reports_omitted(isolated):
    with core.connect() as connection:
        for index in range(105):
            connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
                added_business_date,event_basis_json,created_at_ms) VALUES(?,'stock','合成测试',?,'{}',?)''',
                (f'{index:06d}.SZ', PREVIOUS, int(NOW.timestamp() * 1000) - index))
    job = claim(isolated)
    service.execute_capture(job)
    universe = service.read_view(DAY)['snapshot']['payload']['universe']
    assert universe['requested'] == 100 and len(universe['items']) == 100
    assert universe['total_candidates'] == 106 and universe['omitted'] == 6
    assert len(isolated.source.calls[1][1]['thscodes'].split(',')) == 100


def test_not_ready_numeric_fields_fail_without_success_then_retry_same_job_keeps_frozen_inputs(isolated):
    def not_ready(path, _params):
        if path.endswith('/auction/snapshot'):
            return {'code': 0, 'data': {'auction_phase': 'closed', 'data_status': 'not_ready',
                'timestamp': int(NOW.timestamp() * 1000), 'total': 1, 'item': [quote()]}}
    isolated.source.hook = not_ready
    job = claim(isolated)
    with pytest.raises(ValueError, match='尚未就绪'):
        service.execute_capture(job)
    tasks.finish(job['job_id'], 'failed', error='来源尚未就绪', lease_owner=job['lease_owner'])
    frozen = tasks.task_view(job['job_id'])['parameters']['frozen_inputs']
    assert service.read_view(DAY)['can_retry'] and service.read_view(DAY)['snapshot'] is None
    isolated.previous['event_cohorts']['limit_up']['items'] = []
    isolated.previous['payload'] = {'original': '今天后来改写，不能进入旧任务'}
    isolated.source.hook = None
    retried = isolated.client.post('/api/v1/tasks/' + job['job_id'] + '/retry')
    assert retried.status_code == 200, retried.text
    assert retried.json()['task_id'] == job['job_id']
    assert create(isolated, key='new-key-after-failure').status_code == 409
    assert create(isolated).json()['task_id'] == job['job_id']
    isolated.monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('retry cannot rebuild frozen market inputs'))
    resumed = tasks.claim_next('fixture-retry-worker')
    worker.dispatch(resumed)
    view = service.read_view(DAY)
    assert view['snapshot']['payload']['previous_review']['payload'] == frozen['previous_review']['payload']
    assert tasks.task_view(job['job_id'])['parameters']['frozen_inputs'] == frozen
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1
        current = connection.execute('SELECT parameters_json,parameters_hash,logical_key FROM job').fetchone()
        parameters = json.loads(current['parameters_json'])
        assert current['parameters_hash'] == core.digest(parameters)
        assert current['logical_key'] == core.digest({'kind': 'source_check', 'target_date': DAY, 'parameters': parameters})


@pytest.mark.parametrize('hour,minute,day', [(9, 24, DAY), (9, 30, DAY), (9, 26, PREVIOUS), (9, 26, '2026-10-31')])
def test_window_only_accepts_today_after925_before930(isolated, hour, minute, day):
    isolated.clock[0] = NOW.replace(hour=hour, minute=minute)
    response = isolated.client.post('/api/v1/market/auction/jobs', json={'date': day})
    assert response.status_code == 409


def test_source_holiday_does_not_request_auction(isolated):
    isolated.source.hook = lambda path, params: {'code': 0, 'data': {'item': [{'date': PREVIOUS.replace('-', '')}]}} if path.endswith('/calendar/trading-days') else None
    job = claim(isolated)
    with pytest.raises(ValueError, match='交易日历不含今天'):
        service.execute_capture(job)
    assert len(isolated.source.calls) == 1 and service.read_view(DAY)['snapshot'] is None


def test_cancel_is_terminal_pause_needs_manual_resume_and_expired_resume_or_retry_fails(isolated):
    job = claim(isolated)
    tasks.change_task(job['job_id'], 'pause')
    assert tasks.claim_next('not-auto-resumed') is None
    isolated.clock[0] = NOW.replace(minute=30)
    assert isolated.client.post('/api/v1/tasks/' + job['job_id'] + '/resume').status_code == 409
    isolated.clock[0] = NOW
    assert tasks.change_task(job['job_id'], 'resume')['task_id'] == job['job_id']
    tasks.change_task(job['job_id'], 'cancel')
    assert isolated.client.post('/api/v1/tasks/' + job['job_id'] + '/retry').status_code == 409
    assert create(isolated, key='after-cancel').status_code == 409
    assert service.read_view(DAY)['state'] == 'cancelled'


def test_failed_task_cannot_retry_after_window_or_next_day(isolated):
    job = claim(isolated)
    tasks.finish(job['job_id'], 'failed', error='fixture', lease_owner=job['lease_owner'])
    isolated.clock[0] = NOW.replace(minute=30)
    assert isolated.client.post('/api/v1/tasks/' + job['job_id'] + '/retry').status_code == 409
    isolated.clock[0] = NOW + timedelta(days=1)
    assert isolated.client.post('/api/v1/tasks/' + job['job_id'] + '/retry').status_code == 409
    assert not service.read_view(DAY)['can_retry']


def test_response_arriving_after930_is_never_saved(isolated):
    def late(path, params):
        if path.endswith('/auction/snapshot'):
            isolated.clock[0] = NOW.replace(minute=30)
    isolated.source.hook = late
    job = claim(isolated)
    with pytest.raises(WindowExpired):
        service.execute_capture(job)
    assert service.read_view(DAY)['snapshot'] is None


def test_valid_auction_can_be_saved_with_unknown_benchmark_at_window_end(isolated):
    def late_benchmark(path, params):
        if path.endswith('/short-term-benchmark'):
            isolated.clock[0] = NOW.replace(minute=30)
    isolated.source.hook = late_benchmark
    job = claim(isolated)
    result = service.execute_capture(job)
    assert result['quality'] == 'partial'
    payload = service.read_view(DAY)['snapshot']['payload']
    assert payload['quotes']['summary']['valid'] == 2 and payload['benchmark']['state'] == 'unknown'
    assert service.read_view(DAY)['snapshot']['evidence_count'] == 2


def test_get_checks_stored_content_sha(isolated):
    job = claim(isolated); service.execute_capture(job)
    with core.connect() as connection:
        connection.execute('DROP TRIGGER retain_auction_snapshot_update')
        connection.execute("UPDATE market_auction_snapshot SET content_sha256=?", ('b' * 64,))
    assert isolated.client.get('/api/v1/market/auction', params={'date': DAY}).status_code == 409


@pytest.mark.parametrize('mode', ['secret', 'too_large', 'provider_error'])
def test_rejected_source_response_never_enters_snapshot_or_task_parameters(isolated, mode):
    def invalid(path, params):
        if path.endswith('/auction/snapshot'):
            if mode == 'provider_error':
                raise ProviderError('unsafe ' + isolated.source.key, 403)
            return {'code': 0, 'data': {'echo': isolated.source.key if mode == 'secret' else 'x' * (1024 * 1024 + 1)}}
    isolated.source.hook = invalid
    job = claim(isolated)
    with pytest.raises(ValueError, match='尚未就绪'):
        service.execute_capture(job)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 0
        assert isolated.source.key not in connection.execute('SELECT parameters_json FROM job').fetchone()[0]


def test_collector_total_evidence_budget_and_transport_retry_guard(isolated):
    clock = isolated.clock
    source = SimpleNamespace(key='private', request=lambda *_args, **_kwargs: {'code': 0, 'data': {'padding': 'x' * 800000}})
    collector = Collector(DAY, source, lambda: None, lambda: clock[0])
    assert collector.request('a', '/a') is not None
    assert collector.request('b', '/b') is not None
    assert collector.request('c', '/c') is None
    assert len(collector.evidence) == 2 and collector.sources[-1]['state'] == 'capacity_rejected'
    def transport(path, params, before_attempt):
        clock[0] = NOW.replace(minute=30)
        before_attempt()
        pytest.fail('expired transport retry cannot proceed')
    source.request = transport
    clock[0] = NOW
    second = Collector(DAY, source, lambda: None, lambda: clock[0])
    with pytest.raises(WindowExpired):
        second.request('auction', '/auction')
    assert second.evidence == []


def test_expired_lease_uses_new_owner_and_stale_worker_cannot_publish(isolated):
    job = claim(isolated)
    with core.connect() as connection:
        connection.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?', (tasks.now_ms() - 1, job['job_id']))
    recovered = tasks.claim_next('fixture-auction-worker')
    assert recovered['lease_owner'] != job['lease_owner']
    with pytest.raises(tasks.TaskCancelled):
        service.execute_capture(job)
    worker.dispatch(recovered)
    assert service.read_view(DAY)['state'] == 'available'


def test_assembly_registers_auction_before_generic_market_catchall(isolated):
    from app.main import app
    response = TestClient(app).get('/api/v1/market/auction', params={'date': DAY})
    assert response.status_code == 200 and response.json()['state'] == 'not_collected'


def retain_review(day, stamp, title, code='000008.SZ'):
    fingerprint = str(stamp).zfill(64)
    payload = {'date': day, 'model': 'fixture-auction-context', 'atmosphere': {'summary': title},
               'watchlist': {'stocks': [{'instrument_id': code, 'name': title,
                                        'trigger': '合成计划触发原文', 'invalidation': '合成计划证伪原文'}]},
               'plan': {'notes': '合成昨日计划原文'},
               'provenance': {'rule_version': 'fixture-v1', 'input_fingerprint': fingerprint,
                              'mode': 'retained_retrospective'}}
    encoded = canonical(payload).decode()
    with core.connect() as connection:
        connection.execute('INSERT INTO market_sentiment_snapshot VALUES(?,?,?,?,?,?,?,?,?)',
            ('sentiment_' + str(stamp).zfill(32), day, stamp, 'manual_retained_capture', payload['model'],
             'fixture-v1', fingerprint, sha256(encoded.encode()).hexdigest(), encoded))
    return payload


def test_real_previous_builder_freezes_before_start_version_and_never_reads_today_or_later_judgment(isolated):
    stamp = int(NOW.timestamp() * 1000)
    original = retain_review(PREVIOUS, stamp - 1000, '已保存的旧版计划')
    retain_review(DAY, stamp - 500, '今天的复盘不得冒充昨天', code='000009.SZ')
    retain_review(PREVIOUS, stamp + 1000, '采集后补录不得进入本次', code='000010.SZ')
    market = isolated.market
    market.calendar = lambda: [PREVIOUS, DAY]
    market.panel = lambda day: [{'instrument_id': '600001.SH', 'date': day,
                                'close': 10, 'volume': 100, 'change_pct': 1, 'amount': 100000}]
    market.reference = lambda: {'pool_trade_date': PREVIOUS, 'limitup': []}
    from app.services.noon_context import build_previous_review
    isolated.monkeypatch.setattr(service, '_previous_context',
        lambda market, day, previous, known_at: build_previous_review(market, day, previous, known_at, {}))
    job = claim(isolated); service.execute_capture(job)
    value = service.read_view(DAY)['snapshot']['payload']
    assert value['previous_review']['payload'] == original
    assert value['previous_review']['recorded_at_ms'] == stamp - 1000
    assert value['previous_review']['content_sha256'] == sha256(canonical(original)).hexdigest()
    assert value['universe']['items'][0]['instrument_id'] == '000008.SZ'
    assert '000009.SZ' not in [row['instrument_id'] for row in value['universe']['items']]
    assert '000010.SZ' not in [row['instrument_id'] for row in value['universe']['items']]


def test_preparation_uses_only_sqlite_latest_published_context_and_is_explicitly_unfrozen(ws, monkeypatch):
    market, _ = ws
    previous = market.entry
    original = retain_review(previous, 1000, '采集前可查看的已保存计划')
    monkeypatch.setattr(service, 'now_china', lambda: NOW)
    monkeypatch.setattr(service, 'get_market', lambda: pytest.fail('preparation cannot read market'))
    monkeypatch.setattr(source_service, 'client', lambda *_args: pytest.fail('preparation cannot fetch'))
    value = service.read_view(DAY)
    preparation = value['preparation']
    assert preparation['date'] == previous and preparation['frozen'] is False
    assert preparation['relation'] == 'last_published_day_pending_calendar_check'
    assert preparation['payload'] == original and preparation['observations'][0]['instrument_id'] == '000008.SZ'
    assert preparation['recorded_at_ms'] == 1000
    assert '尚未冻结' in preparation['notice'] and value['snapshot'] is None
    assert service.read_view(PREVIOUS)['preparation'] is None


def test_standard_backup_and_independent_restore_keep_embedded_evidence_exactly(isolated, tmp_path, monkeypatch):
    job = claim(isolated); worker.dispatch(job)
    with core.connect() as connection:
        before = connection.execute('SELECT payload_json,content_sha256 FROM market_auction_snapshot').fetchone()
    scripts = core.PROJECT_ROOT / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    from backup import perform_backup
    from restore import perform_restore
    bundle = tmp_path.parent / (tmp_path.name + '-auction-backup')
    restored = tmp_path.parent / (tmp_path.name + '-auction-restored')
    receipt = perform_backup(core.data_dir(), bundle)
    assert receipt['master_key_included'] is False
    perform_restore(bundle, restored)
    with sqlite3.connect(restored / 'business/app.sqlite3') as connection:
        after = connection.execute('SELECT payload_json,content_sha256 FROM market_auction_snapshot').fetchone()
        assert tuple(before) == after
        assert len(json.loads(after[0])['evidence']) == 3
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
