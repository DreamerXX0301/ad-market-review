import json
import os
from hashlib import sha256
from pathlib import Path
import tempfile
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.infrastructure.config import APP_VERSION
from desktop_licensing.ledger import Ledger
from desktop_preview import runtime


def _record(package, commit):
    (package / "BUILD-MANIFEST.json").write_text(json.dumps({
        "app_version": APP_VERSION, "source_commit": commit,
    }), encoding="utf-8")


def test_existing_build_preserves_external_data_and_original_key(tmp_path, monkeypatch):
    package, installation = tmp_path / "package", tmp_path / "installation"
    package.mkdir()
    installation.mkdir()
    _record(package, "a" * 40)
    monkeypatch.setattr(os, "environ", {**os.environ, "SHIXU_PUBLISHED_DIR": "/wrong-root"})

    runtime._configure_instance(installation, package)
    key = installation / "secrets" / "credential-master.key"
    original = key.read_bytes()
    database = installation / "data" / "business" / "app.sqlite3"
    database.write_bytes(b"existing database")
    runtime._configure_instance(installation, package)

    assert key.read_bytes() == original
    assert database.read_bytes() == b"existing database"
    assert os.environ["SHIXU_DATA_DIR"] == str(installation / "data")
    assert os.environ["SHIXU_CREDENTIAL_MASTER_KEY_FILE"] == str(key)
    assert "SHIXU_PUBLISHED_DIR" not in os.environ
    assert not any(package.rglob("*.sqlite3"))
    assert not any(package.rglob("*.key"))


def test_different_build_and_missing_key_cannot_take_over_existing_data(tmp_path, monkeypatch):
    package, installation = tmp_path / "package", tmp_path / "installation"
    package.mkdir()
    installation.mkdir()
    monkeypatch.setattr(os, "environ", dict(os.environ))
    _record(package, "a" * 40)
    runtime._configure_instance(installation, package)
    database = installation / "data" / "business" / "app.sqlite3"
    database.write_bytes(b"existing database")
    _record(package, "b" * 40)

    with pytest.raises(ValueError, match="不同构建"):
        runtime._configure_instance(installation, package)
    assert database.read_bytes() == b"existing database"
    assert json.loads((installation / "release.json").read_text())["source_commit"] == "a" * 40

    key = installation / "secrets" / "credential-master.key"
    key.unlink()
    _record(package, "a" * 40)
    with pytest.raises(ValueError, match="原主密钥"):
        runtime._configure_instance(installation, package)
    assert not key.exists()
    assert database.read_bytes() == b"existing database"


def test_same_windows_profile_uses_the_existing_preview_authorization(tmp_path, monkeypatch):
    public = Ed25519PrivateKey.generate().public_key()
    (tmp_path / "verify.pem").write_bytes(public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    user_root = tmp_path / "Local" / "ad-market-review"
    monkeypatch.setattr(runtime, "default_state_directory", lambda: user_root / "licensing")
    client = runtime._license_client(tmp_path)
    assert client.directory == user_root / "license-preview-v1" / sha256(public.public_bytes_raw()).hexdigest()[:20]


def test_install_starts_clean_on_selected_volume_with_same_license_identity(tmp_path, monkeypatch):
    package = tmp_path / "selected" / "AMR-Desktop" / "app"
    package.mkdir(parents=True)
    (package / "AMR-INSTALLATION.json").write_text('{"format":1}', encoding="utf-8")
    _record(package, "a" * 40)
    signer = Ed25519PrivateKey.generate()
    public = signer.public_key()
    (package / "verify.pem").write_bytes(public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    fingerprint = sha256(public.public_bytes_raw()).hexdigest()[:20]
    local = tmp_path / "other-volume" / "Local"
    base = local / "ad-market-review"
    old_data = base / "workbench-tray-native-v2" / fingerprint / "data"
    old_data.mkdir(parents=True)
    old_file = old_data / "previous-work.txt"
    old_file.write_bytes(b"prior data")
    old_temp = tmp_path / "old-temp"
    old_temp.mkdir()
    monkeypatch.setattr(os, "environ", {
        **os.environ, "TEMP": str(old_temp), "SHIXU_DATA_DIR": str(old_data),
    })
    monkeypatch.setattr(tempfile, "tempdir", str(old_temp))
    monkeypatch.setattr(runtime, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(runtime, "default_state_directory", lambda: base / "licensing")
    monkeypatch.setattr(runtime, "_install_roots", lambda *_: pytest.fail("legacy setup called"))
    protector = Fernet(Fernet.generate_key())
    monkeypatch.setattr(runtime, "protect", protector.encrypt)
    monkeypatch.setattr(runtime, "unprotect", protector.decrypt)
    existing = runtime._license_client(package)
    machine = existing.machine_code()
    ledger = Ledger(tmp_path / "issuer.sqlite3")
    ledger.initialize(signer)
    card = ledger.issue("permanent", "fixture", machine, signer)
    assert existing.activate(card["code"])["state"] == "active"
    checked = []
    monkeypatch.setattr(runtime, "require_local_ntfs", checked.append)
    launched = []

    def serve(actual_package, profile, client):
        assert actual_package == package
        assert client.directory == base / "license-preview-v1" / fingerprint
        assert client.machine_code() == machine
        assert client.status()["state"] == "active"
        assert profile.name == fingerprint
        assert profile.parent.parent == package.parent / "workspace"
        data = profile / "data"
        work = data / "work"
        scratch = work / "tmp"
        assert Path.cwd() == scratch
        assert os.environ["SHIXU_DATA_DIR"] == str(data)
        assert os.environ["SHIXU_CREDENTIAL_MASTER_KEY_FILE"] == str(profile / "secrets/credential-master.key")
        assert os.environ["SQLITE_TMPDIR"] == str(work)
        assert {os.environ[name] for name in ("TEMP", "TMP", "TMPDIR")} == {str(scratch)}
        assert tempfile.gettempdir() == str(scratch)
        with tempfile.TemporaryDirectory() as created:
            assert Path(created).parent == scratch
        assert not any((data / "business").iterdir())
        launched.append(profile)

    monkeypatch.setattr(runtime, "_serve", serve)
    before = Path.cwd()
    runtime.run_preview(package)
    assert Path.cwd() == before
    assert os.environ["TEMP"] == str(old_temp)
    assert tempfile.tempdir == str(old_temp)
    assert checked == [package, local]
    assert launched and old_file.read_bytes() == b"prior data"
    with pytest.raises(ValueError, match="独立工作台"):
        runtime.run_preview(package, import_previous=True)


@pytest.mark.parametrize("marker", [None, '{"format":true}', '{"format":1,"data":"C:/other"}'])
def test_installed_layout_fails_closed_with_missing_or_invalid_marker(tmp_path, monkeypatch, marker):
    package = tmp_path / "AMR-Desktop" / "app"
    package.mkdir(parents=True)
    if marker is not None:
        (package / "AMR-INSTALLATION.json").write_text(marker, encoding="utf-8")
    monkeypatch.setattr(runtime, "require_local_ntfs", lambda _: None)
    with pytest.raises(ValueError, match="安装标记"):
        runtime._installed_workspace(package)
    assert not (package.parent / "workspace").exists()


def test_installed_layout_rejects_ancestor_symlink(tmp_path, monkeypatch):
    target = tmp_path / "target"
    package = target / "AMR-Desktop" / "app"
    package.mkdir(parents=True)
    (package / "AMR-INSTALLATION.json").write_text('{"format":1}', encoding="utf-8")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    monkeypatch.setattr(runtime, "require_local_ntfs", lambda _: pytest.fail("path traversed"))
    with pytest.raises(ValueError, match="普通目录"):
        runtime._installed_workspace(alias / "AMR-Desktop" / "app")
    assert not (package.parent / "workspace").exists()


def test_installed_diagnostic_routes_temporary_files_before_worker_start(tmp_path, monkeypatch):
    package = tmp_path / "AMR-Desktop" / "app"
    package.mkdir(parents=True)
    (package / "AMR-INSTALLATION.json").write_text('{"format":1}', encoding="utf-8")
    _record(package, "a" * 40)
    workspace = package.parent / "workspace"
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(runtime, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(runtime, "require_local_ntfs", lambda _: None)
    monkeypatch.setattr(runtime, "protect", lambda value: value)
    monkeypatch.setattr(runtime, "unprotect", lambda value: value)

    class VerifiedPaths(Exception):
        pass

    def verify_before_worker(actual_package):
        assert actual_package == package
        data = Path(os.environ["SHIXU_DATA_DIR"])
        assert data.parent.parent == workspace
        assert Path.cwd() == data / "work/tmp"
        assert tempfile.gettempdir() == str(Path.cwd())
        assert os.environ["TEMP"] == str(Path.cwd())
        assert os.environ["SQLITE_TMPDIR"] == str(data / "work")
        raise VerifiedPaths

    monkeypatch.setattr(runtime, "_verify_native_modules", verify_before_worker)
    before_cwd, before_tempdir = Path.cwd(), tempfile.tempdir
    with pytest.raises(VerifiedPaths):
        runtime.diagnose(package)
    assert not list(workspace.iterdir())
    assert Path.cwd() == before_cwd and tempfile.tempdir == before_tempdir


def test_reopen_keeps_the_local_browser_origin(tmp_path):
    with runtime._listen(tmp_path) as listener:
        address = listener.getsockname()
        assert address[0] == "127.0.0.1"
        with runtime._listen(tmp_path) as fallback:
            assert fallback.getsockname()[0] == "127.0.0.1"
            assert fallback.getsockname()[1] != address[1]
    with runtime._listen(tmp_path) as listener:
        assert listener.getsockname() == address


def test_tray_browser_tickets_can_reopen_but_cannot_be_replayed_or_used_late():
    time = [1.0]
    tickets = runtime._BrowserTickets(clock=lambda: time[0])
    first, second = tickets.issue(), tickets.issue()
    assert first != second
    assert tickets.consume(first)
    assert not tickets.consume(first)
    time[0] += 121
    assert not tickets.consume(second)
    assert tickets.consume(tickets.issue())


def test_tray_open_command_keeps_the_runtime_alive_until_tray_pipe_closes():
    opened, stopped = Event(), Event()
    read, write = os.pipe()
    controls = Thread(target=runtime._read_tray_controls, args=(read, opened, stopped.set))
    controls.start()
    try:
        os.write(write, b"o")
        assert opened.wait(2)
        assert not stopped.is_set()
        opened.clear()
        os.write(write, b"o")
        assert opened.wait(2)
        assert not stopped.is_set()
    finally:
        os.close(write)
        controls.join(2)
        os.close(read)
    assert stopped.is_set()
