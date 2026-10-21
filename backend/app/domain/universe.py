"""Current security identity for explicit stock-universe filters.

Board identity is inferred from exchange/code. ST status is based on the current
security name, so it must never be described as a point-in-time historical ST file.
"""
from __future__ import annotations

import re

BOARDS = {"main": "主板", "chinext": "创业板", "star": "科创板", "beijing": "北交所"}


def identity_flags(identity: dict, as_of: str | None = None) -> dict:
    symbol = str(identity.get("instrument_id") or identity.get("thscode") or identity.get("code") or "").upper()
    code = str(identity.get("code") or symbol.split(".")[0]).zfill(6)
    exchange = str(identity.get("exchange") or (symbol.rsplit(".", 1)[1] if "." in symbol else "")).upper()
    board = "unknown"
    if re.fullmatch(r"\d{6}", code):
        if exchange in ("BJ", "BSE", "北京", "北交所") or code.startswith(("4", "8", "92")):
            board = "beijing"
        elif code.startswith(("688", "689")):
            board = "star"
        elif code.startswith(("300", "301")):
            board = "chinext"
        elif code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
            board = "main"
    name = str(identity.get("name") or identity.get("display_name") or "").strip()
    explicit = identity.get("is_st")
    # A missing/placeholder name cannot establish that a security is non-ST.
    named = bool(name and name.upper() not in (symbol, code, "UNKNOWN", "未知", "—", "-"))
    is_st = explicit if isinstance(explicit, bool) else (bool(re.match(r"^(?:S\*?ST|\*?ST)", name.upper())) if named else None)
    return {"board": board, "is_st": is_st, "identity_scope": "current", "identity_as_of": as_of}
