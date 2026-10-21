"""Rebuilding keeps board configuration and discards board result state.

All roots are temporary fixtures. No current installation is prepared/reset.
"""
import hashlib
import json
import sqlite3

import pytest

from test_strategy_preserving_reinitialization import stored, rebuild


def add_sector_configuration_with_results():
    from app import core
    from app.services import sector_strategies as strategies, task_service as tasks
    from app.services.workspace_common import insert
    first = strategies.create_strategy({'name': '保留的板块策略', 'period': 'M', 'sector_kind': 'concept',
        'definition': {'field': 'close', 'op': '>', 'right_field': 'ma30', 'period': 'W'},
        'policy': {'duration_sessions': 40}})
    latest = strategies.update_strategy(first['strategy_id'], {
        'definition': {'all': [{'field': 'close', 'op': '>', 'value': 50, 'period': 'D'},
                              {'field': 'macd_cross', 'op': '==', 'value': True, 'period': 'M'}]},
        'policy': {'duration_sessions': 50, 'exits': [{'field': 'close_below_entry_low', 'op': '==', 'value': True}]}})
    task = tasks.create_task('selection', {'target_type': 'sector', 'strategy_id': first['strategy_id']})
    tasks.finish(task['job_id'], 'succeeded')
    with core.connect() as connection:
        insert(connection, 'sector_selection_run', {'run_id': 'sector-run-fixture', 'job_id': task['job_id'],
            'strategy_id': first['strategy_id'], 'strategy_version': 2, 'business_date': '2026-10-20',
            'market_snapshot_id': 'fixture-sector-generation', 'catalog_signature': 'a'*64,
            'input_sha256': 'b'*64, 'cache_refs_json': '{}', 'status': 'completed',
            'created_at_ms': 1, 'completed_at_ms': 2})
        insert(connection, 'sector_selection_candidate', {'run_id': 'sector-run-fixture',
            'topic_code': '885728.TI', 'sector_kind': 'concept', 'name': '板块样本',
            'decision': 'matched', 'close_price_e8': 10000000000,
            'metrics_json': json.dumps({'metrics_by_period': {'D': {'close': 100, 'high': 105, 'low': 95}}}),
            'checks_json': '{"result":true}', 'cache_refs_json': '{}'})
        policy = json.dumps(latest['policy'])
        insert(connection, 'sector_tracking_round', {'round_id': 'sector-round-fixture',
            'strategy_id': first['strategy_id'], 'entry_strategy_version': 2,
            'origin_run_id': 'sector-run-fixture', 'target_id': '885728.TI', 'topic_code': '885728.TI',
            'topic_id': 'fuyao-885728.TI', 'name': '板块样本', 'sector_kind': 'concept',
            'entry_period': 'M', 'entry_business_date': '2026-10-20', 'entry_price_e8': 10000000000,
            'entry_high_e8': 10500000000, 'entry_low_e8': 9500000000,
            'entry_market_snapshot_id': 'fixture-sector-generation', 'entry_cache_refs_json': '{}',
            'entry_basis_json': '{}', 'entry_policy_json': policy, 'policy_json': policy,
            'policy_revision': 1, 'duration_sessions': 50, 'status': 'tracking',
            'created_at_ms': 1, 'updated_at_ms': 1})
        insert(connection, 'sector_tracking_event', {'event_id': 'sector-event-fixture',
            'event_key': 'sector-event-key-fixture', 'round_id': 'sector-round-fixture',
            'event_type': 'entry', 'business_date': '2026-10-20', 'policy_revision': 1,
            'data_state': 'ready', 'outcome': 'not_applicable', 'trading_age': 0,
            'evidence_json': '{}', 'recorded_at_ms': 1})
    return latest


def test_empty_sector_tables_keep_original_configuration_shape_and_hash(stored):
    root, _ = stored
    connection = rebuild.connect_sqlite(root/'business/app.sqlite3', 'ro')
    try: data = rebuild.configuration(connection)
    finally: connection.close()
    assert not set(rebuild.SECTOR_CONFIG_TABLES) & data.keys()
    original_hash = hashlib.sha256(rebuild.canonical(data).encode()).hexdigest()
    assert rebuild.configuration_hash(data) == original_hash
    with_explicit_empty = {**data, **{table: [] for table in rebuild.SECTOR_CONFIG_TABLES}}
    assert rebuild.configuration_hash(with_explicit_empty) == original_hash


def test_nullable_signal_period_preserves_legacy_hash_without_mutating_clone_data():
    old = {'strategy_version': [{'strategy_id': 'legacy-D', 'version': 1, 'period': 'D'},
                                {'strategy_id': 'legacy-W', 'version': 1, 'period': 'W'}],
           'strategy': [{'strategy_id': 'legacy-D'}, {'strategy_id': 'legacy-W'}]}
    migrated = {**old, 'strategy_version': [{**row, 'signal_period': None} for row in old['strategy_version']]}
    original = json.loads(json.dumps(migrated))
    assert rebuild.configuration_hash(migrated) == hashlib.sha256(rebuild.canonical(old).encode()).hexdigest()
    assert migrated == original and all('signal_period' in row for row in migrated['strategy_version'])
    changed = {**migrated, 'strategy_version': [{**migrated['strategy_version'][0], 'signal_period': 'M'}, migrated['strategy_version'][1]]}
    assert rebuild.configuration_hash(changed) != rebuild.configuration_hash(old)


def test_old_database_without_optional_sector_tables_is_read_only_compatible(stored):
    root, _ = stored
    source = rebuild.connect_sqlite(root/'business/app.sqlite3', 'ro')
    legacy = sqlite3.connect(':memory:'); legacy.row_factory = sqlite3.Row
    try:
        source.backup(legacy)
        expected = rebuild.configuration_hash(rebuild.configuration(source))
        legacy.execute('PRAGMA foreign_keys=OFF')
        for table in (*reversed(rebuild.SECTOR_RESULT_TABLES), *reversed(rebuild.SECTOR_CONFIG_TABLES)):
            legacy.execute('DROP TABLE '+table)
        legacy.commit()
        assert rebuild.configuration_hash(rebuild.configuration(legacy)) == expected
        # The compatibility reader does not migrate/recreate these tables.
        assert legacy.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'sector_%'").fetchone()[0] == 0
    finally:
        source.close(); legacy.close()


def test_prepare_and_activate_keep_sector_versions_policy_but_clear_results(stored, tmp_path):
    root, _ = stored
    latest = add_sector_configuration_with_results()
    connection = rebuild.connect_sqlite(root/'business/app.sqlite3', 'ro')
    try:
        before = rebuild.configuration(connection)
        fingerprint = rebuild.configuration_hash(before)
    finally: connection.close()
    ciphertext = rebuild.credential_hashes(root)
    prepared = tmp_path/'prepared-sector'
    receipt = rebuild.prepare(root, prepared, tmp_path/'backup-sector')
    assert receipt['configuration_sha256'] == fingerprint
    assert receipt['retained_counts']['sector_strategy'] == 1
    assert receipt['retained_counts']['sector_strategy_version'] == 2
    assert set(rebuild.SECTOR_RESULT_TABLES) <= set(receipt['cleared_tables'])
    live = rebuild.connect_sqlite(root/'business/app.sqlite3', 'ro')
    try:
        assert all(live.execute('SELECT count(*) FROM '+table).fetchone()[0] == 1 for table in rebuild.SECTOR_RESULT_TABLES)
    finally: live.close()
    rebuild.activate(prepared, tmp_path/'recoverable-sector')
    connection = rebuild.connect_sqlite(root/'business/app.sqlite3', 'ro')
    try:
        assert rebuild.configuration_hash(rebuild.configuration(connection)) == fingerprint
        assert all(connection.execute('SELECT count(*) FROM '+table).fetchone()[0] == 0 for table in rebuild.SECTOR_RESULT_TABLES)
        strategy = connection.execute('SELECT * FROM sector_strategy WHERE strategy_id=?', (latest['strategy_id'],)).fetchone()
        assert strategy['current_version'] == 2 and strategy['name'] == latest['name']
        versions = connection.execute('SELECT * FROM sector_strategy_version WHERE strategy_id=? ORDER BY version', (latest['strategy_id'],)).fetchall()
        assert versions[0]['period'] == versions[1]['period'] == 'M'
        assert json.loads(versions[0]['policy_json'])['duration_sessions'] == 40
        assert json.loads(versions[1]['policy_json']) == latest['policy']
    finally: connection.close()
    assert rebuild.credential_hashes(root) == ciphertext


def test_activation_detects_sector_configuration_modified_after_prepare(stored, tmp_path):
    root, _ = stored
    strategy = add_sector_configuration_with_results()
    prepared = tmp_path/'prepared-sector'
    rebuild.prepare(root, prepared, tmp_path/'backup-sector')
    connection = rebuild.connect_sqlite(root/'business/app.sqlite3')
    try:
        connection.execute('UPDATE sector_strategy SET name=? WHERE strategy_id=?', ('后来修改板块策略', strategy['strategy_id']))
        connection.commit()
    finally: connection.close()
    with pytest.raises(ValueError, match='changed since preparation'):
        rebuild.activate(prepared, tmp_path/'recoverable-sector')
    assert prepared.is_dir() and root.is_dir()


def test_prepared_sector_result_leak_is_rejected_even_without_stock_results(stored, tmp_path):
    root, _ = stored
    add_sector_configuration_with_results()
    prepared = tmp_path/'prepared-sector'
    rebuild.prepare(root, prepared, tmp_path/'backup-sector')
    connection = rebuild.connect_sqlite(prepared/'business/app.sqlite3')
    try:
        connection.execute('PRAGMA foreign_keys=OFF')
        # Explicitly exercise the board-result guard, independently of job.
        connection.execute("INSERT INTO sector_selection_candidate VALUES('leaked-run','885728.TI','concept','遗留结果','unknown',NULL,'{}','{}','{}')")
        with pytest.raises(ValueError, match='contains old market'):
            # A candidate artifact without a parent is impossible with normal
            # FK writes; a corrupt/stale prepared root must still be rejected.
            rebuild.assert_empty(connection, prepared)
    finally:
        connection.rollback(); connection.close()
