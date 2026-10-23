"""Partition/reference closure survives an independent standard backup and restore."""
import importlib.util
from pathlib import Path
import sys

import pytest

from app import core
from app.analytics.market import MarketData
from app.services import sync_service as sync
from test_sync import transport,start_job

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))


def module(name):
    spec=importlib.util.spec_from_file_location('storage_acceptance_'+name,ROOT/'scripts'/f'{name}.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def test_standard_backup_is_self_contained_for_shared_market_and_reference_parts(transport,tmp_path,monkeypatch):
    sync.execute_sync(start_job());source=core.data_dir();original=core.get_market();head=original.status()
    assert head['storage_layout']=='market-partitions-v1'
    before={period:original.chart('600000.SH',period)['bars'] for period in ('D','W','M')}
    backup=module('backup');bundle=tmp_path/'backup';backup.perform_backup(source,bundle)
    restore=module('restore');manifest=restore.verify_backup(bundle)
    paths=[item['path'] for item in manifest['files']]
    assert any(item.startswith('published/market-objects/reference/') for item in paths)
    assert any(item.startswith('published/market-objects/daily/') for item in paths)
    assert len(paths)==len(set(paths))
    restored=tmp_path/'restored';restore.perform_restore(bundle,restored)
    # The restored graph must never refer to its source or backup locations.
    for path in (restored/'published').rglob('*'):
        assert not path.is_symlink()
    recovered=MarketData(restored,head['snapshot_id'])
    assert recovered.reference()==original.reference()
    for period in ('D','W','M'):assert recovered.chart('600000.SH',period)['bars']==before[period]


def test_standard_backup_rejects_a_missing_shared_reference_dependency(transport,tmp_path):
    sync.execute_sync(start_job());source=core.data_dir();head=core.get_market().status()
    item=next(row for row in head['files'] if row['role'].startswith('reference-part/'))
    (source/item['path']).unlink()
    with pytest.raises(ValueError,match='missing'):module('backup').perform_backup(source,tmp_path/'failed-backup')


def test_conversion_registers_into_existing_restored_head_without_initialization_or_network(transport,tmp_path,monkeypatch):
    sync.execute_sync(start_job());source=core.data_dir();before=core.get_market().status()
    backup=module('backup');bundle=tmp_path/'registration-backup';backup.perform_backup(source,bundle)
    target=tmp_path/'candidate';module('restore').perform_restore(bundle,target)
    converter=module('compact_market');result=converter.compact_generation(source,before['snapshot_id'],target)
    # Existing restored heads must not route through first-initialization guards.
    registration=converter.register_conversion(target,result['status'])
    assert registration['generation']==result['generation']
    assert core.data_dir()==source  # Maintenance helpers must not leak path overrides.
    for key,value in {'SHIXU_DATA_DIR':str(target),'SHIXU_SQLITE_PATH':str(target/'business/app.sqlite3'),'SHIXU_PUBLISHED_DIR':str(target/'published'),'SHIXU_WORK_DIR':str(target/'work'),'SHIXU_RAW_DIR':str(target/'raw')}.items():monkeypatch.setenv(key,value)
    with core.connect() as connection:
        row=connection.execute('SELECT * FROM job WHERE job_id=?',(registration['task_id'],)).fetchone()
        assert row['kind']=='source_check' and row['status']=='partial'
        assert 'local_storage_conversion' in row['parameters_json']
    assert core.get_market().status()['snapshot_id']==result['generation']
    assert converter.register_conversion(target,result['status'])['already_registered']


@pytest.mark.parametrize('pending_kind',['collection','gc_marker'])
def test_backup_rejects_incomplete_market_gc_before_creating_a_false_success(transport,tmp_path,pending_kind):
    sync.execute_sync(start_job());source=core.data_dir()
    if pending_kind=='collection':
        with core.connect() as connection:
            connection.execute('INSERT INTO market_storage_collection VALUES(?,?,?,?,?,?,NULL)',('pending','market-storage-gc-v1','{}',core.digest({}),'planned',core.now_ms()))
    else:
        with core.connect() as connection:
            job=connection.execute('SELECT job_id FROM job LIMIT 1').fetchone()[0]
            artifact=dict(connection.execute("SELECT * FROM snapshot_artifact WHERE role='daily.parquet' LIMIT 1").fetchone())
        sid=core.publish_snapshot(module='analysis_bars',job_id=job,start='2026-10-19',end='2026-10-19',artifacts=[artifact],snapshot_id='gc-backup-unreferenced',activate=False)
        with core.connect() as connection:connection.execute("INSERT INTO snapshot_gc VALUES(?,'gc_pending',?,'controlled maintenance fixture')",(sid,core.now_ms()))
    with pytest.raises(ValueError,match='incomplete'):module('backup').perform_backup(source,tmp_path/'must-not-exist')
    assert not (tmp_path/'must-not-exist').exists()
