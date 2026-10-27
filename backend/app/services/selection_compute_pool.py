"""Task-scoped, bounded spawned CPU preparation; children never own sources.

Only selection_compute.prepare_job is admitted. The parent owns all I/O and
rule evaluation. Dedicated daemon IPC threads keep partial pipe frames from
blocking cancellation; child processes are terminated before those pipes close.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import importlib
import multiprocessing
import os
import pickle
import sys
import threading
import time

MAX_WORKERS = 4
MAX_JOBS = 32
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_BATCH_RESULT_BYTES = 64 * 1024 * 1024
WORKER_BUDGET_ENV = 'SHIXU_SELECTION_CPU_WORKERS'
_POLL_SECONDS = .1
_STARTUP_SECONDS = 30
_FUNCTION = ('app.services.selection_compute', 'prepare_job')
_NATIVE_THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                   'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS')
_CURRENT = ContextVar('selection_compute_session', default=None)
_SPAWN_ENV_LOCK = threading.RLock()


class PoolUnavailable(RuntimeError):
    """Infrastructure failed; the parent may choose its existing serial path."""


class CalculationFailed(RuntimeError):
    """A pure calculation failed; callers must not disguise it as a cache miss."""
    def __init__(self, remote_type, index=None):
        self.remote_type, self.index = remote_type, index
        super().__init__('并行技术计算失败（'+remote_type+'）')


def _validate_worker_limit(value):
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_WORKERS):
        raise ValueError('纯计算进程数必须为0至4')


def _worker_budget():
    """Read only when an outer task scope starts; never adapt a running task."""
    raw = os.environ.get(WORKER_BUDGET_ENV)
    if raw is None:
        return MAX_WORKERS, False
    value = raw.strip()
    if value in ('0', '1', '2', '3', '4'):
        return int(value), False
    import logging
    # An operator may have pasted sensitive text into the wrong variable.
    # Report the configuration name, never its rejected value.
    logging.getLogger(__name__).warning('%s配置无效，应为0至4整数；本次选股改用串行计算', WORKER_BUDGET_ENV)
    return 0, True


def _forbidden_io(*_args, **_kwargs):
    raise PermissionError('纯计算子进程禁止数据库与网络访问')


def _install_child_guards():
    # Set again before imports below; start() also sets the inherited environment
    # before spawn imports __main__, so native libraries cannot initialize early
    # with the parent's thread count.
    for name in _NATIVE_THREADS:
        os.environ[name] = '1'
    import builtins
    import socket
    import sqlite3
    sqlite3.connect = _forbidden_io
    socket.socket.connect = _forbidden_io
    socket.socket.connect_ex = _forbidden_io
    socket.create_connection = _forbidden_io
    original_import = builtins.__import__
    def block_duckdb():
        for name in ('duckdb', '_duckdb'):
            duckdb = sys.modules.get(name)
            if duckdb is None:
                continue
            # Default-connection shortcuts must not bypass connect().
            for entry in ('connect', 'default_connection', 'cursor', 'execute', 'sql', 'query',
                          'read_csv', 'read_json', 'read_parquet', 'from_df', 'from_arrow'):
                if hasattr(duckdb, entry):
                    setattr(duckdb, entry, _forbidden_io)
    def guarded_import(name, *args, **kwargs):
        module = original_import(name, *args, **kwargs)
        block_duckdb()
        return module
    builtins.__import__ = guarded_import
    def audit(event, _args):
        if event in ('sqlite3.connect', 'socket.connect'):
            _forbidden_io()
    sys.addaudithook(audit)
    # Import itself opens no database. Patch both public and extension aliases
    # before the admitted function imports any application module.
    import duckdb
    block_duckdb()
    return {'sqlite_connect': 'blocked', 'duckdb_connect': 'blocked',
            'network_connect': 'blocked', 'native_threads': {name: os.environ[name] for name in _NATIVE_THREADS}}


def _rss():
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if sys.platform == 'darwin' else value * 1024
    except (ImportError, AttributeError):
        return None


def _child_main(connection):
    try:
        guards = _install_child_guards()
        function = getattr(importlib.import_module(_FUNCTION[0]), _FUNCTION[1])
        connection.send_bytes(pickle.dumps(('ready', os.getpid(), time.monotonic(), guards), protocol=5))
        while True:
            raw = connection.recv_bytes(MAX_MESSAGE_BYTES)
            request = pickle.loads(raw)
            if request is None:
                return
            started = time.perf_counter()
            try:
                result = function(request)
            except Exception as error:
                # Do not serialize arbitrary exception objects or source values.
                encoded = pickle.dumps(('calculation_error', type(error).__name__[:80],
                                        time.perf_counter()-started, _rss()), protocol=5)
            else:
                message = ('result', result, time.perf_counter()-started, _rss())
                try:
                    encoded = pickle.dumps(message, protocol=5)
                except Exception as error:
                    encoded = pickle.dumps(('transport_error', type(error).__name__[:80],
                                            time.perf_counter()-started, _rss()), protocol=5)
                if len(encoded) > MAX_MESSAGE_BYTES:
                    encoded = pickle.dumps(('transport_error', 'OutputBudgetExceeded',
                                            time.perf_counter()-started, _rss()), protocol=5)
            connection.send_bytes(encoded)
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class _Worker:
    def __init__(self, process, connection, started, completion=None):
        self.process, self.connection, self.started = process, connection, started
        self.completion = completion
        self.thread = None
        self.done = threading.Event()
        self.reply = None
        self.failure = None
        self.transfer = None
        self.index = None

    def exchange(self, payload=None, *, ready=False):
        self.done.clear(); self.reply = self.failure = None
        def transfer():
            started = time.perf_counter()
            try:
                if not ready:
                    self.connection.send_bytes(payload)
                encoded = self.connection.recv_bytes(MAX_MESSAGE_BYTES)
                self.reply = pickle.loads(encoded)
                self.transfer = (time.perf_counter()-started, len(encoded))
            except Exception as error:
                self.failure = type(error).__name__
            finally:
                self.done.set()
                if self.completion is not None:
                    self.completion.set()
        self.thread = threading.Thread(target=transfer, name='selection-cpu-ipc', daemon=True)
        try:
            self.thread.start()
        except Exception as error:
            self.thread = None
            raise PoolUnavailable('纯计算传输未能启动（'+type(error).__name__+'）') from None


class ComputeSession:
    def __init__(self, check_cancel=None, max_workers=None):
        _validate_worker_limit(max_workers)
        configured, invalid = _worker_budget()
        self.check_cancel = check_cancel
        self.max_workers = configured if max_workers is None else min(configured, max_workers)
        self.owner_pid = os.getpid()
        self.closed = False
        self._workers = []
        self._map_lock = threading.Lock()
        self._completed = threading.Event()
        self.stats = {'worker_pids': [], 'peak_workers': 0, 'submitted_jobs': 0, 'completed_jobs': 0,
                      'configured_worker_limit': configured, 'max_workers': self.max_workers,
                      'invalid_worker_configuration': invalid,
                      'worker_submitted_jobs': {}, 'worker_completed_jobs': {},
                      'request_bytes': 0, 'result_bytes': 0, 'startup_seconds': {},
                      'process_peak_rss_bytes': {}, 'compute_seconds': 0.0,
                      'exchange_seconds': 0.0, 'serialization_seconds': 0.0,
                      'terminated_pids': [], 'killed_pids': [], 'remaining_pids': []}

    def _check(self, callback):
        if self.closed or self.owner_pid != os.getpid():
            raise PoolUnavailable('纯计算范围已结束或进程归属不符')
        if callback:
            callback()

    def _start(self, count, callback):
        context = multiprocessing.get_context('spawn')
        while len(self._workers) < count:
            self._check(callback)
            parent = child = None
            try:
                parent, child = context.Pipe(duplex=True)
                process = context.Process(target=_child_main, args=(child,), name='selection-cpu', daemon=True)
            except Exception as error:
                for connection in (parent, child):
                    if connection is not None: connection.close()
                raise PoolUnavailable('纯计算通道未能创建（'+type(error).__name__+'）') from None
            worker = _Worker(process, parent, time.monotonic(), self._completed)
            self._workers.append(worker)
            try:
                with _SPAWN_ENV_LOCK:
                    previous = {name: os.environ.get(name) for name in _NATIVE_THREADS}
                    try:
                        os.environ.update({name: '1' for name in _NATIVE_THREADS})
                        process.start()
                    finally:
                        for name, value in previous.items():
                            if value is None: os.environ.pop(name, None)
                            else: os.environ[name] = value
            except Exception as error:
                raise PoolUnavailable('纯计算子进程未能启动（'+type(error).__name__+'）') from None
            finally:
                child.close()
            self.stats['worker_pids'].append(process.pid)
            self.stats['peak_workers'] = max(self.stats['peak_workers'], len(self._workers))
            worker.exchange(ready=True)
        for worker in self._workers:
            if worker.process.pid in self.stats['startup_seconds']:
                continue
            while not worker.done.wait(_POLL_SECONDS):
                self._check(callback)
                if not worker.process.is_alive() or time.monotonic()-worker.started > _STARTUP_SECONDS:
                    raise PoolUnavailable('纯计算子进程启动中断或超时')
            self._check(callback)
            if worker.failure or not isinstance(worker.reply, tuple) or len(worker.reply) != 4 or worker.reply[0] != 'ready' or worker.reply[1] != worker.process.pid:
                raise PoolUnavailable('纯计算子进程启动回执无效')
            self.stats['startup_seconds'][worker.process.pid] = worker.reply[2]-worker.started
            self.stats['child_guards'] = worker.reply[3]

    def map(self, function, requests, check_cancel=None):
        if (getattr(function, '__module__', None), getattr(function, '__name__', None)) != _FUNCTION:
            raise ValueError('纯计算池只接受固定的选股准备函数')
        if function is not getattr(importlib.import_module(_FUNCTION[0]), _FUNCTION[1]):
            raise ValueError('纯计算函数身份不一致')
        if not isinstance(requests, (list, tuple)) or len(requests) > MAX_JOBS:
            raise ValueError('单批纯计算请求不得超过32项')
        callback = self.check_cancel if check_cancel is None else check_cancel
        self._check(callback)
        if not requests:
            return []
        if self.max_workers == 0:
            raise PoolUnavailable('纯计算子进程已关闭，使用原有串行准备路径')
        if not self._map_lock.acquire(blocking=False):
            raise PoolUnavailable('同一纯计算范围不接受并发调用')
        try:
            self._start(min(self.max_workers, len(requests)), callback)
            results = [None] * len(requests)
            pending = iter(enumerate(requests)); active = []; byte_count = 0
            def assign(worker):
                self._check(callback)
                try: index, request = next(pending)
                except StopIteration: return False
                started = time.perf_counter()
                try: payload = pickle.dumps(request, protocol=5)
                except Exception as error:
                    raise PoolUnavailable('纯计算输入无法传输（'+type(error).__name__+'）') from None
                self.stats['serialization_seconds'] += time.perf_counter()-started
                if len(payload) > MAX_MESSAGE_BYTES:
                    raise PoolUnavailable('纯计算单项输入超过传输预算')
                worker.index = index
                worker.exchange(payload)
                self.stats['submitted_jobs'] += 1
                submitted = self.stats['worker_submitted_jobs']
                submitted[worker.process.pid] = submitted.get(worker.process.pid, 0)+1
                self.stats['request_bytes'] += len(payload)
                return True
            for worker in self._workers:
                if assign(worker): active.append(worker)
            while active:
                self._check(callback)
                # Clear before inspecting persistent per-worker flags. A
                # completion racing with clear is either seen by this scan or
                # leaves the shared event set for the following wait.
                self._completed.clear()
                ready = next((worker for worker in active if worker.done.is_set()), None)
                if ready is None:
                    if any(not worker.process.is_alive() for worker in active):
                        raise PoolUnavailable('纯计算子进程意外退出')
                    self._completed.wait(_POLL_SECONDS)
                    continue
                if ready.failure:
                    raise PoolUnavailable('纯计算传输中断（'+ready.failure+'）')
                message = ready.reply
                if not isinstance(message, tuple) or len(message) != 4 or message[0] not in ('result', 'calculation_error', 'transport_error'):
                    raise PoolUnavailable('纯计算结果协议无效')
                self.stats['exchange_seconds'] += ready.transfer[0]
                self.stats['result_bytes'] += ready.transfer[1]
                byte_count += ready.transfer[1]
                if byte_count > MAX_BATCH_RESULT_BYTES:
                    raise PoolUnavailable('纯计算批次结果超过传输预算')
                self.stats['compute_seconds'] += message[2]
                self.stats['process_peak_rss_bytes'][ready.process.pid] = message[3]
                if message[0] == 'calculation_error':
                    raise CalculationFailed(message[1], ready.index)
                if message[0] == 'transport_error':
                    raise PoolUnavailable('纯计算结果无法传输（'+message[1]+'）')
                results[ready.index] = message[1]
                self.stats['completed_jobs'] += 1
                completed = self.stats['worker_completed_jobs']
                completed[ready.process.pid] = completed.get(ready.process.pid, 0)+1
                # A very short job may finish before the parent revisits the
                # queue. Rotate it so already finished peers are not starved.
                active.remove(ready)
                if assign(ready): active.append(ready)
            self._check(callback)
            return results
        except BaseException:
            self.close()
            raise
        finally:
            self._map_lock.release()

    def close(self):
        if self.closed:
            return
        self.closed = True
        started = time.monotonic(); soft_deadline = started+1; deadline = started+2
        def cleanup(action):
            try:
                return action()
            except Exception as error:
                # A cleanup syscall race must not replace TaskPaused/Cancelled
                # or the calculation failure that brought us here.
                self.stats.setdefault('cleanup_errors', []).append(type(error).__name__)
                return None
        for worker in self._workers:
            if cleanup(worker.process.is_alive):
                cleanup(worker.process.terminate)
                self.stats['terminated_pids'].append(worker.process.pid)
        for worker in self._workers:
            if worker.process.pid is not None:
                cleanup(lambda: worker.process.join(max(0, soft_deadline-time.monotonic())))
        for worker in self._workers:
            if cleanup(worker.process.is_alive):
                cleanup(worker.process.kill)
                self.stats['killed_pids'].append(worker.process.pid)
        for worker in self._workers:
            if worker.process.pid is not None:
                cleanup(lambda: worker.process.join(max(0, deadline-time.monotonic())))
            cleanup(worker.connection.close)
            if worker.thread is not None:
                cleanup(lambda: worker.thread.join(max(0, deadline-time.monotonic())))
            if cleanup(worker.process.is_alive):
                self.stats['remaining_pids'].append(worker.process.pid)
            else:
                cleanup(worker.process.close)
        self.stats['shutdown_seconds'] = time.monotonic()-started


def current_session():
    session = _CURRENT.get()
    return session if session is not None and not session.closed and session.owner_pid == os.getpid() else None


@contextmanager
def compute_session(check_cancel=None, max_workers=None):
    _validate_worker_limit(max_workers)
    existing = current_session()
    if existing is not None:
        previous = existing.check_cancel
        if check_cancel is not None:
            def both():
                if previous: previous()
                check_cancel()
            existing.check_cancel = both
        try:
            yield existing
        finally:
            existing.check_cancel = previous
        return
    session = ComputeSession(check_cancel, max_workers)
    token = _CURRENT.set(session)
    try:
        yield session
    finally:
        try:
            session.close()
        finally:
            _CURRENT.reset(token)
