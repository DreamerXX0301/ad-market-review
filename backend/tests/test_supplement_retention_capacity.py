"""Synthetic capacity witnesses, never a claim about production retention volume.

These fixtures model an 800-board daily fallback and the immutable references
which prevent interpreting a logical 250-session view as a physical byte cap.
They use only disposable files and exercise the production planner unchanged.
"""
import pytest

from app import core
from app.services import supplement_retention as retention
from test_supplement_retention import accepted, calendar, index


def _all_files(root):
    return {path.relative_to(root).as_posix() for path in root.rglob('*.json')}


def _remove_exact_plan(root, plan):
    """Simulate worker unlink after checking every selected immutable identity."""
    for row in plan['scope']['files']:
        path = root / row['object_key']
        assert core.hash_file(path) == row['sha256']
        assert path.stat().st_size == row['byte_count']
        path.unlink()


def test_800_board_fallback_expiry_requires_2400_objects_and_bounded_drain(tmp_path):
    root = tmp_path / 'supplement'
    days = calendar()
    for board in range(800):
        accepted(root, days[0], capability=f'sector-index-{board:04}',
                 payload={'board': board, 'date': days[0], 'bars': [{'close': 10}]})
    # Real supplementary current/rollback index roots exist, but no longer point
    # to the expired per-board fallback. This is a best-case reclaimable day.
    index(root, days[-2], 1)
    index(root, days[-1], 2, head=True)
    files_before = _all_files(root)
    with retention.plan_retention(root, days, days[-1]) as full:
        assert full['scope']['eligible_files'] == 2400
        assert len(full['scope']['files']) == 2400
        assert full['scope']['backlog_files'] == 0
        assert full['scope']['selected_bytes'] < 256 * 1024 * 1024
    bounded = retention.plan_retention(root, days, days[-1], max_files=256)
    assert bounded['scope']['eligible_files'] == 800 * 3
    assert len(bounded['scope']['files']) == 256
    assert bounded['scope']['backlog_files'] == 2144
    assert _all_files(root) == files_before, 'planning must remain read-only'

    # A bounded round may stop between accepted -> normalized -> raw; surviving
    # parents must keep valid children on every subsequent production plan.
    _remove_exact_plan(root, bounded)
    subsequent = retention.plan_retention(root, days, days[-1])
    assert subsequent['scope']['eligible_files'] == 2144
    assert subsequent['scope']['max_files'] == retention.MAX_DELETE_FILES
    assert subsequent['scope']['selected_bytes'] <= retention.MAX_DELETE_BYTES
    assert len(subsequent['scope']['files']) == 2144
    assert subsequent['scope']['backlog_files'] == 0
    assert all(row['object_key'] not in bounded['protected_keys'] for row in bounded['scope']['files'])


def test_inventory_capacity_refusal_is_atomic_without_a_completed_receipt(tmp_path, monkeypatch):
    root = tmp_path / 'supplement'
    days = calendar()
    for board in range(12):
        accepted(root, days[0], capability=f'sector-index-{board:04}',
                 payload={'board': board, 'date': days[0]})
    index(root, days[-2], 1)
    index(root, days[-1], 2, head=True)
    # Reduce only the budget, not the production algorithm. Proves overflow is
    # a safe refusal and cannot be presented as successful physical retention.
    monkeypatch.setattr(retention, 'MAX_INVENTORY_FILES', 32)
    before = _all_files(root)
    with pytest.raises(ValueError, match='扫描预算'):
        retention.plan_retention(root, days, days[-1])
    assert _all_files(root) == before
    assert not list(root.rglob('*.sqlite*')), 'read-only plan never fabricates a GC receipt'


def test_old_unreferenced_indexes_do_not_extend_logical_window_to_500_sessions(tmp_path):
    root = tmp_path / 'supplement'
    days = calendar()
    target = days[-1]
    current_cutoff = days[-250]
    older_evidence_day = days[-499]
    old_accepted, record, raw = accepted(root, older_evidence_day, capability='sector-index-fallback')
    # Keeping all indexes whose observation dates are in the current window
    # would accidentally preserve another 249 sessions through their closures.
    # Current head + two rollback indexes and actual business pins avoid that.
    index(root, current_cutoff, 1, references={older_evidence_day: record})
    index(root, days[-2], 2)
    index(root, target, 3, head=True)
    logical = retention.trim_document({'sector_index_history': {older_evidence_day: record, target: {}}}, days, target)
    assert set(logical['sector_index_history']) == {target}
    plan = retention.plan_retention(root, days, target)
    assert old_accepted not in plan['protected_keys']
    assert record['artifact'] not in plan['protected_keys']
    assert raw['artifact'] not in plan['protected_keys']
    assert any(row['object_key'] == record['artifact'] for row in plan['scope']['files'])


def test_expired_supplement_and_five_year_sector_tail_share_protected_objects(tmp_path):
    root = tmp_path / 'supplement'
    days = calendar()
    old_key, record, raw = accepted(root, days[0], capability='sector-index-fallback')
    protected_key = f'accepted/fuyao/sector-chart-tail/{days[0]}-886001.TI.json'
    core.atomic_json(root / protected_key, {'version': 'market-supplement-v1', **record})
    index(root, days[-2], 1)
    index(root, days[-1], 2, head=True)
    plan = retention.plan_retention(root, days, days[-1])
    assert old_key in {row['object_key'] for row in plan['scope']['files']}
    assert protected_key in plan['protected_keys']
    assert record['artifact'] in plan['protected_keys']
    assert raw['artifact'] in plan['protected_keys']
    assert plan['scope']['main_history_policy'] == 'separate-five-year-window'
