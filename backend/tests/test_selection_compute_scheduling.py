"""Any completed peer must be served without waiting for a slow queue head."""
import multiprocessing
import os
import pickle
import threading
import time

import pytest

from app.services import selection_compute_pool as pool
from app.services.selection_compute import prepare_job


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(pool.WORKER_BUDGET_ENV, raising=False)
    monkeypatch.setenv('SHIXU_DATA_DIR', str(tmp_path / 'never-created-source'))
    yield
    assert not (tmp_path / 'never-created-source').exists()


class _NoHeadWait(threading.Event):
    def wait(self, timeout=None):
        pytest.fail('parent must wait for any completion, never one queue head')


class _InertProcess:
    def __init__(self, pid): self.pid = pid; self.alive = True
    def is_alive(self): return self.alive
    def terminate(self): self.alive = False
    kill = terminate
    def join(self, timeout=None): pass
    def close(self): pass


class _InertConnection:
    def close(self): pass


class _ControlledWorker:
    def __init__(self, session, number, assigned=None):
        self.process = _InertProcess(900000 + number)
        self.connection = _InertConnection(); self.thread = None
        self.done = _NoHeadWait(); self.failure = None; self.reply = None
        self.transfer = (0.0, 32); self.index = None
        self.session = session; self.assigned = assigned; self.requests = []; self.request = None

    def finish(self):
        self.reply = ('result', self.request['index'], 0.0, 0)
        self.done.set(); self.session._completed.set()

    def exchange(self, payload):
        self.done.clear(); self.reply = None
        self.request = pickle.loads(payload); self.requests.append(self.request['index'])
        if self.assigned is None: self.finish()
        else: self.assigned(self)


def _install_workers(monkeypatch, session, workers):
    session._workers = workers
    session.stats['worker_pids'] = [worker.process.pid for worker in workers]
    def started(count, callback):
        assert count == len(workers)
        session._check(callback)
    monkeypatch.setattr(session, '_start', started)


def test_immediate_results_do_not_let_first_worker_starve_completed_peers(monkeypatch):
    with pool.compute_session(max_workers=4) as session:
        workers = [_ControlledWorker(session, number) for number in range(4)]
        _install_workers(monkeypatch, session, workers)
        result = session.map(prepare_job, [{'index': number} for number in range(32)])
        assert result == list(range(32))
        # The former fixed-order scan assigns 29/1/1/1 here, even though every
        # peer has a completed result ready for consumption throughout.
        assert [len(worker.requests) for worker in workers] == [8, 8, 8, 8]
        assert set(session.stats['worker_submitted_jobs'].values()) == {8}
        assert session.stats['worker_completed_jobs'] == session.stats['worker_submitted_jobs']
    assert session.stats['remaining_pids'] == []


class _RacingCompletion(threading.Event):
    def __init__(self, phase, complete):
        super().__init__(); self.phase = phase; self.complete = complete; self.fired = False

    def _fire(self, phase):
        if not self.fired and self.phase == phase:
            self.fired = True; self.complete()

    def clear(self):
        self._fire('before_clear')
        super().clear()
        self._fire('after_clear')

    def wait(self, timeout=None):
        self._fire('after_scan')
        assert self.is_set(), 'a peer completion was lost between clear, scan and wait'
        assert timeout == pool._POLL_SECONDS
        return super().wait(0)


@pytest.mark.parametrize('phase', ['before_clear', 'after_clear', 'after_scan'])
def test_completion_races_never_lose_a_ready_peer_or_wait_for_slow_head(monkeypatch, phase):
    with pool.compute_session(max_workers=2) as session:
        def assigned(worker):
            if worker.request['index'] == 2:
                # The fast peer must receive another job before the first
                # worker is released. No elapsed-time assertion is required.
                assert worker is workers[1] and not workers[0].done.is_set()
                worker.finish(); workers[0].finish()
        workers = [_ControlledWorker(session, number, assigned) for number in range(2)]
        session._completed = _RacingCompletion(phase, workers[1].finish)
        _install_workers(monkeypatch, session, workers)
        assert session.map(prepare_job, [{'index': number} for number in range(3)]) == [0, 1, 2]
        assert workers[0].requests == [0] and workers[1].requests == [1, 2]
    assert session.stats['remaining_pids'] == []


def _heterogeneous_child(connection):
    """Fixture protocol only: sleeps model differing pure CPU completion times."""
    try:
        connection.send_bytes(pickle.dumps(('ready', os.getpid(), time.monotonic(), {})))
        while True:
            request = pickle.loads(connection.recv_bytes())
            if request is None: return
            started = time.monotonic(); time.sleep(request['delay']); finished = time.monotonic()
            result = {'index': request['index'], 'pid': os.getpid(), 'started': started, 'finished': finished}
            connection.send_bytes(pickle.dumps(('result', result, finished - started, None)))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


def test_actual_spawn_keeps_fast_peers_busy_while_first_job_is_slow(monkeypatch):
    monkeypatch.setattr(pool, '_child_main', _heterogeneous_child)
    requests = [{'index': number, 'delay': .35 if number == 0 else .005} for number in range(32)]
    with pool.compute_session(max_workers=4) as session:
        results = session.map(prepare_job, requests)
        assert [value['index'] for value in results] == list(range(32))
        fast_completed_first = sum(value['finished'] < results[0]['finished'] for value in results[1:])
        assert fast_completed_first >= 20
        assert session.stats['worker_completed_jobs'][results[0]['pid']] == 1
        assert len(session.stats['worker_completed_jobs']) == 4
    assert session.closed and session.stats['remaining_pids'] == []
    assert not ({child.pid for child in multiprocessing.active_children()} & set(session.stats['worker_pids']))
    assert all(not worker.thread.is_alive() for worker in session._workers)
