"""Conservative identity continuity for a current securities directory.

Listing dates are stable identity evidence, unlike mutable valuation fields. A
temporary null response may reuse the last verified date for the same code and
name; a renamed/reused code needs fresh source evidence instead.
"""
from datetime import date
import json


def valid_listing_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def preserve_listing_dates(items, previous, snapshot_id=None):
    result = []
    inherited = []
    for original in items:
        row = dict(original)
        symbol = str(row.get('thscode', '')).upper()
        old = previous.get(symbol, {})
        if not valid_listing_date(row.get('list_date')):
            row['list_date'] = None
            if (row.get('name') and row.get('name') == old.get('name')
                    and valid_listing_date(old.get('list_date'))
                    and not old.get('end_date')
                    and row.get('exchange') == old.get('exchange')):
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
            master = json.loads(reference.read_text()).get('master', {})
        except (OSError, ValueError, KeyError, StopIteration):
            continue
        rows, extra = preserve_listing_dates(rows, master, generation)
        inherited.extend(extra)
        if not any(row.get('list_date') is None for row in rows):
            break
    return rows, list(dict.fromkeys(inherited))
