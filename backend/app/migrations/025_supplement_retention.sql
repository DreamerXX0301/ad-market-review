-- 新手动增量授权的补充对象滚动清理；只追加计划和精确文件回执。
CREATE TABLE supplement_retention_run (
  collection_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES job(job_id),
  version TEXT NOT NULL CHECK(version='supplement-retention-250-v1'),
  anchor_date TEXT NOT NULL CHECK(date(anchor_date,'+0 days') IS anchor_date),
  cutoff_date TEXT NOT NULL CHECK(date(cutoff_date,'+0 days') IS cutoff_date),
  scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
  scope_sha256 TEXT NOT NULL CHECK(length(scope_sha256)=64),
  status TEXT NOT NULL CHECK(status IN ('planned','deleting','completed')),
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms>0),
  completed_at_ms INTEGER,
  UNIQUE(job_id,version),
  CHECK(cutoff_date<=anchor_date),
  CHECK(status<>'completed' OR completed_at_ms IS NOT NULL)
) STRICT;
CREATE TABLE supplement_retention_file (
  collection_id TEXT NOT NULL REFERENCES supplement_retention_run(collection_id),
  object_key TEXT NOT NULL CHECK(substr(object_key,1,1)<>'/' AND instr(object_key,'..')=0 AND instr(object_key,'://')=0),
  sha256 TEXT NOT NULL CHECK(length(sha256)=64),
  byte_count INTEGER NOT NULL CHECK(byte_count>=0),
  status TEXT NOT NULL CHECK(status IN ('planned','removed','absent')),
  removed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(removed_bytes>=0),
  PRIMARY KEY(collection_id,object_key),
  CHECK(status<>'planned' OR removed_bytes=0),
  CHECK(status<>'absent' OR removed_bytes=0),
  CHECK(status<>'removed' OR removed_bytes=byte_count)
) STRICT;
CREATE TRIGGER supplement_retention_identity_immutable BEFORE UPDATE ON supplement_retention_run
WHEN NEW.collection_id<>OLD.collection_id OR NEW.job_id<>OLD.job_id OR NEW.version<>OLD.version
 OR NEW.anchor_date<>OLD.anchor_date OR NEW.cutoff_date<>OLD.cutoff_date
 OR NEW.scope_json<>OLD.scope_json OR NEW.scope_sha256<>OLD.scope_sha256 OR NEW.created_at_ms<>OLD.created_at_ms
 OR OLD.status='completed' OR (OLD.status='deleting' AND NEW.status='planned')
BEGIN SELECT RAISE(ABORT,'retain immutable supplement retention authorization'); END;
CREATE TRIGGER supplement_retention_file_immutable BEFORE UPDATE ON supplement_retention_file
WHEN NEW.collection_id<>OLD.collection_id OR NEW.object_key<>OLD.object_key OR NEW.sha256<>OLD.sha256
 OR NEW.byte_count<>OLD.byte_count OR OLD.status<>'planned'
BEGIN SELECT RAISE(ABORT,'retain immutable supplement file receipt'); END;
CREATE TRIGGER supplement_retention_retained BEFORE DELETE ON supplement_retention_run
BEGIN SELECT RAISE(ABORT,'retain supplement retention history'); END;
CREATE TRIGGER supplement_retention_file_retained BEFORE DELETE ON supplement_retention_file
BEGIN SELECT RAISE(ABORT,'retain supplement file history'); END;
