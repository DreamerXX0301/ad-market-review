"""Machine code and device-bound Ed25519 license format."""
from __future__ import annotations

import base64
import hashlib
import re
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

DAY = 86_400
CLOCK_TOLERANCE = 300
PLAN_DAYS = {"trial": 7, "monthly": 30, "yearly": 365, "permanent": None}
MAX_CODE_LENGTH = 1024
SIGNATURE_DOMAIN = b"ad-market-review/offline-license/v1\n"
MachineTag = Annotated[str, Field(pattern=r"^[A-Z2-7]{32}$")]
Timestamp = Annotated[int, Field(ge=0)]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class License(Message):
    version: Literal[1] = 1
    serial: Annotated[int, Field(ge=1)]
    machine: MachineTag
    plan: Literal["trial", "monthly", "yearly", "permanent"]
    issued_at: Timestamp
    expires_at: Timestamp | None

    @model_validator(mode="after")
    def valid_term(self):
        if (self.plan == "permanent") != (self.expires_at is None):
            raise ValueError("授权期限无效")
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("授权期限无效")
        return self


def machine_tag(device_token: str) -> str:
    return base64.b32encode(hashlib.sha256(bytes.fromhex(device_token)).digest()[:20]).decode("ascii")


def format_machine_code(tag: str) -> str:
    return "AMR-M1-" + "-".join(tag[index:index + 4] for index in range(0, len(tag), 4))


def parse_machine_code(value: str) -> str:
    if len(value) > 64 or not value.isascii():
        raise ValueError("机器码格式无效")
    normalized = value.strip().upper()
    if not normalized.startswith("AMR-M1-"):
        raise ValueError("机器码格式无效")
    tag = normalized.removeprefix("AMR-M1-").replace("-", "")
    if re.fullmatch(r"[A-Z2-7]{32}", tag) is None:
        raise ValueError("机器码格式无效")
    return tag


def verify_license(code: str, public_key: Ed25519PublicKey, expected_machine: str) -> License:
    try:
        if len(code) > MAX_CODE_LENGTH:
            raise ValueError
        prefix, encoded_body, encoded_signature = code.split(".")
        if prefix != "AMR-L1":
            raise ValueError
        body = base64.b64decode(encoded_body, altchars=b"-_", validate=True)
        signature = base64.b64decode(encoded_signature, altchars=b"-_", validate=True)
        public_key.verify(signature, SIGNATURE_DOMAIN + body)
        license = License.model_validate_json(body)
    except (ValueError, InvalidSignature):
        raise ValueError("授权码无效或签名不匹配") from None
    if license.machine != expected_machine:
        raise ValueError("该授权码绑定了其他设备，请核对机器码")
    return license
