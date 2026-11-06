"""Run the authorized auction path against bounded synthetic provider responses."""
from hashlib import sha256
import json
import sqlite3

import pytest

from app import core, worker
from app.adapters.providers import ProviderError
from app.services import auction_automation as automation, auction_discovery as discovery, auction_service
from app.services.auction_integrity import validate_snapshots
from test_auction_retention import capture, encode, job, local, publication
from test_auction_snapshots import DAY, NOW, PREVIOUS, claim, isolated

OPPORTUNITY_CODES = ('688001.SH', '688002.SH')


def authorize():
    automation.update_config({'mode': 'daily', 'expected_revision': automation.read_view()['revision']})
    with core.connect() as connection:
        automation.configure_discovery(connection, enabled=True, expected_revision=automation.discovery_settings(connection)['revision'])


def provider(isolated, *, mode='real_time', late=False, terminal_errors=()):
    source = isolated.source
    original = source.request
    supplemental_calls = []
    errors = iter(terminal_errors)

    def request(path, params=None, *, before_attempt=None, **options):
        if options:
            supplemental_calls.append((path, params, options))
            if path == discovery.QUOTES:
                error = next(errors, None)
                if error is not None:
                    before_attempt()
                    raise error
        return original(path, params, before_attempt=before_attempt)

    def hook(path, params):
        if path != discovery.OPPORTUNITIES:
            return None
        with core.connect() as connection:
            assert connection.execute('SELECT count(*) FROM market_auction_snapshot').fetchone()[0] == 1
        if late:
            isolated.clock[0] = NOW.replace(minute=30)
        day_stamp = int(NOW.timestamp() * 1000)
        return {'code': 0, 'data': {'trade_date': DAY, 'mode': mode, 'timestamp': day_stamp,
            'pagination': {'page': params['page'], 'size': 20, 'total': 3, 'has_next': False},
            'item': [{'thscode': code, 'warn_type': 68710, 'warn_name': '试盘合成', 'warn_timestamp': day_stamp - 60000}
                     for code in [*OPPORTUNITY_CODES, OPPORTUNITY_CODES[0]]]}}
    source.hook = hook
    isolated.monkeypatch.setattr(source, 'request', request)
    return supplemental_calls


def test_discovery_is_opt_in_and_cannot_expand_an_already_frozen_task(isolated):
    pending = claim(isolated)
    with core.connect() as connection:
        automation.configure_discovery(connection, enabled=True, expected_revision=automation.discovery_settings(connection)['revision'])
    calls = provider(isolated)
    result = auction_service.execute_capture(pending)
    assert result['snapshot_id']
    assert not calls
    assert discovery.read_view(DAY)['state'] == 'not_collected'


def test_discovery_verifies_new_names_then_reads_independently(isolated):
    authorize()
    from app.services import auction_topic_summary

    def topic_projection(payload):
        codes = [item['instrument_id'] for item in payload['quotes']['items']]
        assert set(codes) == set(OPPORTUNITY_CODES)
        tag = {'topic_code': '884001.TI', 'kind': 'industry', 'name': '测试行业',
               'members_observed_date': PREVIOUS}
        return {'source_sha256': 'a' * 64, 'source_snapshot_id': 'market-' + 'b' * 24,
                'coverage': {'unclassified': 0, 'rejected_groups': 0},
                'groups': [{**tag, 'instrument_ids': codes}],
                'stock_classifications': {code: {'industries': [tag]} for code in codes}}

    isolated.monkeypatch.setattr(auction_topic_summary, 'build_topic_summary', topic_projection)
    calls = provider(isolated)
    pending = claim(isolated)
    outcome = worker.dispatch(pending)
    assert outcome is not False
    assert [call[0] for call in calls] == [discovery.OPPORTUNITIES, discovery.QUOTES]
    assert all(call[2] == {'max_attempts': 1, 'timeout': 4.0} for call in calls)
    assert calls[-1][1]['thscodes'] == ','.join(OPPORTUNITY_CODES)
    isolated.monkeypatch.setattr(isolated.source, 'request', lambda *_a, **_kw: pytest.fail('GET must stay offline'))
    api = isolated.client.get('/api/v1/market/auction/discovery', params={'date': DAY})
    assert api.status_code == 200, api.text
    view = api.json()
    assert view['state'] == 'available', view
    assert view['coverage'] == {'pages': 1, 'events': 2, 'candidates': 2, 'valid': 2, 'has_more': False, 'total': 3}
    assert view['directions'][0]['stock_count'] == 2
    assert view['directions'][0]['total_auction_amount'] == 220000
    assert view['stocks'][0]['pre_close_price'] == 10
    assert view['stocks'][0]['themes'] == [{'topic_code': '884001.TI', 'kind': 'industry', 'name': '测试行业',
                                           'member_date': PREVIOUS}]
    assert view['directions'][0]['stocks'][0]['themes_state'] == 'observed_sample'
    assert 'evidence' not in view
    with core.connect() as connection:
        validate_snapshots(connection)
        assert connection.execute('SELECT count(*) FROM market_auction_discovery').fetchone()[0] == 1


@pytest.mark.parametrize('mode,late', [('history', False), ('real_time', True)])
def test_unverified_discovery_never_loses_primary(isolated, mode, late):
    if late:
        isolated.clock[0] = NOW.replace(minute=29, second=55)
    authorize()
    calls = provider(isolated, mode=mode, late=late)
    result = auction_service.execute_capture(claim(isolated))
    assert result['snapshot_id']
    assert [call[0] for call in calls] == [discovery.OPPORTUNITIES]
    assert discovery.read_view(DAY)['state'] == 'unavailable'
    assert auction_service.read_view(DAY)['state'] == 'available'


def test_terminal_request_failure_is_distinct_from_incomplete_quotes(isolated):
    authorize()
    isolated.monkeypatch.setattr(discovery, 'sleep', lambda _delay: None)
    with core.connect() as connection:
        connection.executemany("""INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
            added_business_date,event_basis_json,created_at_ms) VALUES(?,'stock','synthetic',?,'{}',?)""",
            [(f'{index:06d}.SZ', PREVIOUS, int(NOW.timestamp() * 1000) - 1) for index in range(105)])
    error = ProviderError('Fixture terminal error', code=5003)
    calls = provider(isolated, terminal_errors=(error, error))
    auction_service.execute_capture(claim(isolated))
    assert auction_service.read_view(DAY)['state'] == 'available'
    assert [call[0] for call in calls] == [discovery.OPPORTUNITIES, discovery.QUOTES, discovery.QUOTES]
    with core.connect() as connection:
        stored = json.loads(connection.execute('SELECT payload_json FROM market_auction_discovery').fetchone()[0])
        assert '部分候选未取得同日有效竞价终态。' in stored['reason']
        assert stored['sources'][-1]['code'] == 5003

    api = isolated.client.get('/api/v1/market/auction/discovery', params={'date': DAY})
    assert api.status_code == 200, api.text
    view = api.json()
    assert view['state'] == 'unavailable'
    assert view['coverage']['candidates'] == 2 and view['coverage']['valid'] == 0
    assert view['supplement']['requested'] > 0 and view['supplement']['valid'] == 0
    assert '竞价终态报价重试后仍失败，本轮待查股票未核验。' in view['reason']
    assert '部分候选未取得同日有效竞价终态。' not in view['reason']
    assert 'sources' not in view and 'evidence' not in view


@pytest.mark.parametrize('code', [5002, '5003'])
def test_terminal_retry_saves_only_verified_retry_response(isolated, code):
    from app.services.auction_integrity import validate_discovery

    authorize()
    waits = []
    isolated.monkeypatch.setattr(discovery, 'sleep', waits.append)
    calls = provider(isolated, terminal_errors=(ProviderError('Do not save this error message', code=code),))
    auction_service.execute_capture(claim(isolated))
    assert waits == [2.0]
    assert calls[1] == calls[2] == (discovery.QUOTES,
                                   {'thscodes': ','.join(OPPORTUNITY_CODES), 'stage': 'final'},
                                   {'max_attempts': 1, 'timeout': 4.0})
    with core.connect() as connection:
        validate_snapshots(connection)
        row = dict(connection.execute('SELECT * FROM market_auction_discovery').fetchone())
    stored = validate_discovery(row)
    assert 'Do not save this error message' not in row['payload_json']
    assert [(source['name'], source['state']) for source in stored['sources']] == [
        ('opportunities:1', 'received'), ('final_quotes', 'received')]
    assert stored['sources'][1]['first_failure']['code'] == int(code)
    assert [source['name'] for source in stored['evidence']] == ['opportunities:1', 'final_quotes']
    assert len(discovery.read_view(DAY)['stocks']) == 2
    assert '请求失败' not in (discovery.read_view(DAY)['reason'] or '')
    stored['sources'][1]['first_failure']['code'] = 4001
    encoded = encode(stored)
    with pytest.raises(ValueError, match='retry evidence'):
        validate_discovery({**row, 'payload_json': encoded.decode(), 'content_sha256': sha256(encoded).hexdigest()})


@pytest.mark.parametrize('scenario', ['rate_limit', 'permission', 'legacy', 'deadline', 'authorization_changed'])
def test_terminal_retry_preserves_existing_guards_and_legacy_budget(isolated, scenario):
    authorize()
    if scenario == 'legacy':
        original = automation.creation_authorization

        def legacy_authorization(*args):
            proof = original(*args)
            proof['discovery'].pop('terminal_retry')
            return proof

        isolated.monkeypatch.setattr(automation, 'creation_authorization', legacy_authorization)
    if scenario == 'deadline':
        isolated.clock[0] = NOW.replace(minute=29, second=56)

    def wait(_delay):
        assert scenario == 'authorization_changed'
        with core.connect() as connection:
            automation.configure_discovery(connection, enabled=False,
                                           expected_revision=automation.discovery_settings(connection)['revision'])

    isolated.monkeypatch.setattr(discovery, 'sleep', wait)
    code = {'rate_limit': 4001, 'permission': 2003}.get(scenario, 5003)
    calls = provider(isolated, terminal_errors=(ProviderError('Fixture terminal error', code=code),))
    auction_service.execute_capture(claim(isolated))
    assert auction_service.read_view(DAY)['state'] == 'available'
    assert [call[0] for call in calls] == [discovery.OPPORTUNITIES, discovery.QUOTES]
    view = discovery.read_view(DAY)
    assert view['state'] == 'unavailable'
    assert view['coverage']['candidates'] == 2
    assert '竞价终态报价请求失败' in view['reason']


def test_terminal_retry_does_not_publish_response_arriving_after_auction(isolated):
    isolated.clock[0] = NOW.replace(minute=29, second=52)
    authorize()
    isolated.monkeypatch.setattr(discovery, 'sleep', lambda _delay: None)
    calls = provider(isolated, terminal_errors=(ProviderError('Fixture terminal error', code=5003),))
    hook = isolated.source.hook

    def late_response(path, params):
        if path == discovery.QUOTES and len(calls) == 3:
            isolated.clock[0] = NOW.replace(minute=30)
        return hook(path, params)

    isolated.source.hook = late_response
    auction_service.execute_capture(claim(isolated))
    assert [call[0] for call in calls] == [discovery.OPPORTUNITIES, discovery.QUOTES, discovery.QUOTES]
    assert auction_service.read_view(DAY)['state'] == 'available'
    assert discovery.read_view(DAY)['stocks'] == []
    with core.connect() as connection:
        validate_snapshots(connection)
        stored = json.loads(connection.execute('SELECT payload_json FROM market_auction_discovery').fetchone()[0])
    assert stored['sources'][-1]['state'] == 'not_accepted'
    assert stored['sources'][-1]['first_failure']['code'] == 5003
    assert [source['name'] for source in stored['evidence']] == ['opportunities:1']


def test_discovery_caps_pages_and_combines_overflow_without_duplicate_ranking(isolated):
    authorize()
    with core.connect() as connection:
        for index in range(105):
            connection.execute("""INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
                added_business_date,event_basis_json,created_at_ms) VALUES(?,'stock','synthetic',?,'{}',?)""",
                (f'{index:06d}.SZ', PREVIOUS, int(NOW.timestamp() * 1000) - 1))
    calls = provider(isolated)

    def paged(path, params):
        if path != discovery.OPPORTUNITIES:
            return None
        page = params['page']
        stamp = int(NOW.timestamp() * 1000)
        stocks = [f'600{20 * page + index:03d}.SH' for index in range(19)] + ['000104.SZ']
        return {'code': 0, 'data': {'trade_date': DAY, 'mode': 'real_time', 'timestamp': stamp,
            'pagination': {'page': page, 'size': 20, 'total': 200, 'has_next': True},
            'item': [{'thscode': code, 'warn_type': '68710', 'warn_timestamp': stamp - 60000}
                     for code in stocks]}}

    isolated.source.hook = paged
    auction_service.execute_capture(claim(isolated))
    assert [call[1]['page'] for call in calls[:-1]] == [1, 2, 3]
    assert calls[-1][0] == discovery.QUOTES
    assert len(calls[-1][1]['thscodes'].split(',')) <= 100
    view = discovery.read_view(DAY)
    assert view['state'] == 'partial'
    assert view['coverage']['has_more'] is True
    assert view['coverage']['valid'] == 57
    assert view['supplement']['valid'] == view['supplement']['requested'] > 0
    assert view['supplement']['remaining'] == 0
    assert '000104.SZ' in {row['instrument_id'] for row in view['supplement']['items']}
    assert '000104.SZ' not in {row['instrument_id'] for row in view['stocks']}
    from app.services import intraday_stock_detail
    observed = []

    def history(day, slot, symbols):
        observed.append((day, slot, symbols))
        return {'date': day, 'slot': slot, 'as_of': PREVIOUS, 'items': {}}

    isolated.monkeypatch.setattr(intraday_stock_detail, 'history_for_symbols', history)
    response = isolated.client.get('/api/v1/market/auction/discovery/history', params={'date': DAY})
    assert response.status_code == 200, response.text
    assert response.json()['as_of'] == PREVIOUS
    assert len(observed) == 1 and observed[0][:2] == (DAY, 'auction')
    assert observed[0][2] == [row['instrument_id'] for row in [*view['stocks'], *view['supplement']['items']]]


def test_discovery_is_retained_and_purged_with_exact_primary(local):
    from app.services import auction_retention

    parent_id, source_job = capture()
    date = '2026-10-28'
    payload = discovery._empty(date, 'unavailable', 'synthetic expired data')
    payload.update(sources=[], evidence=[], requested=[])
    encoded = encode(payload)
    with core.connect() as connection:
        connection.execute('INSERT INTO market_auction_discovery VALUES(?,?,?,?,?,?)',
                           (date, source_job['job_id'], parent_id, core.now_ms(), sha256(encoded).hexdigest(), encoded.decode()))
        validate_snapshots(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute('DELETE FROM market_auction_discovery')
    increment = job('job-increment-discovery')
    publication(increment['job_id'])
    assert auction_retention.after_increment(increment)['protected_snapshots'] == 1
    assert discovery.read_view(date)['state'] == 'unavailable'
    increment = job('job-increment-discovery-authorized', extra={
        'auction_discovery_retention_policy': auction_retention.DISCOVERY_POLICY})
    publication(increment['job_id'])
    assert auction_retention.after_increment(increment)['purged_snapshots'] == 1
    with core.connect() as connection:
        validate_snapshots(connection)
        assert connection.execute('SELECT count(*) FROM market_auction_discovery').fetchone()[0] == 0
    assert discovery.read_view(date)['state'] == 'purged'
