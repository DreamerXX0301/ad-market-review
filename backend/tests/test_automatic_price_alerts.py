"""Automatic closing alerts: causal thresholds, opt-in rules, immutable receipts."""
from copy import deepcopy
import json
import sqlite3

from fastapi import HTTPException
import pytest

from app import core
from app.services import automatic_price_alerts as alerts, task_service as tasks
from test_workspace import ws, advance


@pytest.fixture
def automatic(ws, monkeypatch):
    market, _ = ws
    monkeypatch.setattr(alerts, 'get_market', lambda: market)
    monkeypatch.setattr(market, 'status', lambda: {'snapshot_id': 'raw', 'data_end': market.entry}, raising=False)
    for bars in market.data.values():
        for bar in bars:
            bar.update(open=10.0, high=10.6, low=9.4, close=10.2, volume=100.0,
                       complete=True, corporate_action=False, corporate_action_coverage_known=True)
        bars[43]['high'] = 12
        bars[47]['low'] = 8.5
        bars[60]['high'] = 11.5
        bars[63]['low'] = 9
        bars[70].update(open=10.2, close=10.5, low=10.0, high=10.7)
        bars[71].update(open=10.2, close=12.8, low=10.1, high=13, volume=200)
    return market


def configure(**overrides):
    return alerts.save_config('600001.SH', {'enabled': True, 'modes': ['close_breakout'],
        'require_volume': True, 'expected_revision': 0, **overrides})


def drain():
    for _ in range(20):
        job = tasks.claim_next('automatic-alert-fixture')
        if job is None:
            return
        assert json.loads(job['parameters_json'])['operation'] == alerts.OPERATION
        alerts.execute_check(job)
    pytest.fail('unexpected unbounded automatic alert queue')


def publish_next(market, index=71):
    advance(market, index)
    market.entry = market.days[index]


def test_disabled_default_sql_only_and_save_never_reads_chart_under_transaction(automatic, monkeypatch):
    market = automatic
    monkeypatch.setattr(alerts, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('GET cannot read market')))
    value = alerts.get_config('600001.SH')
    assert value['rule'] == {'rule_id': None, 'revision': 0, 'enabled': False, 'modes': [],
        'require_volume': True, 'algorithm': alerts.ALGORITHM, 'effective_after_date': None}
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 1
    monkeypatch.setattr(alerts, 'get_market', lambda: market)
    monkeypatch.setattr(market, 'bars', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('save cannot compute chart')))
    configured = configure()
    assert configured['rule']['revision'] == 1 and configured['task']['status'] == 'queued'
    assert configured['latest_baseline'] is None


def test_new_empty_database_reminder_reads_are_empty_without_market_or_business_clock(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(alerts, 'get_market', lambda: (_ for _ in ()).throw(AssertionError('GET cannot compute')))
    result = alerts.list_alerts()
    assert result['items'] == [] and result['total'] == 0 and result['business_date'] is None
    assert alerts.list_alerts(scope='today')['total'] == 0
    assert alerts.alert_summary()['total'] == 0
    with core.connect() as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 43
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert connection.execute('PRAGMA recursive_triggers').fetchone()[0] == 1
    with pytest.raises(HTTPException):
        alerts.list_alerts(as_of='2026-11-11')


def test_previous_exact_session_threshold_not_breakout_days_new_structure(automatic):
    market = automatic
    configured = configure()
    drain()
    assert alerts.list_alerts()['total'] == 0
    saved = alerts.get_config('600001.SH')
    old = saved['latest_baseline']
    assert old['as_of'] == market.days[70]
    assert old['levels']['resistance']['price'] == 11.5
    assert saved['latest_evaluation']['trigger_count'] == 0
    assert saved['latest_evaluation']['state'] == 'prepared'
    publish_next(market)
    alerts.create_check({})
    drain()
    result = alerts.list_alerts(scope='today')
    assert result['total'] == 1 and result['latest_check']['is_current']
    event = result['items'][0]
    assert event['line_price'] == old['levels']['breakout']['price']
    assert event['line_price'] < event['close']
    assert event['level_date'] == market.days[70] < event['trade_date']
    assert event['level_evidence']['levels']['resistance']['price'] == 11.5
    assert event['volume_evidence']['ratio'] == 2
    assert event['source_kind'] == alerts.SOURCE
    assert event['annotation_id'] == configured['rule']['rule_id']
    assert event['watchlist_add_state'] == 'disabled'
    latest = alerts.get_config('600001.SH')['latest_baseline']
    assert latest['as_of'] == market.days[71]
    assert latest['levels']['resistance'] is None
    assert latest['levels']['stop']['price'] != old['levels']['stop']['price']
    assert alerts.alert_summary()['total'] == 1
    with core.connect() as connection:
        assert alerts.latest_by_stock(connection, ['600001.SH'], market.entry)['600001.SH']['event_id'] == event['event_id']
        assert connection.execute('SELECT count(*) FROM watchlist_item').fetchone()[0] == 0


def test_config_revision_and_day_gate_cannot_create_retroactive_hit(automatic):
    market = automatic
    publish_next(market)
    rule = configure()['rule']
    drain()
    assert alerts.list_alerts()['total'] == 0
    with pytest.raises(HTTPException) as error:
        configure(expected_revision=0)
    assert error.value.status_code == 409
    same = configure(expected_revision=rule['revision'])
    assert same['rule']['revision'] == rule['revision'] and same['task'] is None
    assert same['latest_evaluation']['trigger_count'] == 0


def test_new_high_does_not_create_fake_resistance_and_missing_volume_is_unknown(automatic):
    market = automatic
    configure()
    drain()
    market.data['600001.SH'][71]['volume'] = None
    publish_next(market)
    alerts.create_check({})
    drain()
    result = alerts.list_alerts()
    assert result['total'] == 0 and result['latest_check']['unknown_count'] == 1
    assert '成交量' in result['unknowns'][0]['reason']
    assert alerts.get_config('600001.SH')['latest_baseline']['levels']['resistance'] is None


def test_later_historical_repair_cannot_move_previously_frozen_trigger(automatic):
    market = automatic
    configure()
    drain()
    frozen = deepcopy(alerts.get_config('600001.SH')['latest_baseline'])
    # The day-T publication repairs an older pressure pivot and prior close.
    # The actual configured monitoring threshold must remain yesterday's value.
    market.data['600001.SH'][60]['high'] = 18
    market.data['600001.SH'][70].update(open=14, close=14, low=13, high=15)
    publish_next(market)
    alerts.create_check({})
    drain()
    event = alerts.list_alerts()['items'][0]
    assert event['line_price'] == frozen['levels']['breakout']['price']
    assert event['previous_close'] == 10.5
    assert event['raw_previous_close'] == 14
    assert event['level_evidence'] == frozen
    with core.connect() as connection:
        owners = {row['owner_id'] for row in connection.execute('SELECT owner_id FROM retention_pin')}
        assert 'auto-price-event:' + event['event_id'] in owners
        assert 'auto-price-baseline:' + event['rule_id'] in owners


def test_missing_monitoring_day_does_not_reconstruct_an_after_the_fact_alert(automatic):
    market = automatic
    configure()
    drain()
    publish_next(market, 72)
    market.data['600001.SH'][72].update(open=10.2, close=12.8, low=10.1, high=13, volume=200)
    alerts.create_check({})
    drain()
    result = alerts.list_alerts()
    assert result['total'] == 0 and result['latest_check']['unknown_count'] == 1
    assert '尚无前一交易日监控基准' in result['unknowns'][0]['reason']
    config = alerts.get_config('600001.SH')
    assert config['latest_baseline']['as_of'] == market.days[72]
    assert config['latest_evaluation']['state'] == 'unknown'
    assert '尚无前一交易日监控基准' in config['latest_evaluation']['reason']


@pytest.mark.parametrize('required', [True, False])
def test_optional_volume_gate_uses_previous20_excluding_trigger_day(automatic, required):
    market = automatic
    configure(require_volume=required)
    drain()
    market.data['600001.SH'][71]['volume'] = 100
    publish_next(market)
    alerts.create_check({})
    drain()
    events = alerts.list_alerts()['items']
    assert len(events) == (0 if required else 1)
    if events:
        assert events[0]['require_volume'] is False
        assert events[0]['volume_evidence']['mean_previous20'] == 100
        assert events[0]['volume_evidence']['ratio'] == 1


def test_touching_frozen_breakout_exactly_is_not_a_close_cross(automatic):
    market = automatic
    configure(require_volume=False)
    drain()
    threshold = alerts.get_config('600001.SH')['latest_baseline']['levels']['breakout']['price']
    market.data['600001.SH'][71].update(close=threshold, high=threshold)
    publish_next(market)
    alerts.create_check({})
    drain()
    assert alerts.list_alerts()['total'] == 0


def test_support_reclaim_uses_original_support_after_an_intraday_wick_break(automatic):
    market = automatic
    configure(modes=['support_reclaim'])
    drain()
    support = alerts.get_config('600001.SH')['latest_baseline']['levels']['support']['price']
    market.data['600001.SH'][71].update(open=10.2, low=support - .1, close=10.3, high=10.5, volume=None)
    publish_next(market)
    alerts.create_check({})
    drain()
    event = alerts.list_alerts()['items'][0]
    assert event['rule'] == 'support_reclaim' and event['state'] == 'triggered'
    assert event['line_price'] == support
    assert event['low'] < support < event['close']


def test_configuration_and_queue_rollback_together_on_enqueue_failure(automatic, monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError('controlled queue failure')
    monkeypatch.setattr(alerts, 'enqueue', blocked)
    with pytest.raises(RuntimeError):
        configure()
    assert alerts.get_config('600001.SH')['rule']['revision'] == 0
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_rule').fetchone()[0] == 0


def test_price_risk_survives_missing_volume_and_manual_drawings_unchanged(automatic):
    market = automatic
    with core.connect() as connection:
        connection.execute("""INSERT INTO chart_annotation(annotation_id,instrument_id,price,label,color,price_basis,
            created_at_ms,updated_at_ms) VALUES('manual-unchanged','600001.SH',8.1,'用户原画线','blue','none',1,1)""")
        old = dict(connection.execute("SELECT * FROM chart_annotation WHERE annotation_id='manual-unchanged'").fetchone())
        clock = connection.execute('SELECT revision FROM line_alert_rule_clock WHERE singleton=1').fetchone()
    configure(modes=['support_breakdown', 'stop_breakdown'])
    drain()
    market.data['600001.SH'][71].update(open=10.2, close=8, low=7.9, high=10.3, volume=None)
    publish_next(market)
    alerts.create_check({})
    drain()
    events = alerts.list_alerts()['items']
    assert len(events) == 2 and all(event['state'] == 'invalidated' for event in events)
    assert all(event['volume_evidence']['state'] == 'unknown' for event in events)
    with core.connect() as connection:
        assert dict(connection.execute("SELECT * FROM chart_annotation WHERE annotation_id='manual-unchanged'").fetchone()) == old
        assert connection.execute('SELECT revision FROM line_alert_rule_clock WHERE singleton=1').fetchone() == clock
        assert connection.execute('SELECT count(*) FROM line_alert_event').fetchone()[0] == 0


@pytest.mark.parametrize('change', ['action', 'coverage', 'missing_previous', 'missing_target', 'forming', 'adjusted_target'])
def test_actions_unknown_coverage_and_exact_session_gaps_cannot_trigger(automatic, change):
    market = automatic
    configure()
    drain()
    if change == 'action':
        market.data['600001.SH'][71]['corporate_action'] = True
    elif change == 'coverage':
        market.data['600001.SH'][71]['corporate_action_coverage_known'] = False
    elif change == 'forming':
        market.data['600001.SH'][71]['complete'] = False
    elif change == 'adjusted_target':
        market.data['600001.SH'][71]['price_basis'] = 'forward_event_affine'
    else:
        missing = market.days[70 if change == 'missing_previous' else 71]
        market.data['600001.SH'] = [bar for bar in market.data['600001.SH'] if bar['date'] != missing]
    publish_next(market)
    alerts.create_check({})
    drain()
    result = alerts.list_alerts()
    assert result['total'] == 0 and result['latest_check']['unknown_count'] == 1


def test_same_input_repeat_request_and_resumed_job_do_not_duplicate_or_rewrite_events(automatic):
    market = automatic
    configure()
    drain()
    publish_next(market)
    first_job = alerts.create_check({'request_key': 'first-event-check'})
    drain()
    event = deepcopy(alerts.list_alerts()['items'][0])
    assert alerts.create_check({'request_key': 'first-event-check'})['job_id'] == first_job['job_id']
    second_job = alerts.create_check({'request_key': 'second-event-check'})
    drain()
    assert second_job['job_id'] != first_job['job_id']
    assert alerts.list_alerts()['items'] == [event]
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_evaluation WHERE trade_date=?', (market.entry,)).fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE automatic_price_alert_event SET event_json='{}'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute('DELETE FROM automatic_price_alert_event')


@pytest.mark.parametrize('change', ['config', 'business_day', 'head'])
def test_frozen_worker_cannot_commit_under_changed_configuration_or_market(automatic, change, monkeypatch):
    market = automatic
    configure()
    drain()
    publish_next(market)
    task = alerts.create_check({})
    job = tasks.claim_next('stale-automatic-check')
    if change == 'config':
        configure(enabled=False, expected_revision=1)
    elif change == 'business_day':
        publish_next(market, 72)
    else:
        monkeypatch.setattr(alerts, '_head', lambda connection: {'primary_snapshot_id': 'new-raw', 'market_snapshot_id': 'new-raw'})
    alerts.execute_check(job)
    assert tasks.task_view(task['job_id'])['status'] == 'no_change'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_event').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_evaluation WHERE job_id=?', (job['job_id'],)).fetchone()[0] == 0


def test_worker_recovery_reuses_persisted_mode_check_without_rewriting_event(automatic, monkeypatch):
    market = automatic
    configure(modes=['close_breakout', 'support_breakdown'])
    drain()
    publish_next(market)
    alerts.create_check({})
    job = tasks.claim_next('recover-price-alert')
    original = tasks.progress
    calls = []
    def paused(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise tasks.TaskPaused()
        return original(*args, **kwargs)
    monkeypatch.setattr(tasks, 'progress', paused)
    with pytest.raises(tasks.TaskPaused):
        alerts.execute_check(job)
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_event').fetchone()[0] == 1
    monkeypatch.setattr(tasks, 'progress', original)
    alerts.execute_check(job)
    assert tasks.task_view(job['job_id'])['status'] == 'succeeded'
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_event').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_evaluation WHERE job_id=?', (job['job_id'],)).fetchone()[0] == 2


def test_source_api_is_stock_only_and_rejects_arbitrary_parameters(automatic, monkeypatch):
    with pytest.raises(HTTPException):
        configure(modes=['realtime_tick'])
    with pytest.raises(HTTPException):
        configure(expected_revision=True)
    with pytest.raises(HTTPException):
        alerts.create_check({'as_of': '2020-01-01'})
    monkeypatch.setattr(automatic, 'instruments', lambda **kwargs: [{'instrument_id': '000001.SH', 'kind': 'index'}])
    with pytest.raises(HTTPException) as error:
        alerts.save_config('000001.SH', {'enabled': True, 'modes': ['close_breakout'], 'require_volume': False, 'expected_revision': 0})
    assert error.value.status_code == 422


def test_old_sync_has_no_new_authority_and_followup_is_idempotent(automatic):
    market = automatic
    configure()
    drain()
    publish_next(market)
    with core.connect() as connection:
        parent = dict(connection.execute("SELECT * FROM job WHERE job_id='fixture-job'").fetchone())
    assert alerts.after_sync(parent) is None
    parameters = {'automatic_price_alert_check_version': alerts.VERSION}
    with core.connect() as connection:
        connection.execute("""INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,
            parameters_hash,status,created_at_ms) VALUES('new-auto-parent','new-auto-parent','new-auto-parent',
            'increment',?,?,?,'succeeded',?)""", (market.entry, core.dumps(parameters), core.digest(parameters), core.now_ms()))
        parent = dict(connection.execute("SELECT * FROM job WHERE job_id='new-auto-parent'").fetchone())
    identifier = alerts.after_sync(parent)
    assert identifier is not None
    assert alerts.after_sync(parent) is None
    alerts.recover_sync_followups()
    drain()
    assert alerts.list_alerts()['total'] == 1
