"""Offline Windows activation preview with current-user DPAPI state."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import getpass
import json
import os
from pathlib import Path
import sys
import warnings

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .client import DesktopClient
from .protocol import MAX_CODE_LENGTH


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def default_state_directory():
    if sys.platform != "win32":
        raise ValueError("设备绑定客户端用于 Windows")
    root = os.environ.get("LOCALAPPDATA")
    if not root or not Path(root).is_absolute():
        raise ValueError("Windows 本地应用数据目录不可用")
    return Path(root) / "ad-market-review" / "licensing"


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise ValueError("Windows 设备凭据保护不可用")
    crypt = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    local_free = ctypes.WinDLL("kernel32.dll", use_last_error=True).LocalFree
    local_free.argtypes = (wintypes.HLOCAL,)
    local_free.restype = wintypes.HLOCAL

    api = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    # The optional description for UnprotectData is always NULL.
    description = wintypes.LPCWSTR if encrypt else ctypes.POINTER(wintypes.LPWSTR)
    api.argtypes = (
        ctypes.POINTER(_DataBlob), description, ctypes.POINTER(_DataBlob),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    )
    api.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _DataBlob()
    try:
        # CRYPTPROTECT_UI_FORBIDDEN. Deliberately omit LOCAL_MACHINE to retain
        # the current user's protection boundary.
        if not api(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            ctypes.memset(result.data, 0, result.size)
            local_free(ctypes.cast(result.data, wintypes.HLOCAL))
    finally:
        ctypes.memset(buffer, 0, len(buffer))


def protect(data: bytes) -> bytes:
    return _dpapi(data, encrypt=True)


def unprotect(data: bytes) -> bytes:
    return _dpapi(data, encrypt=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("machine-code", "activate", "status", "check"))
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--license-file", type=Path)
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        directory = args.state_dir or default_state_directory()
        if sys.platform != "win32":
            raise ValueError("设备绑定客户端用于 Windows")
        key = None
        if args.action != "machine-code":
            if args.public_key is None:
                raise ValueError("需要授权验签公钥文件")
            key = serialization.load_pem_public_key(args.public_key.read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("需要 Ed25519 授权验签公钥")
        client = DesktopClient(directory, key, protect=protect, unprotect=unprotect)
        if args.action == "machine-code":
            result = {"machine_code": client.machine_code()}
        elif args.action == "activate":
            if args.license_file is not None:
                with args.license_file.open("r", encoding="utf-8-sig") as reader:
                    code = reader.read(MAX_CODE_LENGTH + 16)
                    if reader.read(1):
                        raise ValueError("授权文件过大")
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    code = getpass.getpass("请粘贴授权码（输入隐藏）：")
            result = client.activate(code)
        else:
            result = client.status()
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, getpass.GetPassWarning, EOFError):
        print("授权操作失败，请检查本机凭据和终端", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    if args.action == "check" and result["state"] != "active":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
