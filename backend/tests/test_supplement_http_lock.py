"""Real HTTP/worker interleavings protect the last-check/unlink boundary."""
import asyncio
import fcntl
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

from app import core
from app.main import app
from app.services import supplement_access as access
from test_workspace import ws
from test_supplement_retention import collected


def _collector_process(configuration, days, document, mode, entered, release, outcome):
    os.environ.update(configuration)
    from app import core as child_core
    from app.services import supplement_retention as child_retention
    from app.services.task_service import TaskCancelled as Cancelled, TaskPaused as Paused
    market = SimpleNamespace(calendar=lambda: days, status=lambda: {'snapshot_id': 'market-current', 'data_end': days[-1]})
    touched = False
    original_unlink, original_node = Path.unlink, child_retention._node
    root = str(child_core.published_dir()/'market-supplement')
    def unlink(path, *args, **kwargs):
        nonlocal touched
        if not touched and str(path).startswith(root+'/'):
            touched = True
            # This is after unchanged_database's final read, immediately at
            # the actual irreversible operation, with the worker still in EX.
            entered.set()
            if not release.wait(10): raise RuntimeError('unlink test deadline')
        return original_unlink(path, *args, **kwargs)
    def node(*args):
        nonlocal touched
        if mode == 'scan' and not touched:
            touched = True; entered.set()
            if not release.wait(10): raise RuntimeError('scan test deadline')
        return original_node(*args)
    def guard():
        if mode == 'wait': entered.set()
        with child_core.connect() as connection:
            status = connection.execute("SELECT status FROM job WHERE job_id='fixture-job'").fetchone()[0]
        if status == 'paused': raise Paused()
        if status == 'cancelled': raise Cancelled()
    try:
        if mode == 'unlink': Path.unlink = unlink
        if mode == 'scan': child_retention._node = node
        result = child_retention.collect_after_publication('fixture-job', market, document, guard=guard if mode != 'unlink' else None)
        outcome.put(result)
    except (Paused, Cancelled) as error:
        outcome.put({'status': type(error).__name__})
    except BaseException as error:
        outcome.put({'error': repr(error)}); raise
    finally:
        Path.unlink, child_retention._node = original_unlink, original_node


def _configuration():
    return {'SHIXU_DATA_DIR': str(core.data_dir()), 'SHIXU_SQLITE_PATH': str(core.sqlite_path()),
            'SHIXU_PUBLISHED_DIR': str(core.published_dir()), 'SHIXU_RAW_DIR': str(core.raw_dir()), 'SHIXU_WORK_DIR': str(core.work_dir())}


def _stop(process, release):
    release.set()
    if process.pid:
        process.join(10)
        if process.is_alive(): process.terminate(); process.join(2)


def _exclusive_available():
    descriptor = access._open_descriptor()
    try:
        try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        return True
    finally:
        access._close_descriptor(descriptor)


def _exclusive_probe(database, ready, entered, release):
    os.environ['SHIXU_SQLITE_PATH'] = database
    from app.services.supplement_access import supplement_access
    ready.set()
    with supplement_access(exclusive=True):
        entered.set()
        if not release.wait(20): raise RuntimeError('exclusive probe deadline')


def test_actual_queued_route_cannot_commit_at_collectors_last_check_unlink_boundary(collected):
    root, days, details, market, document = collected
    context = multiprocessing.get_context('spawn')
    entered, release, outcome = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_collector_process, args=(_configuration(), days, document, 'unlink', entered, release, outcome))
    response, done = [], threading.Event()
    client = TestClient(app)
    def queue():
        try: response.append(client.post('/api/v1/watchlist/line-alerts/evaluate', json={'request_key': 'during-unlink'}))
        finally: done.set()
    writer = threading.Thread(target=queue)
    try:
        process.start(); assert entered.wait(8)
        writer.start()
        assert not done.wait(.2), 'the real business route must wait for collector EX'
        with core.connect() as connection:
            assert connection.execute("SELECT count(*) FROM job WHERE request_key='during-unlink'").fetchone()[0] == 0
        progress = client.get('/api/v1/tasks/fixture-job')
        assert progress.status_code == 200 and progress.json()['job_id'] == 'fixture-job'
        assert client.get('/health').status_code == 200
        release.set(); assert done.wait(8)
        result = outcome.get(timeout=8)
        assert result['status'] == 'completed' and result['removed_files'] > 0, result
        assert response[0].status_code == 200 and response[0].json()['status'] == 'queued', response[0].text
        with core.connect() as connection:
            assert connection.execute("SELECT count(*) FROM job WHERE request_key='during-unlink'").fetchone()[0] == 1
    finally:
        _stop(process, release)
        if writer.ident: writer.join(10)
    assert process.exitcode == 0 and not writer.is_alive()


def test_actual_route_commit_finishes_before_collector_reads_protection_roots(collected, monkeypatch):
    from app.services import line_alerts
    root, days, details, market, document = collected
    staging, release_write, done = threading.Event(), threading.Event(), threading.Event()
    actual = line_alerts.enqueue
    def staged(connection, *args, **kwargs):
        result = actual(connection, *args, **kwargs)
        staging.set(); assert release_write.wait(10)
        return result
    monkeypatch.setattr(line_alerts, 'enqueue', staged)
    before = {path.relative_to(root).as_posix() for path in root.rglob('*.json')}
    context = multiprocessing.get_context('spawn')
    entered, release_gc, outcome = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_collector_process, args=(_configuration(), days, document, 'wait', entered, release_gc, outcome))
    response = []; client = TestClient(app)
    def queue():
        try: response.append(client.post('/api/v1/watchlist/line-alerts/evaluate', json={'request_key':'before-protection-scan'}))
        finally: done.set()
    writer = threading.Thread(target=queue)
    try:
        writer.start(); assert staging.wait(8)
        process.start(); assert entered.wait(8)
        assert not done.is_set()
        with pytest.raises(Empty):
            outcome.get(timeout=.2)  # EX must wait while the real API transaction still owns SH.
        release_write.set(); assert done.wait(8)
        result = outcome.get(timeout=8)
        assert result['status'] == 'skipped' and '任务' in result['reason'], result
        assert response[0].status_code == 200 and response[0].json()['status'] == 'queued', response[0].text
    finally:
        release_write.set(); _stop(process, release_gc)
        if writer.ident: writer.join(10)
    assert process.exitcode == 0 and not writer.is_alive()
    assert before == {path.relative_to(root).as_posix() for path in root.rglob('*.json')}


@pytest.mark.parametrize('mode,action', [('scan', 'pause'), ('scan', 'cancel'), ('wait', 'pause')])
def test_stop_http_bypasses_gc_lock_and_guard_interrupts_scan_or_lock_wait(collected, mode, action):
    root, days, details, market, document = collected
    with core.connect() as connection: connection.execute("UPDATE job SET status='running' WHERE job_id='fixture-job'")
    before = {path.relative_to(root).as_posix() for path in root.rglob('*.json')}
    context = multiprocessing.get_context('spawn')
    entered, release, outcome = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_collector_process, args=(_configuration(), days, document, mode, entered, release, outcome))
    from contextlib import nullcontext
    client = TestClient(app)
    try:
        with access.supplement_access() if mode == 'wait' else nullcontext():
            process.start(); assert entered.wait(8)
            stopped = client.post('/api/v1/tasks/fixture-job/'+action)
            assert stopped.status_code == 200 and stopped.json()['status'] == ('paused' if action == 'pause' else 'cancelled'), stopped.text
            release.set()
            result = outcome.get(timeout=8)
            assert result['status'] == ('TaskPaused' if action == 'pause' else 'TaskCancelled'), result
    finally:
        _stop(process, release)
    assert process.exitcode == 0
    assert {path.relative_to(root).as_posix() for path in root.rglob('*.json')} == before
    with core.connect() as connection:
        assert connection.execute('SELECT count(*) FROM supplement_retention_run').fetchone()[0] == 0


def test_write_wait_does_not_block_async_health_and_cancel_closes_descriptor(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_SQLITE_PATH', str(tmp_path/'business/app.sqlite3'))
    opened, actual = [], access._open_descriptor
    def track_open():
        descriptor = actual(); opened.append(descriptor); return descriptor
    monkeypatch.setattr(access, '_open_descriptor', track_open)
    async def inner(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})
    wrapped = access.SupplementWriteMiddleware(inner)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url='http://fixture') as client:
            post = asyncio.create_task(client.post('/write'))
            for _ in range(100):
                if len(opened) == 2: break
                await asyncio.sleep(.01)
            assert len(opened) == 2
            assert (await asyncio.wait_for(client.get('/health'), .5)).status_code == 200
            assert not post.done()
            post.cancel()
            with pytest.raises(asyncio.CancelledError): await post
            with pytest.raises(OSError): os.fstat(opened[-1])
    with access.supplement_access(exclusive=True): asyncio.run(scenario())
    assert _exclusive_available()


def test_cancellation_during_descriptor_open_closes_late_result(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_SQLITE_PATH', str(tmp_path/'business/app.sqlite3'))
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    actual_open, actual_close = access._open_descriptor, access._close_descriptor
    def delayed_open():
        started.set(); assert release.wait(5); return actual_open()
    def close(descriptor):
        actual_close(descriptor); closed.set()
    monkeypatch.setattr(access, '_open_descriptor', delayed_open)
    monkeypatch.setattr(access, '_close_descriptor', close)
    async def scenario():
        async def acquire():
            async with access.supplement_write_access(): pytest.fail('cancelled open must not enter')
        request = asyncio.create_task(acquire())
        for _ in range(100):
            if started.is_set(): break
            await asyncio.sleep(.01)
        assert started.is_set()
        request.cancel()
        with pytest.raises(asyncio.CancelledError): await request
        release.set()
        for _ in range(100):
            if closed.is_set(): break
            await asyncio.sleep(.01)
        assert closed.is_set()
    asyncio.run(scenario())


@pytest.mark.parametrize('background', [False, True])
def test_cancelled_http_keeps_lock_until_synchronous_commit_and_background_finish(ws, background):
    started, release, committed = threading.Event(), threading.Event(), threading.Event()
    test_app = FastAPI(); test_app.add_middleware(access.SupplementWriteMiddleware)
    def commit():
        started.set(); assert release.wait(5)
        # ContextVars propagate to sync routes: nested readers borrow the
        # existing fd instead of waiting behind a queued EX on a new SH fd.
        assert access._request_locks.get(), 'sync route/background receives the request lease context'
        with access.supplement_access(): core.set_setting('cancelled-http-write', {'committed': True})
        committed.set(); return {'saved': True}
    @test_app.post('/write')
    def write(background_tasks: BackgroundTasks):
        if background:
            background_tasks.add_task(commit)
            return {'scheduled': True}
        return commit()
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url='http://fixture') as client:
            request = asyncio.create_task(client.post('/write'))
            for _ in range(100):
                if started.is_set(): break
                await asyncio.sleep(.01)
            assert started.is_set()
            request.cancel()
            with pytest.raises(asyncio.CancelledError): await request
            assert not _exclusive_available(), 'SH must outlive a cancelled waiter while its sync route can still commit'
            release.set()
            for _ in range(200):
                if committed.is_set() and not access._draining_writes: break
                await asyncio.sleep(.01)
            assert committed.is_set() and not access._draining_writes
            assert _exclusive_available()
    try: asyncio.run(scenario())
    finally: release.set()
    assert core.setting('cancelled-http-write') == {'committed': True}


@pytest.mark.parametrize('kind', ['sql', 'supplement_reader'])
def test_double_cancel_keeps_actual_sync_scope_locked_until_sql_close_or_read_end(ws, tmp_path, kind):
    staged, release_writer, finished = threading.Event(), threading.Event(), threading.Event()
    late_borrow_rejected = []
    source = tmp_path/'reader-source.json'; source.write_text('{"retained":true}')
    context = multiprocessing.get_context('spawn')
    ready, entered, release_probe = context.Event(), context.Event(), context.Event()
    probe = context.Process(target=_exclusive_probe, args=(str(core.sqlite_path()), ready, entered, release_probe))
    def writer():
        try:
            if kind == 'sql':
                with core.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    connection.execute("INSERT INTO platform_setting VALUES('double-cancel-commit',?,?)", ('{"committed":true}', core.now_ms()))
                    staged.set(); assert release_writer.wait(20)
                # The original Task may be done, but the real commit/close has
                # only just happened. A new connection must now fail closed.
                try:
                    with core.connect(): pytest.fail('closed HTTP lease cannot begin another connection')
                except ValueError as error: late_borrow_rejected.append(str(error))
            else:
                with access.supplement_access():
                    staged.set(); assert release_writer.wait(20)
                    assert source.read_text() == '{"retained":true}'
                try:
                    with access.supplement_access(): pytest.fail('closed HTTP lease cannot begin another reader')
                except ValueError as error: late_borrow_rejected.append(str(error))
        finally:
            finished.set()
    async def original(scope, receive, send):
        await run_in_threadpool(writer)
        await send({'type':'http.response.start', 'status':200, 'headers':[]})
        await send({'type':'http.response.body', 'body':b'ok'})
    async def scenario():
        wrapped = access.SupplementWriteMiddleware(original)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url='http://fixture') as client:
            outer = asyncio.create_task(client.post('/write'))
            for _ in range(200):
                if staged.is_set(): break
                await asyncio.sleep(.01)
            assert staged.is_set()
            outer.cancel()
            with pytest.raises(asyncio.CancelledError): await outer
            inner = next(iter(access._draining_writes))
            inner.cancel()  # Runner/server shutdown can cancel every remaining Task.
            for _ in range(100):
                if inner.done() and not access._draining_writes: break
                await asyncio.sleep(.01)
            assert inner.done() and not access._draining_writes
            assert not finished.is_set()
            probe.start()
            for _ in range(1500):
                if ready.is_set(): break
                await asyncio.sleep(.01)
            assert ready.is_set()
            await asyncio.sleep(.2)
            assert not entered.is_set(), 'independent GC EX cannot pass a live SQL commit or supplement reader after both Tasks are cancelled'
            if kind == 'sql': assert core.setting('double-cancel-commit') is None
            release_writer.set()
            for _ in range(500):
                if finished.is_set() and entered.is_set(): break
                await asyncio.sleep(.01)
            assert finished.is_set() and entered.is_set()
            assert late_borrow_rejected and 'HTTP请求已结束' in late_borrow_rejected[0]
    try:
        asyncio.run(scenario())
    finally:
        release_writer.set(); release_probe.set()
        if probe.pid:
            probe.join(15)
            if probe.is_alive(): probe.terminate(); probe.join(2)
    assert probe.exitcode == 0
    if kind == 'sql': assert core.setting('double-cancel-commit') == {'committed': True}


def test_closed_request_cannot_enter_ensure_ready_or_open_database(ws, monkeypatch):
    from app.infrastructure import database
    def forbidden(): pytest.fail('lease must be pinned/validated before ensure_ready can seed or migrate')
    monkeypatch.setattr(database, 'ensure_ready', forbidden)
    async def scenario():
        async with access.supplement_write_access() as lease:
            lease.close()
            with pytest.raises(ValueError, match='HTTP请求已结束'):
                with database.connect(): pass
    asyncio.run(scenario())


@pytest.mark.parametrize('method,path,protected', [
    ('POST', '/api/v1/tasks/one/resume', True), ('POST', '/api/v1/tasks/one/retry', True),
    ('PUT', '/api/v1/strategies/one', True), ('PATCH', '/api/v1/strategies/one', True), ('DELETE', '/api/v1/watchlist/600001.SH', True),
    ('GET', '/api/v1/watchlist', True), ('GET', '/api/v1/research/jobs/one', True),
    ('GET', '/api/v1/tasks/one', False), ('GET', '/api/v1/sync/session', False),
    ('POST', '/api/v1/tasks/one/pause', False), ('POST', '/api/v1/tasks/one/cancel', False),
    ('POST', '/api/v1/tasks/one/pause/extra', True), ('POST', '/api/v1/tasks/one/not-cancel', True),
])
def test_http_lock_classification_and_original_response(method, path, protected, tmp_path, monkeypatch):
    monkeypatch.setenv('SHIXU_SQLITE_PATH', str(tmp_path/'business/app.sqlite3'))
    async def original(scope, receive, send):
        assert bool(access._request_locks.get()) is protected
        await send({'type':'http.response.start', 'status':409, 'headers':[(b'content-type', b'application/json'), (b'x-original', b'kept')]})
        await send({'type':'http.response.body', 'body':b'{"detail":"original conflict"}'})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=access.SupplementWriteMiddleware(original)), base_url='http://fixture') as client:
            response = await client.request(method, path)
            assert response.status_code == 409 and response.headers['x-original'] == 'kept'
            assert response.json() == {'detail':'original conflict'}
    asyncio.run(scenario())
    assert _exclusive_available()


def test_real_app_errors_keep_response_and_published_readonly_volume_untouched(collected):
    published = core.published_dir(); published.chmod(0o555)
    try:
        client = TestClient(app)
        bad = client.post('/api/v1/watchlist/line-alerts/evaluate', json={'unexpected': True})
        assert bad.status_code == 422 and bad.json() == {'detail': '划线核验只接受请求标识'}
        queued = client.post('/api/v1/watchlist/line-alerts/evaluate', json={'request_key': 'readonly-published'})
        assert queued.status_code == 200 and queued.json()['status'] == 'queued'
        assert not (published/'supplement-access.lock').exists()
        assert (core.sqlite_path().parent/'supplement-access.lock').is_file()
    finally:
        published.chmod(0o755)
