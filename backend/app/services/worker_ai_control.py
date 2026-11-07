"""AI control lane inside the single locked worker process.

The control thread owns AI queue transactions; its one HTTP thread receives only
frozen requests. Neither thread opens the analytics writer or changes market heads.
"""
import logging
import threading
from app.core import connect
from app.services.ai_runtime import AIRuntime

logger = logging.getLogger('shixu.worker')


def may_start_ai():
    with connect() as connection:
        if connection.execute("SELECT 1 FROM job WHERE status IN ('queued','running') LIMIT 1").fetchone():
            return False
        from app.services.sync_progress import lock_record
        return lock_record(connection) is None


class WorkerAIControl:
    def __init__(self, owner, *, runtime=None, admission=None, interval=.5, allow_start=None):
        self.owner = owner
        self.runtime = runtime or AIRuntime()
        self.admission = admission or may_start_ai
        self.allow_start = allow_start
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        # The caller must already hold the existing exclusive worker.lock.
        self.runtime.recover_interrupted()
        self.thread = threading.Thread(target=self._run, name='worker-ai-control', daemon=True)
        self.thread.start()
        return self

    def _may_start(self):
        return not self.stop_event.is_set() and (self.allow_start is None or self.allow_start())

    def _run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    # Always drain/observe cancellation even when new work has
                    # to wait behind a long main-market dispatch.
                    if self.allow_start is None:
                        self.runtime.tick(self.owner, allow_start=False)
                    else:
                        self.runtime.tick(self.owner, allow_start=False, allow_cleanup=self._may_start())
                    if self._may_start() and self.admission() and self._may_start():
                        self.runtime.tick(self.owner, allow_start=True)
                except Exception as error:
                    logger.warning('AI任务控制暂未完成：%s', type(error).__name__)
                self.stop_event.wait(self.interval)
        finally:
            self.runtime.shutdown()

    def close(self):
        self.stop_event.set()
        if self.thread:
            # Keep the enclosing worker.lock until this DB-capable thread has
            # actually stopped. A timed join could release the process lock
            # while an old SQLite transaction is still allowed to publish.
            self.thread.join()
        else:
            self.runtime.shutdown()
