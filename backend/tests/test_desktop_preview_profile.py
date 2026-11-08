"""Tray import checks a closed old profile and only publishes a verified copy."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from zipfile import ZipFile

from cryptography.fernet import Fernet
import pytest

from app import core
from app.infrastructure import database as app_db
from app.infrastructure.file_operations import locked_file
from desktop_preview import profile
from desktop_preview.profile import (
    LEGACY_COMMIT, LEGACY_NATIVE_COMMIT, LEGACY_TRAY_COMMIT, import_legacy, roots, select_import_source,
)

_OLD = {"format": 1, "app_version": "0.1.24"}
_NEW = {"format": 1, "source_commit": "a" * 40, "app_version": "0.1.26"}
_PURGE = ("auction-purged", "2026-10-01", "auction-source", "cleanup", "2026-11-06",
          "2026-10-10", "c" * 64, 5, 2, "purged", None, 1, 4, 5)


def _old_lock(old):
    if old.parent.name != "workbench-preview-v1":
        return old.parent / f"instance-{old.name}.lock"
    return old / "instance.lock"


@pytest.fixture(params=("first_preview", "tray", "native_v1"))
def profiles(tmp_path, monkeypatch, request):
    license_dir = tmp_path / "app" / "license-preview-v1" / ("b" * 20)
    preview, tray, native_v1, new = roots(license_dir)
    old, commit = {
        "first_preview": (preview, LEGACY_COMMIT),
        "tray": (tray, LEGACY_TRAY_COMMIT),
        "native_v1": (native_v1, LEGACY_NATIVE_COMMIT),
    }[request.param]
    old.mkdir(parents=True)
    new.parent.mkdir()
    _old_lock(old).touch()
    core.atomic_json(old / "release.json", {**_OLD, "source_commit": commit})

    data = old / "data"
    for name in ("SHIXU_SQLITE_PATH", "SHIXU_PUBLISHED_DIR", "SHIXU_CREDENTIAL_DIR",
                 "SHIXU_RAW_DIR", "SHIXU_WORK_DIR", "SHIXU_CREDENTIAL_MASTER_KEY_FILE",
                 "SHIXU_MASTER_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SHIXU_DATA_DIR", str(data))
    prior_migrations = tmp_path / "old-migrations"
    prior_migrations.mkdir()
    for migration in app_db.MIGRATIONS_DIR.glob("[0-9]*.sql"):
        if int(migration.name.split("_", 1)[0]) <= 39:
            shutil.copyfile(migration, prior_migrations / migration.name)
    with monkeypatch.context() as old_schema:
        old_schema.setattr(app_db, "MIGRATIONS_DIR", prior_migrations)
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
        with core.connect() as connection:
            connection.execute("INSERT INTO business_clock VALUES(1, '2026-11-06', 'old-calendar', 3)")
            connection.executemany(
                """INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,
                    parameters_hash,status,created_at_ms) VALUES(?,?,?,?,?,?,'succeeded',1)""",
                [
                    ("auction-source", "auction-source", "auction-source", "source_check", "{}", "a" * 64),
                    ("cleanup", "cleanup", "cleanup", "increment",
                     '{"auction_retention_policy":"auction-retention-28d-v1"}', "b" * 64),
                ],
            )
            connection.execute("INSERT INTO market_auction_capture VALUES('2026-10-01','auction-source','auction-source',2)")
            connection.create_function("auction_purge_allowed", 1, lambda snapshot: int(snapshot == _PURGE[0]))
            connection.execute("INSERT INTO market_auction_purge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _PURGE)
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


def test_import_preserves_legacy_bytes_and_restores_wal_evidence(profiles, tmp_path, monkeypatch):
    old, new, license_dir = profiles
    old_db = old / "data/business/app.sqlite3"
    _leave_committed_wal(old_db)
    before = _contents(old)
    license_before = _contents(license_dir)
    monkeypatch.setenv("SHIXU_SQLITE_PATH", str(old_db))

    receipt = import_legacy(old, new, _NEW)

    assert core.sqlite_path() == old_db
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
        assert connection.execute("PRAGMA user_version").fetchone() == (40,)
        assert connection.execute("SELECT * FROM market_auction_purge").fetchone() == _PURGE
        assert connection.execute("SELECT business_date FROM business_clock").fetchone() == ("2026-11-06",)
        sha = connection.execute(
            "SELECT sha256 FROM _schema_migration WHERE name='040_auction_retention_30_days.sql'"
        ).fetchone()[0]
        assert sha == core.hash_file(app_db.MIGRATIONS_DIR / "040_auction_retention_30_days.sql")
        assert sha == receipt["migration_sha256"]
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
    assert receipt["source_schema"] == 39 and receipt["schema"] == 40
    assert receipt["business_rows"]["market_auction_purge"] == 1
    old_commit = json.loads((old / "release.json").read_bytes())["source_commit"]
    assert receipt["source_commit"] == old_commit
    assert receipt["source_profile"] == old.parent.name
    assert receipt["available_bytes_before"] > 0 and receipt["available_bytes_after"] > 0
    with ZipFile(archive) as recovery:
        names = set(recovery.namelist())
        assert json.loads(recovery.read("recovery-manifest.json"))["source_commit"] == old_commit
        assert "standard/business/app.sqlite3" in names
        assert "extra/raw/response" in names
        assert "extra/work/checkpoint" in names
        assert not any("secrets" in name or "browser-profile" in name or "license" in name for name in names)
        assert recovery.testzip() is None
        old_copy = tmp_path / "rollback-check.sqlite3"
        old_copy.write_bytes(recovery.read("standard/business/app.sqlite3"))
    with closing(sqlite3.connect(old_copy)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (39,)
        assert connection.execute("SELECT status FROM job WHERE job_id='active'").fetchone() == ("queued",)
        assert connection.execute("SELECT * FROM market_auction_purge").fetchone() == _PURGE
    assert not list(new.parent.glob(".tray-import-*"))

    with pytest.raises(ValueError, match="全新目录"):
        import_legacy(old, new, _NEW)
    assert core.hash_file(archive) == receipt["archive_sha256"]


def test_import_requires_the_old_instance_to_be_stopped(profiles):
    old, new, _ = profiles
    before = _contents(old)
    with locked_file(_old_lock(old), exclusive=True):
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


def _fixture_database(root, commit):
    database = root / "data/business/app.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch()
    core.atomic_json(root / "release.json", {**_OLD, "source_commit": commit})
    return database


def test_source_selection_prefers_native_then_tray_then_first_preview(tmp_path):
    preview, tray, native_v1, native_v2 = roots(tmp_path / "app/license-preview-v1" / ("b" * 20))
    assert select_import_source(preview, tray, native_v1) is None
    assert native_v2.parent.name == "workbench-tray-native-v2"

    _fixture_database(preview, LEGACY_COMMIT)
    assert select_import_source(preview, tray, native_v1) == preview

    (tray / "data/business").mkdir(parents=True)
    assert select_import_source(preview, tray, native_v1) == preview
    _fixture_database(tray, LEGACY_TRAY_COMMIT)
    assert select_import_source(preview, tray, native_v1) == tray

    _fixture_database(native_v1, LEGACY_NATIVE_COMMIT)
    assert select_import_source(preview, tray, native_v1) == native_v1
    assert not native_v2.parent.exists()


def test_source_selection_refuses_incomplete_or_mismatched_newest_data(tmp_path):
    preview, tray, native_v1, _ = roots(tmp_path / "app/license-preview-v1" / ("b" * 20))
    _fixture_database(preview, LEGACY_COMMIT)
    _fixture_database(tray, LEGACY_TRAY_COMMIT)
    newest_business = native_v1 / "data/business"
    newest_business.mkdir(parents=True)
    (newest_business / "app.sqlite3-wal").touch()

    with pytest.raises(ValueError, match="缺少主文件"):
        select_import_source(preview, tray, native_v1)
    _fixture_database(native_v1, LEGACY_TRAY_COMMIT)
    with pytest.raises(ValueError, match="旧版发行标记"):
        select_import_source(preview, tray, native_v1)


def test_wrong_migration_prefix_never_publishes(profiles):
    old, new, _ = profiles
    database = old / "data/business/app.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE _schema_migration SET sha256=? WHERE name LIKE '039_%'", ("d" * 64,))
    before = _contents(old)
    with pytest.raises(ValueError, match="迁移登记或 SHA"):
        import_legacy(old, new, _NEW)
    assert not new.exists()
    assert not list(new.parent.glob(".tray-import-*"))
    assert _contents(old) == before


def test_interrupted_migration_leaves_old_profile_and_allows_retry(profiles, monkeypatch):
    old, new, _ = profiles
    before = _contents(old)
    real_ensure = profile.ensure_ready

    def interrupt_after_migration():
        database = core.sqlite_path()
        assert database.is_relative_to(new.parent) and not database.is_relative_to(old)
        real_ensure()
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (40,)
        raise OSError("synthetic migration interruption")

    with monkeypatch.context() as failure:
        failure.setattr(profile, "ensure_ready", interrupt_after_migration)
        with pytest.raises(OSError, match="synthetic migration interruption"):
            import_legacy(old, new, _NEW)
    assert not new.exists()
    assert not list(new.parent.glob(".tray-import-*"))
    assert _contents(old) == before
    assert import_legacy(old, new, _NEW)["schema"] == 40
    assert _contents(old) == before
