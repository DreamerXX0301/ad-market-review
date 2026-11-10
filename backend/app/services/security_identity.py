"""Conservative identity continuity for a current securities directory.

Listing dates are stable identity evidence, unlike mutable valuation fields. A
temporary null response may reuse the last verified date for the same identity.
"""
from datetime import date
import json
import re


_SHARE_CODE = re.compile(r'[0-9]{6}\.SH')
_ACTION_NAME = re.compile(r'(?:XD|XR|DR)([\u3400-\u4dbf\u4e00-\u9fff].*)')
_CJK_NAME = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]+')


def valid_listing_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _same_action_name(symbol, row, old):
    if (not _SHARE_CODE.fullmatch(symbol) or row.get('exchange') != 'SH'
            or old.get('exchange') != 'SH' or row.get('asset_type') != 'a-share'
            or old.get('asset_type') != 'a-share' or old.get('thscode') != symbol):
        return False
    name, previous_name = row.get('name'), old.get('name')
    if not isinstance(name, str) or not isinstance(previous_name, str):
        return False
    decorated = _ACTION_NAME.fullmatch(name)
    previous_decorated = _ACTION_NAME.fullmatch(previous_name)
    if not decorated and not previous_decorated:
        return False
    current_base = decorated[1] if decorated else name
    previous_base = previous_decorated[1] if previous_decorated else previous_name
    if current_base == previous_base:
        return True
    # Some Shanghai action-day short names retain only the first three of
    # four Chinese characters. Apply this shape only against an unadorned name.
    if bool(decorated) == bool(previous_decorated):
        return False
    short, full = (current_base, previous_base) if decorated else (previous_base, current_base)
    return (len(short) == 3 and len(full) == 4 and full.startswith(short)
            and _CJK_NAME.fullmatch(full) is not None)


def preserve_listing_dates(items, previous, snapshot_id=None):
    result = []
    inherited = []
    for original in items:
        row = dict(original)
        symbol = str(row.get('thscode', '')).upper()
        old = previous.get(symbol, {})
        if not valid_listing_date(row.get('list_date')):
            row['list_date'] = None
            exact_name = bool(row.get('name') and row.get('name') == old.get('name'))
            if (valid_listing_date(old.get('list_date')) and not row.get('end_date')
                    and not old.get('end_date') and row.get('exchange') == old.get('exchange')
                    and (exact_name or _same_action_name(symbol, row, old))):
                row['list_date'] = old['list_date']
                row['list_date_evidence'] = {'kind': 'retained_verified_directory', 'snapshot_id': snapshot_id}
                inherited.append(symbol)
        result.append(row)
    return result, inherited


def recover_listing_dates(items, previous, snapshot_id=None):
    """Use only checksummed retained directories, newest first, for null fields.

    This also repairs a temporary-null generation created by an older client;
    no external research material or unrelated project's directory is eligible.
    """
    from app.core import connect, published_dir, hash_file
    rows, inherited = preserve_listing_dates(items, previous, snapshot_id)
    if not any(row.get('list_date') is None for row in rows):
        return rows, inherited
    with connect() as connection:
        snapshots = connection.execute("SELECT s.lineage_json FROM dataset_snapshot s LEFT JOIN snapshot_gc g USING(snapshot_id) WHERE s.module='daily_raw' AND s.state='published' AND (g.state IS NULL OR g.state='available') ORDER BY s.published_at_ms DESC LIMIT 8").fetchall()
    visited = {snapshot_id}
    root = published_dir().resolve()
    for snapshot in snapshots:
        generation = json.loads(snapshot[0]).get('market_snapshot_id')
        if not isinstance(generation, str) or generation in visited:
            continue
        visited.add(generation)
        directory = (root / generation).resolve()
        if not directory.is_relative_to(root):
            continue
        try:
            manifest = json.loads((directory / 'manifest.json').read_text())
            if manifest.get('summary',{}).get('snapshot_id') != generation:
                continue
            reference = directory / 'reference.json'
            expected = next(item['sha256'] for item in manifest['summary']['files'] if item['role'] == 'reference.json')
            if hash_file(reference) != expected:
                continue
            from app.analytics.market_storage import load_reference
            master = load_reference(reference).get('master', {})
        except (OSError, ValueError, KeyError, StopIteration):
            continue
        rows, extra = preserve_listing_dates(rows, master, generation)
        inherited.extend(extra)
        if not any(row.get('list_date') is None for row in rows):
            break
    return rows, list(dict.fromkeys(inherited))
