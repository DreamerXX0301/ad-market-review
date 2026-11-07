"""Local machine identity and offline authorization state."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import tempfile
import time
from typing import Annotated, Literal

from pydantic import Field

from .protocol import CLOCK_TOLERANCE, MAX_CODE_LENGTH, Message, Timestamp, format_machine_code, machine_tag, verify_license


class _DeviceState(Message):
    version: Literal[1] = 1
    device_token: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    code: Annotated[str, Field(max_length=MAX_CODE_LENGTH)] | None = None
    last_seen: Timestamp = 0


@contextmanager
def _state_lock(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "device-license.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("授权状态暂不可用，请关闭其他激活窗口后重试") from None
        yield


class DesktopClient:
    def __init__(self, directory, public_key, *, protect, unprotect, clock=time.time):
        self.directory = Path(directory)
        self.path = self.directory / "device.enc"
        self.public_key = public_key
        self.protect = protect
        self.unprotect = unprotect
        self.clock = clock

    def _read(self):
        try:
            with self.path.open("rb") as reader:
                encrypted = reader.read(16385)
        except FileNotFoundError:
            return None
        try:
            if len(encrypted) > 16384:
                raise ValueError
            return _DeviceState.model_validate_json(self.unprotect(encrypted))
        except (OSError, ValueError):
            raise ValueError("授权状态无法解密，请使用原 Windows 用户或联系卖方") from None

    def _write(self, state):
        encrypted = self.protect(state.model_dump_json().encode("utf-8"))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix="device-", suffix=".enc",
                                             delete=False) as writer:
                temporary = Path(writer.name)
                writer.write(encrypted)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def machine_code(self) -> str:
        with _state_lock(self.directory):
            state = self._read()
            if state is None:
                state = _DeviceState(device_token=secrets.token_hex(32))
                self._write(state)
            return format_machine_code(machine_tag(state.device_token))

    def _status(self, state, license, now):
        phase = "active"
        trusted_time = max(state.last_seen, license.issued_at)
        if now < trusted_time - CLOCK_TOLERANCE:
            phase = "clock_check_required"
        elif license.expires_at is not None and max(now, trusted_time) >= license.expires_at:
            phase = "expired"
        return {
            "state": phase, "serial": license.serial, "plan": license.plan,
            "issued_at": license.issued_at, "expires_at": license.expires_at,
        }

    def activate(self, code: str) -> dict:
        with _state_lock(self.directory):
            state = self._read()
            if state is None:
                raise ValueError("请先生成机器码，再向卖方申请授权")
            expected = machine_tag(state.device_token)
            code = code.strip()
            license = verify_license(code, self.public_key, expected)
            if state.code is not None:
                current = verify_license(state.code, self.public_key, expected)
                if license.serial < current.serial or (license.serial == current.serial and license != current):
                    raise ValueError("这份授权早于本机当前授权")
            now = int(self.clock())
            result = self._status(state, license, now)
            if result["state"] == "clock_check_required":
                raise ValueError("请校准电脑的系统时间")
            if result["state"] == "expired":
                raise ValueError("这份授权已经到期，请向卖方申请新授权")
            state.code = code
            state.last_seen = max(state.last_seen, now, license.issued_at)
            self._write(state)
            return result

    def status(self) -> dict:
        with _state_lock(self.directory):
            state = self._read()
            if state is None or state.code is None:
                return {"state": "unactivated"}
            license = verify_license(state.code, self.public_key, machine_tag(state.device_token))
            now = int(self.clock())
            result = self._status(state, license, now)
            if now > state.last_seen:
                state.last_seen = now
                self._write(state)
            return result
