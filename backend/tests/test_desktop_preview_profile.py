"""Tray import checks a closed old profile and only publishes a verified copy."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from zipfile import ZipFile

from cryptography.fernet import Fernet
import pytest

from app import core
from app.infrastructure.file_operations import locked_file
from desktop_preview.profile import LEGACY_COMMIT, import_legacy, roots

_OLD = {"format": 1, "source_commit": LEGACY_COMMIT, "app_version": "0.1.24"}
_NEW = {"format": 1, "source_commit": "a" * 40, "app_version": "0.1.24"}


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    license_dir = tmp_path / "app" / "license-preview-v1" / ("b" * 20)
    old, new = roots(license_dir)
    old.mkdir(parents=True)
    new.parent.mkdir()
    (old / "instance.lock").touch()
    core.atomic_json(old / "release.json", _OLD)

    data = old / "data"
    for name in ("SHIXU_SQLITE_PATH", "SHIXU_PUBLISHED_DIR", "SHIXU_CREDENTIAL_DIR",
                 "SHIXU_RAW_DIR", "SHIXU_WORK_DIR", "SHIXU_CREDENTIAL_MASTER_KEY_FILE",
                 "SHIXU_MASTER_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SHIXU_DATA_DIR", str(data))
    core.ensure_ready()
    core.set_setting("automatic-review-config-v1", {
        "revision": 1, "noon": {"enabled": True, "time": "11:35"},
        "after_close": {"enabled": True, "time": "17:15"},
    })

    key = Fernet.generate_key()
    (old / "secrets").mkdir()
    (old / "secrets/credential-master.key").write_bytes(key)
    (data / "credentials").mkdir()
    (data / "credentials/fuyao.enc").write_bytes(Fernet(key).encrypt(b"test-only-provider-key"))

    (old / "browser-profile").mkdir()
    (old / "browser-profile/sentinel").write_bytes(b"old-browser-session")
    license_dir.mkdir(parents=True)
    (license_dir / "sentinel").write_bytes(b"old-license-state")
    (data / "raw/response").write_bytes(b"original-evidence")
    (data / "work/checkpoint").write_bytes(b"resume-evidence")
    (data / "work/.condition-basis-cache").mkdir()
    (data / "work/.condition-basis-cache/disposable").write_bytes(b"cache")
    (data / "work/worker.lock").touch()

    with core.connect() as connection:
        connection.execute("""INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,
            parameters_hash,status,created_at_ms) VALUES('completed','completed','completed',
            'initialise','{}',?,'succeeded',1)""", ("a" * 64,))
    artifact = data / "published" / "evidence.json"
    artifact.write_bytes(b'{"sealed":true}')
    core.publish_snapshot(
        module="calendar", job_id="completed", start="2026-11-06", end="2026-11-06",
        artifacts=[{"object_key": "evidence.json", "sha256": core.hash_file(artifact),
                    "byte_count": artifact.stat().st_size}],
        snapshot_id="old-calendar",
    )
    return old, new, license_dir


def _contents(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): core.hash_file(path)
        for path in root.rglob("*") if path.is_file()
    }


def _leave_committed_wal(database: Path) -> None:
    script = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("PRAGMA wal_autocheckpoint=0")
db.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES('active','active','active','research','{}',?,'queued',1)", ('b' * 64,))
db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-B", "-c", script, str(database)], check=True, timeout=10)
    assert database.with_name(database.name + "-wal").is_file()


def test_import_preserves_legacy_bytes_and_restores_wal_evidence(profiles):
    old, new, license_dir = profiles
    old_db = old / "data/business/app.sqlite3"
    _leave_committed_wal(old_db)
    before = _contents(old)
    license_before = _contents(license_dir)

    receipt = import_legacy(old, new, _NEW)

    assert _contents(old) == before
    assert _contents(license_dir) == license_before
    assert json.loads((new / "release.json").read_bytes()) == _NEW
    assert (new / "data/raw/response").read_bytes() == b"original-evidence"
    assert (new / "data/work/checkpoint").read_bytes() == b"resume-evidence"
    assert not (new / "data/work/.condition-basis-cache").exists()
    assert not (new / "browser-profile").exists()
    key = (new / "secrets/credential-master.key").read_bytes()
    assert Fernet(key).decrypt((new / "data/credentials/fuyao.enc").read_bytes()) == b"test-only-provider-key"

    with closing(sqlite3.connect(new / "data/business/app.sqlite3")) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (39,)
        assert connection.execute("SELECT status FROM job WHERE job_id='active'").fetchone() == ("paused",)
        assert connection.execute("SELECT snapshot_id FROM published_head").fetchone() == ("old-calendar",)
        automatic = json.loads(connection.execute(
            "SELECT value_json FROM platform_setting WHERE key='automatic-review-config-v1'"
        ).fetchone()[0])
        assert automatic["noon"] == {"enabled": False, "time": "11:35"}
        assert automatic["after_close"] == {"enabled": False, "time": "17:15"}
    archive = new / "migration/recovery.zip"
    assert core.hash_file(archive) == receipt["archive_sha256"]
    assert receipt["paused_jobs"] == 1
    assert receipt["available_bytes_before"] > 0 and receipt["available_bytes_after"] > 0
    with ZipFile(archive) as recovery:
        names = set(recovery.namelist())
        assert "standard/business/app.sqlite3" in names
        assert "extra/raw/response" in names
        assert "extra/work/checkpoint" in names
        assert not any("secrets" in name or "browser-profile" in name or "license" in name for name in names)
        assert recovery.testzip() is None
    assert not list(new.parent.glob(".tray-import-*"))

    with pytest.raises(ValueError, match="全新目录"):
        import_legacy(old, new, _NEW)
    assert core.hash_file(archive) == receipt["archive_sha256"]


def test_import_requires_the_old_instance_to_be_stopped(profiles):
    old, new, _ = profiles
    before = _contents(old)
    with locked_file(old / "instance.lock", exclusive=True):
        with pytest.raises(RuntimeError, match="完整退出"):
            import_legacy(old, new, _NEW)
    assert not new.exists()
    assert _contents(old) == before


def test_failed_evidence_check_never_publishes_tray_data(profiles):
    old, new, _ = profiles
    (old / "data/published/evidence.json").write_bytes(b"changed")
    before = _contents(old)
    with pytest.raises(ValueError, match="hash differs"):
        import_legacy(old, new, _NEW)
    assert not new.exists()
    assert not list(new.parent.glob(".tray-import-*"))
    assert _contents(old) == before


def test_import_rejects_other_old_release_markers(profiles):
    old, new, _ = profiles
    core.atomic_json(old / "release.json", {**_OLD, "source_commit": "b" * 40})
    with pytest.raises(ValueError, match="旧版发行标记"):
        import_legacy(old, new, _NEW)
    assert not new.exists()


def test_import_refuses_master_key_copies_in_archived_data(profiles):
    old, new, _ = profiles
    old_key = (old / "secrets/credential-master.key").read_bytes()
    (old / "data/raw/key-copy").write_bytes(old_key)
    with pytest.raises(ValueError, match="主密钥副本"):
        import_legacy(old, new, _NEW)
    assert not new.exists()
