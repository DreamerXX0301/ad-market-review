"""Restoring schema43 keeps evidence but drops new monitoring authorization."""
import json
from pathlib import Path
import pytest

from backup import connect_sqlite, perform_backup, sha
from restore import perform_restore, verify_backup
from test_deployment import source

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('schema', [43, 44])
def test_restore_disables_automatic_rules_preserves_receipts_and_only_drops_baseline_pins(source, tmp_path, schema):
    database = source / 'business/app.sqlite3'
    with connect_sqlite(database) as connection:
        connection.executescript((ROOT / 'backend/app/migrations/043_automatic_price_alerts.sql').read_text())
        if schema == 44:
            connection.executescript((ROOT / 'backend/app/migrations/044_short_week_price_alerts.sql').read_text())
        for identifier, symbol, revision, enabled in [('on', '600001.SH', 3, 1), ('off', '000001.SZ', 7, 0)]:
            connection.execute('''INSERT INTO automatic_price_alert_rule
                (rule_id,instrument_id,revision,enabled,modes_json,require_volume,algorithm,effective_after_date,created_at_ms,updated_at_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (identifier, symbol, revision, enabled, '["close_breakout"]', 1, 'structure-volume-levels-v1', '2026-10-19', 1, 1))
            if schema == 44:
                connection.execute("UPDATE automatic_price_alert_rule SET profile='short_week_v1',max_risk_pct=3.5 WHERE rule_id=?", (identifier,))
            connection.execute('INSERT INTO automatic_price_alert_baseline VALUES(?,?,?,?,?,?)',
                (identifier, revision, '2026-10-19', 'bars', '{"as_of":"2026-10-19","levels":{"support":{"price":10}}}', 1))
            connection.execute('INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms) VALUES(?,?,?,?,1)',
                ('baseline-' + identifier, 'bars', 'manual', 'auto-price-baseline:' + identifier))
        connection.execute('INSERT INTO automatic_price_alert_event VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('event', 'on', 3, '600001.SH', 'close_breakout', '2026-10-19', 'triggered', 'import', '{"old_price":10.2}', 1))
        connection.execute('INSERT INTO automatic_price_alert_evaluation VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('import', 'on', 3, '600001.SH', 'close_breakout', '2026-10-19', 'triggered', None, '{"old_basis":10}', 'event', 1))
        for identifier, owner in [('event-pin', 'auto-price-event:event'), ('manual-pin', 'line-alert:original')]:
            connection.execute('INSERT INTO retention_pin(pin_id,snapshot_id,owner_kind,owner_id,created_at_ms) VALUES(?,?,?,?,1)',
                (identifier, 'bars', 'manual', owner))
        old_rule = tuple(connection.execute("SELECT * FROM automatic_price_alert_rule WHERE rule_id='on'").fetchone())
        old_clock = connection.execute('SELECT revision FROM automatic_price_alert_clock').fetchone()[0]
        old_events = [tuple(row) for row in connection.execute('SELECT * FROM automatic_price_alert_event')]
        old_evaluations = [tuple(row) for row in connection.execute('SELECT * FROM automatic_price_alert_evaluation')]
    bundle, target = tmp_path / 'bundle', tmp_path / 'restored'
    perform_backup(source, bundle)
    manifest = verify_backup(bundle)
    inventory = {path.relative_to(bundle).as_posix(): sha(path) for path in bundle.rglob('*') if path.is_file()}
    report = perform_restore(bundle, target)
    assert report['automatic_price_alerts_disabled'] is True
    assert report['automatic_price_alert_reset'] == {'disabled_rules': 1, 'removed_baselines': 2, 'removed_baseline_pins': 2}
    with connect_sqlite(target / 'business/app.sqlite3', 'ro') as connection:
        rules = {row['rule_id']: dict(row) for row in connection.execute('SELECT * FROM automatic_price_alert_rule')}
        assert rules['on']['enabled'] == 0 and rules['on']['revision'] == 4
        assert rules['off']['enabled'] == 0 and rules['off']['revision'] == 7
        assert rules['on']['modes_json'] == '["close_breakout"]' and rules['on']['require_volume'] == 1
        if schema == 44:
            assert rules['on']['profile'] == rules['off']['profile'] == 'short_week_v1'
            assert rules['on']['max_risk_pct'] == rules['off']['max_risk_pct'] == 3.5
        assert connection.execute('SELECT revision FROM automatic_price_alert_clock').fetchone()[0] > old_clock
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_baseline').fetchone()[0] == 0
        assert [tuple(row) for row in connection.execute('SELECT * FROM automatic_price_alert_event')] == old_events
        assert [tuple(row) for row in connection.execute('SELECT * FROM automatic_price_alert_evaluation')] == old_evaluations
        assert {row[0] for row in connection.execute("SELECT owner_id FROM retention_pin WHERE owner_kind='manual'")} == {'auto-price-event:event', 'line-alert:original'}
        assert connection.execute("SELECT count(*) FROM job WHERE status IN ('queued','running')").fetchone()[0] == 0
        assert set(report['paused_jobs']) == {'active', 'queued'}
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    with connect_sqlite(database, 'ro') as connection:
        assert tuple(connection.execute("SELECT * FROM automatic_price_alert_rule WHERE rule_id='on'").fetchone()) == old_rule
        assert connection.execute('SELECT count(*) FROM automatic_price_alert_baseline').fetchone()[0] == 2
    assert {path.relative_to(bundle).as_posix(): sha(path) for path in bundle.rglob('*') if path.is_file()} == inventory
    assert verify_backup(bundle) == manifest


def test_legacy_restore_does_not_create_automatic_price_tables(source, tmp_path):
    bundle, target = tmp_path / 'legacy-bundle', tmp_path / 'legacy-restored'
    perform_backup(source, bundle)
    report = perform_restore(bundle, target)
    assert 'automatic_price_alerts_disabled' not in report
    with connect_sqlite(target / 'business/app.sqlite3', 'ro') as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'automatic_price_alert_%'").fetchall() == []
