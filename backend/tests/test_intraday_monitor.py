"""Real SQLite/worker/API paths with synthetic local history and source replies."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
from hashlib import sha256
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import core, worker
from app.routers.intraday_monitor import router
from app.services import source_service
from app.services import intraday_monitor as service
from app.services import intraday_monitor_levels as price_levels
from app.services import intraday_monitor_storage as storage
from app.services import task_service as tasks
from app.services.noon_collection import CHINA, canonical
from test_workspace import ws, make_strategy


SYMBOL = '600001.SH'
BASE = '/api/v1/intraday-monitor'


def test_public_metrics_retains_real_zero_and_incomplete_window_explanation_without_aliases():
    value = service._public_metrics({'board': 'main', 'change_30d_pct': 0.0, 'return_ytd_pct': 1.2,
        'limit_up_30d': 0, 'limit_up_1y': None, 'limit_up_1y_observed': 2,
        'limit_up_1y_complete': False, 'limit_up_1y_reason': '年度来源缺档',
        'limit_up_ytd': 2, 'last_limit_up_date': '2026-11-10', 'sessions_since_limit_up': 3,
        'annual_return_pct': 1.2, 'annual_limit_up_count_coverage': {'unused': True},
        'limit_up_1y_start': '2025-11-14'})
    assert value['change_30d_pct'] == 0.0 and value['limit_up_30d'] == 0
    assert value['limit_up_1y'] is None and value['limit_up_1y_observed'] == 2
    assert value['limit_up_1y_start'] == '2025-11-14'
    assert value['metrics_reasons'] == {'limit_up_1y': '年度来源缺档'}
    assert value['coverage_unknown'] is True
    assert not {'annual_return_pct', 'annual_limit_up_count_coverage'}.intersection(value)


class Source:
    key = 'synthetic-intraday-source-key'

    def __init__(self, market, clock):
        self.market, self.clock = market, clock
        self.days = list(market.days)
        self.calls = []
        self.price = 20
        self.calendar_mode = 'ready'
        self.on_calendar = self.on_quote = None

    def request(self, endpoint, params=None, *, before_attempt=None, **options):
        # A real transport must never be called inside a SQLite write txn.
        with core.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
        if before_attempt:
            before_attempt()
        self.calls.append({'endpoint': endpoint, 'params': deepcopy(params), 'options': options})
        if endpoint.endswith('/calendar/trading-days'):
            if self.on_calendar:
                self.on_calendar()
            if self.calendar_mode == 'failed':
                raise ValueError('synthetic calendar failure')
            days = [day for day in self.days if self.calendar_mode != 'closed' or day != self.clock[0].date().isoformat()]
            return {'code': 0, 'data': {'item': [{'date': day.replace('-', '')} for day in days]}}
        assert endpoint == '/api/a-share/prices/snapshot'
        assert options['max_attempts'] == 1
        if self.on_quote:
            self.on_quote()
        previous = self.market.bars(SYMBOL, self.market.entry)[-1]['close']
        price = self.price
        rows = [{'thscode': symbol, 'last_price': price, 'prev_price': previous,
                 'open_price': previous, 'high_price': max(previous, price) + .1,
                 'low_price': min(previous, price) - .1, 'volume': 1000, 'turnover': 100000,
                 'price_change_ratio_pct': (price / previous - 1) * 100,
                 'price_change': price - previous} for symbol in params['thscodes'].split(',')]
        return {'code': 0, 'data': {'item': rows, 'timestamp': int(self.clock[0].timestamp() * 1000)}}


@pytest.fixture
def monitored(ws, monkeypatch):
    market, client = ws
    client.app.include_router(router)
    lows = [12, 11, 10, 12, 13, 14, 13, 10.1, 14, 15, 16, 17]
    highs = [19, 20, 21, 23, 21, 19, 18, 19, 21, 19, 18, 18]
    for rows in market.data.values():
        for index, row in enumerate(rows):
            lo, hi = lows[index % 12], highs[index % 12]
            row.update(low=lo, high=hi, open=(lo + hi) / 2, close=(lo + hi) / 2,
                       complete=True, corporate_action_coverage_known=True)
        rows[70].update(low=14, high=16, open=15, close=15)
    day = market.days[71]
    clock = [datetime.fromisoformat(day + 'T09:30:05').replace(tzinfo=CHINA)]
    head = {'primary_snapshot_id': 'raw', 'market_snapshot_id': 'raw', 'business_date': market.entry}
    monkeypatch.setattr(service, '_head', lambda _connection: dict(head))
    monkeypatch.setattr(service, '_configured', lambda _connection: True)
    monkeypatch.setattr(service, 'get_market', lambda: market)
    monkeypatch.setattr(service, 'now_china', lambda: clock[0])
    monkeypatch.setattr(service, 'now_ms', lambda: int(clock[0].timestamp() * 1000))
    monkeypatch.setattr(tasks, 'now_ms', lambda: int(clock[0].timestamp() * 1000))
    monkeypatch.setattr(service, '_next_check', 0)
    market.history_calls, market.action_calls = [], []

    def histories(ids, *, start, end):
        market.history_calls.append((list(ids), start, end))
        return {symbol: [row for row in market.bars(symbol, end) if row['date'] >= start] for symbol in ids}

    def actions(ids):
        market.action_calls.append(list(ids))
        return {symbol: [] for symbol in ids}

    monkeypatch.setattr(market, 'history_for_instruments', histories, raising=False)
    monkeypatch.setattr(market, 'actions_for_instruments', actions, raising=False)
    monkeypatch.setattr(market, 'status', lambda: {'ready': True, 'snapshot_id': 'raw', 'data_end': market.entry}, raising=False)
    source = Source(market, clock)
    monkeypatch.setattr(source_service, 'client', lambda *_args: source)
    with core.connect() as connection:
        connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
            added_business_date,event_basis_json,created_at_ms) VALUES(?,'stock','合成测试自选',?,'{}',?)''',
            (SYMBOL, market.entry, int(clock[0].timestamp() * 1000)))
    env = SimpleNamespace(market=market, client=client, source=source, clock=clock,
                          day=day, head=head, monotonic=100)
    set_clock(env, '09:30:05')
    return env


def set_clock(env, time):
    env.clock[0] = datetime.fromisoformat(env.day + 'T' + time).replace(tzinfo=CHINA)
    with core.connect() as connection:
        service._save(connection, 'worker_heartbeat', {'at_ms': int(env.clock[0].timestamp() * 1000)})


def payload(env, *, enabled=True, expected=None, **extra):
    if expected is None:
        expected = env.client.get(BASE).json()['settings']['revision']
    scopes = {key: key != 'gainers_30d' for key in service.SCOPES}
    return {'expected_revision': expected, 'enabled': enabled, 'scopes': scopes,
            'manual_enabled': True, 'automatic_enabled': True, **extra}


def enable(env, **extra):
    response = env.client.put(BASE + '/settings', json=payload(env, **extra))
    assert response.status_code == 200, response.text
    return response.json()


def monitor_jobs():
    with core.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM job WHERE json_extract(parameters_json,'$.operation')='intraday_monitor' ORDER BY rowid")]


def count(table):
    assert table in {'job', 'tracking_round', 'line_alert_event', 'automatic_price_alert_event',
                     'intraday_monitor_state', 'intraday_monitor_event', 'intraday_monitor_evidence', 'intraday_monitor_batch'}
    with core.connect() as connection:
        return connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]


def tick(env):
    env.monotonic += 31
    return service.tick_automatic(at=env.clock[0], monotonic=env.monotonic)


def run_slot(env, time, price):
    set_clock(env, time)
    env.source.price = price
    identifier = tick(env)
    assert identifier, service.read_view()['status']
    claimed = tasks.claim_next('synthetic-monitor-worker')
    assert claimed and claimed['job_id'] == identifier
    worker.dispatch(claimed)
    with core.connect() as connection:
        job = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone())
        batch = dict(connection.execute('SELECT * FROM intraday_monitor_batch WHERE job_id=?', (identifier,)).fetchone())
    assert job['status'] in ('succeeded', 'partial'), job
    return job, batch


def add_line(env, price, *, symbol=SYMBOL, modes=None, kind='resistance', identifier='synthetic-monitor-line'):
    with core.connect() as connection:
        connection.execute('''INSERT INTO chart_annotation(annotation_id,instrument_id,price,label,color,
            price_basis,created_at_ms,updated_at_ms,line_type,alert_modes_json,auto_watchlist,revision,
            effective_business_date,instrument_kind) VALUES(?,?,?,'合成人工压力','red','none',?,?,?,?,0,1,?,'stock')''',
            (identifier, symbol, price, service.now_ms(), service.now_ms(), kind,
             json.dumps(modes if modes is not None else ['pressure_close_breakout']), env.market.entry))


def test_default_get_settings_and_refresh_never_collect_or_create_jobs(monitored):
    env = monitored
    before = count('job')
    view = env.client.get(BASE)
    assert view.status_code == 200
    data = view.json()
    assert data['settings']['enabled'] is False and data['settings']['revision'] == 0
    assert data['settings']['interval_minutes'] == 10
    if 'gainers_30d' in service.SCOPES:
        assert data['settings']['scopes']['gainers_30d'] is True
    assert data['status']['state'] == 'disabled'
    for path in (BASE, BASE + '/summary', BASE + '/events'):
        assert env.client.get(path).status_code == 200
    assert env.client.put(BASE + '/settings', json=payload(env, enabled=False)).status_code == 200
    assert tick(env) is None
    assert env.source.calls == [] and monitor_jobs() == [] and count('job') == before
    assert env.market.history_calls == [] and env.market.action_calls == []


@pytest.mark.parametrize('change', ['enabled_int', 'revision_bool', 'revision_stale', 'missing_scope',
                                  'scope_int', 'empty_scopes', 'empty_sources', 'interval_override', 'extra'])
def test_settings_validate_full_boolean_scope_and_revision_without_side_effects(monitored, change):
    env = monitored
    values = payload(env, expected=0)
    expected_status = 422
    if change == 'enabled_int': values['enabled'] = 1
    elif change == 'revision_bool': values['expected_revision'] = True
    elif change == 'revision_stale':
        values['expected_revision'], expected_status = 1, 409
    elif change == 'missing_scope': values['scopes'].pop(next(iter(values['scopes'])))
    elif change == 'scope_int': values['scopes']['tracking'] = 1
    elif change == 'empty_scopes': values['scopes'] = {key: False for key in values['scopes']}
    elif change == 'empty_sources': values.update(manual_enabled=False, automatic_enabled=False)
    elif change == 'interval_override': values['interval_minutes'] = 1
    else: values['unexpected'] = 'no'
    response = env.client.put(BASE + '/settings', json=values)
    assert response.status_code == expected_status, response.text
    assert env.source.calls == [] and monitor_jobs() == []
    assert env.client.get(BASE).json()['settings']['enabled'] is False


@pytest.mark.parametrize('missing', ['head', 'source', 'heartbeat', 'stocks'])
def test_enable_requires_initialized_source_alive_worker_and_nonempty_scope(monitored, monkeypatch, missing):
    env = monitored
    if missing == 'head': monkeypatch.setattr(service, '_head', lambda _connection: None)
    elif missing == 'source': monkeypatch.setattr(service, '_configured', lambda _connection: False)
    elif missing == 'heartbeat':
        with core.connect() as connection:
            service._save(connection, 'worker_heartbeat', {'at_ms': service.now_ms() - 90_001})
    else:
        with core.connect() as connection: connection.execute('DELETE FROM watchlist_item')
    response = env.client.put(BASE + '/settings', json=payload(env, expected=0))
    assert response.status_code == 409 and env.source.calls == [] and monitor_jobs() == []


def test_scope_union_deduplicates_multiple_strategies_and_manual_members(monitored):
    env = monitored
    for index in range(2):
        strategy = make_strategy(env.client, {'field': 'close', 'op': '>', 'value': 0})
        run = env.client.post('/api/v1/strategies/' + strategy['strategy_id'] + '/runs', json={})
        assert run.status_code == 200, run.text
        joined = env.client.post('/api/v1/tracking-rounds', json={'run_id': run.json()['run_id'], 'instrument_ids': [SYMBOL]})
        assert joined.status_code == 200, joined.text
    add_line(env, 20)
    add_line(env, 19, identifier='unarmed-reference', kind='reference', modes=[])
    view = env.client.get(BASE).json()
    assert view['counts']['stocks'] == 1
    assert view['counts']['tracking'] == view['counts']['watchlist'] == view['counts']['manual_lines'] == 1
    assert view['stocks'][0]['strategy_count'] == 2 and view['stocks'][0]['manual_count'] == 1
    assert set(view['stocks'][0]['origins']) == {'tracking', 'watchlist', 'manual_lines'}
    enable(env)
    assert monitor_jobs() == [] and env.source.calls == []


def test_union_limit_is_explicit_and_never_silently_truncated(monitored):
    env = monitored
    with core.connect() as connection:
        connection.execute('DELETE FROM watchlist_item')
        connection.executemany('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
            added_business_date,event_basis_json,created_at_ms) VALUES(?,'stock','合成容量测试',?,'{}',?)''',
            [(f'{index:06d}.SH', env.market.entry, service.now_ms()) for index in range(1, service.MAX_STOCKS + 1)])
    add_line(env, 20)
    view = env.client.get(BASE).json()
    assert view['counts']['stocks'] == service.MAX_STOCKS + 1 and view['counts']['excluded'] == 1
    assert view['status']['state'] == 'blocked'
    response = env.client.put(BASE + '/settings', json=payload(env, expected=0))
    assert response.status_code == 409 and '不会静默' in response.text
    assert env.source.calls == [] and monitor_jobs() == []


@pytest.mark.parametrize('mode', ['failed', 'closed'])
def test_calendar_failure_or_exchange_closed_day_does_not_collect_or_retry_same_day(monitored, mode):
    env = monitored
    enable(env)
    env.source.calendar_mode = mode
    assert tick(env) is None and monitor_jobs() == []
    assert len(env.source.calls) == 1 and env.source.calls[0]['endpoint'].endswith('/calendar/trading-days')
    set_clock(env, '09:40:05')
    assert tick(env) is None
    assert len(env.source.calls) == 1 and monitor_jobs() == []
    with core.connect() as connection:
        receipt = service._json(connection, service.CALENDAR_KEY, {})
    assert receipt['date'] == env.day
    assert receipt['state'] == ('failed' if mode == 'failed' else 'ready')


@pytest.mark.parametrize('time', ['09:29:59', '09:31:00', '11:40:05', '12:00:05', '13:00:05', '15:10:05'])
def test_scheduler_never_catches_up_outside_creation_slots(monitored, time):
    env = monitored
    enable(env)
    set_clock(env, time)
    assert tick(env) is None and monitor_jobs() == [] and env.source.calls == []


@pytest.mark.parametrize('weekend', ['2026-04-18', '2026-04-19'])
def test_weekends_do_not_request_a_calendar_or_a_quote(monitored, weekend):
    env = monitored
    enable(env)
    env.day = weekend
    set_clock(env, '09:30:05')
    assert tick(env) is None and monitor_jobs() == [] and env.source.calls == []


def test_same_slot_freezes_one_immutable_task_and_generic_creation_is_forbidden(monitored):
    env = monitored
    enabled = enable(env)
    assert enable(env)['revision'] == enabled['revision']
    identifier = tick(env)
    assert identifier and tick(env) is None
    with core.connect() as connection:
        job = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone())
    frozen = json.loads(job['parameters_json'])
    assert frozen['config'] == enabled and frozen['slot_key'] == '09:30'
    assert frozen['universe'][0]['instrument_id'] == SYMBOL
    assert frozen['head'] == env.head and len(frozen['calendar_sha256']) == 64
    assert frozen['scheduled_at'].endswith('09:30:00+08:00')
    assert frozen['deadline_at'].endswith('09:32:00+08:00')
    assert job['parameters_hash'] == core.digest(frozen)
    assert count('intraday_monitor_batch') == 1
    with pytest.raises(HTTPException) as error:
        tasks.create_task('source_check', {'operation': service.OPERATION}, request_key='manual-monitor')
    assert error.value.status_code == 403
    tasks.change_task(identifier, 'cancel')
    assert tick(env) is None and len(monitor_jobs()) == 1


def test_concurrent_scheduler_ticks_still_register_one_task_for_the_slot(monitored):
    env = monitored
    enable(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda counter: service.tick_automatic(at=env.clock[0], monotonic=counter), (1000, 1100)))
    assert sum(result is not None for result in results) == 1
    assert len(monitor_jobs()) == count('intraday_monitor_batch') == 1
    assert len(env.source.calls) == 1


def test_disabling_while_calendar_request_returns_prevents_late_task_creation(monitored):
    env = monitored
    enable(env)
    env.source.on_calendar = lambda: enable(env, enabled=False)
    assert tick(env) is None
    assert monitor_jobs() == [] and len(env.source.calls) == 1


def test_disabled_configuration_rejects_queued_capture_before_any_source_call(monitored):
    env = monitored
    enable(env)
    identifier = tick(env)
    enable(env, enabled=False)
    claimed = tasks.claim_next('synthetic-worker')
    assert claimed['job_id'] == identifier
    with pytest.raises(ValueError, match='设置'):
        worker.dispatch(claimed)
    assert count('intraday_monitor_state') == count('intraday_monitor_event') == 0
    assert len(env.source.calls) == 1  # Calendar only.
    with core.connect() as connection:
        assert connection.execute('SELECT status FROM intraday_monitor_batch').fetchone()[0] == 'failed'


def test_explicit_pause_is_not_resumed_or_retried_and_stale_lease_cannot_collect(monitored):
    env = monitored
    enable(env)
    identifier = tick(env)
    tasks.change_task(identifier, 'pause')
    assert tasks.claim_next('other-worker') is None
    for action in ('resume', 'retry'):
        with pytest.raises(HTTPException) as error:
            tasks.change_task(identifier, action)
        assert error.value.status_code == 409
    assert tick(env) is None
    tasks.change_task(identifier, 'cancel')
    set_clock(env, '09:40:05')
    second = tick(env)
    job = tasks.claim_next('synthetic-worker')
    assert job['job_id'] == second
    with pytest.raises(tasks.TaskCancelled):
        worker.dispatch({**job, 'lease_owner': 'stale-worker'})
    assert count('intraday_monitor_state') == count('intraday_monitor_event') == 0
    assert len([call for call in env.source.calls if call['endpoint'].endswith('/prices/snapshot')]) == 0


def test_late_quote_after_disable_cannot_publish_states_events_or_source_files(monitored):
    env = monitored
    enable(env)
    identifier = tick(env)
    claimed = tasks.claim_next('synthetic-worker')
    assert claimed['job_id'] == identifier
    env.source.on_quote = lambda: enable(env, enabled=False)
    with pytest.raises(ValueError, match='设置'):
        worker.dispatch(claimed)
    assert count('intraday_monitor_state') == count('intraday_monitor_event') == 0
    assert not list(core.raw_dir().glob('intraday-monitor/**/*.json.gz'))


def test_scope_change_while_response_returns_cannot_commit_frozen_old_universe(monitored):
    env = monitored
    enable(env)
    identifier = tick(env)
    claimed = tasks.claim_next('synthetic-worker')
    assert claimed['job_id'] == identifier
    def add_stock():
        with core.connect() as connection:
            connection.execute('''INSERT INTO watchlist_item(instrument_id,source_kind,source_name,
                added_business_date,event_basis_json,created_at_ms) VALUES('000001.SZ','stock','合成新增',?,'{}',?)''',
                (env.market.entry, service.now_ms()))
    env.source.on_quote = add_stock
    with pytest.raises(ValueError, match='关注范围'):
        worker.dispatch(claimed)
    assert count('intraday_monitor_state') == count('intraday_monitor_event') == 0
    assert not list(core.raw_dir().glob('intraday-monitor/**/*.json.gz'))


def test_real_worker_three_slots_persist_sha_states_and_merge_source_events_without_old_mutations(monitored):
    env = monitored
    initial = price_levels.automatic_baseline(SYMBOL, env.market.bars(SYMBOL, env.market.entry),
        previous_day=env.market.entry, calendar=env.market.days, snapshot='raw', primary_snapshot='raw')
    zone = next(item for item in initial['levels'] if item['kind'] == 'resistance' and item['source_kind'] == 'automatic_zone')
    add_line(env, zone['upper'])
    enable(env)
    preserved = {table: count(table) for table in ('tracking_round', 'line_alert_event', 'automatic_price_alert_event')}
    _, seeded = run_slot(env, '09:30:05', zone['lower'] - .5)
    assert seeded['event_count'] == 0 and seeded['valid_count'] == 1
    _, breakout = run_slot(env, '09:40:05', zone['upper'] + .5)
    events = env.client.get(BASE + '/events').json()
    assert events['date'] == env.day and events['chart_as_of'] == env.market.entry
    assert events['total'] == events['unread_count'] == 1
    event = events['items'][0]
    assert event['type'] == 'pressure_breakout' and event['sampling_only'] is True
    assert {source['source_kind'] for source in event['sources']} == {'manual_line', 'automatic_zone'}
    assert all(source['source_time_ms'] and source['observed_at_ms'] for source in event['sources'])
    assert breakout['event_count'] == 1
    assert env.client.get(BASE + '/events').json()['unread_count'] == 1
    assert env.client.get(BASE + '/events/read').status_code == 405
    assert env.client.put(BASE + '/events/read', json={'event_ids': [event['event_id']]}).status_code == 405
    read = env.client.post(BASE + '/events/read', json={'event_ids': [event['event_id']]})
    assert read.status_code == 200 and read.json()['updated'] == 1
    assert env.client.get(BASE + '/summary').json()['unread_count'] == 0
    _, held = run_slot(env, '09:50:05', zone['upper'] + .7)
    later = env.client.get(BASE + '/events').json()
    assert later['total'] == 1 and later['unread_count'] == 1
    assert later['items'][0]['phase'] == 'holding' and later['items'][0]['confirmed_at_ms']
    assert all(source.get('confirmed_at_ms') for source in later['items'][0]['sources'])
    assert held['event_count'] == 1 and count('intraday_monitor_evidence') == 4
    assert len(env.market.history_calls) == 1  # Daily level preparation is reused.
    assert all(count(table) == before for table, before in preserved.items())
    assert count('intraday_monitor_state') == 1 and len(monitor_jobs()) == 3
    assert len([call for call in env.source.calls if call['endpoint'].endswith('/calendar/trading-days')]) == 1
    assert len([call for call in env.source.calls if call['endpoint'].endswith('/prices/snapshot')]) == 3
    with core.connect() as connection:
        batches = [dict(row) for row in connection.execute('SELECT * FROM intraday_monitor_batch ORDER BY slot_key')]
        assert connection.execute('SELECT count(*) FROM retention_pin WHERE owner_id LIKE ?', ('intraday-monitor:%',)).fetchone()[0] == 3
    for batch in batches:
        manifest = json.loads(batch['manifest_json'])
        assert manifest['publication_state'] == 'published'
        for key in ('quote_file', 'raw_file'):
            path = storage.verify_file(manifest[key])
            assert sha256(path.read_bytes()).hexdigest() == manifest[key]['sha256']
        with gzip.open(storage.checked_path(manifest['raw_file']), 'rt') as reader:
            frozen = json.load(reader)
        capture = frozen['capture']
        assert frozen['date'] == env.day and frozen['batch_id'] == batch['batch_id']
        assert capture['quotes_by_id'][SYMBOL]['quality'] == 'batch_time_only'
        for source in capture['sources']:
            assert sha256(canonical(source['payload'])).hexdigest() == source['sha256']
    summary = env.client.get(BASE + '/summary').json()
    assert summary['date'] == env.day and summary['chart_as_of'] == env.market.entry


def test_event_date_is_independent_of_published_close_and_invalid_read_payload_is_rejected(monitored):
    env = monitored
    assert env.client.get(BASE + '/events?date=' + env.market.entry).json()['date'] == env.market.entry
    assert env.client.get(BASE + '/events').json()['date'] == env.day
    assert env.client.get(BASE + '/events?date=invalid').status_code == 422
    for values in ({}, {'event_ids': []}, {'event_ids': ['one'], 'extra': True}, {'event_ids': [1]}):
        assert env.client.post(BASE + '/events/read', json=values).status_code == 422
    assert env.source.calls == [] and monitor_jobs() == []
