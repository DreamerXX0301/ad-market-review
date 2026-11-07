from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
import shutil
import sqlite3
import sys
from threading import Barrier
import time

import pytest
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from desktop_licensing.client import DesktopClient
from desktop_licensing.ledger import Ledger
from desktop_licensing.protocol import DAY, format_machine_code


@dataclass
class Clock:
    now: int = 1_800_000_000

    def __call__(self):
        return self.now


@pytest.fixture
def issuer(tmp_path):
    clock = Clock()
    signer = Ed25519PrivateKey.generate()
    ledger = Ledger(tmp_path / "issuer.sqlite3", clock)
    ledger.initialize(signer)
    return ledger, signer, clock


class TestProtector:
    __test__ = False

    def __init__(self):
        self.cipher = Fernet(Fernet.generate_key())

    def protect(self, data):
        return self.cipher.encrypt(data)

    def unprotect(self, data):
        try:
            return self.cipher.decrypt(data)
        except InvalidToken:
            raise ValueError("test profile cannot decrypt") from None


def _client(directory, signer, clock, protector=None):
    protector = protector or TestProtector()
    return DesktopClient(directory, signer.public_key(), protect=protector.protect,
                         unprotect=protector.unprotect, clock=clock)


@pytest.mark.parametrize("plan,days", [("trial", 7), ("monthly", 30), ("yearly", 365), ("permanent", None)])
def test_offline_terms_use_signed_issue_time_and_different_device_cannot_activate(issuer, tmp_path, plan, days):
    ledger, signer, clock = issuer
    first = _client(tmp_path / "first", signer, clock)
    second = _client(tmp_path / "second", signer, clock)
    first_code, second_code = first.machine_code(), second.machine_code()
    assert first_code != second_code
    order = ledger.issue(plan, "order", first_code.lower(), signer)
    expected_expiry = None if days is None else clock.now + days * DAY
    clock.now += DAY
    result = first.activate(order["code"])
    assert result["state"] == "active"
    assert result["expires_at"] == expected_expiry and result["plan"] == plan
    assert first.activate(order["code"]) == result
    with pytest.raises(ValueError, match="绑定了其他设备"):
        second.activate(order["code"])
    assert second.status() == {"state": "unactivated"}
    assert order["code"].encode() not in ledger.path.read_bytes()
    assert order["code"].encode() not in first.path.read_bytes()


def test_trial_expires_at_boundary_and_new_paid_card_reactivates_same_installation(issuer, tmp_path):
    ledger, signer, clock = issuer
    client = _client(tmp_path / "trial", signer, clock)
    machine = client.machine_code()
    trial = ledger.issue("trial", "initial", machine, signer)
    client.activate(trial["code"])

    clock.now = trial["expires_at"] - 1
    assert client.status()["state"] == "active"
    clock.now += 1
    assert client.status()["state"] == "expired"
    with pytest.raises(ValueError, match="已经到期"):
        client.activate(trial["code"])
    with pytest.raises(ValueError, match="已有试用"):
        ledger.issue("trial", "trial-again", machine, signer)

    month = ledger.issue("monthly", "paid", machine, signer)
    assert month["expires_at"] == clock.now + 30 * DAY
    assert client.activate(month["code"])["state"] == "active"
    assert client.machine_code() == machine
    with pytest.raises(ValueError, match="早于"):
        client.activate(trial["code"])


def test_early_renewal_stacks_paid_time_permanent_remains_permanent(issuer, tmp_path):
    ledger, signer, clock = issuer
    client = _client(tmp_path / "renewal", signer, clock)
    machine = client.machine_code()
    trial = ledger.issue("trial", "trial", machine, signer)
    client.activate(trial["code"])
    clock.now += DAY
    monthly = ledger.issue("monthly", "month", machine, signer)
    assert monthly["expires_at"] == clock.now + 30 * DAY
    client.activate(monthly["code"])
    clock.now += 10 * DAY
    yearly = ledger.issue("yearly", "year", machine, signer)
    assert yearly["expires_at"] == monthly["expires_at"] + 365 * DAY
    client.activate(yearly["code"])
    permanent = ledger.issue("permanent", "permanent", machine, signer)
    assert client.activate(permanent["code"])["expires_at"] is None
    clock.now += 1000 * DAY
    assert client.status()["state"] == "active"
    with pytest.raises(ValueError, match="已有永久授权"):
        ledger.issue("monthly", "unused", machine, signer)


def test_competing_orders_cannot_bind_same_order_twice_and_exact_retry_is_idempotent(issuer):
    ledger, signer, clock = issuer
    gate = Barrier(2)
    machines = [format_machine_code(letter * 32) for letter in ("A", "B")]

    def claim(machine):
        gate.wait(timeout=5)
        try:
            return machine, ledger.issue("monthly", "one-order", machine, signer)
        except ValueError as error:
            return machine, error

    with ThreadPoolExecutor(2) as threads:
        outcomes = list(threads.map(claim, machines))
    assert sum(isinstance(result, dict) for _, result in outcomes) == 1
    assert sum(isinstance(result, ValueError) for _, result in outcomes) == 1
    machine, issued = next((machine, result) for machine, result in outcomes if isinstance(result, dict))
    clock.now += DAY
    assert ledger.issue("monthly", "one-order", machine, signer) == issued
    assert ledger.info("one-order")["issued_at"] == clock.now - DAY


def test_tampered_license_and_wrong_signer_are_rejected(issuer, tmp_path):
    ledger, signer, clock = issuer
    client = _client(tmp_path / "client", signer, clock)
    machine = client.machine_code()
    card = ledger.issue("trial", "intact", machine, signer)
    prefix, body, signature = card["code"].split(".")
    tampered = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(ValueError, match="签名不匹配"):
        client.activate(".".join((prefix, body, tampered)))
    assert client.status() == {"state": "unactivated"}
    with pytest.raises(ValueError, match="台账不匹配"):
        ledger.issue("monthly", "wrong-key", machine, Ed25519PrivateKey.generate())
    with pytest.raises(ValueError, match="找不到"):
        ledger.info("wrong-key")
    client.activate(card["code"])


def test_system_clock_rollback_does_not_extend_trial(issuer, tmp_path):
    ledger, signer, clock = issuer
    client = _client(tmp_path / "client", signer, clock)
    card = ledger.issue("trial", "trial", client.machine_code(), signer)
    client.activate(card["code"])
    clock.now = card["expires_at"]
    assert client.status()["state"] == "expired"
    clock.now -= 60
    assert client.status()["state"] == "expired"
    clock.now -= 600
    assert client.status()["state"] == "clock_check_required"
    with pytest.raises(ValueError, match="校准"):
        client.activate(card["code"])


def test_program_replacement_keeps_machine_and_entitlement_but_copy_fails_for_other_profile(
    issuer, tmp_path, monkeypatch,
):
    ledger, signer, clock = issuer
    protector = TestProtector()
    state_dir = tmp_path / "user-data" / "licensing"
    program_a, program_b = tmp_path / "version-a", tmp_path / "version-b"
    program_a.mkdir()
    program_b.mkdir()

    monkeypatch.chdir(program_a)
    first = _client(state_dir, signer, clock, protector)
    machine = first.machine_code()
    card = ledger.issue("monthly", "before-update", machine, signer)
    active = first.activate(card["code"])

    monkeypatch.chdir(program_b)
    new_program = _client(state_dir, signer, clock, protector)
    assert new_program.machine_code() == machine
    assert new_program.status() == active
    assert not list(program_a.iterdir()) and not list(program_b.iterdir())

    copied = tmp_path / "copied-other-profile"
    shutil.copytree(state_dir, copied)
    other = _client(copied, signer, clock)
    with pytest.raises(ValueError, match="无法解密"):
        other.status()


def test_invalid_import_preserves_current_valid_license(issuer, tmp_path):
    ledger, signer, clock = issuer
    client = _client(tmp_path / "client", signer, clock)
    card = ledger.issue("monthly", "good", client.machine_code(), signer)
    active = client.activate(card["code"])
    with pytest.raises(ValueError, match="授权码无效"):
        client.activate("tampered")
    assert client.status() == active


def test_issuer_refuses_to_initialize_or_issue_into_an_existing_business_database(issuer, tmp_path):
    _, signer, _ = issuer
    path = tmp_path / "business.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA recursive_triggers=ON")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("CREATE TABLE strategy(name TEXT)")
        db.execute("INSERT INTO strategy VALUES ('original')")
    before = path.read_bytes()
    ledger = Ledger(path)
    with pytest.raises(FileExistsError):
        ledger.initialize(signer)
    with pytest.raises(ValueError, match="授权台账"):
        ledger.issue("trial", "bad-path", format_machine_code("A" * 32), signer)
    assert path.read_bytes() == before


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows DPAPI")
def test_windows_dpapi_and_cli_check_disable_expired_trial_then_allow_new_code(tmp_path, monkeypatch, capsys):
    from desktop_licensing.windows import default_state_directory, main, protect, unprotect

    clock = Clock(int(time.time()) - 8 * DAY)
    signer = Ed25519PrivateKey.generate()
    ledger = Ledger(tmp_path / "issuer.sqlite3", clock)
    ledger.initialize(signer)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local app data"))
    state_dir = default_state_directory()
    client = DesktopClient(state_dir, signer.public_key(), protect=protect, unprotect=unprotect, clock=clock)
    machine = client.machine_code()
    trial = ledger.issue("trial", "win-trial", machine, signer)
    assert client.activate(trial["code"])["state"] == "active"

    public = tmp_path / "verify.pem"
    public.write_bytes(signer.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    assert main(["check", "--public-key", str(public)]) == 3
    assert '"state": "expired"' in capsys.readouterr().out
    clock.now = int(time.time())
    paid = ledger.issue("monthly", "win-paid", machine, signer)
    license_file = tmp_path / "test-code.txt"
    license_file.write_text(paid["code"], encoding="utf-8")
    assert main(["activate", "--public-key", str(public), "--license-file", str(license_file)]) == 0
    assert main(["check", "--public-key", str(public)]) == 0
    assert paid["code"].encode() not in client.path.read_bytes()
