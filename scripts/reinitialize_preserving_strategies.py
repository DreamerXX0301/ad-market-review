#!/usr/bin/env python3
"""Prepare and activate a recoverable empty market root, retaining strategy configuration.

Run preparation after verification; stop API and worker before activation. This
does not submit initialization or read decrypted credentials. Runtime data and
configuration exports belong outside Git. The original data root is renamed,
never deleted, so an operator can recover all previous results.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from backup import perform_backup, sha, connect_sqlite, check_db
from restore import verify_backup

STRATEGY_TABLES = ('strategy', 'strategy_version', 'tracking_policy_version')
SECTOR_CONFIG_TABLES = ('sector_strategy', 'sector_strategy_version')
CONFIG_TABLES = ('provider_connection', *STRATEGY_TABLES, 'platform_setting', 'topic', 'topic_revision', 'topic_override', *SECTOR_CONFIG_TABLES)
SECTOR_RESULT_TABLES = ('sector_selection_run', 'sector_selection_candidate', 'sector_tracking_round', 'sector_tracking_event')
EMPTY_TABLES = ('job', 'selection_run', 'selection_candidate', 'tracking_round', 'research_report', 'backtest_run', 'dataset_snapshot', 'published_head', 'business_clock', *SECTOR_RESULT_TABLES)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def configuration(connection):
    existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    data = {table: [dict(row) for row in connection.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
            for table in CONFIG_TABLES if table not in SECTOR_CONFIG_TABLES or table in existing}
    # An old installation and a migrated installation with no board strategies
    # represent exactly the same configuration. Keep their established hash.
    if not any(data.get(table) for table in SECTOR_CONFIG_TABLES):
        for table in SECTOR_CONFIG_TABLES: data.pop(table, None)
    # A stale worker heartbeat is runtime state. Source/strategy preferences are
    # configuration; retain their exact JSON without logging their values.
    data['platform_setting'] = [row for row in data['platform_setting'] if row['key'] != 'worker_heartbeat']
    requested = set()
    def visit(node):
        if isinstance(node, dict):
            if node.get('field') == 'topic_ids' and isinstance(node.get('value'), str): requested.add(node['value'])
            for value in node.values(): visit(value)
        elif isinstance(node, list):
            for value in node: visit(value)
    for table in ('strategy_version', 'tracking_policy_version'):
        for row in data[table]:
            for key, value in row.items():
                if key.endswith('_json') and isinstance(value, str): visit(json.loads(value))
    keep = ({row['topic_id'] for row in data['topic'] if row['origin'] == 'manual'}
            | {row['topic_id'] for row in data['topic_override']} | requested)
    for table in ('topic', 'topic_revision', 'topic_override'):
        data[table] = [row for row in data[table] if row['topic_id'] in keep]
    return data


def configuration_hash(data):
    normalized = dict(data)
    if 'strategy_version' in normalized:
        # Migration010 adds an optional signal period. NULL is the unchanged
        # legacy D/W interpretation, not a new user configuration value.
        normalized['strategy_version'] = [
            {key: value for key, value in row.items() if key != 'signal_period' or value is not None}
            for row in normalized['strategy_version']]
    if not any(normalized.get(table) for table in SECTOR_CONFIG_TABLES):
        for table in SECTOR_CONFIG_TABLES: normalized.pop(table, None)
    return hashlib.sha256(canonical(normalized).encode()).hexdigest()


def credential_hashes(root):
    folder = root / 'credentials'
    if folder.is_symlink(): raise ValueError('Credential directory must not be a symlink')
    result = {}
    for file in sorted(folder.glob('*.enc')):
        if file.is_symlink() or not file.is_file(): raise ValueError('Invalid credential ciphertext')
        result[file.name] = sha(file)
    return result


def assert_idle(connection):
    if connection.execute("SELECT count(*) FROM job WHERE status IN ('running','queued','paused')").fetchone()[0]:
        raise ValueError('Finish or explicitly cancel existing active tasks before rebuilding')


def assert_empty(connection, root):
    existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if any(connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in EMPTY_TABLES
           if table not in SECTOR_RESULT_TABLES or table in existing):
        raise ValueError('Prepared root contains old market or derived results')
    if any(path.is_file() for folder in ('published','raw') for path in (root/folder).rglob('*')):
        raise ValueError('Prepared root contains market files')


def independent(*paths):
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for index,a in enumerate(paths) for b in paths[index+1:]):
        raise ValueError('Data, staging, backup and recovery paths must be independent')


def clone_configuration(data, database):
    connection = connect_sqlite(database)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('PRAGMA defer_foreign_keys=ON')
        # Date guards protect new UI edits. This isolated administrative import
        # restores original configuration timestamps verbatim, before any market
        # clock exists; recreate the exact migration triggers before publishing.
        triggers = list(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN ('topic_revision_current_day','topic_override_current_day')"))
        for trigger in triggers: connection.execute('DROP TRIGGER ' + trigger['name'])
        for table in CONFIG_TABLES:
            for row in data.get(table, []):
                columns = list(row)
                values = [row[key] for key in columns]
                if table == 'strategy':
                    values = [None if key in ('current_version','current_policy_version') else row[key] for key in columns]
                query = 'INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')'
                if table == 'provider_connection':
                    query += ' ON CONFLICT(connection_id) DO UPDATE SET ' + ','.join(key + '=excluded.' + key for key in columns if key != 'connection_id')
                connection.execute(query, values)
        for row in data['strategy']:
            connection.execute('UPDATE strategy SET current_version=?,current_policy_version=? WHERE strategy_id=?', (row['current_version'], row['current_policy_version'], row['strategy_id']))
        for trigger in triggers: connection.execute(trigger['sql'])
        connection.commit()
        check_db(connection)
        if configuration_hash(configuration(connection)) != configuration_hash(data): raise ValueError('Retained strategy configuration differs after cloning')
        assert_empty(connection,database.parents[1])
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except BaseException:
        connection.rollback()
        raise
    finally: connection.close()


def prepare(data_root, staging_root, backup_root):
    data_root, staging_root, backup_root = (Path(path).expanduser().resolve() for path in (data_root, staging_root, backup_root))
    if staging_root.exists() or backup_root.exists(): raise ValueError('Choose new staging and backup directories')
    independent(data_root,staging_root,backup_root)
    from app.infrastructure.credentials import CredentialStore
    if CredentialStore().master_path.expanduser().resolve().is_relative_to(data_root):
        raise ValueError('The credential master key must live outside the data root')
    # This admin tool deliberately handles the standard single-root layout only.
    # It must not silently leave external storage roots or credentials behind.
    for key in ('SHIXU_SQLITE_PATH', 'SHIXU_PUBLISHED_DIR', 'SHIXU_RAW_DIR', 'SHIXU_WORK_DIR', 'SHIXU_CREDENTIAL_DIR'):
        if os.environ.get(key): raise ValueError('Use the standard data-root layout for this rebuild tool: ' + key)
    live = connect_sqlite(data_root / 'business/app.sqlite3', 'ro')
    try: assert_idle(live); before = configuration_hash(configuration(live))
    finally: live.close()
    credentials = credential_hashes(data_root)
    result = perform_backup(data_root, backup_root)
    verify_backup(backup_root)
    frozen = connect_sqlite(backup_root / 'business/app.sqlite3', 'ro')
    try: data = configuration(frozen)
    finally: frozen.close()
    if configuration_hash(data) != before: raise ValueError('Configuration changed during backup; prepare a new snapshot')
    env = dict(os.environ, SHIXU_DATA_DIR=str(staging_root), PYTHONPATH=str(ROOT / 'backend'))
    subprocess.run([sys.executable, '-m', 'app.cli', 'migrate'], cwd=ROOT / 'backend', env=env, check=True, capture_output=True)
    clone_configuration(data, staging_root / 'business/app.sqlite3')
    target = staging_root / 'credentials'; target.mkdir(mode=0o700, exist_ok=True)
    for name, digest in credentials.items():
        source = data_root / 'credentials' / name
        if sha(source) != digest: raise ValueError('Credentials changed during preparation')
        shutil.copyfile(source, target / name); (target / name).chmod(0o600)
    if credential_hashes(staging_root) != credentials: raise ValueError('Credential ciphertexts differ')
    receipt = {'format':'shixu-strategy-preserving-rebuild-v1','source_root':str(data_root),'backup_root':str(backup_root),
               'configuration_sha256':before,'credential_ciphertext_sha256':credentials,'master_key_copied':False,
               'retained_counts':{table:len(data.get(table, [])) for table in CONFIG_TABLES},'backup':result,
               'cleared_tables':list(EMPTY_TABLES),'activation_complete':False}
    (staging_root / 'reinitialization-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return receipt


def activate(staging_root, recoverable_root):
    staging_root, recoverable_root = Path(staging_root).resolve(), Path(recoverable_root).resolve()
    receipt = json.loads((staging_root / 'reinitialization-receipt.json').read_text())
    if receipt.get('format') != 'shixu-strategy-preserving-rebuild-v1' or receipt.get('activation_complete'): raise ValueError('Invalid or already activated preparation')
    live_root = Path(receipt['source_root'])
    if recoverable_root.exists() or recoverable_root.is_relative_to(live_root): raise ValueError('Choose a new recovery directory outside live data')
    independent(live_root,staging_root,recoverable_root,Path(receipt['backup_root']))
    verify_backup(receipt['backup_root'])
    # The worker's own lock proves its writer is stopped. The operator must stop
    # the API too; no running connection may remain bound to the renamed root.
    with contextlib.ExitStack() as stack:
        lock = stack.enter_context((live_root / 'work/worker.lock').open('a+b'))
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Stop the worker before activation') from None
        prepared_lock=stack.enter_context((staging_root/'work/worker.lock').open('a+b'))
        try: fcntl.flock(prepared_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Stop any worker using the prepared root') from None
        live = connect_sqlite(live_root / 'business/app.sqlite3')
        try:
            assert_idle(live)
            if configuration_hash(configuration(live)) != receipt['configuration_sha256']: raise ValueError('Strategies or source configuration changed since preparation')
            check_db(live); live.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally: live.close()
        if credential_hashes(live_root) != receipt['credential_ciphertext_sha256']: raise ValueError('Credentials changed since preparation')
        prepared = connect_sqlite(staging_root / 'business/app.sqlite3', 'ro')
        try:
            check_db(prepared)
            if configuration_hash(configuration(prepared)) != receipt['configuration_sha256']: raise ValueError('Prepared configuration was altered')
            assert_empty(prepared,staging_root)
        finally: prepared.close()
        if credential_hashes(staging_root) != receipt['credential_ciphertext_sha256']: raise ValueError('Prepared credentials were altered')
        recoverable_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(live_root, recoverable_root)
        try: os.replace(staging_root, live_root)
        except BaseException:
            os.replace(recoverable_root, live_root)
            raise
    receipt.update(activation_complete=True, recoverable_original_root=str(recoverable_root))
    (live_root / 'reinitialization-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='mode', required=True)
    stage=sub.add_parser('prepare'); stage.add_argument('--data-root', required=True); stage.add_argument('--staging-root', required=True); stage.add_argument('--backup-root', required=True)
    launch=sub.add_parser('activate'); launch.add_argument('--staging-root', required=True); launch.add_argument('--recoverable-root', required=True)
    args=parser.parse_args()
    result=prepare(args.data_root,args.staging_root,args.backup_root) if args.mode=='prepare' else activate(args.staging_root,args.recoverable_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
