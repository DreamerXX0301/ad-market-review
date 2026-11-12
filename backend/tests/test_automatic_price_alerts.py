"""Automatic closing alerts: causal thresholds, opt-in rules, immutable receipts."""
from copy import deepcopy
import json
from pathlib import Path
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
        'require_volume': True, 'algorithm': alerts.SHORT_VERSION, 'effective_after_date': None,
        'profile': 'short_week_v1', 'max_risk_pct': 5.0}
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
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 44
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


def configure_short(**overrides):
    return configure(profile='short_week_v1', max_risk_pct=5.0,
                     **{'modes': ['stop_breakdown'], **overrides})


def short_session(market, index, close, *, previous_close=None, **values):
    previous_close = previous_close or market.data['600001.SH'][index - 1]['close']
    market.data['600001.SH'][index].update(
        open=previous_close, high=max(previous_close, close) + .2,
        low=min(previous_close, close) - .2, close=close, volume=100,
        **values,
    )
    publish_next(market, index)
    alerts.create_check({})
    drain()
    return alerts.get_config('600001.SH')['latest_baseline']


def test_old_client_keeps_structure_profile_and_frozen_task_without_new_fields(automatic):
    market = automatic
    configured = configure()
    assert configured['rule']['profile'] == 'structure'
    assert configured['rule']['max_risk_pct'] == 5.0
    assert configured['rule']['algorithm'] == alerts.ALGORITHM
    job = tasks.claim_next('frozen-before-short-mode')
    parameters = json.loads(job['parameters_json'])
    for rule in parameters['rules']:
        rule.pop('profile')
        rule.pop('max_risk_pct')
    # A frozen pre-upgrade task is deliberately not expanded to the new model.
    parameters['rules_signature'] = core.digest(parameters['rules'])
    old_job = {**job, 'parameters_json': core.dumps(parameters)}
    alerts.execute_check(old_job)
    baseline = alerts.get_config('600001.SH')['latest_baseline']
    assert baseline['as_of'] == market.days[70]
    assert baseline['profile'] == 'structure'
    assert baseline['algorithm'] == alerts.ALGORITHM
    assert baseline['levels']['support']['price'] == 9
    assert baseline['levels']['stop']['price'] < 9


def test_short_stop_is_frozen_before_close_raises_only_and_stays_breached(automatic):
    market = automatic
    configured = configure_short()
    assert configured['rule']['profile'] == 'short_week_v1'
    assert configured['rule']['algorithm'] == alerts.SHORT_VERSION
    drain()
    initial = deepcopy(alerts.get_config('600001.SH')['latest_baseline'])
    initial_stop = initial['levels']['stop']['price']
    assert initial['as_of'] == market.days[70]
    assert initial['algorithm'] == alerts.SHORT_VERSION
    assert initial['levels']['support'] is None  # A distant pivot is not a nearby support.
    assert not {'display_support', 'display_scope', 'display_distance_state'} & initial['levels'].keys()
    assert initial_stop >= initial['levels']['reference_price'] * .95
    assert alerts.list_alerts()['total'] == 0  # Saving does not trigger retrospectively.

    down = short_session(market, 71, 10.3)
    assert down['levels']['stop']['price'] == initial_stop
    assert down['risk_anchor'] == initial['risk_anchor']
    assert down['levels']['stop']['state'] == 'monitoring'

    up = short_session(market, 72, 12.8)
    raised_stop = up['levels']['stop']['price']
    assert raised_stop > initial_stop
    assert up['risk_anchor']['as_of'] == market.days[72]
    assert up['levels']['stop']['monitoring_since'] == market.days[72]

    still_above = short_session(market, 73, 12.4)
    assert still_above['levels']['stop']['price'] == raised_stop
    assert still_above['risk_anchor'] == up['risk_anchor']
    assert alerts.list_alerts()['total'] == 0

    breached = short_session(market, 74, 11.8)
    events = alerts.list_alerts()['items']
    assert len(events) == 1
    event = deepcopy(events[0])
    assert event['trade_date'] == market.days[74]
    assert event['level_date'] == market.days[73]
    assert event['line_price'] == raised_stop > event['close']
    assert event['level_evidence'] == still_above
    assert event['algorithm'] == alerts.SHORT_VERSION
    assert event['profile'] == 'short_week_v1'
    assert event['max_risk_pct'] == 5.0
    assert event['automatic_level']['source'] == 'short_risk_boundary'
    assert breached['levels']['stop']['price'] == raised_stop
    assert breached['levels']['stop']['state'] == 'breached'
    assert breached['risk_anchor'] == up['risk_anchor']

    # A price recovery or a second downward crossing cannot silently re-arm it.
    recovered = short_session(market, 75, 14.0)
    assert recovered['levels']['stop']['state'] == 'breached'
    assert recovered['levels']['stop']['price'] == raised_stop
    again = short_session(market, 76, 11.7)
    assert again['levels']['stop']['state'] == 'breached'
    assert again['levels']['stop']['price'] == raised_stop
    assert alerts.list_alerts()['items'] == [event]
    with core.connect() as connection:
        pins = {row['owner_id'] for row in connection.execute('SELECT owner_id FROM retention_pin')}
        assert 'auto-price-event:' + event['event_id'] in pins
        assert 'auto-price-baseline:' + event['rule_id'] in pins
        assert connection.execute('SELECT count(*) FROM line_alert_event').fetchone()[0] == 0


def test_short_profile_and_risk_changes_create_new_revision_without_retroactive_alerts(automatic):
    market = automatic
    legacy = configure(modes=['stop_breakdown'])['rule']
    drain()
    assert alerts.get_config('600001.SH')['latest_baseline']['profile'] == 'structure'
    short = configure_short(expected_revision=legacy['revision'])
    assert short['rule']['revision'] == legacy['revision'] + 1
    assert short['task']['status'] == 'queued'
    assert short['latest_baseline'] is None
    drain()
    basis = alerts.get_config('600001.SH')['latest_baseline']
    assert basis['profile'] == 'short_week_v1'
    assert basis['levels']['stop']['price'] > 9
    assert alerts.list_alerts()['total'] == 0
    with core.connect() as connection:
        before_count = connection.execute('SELECT count(*) FROM job').fetchone()[0]
    same = configure_short(expected_revision=short['rule']['revision'])
    assert same['rule']['revision'] == short['rule']['revision']
    assert same['task'] is None and same['latest_baseline'] == basis
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == before_count

    narrower = configure(profile='short_week_v1', max_risk_pct=3.0,
                         modes=['stop_breakdown'], expected_revision=short['rule']['revision'])
    assert narrower['rule']['revision'] == short['rule']['revision'] + 1
    assert narrower['rule']['max_risk_pct'] == 3.0
    assert narrower['latest_baseline'] is None
    drain()
    result = alerts.get_config('600001.SH')
    assert result['latest_baseline']['as_of'] == market.days[70]
    assert result['latest_baseline']['levels']['stop']['price'] >= 10.5 * .97
    assert result['latest_evaluation']['state'] == 'prepared'
    assert alerts.list_alerts()['total'] == 0


def test_explicit_short_reset_rearms_same_settings_without_rewriting_the_breach(automatic):
    market = automatic
    configure_short()
    drain()
    broken = short_session(market, 71, 8.0)
    assert broken['levels']['stop']['state'] == 'breached'
    original_event = deepcopy(alerts.list_alerts()['items'][0])
    old_rule = alerts.get_config('600001.SH')['rule']
    unchanged = configure_short(expected_revision=old_rule['revision'])
    assert unchanged['task'] is None
    assert unchanged['latest_baseline'] == broken

    reset = configure_short(expected_revision=old_rule['revision'], reset_monitoring=True)
    assert reset['rule']['revision'] == old_rule['revision'] + 1
    assert reset['rule']['effective_after_date'] == market.days[71]
    assert reset['latest_baseline'] is None and reset['task']['status'] == 'queued'
    drain()
    prepared = alerts.get_config('600001.SH')
    basis = prepared['latest_baseline']
    assert prepared['latest_evaluation']['state'] == 'prepared'
    assert basis['as_of'] == market.days[71]
    assert basis['levels']['stop']['state'] == 'monitoring'
    assert basis['levels']['stop']['price'] < 8
    assert alerts.list_alerts()['items'] == [original_event]

    # The new revision can trigger only on a later published trading session.
    short_session(market, 72, 7.0)
    events = alerts.list_alerts()['items']
    assert len(events) == 2
    later = next(event for event in events if event['revision'] == reset['rule']['revision'])
    assert later['level_date'] == market.days[71]
    assert later['trade_date'] == market.days[72]
    assert later['line_price'] == basis['levels']['stop']['price']
    assert next(event for event in events if event['revision'] == old_rule['revision']) == original_event


@pytest.mark.parametrize('overrides', [
    {'reset_monitoring': 1}, {'reset_monitoring': 'true'},
    {'reset_monitoring': None}, {'reset_monitoring': True, 'enabled': False},
    {'reset_monitoring': True, 'profile': 'structure'},
])
def test_reset_requires_explicit_boolean_and_an_enabled_short_profile(automatic, overrides):
    payload = {'profile': 'short_week_v1', 'max_risk_pct': 5.0, **overrides}
    with pytest.raises(HTTPException) as error:
        configure(**payload)
    assert error.value.status_code == 422
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_rule').fetchone()[0] == 0


@pytest.mark.parametrize('risk', [True, '5', None, 0, .49, 20.01, 5.001, float('nan'), float('inf')])
def test_short_risk_validation_rejects_invalid_values_without_any_save_or_task(automatic, risk):
    with core.connect() as connection:
        before_jobs = connection.execute('SELECT count(*) FROM job').fetchone()[0]
    with pytest.raises(HTTPException) as error:
        configure(profile='short_week_v1', max_risk_pct=risk)
    assert error.value.status_code == 422
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_rule').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == before_jobs


@pytest.mark.parametrize('profile', ['short', '', None, True, [], {}])
def test_unknown_short_profile_is_not_coerced_into_a_different_model(automatic, profile):
    with pytest.raises(HTTPException) as error:
        configure(profile=profile, max_risk_pct=5.0)
    assert error.value.status_code == 422


@pytest.mark.parametrize('risk', [.5, 5.25, 20])
def test_short_risk_accepts_inclusive_bounds_and_two_decimal_configuration(automatic, risk):
    saved = configure(profile='short_week_v1', max_risk_pct=risk, modes=['stop_breakdown'])
    assert saved['rule']['max_risk_pct'] == risk
    drain()
    baseline = alerts.get_config('600001.SH')['latest_baseline']
    assert baseline['max_risk_pct'] == risk
    assert baseline['levels']['stop']['price'] >= baseline['levels']['reference_price'] * (1 - risk / 100)


def test_raised_only_stop_retains_bounded_original_source_evidence(automatic):
    market = automatic
    initial = alerts.build_baseline('600001.SH', market.bars('600001.SH'), market.days[70],
                                   market.days, 'old-generation', 'old-source',
                                   profile='short_week_v1', max_risk_pct=5.0)
    initial = alerts.advance_short_baseline(None, initial, market.bars('600001.SH'))
    rows = market.bars('600001.SH')
    rows[71].update(open=10.5, close=10.3, low=10.1, high=10.7)
    later = alerts.build_baseline('600001.SH', rows, market.days[71], market.days,
                                 'next-generation', 'next-source',
                                 profile='short_week_v1', max_risk_pct=5.0)
    carried = alerts.advance_short_baseline(initial, later, rows)
    assert carried['levels']['stop']['price'] == initial['levels']['stop']['price']
    assert carried['risk_anchor'] == initial['risk_anchor']
    assert alerts._baseline_sources(carried) == {'old-source', 'next-source'}
    assert carried['risk_anchor']['snapshot_id'] == 'old-generation'
    assert 'risk_anchor' not in carried['risk_anchor']  # Daily updates do not grow a recursive chain.
    assert 'levels' not in carried['risk_anchor']


@pytest.mark.parametrize('change', ['action', 'coverage', 'missing_target', 'missing_previous', 'forming', 'noon'])
def test_short_unusable_session_cannot_trigger_or_carry_a_nominal_risk_line(automatic, change):
    market = automatic
    configure_short()
    drain()
    bars = market.data['600001.SH']
    bars[71].update(open=10.2, close=8.0, low=7.8, high=10.3)
    if change == 'action':
        bars[71]['corporate_action'] = True
    elif change == 'coverage':
        bars[71]['corporate_action_coverage_known'] = False
    elif change == 'forming':
        bars[71]['complete'] = False
    elif change == 'noon':
        bars[71]['noon_provisional'] = True
    else:
        missing = market.days[71 if change == 'missing_target' else 70]
        market.data['600001.SH'] = [bar for bar in bars if bar['date'] != missing]
    publish_next(market)
    alerts.create_check({})
    drain()
    listing = alerts.list_alerts()
    assert listing['total'] == 0
    assert listing['latest_check']['unknown_count'] == 1
    latest = alerts.get_config('600001.SH')['latest_baseline']
    assert latest['levels']['stop'] is None


def test_short_skipped_check_keeps_existing_floor_but_does_not_backfill_a_crossing(automatic):
    market = automatic
    configure_short()
    drain()
    before = deepcopy(alerts.get_config('600001.SH')['latest_baseline'])
    # Both sessions have real quotations, but day 71 was never monitored.
    market.data['600001.SH'][71].update(open=10.5, close=10.4, low=10.2, high=10.7)
    market.data['600001.SH'][72].update(open=10.4, close=10.1, low=10.0, high=10.6)
    publish_next(market, 72)
    alerts.create_check({})
    drain()
    after = alerts.get_config('600001.SH')
    assert after['latest_evaluation']['state'] == 'unknown'
    assert '尚无前一交易日监控基准' in after['latest_evaluation']['reason']
    assert after['latest_baseline']['levels']['stop']['price'] == before['levels']['stop']['price']
    assert after['latest_baseline']['risk_anchor'] == before['risk_anchor']
    assert alerts.list_alerts()['total'] == 0
    short_session(market, 73, 9.7)
    event = alerts.list_alerts()['items'][0]
    assert event['trade_date'] == market.days[73]
    assert event['level_date'] == market.days[72]
    assert event['line_price'] == before['levels']['stop']['price']


def test_short_skipped_close_breach_then_recovery_is_recorded_without_historical_alert(automatic):
    market = automatic
    configure_short()
    drain()
    initial = deepcopy(alerts.get_config('600001.SH')['latest_baseline'])
    floor = initial['levels']['stop']['price']
    market.data['600001.SH'][71].update(open=10.5, close=9.8, low=9.7, high=10.7)
    market.data['600001.SH'][72].update(open=9.8, close=10.3, low=9.7, high=10.5)
    publish_next(market, 72)
    alerts.create_check({})
    drain()
    result = alerts.get_config('600001.SH')
    assert result['latest_evaluation']['state'] == 'unknown'
    assert result['latest_baseline']['levels']['stop']['price'] == floor
    assert result['latest_baseline']['levels']['stop']['state'] == 'breached'
    assert result['latest_baseline']['levels']['stop']['observed_breach_date'] == market.days[71]
    assert alerts.list_alerts()['total'] == 0
    next_crossing = short_session(market, 73, 9.7)
    assert next_crossing['levels']['stop']['state'] == 'breached'
    assert next_crossing['levels']['stop']['price'] == floor
    assert alerts.list_alerts()['total'] == 0


def test_short_noon_quote_inside_a_skipped_interval_suspends_the_risk_reference(automatic):
    market = automatic
    configure_short()
    drain()
    initial = deepcopy(alerts.get_config('600001.SH')['latest_baseline'])
    market.data['600001.SH'][71]['noon_provisional'] = True
    market.data['600001.SH'][72].update(open=10.4, close=10.1, low=10.0, high=10.6)
    publish_next(market, 72)
    alerts.create_check({})
    drain()
    result = alerts.get_config('600001.SH')
    assert result['latest_evaluation']['state'] == 'unknown'
    assert result['latest_baseline']['levels']['stop'] is None
    assert result['latest_baseline']['risk_anchor']['suspended'] is True
    assert result['latest_baseline']['risk_anchor']['level']['price'] == initial['risk_anchor']['level']['price']
    assert alerts.list_alerts()['total'] == 0


def test_short_same_day_quote_repair_cannot_lower_the_saved_risk_floor(automatic):
    market = automatic
    configure_short()
    drain()
    frozen = deepcopy(alerts.get_config('600001.SH')['latest_baseline'])
    # The revised current-day source remains above the frozen floor but would
    # produce a lower fresh reference; rerunning cannot loosen the monitor.
    market.data['600001.SH'][70].update(open=10.2, close=10.3, low=10.0, high=10.6)
    fresh = alerts.build_baseline('600001.SH', market.bars('600001.SH'), market.entry,
                                 market.days, 'raw', 'raw',
                                 profile='short_week_v1', max_risk_pct=5.0)
    assert fresh['levels']['stop']['price'] < frozen['levels']['stop']['price']
    alerts.create_check({'request_key': 'same-day-revised-source'})
    drain()
    updated = alerts.get_config('600001.SH')['latest_baseline']
    assert updated['levels']['stop']['price'] == frozen['levels']['stop']['price']
    assert updated['risk_anchor'] == frozen['risk_anchor']
    assert alerts.list_alerts()['total'] == 0


@pytest.mark.parametrize('change', ['action', 'missing_quote'])
def test_short_comparability_break_suspends_until_explicit_reset_even_after_warmup(automatic, change):
    market = automatic
    configure_short()
    drain()
    original = deepcopy(alerts.get_config('600001.SH')['latest_baseline'])
    if change == 'action':
        market.data['600001.SH'][71]['corporate_action'] = True
    else:
        market.data['600001.SH'] = [row for row in market.data['600001.SH'] if row['date'] != market.days[71]]
    publish_next(market, 71)
    alerts.create_check({})
    drain()
    suspended = alerts.get_config('600001.SH')
    assert suspended['latest_evaluation']['state'] == 'unknown'
    assert suspended['latest_baseline']['levels']['stop'] is None

    # Enough later valid quotations make a new formula computable, but do not
    # grant permission to reset an existing nominal-price monitor across a gap.
    publish_next(market, 90)
    rows = market.bars('600001.SH', as_of=market.entry)
    freshly_computed = alerts.build_baseline('600001.SH', rows, market.entry,
                                            market.days, 'raw', 'raw',
                                            profile='short_week_v1', max_risk_pct=5.0)
    assert freshly_computed['levels']['stop'] is not None
    alerts.create_check({})
    drain()
    still_suspended = alerts.get_config('600001.SH')
    assert still_suspended['latest_baseline']['levels']['stop'] is None
    assert still_suspended['latest_evaluation']['state'] == 'unknown'
    assert alerts.list_alerts()['total'] == 0
    anchor = still_suspended['latest_baseline']['risk_anchor']
    assert anchor['as_of'] == original['risk_anchor']['as_of']
    assert anchor['level']['price'] == original['risk_anchor']['level']['price']

    configured = configure_short(expected_revision=still_suspended['rule']['revision'], reset_monitoring=True)
    assert configured['latest_baseline'] is None
    drain()
    reset = alerts.get_config('600001.SH')
    assert reset['latest_evaluation']['state'] == 'prepared'
    assert reset['latest_baseline']['levels']['stop']['state'] == 'monitoring'
    assert reset['latest_baseline']['risk_anchor']['as_of'] == market.days[90]
    assert alerts.list_alerts()['total'] == 0


def test_schema44_preserves_preexisting_structure_rule_and_baseline_exactly(tmp_path):
    path = tmp_path / 'preexisting-schema43.sqlite3'
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA recursive_triggers=ON')
    connection.execute('PRAGMA busy_timeout=10000')
    directory = Path(__file__).resolve().parents[1] / 'app/migrations'
    try:
        for migration in sorted(directory.glob('[0-9]*.sql')):
            if int(migration.name.split('_')[0]) < 44:
                connection.executescript(migration.read_text())
        connection.execute('''INSERT INTO automatic_price_alert_rule
            (rule_id,instrument_id,revision,enabled,modes_json,require_volume,algorithm,
             effective_after_date,created_at_ms,updated_at_ms)
            VALUES('legacy','600001.SH',7,1,'["stop_breakdown"]',1,?,'2026-11-11',1,2)''',
            (alerts.ALGORITHM,))
        frozen = core.dumps({'as_of': '2026-11-11', 'algorithm': alerts.ALGORITHM,
                             'levels': {'stop': {'price': 8.5}}, 'monitoring_baseline': True})
        connection.execute("INSERT INTO automatic_price_alert_baseline VALUES('legacy',7,'2026-11-11','old-source',?,3)", (frozen,))
        before_rule = dict(connection.execute('SELECT * FROM automatic_price_alert_rule').fetchone())
        before_basis = dict(connection.execute('SELECT * FROM automatic_price_alert_baseline').fetchone())
        clock = connection.execute('SELECT revision FROM automatic_price_alert_clock').fetchone()[0]
        connection.commit()
        migration = next(directory.glob('044_*.sql'))
        connection.executescript(migration.read_text())
        after_rule = dict(connection.execute('SELECT * FROM automatic_price_alert_rule').fetchone())
        assert after_rule == {**before_rule, 'profile': 'structure', 'max_risk_pct': 5.0}
        assert dict(connection.execute('SELECT * FROM automatic_price_alert_baseline').fetchone()) == before_basis
        assert connection.execute('SELECT revision FROM automatic_price_alert_clock').fetchone()[0] == clock
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        connection.close()
