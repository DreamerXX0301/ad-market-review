#!/usr/bin/env python3
"""Assemble a private Windows x64 desktop preview without collecting user data."""

from __future__ import annotations

import argparse
from base64 import b64decode
from datetime import datetime, timezone
from hashlib import file_digest, sha256
from importlib.util import MAGIC_NUMBER
import json
import os
from pathlib import Path, PurePosixPath
import py_compile
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = "AMR-Desktop-Tray-Windows-x64"
AUTH_PREFIX = "AMR-License-Test-Windows-x64"
AUTH_SHA256 = "28cc92be54ff385380c0e58aa74f77a1fde5a9b1452f5891bf450a716181b3fe"
SQLITE_URL = "https://www.sqlite.org/2026/sqlite-dll-win-x64-3540100.zip"
SQLITE_SHA3 = "c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4"
GO_URL = "https://dl.google.com/go/go1.27.3.darwin-arm64.tar.gz"
GO_SHA256 = "dd3908cf20959d798c1e06ebe5b72213f0505ce01373b40ce3110975a6cd99c2"
CLIENT_FILES = ("__init__.py", "client.py", "protocol.py", "windows.py")
MAINTENANCE_FILES = ("backup.py", "restore.py", "noon_archive.py", "dragon_tiger_archive.py")
CONTRACT_FILES = ("ai-evidence-answer-v2.schema.json", "ai-research-interpretation-v1.schema.json")
LAUNCHER = "AMR-Desktop-Preview.exe"


def run(*args: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def capture(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as file:
        return file_digest(file, algorithm).hexdigest()


def fetch(path: Path, url: str, algorithm: str, expected: str) -> Path:
    if path.is_file():
        if digest(path, algorithm) != expected:
            raise ValueError(f"Checksum mismatch for {path.name}")
        return path
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    os.close(descriptor)
    try:
        run("curl", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https", "--max-time", "600",
            "--output", temporary, url)
        if digest(Path(temporary), algorithm) != expected:
            raise ValueError(f"Checksum mismatch for {path.name}")
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def safe_member(member: zipfile.ZipInfo) -> PurePosixPath:
    name = PurePosixPath(member.filename)
    if (name.is_absolute() or ".." in name.parts or "\\" in member.filename
            or ":" in member.filename or stat.S_ISLNK(member.external_attr >> 16)):
        raise ValueError(f"Unsafe ZIP member: {member.filename}")
    return name


def write_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source, destination.open("xb") as target:
        shutil.copyfileobj(source, target)


def copy_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Expected a regular build input: {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def stage_python(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Expected a regular Python source: {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(source), str(destination.with_suffix(".pyc")),
        dfile=source.relative_to(ROOT).as_posix(), doraise=True, optimize=0,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )


def copy_tree(source: Path, destination: Path, suffixes: set[str] | None = None, *, bytecode: bool = False) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Unexpected build input symlink: {relative}")
        if path.is_file() and (suffixes is None or path.suffix in suffixes):
            if bytecode and path.suffix == ".py":
                stage_python(path, destination / relative)
            else:
                copy_file(path, destination / relative)


def stage_runtime(package: Path, previous: Path, sqlite: Path) -> None:
    if digest(previous) != AUTH_SHA256:
        raise ValueError("The authorization test ZIP differs from the verified source")
    with zipfile.ZipFile(previous) as archive:
        verify_key = archive.read(f"{AUTH_PREFIX}/verify.pem")
        lines = verify_key.strip().splitlines()
        if (len(lines) != 3 or lines[0] != b"-----BEGIN PUBLIC KEY-----"
                or lines[2] != b"-----END PUBLIC KEY-----"):
            raise ValueError("Expected an Ed25519 public key")
        public_der = b64decode(lines[1], validate=True)
        if len(public_der) != 44 or not public_der.startswith(bytes.fromhex("302a300506032b6570032100")):
            raise ValueError("Unexpected public key type")
        (package / "verify.pem").write_bytes(verify_key)
        for member in archive.infolist():
            name = safe_member(member)
            if (name.parts[:2] == (AUTH_PREFIX, "runtime") and len(name.parts) == 3
                    and name.name not in ("sqlite3.dll", "python313._pth")
                    and not member.is_dir()):
                write_member(archive, member, package / "runtime" / name.name)
    with zipfile.ZipFile(package / "runtime" / "python313.zip") as stdlib:
        with stdlib.open("encodings/__init__.pyc") as encoding:
            if encoding.read(4) != MAGIC_NUMBER:
                raise ValueError("The build interpreter and the embedded runtime use different bytecode formats")
    with zipfile.ZipFile(sqlite) as archive:
        member = archive.getinfo("sqlite3.dll")
        safe_member(member)
        write_member(archive, member, package / "runtime" / "sqlite3.dll")
    (package / "runtime" / "python313._pth").write_bytes(
        b"python313.zip\r\n.\r\npackages\r\n..\\backend\r\n..\\scripts\r\n"
    )


def install_packages(package: Path, scratch: Path, wheelhouse: Path | None) -> None:
    requirements = scratch / "requirements.txt"
    target = package / "runtime" / "packages"
    with requirements.open("w", encoding="utf-8") as file:
        subprocess.run(
            ("uv", "export", "--locked", "--no-dev", "--no-emit-project", "--no-header", "--no-annotate",
             "--directory", "backend", "--offline"), cwd=ROOT, stdout=file, check=True,
        )
    install = [
        "uv", "pip", "install", "--no-deps", "--require-hashes", "--only-binary", ":all:",
        "--python-platform", "x86_64-pc-windows-msvc", "--python-version", "3.13",
        "--target", str(target), "-r", str(requirements),
    ]
    if wheelhouse:
        install.extend(("--no-index", "--find-links", str(wheelhouse)))
    run(*install, env={**os.environ, "UV_CACHE_DIR": str(scratch / "uv-cache")})
    # uv generates console scripts for the build host even when wheel tags target Windows.
    if (target / "bin").is_dir():
        shutil.rmtree(target / "bin")
        for record in target.glob("*.dist-info/RECORD"):
            original = record.read_text(encoding="utf-8")
            portable = "".join(line for line in original.splitlines(keepends=True) if not line.startswith("bin/"))
            if original != portable:
                record.write_text(portable, encoding="utf-8")


def build_frontend(package: Path, offline: bool) -> None:
    frontend = ROOT / "frontend"
    run("npm", "ci", "--no-audit", "--no-fund", "--offline" if offline else "--prefer-offline", cwd=frontend)
    run("npm", "run", "build", cwd=frontend,
        env={**os.environ, "VITE_DESKTOP_PREVIEW": "1", "VITE_READONLY_PREVIEW": "0"})
    copy_tree(frontend / "dist", package / "frontend" / "dist")
    lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    notices = package / "licenses" / "frontend"
    for name, dependency in lock["packages"].items():
        if not name.startswith("node_modules/") or dependency.get("dev"):
            continue
        for file in (frontend / name).glob("*"):
            if file.is_file() and file.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                copy_file(file, notices / name.removeprefix("node_modules/") / file.name)


def stage_application(package: Path) -> None:
    for module in ("app", "desktop_preview"):
        copy_tree(ROOT / "backend" / module, package / "backend" / module, {".py", ".sql", ".json"}, bytecode=True)
    if not (package / "backend/desktop_preview/__main__.pyc").is_file():
        raise ValueError("Desktop entrypoint is missing")
    for name in CLIENT_FILES:
        stage_python(ROOT / "backend" / "desktop_licensing" / name, package / "backend" / "desktop_licensing" / name)
    for name in MAINTENANCE_FILES:
        stage_python(ROOT / "scripts" / name, package / "scripts" / name)
    for name in CONTRACT_FILES:
        copy_file(ROOT / "contracts" / name, package / "contracts" / name)
    copy_tree(ROOT / "contracts" / "prompts", package / "contracts" / "prompts", {".md"})
    copy_file(ROOT / "VERSION", package / "VERSION")


def build_launcher(package: Path, scratch: Path, go_archive: Path) -> None:
    with tarfile.open(go_archive) as archive:
        archive.extractall(scratch, filter="data")
    go = scratch / "go"
    env = {
        **os.environ,
        "GOOS": "windows", "GOARCH": "amd64", "GOAMD64": "v1", "GOENV": "off", "GOTOOLCHAIN": "local",
        "GOPROXY": "off", "GOSUMDB": "off", "GOWORK": "off", "CGO_ENABLED": "0",
        "GOFLAGS": "", "GOEXPERIMENT": "",
        "GOCACHE": str(scratch / "go-cache"), "GOPATH": str(scratch / "go-path"),
    }
    run(str(go / "bin" / "go"), "build", "-trimpath", "-ldflags=-s -w -buildid= -H=windowsgui",
        "-o", str(package / LAUNCHER),
        str(ROOT / "scripts" / "windows_desktop_launcher.go"), env=env)
    for filename in ("LICENSE", "PATENTS"):
        copy_file(go / filename, package / "licenses" / f"Go-{filename}.txt")


def verify_binaries(package: Path) -> int:
    count = 0
    for path in package.rglob("*"):
        if path.suffix.lower() not in (".exe", ".dll", ".pyd"):
            continue
        with path.open("rb") as file:
            header = file.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise ValueError(f"Invalid PE header: {path.name}")
            pe_offset = struct.unpack_from("<I", header, 60)[0]
            file.seek(pe_offset)
            if file.read(6) != b"PE\0\0\x64\x86":
                raise ValueError(f"Windows x64 PE required: {path.name}")
            if path == package / LAUNCHER:
                file.seek(pe_offset + 24 + 68)  # IMAGE_OPTIONAL_HEADER64.Subsystem
                if file.read(2) != b"\x02\0":
                    raise ValueError("The desktop launcher must use the Windows GUI subsystem")
        count += 1
    return count


def zip_package(package: Path, output: Path, epoch: int) -> None:
    zip_date = datetime.fromtimestamp(epoch, timezone.utc).timetuple()[:6]
    descriptor, temporary = tempfile.mkstemp(prefix=".amr-windows-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    name = package.name + "/" + path.relative_to(package).as_posix()
                    member = zipfile.ZipInfo(name, zip_date)
                    member.compress_type = zipfile.ZIP_DEFLATED
                    with path.open("rb") as source, archive.open(member, "w") as target:
                        shutil.copyfileobj(source, target)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Windows package archive failed CRC verification")
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-preview", type=Path, required=True, help="Verified Windows authorization ZIP")
    parser.add_argument("--inputs", type=Path, help="Optional pre-fetched go.tar.gz, sqlite-dll.zip and wheels/")
    parser.add_argument("--scratch", type=Path, help="Existing folder for an isolated build")
    parser.add_argument("--npm-offline", action="store_true")
    parser.add_argument("--allow-working-tree", action="store_true", help="Label a dirty checkout as development only")
    args = parser.parse_args()
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13):
        raise ValueError("Build this preview with CPython 3.13")
    output = args.output.expanduser().absolute()
    if output.suffix != ".zip" or output.exists() or output.is_symlink():
        raise ValueError("Choose a new .zip output path")
    dirty = bool(capture("git", "status", "--short", "--untracked-files=normal"))
    if dirty and not args.allow_working_tree:
        raise ValueError("Freeze the source before building a preview, or explicitly label a development build")
    source_commit = capture("git", "rev-parse", "HEAD")
    epoch = int(capture("git", "show", "-s", "--format=%ct", source_commit))
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.scratch or tempfile.gettempdir()).free < 2 * 1024**3:
        raise ValueError("The build directory needs 2 GiB of available space")
    if shutil.disk_usage(output.parent).free < 512 * 1024**2:
        raise ValueError("The output filesystem needs 512 MiB of available space")
    with tempfile.TemporaryDirectory(prefix="amr-windows-build-", dir=args.scratch) as temporary:
        scratch = Path(temporary)
        inputs = args.inputs or scratch
        go = fetch(inputs / "go.tar.gz", GO_URL, "sha256", GO_SHA256)
        sqlite = fetch(inputs / "sqlite-dll.zip", SQLITE_URL, "sha3_256", SQLITE_SHA3)
        package = scratch / ARTIFACT
        package.mkdir()
        stage_runtime(package, args.auth_preview.expanduser(), sqlite)
        wheels = inputs / "wheels"
        install_packages(package, scratch, wheels if wheels.is_dir() else None)
        build_frontend(package, args.npm_offline)
        stage_application(package)
        for directory in ("backend", "scripts", "frontend"):
            if any((package / directory).rglob("*.py")):
                raise ValueError(f"Unexpected first-party Python source in {directory}")
        if any(package.rglob("*.map")):
            raise ValueError("Unexpected source map in the preview package")
        build_launcher(package, scratch, go)
        binary_count = verify_binaries(package)
        (package / "THIRD-PARTY.txt").write_text(
            "CPython 3.13.17 Windows x64: https://www.python.org/downloads/release/python-31317/\n"
            "See runtime/LICENSE.txt and the original wheel licenses under runtime/packages/*.dist-info/.\n"
            "SQLite 3.54.1: https://www.sqlite.org/download.html\n"
            "SQLite is in the public domain: https://www.sqlite.org/copyright.html\n"
            "Go 1.27.3: https://go.dev/dl/ (see licenses/Go-LICENSE.txt and Go-PATENTS.txt).\n"
            "See licenses/frontend/ for frontend dependency notices.\n",
            encoding="utf-8",
        )
        (package / "READ-ME.txt").write_text(
            "冒险的梦想家 Windows x64 托盘预览\n\n"
            "完整解压 ZIP，再双击 AMR-Desktop-Preview.exe。\n"
            "在任务栏通知区域找到工作台图标，图标也可能位于折叠菜单。\n"
            "双击图标，或右键选择「打开工作台」，使用 Windows 默认浏览器打开。\n"
            "关闭浏览器页面后工作台继续运行。右键选择「退出工作台」可安全停止。\n"
            "同一 Windows 用户的有效测试授权可沿用，也可按页面提示导入新授权。\n\n"
            "请先退出旧版。首次打开新版时，若检测到旧版数据，会提示选择迁移或开始新工作台。\n"
            "也可在新版首次正常启动前，于解压目录的 PowerShell 执行以下命令。\n"
            ".\\AMR-Desktop-Preview.exe --import-previous\n"
            "原预览数据保留。按弹窗结果确认完成后，再正常启动新版。\n\n"
            "可在 PowerShell 运行 .\\AMR-Desktop-Preview.exe --diagnose 检查运行环境，结果会在弹窗显示。\n"
            "本包包含可逆向分析的 Python 字节码和编译后的前端文件。\n",
            encoding="utf-8-sig",
        )
        payload = {
            file.relative_to(package).as_posix(): digest(file)
            for file in sorted(package.rglob("*")) if file.is_file()
        }
        build_id = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        manifest = {
            "artifact": "private-windows-desktop-preview", "target": "windows-x64",
            "source_commit": source_commit, "app_version": (ROOT / "VERSION").read_text().strip(),
            "build_id": build_id, "working_tree": dirty, "binary_count": binary_count,
            "python_runtime": "3.13.17", "sqlite_runtime": "3.54.1",
            "first_party_python": "cpython-3.13-bytecode", "launcher_subsystem": "windows-gui",
            "authorization_preview_sha256": AUTH_SHA256, "sqlite_zip_sha3_256": SQLITE_SHA3,
            "go_toolchain_sha256": GO_SHA256, "uv_lock_sha256": digest(ROOT / "backend/uv.lock"),
            "frontend_lock_sha256": digest(ROOT / "frontend/package-lock.json"), "files": payload,
        }
        (package / "BUILD-MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        zip_package(package, output, epoch)
    print(json.dumps({"path": str(output), "sha256": digest(output), "size": output.stat().st_size,
                      "source_commit": source_commit, "build_id": build_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
