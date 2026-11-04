"""Small bounded HTTP adapters, with sanitized errors and no credential logging."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import re
from threading import RLock
import time
from urllib.parse import urlparse
import httpx


class ProviderError(ValueError):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code


_CONNECTION_SCOPE = ContextVar('provider_connection_scope', default=None)
_SECTOR_TAIL_PACING = ContextVar('sector_tail_pacing', default=None)
SECTOR_TAIL_PACING_VERSION = 'sector-tail-pacing-v2'


def validate_sector_tail_pacing(version):
    if version is not None and version != SECTOR_TAIL_PACING_VERSION:
        raise ValueError('板块尾部请求节奏版本不受支持')
    return version


class _PacingInterrupted(Exception):
    pass


class _SectorTailPacing:
    """One explicit serial batch; real transport starts share the same clock."""
    interval = .6

    def __init__(self, guard):
        self.guard = guard
        self.last_start = None
        self.blocked = None
        self.closed = False
        self.attempts = 0

    def check(self):
        if self.closed:
            raise ProviderError('本次板块尾部请求范围已结束')
        try:
            if self.guard:
                self.guard()
        except Exception as error:
            raise _PacingInterrupted() from error

    def wait(self, seconds):
        deadline = time.monotonic()+seconds
        while True:
            self.check()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(.2, remaining))

    def before_request(self):
        self.check()
        if self.blocked is not None:
            raise self.blocked
        if self.last_start is not None:
            self.wait(max(0, self.interval-(time.monotonic()-self.last_start)))
        self.check()
        self.last_start = time.monotonic()
        self.attempts += 1


@contextmanager
def sector_tail_request_scope(version, guard=None):
    """Only a frozen increment batch may change this one endpoint's pacing."""
    validate_sector_tail_pacing(version)
    pacing = _SectorTailPacing(guard) if version is not None else None
    token = _SECTOR_TAIL_PACING.set(pacing)
    try:
        yield pacing
    except _PacingInterrupted as error:
        raise error.__cause__
    finally:
        if pacing is not None:
            pacing.closed = True
        _SECTOR_TAIL_PACING.reset(token)


def _http_client():
    return httpx.Client(timeout=httpx.Timeout(45, connect=12), follow_redirects=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1, keepalive_expiry=30))


class _ConnectionScope:
    """Task-owned transport reuse, never a process-wide credential cache."""
    def __init__(self):
        self.entries = {}
        self.lock = RLock()
        self.closed = False

    def connection(self, adapter):
        if self.closed:
            raise ProviderError('本次任务的数据源连接范围已结束')
        identity = (adapter.source_id, adapter.key)
        if identity not in self.entries:
            if len(self.entries) >= 8:
                raise ProviderError('本次任务的数据源连接范围超出限制')
            self.entries[identity] = (_http_client(), {'last_request': adapter.last_request})
        return self.entries[identity]

    def close(self):
        # A failed close must not retain the other connections or replace the
        # task's original exception. There are no durable cookies or secrets.
        with self.lock:
            self.closed = True
            for client, _ in self.entries.values():
                try:
                    client.close()
                except Exception:
                    pass
            self.entries.clear()


@contextmanager
def provider_connection_scope():
    """Reuse bounded HTTP connections until one explicit task finishes.

    Nested helpers share the current task's scope; all success, cancellation
    and error exits close its transports. A later task starts a fresh scope.
    """
    existing = _CONNECTION_SCOPE.get()
    if existing is not None:
        yield
        return
    scope = _ConnectionScope()
    token = _CONNECTION_SCOPE.set(scope)
    try:
        yield
    finally:
        _CONNECTION_SCOPE.reset(token)
        scope.close()


@contextmanager
def _request_connection(adapter):
    scope = _CONNECTION_SCOPE.get()
    if scope is None:
        with _http_client() as client:
            yield client, {'last_request': adapter.last_request}
    else:
        # The sole worker is sequential. This also makes copied contexts safe
        # if a caller introduces threads: sharing a scope does not add requests.
        with scope.lock:
            yield scope.connection(adapter)


class ProviderClient:
    BASES = {"fuyao": "https://fuyao.aicubes.cn", "tickflow": "https://api.tickflow.org", "tickflow-public": "https://free-api.tickflow.org"}

    def __init__(self, source_id, key=None):
        if source_id not in self.BASES:
            raise ProviderError("尚未适配该来源")
        self.source_id, self.key = source_id, key
        self.last_request = 0.0

    def request(self, path, params=None, *, before_attempt=None, max_attempts=3, timeout=None):
        if self.source_id != "tickflow-public" and not self.key:
            raise ProviderError("请先配置该来源的Key；注册免费Key不等于付费会员", 401)
        headers = {"User-Agent": "ShixuWorkbench/0.1", "Accept": "application/json"}
        if self.key:
            headers["X-api-key"] = self.key
        params = {k: str(v).lower() if isinstance(v, bool) else v for k, v in (params or {}).items()}
        pacing = _SECTOR_TAIL_PACING.get() if self.source_id == 'fuyao' and path == '/api/a-share-index/prices/historical' else None
        with _request_connection(self) as (client, state):
            for attempt in range(max_attempts):
                can_retry = attempt + 1 < max_attempts
                if pacing is not None:
                    pacing.before_request()
                else:
                    delay = (1.2 if self.source_id == "fuyao" else 6.2) - (time.monotonic() - state['last_request'])
                    if delay > 0:
                        time.sleep(delay)
                # Explicit callers may have a deadline/lease. Keep this outside
                # the parsing retry catch so stopping cannot trigger another GET.
                if before_attempt is not None:
                    before_attempt()
                try:
                    try:
                        options = {'timeout': timeout} if timeout is not None else {}
                        response = client.get(self.BASES[self.source_id] + path, params=params, headers=headers, **options)
                    finally:
                        # Transport failures also consume the request interval.
                        self.last_request = state['last_request'] = time.monotonic()
                    if pacing is not None and response.status_code in (401, 403, 429):
                        pacing.blocked = ProviderError('来源拒绝本次板块尾部请求，已停止新增请求', status=response.status_code)
                        raise pacing.blocked
                    if response.status_code == 429 or response.status_code >= 500:
                        if can_retry:
                            (pacing.wait if pacing is not None else time.sleep)(2 ** attempt * 2)
                            continue
                    if response.status_code != 200:
                        raise ProviderError("来源返回HTTP " + str(response.status_code), response.status_code)
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError('来源响应格式异常')
                    if self.source_id == "fuyao" and body.get("code") != 0:
                        code = body.get("code")
                        if pacing is not None and code in (2001, 2003, 4001):
                            pacing.blocked = ProviderError('来源认证、权限或限流拒绝，已停止新增板块尾部请求', code=code)
                            raise pacing.blocked
                        if (code == 4001 or str(code).startswith("5")) and can_retry:
                            (pacing.wait if pacing is not None else time.sleep)(2 ** attempt * 2)
                            continue
                        raise ProviderError("来源业务响应未通过（code=" + str(code) + "）", code=code)
                    if body.get("data") is None:
                        raise ProviderError("来源暂无数据，不能当作完整空结果")
                    return body
                except (httpx.HTTPError, ValueError) as error:
                    if isinstance(error, ProviderError):
                        raise
                    if not can_retry:
                        raise ProviderError("来源连接或数据格式异常，已有限重试；请稍后再试") from None
                    (pacing.wait if pacing is not None else time.sleep)(2 ** attempt)
        raise ProviderError("来源请求未完成")

    def download(self, kind, target: Path, progress=None):
        if self.source_id != "fuyao":
            raise ProviderError("该来源没有通过本应用全市场批量下载验收")
        payload = self.request("/api/dump/market-dumps/" + kind + "/download-url")
        url = payload["data"].get("presigned_url")
        parsed = urlparse(url or "")
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host == "o.thsi.cn" or host.endswith(".amazonaws.com") or host.endswith(".r2.cloudflarestorage.com")):
            raise ProviderError("下载主机不在已验收的供应商对象存储范围")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".partial")
        try:
            with httpx.stream("GET", url, timeout=httpx.Timeout(120, connect=15), follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ProviderError("来源文件下载失败（HTTP " + str(response.status_code) + "）")
                declared = int(response.headers.get("Content-Length", 0))
                if declared > 512 * 1024 * 1024:
                    raise ProviderError("数据包超出单次下载容量限制")
                size = 0
                with temporary.open("wb") as writer:
                    for block in response.iter_bytes(1024 * 1024):
                        size += len(block)
                        if size > 512 * 1024 * 1024:
                            raise ProviderError("下载超出容量限制")
                        writer.write(block)
                        if progress:
                            progress(size, declared)
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return {"bytes": size, "host": host, "request_id": payload.get("request_id")}
