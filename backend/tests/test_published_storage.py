"""Published storage is an additive file inventory, not a retention estimate."""
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import published_storage as storage
from test_platform import isolated


CURRENT = 'market-' + 'a' * 24
OLD = 'market-' + 'b' * 24


def put(root, name, size):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    return path


def by_key(result):
    return {row['key']: row for row in result['categories']}


def test_head_bound_five_categories_add_up_and_scan_each_directory_once(tmp_path, monkeypatch):
    root = tmp_path / 'published'
    files = [put(root, f'{CURRENT}/raw-daily.parquet', 3),
             put(root, f'{CURRENT}/daily.parquet', 11),
             put(root, f'{OLD}/raw-daily.parquet', 5),
             put(root, f'{OLD}/daily.parquet', 7),
             put(root, 'market-supplement/artifacts/day.json', 13),
             put(root, 'backtests/report/report.json', 17),
             put(root, 'catalog/security.json', 19),
             put(root, 'market-malformed/other', 23)]
    # The mutable file head and timestamps cannot override the request's pinned
    # SQLite-published generation. Supplement is not a main market generation.
    head = root / 'market-head.json'
    head.write_text(json.dumps({'snapshot_id': OLD}))
    files.append(head)
    os.utime(root / OLD, (2_000_000_000, 2_000_000_000))
    (root / ('market-' + 'c' * 24)).mkdir()
    put(root, '.staging-new/never-counted', 100)
    put(root, f'{CURRENT}/.staging-nested/never-counted', 200)
    for name in ('business', 'raw', 'work', 'credentials', 'backups', 'other-release'):
        put(tmp_path, f'{name}/not-published', 1000)
    visited = Counter()
    scan = storage.os.scandir

    def count_scan(fd):
        visited[os.fstat(fd).st_ino] += 1
        return scan(fd)

    monkeypatch.setattr(storage.os, 'scandir', count_scan)
    result = storage.published_storage(root, CURRENT)
    rows = by_key(result)
    assert result['scope'] == 'active_published_root'
    assert result['complete'] and result['scan_errors'] == 0
    assert result['current_market_snapshot_id'] == CURRENT
    assert result['market_generation_count'] == 3
    assert result['old_market_generation_count'] == 2
    assert rows['current_market']['logical_bytes'] == 14
    assert rows['old_market']['logical_bytes'] == 12
    assert rows['supplement']['logical_bytes'] == 13
    assert rows['backtests']['logical_bytes'] == 17
    assert rows['metadata_other']['logical_bytes'] == 42 + head.stat().st_size
    assert result['logical_bytes'] == sum(path.stat().st_size for path in files)
    assert result['logical_bytes'] == sum(row['logical_bytes'] for row in rows.values())
    assert result['file_count'] == len(files) == sum(row['file_count'] for row in rows.values())
    assert result['allocated_bytes'] == sum(path.stat().st_blocks * 512 for path in files)
    assert result['excluded'] == {'staging_entries': 2, 'symlink_entries': 0, 'special_entries': 0}
    assert visited and set(visited.values()) == {1}
    assert (root / '.staging-new').stat().st_ino not in visited


def test_symlinks_dangling_links_and_special_files_never_counted_or_traversed(tmp_path):
    root = tmp_path / 'published'
    put(root, 'catalog/real', 7)
    external = put(tmp_path, 'outside/private', 100)
    (root / 'file-alias').symlink_to(external)
    (root / 'directory-alias').symlink_to(external.parent, target_is_directory=True)
    (root / 'dangling').symlink_to(tmp_path / 'missing')
    os.mkfifo(root / 'fifo')
    result = storage.published_storage(root, None)
    assert result['logical_bytes'] == 7 and result['file_count'] == 1
    assert result['complete']
    assert result['excluded'] == {'staging_entries': 0, 'symlink_entries': 3, 'special_entries': 1}


def test_hard_links_are_per_path_sizes_not_claimed_unique_disk_or_reclaim(tmp_path):
    original = put(tmp_path, 'catalog/a', 23)
    os.link(original, tmp_path / 'catalog/b')
    result = storage.published_storage(tmp_path, None)
    assert result['logical_bytes'] == 46 and result['file_count'] == 2
    assert result['allocated_bytes'] == original.stat().st_blocks * 512 * 2
    assert not any('reclaim' in key or 'exclusive' in key for key in result)


def test_no_file_contents_are_read(tmp_path, monkeypatch):
    put(tmp_path, f'{CURRENT}/daily.parquet', 31)

    def forbidden(*args, **kwargs):
        raise AssertionError('storage inventory must not read file contents')

    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    assert storage.published_storage(tmp_path, CURRENT)['logical_bytes'] == 31


def test_uninitialised_missing_directory_is_known_empty(tmp_path):
    result = storage.published_storage(tmp_path / 'not-created', None)
    assert result['complete'] and result['logical_bytes'] == result['allocated_bytes'] == 0
    assert result['file_count'] == result['market_generation_count'] == 0
    assert len(result['categories']) == 5


@pytest.mark.parametrize('root_kind', ['missing', 'file', 'symlink'])
def test_invalid_root_or_missing_current_generation_is_explicit_subtotal(tmp_path, root_kind):
    root = tmp_path / 'published'
    if root_kind == 'file':
        root.write_bytes(b'not-a-directory')
    elif root_kind == 'symlink':
        (tmp_path / 'external').mkdir()
        root.symlink_to(tmp_path / 'external', target_is_directory=True)
    result = storage.published_storage(root, CURRENT)
    assert result['complete'] is False and result['scan_errors'] > 0 and result['reason']
    assert result['logical_bytes'] == result['file_count'] == 0


def test_invalid_current_identity_never_classifies_supplement_as_current(tmp_path):
    put(tmp_path, 'market-supplement/day', 11)
    result = storage.published_storage(tmp_path, 'market-supplement')
    assert result['current_market_snapshot_id'] is None and not result['complete']
    assert by_key(result)['current_market']['logical_bytes'] == 0
    assert by_key(result)['supplement']['logical_bytes'] == 11


def test_directory_read_error_keeps_measured_subtotal_without_500(tmp_path, monkeypatch):
    put(tmp_path, 'catalog/kept', 19)
    blocked = put(tmp_path, 'blocked/private', 41).parent.stat().st_ino
    scan = storage.os.scandir

    def deny(fd):
        if os.fstat(fd).st_ino == blocked:
            raise PermissionError('fixture unreadable directory')
        return scan(fd)

    monkeypatch.setattr(storage.os, 'scandir', deny)
    result = storage.published_storage(tmp_path, None)
    assert result['complete'] is False and result['scan_errors'] == 1
    assert result['logical_bytes'] == 19 and result['file_count'] == 1
    assert result['reason']


def test_unknown_file_allocation_keeps_logical_size_but_total_allocation_unknown(tmp_path, monkeypatch):
    put(tmp_path, 'market-supplement/unknown-blocks', 17)
    put(tmp_path, 'catalog/known-blocks', 23)
    scan = storage.os.scandir

    class Entry:
        def __init__(self, entry):
            self.entry, self.name = entry, entry.name

        def stat(self, **kwargs):
            value = self.entry.stat(**kwargs)
            if self.name == 'unknown-blocks':
                return SimpleNamespace(st_mode=value.st_mode, st_size=value.st_size)
            return value

    @contextmanager
    def missing_blocks(fd):
        with scan(fd) as entries:
            yield (Entry(entry) for entry in entries)

    monkeypatch.setattr(storage.os, 'scandir', missing_blocks)
    result = storage.published_storage(tmp_path, None)
    assert result['complete'] and result['logical_bytes'] == 40
    assert result['allocated_bytes'] is None
    assert by_key(result)['supplement']['allocated_bytes'] is None
    assert by_key(result)['metadata_other']['allocated_bytes'] is not None


def test_directory_swapped_for_symlink_between_stat_and_open_is_not_followed(tmp_path, monkeypatch):
    root = tmp_path / 'published'
    original = put(root, 'swap/original', 7).parent
    put(tmp_path, 'external/not-in-scope', 100)
    open_directory = storage.os.open
    swapped = False

    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == 'swap' and not swapped:
            original.rename(root / '.staging-original')
            original.symlink_to(tmp_path / 'external', target_is_directory=True)
            swapped = True
        return open_directory(path, flags, *args, **kwargs)

    monkeypatch.setattr(storage.os, 'open', swap)
    result = storage.published_storage(root, None)
    assert swapped and result['logical_bytes'] == 0
    assert not result['complete'] and result['scan_errors'] == 1


def test_bootstrap_and_data_status_share_compatible_inventory_without_tasks(isolated):
    from fastapi.testclient import TestClient
    from app.core import connect, published_dir
    from app.main import app
    put(published_dir(), 'catalog/fixture', 29)
    with connect() as connection:
        jobs_before = connection.execute('SELECT count(*) FROM job').fetchone()[0]
    with TestClient(app) as client:
        bootstrap = client.get('/api/v1/bootstrap').json()['data_summary']
        status = client.get('/api/v1/data/status').json()['summary']
    assert bootstrap == status
    assert status['storage_bytes'] == status['storage_accounting']['logical_bytes'] == 29
    assert status['storage_accounting']['complete']
    with connect() as connection:
        assert connection.execute('SELECT count(*) FROM job').fetchone()[0] == jobs_before
