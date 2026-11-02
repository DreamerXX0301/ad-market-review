"""Resolve an unambiguous stock identity from the published local directory.

Only identity metadata is read. No prices, mutable DuckDB, provider, business
write, fuzzy company-name guess or research-cache renewal is involved.
"""
import re
import sqlite3
import unicodedata

from app.services.ai_read_tools import _heads, _market


def resolve_scope_focus(text: str) -> dict:
    if not isinstance(text, str) or not 2 <= len(text) <= 2000:
        return {}
    question = ''.join(c for c in unicodedata.normalize('NFKC', text)
                       if unicodedata.category(c) != 'Cf').casefold()
    codes = set(re.findall(r'(?<![a-z0-9])(\d{6}(?:\.[a-z]+)?)(?![a-z0-9])', question))
    try:
        before = _heads()
        market = _market(before)
        if not market.status().get('ready'):
            return {}
        rows = market.instruments('', limit=10000)
        candidates = {}
        for row in rows:
            if row.get('kind') != 'stock':
                continue
            identifier, name = row.get('instrument_id'), row.get('name')
            if (not isinstance(identifier, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', identifier)
                    or not isinstance(name, str) or not 2 <= len(name) <= 100):
                continue
            normalized_name = unicodedata.normalize('NFKC', name).casefold()
            if (normalized_name in question
                    or identifier.casefold() in codes or identifier[:6] in codes):
                candidates[identifier] = {'instrument_id': identifier, 'name': name}
                if len(candidates) > 1:
                    return {}
        if before != _heads() or len(candidates) != 1:
            return {}
        return next(iter(candidates.values()))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error):
        # An unavailable/changed directory never grants authority or leaks paths.
        return {}
