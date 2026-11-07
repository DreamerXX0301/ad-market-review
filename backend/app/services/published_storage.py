"""One read-only inventory of this instance's published ordinary file entries.

Logical size counts paths, including hard links and filesystem clones separately.
Allocated blocks are also per entry, so neither number estimates exclusive space
or space that a retention operation could release. No file contents are read.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from app.infrastructure.file_operations import is_link_or_reparse, open_directory


_GENERATION = re.compile(r"market-[0-9a-f]{24}\Z")
_KEYS = ("current_market", "old_market", "shared_market", "supplement", "backtests", "metadata_other")


def published_storage(root: Path, current_snapshot_id: str | None) -> dict:
    """Classify ordinary files by top-level directory, using the pinned head.

    Pinned directories prevent a concurrent directory swap from turning this
    inventory into traversal of a symlink or reparse point outside the root.
    Excluded counts describe encountered entries/subtrees, not their contents.
    """
    rows = {key: {"key": key, "logical_bytes": 0, "allocated_bytes": 0,
                  "file_count": 0} for key in _KEYS}
    for key in ("current_market", "old_market"):
        rows[key]["generation_count"] = 0
    excluded = {"staging_entries": 0, "symlink_entries": 0, "special_entries": 0}
    errors = 0
    reasons = set()
    generations = set()
    current = current_snapshot_id if isinstance(current_snapshot_id, str) and _GENERATION.fullmatch(current_snapshot_id) else None
    if current_snapshot_id and current is None:
        errors += 1
        reasons.add("当前行情代际标识不可核验")

    def failed():
        nonlocal errors
        errors += 1
        reasons.add("部分目录或文件读取失败，已计量结果仅为小计")

    def category(top):
        if top and _GENERATION.fullmatch(top):
            return "current_market" if top == current else "old_market"
        return {"market-objects": "shared_market", "market-supplement": "supplement", "dragon-tiger": "supplement", "backtests": "backtests"}.get(top, "metadata_other")

    def walk(directory_fd, top=None):
        try:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if entry.name.startswith(".staging"):
                        excluded["staging_entries"] += 1
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if is_link_or_reparse(info):
                            excluded["symlink_entries"] += 1
                        elif stat.S_ISDIR(info.st_mode):
                            with open_directory(entry.name, parent=directory_fd) as child:
                                child_top = top or entry.name
                                if top is None and _GENERATION.fullmatch(entry.name):
                                    generations.add(entry.name)
                                    rows[category(entry.name)]["generation_count"] += 1
                                walk(child, child_top)
                        elif stat.S_ISREG(info.st_mode):
                            row = rows[category(top)]
                            row["logical_bytes"] += info.st_size
                            row["file_count"] += 1
                            blocks = getattr(info, "st_blocks", None)
                            if not isinstance(blocks, int) or blocks < 0:
                                row["allocated_bytes"] = None
                            elif row["allocated_bytes"] is not None:
                                row["allocated_bytes"] += blocks * 512
                        else:
                            excluded["special_entries"] += 1
                    except OSError:
                        failed()
        except OSError:
            failed()

    try:
        with open_directory(root) as directory:
            walk(directory)
    except FileNotFoundError:
        # An uninitialised installation has no published directory yet.
        if current:
            failed()
    except OSError:
        failed()
    if current and current not in generations:
        errors += 1
        reasons.add("当前行情代际目录未计入")
    categories = list(rows.values())
    allocated = [row["allocated_bytes"] for row in categories]
    result = {
        "scope": "active_published_root",
        "logical_bytes": sum(row["logical_bytes"] for row in categories),
        "allocated_bytes": sum(allocated) if all(value is not None for value in allocated) else None,
        "file_count": sum(row["file_count"] for row in categories),
        "complete": errors == 0,
        "current_market_snapshot_id": current,
        "market_generation_count": len(generations),
        "old_market_generation_count": rows["old_market"]["generation_count"],
        "categories": categories,
        "excluded": excluded,
        "scan_errors": errors,
    }
    if reasons:
        result["reason"] = "；".join(sorted(reasons))
    return result
