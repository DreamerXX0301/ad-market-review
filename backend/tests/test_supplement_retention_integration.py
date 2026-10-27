"""Policy/lock/migration integration uses disposable fixtures and no live services."""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import multiprocessing
from pathlib import Path
import shutil
from types import SimpleNamespace

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import core, worker
from app.services import market_supplement as supplement, task_service as tasks
from app.services import retention_policy, supplement_access as access
from test_platform import isolated
from test_sector_chart_chain import local, http, finish_claimed
from test_sector_charts import DAY


def _lock_process(database,exclusive,ready,entered,release,errors):
    """Spawn a genuinely independent process, not an in-process lock imitation."""
    import os
    os.environ['SHIXU_SQLITE_PATH']=database
    from app.services.supplement_access import supplement_access
    try:
        ready.set()
        with supplement_access(exclusive=exclusive):
            entered.set()
            if not release.wait(8):raise RuntimeError('test release deadline')
    except BaseException as error:
        errors.put(type(error).__name__+': '+str(error))
        raise


@pytest.mark.parametrize('holding,joining,blocks',[(False,False,False),(False,True,True),(True,False,True),(True,True,True)])
def test_reader_collector_file_lock_is_shared_or_exclusive_across_processes(tmp_path,monkeypatch,holding,joining,blocks):
    database=str(tmp_path/'business/app.sqlite3');monkeypatch.setenv('SHIXU_SQLITE_PATH',database)
    context=multiprocessing.get_context('spawn')
    ready=context.Event();entered=context.Event();release=context.Event();errors=context.Queue()
    process=context.Process(target=_lock_process,args=(database,joining,ready,entered,release,errors))
    try:
        with access.supplement_access(exclusive=holding):
            process.start();assert ready.wait(15)
            if blocks:assert not entered.wait(.15),'collector/read lock entered while incompatible holder was active'
            else:assert entered.wait(5),'independent readers should overlap'
        assert entered.wait(5),'waiter should enter after the holder releases'
    finally:
        release.set()
        if process.pid:
            process.join(5)
            if process.is_alive():process.terminate();process.join(2)
    assert process.exitcode==0
    assert errors.empty()


def test_access_lock_releases_on_exception_and_rejects_symlink(tmp_path,monkeypatch):
    database=tmp_path/'business/app.sqlite3';monkeypatch.setenv('SHIXU_SQLITE_PATH',str(database))
    with pytest.raises(RuntimeError):
        with access.supplement_access(exclusive=True):raise RuntimeError('fixture')
    with access.supplement_access():pass
    path=database.parent/'supplement-access.lock';path.unlink()
    target=tmp_path/'outside';target.write_text('sentinel');path.symlink_to(target)
    with pytest.raises(ValueError,match='符号链接'):
        with access.supplement_access():pass
    assert target.read_text()=='sentinel'


def test_policy_versions_are_explicit_and_legacy_is_not_upgraded():
    assert retention_policy.validate(None) is None
    assert retention_policy.validate(retention_policy.VERSION)==retention_policy.VERSION
    for value in ('future-v1','',False,0,{},[]):
        with pytest.raises(ValueError):retention_policy.validate(value)


@pytest.mark.parametrize('mode',['initialise','increment'])
def test_new_manual_sync_freezes_policy_without_source_request(http,local,mode):
    response=http.post('/api/v1/sync/tasks',json={'mode':mode,'request_key':'retention-'+mode})
    assert response.status_code==200,response.text
    task=response.json();assert task['parameters']['retention_policy_version']==retention_policy.VERSION
    assert task['parameters']['data_chain_version']==('sector-charts-v1' if mode=='initialise' else 'sector-charts-increment-v1')
    assert local.adapter.calls==[]


def test_new_manual_supplement_and_offline_repair_have_separate_scope(http,local):
    response=http.post('/api/v1/sync/market-supplement',json={'request_key':'retention-publication','year_history':False,'sector_history':False})
    assert response.status_code==200,response.text
    assert response.json()['parameters']['retention_policy_version']==retention_policy.VERSION
    repaired=http.post('/api/v1/sync/market-supplement',json={'request_key':'readonly-revalidation','revalidate_only':True})
    assert repaired.status_code==200,repaired.text
    assert 'retention_policy_version' not in repaired.json()['parameters']
    assert local.adapter.calls==[]


@pytest.mark.parametrize('authorized',[False,True])
def test_increment_chain_transmits_only_its_frozen_retention_policy(local,authorized):
    parameters={'source_id':'fuyao','data_chain_version':'sector-charts-increment-v1'}
    if authorized:parameters['retention_policy_version']=retention_policy.VERSION
    parent=tasks.create_task('increment',parameters,target_date=DAY,request_key='parent-retention')
    job=finish_claimed(parent);children=worker.recover_followups()
    assert len(children)==1;batch=children[0]
    assert batch['parameters']['operation']=='sector_charts'
    assert batch['parameters'].get('retention_policy_version')==(retention_policy.VERSION if authorized else None)
    assert ('retention_policy_version' in batch['parameters']) is authorized
    finish_claimed(batch)
    children=worker.recover_followups();assert len(children)==1
    child=children[0]
    assert child['parameters']['operation']=='market_supplement'
    assert ('retention_policy_version' in child['parameters']) is authorized
    assert child['parameters'].get('retention_policy_version')==(retention_policy.VERSION if authorized else None)
    assert worker.recover_followups()==[]
    assert tasks.task_view(job['job_id'])['parameters']==parameters
    assert local.adapter.calls==[]


@pytest.mark.parametrize('authorized',[False,True])
def test_initial_chain_keeps_policy_across_topics_boards_and_supplement(local,authorized):
    parameters={'source_id':'fuyao','window':'near-five-years','data_chain_version':'sector-charts-v1'}
    if authorized:parameters['retention_policy_version']=retention_policy.VERSION
    parent=tasks.create_task('initialise',parameters,target_date=DAY)
    finish_claimed(parent);topic=worker.recover_followups()[0]
    assert ('retention_policy_version' in topic['parameters']) is authorized
    finish_claimed(topic);batch=worker.recover_followups()[0]
    assert ('retention_policy_version' in batch['parameters']) is authorized
    finish_claimed(batch);child=worker.recover_followups()[0]
    assert child['parameters']['operation']=='market_supplement'
    assert ('retention_policy_version' in child['parameters']) is authorized
    assert local.adapter.calls==[]


@pytest.mark.parametrize('authorized',[False,True])
def test_supplement_execution_does_not_add_policy_to_old_frozen_task(isolated,monkeypatch,authorized):
    import app.services.supplement_retention as retention
    market=SimpleNamespace(status=lambda:{'snapshot_id':'fixture','data_end':DAY})
    monkeypatch.setattr(supplement,'get_market',lambda:market)
    parameters={'operation':'market_supplement','year_history':False}
    if authorized:parameters['retention_policy_version']=retention_policy.VERSION
    task=tasks.create_task('source_check',parameters,target_date=DAY);job=tasks.claim_next('retention-fixture')
    calls=[]
    monkeypatch.setattr(supplement,'refresh_for_market',lambda *args,**kwargs:calls.append(('refresh',kwargs)) or {'snapshot_id':'fixture','target_date':DAY})
    monkeypatch.setattr(retention,'collect_after_publication',lambda *args,**kwargs:calls.append(('gc',args)) or {'status':'completed'})
    events=[]
    result=supplement.execute_supplement(task['job_id'],progress=lambda *args:events.append(args))
    assert ('retention_policy_version' in calls[0][1]) is authorized
    assert len(calls)==(2 if authorized else 1)
    assert ('retention_result' in result) is authorized
    assert len(events)==int(authorized)
    if authorized:
        assert events[0][0]=='核验补充数据保留引用'
        assert events[0][3]['supplement_published'] is True
    assert tasks.task_view(job['job_id'])['parameters']==parameters


@pytest.mark.parametrize('error_type',[RuntimeError,tasks.TaskPaused,tasks.TaskCancelled])
def test_post_publication_progress_failure_keeps_published_result_or_explicit_interrupt(isolated,monkeypatch,error_type):
    import app.services.supplement_retention as retention
    market=SimpleNamespace(status=lambda:{'snapshot_id':'fixture','data_end':DAY})
    monkeypatch.setattr(supplement,'get_market',lambda:market)
    published={'snapshot_id':'fixture','target_date':DAY,'quality':'partial','message':'already published'}
    monkeypatch.setattr(supplement,'refresh_for_market',lambda *args,**kwargs:dict(published))
    monkeypatch.setattr(retention,'collect_after_publication',lambda *args,**kwargs:pytest.fail('diagnostic failed before collector'))
    task=tasks.create_task('source_check',{'operation':'market_supplement','retention_policy_version':retention_policy.VERSION},target_date=DAY)
    tasks.claim_next('retention-fixture')
    def failed_progress(*args):raise error_type('controlled diagnostic or interruption')
    if error_type is RuntimeError:
        result=supplement.execute_supplement(task['job_id'],progress=failed_progress)
        assert all(result[key]==value for key,value in published.items())
        assert result['retention_result']['status']=='warning'
    else:
        with pytest.raises(error_type):supplement.execute_supplement(task['job_id'],progress=failed_progress)


def test_migration_025_adds_only_two_empty_tables_and_preserves_all_original_objects(tmp_path,monkeypatch):
    from app.infrastructure import database
    folder=tmp_path/'migrations';folder.mkdir()
    original=database.MIGRATIONS_DIR
    for source in original.glob('[0-9]*.sql'):
        if int(source.name.split('_')[0])<=24:shutil.copy2(source,folder/source.name)
    monkeypatch.setattr(database,'MIGRATIONS_DIR',folder)
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('SHIXU_SQLITE_PATH',str(tmp_path/'data/business/app.sqlite3'))
    core.ensure_ready()
    core.set_setting('retention-migration-sentinel',{'kept':True,'date':DAY})
    tasks.create_task('increment',{'frozen':'legacy'},target_date=DAY)
    def snapshot():
        with core.connect() as connection:
            objects={(row['type'],row['name']):row['sql'] for row in connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
            tables={row['name']:Counter(tuple(record) for record in connection.execute('SELECT * FROM "'+row['name']+'"')) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            return objects,tables,connection.execute('PRAGMA user_version').fetchone()[0]
    objects,tables,version=snapshot();assert version==24 and len(tables)==52
    migration=original/'025_supplement_retention.sql';shutil.copy2(migration,folder/migration.name)
    core.ensure_ready();new_objects,new_tables,new_version=snapshot()
    assert new_version==25 and set(new_tables)-set(tables)=={'supplement_retention_run','supplement_retention_file'}
    assert not new_tables['supplement_retention_run'] and not new_tables['supplement_retention_file']
    for key,value in objects.items():assert new_objects[key]==value,key
    for table,values in tables.items():
        if table=='_schema_migration':
            added=new_tables[table]-values;assert sum(added.values())==1
            record=next(iter(added));assert record[0]==migration.name and record[1]==hashlib.sha256(migration.read_bytes()).hexdigest()
        else:assert new_tables[table]==values,table
    with core.connect() as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert connection.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_quote_date_pushdown_preserves_every_used_signature_and_excludes_future(tmp_path,monkeypatch):
    calendar=pd.bdate_range('2024-12-18','2026-01-08').strftime('%Y-%m-%d').tolist();target='2026-01-06'
    source=[{'instrument_id':symbol,'date':day,'close':100+i+(index/10)} for i,day in enumerate(calendar) for index,symbol in enumerate(('600001.SH','000001.SZ'))]
    artifact=tmp_path/'daily.parquet';pq.write_table(pa.Table.from_pylist(source),artifact,row_group_size=16)
    market=SimpleNamespace(status=lambda:{'snapshot_id':'fixture','data_end':target},calendar=lambda:list(calendar),
        instruments=lambda **kw:[{'kind':'stock','code':symbol[:6],'instrument_id':symbol} for symbol in ('600001.SH','000001.SZ')],parquet_input=lambda name:str(artifact))
    monkeypatch.setattr(supplement,'_root',lambda:tmp_path/'published/market-supplement')
    old_day='2024-12-20'
    monkeypatch.setattr(supplement,'load_supplement',lambda:{'public_pools':{old_day:{'date':old_day}},'fund_flows':{},'sector_index_history':{}})
    monkeypatch.setattr(supplement.SupplementCache,'reuse',lambda self,source,capability,day,signature,**kw:{'date':day,'pool_kind':capability,'complete':True,'normalization_version':supplement.NORMALIZATION_VERSION,'items':[]})
    monkeypatch.setattr(supplement,'PublicMarketClient',lambda:SimpleNamespace(limit_pool=lambda *a,**k:pytest.fail('network forbidden')))
    monkeypatch.setattr(supplement,'fetch_fuyao_pool',lambda *a,**k:pytest.fail('network forbidden'))
    monkeypatch.setattr(supplement,'fetch_history_for_market',lambda *a,**k:(_ for _ in ()).throw(ValueError('synthetic missing fund source')))
    monkeypatch.setattr(supplement,'_publish_document',lambda document,*a,**kw:document)
    from app.services import source_service
    monkeypatch.setattr(source_service,'client',lambda *a,**k:SimpleNamespace(key='fixture-only'))
    original_signature=supplement._pool_signature;signatures=[]
    def capture(day,source,closes,identity,pool_kind=None):
        signatures.append((day,dict(closes)));return original_signature(day,source,closes,identity,pool_kind)
    monkeypatch.setattr(supplement,'_pool_signature',capture)
    original_connect=duckdb.connect;queries=[]
    class Connection:
        def __init__(self):self.inner=original_connect(':memory:')
        def execute(self,sql,args=None):
            result=self.inner.execute(sql,args) if args is not None else self.inner.execute(sql)
            if sql.startswith('CREATE TABLE quotes AS'):
                queries.append((sql,list(args),self.inner.execute('SELECT * FROM quotes ORDER BY instrument_id,date').fetchall()))
            return result
        def close(self):self.inner.close()
    monkeypatch.setattr(supplement.duckdb,'connect',lambda *a,**k:Connection())
    result=supplement.refresh_for_market(market,target,recent_days=20)
    assert result['target_date']==target and len(queries)==1 and signatures
    sql,args,materialized=queries[0]
    assert 'WHERE date>=?' in sql and 'date<=?' in sql
    assert args[1]==old_day and args[2]==target
    expected=[(row['instrument_id'],row['date'],row['close']) for row in source if old_day<=row['date']<=target]
    assert materialized==sorted(expected,key=lambda row:(row[0],row[1]))
    for day,closes in signatures:
        assert closes=={row['instrument_id']:row['close'] for row in source if row['date']==day}
    assert old_day in {day for day,_ in signatures}
    assert all(day<=target for day,_ in signatures)


def test_real_supplement_loader_holds_shared_lock_through_dependency_expansion(tmp_path,monkeypatch):
    import threading
    from app.services import supplement_storage
    database=str(tmp_path/'business/app.sqlite3');monkeypatch.setenv('SHIXU_SQLITE_PATH',database)
    root=tmp_path/'published/market-supplement';index=root/'indexes/fixture.json'
    core.atomic_json(index,{'version':supplement.VERSION,'snapshot_id':'fixture','target_date':DAY,'public_pools':{}})
    core.atomic_json(root/'head.json',{'snapshot_id':'fixture','artifact':'indexes/fixture.json','sha256':core.hash_file(index)})
    monkeypatch.setattr(supplement,'_root',lambda:root)
    expanding=threading.Event();release_read=threading.Event();reader_done=threading.Event();errors=[]
    actual=supplement_storage.expand_document
    def slow_expand(*args,**kwargs):
        expanding.set();assert release_read.wait(8)
        return actual(*args,**kwargs)
    monkeypatch.setattr(supplement_storage,'expand_document',slow_expand)
    def read():
        try:
            result,identity=supplement.load_supplement_with_fingerprint('fixture')
            assert result['snapshot_id']=='fixture' and identity is not None
        except BaseException as error:errors.append(error)
        finally:reader_done.set()
    reader=threading.Thread(target=read);reader.start()
    context=multiprocessing.get_context('spawn');ready=context.Event();entered=context.Event();release_gc=context.Event();worker_errors=context.Queue()
    collector=context.Process(target=_lock_process,args=(database,True,ready,entered,release_gc,worker_errors))
    try:
        assert expanding.wait(15)
        collector.start();assert ready.wait(15)
        assert not entered.wait(.15),'GC must not pass the real loader while it is expanding references'
        release_read.set();assert reader_done.wait(5)
        assert entered.wait(5)
    finally:
        release_read.set();release_gc.set();reader.join(5)
        if collector.pid:
            collector.join(5)
            if collector.is_alive():collector.terminate();collector.join(2)
    assert not errors and collector.exitcode==0 and worker_errors.empty()
