#!/usr/bin/env python3
"""Compile the Windows preview's selected Python modules as native extensions."""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = (
    "desktop_preview.access", "desktop_preview.profile", "desktop_preview.runtime", "desktop_preview.windows_runtime",
    "desktop_licensing.client", "desktop_licensing.protocol", "desktop_licensing.windows",
)
MAINTENANCE = ("backup", "restore", "noon_archive", "dragon_tiger_archive")
CYTHON_VERSION = "3.2.4"
CYTHON_WHEEL = f"cython-{CYTHON_VERSION}-cp313-cp313-macosx_11_0_arm64.whl"
CYTHON_URL = (
    "https://files.pythonhosted.org/packages/18/b5/"
    "1cfca43b7d20a0fdb1eac67313d6bb6b18d18897f82dd0f17436bdd2ba7f/" + CYTHON_WHEEL
)
CYTHON_SHA256 = "28e8075087a59756f2d059273184b8b639fe0f16cf17470bd91c39921bc154e0"
CYTHON_NOTICE_FILES = (
    ("LICENSE.txt", "9568a2b155e66ac3e0ba1fd80b52b827b9460e6cf6f233125e7cbca8e206ddc3"),
    ("COPYING.txt", "e1eb1c49a8508e8173dac30157e4a6439a44ad8846194746c424fbc3fc2b95d7"),
)
ZIG_VERSION = "0.16.0"
ZIG_DIRECTORY = f"zig-aarch64-macos-{ZIG_VERSION}"
ZIG_URL = f"https://ziglang.org/download/{ZIG_VERSION}/{ZIG_DIRECTORY}.tar.xz"
ZIG_SHA256 = "b23d70deaa879b5c2d486ed3316f7eaa53e84acf6fc9cc747de152450d401489"
PYTHON_SDK_URL = "https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.zip"
PYTHON_SDK_SHA256 = "b26ca4b4ee51e157836049934d2b8eabd55939be296f3130254251b6df96ec2f"
WINDOWS_SUFFIX = ".cp313-win_amd64.pyd"
DIRECTIVES = (
    "binding=True,annotation_typing=False,embedsignature=False,"
    "linetrace=False,profile=False,emit_code_comments=False"
)


def module_path(module: str) -> Path:
    return Path(*module.split("."))


def app_modules() -> tuple[str, ...]:
    backend = ROOT / "backend"
    modules = []
    for source in sorted((backend / "app").rglob("*.py")):
        relative = source.relative_to(backend)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if source.is_symlink():
            raise ValueError(f"Unexpected protected source symlink: {relative}")
        if source.name == "__init__.py":
            body = ast.parse(source.read_bytes(), filename=str(relative)).body
            if body and (len(body) != 1 or not isinstance(body[0], ast.Expr)
                         or not isinstance(body[0].value, ast.Constant) or not isinstance(body[0].value.value, str)):
                raise ValueError(f"Package bootstrap contains application code: {relative}")
        else:
            modules.append(".".join(relative.with_suffix("").parts))
    return tuple(modules)


MODULES = (*app_modules(), *WRAPPERS, *MAINTENANCE)


def package_module(module: str) -> Path:
    return Path("scripts" if module in MAINTENANCE else "backend") / module_path(module)


def require_host() -> None:
    if (sys.platform != "darwin" or os.uname().machine != "arm64"
            or sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13)
            or sysconfig.get_config_var("Py_GIL_DISABLED")):
        raise ValueError("The protected preview needs standard CPython 3.13 on an Apple Silicon Mac")


def check_sha256(path: Path, expected: str) -> None:
    with path.open("rb") as file:
        if file_digest(file, "sha256").hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for {path.name}")


def unpack_cython(wheel: Path, target: Path) -> None:
    check_sha256(wheel, CYTHON_SHA256)
    with ZipFile(wheel) as archive:
        archive.extractall(target)


def extract_windows_sdk(archive_path: Path, sdk: Path, runtime_dll: Path) -> None:
    check_sha256(archive_path, PYTHON_SDK_SHA256)
    with ZipFile(archive_path) as archive:
        with archive.open("python313.dll") as dll:
            check_sha256(runtime_dll, file_digest(dll, "sha256").hexdigest())
        for member in archive.infolist():
            name = PurePosixPath(member.filename)
            if member.is_dir() or len(name.parts) < 2:
                continue
            category = name.parts[0].lower()
            if category != "include" and member.filename.lower() != "libs/python313.lib":
                continue
            if (".." in name.parts or "\\" in member.filename or ":" in member.filename
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError(f"Invalid SDK member {member.filename}")
            destination = sdk / category / Path(*name.parts[1:])
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)


def compile_modules(backend: Path, scripts: Path, scratch: Path, cython: Path, suffix: str,
                    compiler: list[str], libraries: list[str], env: dict[str, str]) -> None:
    # Keep all Python copies, generated C, and compiler caches outside the ZIP.
    for name in MODULES:
        relative = module_path(name).with_suffix(".py")
        source = ROOT / package_module(name).with_suffix(".py")
        temporary_source = scratch / relative
        if source.is_symlink():
            raise ValueError(f"Unexpected protected source symlink: {relative}")
        temporary_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, temporary_source)

    def compile_one(name: str) -> None:
        relative = module_path(name)
        c_file = relative.with_suffix(".c")
        target = (scripts if name in MAINTENANCE else backend) / relative.with_suffix(suffix)
        compiled = scratch / "compiled" / relative.with_suffix(suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        compiled.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            sys.executable, "-IB", "-c",
            "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module('cython', run_name='__main__')",
            str(cython), "-3", "--module-name", name, "-X", DIRECTIVES,
            "-o", str(c_file), str(relative.with_suffix(".py")),
        ], cwd=scratch, check=True)
        subprocess.run([*compiler, str(c_file), *libraries, "-o", str(compiled)],
                       cwd=scratch, env=env, check=True)
        shutil.copyfile(compiled, target)
        print(f"Built {name} as {target.name}", flush=True)

    # Warm the Windows compiler's common runtime libraries before using parallel jobs.
    compile_one(MODULES[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(compile_one, MODULES[1:]))


def build_windows_core(package: Path, scratch: Path, cython_wheel: Path,
                       zig_archive: Path, python_sdk: Path) -> None:
    require_host()
    cython, sdk = scratch / "cython", scratch / "python-sdk"
    unpack_cython(cython_wheel, cython)
    check_sha256(zig_archive, ZIG_SHA256)
    with tarfile.open(zig_archive) as archive:
        archive.extractall(scratch, filter="data")
    zig = scratch / ZIG_DIRECTORY
    extract_windows_sdk(python_sdk, sdk, package / "runtime/python313.dll")
    if not (sdk / "include/Python.h").is_file() or not (sdk / "libs/python313.lib").is_file():
        raise ValueError("The Windows SDK is missing CPython headers or its import library")
    compile_modules(
        package / "backend", package / "scripts", scratch, cython, WINDOWS_SUFFIX,
        [str(zig / "zig"), "cc", "-target", "x86_64-windows-gnu", "-shared", "-O2", "-g0", "-DNDEBUG",
         "-s", "-I", str(sdk / "include")],
        [str(sdk / "libs/python313.lib")],
        {**os.environ, "ZIG_LOCAL_CACHE_DIR": str(scratch / "zig-local"),
         "ZIG_GLOBAL_CACHE_DIR": str(scratch / "zig-global")},
    )
    licenses = package / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(zig / "LICENSE", licenses / "Zig-LICENSE.txt")
    shutil.copyfile(zig / "lib/libc/mingw/COPYING", licenses / "MinGW-COPYING.txt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="store_true", required=True, help="Build a local test overlay for macOS")
    parser.add_argument("--cython-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory for the native test overlay")
    parser.add_argument("--scratch", type=Path)
    args = parser.parse_args()
    require_host()
    output = args.output.expanduser().absolute()
    scratch_parent = args.scratch or tempfile.gettempdir()
    if shutil.disk_usage(scratch_parent).free < 1024**3:
        raise ValueError("Native test build needs 1 GiB of available space")
    output.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix="amr-native-host-", dir=args.scratch) as temporary:
            scratch = Path(temporary)
            cython = scratch / "cython"
            unpack_cython(args.cython_wheel.expanduser(), cython)
            suffix = sysconfig.get_config_var("EXT_SUFFIX")
            if not suffix or not re.fullmatch(r"\.cpython-313-[A-Za-z0-9_-]+\.so", suffix):
                raise ValueError("Unexpected macOS extension suffix")
            compile_modules(output, output / "scripts", scratch, cython, suffix,
                            ["/usr/bin/clang", "-bundle", "-undefined", "dynamic_lookup", "-O2", "-g0",
                             "-DNDEBUG", "-Wl,-x", "-I", sysconfig.get_path("include")], [], os.environ.copy())
    except Exception:
        shutil.rmtree(output)
        raise


if __name__ == "__main__":
    main()
