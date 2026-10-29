"""HTTP-only workbench gate for one user-started after-market chain.

The sole worker never enters this middleware. Static/health/progress/control
remain reachable; the durable SQLite pointer, rather than browser storage or
one job's status, controls all business routes across tabs and API processes.
"""
from contextvars import ContextVar
import re

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core import connect

business_http_request = ContextVar('increment_business_http_request', default=False)


def require_business_unlocked(connection):
    """Use immediately after BEGIN IMMEDIATE at direct SQL job creators."""
    from .automatic_tasks import guard_creation
    guard_creation(connection)
    if business_http_request.get():
        from .sync_progress import require_unlocked
        require_unlocked(connection)


def _access(method, path):
    from .sync_progress import lock_record, _chain
    with connect() as connection:
        connection.execute('BEGIN')
        saved = lock_record(connection)
        if saved is None:
            return None
        matched = re.fullmatch(r'/api/v1/tasks/([^/]+)(?:/(pause|cancel))?', path)
        if matched and (method in ('GET', 'HEAD') and matched[2] is None or method == 'POST' and matched[2]):
            root = connection.execute('SELECT * FROM job WHERE job_id=?', (saved['root_task_id'],)).fetchone()
            if root and any(item['job_id'] == matched[1] for item in _chain(connection, root)[0]):
                return None
        return saved


class IncrementWorkbenchMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '').rstrip('/')
        if scope['type'] != 'http' or not path.startswith('/api/v1/'):
            return await self.app(scope, receive, send)
        method = scope['method']
        # /sync/tasks still performs its transaction-local guard and may only
        # replay the same accepted request while locked; a new key is rejected.
        exempt = (method == 'OPTIONS' or method in ('GET', 'HEAD') and path in ('/api/v1/health', '/api/v1/sync/session') or
                  method == 'POST' and path in ('/api/v1/sync/tasks', '/api/v1/sync/session/acknowledge', '/api/v1/sync/session/control'))
        token = business_http_request.set(True)
        try:
            if not exempt:
                saved = await run_in_threadpool(_access, method, path)
                if saved:
                    response = JSONResponse({'detail': {'message': '盘后增量期间工作台已锁定，请先查看进度并确认完整结果',
                                                         'code': 'increment_workbench_locked', 'increment_session': saved}}, status_code=409)
                    return await response(scope, receive, send)
            return await self.app(scope, receive, send)
        finally:
            business_http_request.reset(token)
