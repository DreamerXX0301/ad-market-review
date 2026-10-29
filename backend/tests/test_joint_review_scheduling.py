"""Joint auction/noon gates over isolated SQLite and synthetic calendar data."""
import json
import shutil

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import core
from app.infrastructure import database
from app.routers import platform
from app.services import automatic_tasks as automatic, task_service as tasks
from test_automatic_tasks import DAY, enable, scheduled


@pytest.mark.parametrize('submission', ['manual_http', 'automatic'])
def test_new_increment_freezes_both_cleanup_policies_and_keeps_old_job(scheduled, submission):
    source, clock = scheduled
    clock[0] = clock[0].replace(hour=18, minute=30)
    old_parameters = {'source_id': 'fuyao', 'data_chain_version': 'sector-charts-increment-v1',
                      'sync_completion_date': '2026-10-28'}
    old = tasks.create_task('increment', old_parameters, target_date='2026-10-28',
                            request_key='legacy-before-joint-release')
    tasks.finish(old['job_id'])
    with core.connect() as connection:
        before = connection.execute('SELECT parameters_json,parameters_hash FROM job WHERE job_id=?',
                                    (old['job_id'],)).fetchone()
        before = tuple(before)
    if submission == 'manual_http':
        app = FastAPI(); app.include_router(platform.router)
        with TestClient(app) as client:
            response = client.post('/api/v1/sync/tasks', json={
                'mode': 'increment', 'request_key': 'manual-joint-increment'})
        assert response.status_code == 200
        created = response.json()
    else:
        enable(noon=False, after_close=True)
        identifiers = automatic.AutomaticReviewScheduler().tick()['created']
        assert len(identifiers) == 1
        created = tasks.task_view(identifiers[0])
        assert automatic.AutomaticReviewScheduler().tick()['created'] == []
    parameters = created['parameters']
    assert parameters['sync_completion_date'] == DAY
    assert parameters['noon_retention_policy'] == 'noon-retention-28d-v1'
    assert parameters['auction_retention_policy'] == 'auction-retention-28d-v1'
    assert parameters['noon_close_policy'] == 'noon-close-v1'
    assert parameters['increment_session_version'] == 'increment-workbench-lock-v1'
    assert parameters['workspace_followup_version'] == 'after-data-chain-v1'
    with core.connect() as connection:
        saved = connection.execute('SELECT parameters_json FROM job WHERE job_id=?',
                                   (created['job_id'],)).fetchone()
        assert json.loads(saved[0]) == parameters
        assert tuple(connection.execute('SELECT parameters_json,parameters_hash FROM job WHERE job_id=?',
                                        (old['job_id'],)).fetchone()) == before
        assert connection.execute("SELECT count(*) FROM job WHERE kind='increment'").fetchone()[0] == 2
    assert not any(key in old_parameters for key in ('noon_retention_policy', 'auction_retention_policy'))
    assert 'workspace_followup_version' not in old_parameters
    assert len(source.calls) == (1 if submission == 'automatic' else 0)


def _facts(connection, names):
    return {name: sorted([tuple(row) for row in connection.execute('SELECT * FROM "' + name + '"')],
                         key=repr) for name in names}


def test_schema29_to32_preserves_old_facts_and_immutable_migration_sha(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'data'))
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR',
                'SHIXU_WORK_DIR', 'SHIXU_CREDENTIAL_DIR'):
        monkeypatch.delenv(key, raising=False)
    original = database.MIGRATIONS_DIR
    old_migrations = tmp_path / 'schema29'; old_migrations.mkdir()
    for path in original.glob('[0-9]*.sql'):
        if int(path.name.split('_', 1)[0]) <= 29:
            shutil.copyfile(path, old_migrations / path.name)
    monkeypatch.setattr(database, 'MIGRATIONS_DIR', old_migrations)
    core.ensure_ready()
    task = tasks.create_task('research', {'synthetic': 'pre-upgrade frozen task'},
                             request_key='schema29-fixture-only')
    core.set_setting('joint-migration-fixture', {'preserve': ['中文', None, 1.25]})
    with core.connect() as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 29
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'_schema_migration'")]
        before = _facts(connection, names)
        ddl = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL"))
        versions = {row[0]: row[1] for row in connection.execute('SELECT name,sha256 FROM _schema_migration')}
    monkeypatch.setattr(database, 'MIGRATIONS_DIR', original)
    core.ensure_ready()
    with core.connect() as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 32
        assert _facts(connection, names) == before
        current_ddl = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL"))
        assert all(current_ddl[name] == sql for name, sql in ddl.items())
        current = {row[0]: row[1] for row in connection.execute('SELECT name,sha256 FROM _schema_migration')}
        assert {name: current[name] for name in versions} == versions
        assert set(current) - set(versions) == {
            '030_auction_snapshots.sql', '031_auction_automation.sql', '032_auction_retention.sql'}
        assert all(current[name] == core.hash_file(original / name) for name in current)
        assert connection.execute("SELECT mode,revision,instrument_ids_json FROM market_auction_automation").fetchone()[:] == ('off', 0, '[]')
        for name in ('market_auction_capture', 'market_auction_snapshot',
                     'market_auction_automation_run', 'market_auction_purge'):
            assert connection.execute('SELECT count(*) FROM ' + name).fetchone()[0] == 0
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        all_names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        after = _facts(connection, all_names)
    core.ensure_ready()
    with core.connect() as connection:
        assert _facts(connection, all_names) == after
        assert connection.execute('SELECT status FROM job WHERE job_id=?', (task['job_id'],)).fetchone()[0] == 'queued'
