"""Restored copies cannot inherit permission to run daily automatic reviews."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backup import connect_sqlite, perform_backup, sha
from restore import perform_restore, verify_backup
from test_deployment import source

ROOT = Path(__file__).resolve().parents[1]
CONFIG = 'automatic-review-config-v1'
STATE = 'automatic-review-state-v1'
CALENDAR = 'automatic-review-calendar-v1'


def _settings(connection):
    return {row[0]: row[1] for row in connection.execute('SELECT key,value_json FROM platform_setting')}


def _bundle_sha(bundle):
    return {path.relative_to(bundle).as_posix(): sha(path) for path in bundle.rglob('*') if path.is_file()}


@pytest.mark.parametrize('switches', [(True, True), (True, False), (False, True), (False, False)])
def test_restore_disables_both_automatic_systems_without_rewriting_originals(source, tmp_path, switches):
    config = {'revision': 7, 'noon': {'enabled': switches[0], 'time': '12:22'},
              'after_close': {'enabled': switches[1], 'time': '19:15'}}
    state = {'noon': {'date': '2026-10-19', 'state': 'claiming', 'token': 'synthetic-claim'},
             'after_close': {'date': '2026-10-18', 'state': 'submitted', 'task_id': 'active'}}
    calendar = {'date': '2026-10-19', 'state': 'ready', 'source_sha256': 'a' * 64,
                'days': ['2026-10-19']}
    with connect_sqlite(source / 'business/app.sqlite3') as connection:
        connection.executescript((ROOT / 'backend/app/migrations/003_platform.sql').read_text())
        connection.executescript((ROOT / 'backend/app/migrations/030_auction_snapshots.sql').read_text())
        connection.executescript((ROOT / 'backend/app/migrations/031_auction_automation.sql').read_text())
        connection.execute("UPDATE market_auction_automation SET mode='daily',revision=revision+1,updated_at_ms=1 WHERE singleton=1")
        connection.execute("""INSERT INTO market_auction_automation_run(trade_date,job_id,schedule_revision,
            state,attempts,automatic_control,retry_after_at_ms,created_at_ms,updated_at_ms)
            VALUES('2026-10-19','active',1,'waiting_retry',1,1,100,1,1)""")
        for key, value in ((CONFIG, config), (STATE, state), (CALENDAR, calendar)):
            connection.execute('INSERT INTO platform_setting VALUES(?,?,1)',
                               (key, json.dumps(value, ensure_ascii=False)))
        original = _settings(connection)
        auction_config = tuple(connection.execute('SELECT * FROM market_auction_automation').fetchone())
        auction_receipt = tuple(connection.execute('SELECT * FROM market_auction_automation_run').fetchone())
    bundle = tmp_path / 'bundle'; perform_backup(source, bundle)
    manifest = verify_backup(bundle); original_bundle = _bundle_sha(bundle)
    report = perform_restore(bundle, tmp_path / 'restored')
    assert report['automatic_review_disabled'] is True and report['auction_automation_disabled'] is True
    assert {'active', 'queued'} <= set(report['paused_jobs'])
    assert _bundle_sha(bundle) == original_bundle
    assert verify_backup(bundle) == manifest
    with connect_sqlite(tmp_path / 'restored/business/app.sqlite3', 'ro') as connection:
        restored = _settings(connection)
        expected = deepcopy(config); expected['revision'] += 1
        expected['noon']['enabled'] = expected['after_close']['enabled'] = False
        assert json.loads(restored.pop(CONFIG)) == expected
        assert restored == {key: value for key, value in original.items() if key != CONFIG}
        assert connection.execute("SELECT mode,revision FROM market_auction_automation").fetchone()[:] == ('off', 2)
        run = connection.execute('SELECT * FROM market_auction_automation_run').fetchone()
        assert run['trade_date'] == '2026-10-19' and run['job_id'] == 'active'
        assert run['schedule_revision'] == 1 and run['attempts'] == 1
        assert run['state'] == 'paused' and run['automatic_control'] == 0 and run['retry_after_at_ms'] is None
        for job_id in ('active', 'queued'):
            job = connection.execute('SELECT status,phase,lease_owner,lease_expires_at_ms FROM job WHERE job_id=?', (job_id,)).fetchone()
            assert tuple(job) == ('paused', 'restored_waiting_for_manual_resume', None, None)
        assert connection.execute("SELECT count(*) FROM job WHERE status IN ('running','queued')").fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == 3
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    with connect_sqlite(source / 'business/app.sqlite3', 'ro') as connection:
        assert _settings(connection) == original
        assert tuple(connection.execute('SELECT * FROM market_auction_automation').fetchone()) == auction_config
        assert tuple(connection.execute('SELECT * FROM market_auction_automation_run').fetchone()) == auction_receipt
        assert connection.execute("SELECT status FROM job WHERE job_id='active'").fetchone()[0] == 'running'


@pytest.mark.parametrize('settings_table', [False, True])
def test_legacy_backup_without_daily_config_does_not_create_settings(source, tmp_path, settings_table):
    if settings_table:
        with connect_sqlite(source / 'business/app.sqlite3') as connection:
            connection.executescript((ROOT / 'backend/app/migrations/003_platform.sql').read_text())
    bundle = tmp_path / 'bundle'; perform_backup(source, bundle)
    report = perform_restore(bundle, tmp_path / 'restored')
    assert report['automatic_review_disabled'] is False
    with connect_sqlite(tmp_path / 'restored/business/app.sqlite3', 'ro') as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_setting'").fetchone()
        assert bool(exists) is settings_table
        if exists:
            assert connection.execute('SELECT 1 FROM platform_setting WHERE key=?', (CONFIG,)).fetchone() is None


@pytest.mark.parametrize('damage', ['structure', 'revision', 'switch', 'time'])
def test_malformed_config_rejects_restore_without_modifying_bundle(source, tmp_path, damage):
    config = {'revision': 3, 'noon': {'enabled': True, 'time': '12:05'},
              'after_close': {'enabled': True, 'time': '18:30'}}
    if damage == 'revision': config['revision'] = True
    if damage == 'switch': config['noon']['enabled'] = 'yes'
    if damage == 'time': config['after_close']['time'] = '17:59'
    value = json.dumps([] if damage == 'structure' else config)
    with connect_sqlite(source / 'business/app.sqlite3') as connection:
        connection.executescript((ROOT / 'backend/app/migrations/003_platform.sql').read_text())
        connection.execute('INSERT INTO platform_setting VALUES(?,?,1)', (CONFIG, value))
    bundle = tmp_path / 'bundle'; perform_backup(source, bundle)
    original = _bundle_sha(bundle); target = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='Invalid automatic review'):
        perform_restore(bundle, target)
    assert not target.exists() and _bundle_sha(bundle) == original
    assert not list(tmp_path.glob('.shixu-restore-*'))
