"""Private desktop authorization reaches every licensed business route."""
import importlib
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.responses import JSONResponse

from desktop_preview.access import create_desktop_app

ORIGIN = "http://127.0.0.1:51779"


class License:
    state = "unactivated"

    def status(self):
        return {"state": self.state, "expires_at": None}

    def machine_code(self):
        return "TEST<&>"

    def activate(self, code):
        assert code == "signed+license"
        self.state = "active"


@pytest.fixture
def desktop(monkeypatch):
    license = License()
    stopped = threading.Event()
    calls = []
    main = importlib.import_module("app.main")

    async def business(scope, receive, send):
        calls.append(scope["path"])
        await JSONResponse({"path": scope["path"]})(scope, receive, send)

    monkeypatch.setattr(main, "app", business)
    asgi = create_desktop_app(
        license, origin=ORIGIN, ticket="launch-test", stop=stopped.set, stopping=stopped.is_set,
    )
    http = TestClient(asgi, base_url=ORIGIN, follow_redirects=False, client=("127.0.0.1", 5000))
    return http, license, stopped, calls


def test_ticket_is_one_use_and_never_enables_unlicensed_reads(desktop):
    http, license, _, calls = desktop
    path = "/api/v1/watchlist"
    assert http.get(path).json()["detail"]["code"] == "desktop_session_required"
    assert http.get("/__amr/open?ticket=wrong").status_code == 403
    response = http.get("/__amr/open?ticket=launch-test")
    assert response.status_code == 303
    assert response.headers["location"] == "/__amr/manage"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert http.get("/__amr/open?ticket=launch-test").status_code == 403
    page = http.get("/__amr/manage")
    assert page.headers["referrer-policy"] == "same-origin"
    assert "TEST&lt;&amp;&gt;" in page.text
    assert "launch-test" not in page.text
    assert http.get(path).json()["detail"]["code"] == "desktop_license_required"
    assert not calls

    assert http.post("/__amr/activate", data={"license": "signed+license"}).status_code == 403
    assert license.state == "unactivated"
    response = http.post("/__amr/activate", headers={"Origin": ORIGIN}, data={"license": "signed+license"})
    assert response.status_code == 303
    response = http.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert calls == [path]

    license.state = "expired"
    assert http.get("/api/v1/research/jobs/report1").status_code == 403
    assert http.get("/").headers["location"] == "/__amr/manage"
    assert http.post("/api/v1/tasks/task1/resume", headers={"Origin": ORIGIN}).status_code == 403
    assert http.post("/api/v1/tasks/task1/pause", headers={"Origin": ORIGIN}).status_code == 200
    assert http.post("/api/v1/ai/tasks/ai1/cancel", headers={"Origin": ORIGIN}).status_code == 200
    assert calls == [path, "/api/v1/tasks/task1/pause", "/api/v1/ai/tasks/ai1/cancel"]


def test_cross_origin_and_other_local_sessions_cannot_use_valid_license(desktop):
    http, license, _, calls = desktop
    license.state = "active"
    assert http.get("/__amr/open?ticket=launch-test", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert http.get("/__amr/open?ticket=launch-test").headers["location"] == "/"
    assert http.get("/api/v1/watchlist", headers={"Host": "foreign.test"}).status_code == 403
    assert http.get("/api/v1/watchlist", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert http.post("/api/v1/tasks/task1/pause", headers={"Origin": "http://127.0.0.1:51780"}).status_code == 403
    assert not calls
    assert http.get("/api/v1/watchlist").status_code == 200


def test_exit_blocks_admission_even_when_authorization_is_active(desktop):
    http, license, stopped, calls = desktop
    license.state = "active"
    assert http.get("/__amr/open?ticket=launch-test").headers["location"] == "/"
    assert http.post("/__amr/quit", headers={"Origin": "https://foreign.test"}).status_code == 403
    assert not stopped.is_set()
    assert http.post("/__amr/quit", headers={"Origin": ORIGIN}).status_code == 200
    assert stopped.is_set()
    assert http.get("/api/v1/watchlist").json()["detail"]["code"] == "desktop_stopping"
    assert not calls


def test_worker_without_license_never_initializes_or_claims(monkeypatch):
    worker = importlib.import_module("app.worker")

    def forbidden():
        pytest.fail("inactive worker must wait outside the database")

    monkeypatch.setattr(worker, "ensure_ready", forbidden)
    worker.run(once=True, stop_event=threading.Event(), allow_start=lambda: False)


@pytest.mark.parametrize("reason", ["expired", "shutdown"])
def test_open_ai_stream_closes_when_desktop_access_ends(monkeypatch, reason):
    from app import main
    from app.routers import ai_events
    from app.services import ai_event_read

    license = License()
    license.state = "active"
    stopped = threading.Event()
    business = FastAPI()
    business.include_router(ai_events.router)

    def first_batch(task_id, after):
        assert after == 0, "desktop must stop reading AI events after access ends"
        if reason == "expired":
            license.state = "expired"
        else:
            stopped.set()
        return {"state": "running"}, [(1, "delta", {"text": "before close"})]

    monkeypatch.setattr(main, "app", business)
    monkeypatch.setattr(ai_events, "read_batch", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_event_read, "read_batch", first_batch)
    asgi = create_desktop_app(
        license, origin=ORIGIN, ticket="stream-test", stop=stopped.set, stopping=stopped.is_set,
    )
    http = TestClient(asgi, base_url=ORIGIN, follow_redirects=False, client=("127.0.0.1", 5000))
    assert http.get("/__amr/open?ticket=stream-test").status_code == 303
    response = http.get("/api/v1/ai/tasks/task1/events")
    assert response.status_code == 200
    assert response.text.count("event: delta") == 1


def test_worker_rechecks_license_after_an_admitted_maintenance_step(monkeypatch, tmp_path):
    worker = importlib.import_module("app.worker")
    research = importlib.import_module("app.services.research_service")
    progress = importlib.import_module("app.services.sync_progress")
    authorization = threading.Event()
    authorization.set()

    def expire(**kwargs):
        authorization.clear()

    def forbidden(*args, **kwargs):
        pytest.fail("worker must not begin a new step after expiration")

    monkeypatch.setattr(worker, "ensure_ready", lambda: None)
    monkeypatch.setattr(worker, "work_dir", lambda: tmp_path)
    monkeypatch.setattr(progress, "worker_released", lambda **kwargs: None)
    monkeypatch.setattr(research, "cleanup_expired", expire)
    monkeypatch.setattr(worker, "set_setting", forbidden)
    monkeypatch.setattr(worker.tasks, "claim_next", forbidden)
    worker.run(once=True, stop_event=threading.Event(), allow_start=authorization.is_set)
    assert not authorization.is_set()


def test_expired_background_lanes_drain_ai_without_cleanup_or_new_work(monkeypatch):
    from app.services import intraday_brief
    from app.services.worker_ai_control import WorkerAIControl

    def forbidden(*args, **kwargs):
        pytest.fail("inactive brief or AI must not enter new work")

    monkeypatch.setattr(intraday_brief, "connect", forbidden)
    intraday_brief._tick(None, threading.Event(), allow_start=lambda: False)

    calls = []
    ticked = threading.Event()

    class Runtime:
        def recover_interrupted(self):
            pass

        def tick(self, owner, *, allow_start, allow_cleanup=True):
            calls.append((owner, allow_start, allow_cleanup))
            ticked.set()

        def shutdown(self):
            pass

    control = WorkerAIControl("single-owner", runtime=Runtime(), admission=forbidden, allow_start=lambda: False)
    control.start()
    try:
        assert ticked.wait(2)
    finally:
        control.close()
    assert calls and set(calls) == {("single-owner", False, False)}
