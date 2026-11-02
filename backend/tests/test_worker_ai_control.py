"""The sole worker retains its lock until AI DB control has stopped."""
import threading
import time
from app.services.worker_ai_control import WorkerAIControl


class Runtime:
    def __init__(self):
        self.calls=[]
        self.recovered=False
        self.closed=False
        self.ticked=threading.Event()
    def recover_interrupted(self):self.recovered=True
    def tick(self, owner, allow_start=True):
        self.calls.append((owner,allow_start))
        self.ticked.set()
    def shutdown(self):self.closed=True


def test_blocked_business_lane_still_drains_ai_without_claiming():
    runtime=Runtime()
    control=WorkerAIControl('single-owner',runtime=runtime,admission=lambda:False,interval=.01).start()
    assert runtime.ticked.wait(1)
    control.close()
    assert runtime.recovered and runtime.closed
    assert runtime.calls and all(allowed is False for _,allowed in runtime.calls)
    assert not control.thread.is_alive()


def test_close_waits_for_control_transaction_before_releasing_caller():
    entered=threading.Event();release=threading.Event()
    class Slow(Runtime):
        def tick(self,*args,**kwargs):
            entered.set();assert release.wait(2)
    runtime=Slow();control=WorkerAIControl('single-owner',runtime=runtime,admission=lambda:False).start()
    assert entered.wait(1)
    closer=threading.Thread(target=control.close);closer.start()
    time.sleep(.03)
    assert closer.is_alive() and not runtime.closed
    release.set();closer.join(1)
    assert not closer.is_alive() and runtime.closed and not control.thread.is_alive()
