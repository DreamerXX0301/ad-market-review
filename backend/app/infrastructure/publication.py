"""Immutable dataset publication; files become durable before SQLite heads switch."""
from __future__ import annotations
from .config import published_dir
from .database import connect
from .storage import new_id,now_ms,dumps,hash_file,atomic_json

def publish_snapshot(*, module: str, job_id: str, start: str, end: str, artifacts: list[dict], quality: str = "partial", scope: str = "CN_A", lineage: dict | None = None, price_basis: str = "not_applicable", algorithm: str = "market-v1", expected_rows: int | None = None, missing: int = 0, snapshot_id: str | None = None, activate: bool = True) -> str:
    """Persist immutable files first, then switch the SQLite head atomically."""
    snapshot_id = snapshot_id or new_id(module)
    with connect() as connection:
        existing = connection.execute("SELECT state FROM dataset_snapshot WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if existing:
            if existing["state"] != "published":
                raise ValueError("同名快照尚未完成发布，请恢复原任务")
            return snapshot_id
    manifest = {"snapshot_id": snapshot_id, "module": module, "scope": scope, "start": start, "end": end, "quality": quality, "price_basis": price_basis, "algorithm": algorithm, "artifacts": artifacts, "lineage": lineage or {}}
    manifest_path = published_dir() / "catalog" / snapshot_id / "manifest.json"
    for artifact in artifacts:
        target = (published_dir() / artifact["object_key"]).resolve()
        if not target.is_relative_to(published_dir()) or not target.is_file() or hash_file(target) != artifact["sha256"]:
            raise ValueError("发布文件不存在或校验和不一致")
    atomic_json(manifest_path, manifest)
    relative = str(manifest_path.relative_to(published_dir()))
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = connection.execute("SELECT status FROM job WHERE job_id=?", (job_id,)).fetchone()
        if not job or job[0] in ('cancelled', 'paused', 'failed'):
            raise ValueError("任务已停止，拒绝发布新数据")
        if connection.execute("SELECT state FROM dataset_snapshot WHERE snapshot_id=?", (snapshot_id,)).fetchone():
            return snapshot_id
        connection.execute("INSERT INTO dataset_snapshot(snapshot_id,module,scope_key,job_id,data_begin,data_end,knowledge_cutoff_ms,price_basis,algorithm_version,lineage_json,manifest_path,manifest_sha256,state,quality,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (snapshot_id, module, scope, job_id, start, end, now_ms(), price_basis, algorithm, dumps(lineage or {}), relative, hash_file(manifest_path), "staged", quality, now_ms()))
        for artifact in artifacts:
            connection.execute("INSERT INTO snapshot_artifact(snapshot_id,object_key,role,sha256,byte_count,row_count) VALUES(?,?,?,?,?,?)", (snapshot_id, artifact["object_key"], artifact.get("role", module), artifact["sha256"], artifact["byte_count"], artifact.get("row_count")))
        observed = sum(a.get("row_count", 0) or 0 for a in artifacts)
        connection.execute("INSERT INTO snapshot_coverage(snapshot_id,feature,scope_json,coverage_state,observed_rows,expected_rows,unexplained_missing,continuous_begin,continuous_end,gaps_json,exceptions_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (snapshot_id, module, dumps({"scope": scope}), quality if quality in ("ready", "partial", "unknown") else "unknown", observed, expected_rows, missing, start, end, dumps((lineage or {}).get("gaps", [])), dumps((lineage or {}).get("exceptions", {}))))
        connection.execute("UPDATE dataset_snapshot SET state='validated' WHERE snapshot_id=?", (snapshot_id,))
        connection.execute("UPDATE dataset_snapshot SET state='published',published_at_ms=? WHERE snapshot_id=?", (now_ms(), snapshot_id))
        if activate:
            connection.execute("INSERT INTO published_head(module,scope_key,snapshot_id,changed_at_ms) VALUES(?,?,?,?) ON CONFLICT(module,scope_key) DO UPDATE SET snapshot_id=excluded.snapshot_id,changed_at_ms=excluded.changed_at_ms", (module, scope, snapshot_id, now_ms()))
    return snapshot_id
