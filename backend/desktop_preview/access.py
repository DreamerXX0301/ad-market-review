"""Authenticate the desktop loopback session before reaching business routes."""
from __future__ import annotations

from datetime import datetime
from html import escape
import re
from secrets import compare_digest, token_urlsafe
from time import sleep
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from desktop_licensing.protocol import MAX_CODE_LENGTH

_COOKIE = "amr_desktop_session"
_CONTROL = "/__amr"
_STOP_PATH = re.compile(r"/api/v1/(?:tasks/[^/]+/(?:pause|cancel)|ai/tasks/[^/]+/cancel)")
_STATUS = {
    "active": "授权有效",
    "unactivated": "请导入授权",
    "expired": "授权已到期，请导入新的授权",
    "clock_check_required": "请校准 Windows 系统时间",
    "unavailable": "暂时无法读取授权，请重试",
}
_HEADER = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}
_BOOTSTRAP_HEADER = {**_HEADER, "Referrer-Policy": "no-referrer"}
_CONTROL_HEADER = {
    **_HEADER,
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
}
_MANAGEMENT_SCRIPT = """
const picker = document.getElementById("license-file");
const field = document.getElementById("license");
const fileStatus = document.getElementById("license-file-status");

picker.addEventListener("change", async () => {
  const file = picker.files[0];
  fileStatus.textContent = "";
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".amr-license") ||
      file.size === 0 || file.size > field.maxLength) {
    fileStatus.textContent = "请重新选择有效的 .amr-license 文件。";
    return;
  }
  try {
    const content = (await file.text()).trim();
    if (picker.files[0] !== file) return;
    if (!content) throw new Error();
    field.value = content;
    fileStatus.textContent = "授权内容已读取。请点击“导入授权”保存。";
  } catch {
    if (picker.files[0] === file) {
      fileStatus.textContent = "无法读取授权文件，可以手动粘贴授权内容。";
    }
  }
});

const copy = document.getElementById("copy-machine");
copy?.addEventListener("click", async () => {
  const code = document.getElementById("machine-code");
  const status = document.getElementById("machine-status");
  try {
    await navigator.clipboard.writeText(code.textContent);
    status.textContent = "已复制机器码。";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(code);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = "已选中机器码，请按 Ctrl+C 复制。";
  }
});
"""


def _status(client):
    # A second process can hold the DPAPI state lock for its short write.
    for attempt in range(4):
        try:
            return client.status()
        except RuntimeError:
            if attempt < 3:
                sleep(0.03)
        except (OSError, ValueError):
            break
    return {"state": "unavailable"}


def license_active(client) -> bool:
    """Fail closed without making a transient state-lock error fatal."""
    return _status(client)["state"] == "active"


def _same(actual: str, expected: str) -> bool:
    return compare_digest(actual.encode("utf-8"), expected.encode("utf-8"))


def _error(code: str, message: str, status: int, *, headers=_HEADER):
    return JSONResponse({"detail": {"code": code, "message": message}}, status, headers=headers)


def _status_message(status):
    phase = status["state"]
    message = _STATUS.get(phase, _STATUS["unavailable"])
    if phase == "active":
        expires = status.get("expires_at")
        expiry = datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M") if expires is not None else "永久"
        message += " · " + expiry
    return message


def _manage(client, message="", *, tray_mode=False):
    status = _status(client)
    copy_button = ""
    try:
        machine = client.machine_code()
    except (RuntimeError, OSError, ValueError):
        machine = "暂时无法读取机器码"
    else:
        if tray_mode:
            copy_button = ' <button id="copy-machine" type="button">复制机器码</button><span id="machine-status" role="status"></span>'
    enter = '<p><a href="/">打开工作台</a></p>' if status["state"] == "active" else ""
    notice = '<p role="status">' + escape(message) + '</p>' if message else ""
    headers = _CONTROL_HEADER
    picker = ""
    script = ""
    if tray_mode:
        help_text = "把本机机器码发给授权方。收到 .amr-license 文件后，可以直接选择文件，也可以用文本编辑器打开后粘贴授权内容。"
        picker = '<p><label for="license-file">选择授权文件</label> <input id="license-file" type="file" accept=".amr-license"><br><span id="license-file-status" role="status"></span></p>'
        exit_help = "<p>关闭浏览器页面后，工作台仍在后台运行。请从 Windows 托盘退出。</p>"
        nonce = token_urlsafe(16)
        headers = {**headers, "Content-Security-Policy": headers["Content-Security-Policy"] + f"; script-src 'nonce-{nonce}'"}
        script = f'<script nonce="{nonce}">{_MANAGEMENT_SCRIPT}</script>'
    else:
        help_text = "用文本编辑器打开 .amr-license 文件，将授权内容复制到下方。"
        exit_help = f'<form method="post" action="{_CONTROL}/quit"><button type="submit">退出工作台</button></form>'
    body = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>冒险的梦想家 · 桌面授权</title>
<style>
body{{font:16px/1.6 system-ui,sans-serif;max-width:42rem;margin:3rem auto;padding:0 1rem;color:#142033}}
textarea{{display:block;width:100%;box-sizing:border-box;margin:.5rem 0 1rem;font:inherit}}
button,a{{font:inherit;color:inherit}} code{{overflow-wrap:anywhere}}
</style>
<h1>冒险的梦想家</h1>
<p>{escape(_status_message(status))}</p>{notice}{enter}
<p>本机机器码 <code id="machine-code">{escape(machine)}</code>{copy_button}</p>
<p>{help_text}</p>
<form method="post" action="{_CONTROL}/activate">
{picker}
<label for="license">授权内容</label>
<textarea id="license" name="license" rows="4" maxlength="{MAX_CODE_LENGTH + 32}" required></textarea>
<button type="submit">导入授权</button>
</form>
<p><a href="{_CONTROL}/manage">刷新授权状态</a></p>
{exit_help}{script}
</html>"""
    return HTMLResponse(body, headers=headers)


async def _read_license(request: Request) -> str:
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise ValueError("请通过授权页导入")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 4 * MAX_CODE_LENGTH:
            raise ValueError("授权内容过长")
        data.extend(chunk)
    try:
        form = parse_qs(data.decode("ascii"), strict_parsing=True, max_num_fields=1)
        code = form["license"][0].strip().lstrip("\ufeff").strip()
    except (ValueError, KeyError) as error:
        raise ValueError("授权内容格式不正确") from error
    if not code or len(code) > MAX_CODE_LENGTH:
        raise ValueError("授权内容格式不正确")
    return code


def create_desktop_app(
    client, *, origin: str, stop: Callable[[], None], ticket: str | None = None,
    stopping: Callable[[], bool] | None = None,
    consume_ticket: Callable[[str], bool] | None = None,
):
    """Use a single launch ticket or a callback that atomically consumes fresh tickets."""
    from app.main import app as business

    address = urlsplit(origin)
    host = address.netloc
    if origin != f"http://127.0.0.1:{address.port}" or address.port is None:
        raise ValueError("桌面预览需要固定的本机回环地址")
    if (ticket is None) == (consume_ticket is None) or ticket == "":
        raise ValueError("桌面预览需要一种启动票据验证方式")
    session = token_urlsafe(32)
    tray_mode = consume_ticket is not None
    cookie_name = f"{_COOKIE}_{address.port}" if tray_mode else _COOKIE
    closing = False

    if consume_ticket is None:
        opened = False

        def accept_ticket(supplied: str):
            nonlocal opened
            if opened or not _same(supplied, ticket):
                return False
            opened = True
            return True
    else:
        accept_ticket = consume_ticket

    def is_closing():
        return closing or stopping is not None and stopping()

    async def may_stream():
        return not is_closing() and await run_in_threadpool(license_active, client) and not is_closing()

    async def desktop(scope, receive, send):
        nonlocal closing
        if scope["type"] == "lifespan":
            return await business(scope, receive, send)
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        request = Request(scope, receive)
        path, method = request.url.path, request.method
        peer = scope.get("client")
        origins = request.headers.getlist("origin")
        if (
            not peer or peer[0] != "127.0.0.1" or request.headers.getlist("host") != [host]
            or (origins and origins != [origin])
            or (method not in ("GET", "HEAD") and origins != [origin])
            or request.headers.get("sec-fetch-site") in ("cross-site", "same-site")
        ):
            response = _error("desktop_origin_rejected", "请从桌面启动器打开本机工作台", 403)
            return await response(scope, receive, send)

        if is_closing():
            return await _error("desktop_stopping", "工作台正在退出", 503)(scope, receive, send)

        if path == _CONTROL + "/open" and method == "GET":
            supplied = request.query_params.getlist("ticket")
            if len(supplied) != 1 or not supplied[0] or not accept_ticket(supplied[0]):
                response = _error(
                    "desktop_ticket_rejected", "启动票据已失效，请从托盘或桌面启动器重新打开", 403,
                    headers=_BOOTSTRAP_HEADER,
                )
            else:
                active = await run_in_threadpool(license_active, client)
                if is_closing():
                    return await _error("desktop_stopping", "工作台正在退出", 503)(scope, receive, send)
                response = RedirectResponse("/" if active else _CONTROL + "/manage", 303, headers=_BOOTSTRAP_HEADER)
                response.set_cookie(cookie_name, session, httponly=True, samesite="strict")
            return await response(scope, receive, send)

        if not _same(request.cookies.get(cookie_name, ""), session):
            response = _error("desktop_session_required", "请从桌面启动器打开本机工作台", 403)
            return await response(scope, receive, send)

        if not tray_mode and path == _CONTROL + "/quit" and method == "POST":
            closing = True
            response = HTMLResponse("<!doctype html><meta charset='utf-8'><p>正在退出工作台，可以关闭此窗口。</p>",
                                    headers=_CONTROL_HEADER)
            response.delete_cookie(cookie_name, httponly=True, samesite="strict")
            try:
                await response(scope, receive, send)
            finally:
                stop()
            return
        if path == _CONTROL + "/manage" and method == "GET":
            response = await run_in_threadpool(_manage, client, tray_mode=tray_mode)
            return await response(scope, receive, send)
        if path == _CONTROL + "/status" and method == "GET":
            response = JSONResponse(await run_in_threadpool(_status, client), headers=_HEADER)
            return await response(scope, receive, send)
        if path == _CONTROL + "/activate" and method == "POST":
            try:
                code = await _read_license(request)
                await run_in_threadpool(client.activate, code)
            except ClientDisconnect:
                return
            except ValueError as error:
                response = await run_in_threadpool(_manage, client, str(error), tray_mode=tray_mode)
            except (OSError, RuntimeError):
                response = await run_in_threadpool(_manage, client, "无法保存授权，请重试", tray_mode=tray_mode)
            else:
                response = RedirectResponse(_CONTROL + "/manage", 303, headers=_HEADER)
            return await response(scope, receive, send)
        if path == _CONTROL or path.startswith(_CONTROL + "/"):
            return await _error("desktop_not_found", "入口不存在", 404)(scope, receive, send)
        async def send_private(message):
            if message["type"] == "http.response.start":
                protected = {key.lower().encode(): value.encode() for key, value in _HEADER.items()}
                headers = [(key, value) for key, value in message.get("headers", ()) if key.lower() not in protected]
                message = {**message, "headers": [*headers, *protected.items()]}
            await send(message)
        if method == "POST" and _STOP_PATH.fullmatch(path):
            return await business(scope, receive, send_private)
        if not await run_in_threadpool(license_active, client):
            if method == "GET" and path == "/":
                response = RedirectResponse(_CONTROL + "/manage", 303, headers=_HEADER)
            else:
                response = _error("desktop_license_required", "请从授权管理导入有效授权", 403)
            return await response(scope, receive, send)
        if is_closing():
            return await _error("desktop_stopping", "工作台正在退出", 503)(scope, receive, send)
        scope = {**scope, "state": {**scope.get("state", {}), "desktop_stream_admission": may_stream}}
        return await business(scope, receive, send_private)

    return desktop
