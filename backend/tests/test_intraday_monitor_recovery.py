from copy import deepcopy
import json
import pytest
from app import core
from app.services import intraday_monitor as monitor
from app.services import intraday_monitor_levels as levels
from app.services import task_service as tasks
from test_workspace import ws
from test_intraday_monitor import monitored, enable, tick, set_clock, run_slot, SYMBOL


def test_post_commit_crash_recovers_after_deadline_and_disabled_config(monitored, monkeypatch):
    class PowerLoss(BaseException):
        pass
    env = monitored
    enable(env)
    identifier = tick(env)
    job = tasks.claim_next('old-worker')
    original = tasks.finish
    monkeypatch.setattr(tasks, 'finish', lambda *a, **k: (_ for _ in ()).throw(PowerLoss()))
    with pytest.raises(PowerLoss):
        monitor.execute_capture(job)
    calls = deepcopy(env.source.calls)
    with core.connect() as c:
        assert c.execute('SELECT status FROM intraday_monitor_batch WHERE job_id=?', (identifier,)).fetchone()[0] == 'succeeded'
        assert c.execute('SELECT status FROM job WHERE job_id=?', (identifier,)).fetchone()[0] == 'running'
    monkeypatch.setattr(tasks, 'finish', original)
    set_clock(env, '10:30:05')
    enable(env, enabled=False)
    recovered = tasks.claim_next('replacement-worker')
    assert recovered and recovered['job_id'] == identifier
    monitor.execute_capture(recovered)
    assert env.source.calls == calls
    with core.connect() as c:
        assert c.execute('SELECT status FROM job WHERE job_id=?', (identifier,)).fetchone()[0] == 'succeeded'
        assert c.execute('SELECT count(*) FROM intraday_monitor_state').fetchone()[0] == 1


def test_carried_old_risk_anchor_pins_batch_and_latest_baseline(monitored):
    env = monitored
    old = levels.automatic_baseline(SYMBOL, env.market.bars(SYMBOL, env.market.entry),
        previous_day=env.market.entry, calendar=env.market.days, snapshot='bars', primary_snapshot='bars')
    saved = old['monitoring_baseline']
    saved['levels']['stop']['price'] = 14.9
    saved['risk_anchor']['level']['price'] = 14.9
    with core.connect() as c:
        c.execute('INSERT INTO intraday_monitor_baseline VALUES(?,?,?,?)',
                  (env.market.entry, SYMBOL, 'bars', json.dumps(old)))
    enable(env)
    _, batch = run_slot(env, '09:30:05', 15.)
    with core.connect() as c:
        current = json.loads(c.execute('SELECT baseline_json FROM intraday_monitor_baseline WHERE trade_date=? AND instrument_id=?', (env.day, SYMBOL)).fetchone()[0])
        assert current['monitoring_baseline']['risk_anchor']['primary_snapshot_id'] == 'bars'
        for owner in ['intraday-monitor:' + batch['batch_id'], 'intraday-monitor-baseline:' + SYMBOL]:
            pins = {row[0] for row in c.execute('SELECT snapshot_id FROM retention_pin WHERE owner_kind=\'manual\' AND owner_id=?', (owner,))}
            assert pins == {'raw', 'bars'}
