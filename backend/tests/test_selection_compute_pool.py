"""Actual spawned processes, isolated pure jobs, no source/database/network I/O."""
from copy import deepcopy
from datetime import date, timedelta
import multiprocessing
import os
import pickle
import signal
import struct
import subprocess
import sys
import threading
import time

import pytest

from app.services import selection_compute_pool as pool
from app.services.selection_compute import prepare_job


def request(symbol='600001.SH'):
    from app.services.condition_basis_cache import key_for
    calendar=[];day=date(2025,1,1)
    while len(calendar)<80:
        if day.weekday()<5:calendar.append(day.isoformat())
        day+=timedelta(days=1)
    rows=[{'instrument_id':symbol,'date':day,'open':10+i/100,'high':11+i/100,
           'low':9+i/100,'close':10.5+i/100,'volume':10000+i,'amount':100000+i,
           'corporate_action':False,'corporate_action_coverage_known':True}
          for i,day in enumerate(calendar)]
    token={'verified_closure_sha256':'a'*64}
    return {'symbol':symbol,'day':calendar[-1],'calendar':calendar,'rows':rows,'actions':[],
            'basis_key':key_for(symbol,calendar[-1],('fixture','a'*64),token),'snapshot':'fixture',
            'history_first':None,'coverage':{'known':True,'source_token':token,'snapshot_id':'fixture','reason':None},
            'requests':{period:{'include_higher_lows':False,'boll_configs':()} for period in ('D','W','M')}}


def semantic(value):
    return {key:item for key,item in value.items() if key!='stages'}


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIXU_DATA_DIR',str(tmp_path/'never-created-source'))
    monkeypatch.delenv(pool.WORKER_BUDGET_ENV,raising=False)
    yield
    assert not (tmp_path/'never-created-source').exists()


def no_children(session):
    assert session.closed
    assert session.stats['remaining_pids']==[]
    assert not ({child.pid for child in multiprocessing.active_children()} & set(session.stats['worker_pids']))
    assert all(worker.thread is None or not worker.thread.is_alive() for worker in session._workers)


def _probe_guards(connection):
    guards=pool._install_child_guards()
    import duckdb
    import _duckdb
    import socket
    import sqlite3
    checks={}
    for name,action in [('sqlite',lambda:sqlite3.connect(':memory:')),
                        ('duckdb',lambda:duckdb.connect(':memory:')),
                        ('duckdb_extension',lambda:_duckdb.connect(':memory:')),
                        ('duckdb_default',lambda:duckdb.sql('select 1')),
                        ('socket',lambda:socket.create_connection(('127.0.0.1',1)))]:
        try:action()
        except PermissionError:checks[name]='blocked'
        else:checks[name]='unexpectedly allowed'
    connection.send((guards,checks));connection.close()


def _ignore_termination(connection):
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    connection.send('ready')
    while True:time.sleep(.1)


def _partial_ready(connection):
    os.write(connection.fileno(),struct.pack('!i',4096)+b'partial')
    while True:time.sleep(.1)


def _partial_result(connection):
    connection.send_bytes(pickle.dumps(('ready',os.getpid(),time.monotonic(),{})))
    connection.recv_bytes()
    os.write(connection.fileno(),struct.pack('!i',4096)+b'partial')
    while True:time.sleep(.1)


def _small_output_budget(connection):
    pool.MAX_MESSAGE_BYTES=12000
    pool._child_main(connection)


def _unserializable_output(connection):
    original=pickle.dumps
    def encode(value,*args,**kwargs):
        if isinstance(value,tuple) and value[0]=='result':raise TypeError('fixture')
        return original(value,*args,**kwargs)
    pickle.dumps=encode
    pool._child_main(connection)


def test_lazy_scope_decorator_and_empty_batch_start_no_processes():
    with pool.compute_session(max_workers=2) as session:
        assert pool.current_session() is session
        assert session.map(prepare_job,[])==[] and not session.stats['worker_pids']
        with pool.compute_session() as nested:assert nested is session
    no_children(session);assert pool.current_session() is None
    @pool.compute_session(max_workers=1)
    def decorated():return pool.current_session()
    assert decorated().closed
    for number in (-1,5,True):
        with pytest.raises(ValueError):
            with pool.compute_session(max_workers=number):pass


def test_actual_spawn_preserves_pure_results_order_and_reuses_workers(monkeypatch):
    monkeypatch.setenv('OPENBLAS_NUM_THREADS','7')
    jobs=[request(f'{600001+i:06}.SH') for i in range(8)]
    expected=[semantic(prepare_job(deepcopy(item))) for item in jobs]
    with pool.compute_session(max_workers=2) as session:
        assert [semantic(value) for value in session.map(prepare_job,jobs)]==expected
        pids=list(session.stats['worker_pids'])
        assert [semantic(value) for value in session.map(prepare_job,jobs[:2])]==expected[:2]
        assert session.stats['worker_pids']==pids and len(pids)==2
        assert session.stats['completed_jobs']==10 and session.stats['peak_workers']==2
        assert session.stats['request_bytes']>0 and session.stats['result_bytes']>0
        assert all(value>0 for value in session.stats['process_peak_rss_bytes'].values())
        assert set(session.stats['child_guards']['native_threads'].values())=={'1'}
        assert os.environ['OPENBLAS_NUM_THREADS']=='7'
    no_children(session)
    with pytest.raises(pool.PoolUnavailable):session.map(prepare_job,jobs)


def test_child_guards_block_all_database_and_network_entrypoints():
    context=multiprocessing.get_context('spawn');parent,child=context.Pipe()
    process=context.Process(target=_probe_guards,args=(child,))
    process.start();child.close()
    try:
        assert parent.poll(10)
        guards,checks=parent.recv()
        assert set(checks.values())=={'blocked'}
        assert set(guards['native_threads'].values())=={'1'}
        process.join(2);assert process.exitcode==0
    finally:
        if process.is_alive():process.kill();process.join(2)
        parent.close();process.close()


@pytest.mark.parametrize('control',['pause','cancel'])
def test_parent_control_exception_is_not_fallback_and_reaps_spawned_children(control):
    from app.services.task_service import TaskPaused,TaskCancelled
    error_type=TaskPaused if control=='pause' else TaskCancelled
    with pool.compute_session(max_workers=2) as session:
        def check():
            if session.stats['submitted_jobs']:raise error_type('controlled')
        session.check_cancel=check
        start=time.monotonic()
        with pytest.raises(error_type,match='controlled'):
            session.map(prepare_job,[request(),request('600002.SH')])
        assert time.monotonic()-start<10
        assert session.stats['shutdown_seconds']<2.1
    no_children(session)


def test_calculation_error_is_distinct_from_pool_failure_and_stops_whole_scope():
    with pool.compute_session(max_workers=1) as session:
        with pytest.raises(pool.CalculationFailed) as caught:
            session.map(prepare_job,[{}])
        assert caught.value.remote_type=='KeyError'
        assert not isinstance(caught.value,pool.PoolUnavailable)
        assert session.stats['completed_jobs']==0
    no_children(session)


def test_dead_worker_is_explicit_infrastructure_failure_without_automatic_restart():
    with pool.compute_session(max_workers=1) as session:
        session.map(prepare_job,[request()])
        process=session._workers[0].process
        process.kill();process.join(1)
        with pytest.raises(pool.PoolUnavailable):session.map(prepare_job,[request()])
        assert len(session.stats['worker_pids'])==1
    no_children(session)


@pytest.mark.skipif(os.name=='nt',reason='POSIX SIGTERM-ignore fixture')
def test_global_two_second_close_budget_kills_a_term_ignoring_child():
    context=multiprocessing.get_context('spawn');parent,child=context.Pipe()
    process=context.Process(target=_ignore_termination,args=(child,),daemon=True)
    process.start();child.close()
    assert parent.poll(5) and parent.recv()=='ready'
    session=pool.ComputeSession(max_workers=1)
    session._workers.append(pool._Worker(process,parent,time.monotonic()))
    session.stats['worker_pids'].append(process.pid)
    try:
        session.close()
        assert session.stats['shutdown_seconds']<2.1
        assert session.stats['killed_pids']==session.stats['worker_pids']
        no_children(session)
    finally:
        if not session.closed:session.close()
        try:
            if process.is_alive():process.kill();process.join(1)
        except ValueError:pass  # Successful close already released the handle.


def test_spawn_session_can_run_from_an_existing_worker_thread():
    results=[];errors=[];sessions=[]
    job=request()
    def work():
        try:
            with pool.compute_session(max_workers=1) as session:
                sessions.append(session)
                results.extend(session.map(prepare_job,[job]))
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=work);thread.start();thread.join(10)
    assert not thread.is_alive() and not errors
    assert results[0]['identity']==(job['symbol'],job['day'],job['basis_key'])
    no_children(sessions[0])


def test_unapproved_function_and_oversized_batch_refuse_before_spawning():
    with pool.compute_session() as session:
        with pytest.raises(ValueError):session.map(sum,[[1,2]])
        with pytest.raises(ValueError):session.map(prepare_job,[{}]*33)
        assert session.stats['worker_pids']==[]


@pytest.mark.parametrize('kind',['input','batch_output','single_output','input_encoding','output_encoding'])
def test_transport_budgets_and_encoding_allow_serial_fallback(monkeypatch,kind):
    job=request()
    if kind=='input':monkeypatch.setattr(pool,'MAX_MESSAGE_BYTES',1000)
    elif kind=='batch_output':monkeypatch.setattr(pool,'MAX_BATCH_RESULT_BYTES',100)
    elif kind=='single_output':monkeypatch.setattr(pool,'_child_main',_small_output_budget)
    elif kind=='input_encoding':job['ignored_serial_only_value']=lambda:None
    elif kind=='output_encoding':monkeypatch.setattr(pool,'_child_main',_unserializable_output)
    # Every fixture remains a valid pure calculation in the existing path.
    assert prepare_job(job)['identity'][0]==job['symbol']
    with pool.compute_session(max_workers=1) as session:
        with pytest.raises(pool.PoolUnavailable):session.map(prepare_job,[job])
    no_children(session)


@pytest.mark.skipif(os.name=='nt',reason='POSIX partial pipe frame fixture')
@pytest.mark.parametrize('target',[_partial_ready,_partial_result])
def test_partial_pipe_frames_do_not_block_parent_cancellation(monkeypatch,target):
    from app.services.task_service import TaskCancelled
    monkeypatch.setattr(pool,'_child_main',target)
    with pool.compute_session(max_workers=1) as session:
        waiting_since=None
        def cancel():
            nonlocal waiting_since
            ready=session.stats['submitted_jobs'] if target is _partial_result else session._workers
            if ready:
                if waiting_since is None:waiting_since=time.monotonic()
                if time.monotonic()-waiting_since>.2:raise TaskCancelled('partial-frame cancel')
        session.check_cancel=cancel
        with pytest.raises(TaskCancelled,match='partial-frame cancel'):
            session.map(prepare_job,[request()])
        assert time.monotonic()-waiting_since<2.3
    no_children(session)


def test_spawn_start_failure_restores_environment_and_reaps_scope(monkeypatch):
    monkeypatch.setenv('OPENBLAS_NUM_THREADS','7')
    def fail_start(_process):raise OSError('fixture')
    monkeypatch.setattr(multiprocessing.context.SpawnProcess,'start',fail_start)
    with pool.compute_session(max_workers=1) as session:
        with pytest.raises(pool.PoolUnavailable):session.map(prepare_job,[request()])
        assert os.environ['OPENBLAS_NUM_THREADS']=='7'
    no_children(session)


def test_nested_guard_is_restored():
    calls=[]
    outer=lambda:calls.append('outer')
    with pool.compute_session(check_cancel=outer) as session:
        with pool.compute_session(check_cancel=lambda:calls.append('inner')):
            session.map(prepare_job,[])
        assert session.check_cancel is outer
        session.map(prepare_job,[])
    assert calls==['outer','inner','outer']


def test_native_thread_limits_exist_before_spawn_main_is_imported(tmp_path):
    script=tmp_path/'spawn_main_probe.py'
    script.write_text('''import os, pickle, time
imported_threads=os.environ.get("OPENBLAS_NUM_THREADS")
from app.services import selection_compute_pool as pool
def child(connection):
    connection.send_bytes(pickle.dumps(("ready",os.getpid(),time.monotonic(),{"at_import":imported_threads})))
    connection.recv_bytes()
if __name__ == "__main__":
    os.environ["OPENBLAS_NUM_THREADS"]="7"
    pool._child_main=child
    with pool.compute_session(max_workers=1) as session:
        session._start(1,None)
        assert session.stats["child_guards"]["at_import"]=="1"
        assert os.environ["OPENBLAS_NUM_THREADS"]=="7"
    assert session.stats["remaining_pids"]==[]
''',encoding='utf-8')
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(sys.path))
    result=subprocess.run([sys.executable,str(script)],env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('configured',[None,'0','1','2','3','4',' 2 '])
def test_operator_budget_is_bounded_and_lazy(monkeypatch,configured):
    if configured is not None:monkeypatch.setenv(pool.WORKER_BUDGET_ENV,configured)
    expected=4 if configured is None else int(configured)
    with pool.compute_session() as session:
        assert session.max_workers==expected
        assert session.stats['configured_worker_limit']==expected
        assert not session.stats['invalid_worker_configuration']
        assert session.map(prepare_job,[])==[] and session.stats['worker_pids']==[]
    no_children(session)


@pytest.mark.parametrize('configured',['','-1','5','1.0','true','02','unexpected-secret-value'])
def test_invalid_operator_budget_uses_serial_and_never_logs_the_value(monkeypatch,caplog,configured):
    monkeypatch.setenv(pool.WORKER_BUDGET_ENV,configured)
    with pool.compute_session() as session:
        assert session.max_workers==0 and session.stats['invalid_worker_configuration']
        assert session.stats['worker_pids']==[]
    assert len(caplog.records)==1
    assert pool.WORKER_BUDGET_ENV in caplog.records[0].getMessage()
    assert caplog.records[0].args==(pool.WORKER_BUDGET_ENV,)
    no_children(session)


def test_zero_budget_keeps_control_scope_but_never_spawns(monkeypatch):
    from app.services.task_service import TaskCancelled
    monkeypatch.setenv(pool.WORKER_BUDGET_ENV,'0')
    with pool.compute_session(max_workers=4) as session:
        assert pool.current_session() is session and session.max_workers==0
        with pytest.raises(pool.PoolUnavailable):session.map(prepare_job,[request()])
        def cancelled():raise TaskCancelled('disabled scope still checks control')
        session.check_cancel=cancelled
        with pytest.raises(TaskCancelled):session.map(prepare_job,[request()])
        assert session.stats['worker_pids']==[]
    no_children(session)


def test_outer_scope_freezes_budget_and_explicit_limit_cannot_expand_it(monkeypatch):
    monkeypatch.setenv(pool.WORKER_BUDGET_ENV,'2')
    with pool.compute_session(max_workers=4) as outer:
        assert outer.max_workers==2
        monkeypatch.setenv(pool.WORKER_BUDGET_ENV,'0')
        with pool.compute_session(max_workers=1) as nested:
            assert nested is outer and nested.max_workers==2
        assert outer.max_workers==2
    with pool.compute_session() as following:assert following.max_workers==0
    monkeypatch.setenv(pool.WORKER_BUDGET_ENV,'4')
    with pool.compute_session(max_workers=1) as smaller:assert smaller.max_workers==1
    with pool.compute_session(max_workers=0) as disabled:assert disabled.max_workers==0


def test_environment_one_uses_one_actual_child_and_keeps_exact_results(monkeypatch):
    monkeypatch.setenv(pool.WORKER_BUDGET_ENV,'1')
    jobs=[request('600001.SH'),request('600002.SH')]
    expected=[semantic(prepare_job(deepcopy(job))) for job in jobs]
    with pool.compute_session(max_workers=4) as session:
        assert [semantic(value) for value in session.map(prepare_job,jobs)]==expected
        assert session.stats['peak_workers']==1 and len(session.stats['worker_pids'])==1
    no_children(session)
