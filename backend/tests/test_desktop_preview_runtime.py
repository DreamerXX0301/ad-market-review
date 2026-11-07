import json
import os
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.infrastructure.config import APP_VERSION
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


def test_reopen_keeps_the_local_browser_origin(tmp_path):
    with runtime._listen(tmp_path) as listener:
        address = listener.getsockname()
        assert address[0] == "127.0.0.1"
        with runtime._listen(tmp_path) as fallback:
            assert fallback.getsockname()[0] == "127.0.0.1"
            assert fallback.getsockname()[1] != address[1]
    with runtime._listen(tmp_path) as listener:
        assert listener.getsockname() == address
