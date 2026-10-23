-- 离线行情对象回收：保留冻结范围及每个SHA的删除回执，不删除业务证据。
CREATE TABLE market_storage_collection (
  collection_id TEXT PRIMARY KEY,
  version TEXT NOT NULL CHECK(version='market-storage-gc-v1'),
  scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
  scope_sha256 TEXT NOT NULL CHECK(length(scope_sha256)=64),
  status TEXT NOT NULL CHECK(status IN ('planned','deleting','completed')),
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms>0),
  completed_at_ms INTEGER
) STRICT;
CREATE TABLE market_storage_collection_file (
  collection_id TEXT NOT NULL REFERENCES market_storage_collection(collection_id),
  object_key TEXT NOT NULL CHECK(substr(object_key,1,1)<>'/' AND instr(object_key,'..')=0),
  sha256 TEXT NOT NULL CHECK(length(sha256)=64),
  byte_count INTEGER NOT NULL CHECK(byte_count>=0),
  status TEXT NOT NULL CHECK(status IN ('planned','removed','absent')),
  removed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(removed_bytes>=0),
  PRIMARY KEY(collection_id,object_key)
) STRICT;
CREATE TRIGGER market_collection_identity_immutable BEFORE UPDATE ON market_storage_collection
WHEN NEW.collection_id<>OLD.collection_id OR NEW.version<>OLD.version OR NEW.scope_json<>OLD.scope_json OR NEW.scope_sha256<>OLD.scope_sha256 OR NEW.created_at_ms<>OLD.created_at_ms OR OLD.status='completed'
BEGIN SELECT RAISE(ABORT,'retain immutable market collection authorization'); END;
CREATE TRIGGER market_collection_file_identity_immutable BEFORE UPDATE ON market_storage_collection_file
WHEN NEW.collection_id<>OLD.collection_id OR NEW.object_key<>OLD.object_key OR NEW.sha256<>OLD.sha256 OR NEW.byte_count<>OLD.byte_count OR OLD.status<>'planned'
BEGIN SELECT RAISE(ABORT,'retain immutable market collection file receipt'); END;
CREATE TRIGGER market_collection_retained BEFORE DELETE ON market_storage_collection
BEGIN SELECT RAISE(ABORT,'retain market collection history'); END;
CREATE TRIGGER market_collection_file_retained BEFORE DELETE ON market_storage_collection_file
BEGIN SELECT RAISE(ABORT,'retain market collection file history'); END;
