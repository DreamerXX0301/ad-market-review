from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import shutil
import sqlite3
import sys
from threading import Barrier
import time

import pytest
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from desktop_licensing.__main__ import main as seller_main
from desktop_licensing.client import DesktopClient
from desktop_licensing.ledger import Ledger
from desktop_licensing.protocol import DAY, format_machine_code, parse_machine_code, verify_license


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

    month = ledger.issue("monthly", "paid", machine, signer)
    assert month["expires_at"] == clock.now + 30 * DAY
    assert client.activate(month["code"])["state"] == "active"
    assert client.machine_code() == machine
    with pytest.raises(ValueError, match="早于"):
        client.activate(trial["code"])


def test_daily_card_replaces_trial_and_expires_at_exact_signed_boundary(issuer, tmp_path):
    ledger, signer, clock = issuer
    client = _client(tmp_path / "daily-client", signer, clock)
    machine = client.machine_code()
    trial = ledger.issue("trial", "trial", machine, signer)
    client.activate(trial["code"])
    clock.now += DAY
    daily = ledger.issue("daily", "day", machine, signer)
    assert daily["plan"] == ledger.info("day")["plan"] == "daily"
    assert daily["expires_at"] == clock.now + DAY
    assert daily["expires_at"] < trial["expires_at"]
    assert client.activate(daily["code"])["plan"] == "monthly"
    clock.now = daily["expires_at"] - 1
    assert client.status()["state"] == "active"
    clock.now += 1
    assert client.status()["state"] == "expired"
    assert ledger.issue("daily", "day", machine, signer) == daily
    with pytest.raises(ValueError, match="套餐"):
        ledger.issue("monthly", "day", machine, signer)


def test_new_seven_day_order_replaces_daily_with_prior_trial_and_same_second_issuance(issuer, tmp_path):
    ledger, signer, clock = issuer
    client = _client(tmp_path / "reissue", signer, clock)
    machine = client.machine_code()
    first_trial = ledger.issue("trial", "trial-initial", machine, signer)
    client.activate(first_trial["code"])
    clock.now += 3600
    daily = ledger.issue("daily", "one-day", machine, signer)
    client.activate(daily["code"])
    next_trial = ledger.issue("trial", "trial-reissued", machine, signer)
    assert next_trial["issued_at"] == daily["issued_at"] == clock.now
    assert first_trial["serial"] < daily["serial"] < next_trial["serial"]
    assert next_trial["expires_at"] == next_trial["issued_at"] + 7 * DAY
    activated = client.activate(next_trial["code"])
    assert activated["state"] == "active"
    assert activated["expires_at"] == next_trial["expires_at"]

    clock.now += 3600
    assert ledger.issue("trial", "trial-reissued", machine, signer) == next_trial
    assert client.activate(next_trial["code"]) == activated
    for earlier in (first_trial, daily):
        with pytest.raises(ValueError, match="早于"):
            client.activate(earlier["code"])
    assert client.status() == activated
    clock.now = next_trial["expires_at"]
    assert client.status()["state"] == "expired"
    with pytest.raises(ValueError, match="已经到期"):
        client.activate(next_trial["code"])


def test_daily_cli_exports_legacy_compatible_card_starting_at_issuance(issuer, tmp_path, capsys):
    ledger, signer, clock = issuer
    machine = format_machine_code("A" * 32)
    clock.now = int(time.time()) - DAY
    month = ledger.issue("monthly", "month", machine, signer)
    private = tmp_path / "test-signer.pem"
    private.write_bytes(signer.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    output = tmp_path / "one-day.amr-license"
    assert seller_main([
        "issue", "--db", str(ledger.path), "--private-key", str(private),
        "--plan", "daily", "--order-ref", "day", "--machine-code", machine, "--output", str(output),
    ]) == 0
    issued = json.loads(capsys.readouterr().out)
    assert issued["plan"] == "daily" and "code" not in issued
    assert issued["expires_at"] == issued["issued_at"] + DAY < month["expires_at"]
    signed = verify_license(output.read_text("ascii").strip(), signer.public_key(), parse_machine_code(machine))
    assert signed.plan == "monthly" and signed.expires_at == issued["expires_at"]
    assert seller_main(["info", "--db", str(ledger.path), "--order-ref", "day"]) == 0
    assert json.loads(capsys.readouterr().out)["plan"] == "daily"


def test_daily_issue_migrates_v1_atomically_preserving_orders_and_sequence(tmp_path, monkeypatch):
    from desktop_licensing import ledger as issuer_module

    path = tmp_path / "legacy.sqlite3"
    signer, clock = Ed25519PrivateKey.generate(), Clock()
    key_hash = hashlib.sha256(signer.public_key().public_bytes_raw()).hexdigest()
    machine = "A" * 32
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            PRAGMA foreign_keys=ON;
            PRAGMA recursive_triggers=ON;
            PRAGMA busy_timeout=5000;
            PRAGMA application_id=0x414D524F;
            PRAGMA user_version=1;
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
        """)
        db.execute("INSERT INTO issuer VALUES (1, ?)", (key_hash,))
        db.executemany("INSERT INTO license_issue VALUES (?, ?, ?, 'monthly', ?, ?)", [
            (4, "month", machine, clock.now, clock.now + 40 * DAY),
            (41, "removed", "B" * 32, clock.now, clock.now + 30 * DAY),
        ])
        db.execute("DELETE FROM license_issue WHERE order_ref='removed'")

    ledger = Ledger(path, clock)
    code = format_machine_code(machine)
    old_record = ledger.info("month")
    old_issue = ledger.issue("monthly", "month", code, signer)
    assert old_record["serial"] == 4 and old_issue["serial"] == 4
    before = path.read_bytes()
    with pytest.raises(ValueError, match="台账不匹配"):
        ledger.issue("daily", "wrong-key", code, Ed25519PrivateKey.generate())

    def failed_signature(*args):
        raise RuntimeError("fixture signing failure")

    with monkeypatch.context() as patch:
        patch.setattr(issuer_module, "_signed_issue", failed_signature)
        with pytest.raises(RuntimeError, match="fixture signing failure"):
            ledger.issue("daily", "upgrade", code, signer)
    assert path.read_bytes() == before
    assert ledger.info("month") == old_record

    daily = ledger.issue("daily", "upgrade", code, signer)
    assert daily["serial"] == 42 and daily["expires_at"] == clock.now + DAY
    assert ledger.info("month") == old_record
    assert ledger.issue("monthly", "month", code, signer) == old_issue
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA recursive_triggers=ON")
        db.execute("PRAGMA busy_timeout=5000")
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("SELECT public_key_sha256 FROM issuer").fetchone() == (key_hash,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert "license_issue_machine" in {row[1] for row in db.execute("PRAGMA index_list(license_issue)")}
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO license_issue VALUES (NULL, 'invalid', ?, 'daily', ?, NULL)", (machine, clock.now))


def test_early_renewal_starts_at_issue_time_and_permanent_remains_permanent(issuer, tmp_path):
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
    assert yearly["expires_at"] == yearly["issued_at"] + 365 * DAY == clock.now + 365 * DAY
    client.activate(yearly["code"])
    permanent = ledger.issue("permanent", "permanent", machine, signer)
    assert client.activate(permanent["code"])["expires_at"] is None
    clock.now += 1000 * DAY
    assert client.status()["state"] == "active"
    with pytest.raises(ValueError, match="已有永久授权"):
        ledger.issue("monthly", "unused", machine, signer)
    with pytest.raises(ValueError, match="已有永久授权"):
        ledger.issue("trial", "unused-trial", machine, signer)


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
