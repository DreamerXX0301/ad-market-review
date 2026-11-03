"""Exercise two independent API clients against the same isolated conversation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient

from app.routers.ai_events import router as events_router
from app.services.ai_runtime import AIRuntime
from test_ai_agent_round import create, modern_agent_client
from test_ai_service import enable, fake


def test_shared_session_serializes_sends_and_replays_without_new_work(modern_agent_client):
    first = modern_agent_client
    first.app.include_router(events_router)
    second = TestClient(first.app)
    runtime = AIRuntime(fake)
    try:
        enable(first, runtime, 'openai')
        created = create(first).json()
        path = '/api/v1/ai/sessions/' + created['session_id']
        assert second.get(path).json()['active_task_id'] == created['task_id']
        first.post('/api/v1/ai/tasks/' + created['task_id'] + '/cancel').raise_for_status()
        assert second.get(path).json()['active_task_id'] is None

        barrier = Barrier(2)

        def submit(client, key):
            barrier.wait(timeout=5)
            body = {'text': '今日情绪 ' + key, 'request_key': key, 'consent': True}
            return client.post(path + '/messages', json=body), body

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(submit, client, key)
                       for client, key in ((first, 'tab-a'), (second, 'tab-b'))]
            results = sorted([f.result() for f in pending], key=lambda pair: pair[0].status_code)
        (accepted, body), (busy, _) = results
        assert (accepted.status_code, busy.status_code) == (202, 409)
        assert busy.json()['detail']['reason_code'] == 'session_busy'
        task_id = accepted.json()['task_id']
        assert second.post(path + '/messages', json=body).json()['task_id'] == task_id
        view = second.get(path).json()
        assert view['active_task_id'] == task_id
        assert [message['task_id'] for message in view['messages']] == [created['task_id'], task_id]
        task = '/api/v1/ai/tasks/' + task_id
        first.post(task + '/cancel').raise_for_status()
        before = second.get(task).json()
        for client in (first, second):
            assert client.get(task + '/events?after=0').status_code == 200
        assert second.get(task).json() == before
        assert second.get(path).json()['active_task_id'] is None

        provider_path = '/api/v1/ai/providers/openai'
        provider = next(item for item in first.get('/api/v1/ai/providers').json()['items']
                        if item['provider_id'] == 'openai')
        saved = first.put(provider_path, json={
            'expected_revision': provider['config_revision'], 'enabled': False,
            'model': provider['model'], 'api_key': 'synthetic-replacement-credential',
            'limits': {'daily_request_limit': 31},
        }).raise_for_status().json()
        assert saved['config_revision'] == provider['config_revision'] + 1
        assert saved['limits'] == {**provider['limits'], 'daily_request_limit': 31}
        assert second.get(path).json()['config_matches'] is False
        first.delete(path).raise_for_status()
        assert second.get(path).status_code == 404
        assert second.get(task).json()['session_id'] is None
    finally:
        runtime.shutdown()
