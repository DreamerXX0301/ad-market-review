from datetime import datetime
from hashlib import sha256
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

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


def test_rotation_member_identity_uses_original_reference_and_preserves_unknowns(monkeypatch):
    from copy import deepcopy

    code, a, b = '884045.TI', '000001.SZ', '000002.SZ'
    reference = {
        'master': {}, 'calendar': [PRIOR, DAY],
        'industry_catalog': [{'thscode': code, 'name': '测试行业'}], 'concept_catalog': [],
        'topics': [{'topic_code': code, 'observed_date': PRIOR, 'source': 'fuyao',
                    'kind': 'industry', 'members_complete': True, 'instrument_ids': [b, a]}],
    }
    market = SimpleNamespace(status=lambda: {'snapshot_id': GENERATION, 'data_end': PRIOR},
                             reference=lambda: reference)
    monkeypatch.setattr(service, 'get_market', lambda: market)

    def snapshot(**board_changes):
        board = {'instrument_id': code, 'kind': 'industry', 'member_date': PRIOR,
                 'member_list_complete': True, 'member_count': 2, **board_changes}
        return {'date': DAY, 'member_reference': {'generation': GENERATION, 'date': PRIOR},
                'index_only_sectors': [board], 'comparison_input': {'stocks': []}}

    first, second = snapshot(), snapshot()
    saved = deepcopy(first)
    service._attach_saved_stock_references([first, second])
    identity = first['index_only_sectors'][0]['member_reference_sha256']
    assert identity == second['index_only_sectors'][0]['member_reference_sha256']
    assert len(identity) == 64 and 'member_reference_sha256' not in saved['index_only_sectors'][0]
    reference['topics'][0]['instrument_ids'] = [a, b]
    reordered = snapshot()
    service._attach_saved_stock_references([reordered])
    assert reordered['index_only_sectors'][0]['member_reference_sha256'] == identity
    for changes in ({'member_date': '2026-10-26'}, {'member_list_complete': False},
                    {'member_count': 1}, {'kind': 'concept'}):
        unknown = snapshot(**changes)
        service._attach_saved_stock_references([unknown])
        assert 'member_reference_sha256' not in unknown['index_only_sectors'][0]
    reference['topics'][0]['members_complete'] = False
    incomplete = snapshot()
    service._attach_saved_stock_references([incomplete])
    assert 'member_reference_sha256' not in incomplete['index_only_sectors'][0]


@pytest.mark.parametrize(('offset', 'pages', 'code', 'complete'), [
    (0, 0, 0, True),
    (-30, 0, 0, False),
    (0, 1, 0, False),
    (0, 0, 1, False),
])
def test_intraday_zero_requires_success_and_timestamp_in_exact_window(monkeypatch, offset, pages, code, complete):
    clock = datetime(2026, 10, 28, 10, 0, 7, tzinfo=CHINA)
    monkeypatch.setattr(service, 'now_china', lambda: clock)
    monkeypatch.setattr(service, '_guard_job', lambda _: None)
    params = {'parameters_json': json.dumps({'version': service.VERSION})}
    response = {'code': code, 'data': {'item': [], 'timestamp': int(clock.timestamp() * 1000) + offset * 1000,
                'pagination': {'page': 1, 'pages': pages, 'size': 200, 'total': 0}}}
    def request(endpoint, params, **_kwargs):
        assert params == {'date_ms': int(clock.replace(hour=0, second=0).timestamp() * 1000), 'page': 1, 'size': 200}
        return response
    adapter = SimpleNamespace(request=request)
    capture = service.Capture(params, DAY, service.SLOTS[0], adapter, None, 30)
    assert capture.pool('limit_break', '/api/a-share/special-data/limit-break-pool') == ([], 0, complete)
    assert bool(capture.gaps) is not complete


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
    }, status=lambda: {'snapshot_id': GENERATION, 'data_end': PRIOR},
       instruments=lambda **_: [{'instrument_id': '000001.SZ', 'name': '测试股票'}])
    monkeypatch.setattr(service, 'get_market', lambda: market)
    requests = []

    def source(endpoint, params, before_attempt, **options):
        before_attempt()
        requests.append((endpoint, params))
        timestamp = int(clock.timestamp() * 1000)
        if endpoint.endswith('trading-days'):
            return {'data': {'item': [{'date': day.replace('-', '')} for day in (PRIOR, DAY)], 'timestamp': timestamp}}
        if endpoint.endswith('limit-down-pool'):
            assert options['max_attempts'] == 1 and 0 < options['timeout'] <= 8
            return {'code': 0, 'data': {'item': [], 'timestamp': timestamp,
                                       'pagination': {'total': 0, 'page': 1, 'size': 200, 'pages': 0}}}
        if clock.hour == 11 and (endpoint.endswith('limit-up-pool') or endpoint.endswith('limit-break-pool')):
            return {'code': 0, 'data': {'item': [], 'timestamp': timestamp,
                                       'pagination': {'total': 0, 'page': 1, 'size': 200, 'pages': 0}}}
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
        parameters = json.loads(connection.execute("SELECT parameters_json FROM job WHERE request_key='a'").fetchone()[0])
        assert parameters['board_change_evidence'] == service.BOARD_CHANGE_EVIDENCE
        assert parameters['request_budget'] == service.REQUEST_BUDGETS[service.VERSION] + 2
        assert saved['board_pool_evidence']['limit_down'] == {'state': 'observed', 'count': 0, 'items': []}
        chart = next(row for row in saved['comparison_input']['stocks'] if row['instrument_id'] == '000001.SZ')
        assert (chart['open'], chart['high'], chart['low'], chart['latest_price']) == (10, 10.1, 10, 10.1)
        assert 'quote_source' not in saved['sectors'][0], 'read-side metadata never changes the frozen snapshot'
    assert first['snapshots'][0]['sectors'][0]['kind'] == 'industry'
    projected = first['snapshots'][0]['sectors'][0]
    assert (projected['quote_source'], projected['quote_basis'], projected['prev_close']) == (
        'fuyao', 'provider_index', 10)
    member_board = first['snapshots'][0]['index_only_sectors'][0]
    assert len(member_board['member_reference_sha256']) == 64
    assert 'member_reference_sha256' not in saved['index_only_sectors'][0]
    assert first['snapshots'][0]['summary']['limit_up_pool_state'] == 'observed'
    assert first['snapshots'][0]['summary']['limit_break_pool_state'] == 'observed'
    sector_stocks = first['snapshots'][0]['board_stocks']['884045.TI']
    assert sector_stocks['member_date'] == PRIOR and sector_stocks['matched_count'] == 3
    assert {item['instrument_id']: item.get('limit_up_state') for item in sector_stocks['items']} == {
        '000001.SZ': 'sealed', '000002.SZ': None, '000003.SZ': 'broken',
    }
    from app.services import intraday_members

    def member_proof(generation, expected_end, code, *, observed, reference_sha):
        assert (generation, expected_end, code, observed) == (GENERATION, PRIOR, '884045.TI', PRIOR)
        assert reference_sha is None
        return ['000001.SZ', '000002.SZ', '000003.SZ', '688001.SH'], observed, True

    monkeypatch.setattr(intraday_members, '_topic_members', member_proof)
    monkeypatch.setattr(intraday_members, 'get_market', lambda: market)
    lookup = {'date': DAY, 'slot': '1000', 'snapshot_id': first['snapshots'][0]['id'],
              'topic_code': '884045.TI', 'limit': 2}
    members = api.get(url + '/members', params=lookup).json()
    assert members['state'] == 'partial' and members['pages'] == 2
    assert members['quote_coverage'] == {'valid': 3, 'total': 4}
    assert [item['instrument_id'] for item in members['items']] == ['000002.SZ', '000001.SZ']
    assert members['items'][1]['continue_day_cnt'] == 2
    second_page = api.get(url + '/members', params={**lookup, 'page': 2}).json()
    assert second_page['items'][0]['limit_up_state'] == 'broken'
    assert second_page['items'][1]['change_pct'] is None and second_page['items'][1]['missing_reason']
    ascending = {**lookup, 'sort': 'change_pct', 'direction': 'asc'}
    first_sorted = api.get(url + '/members', params=ascending).json()
    assert (first_sorted['sort'], first_sorted['direction']) == ('change_pct', 'asc')
    assert [item['rank'] for item in first_sorted['items']] == [3, 2]
    second_sorted = api.get(url + '/members', params={**ascending, 'page': 2}).json()
    assert [item['rank'] for item in second_sorted['items']] == [1, 4]
    assert second_sorted['items'][-1]['change_pct'] is None
    assert api.get(url + '/members', params={**lookup, 'sort': 'unexpected'}).status_code == 422

    def history_for_symbols(date, slot, symbols):
        assert (date, slot) == (DAY, '1000') and len(symbols) == 4
        return {'date': date, 'slot': slot, 'as_of': PRIOR, 'items': {
            '000001.SZ': {'limit_up_1y': 0},
            '000002.SZ': {'limit_up_1y_observed': 0},
            '000003.SZ': {'limit_up_1y_observed': 5},
        }}

    monkeypatch.setattr(intraday_members, 'history_for_symbols', history_for_symbols)
    historical = api.get(url + '/members', params={**lookup, 'sort': 'limit_up_1y'}).json()
    assert [item['instrument_id'] for item in historical['items']] == ['000003.SZ', '000001.SZ']
    assert historical['history']['as_of'] == PRIOR
    assert set(historical['history']['items']) == {'000003.SZ', '000001.SZ'}
    only_star = api.get(url + '/members', params={**lookup, 'board': 'star'}).json()
    assert only_star['matched'] == 1 and only_star['items'][0]['instrument_id'] == '688001.SH'
    assert api.get(url + '/members', params={**lookup, 'slot': '1030'}).json()['state'] == 'missing'
    assert api.get(url + '/members', params={**lookup, 'snapshot_id': 'intraday_' + 'a' * 32}).json()['state'] == 'unknown'
    legacy = {key: value for key, value in saved.items() if key != 'member_reference'}
    original_proof = next(item for item in legacy['source_checks'] if item['domain'] == 'prior_session')
    assert intraday_members._member_provenance(legacy) == (GENERATION, PRIOR, original_proof['reference_sha256'])
    assert intraday_members._member_provenance({**legacy, 'source_checks': [original_proof, original_proof]}) is None
    assert intraday_members._member_provenance({
        **legacy, 'source_checks': [{**original_proof, 'reference_sha256': 'unverified'}],
    }) is None
    assert [row['instrument_id'] for row in first['snapshots'][0]['sealed_stocks']] == ['000001.SZ']
    assert not first['snapshots'][0]['candidates']
    assert len(requests) == 7
    assert requests[4][0].endswith('limit-break-pool')
    assert requests[6][0].endswith('limit-down-pool')
    major_indices = first['snapshots'][0]['market_pulse']['indices']
    assert [row['instrument_id'] for row in major_indices] == ['000001.SH', '399001.SZ', '399006.SZ', '000688.SH']
    assert all(row['latest_price'] == 10.1 for row in major_indices)
    assert all(row['amount'] == 100 and row['amount_comparison']['previous_amount'] is None for row in major_indices)
    assert 'interval_amount' not in major_indices[0]  # No 09:30 endpoint was captured.
    assert requests[3][1]['thscodes'] == ','.join(row['instrument_id'] for row in major_indices)
    assert first['snapshots'][0]['ladder']['highest_board'] == 2
    broken = first['snapshots'][0]['ladder']['broken_board']
    assert broken['state'] == 'observed' and broken['count'] == 1
    assert broken['items'][0]['instrument_id'] == '000003.SZ'
    assert broken['items'][0]['themes'] == [{'name': '测试行业', 'kind': 'industry', 'member_date': PRIOR}]
    prior = first['snapshots'][0]['relay_feedback']['prior_session']
    assert prior['date'] == PRIOR and prior['state'] == 'observed' and prior['promotion_count'] == 1
    assert prior['items'][0]['previous_board_count'] == 1
    expected_themes = [{'name': '测试行业', 'kind': 'industry', 'member_date': PRIOR}]
    assert prior['items'][0]['themes'] == expected_themes
    assert first['snapshots'][0]['relay_feedback']['same_day']['state'] == 'unknown'

    # Successful new-flag captures use the same 28-natural-day retention boundary.
    with core.connect() as connection:
        for older_day, key in (('2026-09-30', 'expired-intraday'), ('2026-10-01', 'retained-boundary')):
            identifier = service._enqueue(connection, key, older_day, service.SLOTS[0], clock)
            connection.execute("UPDATE job SET status='succeeded' WHERE job_id=?", (identifier,))
            raw = core.dumps({'retained': key})
            connection.execute('''UPDATE market_intraday_capture SET snapshot_id=?,payload_json=?,content_sha256=?
                WHERE job_id=?''', ('intraday_' + sha256(key.encode()).hexdigest()[:32], raw,
                                   sha256(raw.encode()).hexdigest(), identifier))

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
    major_index = second['market_pulse']['indices'][0]
    assert major_index['amount'] == 120 and major_index['interval_amount']['amount'] == 20
    assert major_index['interval_amount']['previous_amount'] is None
    assert {'comparison_input', 'source_checks', 'sealed_pool_input'}.isdisjoint(second)
    same_day = second['relay_feedback']['same_day']
    assert same_day['label'] == '开盘首轮' and same_day['state'] == 'observed'
    assert same_day['held_count'] == 0 and same_day['entered_count'] == same_day['absent_count'] == 1
    assert same_day['entered'][0]['instrument_id'] == '000003.SZ'
    assert same_day['absent'][0]['instrument_id'] == '000001.SZ'
    assert same_day['entered'][0]['themes'] == same_day['absent'][0]['themes'] == expected_themes
    assert second['ladder']['broken_board']['items'][0]['themes'][0]['name'] == '测试行业'
    groups = second['board_changes']['groups']
    assert groups['new_sealed']['count'] == groups['opened']['count'] == groups['resealed']['count'] == 1
    assert groups['new_limit_down']['count'] == 0 and groups['new_limit_down']['state'] == 'observed'
    assert groups['resealed']['items'][0]['themes'] == expected_themes
    assert groups['resealed']['items'][0]['continue_day_cnt'] == 1
    with core.connect() as connection:
        assert connection.execute("SELECT count(*) FROM market_intraday_capture WHERE trade_date='2026-09-30'").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM market_intraday_capture WHERE trade_date='2026-10-01'").fetchone()[0] == 1
    prior_broken = second['relay_feedback']['prior_broken_session']
    assert prior_broken['date'] == PRIOR and prior_broken['state'] == 'observed'
    assert prior_broken['sealed_count'] == 1 and prior_broken.get('promotion_count') is None
    assert prior_broken['items'][0]['themes'] == expected_themes
    with core.connect() as connection:
        old = service._saved_slot(connection, DAY, '1030')
    relay = old['relay_feedback']
    relay['same_day']['entered'][0].pop('themes')
    relay['prior_broken_session']['items'][0].pop('themes')
    service._attach_saved_stock_references([old])
    assert relay['same_day']['entered'][0]['themes'] == expected_themes
    assert relay['prior_broken_session']['items'][0]['themes'] == expected_themes

    clock = clock.replace(hour=11, minute=0)
    assert queue('verified-empty').status_code == 200
    worker.dispatch(tasks.claim_next('test'))
    empty = api.get(url).json()['snapshots'][-1]
    assert empty['slot'] == '1100'
    assert empty['summary']['limit_up_pool_state'] == empty['summary']['limit_break_pool_state'] == 'observed'
    assert empty['ladder']['sealed_count'] == empty['ladder']['broken_board']['count'] == 0
    assert empty['ladder']['broken_board']['items'] == []

    # An already frozen v5 task without the marker keeps the original network plan.
    clock = clock.replace(hour=14, minute=0)
    with core.connect() as connection:
        identifier = service._enqueue(connection, 'old-v5', DAY, service.SLOTS[4], clock)
        frozen = json.loads(connection.execute('SELECT parameters_json FROM job WHERE job_id=?', (identifier,)).fetchone()[0])
        frozen.pop('board_change_evidence'); frozen.pop('request_budget')
        connection.execute('UPDATE job SET parameters_json=?,parameters_hash=? WHERE job_id=?',
                           (core.dumps(frozen), core.digest(frozen), identifier))
    before = len(requests)
    worker.dispatch(tasks.claim_next('test'))
    assert not any(endpoint.endswith('limit-down-pool') for endpoint, _ in requests[before:])
    with core.connect() as connection:
        assert 'board_pool_evidence' not in service._saved_slot(connection, DAY, '1400')

    # The added evidence must not bypass the unchanged 2MiB atomic publication guard.
    clock = clock.replace(hour=13, minute=30)
    assert queue('oversized-board-evidence').status_code == 200
    original_canonical = service.canonical
    def oversized(value):
        encoded = original_canonical(value)
        return encoded + b' ' * service.MAX_PAYLOAD if value.get('version') == service.VERSION else encoded
    with monkeypatch.context() as patch:
        patch.setattr(service, 'canonical', oversized)
        claimed = tasks.claim_next('test')
        with pytest.raises(ValueError, match='2MiB'):
            worker.dispatch(claimed)
        tasks.finish(claimed['job_id'], 'failed', error='盘中快照超过2MiB容量上限', lease_owner=claimed['lease_owner'])
    with core.connect() as connection:
        record = connection.execute('''SELECT c.snapshot_id,j.status FROM market_intraday_capture c
            JOIN job j USING(job_id) WHERE j.request_key='oversized-board-evidence' ''').fetchone()
        assert record['snapshot_id'] is None and record['status'] == 'failed'

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


def test_stock_and_index_turnover_compares_only_matching_evidence(monkeypatch):
    from contextlib import nullcontext

    def quote(symbol, amount, *, currency='CNY'):
        return dict(instrument_id=symbol, currency=currency, price_basis='none',
                    prev_close=10, latest_price=11, amount=amount)

    a, b = '000001.SH', '399001.SZ'
    stock, extra = '000001.SZ', '600001.SH'
    pulse = {'turnover': {'amount': 1000}, 'indices': [{'instrument_id': a}, {'instrument_id': b}]}
    current = {'version': service.VERSION, 'date': DAY, 'slot': '1030', 'market_pulse': pulse,
               'relay_feedback': {'prior_session': {'date': PRIOR}},
               'comparison_input': {'stocks': [quote(stock, 500), quote(extra, 500)],
                                    'indices': [quote(a, 400), quote(b, 200)]}}
    before = {'comparison_input': {'indices': [quote(a, 250), quote(b, 230)]}}
    previous = {'comparison_input': {'stocks': [quote(stock, 450), quote(extra, 500, currency='USD')],
                                     'indices': [quote(a, 500), quote(b, 210)]}}
    previous_before = {'comparison_input': {'indices': [quote(a, 300), quote(b, 100)]}}
    service._attach_turnover(current, before, previous, previous_before, PRIOR)
    market_comparison = pulse['turnover']['amount_comparison']
    assert market_comparison == {'previous_date': PRIOR, 'comparison_amount': 500, 'previous_amount': 450,
                                 'change_from_previous': 50, 'comparable_count': 1}
    first_index, second_index = pulse['indices']
    assert first_index['amount'] == 400
    assert first_index['amount_comparison']['change_from_previous'] == -100
    assert first_index['interval_amount']['amount'] == 150
    assert first_index['interval_amount']['change_from_previous'] == -50
    assert second_index['interval_amount']['amount'] is None  # Same-day amount dropped.
    assert second_index['interval_amount']['previous_amount'] is None

    pulse['turnover'].pop('amount_comparison')
    pulse['indices'] = [{'instrument_id': a}]
    current['comparison_input'].pop('indices')
    monkeypatch.setattr(service, 'connect', lambda: nullcontext())
    def retained(_, day, slot):
        assert (day, slot) == (PRIOR, '1030')
        return previous
    monkeypatch.setattr(service, '_optional_turnover_slot', retained)
    service._restore_legacy_turnover([current])
    assert pulse['turnover']['amount_comparison'] == market_comparison
    assert 'amount' not in pulse['indices'][0]  # Old checksums cannot recover missing index amounts.


def test_unconfirmed_stock_only_gets_exact_regular_price_reference_with_dated_identity(monkeypatch):
    code = '920007.BJ'
    identity = {'name': '样本北交所股', 'list_date': '2025-08-08', 'kind': 'stock'}
    market = SimpleNamespace(status=lambda: {'snapshot_id': GENERATION, 'data_end': PRIOR},
                             reference=lambda: {'master': {code: identity},
                                                'calendar': ['2026-10-21', '2026-10-22', '2026-10-23',
                                                             '2026-10-26', '2026-10-27']})
    monkeypatch.setattr(service, 'get_market', lambda: market)

    def projected(**overrides):
        quote = {'instrument_id': code, 'price_basis': 'none', 'currency': 'CNY',
                 'amount': 123, 'latest_price': 39.49, 'prev_close': 30.38, **overrides}
        quote['change_pct'] = (quote['latest_price'] / quote['prev_close'] - 1) * 100
        row = {'instrument_id': code, 'name': identity['name'], 'change_pct': quote['change_pct'],
               'limit_up_state': 'not_in_verified_pool', 'continue_day_cnt': None}
        snapshot = {'date': DAY, 'member_reference': {'generation': GENERATION, 'date': PRIOR},
                    'candidates': [row], 'comparison_input': {'stocks': [quote]}}
        service._attach_saved_stock_references([snapshot])
        return row

    result = projected()
    assert result['limit_up_reference_price'] == 39.49
    assert result['limit_up_state'] == 'not_in_verified_pool' and result['continue_day_cnt'] is None
    assert 'limit_up_reference_price' not in projected(latest_price=39.48)
    assert 'limit_up_reference_price' not in projected(amount=0)
    assert 'limit_up_reference_price' not in projected(currency='USD')
    identity['list_date'] = '2026-10-23'
    assert 'limit_up_reference_price' not in projected()
    identity.update(list_date='2025-08-08', name='*ST 样本')
    assert 'limit_up_reference_price' not in projected()
