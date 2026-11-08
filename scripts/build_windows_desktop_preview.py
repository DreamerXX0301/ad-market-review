#!/usr/bin/env python3
"""Build the private Windows x64 workbench installer or portable ZIP."""

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
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import zipfile

import windows_desktop_native_core as native


ROOT = Path(__file__).resolve().parents[1]
AUTH_PREFIX = "AMR-License-Test-Windows-x64"
AUTH_SHA256 = "28cc92be54ff385380c0e58aa74f77a1fde5a9b1452f5891bf450a716181b3fe"
SQLITE_URL = "https://www.sqlite.org/2026/sqlite-dll-win-x64-3540100.zip"
SQLITE_SHA3 = "c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4"
GO_URL = "https://dl.google.com/go/go1.27.3.darwin-arm64.tar.gz"
GO_SHA256 = "dd3908cf20959d798c1e06ebe5b72213f0505ce01373b40ce3110975a6cd99c2"
CONTRACT_FILES = ("ai-evidence-answer-v2.schema.json", "ai-research-interpretation-v1.schema.json")
LAUNCHER = "AMR-Desktop-Preview.exe"
GUIDE = "使用说明.html"


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


def copy_tree(source: Path, destination: Path, suffixes: set[str] | None = None, *,
              bytecode: bool = False, exclude: frozenset[Path] = frozenset()) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if relative in exclude or any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
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


def build_frontend(package: Path, offline: bool, desktop_version: str) -> dict[str, str]:
    frontend = ROOT / "frontend"
    run("npm", "ci", "--no-audit", "--no-fund", "--offline" if offline else "--prefer-offline", cwd=frontend)
    run("npm", "run", "build", cwd=frontend, env={
        **os.environ,
        "VITE_DESKTOP_PREVIEW": "1",
        "VITE_DESKTOP_VERSION": desktop_version,
        "VITE_READONLY_PREVIEW": "0",
    })
    dist = package / "frontend" / "dist"
    copy_tree(frontend / "dist", dist, exclude=frozenset({Path("author-wechat.jpg")}))
    for asset in dist.rglob("*.js"):
        content = asset.read_bytes()
        if ("联系作者".encode() in content or b"author-wechat.jpg" in content
                or b"\\u8054\\u7cfb\\u4f5c\\u8005" in content.lower()):
            raise ValueError(f"Desktop author contact leaked into {asset.name}")
    lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    notices = package / "licenses" / "frontend"
    for name, dependency in lock["packages"].items():
        if not name.startswith("node_modules/") or dependency.get("dev"):
            continue
        for file in (frontend / name).glob("*"):
            if file.is_file() and file.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                copy_file(file, notices / name.removeprefix("node_modules/") / file.name)
    obfuscator = lock["packages"]["node_modules/javascript-obfuscator"]
    copy_file(frontend / "node_modules/javascript-obfuscator/LICENSE.BSD",
              notices / "javascript-obfuscator/LICENSE.BSD")
    return {"version": obfuscator["version"], "integrity": obfuscator["integrity"]}


def stage_application(package: Path) -> None:
    for module in ("app", "desktop_preview"):
        protected_sources = frozenset(native.module_path(name).relative_to(module).with_suffix(".py")
                                      for name in native.MODULES if name.startswith(f"{module}."))
        copy_tree(ROOT / "backend" / module, package / "backend" / module, {".py", ".sql", ".json"},
                  bytecode=True, exclude=protected_sources)
    if not (package / "backend/desktop_preview/__main__.pyc").is_file():
        raise ValueError("Desktop entrypoint is missing")
    license_init = Path("backend/desktop_licensing/__init__.py")
    stage_python(ROOT / license_init, package / license_init)
    for name in CONTRACT_FILES:
        copy_file(ROOT / "contracts" / name, package / "contracts" / name)
    copy_file(ROOT / "VERSION", package / "VERSION")
    copy_file(ROOT / "DESKTOP_VERSION", package / "DESKTOP_VERSION")


def windows_go_environment(scratch: Path) -> dict[str, str]:
    return {
        **os.environ,
        "GOOS": "windows", "GOARCH": "amd64", "GOAMD64": "v1", "GOENV": "off", "GOTOOLCHAIN": "local",
        "GOPROXY": "off", "GOSUMDB": "off", "GOWORK": "off", "CGO_ENABLED": "0",
        "GOFLAGS": "", "GOEXPERIMENT": "",
        "GOCACHE": str(scratch / "go-cache"), "GOPATH": str(scratch / "go-path"),
    }


def build_launcher(package: Path, scratch: Path, go_archive: Path) -> None:
    with tarfile.open(go_archive) as archive:
        archive.extractall(scratch, filter="data")
    go = scratch / "go"
    run(str(go / "bin" / "go"), "build", "-trimpath", "-ldflags=-s -w -buildid= -H=windowsgui",
        "-o", str(package / LAUNCHER),
        str(ROOT / "scripts" / "windows_desktop_launcher.go"), env=windows_go_environment(scratch))
    for filename in ("LICENSE", "PATENTS"):
        copy_file(go / filename, package / "licenses" / f"Go-{filename}.txt")


def verify_binaries(package: Path, *, gui_name: str = LAUNCHER) -> int:
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
            if path == package / gui_name:
                file.seek(pe_offset + 24 + 68)  # IMAGE_OPTIONAL_HEADER64.Subsystem
                if file.read(2) != b"\x02\0":
                    raise ValueError("The desktop launcher must use the Windows GUI subsystem")
        count += 1
    return count


def verify_extension(path: Path, init_name: str) -> None:
    image = path.read_bytes()
    pe_offset = struct.unpack_from("<I", image, 60)[0]
    if struct.unpack_from("<II", image, pe_offset + 12) != (0, 0):
        raise ValueError(f"Unexpected COFF symbol table for {path.name}")
    optional = pe_offset + 24
    section_count = struct.unpack_from("<H", image, pe_offset + 6)[0]
    section_table = optional + struct.unpack_from("<H", image, pe_offset + 20)[0]
    sections = [struct.unpack_from("<IIII", image, section_table + index * 40 + 8)
                for index in range(section_count)]

    def file_offset(address: int) -> int:
        for _, start, size, offset in sections:
            if start <= address < start + size:
                return offset + address - start
        raise ValueError(f"Unmapped PE address in {path.name}")

    export_rva = struct.unpack_from("<I", image, optional + 112)[0]
    _, _, _, _, _, _, functions, names, _, table, _ = struct.unpack_from(
        "<IIHHIIIIIII", image, file_offset(export_rva),
    )
    if (functions, names) != (1, 1):
        raise ValueError(f"Unexpected PE exports for {path.name}")
    name_rva = struct.unpack_from("<I", image, file_offset(table))[0]
    name_start = file_offset(name_rva)
    if image[name_start:image.index(0, name_start)] != init_name.encode("ascii"):
        raise ValueError(f"Incorrect Python entrypoint for {path.name}")

    debug_rva, debug_size = struct.unpack_from("<II", image, optional + 112 + 6 * 8)
    if debug_rva or debug_size:
        # Zig's deterministic REPRO marker contains zero debug data.
        if debug_size != 28 or struct.unpack_from("<IIII", image, file_offset(debug_rva) + 12) != (16, 0, 0, 0):
            raise ValueError(f"Unexpected PE debug information for {path.name}")


def verify_protected_payload(package: Path) -> None:
    bootstrap = {"desktop_preview/__main__.pyc", "desktop_preview/__init__.pyc", "desktop_licensing/__init__.pyc"}
    for path in (package / "backend").rglob("*.pyc"):
        relative = path.relative_to(package / "backend")
        if relative.as_posix() not in bootstrap and not (relative.is_relative_to("app") and path.name == "__init__.pyc"):
            raise ValueError(f"Unexpected bytecode outside desktop bootstrap: {relative}")
    for name in native.MODULES:
        module = package / native.package_module(name)
        if any(module.with_suffix(suffix).exists() for suffix in (".py", ".pyc")):
            raise ValueError(f"Unexpected Python fallback for {name}")
        verify_extension(module.with_suffix(native.WINDOWS_SUFFIX), f"PyInit_{module.name}")
    if any(path.suffix.lower() in {".c", ".cpp", ".pdb", ".exp", ".lib", ".obj"}
           for directory in ("backend", "scripts") for path in (package / directory).rglob("*")):
        raise ValueError("Unexpected native compiler intermediate in the protected backend")


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


def build_installer(package: Path, output: Path, guide: Path, scratch: Path, epoch: int) -> None:
    source = scratch / "installer-src"
    source.mkdir()
    names = []
    for file in sorted((ROOT / "scripts/windows_installer").glob("*.go")):
        if not file.name.endswith("_test.go"):
            copy_file(file, source / file.name)
            names.append(file.name)
    if not names:
        raise ValueError("Windows installer sources are missing")
    payload = source / "payload.zip"
    zip_package(package, payload, epoch)
    linker = f"-s -w -buildid= -H=windowsgui -X main.payloadSHA256={digest(payload)}"
    with tempfile.TemporaryDirectory(prefix=".amr-setup-", dir=output.parent) as temporary:
        staging = Path(temporary)
        executable = staging / output.name
        run(str(scratch / "go/bin/go"), "build", "-trimpath", f"-ldflags={linker}",
            "-o", str(executable), *names, cwd=source, env=windows_go_environment(scratch))
        if verify_binaries(staging, gui_name=output.name) != 1:
            raise ValueError("Expected a single Windows installer executable")
        copy_file(package / GUIDE, staging / GUIDE)
        os.link(staging / GUIDE, guide)
        try:
            os.link(executable, output)
        except OSError:
            guide.unlink()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New installer .exe or portable .zip")
    parser.add_argument("--auth-preview", type=Path, required=True, help="Verified Windows authorization ZIP")
    parser.add_argument("--inputs", type=Path, help="Optional pre-fetched go.tar.gz, sqlite-dll.zip and wheels/")
    parser.add_argument("--scratch", type=Path, help="Existing folder for an isolated build")
    parser.add_argument("--npm-offline", action="store_true")
    parser.add_argument("--allow-working-tree", action="store_true", help="Label a dirty checkout as development only")
    args = parser.parse_args()
    native.require_host()
    desktop_version = (ROOT / "DESKTOP_VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", desktop_version):
        raise ValueError("Invalid desktop version")
    output = args.output.expanduser().absolute()
    if output.suffix not in {".zip", ".exe"} or output.exists() or output.is_symlink():
        raise ValueError("Choose a new .exe or .zip output path")
    guide = output.with_name(GUIDE) if output.suffix == ".exe" else None
    if guide is not None and (guide.exists() or guide.is_symlink()):
        raise ValueError("Choose a delivery directory without an existing user guide")
    dirty = bool(capture("git", "status", "--short", "--untracked-files=normal"))
    if dirty and (guide is not None or not args.allow_working_tree):
        raise ValueError("Freeze the source before building an installer or release ZIP")
    source_commit = capture("git", "rev-parse", "HEAD")
    epoch = int(capture("git", "show", "-s", "--format=%ct", source_commit))
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.scratch or tempfile.gettempdir()).free < 4 * 1024**3:
        raise ValueError("The build directory needs 4 GiB of available space")
    if shutil.disk_usage(output.parent).free < 512 * 1024**2:
        raise ValueError("The output filesystem needs 512 MiB of available space")
    with tempfile.TemporaryDirectory(prefix="amr-windows-build-", dir=args.scratch) as temporary:
        scratch = Path(temporary)
        inputs = args.inputs or scratch
        go = fetch(inputs / "go.tar.gz", GO_URL, "sha256", GO_SHA256)
        sqlite = fetch(inputs / "sqlite-dll.zip", SQLITE_URL, "sha3_256", SQLITE_SHA3)
        package = scratch / f"AMR-Desktop-{desktop_version}-Windows-x64"
        package.mkdir()
        stage_runtime(package, args.auth_preview.expanduser(), sqlite)
        wheels = inputs / "wheels"
        install_packages(package, scratch, wheels if wheels.is_dir() else None)
        frontend_obfuscation = build_frontend(package, args.npm_offline, desktop_version)
        stage_application(package)
        cython = fetch(inputs / native.CYTHON_WHEEL, native.CYTHON_URL, "sha256", native.CYTHON_SHA256)
        zig = fetch(inputs / "zig.tar.xz", native.ZIG_URL, "sha256", native.ZIG_SHA256)
        sdk = fetch(inputs / "python-win-sdk.zip", native.PYTHON_SDK_URL, "sha256", native.PYTHON_SDK_SHA256)
        native.build_windows_core(package, scratch, cython, zig, sdk)
        for notice, checksum in native.CYTHON_NOTICE_FILES:
            url = f"https://raw.githubusercontent.com/cython/cython/{native.CYTHON_VERSION}/{notice}"
            path = fetch(inputs / f"cython-{notice.lower()}", url, "sha256", checksum)
            copy_file(path, package / "licenses" / f"Cython-{notice}")
        for directory in ("backend", "scripts", "frontend"):
            if any((package / directory).rglob("*.py")):
                raise ValueError(f"Unexpected first-party Python source in {directory}")
        if any(package.rglob("*.map")):
            raise ValueError("Unexpected source map in the preview package")
        copy_file(ROOT / "scripts/assets/windows-tray.ico", package / "tray.ico")
        build_launcher(package, scratch, go)
        binary_count = verify_binaries(package)
        verify_protected_payload(package)
        (package / "THIRD-PARTY.txt").write_text(
            "CPython 3.13.17 Windows x64: https://www.python.org/downloads/release/python-31317/\n"
            "See runtime/LICENSE.txt and the original wheel licenses under runtime/packages/*.dist-info/.\n"
            "SQLite 3.54.1: https://www.sqlite.org/download.html\n"
            "SQLite is in the public domain: https://www.sqlite.org/copyright.html\n"
            "Go 1.27.3: https://go.dev/dl/ (see licenses/Go-LICENSE.txt and Go-PATENTS.txt).\n"
            "See licenses/frontend/ for frontend dependency notices.\n"
            f"javascript-obfuscator {frontend_obfuscation['version']} (build-time, BSD-2-Clause).\n"
            "https://github.com/javascript-obfuscator/javascript-obfuscator\n"
            f"npm integrity {frontend_obfuscation['integrity']}\n"
            f"Cython {native.CYTHON_VERSION}: https://cython.org/ (see licenses/Cython-LICENSE.txt).\n"
            f"Zig {native.ZIG_VERSION}: https://ziglang.org/ (see licenses/Zig-LICENSE.txt and MinGW-COPYING.txt).\n",
            encoding="utf-8",
        )
        copy_file(ROOT / "docs/Windows使用说明.html", package / GUIDE)
        install_help = (
            "双击安装 EXE，选择本机 NTFS 盘上的文件夹。安装完成后会打开工作台。\n"
            "程序位于 AMR-Desktop\\app，业务数据和计算临时文件位于 AMR-Desktop\\workspace。\n"
            "本安装版从空库开始。换装前请退出旧版，原业务文件留在原处。\n"
            if guide else
            "完整解压 ZIP，再双击 AMR-Desktop-Preview.exe。\n"
            "从旧便携版换装时请先退出旧版。首次打开时按界面提示选择是否导入。\n"
        )
        (package / "READ-ME.txt").write_text(
            f"冒险的梦想家 Windows x64 桌面版 {desktop_version}\n\n"
            f"{install_help}"
            "双击同目录的 使用说明.html，可离线查看首次配置、自动采集、全部菜单和图表操作。\n"
            "在任务栏通知区域找到工作台图标，图标也可能位于折叠菜单。\n"
            "双击图标，或右键选择「打开工作台」，使用 Windows 默认浏览器打开。\n"
            "关闭浏览器页面后工作台继续运行。右键选择「退出工作台」可安全停止。\n"
            "找不到托盘图标时，可在程序目录的 PowerShell 运行 .\\AMR-Desktop-Preview.exe --quit 请求安全退出。\n"
            "同一 Windows 用户的有效测试授权可沿用，也可按页面提示导入新授权。\n\n"
            "可在 PowerShell 运行 .\\AMR-Desktop-Preview.exe --diagnose 检查运行环境，结果会在弹窗显示。\n"
            "后端业务、授权和维护模块以原生扩展交付，启动引导保留 Python 字节码，前端提供编译后的文件。"
            "程序仍可被逆向分析。\n",
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
            "desktop_version": desktop_version,
            "build_id": build_id, "working_tree": dirty, "binary_count": binary_count,
            "python_runtime": "3.13.17", "sqlite_runtime": "3.54.1",
            "first_party_python": "cpython-3.13-bytecode-and-native-core",
            "launcher_subsystem": "windows-gui",
            "frontend_obfuscation": frontend_obfuscation,
            "authorization_preview_sha256": AUTH_SHA256, "sqlite_zip_sha3_256": SQLITE_SHA3,
            "go_toolchain_sha256": GO_SHA256, "uv_lock_sha256": digest(ROOT / "backend/uv.lock"),
            "frontend_lock_sha256": digest(ROOT / "frontend/package-lock.json"), "files": payload,
        }
        manifest.update(native_modules=list(native.MODULES), native_toolchain={
            "cython": native.CYTHON_VERSION, "cython_wheel_sha256": native.CYTHON_SHA256,
            "zig": native.ZIG_VERSION, "zig_archive_sha256": native.ZIG_SHA256,
            "python_sdk_sha256": native.PYTHON_SDK_SHA256,
        })
        (package / "BUILD-MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if guide is None:
            zip_package(package, output, epoch)
        else:
            build_installer(package, output, guide, scratch, epoch)
    print(json.dumps({"path": str(output), "sha256": digest(output), "size": output.stat().st_size,
                      "source_commit": source_commit, "build_id": build_id,
                      **({"guide": str(guide), "guide_sha256": digest(guide)} if guide else {})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
