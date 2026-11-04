from datetime import datetime
from hashlib import sha256
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import core, worker
from app.routers.intraday import router
from app.services import intraday_service as service, source_service, task_service as tasks
from app.services.noon_collection import CHINA
from test_noon_prior_events import GENERATION, PRIOR, publish

DAY = '2026-10-28'


def test_old_snapshot_and_member_evidence_cutoff():
    for version in ('intraday-snapshot-v1', 'intraday-snapshot-v2', 'intraday-snapshot-v3'):
        value = {'version': version, 'id': 'previous', 'date': DAY, 'slot': 'opening'}
        encoded = json.dumps(value)
        assert service.decode_snapshot({
            'snapshot_id': 'previous', 'trade_date': DAY, 'slot': 'opening',
            'payload_json': encoded, 'content_sha256': sha256(encoded.encode()).hexdigest(),
        }) == value
    known = {'topic_code': '884045.TI', 'observed_date': '2026-10-27', 'source': 'fuyao', 'kind': 'industry',
             'members_complete': True, 'instrument_ids': ['000001.SZ']}
    future = {**known, 'topic_code': '884046.TI', 'observed_date': '2026-10-29'}
    catalog = {code: {'kind': 'industry'} for code in ('884045.TI', '884046.TI')}
    assert service._memberships({'topics': [known, future]}, catalog, DAY) == {
        '884045.TI': {'instrument_ids': ['000001.SZ'], 'date': '2026-10-27', 'complete': True},
    }
    assert not service._memberships({'topics': [known, known]}, catalog, DAY)
    assert not service._memberships({'topics': [{**known, 'source': 'other'}]}, catalog, DAY)
    assert not service._memberships({'topics': [{**known, 'kind': 'concept'}]}, catalog, DAY)


def test_break_pool_rejects_stale_source_and_third_page(monkeypatch):
    clock = datetime(2026, 10, 28, 9, 45, tzinfo=CHINA)
    monkeypatch.setattr(service, 'now_china', lambda: clock)
    monkeypatch.setattr(service, '_guard_job', lambda _: None)
    calls = []

    def stale(endpoint, params, before_attempt):
        before_attempt()
        calls.append(endpoint)
        return {'data': {'item': [], 'timestamp': int(clock.timestamp() * 1000) - 86_400_000}}

    capture = service.Capture({}, DAY, service.LEGACY_SLOTS[0], SimpleNamespace(request=stale), None, 30)
    endpoint = '/api/a-share/special-data/limit-break-pool'
    assert capture.request('limit_break_page_1', endpoint, dated=True) is None
    assert capture.request('limit_break_page_3', endpoint, dated=True) is None
    assert calls == [endpoint]
    assert [gap['domain'] for gap in capture.gaps] == ['limit_break_page_1', 'limit_break_page_3']


def test_worker_only_slots_comparison_and_source_date(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    monkeypatch.setattr(service, '_configured', lambda conn: True)
    monkeypatch.setattr(tasks, 'initialization_state', lambda conn: {'main_published': True})
    clock = datetime(2026, 10, 28, 10, 0, 7, tzinfo=CHINA)
    first_slot = lambda: clock.hour == 10 and clock.minute == 0
    monkeypatch.setattr(service, 'now_china', lambda: clock)
    _, prior_parquet = publish(core.published_dir(), {
        'pool_trade_date': PRIOR, 'pool_coverage': {key: {'complete': True, 'declared_total': 1}
                                                   for key in ('limitup', 'breakpool')},
        'limitup': [{'thscode': '000001.SZ', 'last_price': 10., 'continue_day_cnt': 1}],
        'breakpool': [{'thscode': '000003.SZ', 'last_price': 10.}],
    }, [{'instrument_id': code, 'date': PRIOR, 'close': 10., 'volume': 100., 'price_basis': 'none', 'source': 'fuyao'}
         for code in ('000001.SZ', '000003.SZ')])
    market = SimpleNamespace(reference=lambda: {
        'industry_catalog': [{'thscode': '884045.TI', 'name': '测试行业'}], 'concept_catalog': [],
        'master': {'000002.SZ': {'list_date': DAY}},
        'topics': [{'topic_code': '884045.TI', 'observed_date': '2026-10-27', 'source': 'fuyao', 'kind': 'industry',
                    'members_complete': True, 'instrument_ids': ['000001.SZ', '000002.SZ', '000003.SZ']}],
    }, status=lambda: {'snapshot_id': GENERATION},
       instruments=lambda **_: [{'instrument_id': '000001.SZ', 'name': '测试股票'}])
    monkeypatch.setattr(service, 'get_market', lambda: market)
    requests = []

    def source(endpoint, params, before_attempt):
        before_attempt()
        requests.append((endpoint, params))
        timestamp = int(clock.timestamp() * 1000)
        if endpoint.endswith('trading-days'):
            return {'data': {'item': [{'date': day.replace('-', '')} for day in (PRIOR, DAY)], 'timestamp': timestamp}}
        if endpoint.endswith('limit-up-pool'):
            assert params['date_ms'] == int(datetime(2026, 10, 28, tzinfo=CHINA).timestamp() * 1000)
            sealed = ([{'thscode': '000001.SZ', 'last_price': 10.1, 'continue_day_cnt': 2}] if first_slot() else
                      [{'thscode': '000003.SZ', 'last_price': 10.1, 'continue_day_cnt': 1}])
            return {'data': {'item': sealed, 'timestamp': timestamp,
                             'pagination': {'total': len(sealed), 'page': 1, 'size': 200, 'pages': 1}}}
        if endpoint.endswith('limit-break-pool'):
            assert params['date_ms'] == int(datetime(2026, 10, 28, tzinfo=CHINA).timestamp() * 1000)
            row = ({'thscode': '000003.SZ', 'last_price': 10.05} if first_slot() else
                   {'thscode': '000001.SZ', 'last_price': 10.2})
            return {'data': {'item': [row], 'timestamp': timestamp,
                             'pagination': {'total': 1, 'page': 1, 'size': 200, 'pages': 1}}}
        board = 'index' in endpoint
        previous = 10
        price = 10.1 if first_slot() else 10.2
        row = {'thscode': '884045.TI' if board else '000001.SZ', 'last_price': price,
               'prev_price': previous, 'open_price': 10, 'high_price': price, 'low_price': 10,
               'turnover': 100 if first_slot() else 120, 'price_change_ratio_pct': (price / previous - 1) * 100}
        if board and ',' in params['thscodes']:
            rows = [{**row, 'thscode': code} for code in params['thscodes'].split(',')]
            return {'data': {'item': rows, 'timestamp': timestamp}}
        if clock.hour == 10 and clock.minute == 30 and board:
            timestamp -= 24 * 3600 * 1000
        rows = [row] if board else [row, {
            **row, 'thscode': '000002.SZ', 'last_price': 59, 'high_price': 59, 'price_change_ratio_pct': 490,
        }]
        if not board:
            rows.append({**row, 'thscode': '000003.SZ', 'last_price': 10.05 if first_slot() else 10.1,
                         'price_change_ratio_pct': .5 if first_slot() else 1})
        return {'data': {'item': rows, 'timestamp': timestamp, 'total': len(rows)}}

    monkeypatch.setattr(source_service, 'client', lambda key: SimpleNamespace(request=source))
    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    url = '/api/v1/market/intraday'

    def queue(key):
        return api.post(url + '/jobs', json={'date': DAY, 'request_key': key})

    assert api.get(url).json()['can_capture']
    assert queue('a').status_code == 200
    assert queue('a').status_code == 200
    assert queue('different').status_code == 409
    assert not requests
    worker.dispatch(tasks.claim_next('test'))
    first = api.get(url).json()
    assert [row['instrument_id'] for row in first['snapshots'][0]['stocks']] == ['000001.SZ', '000003.SZ']
    assert first['snapshots'][0]['summary']['recent_listing_count'] == 1
    assert first['snapshots'][0]['summary']['recent_listings'][0]['instrument_id'] == '000002.SZ'
    assert first['snapshots'][0]['stocks'][0]['amount_delta'] is None
    assert first['snapshots'][0]['scheduled_at'] == '2026-10-28T10:00:00+08:00'
    with core.connect() as connection:
        saved = service._saved_slot(connection, DAY, '1000')
        chart = next(row for row in saved['comparison_input']['stocks'] if row['instrument_id'] == '000001.SZ')
        assert (chart['open'], chart['high'], chart['low'], chart['latest_price']) == (10, 10.1, 10, 10.1)
    assert first['snapshots'][0]['sectors'][0]['kind'] == 'industry'
    assert first['snapshots'][0]['summary']['limit_up_pool_state'] == 'observed'
    assert first['snapshots'][0]['summary']['limit_break_pool_state'] == 'observed'
    sector_stocks = first['snapshots'][0]['board_stocks']['884045.TI']
    assert sector_stocks['member_date'] == PRIOR and sector_stocks['matched_count'] == 3
    assert {item['instrument_id']: item.get('limit_up_state') for item in sector_stocks['items']} == {
        '000001.SZ': 'sealed', '000002.SZ': None, '000003.SZ': 'broken',
    }
    assert [row['instrument_id'] for row in first['snapshots'][0]['sealed_stocks']] == ['000001.SZ']
    assert not first['snapshots'][0]['candidates']
    assert len(requests) == 6
    assert requests[4][0].endswith('limit-break-pool')
    major_indices = first['snapshots'][0]['market_pulse']['indices']
    assert [row['instrument_id'] for row in major_indices] == ['000001.SH', '399001.SZ', '399006.SZ', '000688.SH']
    assert all(row['latest_price'] == 10.1 for row in major_indices)
    assert requests[3][1]['thscodes'] == ','.join(row['instrument_id'] for row in major_indices)
    assert first['snapshots'][0]['ladder']['highest_board'] == 2
    broken = first['snapshots'][0]['ladder']['broken_board']
    assert broken['state'] == 'observed' and broken['count'] == 1
    assert broken['items'][0]['instrument_id'] == '000003.SZ'
    assert broken['items'][0]['themes'] == [{'name': '测试行业', 'kind': 'industry', 'member_date': PRIOR}]
    prior = first['snapshots'][0]['relay_feedback']['prior_session']
    assert prior['date'] == PRIOR and prior['state'] == 'observed' and prior['promotion_count'] == 1
    assert prior['items'][0]['previous_board_count'] == 1
    assert first['snapshots'][0]['relay_feedback']['same_day']['state'] == 'unknown'

    clock = clock.replace(hour=10, minute=30)
    assert queue('b').status_code == 200
    worker.dispatch(tasks.claim_next('test'))
    second = api.get(url).json()['snapshots'][-1]
    assert second['slot'] == '1030'
    assert second['stocks'][0]['amount_delta'] == 20
    assert second['quality'] == 'partial' and not second['sectors']
    assert second['summary']['comparison_slot'] == '1000'
    interval = second['summary']['interval_amount']
    assert interval['amount'] == 40 and interval['comparable_count'] == 2
    assert interval['previous_date'] == PRIOR and interval['previous_amount'] is None
    assert {'comparison_input', 'source_checks', 'sealed_pool_input'}.isdisjoint(second)
    same_day = second['relay_feedback']['same_day']
    assert same_day['label'] == '开盘首轮' and same_day['state'] == 'observed'
    assert same_day['held_count'] == 0 and same_day['entered_count'] == same_day['absent_count'] == 1
    assert same_day['entered'][0]['instrument_id'] == '000003.SZ'
    assert same_day['absent'][0]['instrument_id'] == '000001.SZ'
    assert second['ladder']['broken_board']['items'][0]['themes'][0]['name'] == '测试行业'
    prior_broken = second['relay_feedback']['prior_broken_session']
    assert prior_broken['date'] == PRIOR and prior_broken['state'] == 'observed'
    assert prior_broken['sealed_count'] == 1 and prior_broken.get('promotion_count') is None

    # Old queued work runs within its frozen request plan and still publishes its own shape.
    clock = clock.replace(hour=14, minute=35)
    with core.connect() as connection:
        service._enqueue(connection, 'legacy-v3', DAY, service.LEGACY_SLOTS[-1], clock, version=service.FEEDBACK_VERSION)
    worker.dispatch(tasks.claim_next('test'))
    older_version = api.get(url).json()['snapshots'][-1]
    assert older_version['slot'] == 'late' and older_version['summary']['request_count'] == 5
    assert older_version['sealed_stocks'] and 'market_pulse' in older_version
    assert 'limit_break_pool_state' not in older_version['summary']
    assert 'broken_board' not in older_version['ladder']
    assert 'prior_broken_session' not in older_version['relay_feedback']

    prior_parquet.write_bytes(prior_parquet.read_bytes() + b'changed')
    assert service._prior_closing_cohort(market, [PRIOR, DAY], DAY) == {
        'date': PRIOR, 'complete': False, 'items': [],
    }
    assert service._prior_closing_cohort(market, [DAY], DAY) == {
        'date': None, 'complete': False, 'items': [],
    }

    conflicting_event = {'thscode': '000001.SZ', 'last_price': 10.}
    publish(core.published_dir(), {'pool_trade_date': PRIOR,
        'limitup': [conflicting_event], 'breakpool': [conflicting_event],
    }, [{'instrument_id': '000001.SZ', 'date': PRIOR, 'close': 10., 'volume': 100.,
         'price_basis': 'none', 'source': 'fuyao'}])
    conflicting_prior = service._prior_closing_cohort(market, [PRIOR, DAY], DAY, include_broken=True)
    assert not conflicting_prior['complete'] and not conflicting_prior['items']
    assert conflicting_prior['broken'] == {'date': PRIOR, 'complete': False, 'items': []}
    clock = clock.replace(hour=14, minute=57)
    assert not api.get(url).json()['can_capture']


def test_automation_creates_only_during_six_verified_windows(tmp_path, monkeypatch):
    from app.services import automatic_tasks, sync_progress

    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    core.ensure_ready()
    monkeypatch.setattr(service, '_configured', lambda connection: True)
    monkeypatch.setattr(tasks, 'initialization_state', lambda connection: {'main_published': True})
    clock = datetime(2026, 10, 28, 10, 0, tzinfo=CHINA)
    monkeypatch.setattr(service, 'now_china', lambda: clock)
    requests = []
    receipt = {'date': DAY, 'state': 'ready', 'days': [DAY], 'source_id': 'fuyao', 'source_sha256': 'a' * 64}

    def calendar(*_, setting_key):
        requests.append(clock)
        assert setting_key == service.AUTOMATION_CALENDAR_KEY
        with core.connect() as connection:
            automatic_tasks._save(connection, setting_key, receipt)
        return receipt

    monkeypatch.setattr(automatic_tasks, '_calendar', calendar)
    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    automation = '/api/v1/market/intraday/automation'
    assert api.get(automation).json()['enabled'] is False
    assert service.tick_automatic() is None and not requests
    assert api.put(automation, json={'enabled': True, 'expected_revision': 0}).json()['revision'] == 1
    assert api.put(automation, json={'enabled': False, 'expected_revision': 0}).status_code == 409
    monkeypatch.setattr(sync_progress, 'lock_record', lambda connection: {'root_task_id': 'hold'})
    assert service.tick_automatic() is None and not requests
    monkeypatch.setattr(sync_progress, 'lock_record', lambda connection: None)
    other = tasks.create_task('source_check', {'operation': 'noon_snapshot'}, DAY, 'already-queued')
    assert service.tick_automatic() is None and not requests
    with core.connect() as connection:
        connection.execute("UPDATE job SET status='failed' WHERE job_id=?", (other['job_id'],))

    for key in ('09:25', '09:59', '10:01', '11:30', '12:05', '13:00', '15:00'):
        hour, minute = map(int, key.split(':'))
        clock = clock.replace(hour=hour, minute=minute)
        assert service.tick_automatic() is None
    assert not requests

    receipt['days'] = []
    clock = clock.replace(hour=10, minute=0)
    assert service.tick_automatic() is None
    receipt['days'] = [DAY]
    for slot in service.SLOTS:
        hour, minute = map(int, slot['start'].split(':'))
        clock = clock.replace(hour=hour, minute=minute)
        identifier = service.tick_automatic()
        assert identifier and service.tick_automatic() is None
        job = tasks.task_view(identifier)
        assert job['requested_by'] == 'automatic'
        assert job['parameters']['slot'] == slot['key']
        assert job['parameters']['automation']['calendar_sha256'] == 'a' * 64
        # A failed automatic observation retains its slot; later stages can proceed.
        with core.connect() as connection:
            connection.execute("UPDATE job SET status='failed' WHERE job_id=?", (identifier,))
        assert service.tick_automatic() is None

    clock = clock.replace(day=29, hour=10, minute=0)
    assert service.tick_automatic() is None
    with core.connect() as connection:
        assert automatic_tasks._json(connection, automatic_tasks.CALENDAR_KEY, None) is None


def test_interval_vs_previous_day_uses_four_exact_symbol_endpoints():
    def quote(symbol, amount):
        return dict(instrument_id=symbol, currency='CNY', price_basis='none',
                    prev_close=10, latest_price=11, amount=amount)

    def saved(*rows):
        return {'comparison_input': {'stocks': rows}}

    a = '000001.SZ'
    b = '000002.SZ'
    result = service._interval_amount([quote(a, 500), quote(b, 900)], saved(quote(a, 200), quote(b, 400)),
                                      saved(quote(a, 400), quote(b, 600)), saved(quote(a, 300)), PRIOR)
    assert result['amount'] == 800
    assert result['comparison_amount'] == 300
    assert result['previous_amount'] == 100
    assert result['change_from_previous'] == 200
    assert result['comparable_count'] == 2 and result['previous_comparable_count'] == 1
