"""Small bounded HTTP adapters, with sanitized errors and no credential logging."""
from __future__ import annotations
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import httpx


class ProviderError(ValueError):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code


class ProviderClient:
    BASES = {"fuyao": "https://fuyao.aicubes.cn", "tickflow": "https://api.tickflow.org", "tickflow-public": "https://free-api.tickflow.org"}

    def __init__(self, source_id, key=None):
        if source_id not in self.BASES:
            raise ProviderError("尚未适配该来源")
        self.source_id, self.key = source_id, key
        self.last_request = 0.0

    def request(self, path, params=None):
        if self.source_id != "tickflow-public" and not self.key:
            raise ProviderError("请先配置该来源的Key；注册免费Key不等于付费会员", 401)
        delay = (1.2 if self.source_id == "fuyao" else 6.2) - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        headers = {"User-Agent": "ShixuWorkbench/0.1", "Accept": "application/json"}
        if self.key:
            headers["X-api-key"] = self.key
        params = {k: str(v).lower() if isinstance(v, bool) else v for k, v in (params or {}).items()}
        for attempt in range(3):
            try:
                with httpx.Client(timeout=httpx.Timeout(45, connect=12), follow_redirects=False) as client:
                    response = client.get(self.BASES[self.source_id] + path, params=params, headers=headers)
                self.last_request = time.monotonic()
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        time.sleep(2 ** attempt * 2)
                        continue
                if response.status_code != 200:
                    raise ProviderError("来源返回HTTP " + str(response.status_code), response.status_code)
                body = response.json()
                if self.source_id == "fuyao" and body.get("code") != 0:
                    code = body.get("code")
                    if (code == 4001 or str(code).startswith("5")) and attempt < 2:
                        time.sleep(2 ** attempt * 2)
                        continue
                    raise ProviderError("来源业务响应未通过（code=" + str(code) + "）", code=code)
                if body.get("data") is None:
                    raise ProviderError("来源暂无数据，不能当作完整空结果")
                return body
            except (httpx.HTTPError, ValueError) as error:
                if isinstance(error, ProviderError):
                    raise
                if attempt == 2:
                    raise ProviderError("来源连接或数据格式异常，已有限重试；请稍后再试") from None
                time.sleep(2 ** attempt)
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
