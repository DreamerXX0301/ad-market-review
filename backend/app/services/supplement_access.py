"""Cross-process reader/collector lock on the shared business volume.

API mounts published data read-only, so the lock lives beside SQLite instead.
No SQLite transaction is held while reading files or waiting for the collector.
"""
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import asyncio
import os
import re
import threading
import time
from functools import wraps
from pathlib import Path

from app.core import sqlite_path
from app.infrastructure.file_operations import lock_fd, open_lock, unlock_fd

_held = threading.local()
_request_locks = ContextVar('supplement_request_locks', default={})
_draining_writes = set()


def _lock_path():
    return Path(sqlite_path()).parent / 'supplement-access.lock'


def _open_descriptor():
    path = _lock_path()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or path.is_symlink():
        raise ValueError('补充数据访问锁不能是符号链接')
    return open_lock(path)


def _close_descriptor(descriptor):
    try:
        unlock_fd(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def supplement_access(*, exclusive=False, guard=None):
    path = _lock_path()
    held = getattr(_held, 'paths', {})
    key = str(path.absolute())
    if key in held:
        if exclusive and not held[key]:
            raise ValueError('补充读锁不能隐式升级为清理写锁')
        yield
        return
    lease = _request_locks.get().get(key)
    if lease is not None:
        if exclusive:
            raise ValueError('补充读锁不能隐式升级为清理写锁')
        with lease.borrow():
            yield
        return
    descriptor = _open_descriptor()
    try:
        if guard:
            while True:
                guard()
                try:
                    lock_fd(descriptor, exclusive=exclusive, blocking=False)
                    break
                except BlockingIOError:
                    time.sleep(.05)
        else:
            lock_fd(descriptor, exclusive=exclusive)
        _held.paths = {**held, key: exclusive}
        try:
            yield
        finally:
            _held.paths = held
    finally:
        _close_descriptor(descriptor)


class _RequestLease:
    """Actual sync scopes, rather than asyncio Task.done, own the final unlock."""
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.deferred = False
        self.closing = False
        self.borrowers = 0
        self.mutex = threading.Lock()

    def _ready_to_close(self):
        # Caller owns mutex. Detach exactly once before OS close so a reused fd
        # can never be mistaken for this lease by a late/new thread borrower.
        if self.closing and self.borrowers == 0 and self.descriptor is not None:
            descriptor, self.descriptor = self.descriptor, None
            return descriptor
        return None

    def close(self):
        with self.mutex:
            self.closing = True
            descriptor = self._ready_to_close()
        if descriptor is not None:
            _close_descriptor(descriptor)

    @contextmanager
    def borrow(self):
        with self.mutex:
            if self.closing or self.descriptor is None:
                raise ValueError('HTTP请求已结束，禁止开始新的业务连接或补充读取')
            self.borrowers += 1
        try:
            yield
        finally:
            with self.mutex:
                self.borrowers -= 1
                descriptor = self._ready_to_close()
            if descriptor is not None:
                _close_descriptor(descriptor)

    def finish_with(self, task):
        # A synchronous route may still be committing in Starlette's thread
        # pool after its HTTP waiter is cancelled. Keep SH until that work and
        # its response/background lifecycle really finish, then close once.
        self.deferred = True
        _draining_writes.add(task)
        def complete(completed):
            try:
                if not completed.cancelled():
                    completed.exception()
            finally:
                _draining_writes.discard(completed)
                self.close()
        task.add_done_callback(complete)


@contextmanager
def supplement_request_access():
    """Pin an HTTP lease through the actual SQLite connection's close.

    Database.connect imports this lazily to avoid a core/access import cycle.
    Worker connections and heartbeat threads have no request lease and are
    unchanged. No transaction can begin after a doubly-cancelled lease closes.
    """
    lease = _request_locks.get().get(str(_lock_path().absolute()))
    if lease is None:
        yield
    else:
        with lease.borrow():
            yield


@asynccontextmanager
async def supplement_write_access():
    """Await SH without blocking the ASGI loop or borrowing thread-local state."""
    loop = asyncio.get_running_loop()
    pending = loop.run_in_executor(None, _open_descriptor)
    try:
        descriptor = await asyncio.shield(pending)
    except BaseException:
        # A cancelled await must not abandon an open that is still executing.
        def discard_open(completed):
            if not completed.cancelled() and completed.exception() is None:
                _close_descriptor(completed.result())
        pending.add_done_callback(discard_open)
        raise
    lease = _RequestLease(descriptor)
    token = None
    try:
        while True:
            try:
                lock_fd(descriptor, blocking=False)
                break
            except BlockingIOError:
                await asyncio.sleep(.02)
        key = str(_lock_path().absolute())
        token = _request_locks.set({**_request_locks.get(), key: lease})
        yield lease
    finally:
        if token is not None:
            _request_locks.reset(token)
        if not lease.deferred:
            lease.close()


class SupplementWriteMiddleware:
    """All HTTP business mutations are readers of the retention root graph.

    SH covers parsing, route/dependency work, commit, response and background
    completion. The sole worker's EX therefore cannot interleave unlink with a
    new queued task or an old-generation business reference. Two audited GET
    side effects also join: legacy watchlist baseline repair and research TTL/
    pin renewal. Task/progress GETs and the heartbeat remain independent.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        method, path = scope['method'], scope.get('path', '').rstrip('/')
        # These exact controls only stop an existing task; they cannot acquire
        # new inputs, pins or file references. They must reach the worker guard
        # while EX is held. Resume/retry/create retain the normal SH boundary.
        stopping_task = method == 'POST' and (re.fullmatch(r'/api/v1/tasks/[^/]+/(?:pause|cancel)', path) is not None or path == '/api/v1/sync/session/control')
        mutates_on_get = method in ('GET', 'HEAD') and (path == '/api/v1/watchlist' or path.startswith('/api/v1/research/jobs/'))
        if stopping_task or method not in ('POST', 'PUT', 'PATCH', 'DELETE') and not mutates_on_get:
            return await self.app(scope, receive, send)
        async with supplement_write_access() as lease:
            inner = asyncio.create_task(self.app(scope, receive, send))
            try:
                return await asyncio.shield(inner)
            except asyncio.CancelledError:
                lease.finish_with(inner)
                raise


def supplement_reader(function):
    """Keep a captured index alive until a route has consumed its lazy leaves."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with supplement_access():
            return function(*args, **kwargs)
    return wrapped
