"""Interactive Mac seller tool for the private Windows preview."""

from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from desktop_licensing.ledger import Ledger
from desktop_licensing.protocol import parse_machine_code

_OPTIONS = {
    "1": ("daily", "1 天卡（24 小时）"),
    "2": ("trial", "7 天试用"),
    "3": ("monthly", "30 天包月"),
    "4": ("yearly", "365 天包年"),
    "5": ("permanent", "永久"),
}


def _preserve_v1_ledger(database: Path) -> None:
    """Keep an independently checked SQLite snapshot before the first migration."""
    recovery = database.with_name("test-issuer-v1-recovery.sqlite3")
    temporary = None
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA user_version").fetchone()[0] != 1:
                return
            with NamedTemporaryFile(prefix=".issuer-backup-", dir=database.parent, delete=False) as file:
                temporary = Path(file.name)
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
                if target.execute("PRAGMA user_version").fetchone()[0] != 1:
                    return
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("旧版发卡台账备份校验失败")
            with temporary.open("rb") as file:
                os.fsync(file.fileno())
            os.replace(temporary, recovery)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def issue_card(root: Path, out: Path, machine: str, plan: str) -> tuple[Path, int | None]:
    tag = parse_machine_code(machine)
    signer = serialization.load_pem_private_key((root / "test-signing.pem").read_bytes(), password=None)
    if not isinstance(signer, Ed25519PrivateKey):
        raise ValueError("测试发卡密钥无效")
    database = root / "test-issuer.sqlite3"
    if plan == "daily":
        _preserve_v1_ledger(database)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    reference = f"test-trial-{tag}" if plan == "trial" else f"test-{uuid.uuid4().hex}"
    result = Ledger(database).issue(plan, reference, machine, signer)
    destination = out / f"AMR-Test-{plan}-{result['serial']:04d}.amr-license"
    contents = (result["code"] + "\n").encode("ascii")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if destination.read_bytes() != contents:
            raise ValueError("目标文件已有其他授权，请保留原文件后重试") from None
    else:
        with os.fdopen(descriptor, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
    return destination, result["expires_at"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print("冒险的梦想家 · Mac 测试发卡")
    try:
        machine = input("粘贴 Windows 授权页显示的机器码，再按回车\n").strip()
        for number, (_, label) in _OPTIONS.items():
            print(number, label)
        selected = input("选择类型，直接回车选 2（7 天试用） ").strip() or "2"
        if selected not in _OPTIONS:
            raise ValueError("请选择 1 到 5")
        path, expiry = issue_card(args.state_dir.resolve(), args.output_dir, machine, _OPTIONS[selected][0])
        print("\n授权文件已生成")
        print(path)
        print("到期", "长期有效" if expiry is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expiry)))
        print("把 .amr-license 文件传到 Windows，在工作台的「授权管理」中导入。")
    except (EOFError, KeyboardInterrupt):
        return 0
    except ValueError as error:
        print(error)
        return 2
    except (OSError, sqlite3.Error):
        print("测试发卡资料无法读取或写入，请核对本机发卡工具与下载目录。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
