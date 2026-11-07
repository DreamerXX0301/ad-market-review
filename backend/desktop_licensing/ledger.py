"""Offline issuer. Only the seller operates this separate SQLite ledger."""
from __future__ import annotations

import base64
from contextlib import closing, contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .protocol import DAY, License, PLAN_DAYS, SIGNATURE_DOMAIN, parse_machine_code

_APPLICATION_ID = 0x414D524F
_SCHEMA = """
CREATE TABLE issuer (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    public_key_sha256 TEXT NOT NULL
);
CREATE TABLE license_issue (
    serial INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ref TEXT NOT NULL UNIQUE,
    machine TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('trial','monthly','yearly','permanent')),
    issued_at INTEGER NOT NULL,
    expires_at INTEGER,
    CHECK ((plan = 'permanent') = (expires_at IS NULL)),
    CHECK (expires_at IS NULL OR expires_at > issued_at)
);
CREATE INDEX license_issue_machine ON license_issue(machine, serial DESC);
"""


def _connect(path: Path) -> sqlite3.Connection:
    # The issuer uses rollback journaling and never opens a missing file.
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
    except BaseException:
        connection.close()
        raise
    return connection


def _key_id(signer: Ed25519PrivateKey) -> str:
    return hashlib.sha256(signer.public_key().public_bytes_raw()).hexdigest()


def _license(row) -> License:
    return License(serial=row["serial"], machine=row["machine"], plan=row["plan"],
                   issued_at=row["issued_at"], expires_at=row["expires_at"])


def _signed_issue(row, signer: Ed25519PrivateKey) -> dict:
    license = _license(row)
    payload = license.model_dump_json().encode("utf-8")
    signature = signer.sign(SIGNATURE_DOMAIN + payload)
    encoded_payload, encoded_signature = (
        base64.urlsafe_b64encode(part).decode("ascii") for part in (payload, signature)
    )
    return {
        "order_ref": row["order_ref"], **license.model_dump(),
        "code": f"AMR-L1.{encoded_payload}.{encoded_signature}",
    }


class Ledger:
    def __init__(self, path: Path, clock=time.time):
        self.path = path.resolve()
        self.clock = clock

    def initialize(self, signer: Ed25519PrivateKey) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        try:
            with closing(_connect(self.path)) as db, db:
                db.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA)
                db.execute("INSERT INTO issuer VALUES (1, ?)", (_key_id(signer),))
                db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                db.execute("PRAGMA user_version=1")
        except BaseException:
            self.path.unlink()
            raise

    @contextmanager
    def _database(self, *, write=False):
        with closing(_connect(self.path)) as db:
            if (
                db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != 1
            ):
                raise ValueError("文件不是受支持的离线授权台账")
            if write:
                db.execute("BEGIN IMMEDIATE")
            with db:
                yield db

    def issue(self, plan: str, order_ref: str, machine_code: str, signer: Ed25519PrivateKey) -> dict:
        if plan not in PLAN_DAYS:
            raise ValueError("授权类型无效")
        order_ref = order_ref.strip()
        if not 1 <= len(order_ref) <= 80 or not order_ref.isprintable():
            raise ValueError("订单编号需为 1 至 80 个可显示字符")
        machine = parse_machine_code(machine_code)
        with self._database(write=True) as db:
            key = db.execute("SELECT public_key_sha256 FROM issuer WHERE singleton=1").fetchone()
            if key is None or key[0] != _key_id(signer):
                raise ValueError("签名私钥与台账不匹配")
            existing = db.execute("SELECT * FROM license_issue WHERE order_ref=?", (order_ref,)).fetchone()
            if existing is not None:
                if existing["machine"] != machine or existing["plan"] != plan:
                    raise ValueError("订单已为另一机器码或套餐签发，请核对原记录")
                return _signed_issue(existing, signer)

            previous = db.execute(
                "SELECT * FROM license_issue WHERE machine=? ORDER BY serial DESC LIMIT 1", (machine,)
            ).fetchone()
            now = int(self.clock())
            if previous is not None and now < previous["issued_at"]:
                raise ValueError("签发电脑的时间早于上次记录，请校准系统时间")
            if previous is not None and plan == "trial":
                raise ValueError("这台电脑已有试用或正式授权")
            if previous is not None and previous["plan"] == "permanent":
                raise ValueError("这台电脑已有永久授权")

            start = now
            if previous is not None and previous["plan"] != "trial":
                start = max(start, previous["expires_at"])
            days = PLAN_DAYS[plan]
            expires_at = None if days is None else start + days * DAY
            serial = db.execute(
                "INSERT INTO license_issue(order_ref, machine, plan, issued_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (order_ref, machine, plan, now, expires_at),
            ).lastrowid
            row = db.execute("SELECT * FROM license_issue WHERE serial=?", (serial,)).fetchone()
            return _signed_issue(row, signer)

    def info(self, order_ref: str) -> dict:
        with self._database() as db:
            row = db.execute("SELECT * FROM license_issue WHERE order_ref=?", (order_ref,)).fetchone()
            if row is None:
                raise ValueError("找不到该订单的签发记录")
            return dict(row)
