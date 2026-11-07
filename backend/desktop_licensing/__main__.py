"""Seller tool for signing offline Windows licenses."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .ledger import Ledger, SELLER_PLAN_DAYS


def _private_key(path):
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("需要 Ed25519 授权签名私钥")
    return key


def _print_public(key):
    print(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii"), end="")


def _seller(args):
    if args.action == "keygen":
        key = Ed25519PrivateKey.generate()
        descriptor = os.open(args.private_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
            ))
            file.flush()
            os.fsync(file.fileno())
        _print_public(key)
        return None
    if args.action == "public-key":
        _print_public(_private_key(args.private_key))
        return None

    ledger = Ledger(args.db)
    if args.action == "info":
        return ledger.info(args.order_ref)
    signer = _private_key(args.private_key)
    if args.action == "init":
        ledger.initialize(signer)
        return {"status": "initialized"}
    if args.output is not None and args.output.suffix != ".amr-license":
        raise ValueError("授权文件须使用 .amr-license 扩展名")
    result = ledger.issue(args.plan, args.order_ref, args.machine_code, signer)
    if args.output is not None:
        with args.output.open("x", encoding="ascii") as file:
            file.write(result.pop("code") + "\n")
        result["license_file"] = str(args.output)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("keygen", "public-key"):
        actions.add_parser(name).add_argument("--private-key", type=Path, required=True)
    for name in ("init", "issue"):
        command = actions.add_parser(name)
        command.add_argument("--db", type=Path, required=True)
        command.add_argument("--private-key", type=Path, required=True)
        if name == "issue":
            command.add_argument("--plan", choices=tuple(SELLER_PLAN_DAYS), required=True)
            command.add_argument("--order-ref", required=True)
            command.add_argument("--machine-code", required=True)
            command.add_argument("--output", type=Path, help="保存为新 .amr-license 文件")
    info = actions.add_parser("info")
    info.add_argument("--db", type=Path, required=True)
    info.add_argument("--order-ref", required=True)
    args = parser.parse_args(argv)
    try:
        result = _seller(args)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, TypeError):
        print("授权操作失败，请检查授权台账和签名文件", file=sys.stderr)
        return 2
    if result is not None:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
