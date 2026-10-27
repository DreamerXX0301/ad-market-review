"""User-approved lifecycle, exercised through real SQLite/API/worker fixtures."""
import json
import sqlite3
import datetime as dt

import pytest

from app import core, worker
from app.services import line_alerts as alerts, line_alert_retention as retention, task_service as tasks
from test_line_alerts import lines, create, quote, drain
from test_watchlist import personal
from test_workspace import ws, advance, make_strategy, add_one, FixtureMarket


@pytest.fixture(autouse=True)
def long_fixture_calendar(monkeypatch):
    original = FixtureMarket.__init__
    def initialize(self):
        original(self)
        day = dt.date.fromisoformat(self.days[-1])
        while len(self.days) < 230:
            day += dt.timedelta(days=1)
            if day.weekday() >= 5:
                continue
            self.days.append(day.isoformat())
            for history in self.data.values():
                history.append({**history[-1], 'date': day.isoformat()})
    monkeypatch.setattr(FixtureMarket, '__init__', initialize)


def at(market, index):
    market.entry = market.days[index]
    advance(market, index)


def sync(market, *, policy=True):
    parameters = {'line_alert_check_version': alerts.VERSION}
    if policy:
        parameters['line_alert_retention_version'] = retention.VERSION
    parent = tasks.create_task('increment', parameters, target_date=market.entry)
    tasks.finish(parent['job_id'])
    with core.connect() as connection:
        saved = dict(connection.execute('SELECT * FROM job WHERE job_id=?', (parent['job_id'],)).fetchone())
    child = alerts.after_sync(saved)
    return parent, child


def line_ids(client):
    return [item['annotation_id'] for item in client.get('/api/v1/instruments/600001.SH/annotations').json()['items']]


def touch_line(market, client, index=70, **kwargs):
    at(market, index)
    quote(market, index, low=99, high=110, close=105)
    line = create(client, kind='resistance', modes=['pressure_touch'], auto=False, **kwargs)
    drain()
    return line


def test_never_triggered_lines_survive_more_than_60_market_sessions(lines):
    market, client = lines
    at(market, 70)
    line = create(client, price=10000, kind='resistance', modes=['pressure_touch'], auto=False)
    drain()
    at(market, 200)
    sync(market); drain()
    assert line_ids(client) == [line['annotation_id']]
    assert client.get('/api/v1/watchlist/line-alerts').json()['total'] == 0
    assert client.get('/api/v1/instruments/600001.SH/annotations').json()['items'][0]['first_alert_date'] is None


def test_trigger_day_zero_then_60_sessions_then_next_increment_deletes_only_owned_data(lines):
    market, client = lines
    line = touch_line(market, client)
    client.post('/api/v1/watchlist', json={'instrument_id':'600001.SH'})
    at(market, 130)
    quote(market, 130, low=99, close=105)
    strategy = make_strategy(client, definition={'field':'close','op':'>','value':0})
    _, tracking = add_one(client, strategy)
    sync(market); drain()
    assert line_ids(client) == [line['annotation_id']]
    with core.connect() as connection:
        before_watch = dict(connection.execute('SELECT * FROM watchlist_item').fetchone())
        before_round = dict(connection.execute('SELECT * FROM tracking_round WHERE round_id=?', (tracking['round_id'],)).fetchone())
        assert retention.first_alerts(connection)[line['annotation_id']] == market.days[70]
        connection.execute("INSERT INTO retention_pin VALUES('keep-unrelated','raw','manual','unrelated-source',?,NULL)", (core.now_ms(),))
    at(market, 131)
    # GET and a manual reminder check do not perform destructive cleanup.
    assert line_ids(client) == [line['annotation_id']]
    client.post('/api/v1/watchlist/line-alerts/evaluate', json={}); drain()
    assert line_ids(client) == [line['annotation_id']]
    _, child = sync(market); drain()
    assert line_ids(client) == []
    assert client.get('/api/v1/watchlist/line-alerts').json()['total'] == 0
    with core.connect() as connection:
        assert dict(connection.execute('SELECT * FROM watchlist_item').fetchone()) == before_watch
        assert dict(connection.execute('SELECT * FROM tracking_round WHERE round_id=?', (tracking['round_id'],)).fetchone()) == before_round
        assert connection.execute("SELECT 1 FROM retention_pin WHERE owner_id='unrelated-source'").fetchone()
        assert not connection.execute("SELECT 1 FROM retention_pin WHERE owner_id LIKE 'line-alert:%'").fetchone()
        assert not connection.execute('SELECT 1 FROM line_alert_evaluation WHERE annotation_id=?', (line['annotation_id'],)).fetchone()
        assert list(connection.execute('PRAGMA foreign_key_check')) == []
        progress = retention.checkpoint(connection, child)
        assert progress['complete'] and progress['removed_lines'] == 1 and progress['removed_alerts'] >= 2
    assert client.get('/api/v1/watchlist/line-alerts').json()['latest_check']['is_current'] is True


def test_reference_lines_and_indices_do_not_expire_without_any_reminder(lines):
    market, client = lines
    at(market, 70)
    quiet = client.post('/api/v1/instruments/600001.SH/annotations', json={'price':100,'label':'只显示'}).json()
    at(market, 210)
    sync(market); drain()
    assert quiet['annotation_id'] in line_ids(client)


def test_old_frozen_increment_has_no_new_deletion_authority(lines):
    market, client = lines
    line = touch_line(market, client)
    at(market, 131)
    _, child = sync(market, policy=False); drain()
    assert line['annotation_id'] in line_ids(client)
    assert 'line_alert_retention_version' not in tasks.task_view(child)['parameters']


def test_risk_alert_starts_same_deadline_and_editing_a_line_never_resets_it(lines):
    market, client = lines
    at(market, 70)
    quote(market, 69, close=105)
    quote(market, 70, low=95, close=99)
    line = create(client, modes=['support_breakdown'])
    drain()
    assert client.get('/api/v1/watchlist/line-alerts').json()['items'][0]['state'] == 'invalidated'
    assert client.get('/api/v1/watchlist/ids').json()['total'] == 0
    at(market, 100)
    response = client.patch('/api/v1/instruments/600001.SH/annotations/' + line['annotation_id'],
                            json={'price':101, 'label':'修改不延期'})
    assert response.status_code == 200
    assert response.json()['revision'] == 2 and response.json()['first_alert_date'] == market.days[70]
    drain()
    at(market, 130)
    sync(market); drain()
    assert line['annotation_id'] in line_ids(client)
    at(market, 131)
    sync(market); drain()
    assert line['annotation_id'] not in line_ids(client)
    assert client.get('/api/v1/watchlist/line-alerts').json()['total'] == 0


def test_deleted_or_disabled_line_still_has_its_expired_alerts_cleaned(lines):
    market, client = lines
    line = touch_line(market, client)
    client.delete('/api/v1/instruments/600001.SH/annotations/' + line['annotation_id']); drain()
    assert client.get('/api/v1/watchlist/line-alerts').json()['total'] == 1
    at(market, 131)
    _, child = sync(market)
    assert child is not None  # orphan history must not be skipped by no_rules_ok
    drain()
    assert client.get('/api/v1/watchlist/line-alerts').json()['total'] == 0


def test_late_historical_recheck_cannot_rearm_expired_line_or_auto_collect(lines):
    market, client = lines
    at(market, 70); quote(market, 70, low=99, close=105)
    line = create(client, kind='resistance', modes=['pressure_touch'], auto=True)
    at(market, 131); drain()  # old save check is stale, rather than backdating its result
    client.post('/api/v1/watchlist/line-alerts/evaluate', json={}); drain()
    event = client.get('/api/v1/watchlist/line-alerts').json()['items'][0]
    assert event['trade_date'] == market.days[70] and event['added_to_watchlist'] is False
    assert '60' in event['reason']
    assert client.get('/api/v1/watchlist/ids').json()['total'] == 0
    sync(market); drain()
    assert line['annotation_id'] not in line_ids(client)


def test_missing_calendar_evidence_never_guesses_a_deletion(lines, monkeypatch):
    market, client = lines
    line = touch_line(market, client)
    first = market.days[70]
    at(market, 131)
    monkeypatch.setattr(market, 'calendar', lambda: [day for day in market.days if day != first])
    _, child = sync(market); drain()
    assert line['annotation_id'] in line_ids(client)
    with core.connect() as connection:
        assert retention.checkpoint(connection, child)['calendar_unknown_lines'] == 1


def test_ordinary_api_connections_still_cannot_erase_or_rewrite_alert_evidence(lines):
    market, client = lines
    touch_line(market, client)
    for statement in ('DELETE FROM line_alert_event', "UPDATE line_alert_event SET state='invalidated'"):
        with pytest.raises(sqlite3.IntegrityError, match='retain line alert evidence'):
            with core.connect() as connection:
                connection.execute(statement)


def test_new_manual_check_cannot_be_promoted_to_a_cleanup_job(lines):
    market, client = lines
    touch_line(market, client)
    at(market, 131)
    identifier = client.post('/api/v1/watchlist/line-alerts/evaluate', json={}).json()['job_id']
    job = tasks.claim_next('retention-test')
    assert job['job_id'] == identifier
    parameters = json.loads(job['parameters_json'])
    parameters['line_alert_retention_version'] = retention.VERSION
    with pytest.raises(ValueError, match='盘后增量'):
        with core.connect() as connection:
            retention.start(connection, job, parameters)


def test_cleanup_pause_resume_uses_its_clock_checkpoint_without_replaying_quotes(lines, monkeypatch):
    market, client = lines
    touch_line(market, client, price=100)
    create(client, price=101, kind='resistance', modes=['pressure_touch'], auto=False); drain()
    at(market, 131)
    _, child = sync(market)
    monkeypatch.setattr(retention, 'BATCH_SIZE', 1)
    original = retention._authorized
    paused = False
    def stop_after_one(connection, job, parameters):
        nonlocal paused
        value = retention.checkpoint(connection, job['job_id'])
        if value and value['removed_lines'] == 1 and not paused:
            paused = True
            tasks.change_task(child, 'pause')
        return original(connection, job, parameters)
    monkeypatch.setattr(retention, '_authorized', stop_after_one)
    with pytest.raises(tasks.TaskCancelled):
        worker.dispatch(tasks.claim_next('retention-pause'))
    assert tasks.task_view(child)['status'] == 'paused'
    monkeypatch.setattr(retention, '_authorized', original)
    monkeypatch.setattr(market, 'history_for_instruments', lambda *a, **k: pytest.fail('cleanup resume cannot replay quote checks'))
    tasks.change_task(child, 'resume'); drain()
    with core.connect() as connection:
        value = retention.checkpoint(connection, child)
        assert value['complete'] and value['removed_lines'] == 2
    assert line_ids(client) == []
