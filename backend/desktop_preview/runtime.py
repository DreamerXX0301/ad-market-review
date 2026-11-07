"""Supervise the loopback API, licensed worker and private Edge app window."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from hashlib import sha256
import json
import multiprocessing
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from desktop_licensing.client import DesktopClient
from desktop_licensing.windows import default_state_directory, protect, unprotect
from .access import license_active
from .windows_runtime import edge_path, protect_process_tree, require_local_ntfs


def _ordinary(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if reparse or not expected(info.st_mode):
        raise ValueError(f"工作台路径不是普通{'目录' if directory else '文件'}，{path.name}")


def _directory(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir(exist_ok=True, mode=0o700)
    _ordinary(path, directory=True)
    return path


def _load_public(package: Path):
    key = serialization.load_pem_public_key((package / "verify.pem").read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("测试包验签公钥无效")
    return key


def _license_client(package: Path) -> DesktopClient:
    key = _load_public(package)
    fingerprint = sha256(key.public_bytes_raw()).hexdigest()[:20]
    base = default_state_directory().parent
    profile = base / "license-preview-v1" / fingerprint
    for path in (base, profile.parent, profile):
        if path.exists() or path.is_symlink():
            _ordinary(path, directory=True)
    return DesktopClient(profile, key, protect=protect, unprotect=unprotect)


def _install_root(client: DesktopClient) -> Path:
    base = client.directory.parents[1]
    local = base.parent
    _ordinary(local, directory=True)
    require_local_ntfs(local)
    return _directory(_directory(_directory(local, base.name), "workbench-preview-v1"), client.directory.name)


def _release_record(package: Path) -> dict:
    from app.infrastructure.config import APP_VERSION

    manifest = json.loads((package / "BUILD-MANIFEST.json").read_text(encoding="utf-8"))
    commit, version = manifest.get("source_commit"), manifest.get("app_version")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None or version != APP_VERSION:
        raise ValueError("安装包版本清单无效")
    return {"format": 1, "source_commit": commit, "app_version": version}


def _configure_environment(data: Path, key: Path) -> None:
    # A desktop preview must not inherit the operator's existing service roots.
    for name in tuple(os.environ):
        if name.startswith("SHIXU_"):
            del os.environ[name]
    os.environ.update(
        SHIXU_DATA_DIR=str(data),
        SHIXU_CREDENTIAL_MASTER_KEY_FILE=str(key),
        SQLITE_TMPDIR=str(data / "work"),
    )


def _configure_instance(root: Path, package: Path) -> None:
    from app.infrastructure.storage import atomic_json

    data = _directory(root, "data")
    marker = root / "release.json"
    record = _release_record(package)
    if marker.exists() or marker.is_symlink():
        _ordinary(marker, directory=False)
        if json.loads(marker.read_text(encoding="utf-8")) != record:
            raise ValueError("检测到不同构建的数据目录，请先完成独立备份、恢复和迁移验收")
    else:
        if any(data.iterdir()):
            raise ValueError("已有数据缺少版本回执，请先核对备份")
        atomic_json(marker, record)

    for name in ("business", "published", "raw", "work", "credentials"):
        _directory(data, name)
    database = data / "business" / "app.sqlite3"
    if database.exists() or database.is_symlink():
        _ordinary(database, directory=False)
    key = _directory(root, "secrets") / "credential-master.key"
    if key.exists() or key.is_symlink():
        _ordinary(key, directory=False)
        with key.open("rb") as file:
            Fernet(file.read(128).strip())
    else:
        if database.exists() or any((data / "credentials").iterdir()):
            raise ValueError("找到已有业务数据，请保留并核对原主密钥")
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(Fernet.generate_key())
            file.flush()
            os.fsync(file.fileno())
    _configure_environment(data, key)


def _worker_entry(package: Path, stop) -> None:
    # Ctrl+C is handled by the parent, which asks the worker to finish safely.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    from app.worker import run

    client = _license_client(package)
    run(stop_event=stop, allow_start=lambda: license_active(client))


@contextmanager
def _listen(root: Path):
    from app.infrastructure.storage import atomic_json

    address = root / "loopback.json"
    port = 0
    if address.exists() or address.is_symlink():
        _ordinary(address, directory=False)
        port = json.loads(address.read_text(encoding="utf-8"))
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("工作台本机端口回执无效")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            if not port:
                raise
            # Windows can retain the previous connection while it finishes closing.
            listener.bind(("127.0.0.1", 0))
        if not port:
            atomic_json(address, listener.getsockname()[1])
        yield listener


def _serve(package: Path, root: Path, client: DesktopClient) -> None:
    import uvicorn

    from .access import create_desktop_app

    browser = edge_path()
    browser_data = _directory(root, "browser-profile")
    protect_process_tree()
    context = multiprocessing.get_context("spawn")
    worker_stop = context.Event()
    closing = threading.Event()
    workers = []
    errors = []
    with _listen(root) as listener:
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        ticket = secrets.token_urlsafe(32)

        def stop() -> None:
            closing.set()
            worker_stop.set()
            server.should_exit = True

        application = create_desktop_app(
            client, origin=origin, ticket=ticket, stop=stop, stopping=closing.is_set,
        )
        server = uvicorn.Server(uvicorn.Config(
            application, host="127.0.0.1", loop="asyncio", http="h11", ws="none",
            proxy_headers=False, access_log=False, log_level="warning", lifespan="on",
        ))

        def supervise() -> None:
            try:
                while not server.started:
                    if closing.wait(0.1):
                        return
                window = subprocess.Popen([
                    str(browser), f"--user-data-dir={browser_data}", "--no-first-run",
                    "--no-default-browser-check", "--disable-background-mode", "--disable-sync",
                    f"--app={origin}/__amr/open?ticket={ticket}",
                ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while not closing.wait(0.25):
                    if window.poll() is not None:
                        if window.returncode:
                            errors.append("工作台窗口未能启动")
                        break
                    if not workers and license_active(client):
                        worker = context.Process(target=_worker_entry, args=(package, worker_stop), name="amr-worker")
                        worker.start()
                        workers.append(worker)
                    if workers and not workers[0].is_alive():
                        errors.append("后台工作进程已退出，请保留启动窗口的错误信息")
                        break
            except (OSError, RuntimeError, ValueError):
                errors.append("无法启动工作台窗口或后台进程")
            finally:
                stop()

        supervisor = threading.Thread(target=supervise, name="amr-desktop-supervisor")
        supervisor.start()
        print("正在启动本机工作台。可用工作台的退出入口安全停止。", flush=True)
        try:
            server.run(sockets=[listener])
        finally:
            stop()
            supervisor.join()
            if workers:
                if workers[0].is_alive():
                    print("后台任务正在收尾，请等候。", flush=True)
                workers[0].join()
                workers[0].close()
        if errors:
            raise RuntimeError(errors[0])
        if not server.started:
            raise RuntimeError("本机 API 启动失败，请保留启动窗口的错误信息")


def run_preview(package: Path) -> None:
    if sys.platform != "win32":
        raise ValueError("请在 Windows x64 测试机运行")
    from app.infrastructure.file_operations import locked_file

    client = _license_client(package)
    root = _install_root(client)
    try:
        with locked_file(root / "instance.lock", exclusive=True, blocking=False):
            _configure_instance(root, package)
            _serve(package, root, client)
    except BlockingIOError:
        raise RuntimeError("工作台已经打开或正在收尾") from None


def _diagnostic_worker(stop) -> None:
    from app.worker import run

    run(once=True, stop_event=stop)


def diagnose(package: Path) -> dict:
    """Exercise the bundled backend with an empty, isolated business directory."""
    import httpx
    import duckdb
    import numpy
    import pandas
    import pyarrow
    import sqlite3

    with TemporaryDirectory(prefix="amr-desktop-diagnose-") as temporary:
        root = Path(temporary).resolve()
        if sys.platform == "win32":
            require_local_ntfs(root)
            edge_path()
            probe_data = secrets.token_bytes(32)
            if unprotect(protect(probe_data)) != probe_data:
                raise RuntimeError("Windows 本地授权保护检查失败")
        data = root / "data"
        key = root / "diagnostic-only.key"
        key.write_bytes(Fernet.generate_key())
        _configure_environment(data, key)

        from app.infrastructure.database import check_sqlite_runtime, connect
        from app.main import app

        check_sqlite_runtime()
        context = multiprocessing.get_context("spawn")
        worker_stop = context.Event()
        worker = context.Process(target=_diagnostic_worker, args=(worker_stop,))
        worker.start()
        try:
            worker.join(90)
            if worker.exitcode != 0:
                raise RuntimeError("隔离工作进程启动或迁移检查失败")
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join()
            worker.close()

        async def probe():
            transport = httpx.ASGITransport(app=app)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                    health, frontend = await client.get("/health"), await client.get("/")
                    if health.status_code != 200 or health.json().get("application") != "ad-market-review":
                        raise RuntimeError("本机 API 健康检查失败")
                    if frontend.status_code != 200 or "text/html" not in frontend.headers.get("content-type", ""):
                        raise RuntimeError("安装包缺少可用的工作台页面")

        asyncio.run(probe())

        def verify_database():
            with connect() as db:
                schema = db.execute("PRAGMA user_version").fetchone()[0]
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("诊断业务库完整性检查失败")
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("诊断业务库存在无效引用")
                return schema

        schema = verify_database()
        sys.path.insert(0, str(package / "scripts"))
        try:
            from backup import perform_backup
            from restore import perform_restore

            bundle, restored = root / "backup", root / "restored"
            perform_backup(data, bundle)
            perform_restore(bundle, restored)
            _configure_environment(restored, key)
            if verify_database() != schema:
                raise RuntimeError("恢复副本的数据库结构版本不一致")
        finally:
            sys.path.pop(0)
        return {
            "app_version": _release_record(package)["app_version"], "schema": schema,
            "sqlite": sqlite3.sqlite_version, "duckdb": duckdb.__version__,
            "numpy": numpy.__version__, "pandas": pandas.__version__, "pyarrow": pyarrow.__version__,
            "api": "ok", "frontend": "ok", "worker_once": "ok", "backup_restore": "ok",
        }
