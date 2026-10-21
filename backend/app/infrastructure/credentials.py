"""Encrypted provider credentials; the master key lives outside project data."""
import os
from pathlib import Path
import re

from cryptography.fernet import Fernet, InvalidToken

from .config import data_dir


class CredentialStore:
    def __init__(self):
        self.directory = Path(os.environ.get("SHIXU_CREDENTIAL_DIR", str(data_dir() / "credentials")))
        explicit = os.environ.get("SHIXU_CREDENTIAL_MASTER_KEY_FILE") or os.environ.get("SHIXU_MASTER_KEY_FILE")
        self.master_path = Path(explicit or (Path.home() / ".local/share/shixu/credential_master_key"))
        self.explicit_master = bool(explicit)

    def _path(self, source_id):
        if not re.fullmatch(r"[a-z0-9-]{1,60}", source_id):
            raise ValueError("无效数据源标识")
        return self.directory / (source_id + ".enc")

    def exists(self, source_id):
        return self._path(source_id).is_file()

    def _cipher(self, create=False):
        if not self.master_path.is_file():
            if not create or self.explicit_master:
                raise ValueError("凭据主密钥未就绪，请先完成部署密钥初始化")
            self.master_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.master_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as writer:
                writer.write(Fernet.generate_key())
                writer.flush()
                os.fsync(writer.fileno())
        return Fernet(self.master_path.read_bytes().strip())

    def save(self, source_id, value):
        value = str(value).strip()
        if not value or len(value) > 1000 or "\n" in value:
            raise ValueError("Key格式无效")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self._path(source_id)
        temporary = target.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as writer:
            writer.write(self._cipher(create=True).encrypt(value.encode()))
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, target)
        return "encrypted:" + source_id

    def get(self, source_id):
        target = self._path(source_id)
        if not target.exists():
            return None
        try:
            return self._cipher().decrypt(target.read_bytes()).decode()
        except InvalidToken:
            raise ValueError("凭据无法解密，请核对主密钥或重新配置该来源") from None
