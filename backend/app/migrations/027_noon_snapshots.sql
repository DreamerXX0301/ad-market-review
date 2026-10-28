-- 午评独立于日线发布；永久名额不因失败或正文到期而释放。
CREATE TABLE market_noon_capture (
 trade_date TEXT PRIMARY KEY CHECK(date(trade_date,'+0 days') IS trade_date),
 job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
 request_key TEXT NOT NULL UNIQUE,
 requested_at_ms INTEGER NOT NULL CHECK(requested_at_ms>0)
) STRICT;
CREATE TRIGGER retain_noon_capture_update BEFORE UPDATE ON market_noon_capture
BEGIN SELECT RAISE(ABORT,'retain noon daily capture ledger'); END;
CREATE TRIGGER retain_noon_capture_delete BEFORE DELETE ON market_noon_capture
BEGIN SELECT RAISE(ABORT,'retain noon daily capture ledger'); END;
CREATE TRIGGER retain_noon_capture_replace BEFORE INSERT ON market_noon_capture
WHEN EXISTS(SELECT 1 FROM market_noon_capture WHERE trade_date=NEW.trade_date OR job_id=NEW.job_id OR request_key=NEW.request_key)
BEGIN SELECT RAISE(ABORT,'retain noon daily capture ledger'); END;

CREATE TABLE market_noon_snapshot (
 snapshot_id TEXT PRIMARY KEY,
 trade_date TEXT NOT NULL UNIQUE REFERENCES market_noon_capture(trade_date),
 job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
 collected_start_at TEXT NOT NULL,
 collected_end_at TEXT NOT NULL,
 recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms>0),
 quality TEXT NOT NULL CHECK(quality IN('complete','partial')),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object' AND length(payload_json)<=8388608),
 sources_json TEXT NOT NULL CHECK(json_valid(sources_json) AND json_type(sources_json)='array'),
 CHECK(json_extract(payload_json,'$.date') IS trade_date),
 CHECK(json_extract(payload_json,'$.version') IS 'noon-snapshot-v1')
) STRICT;
CREATE INDEX noon_snapshot_by_date ON market_noon_snapshot(trade_date DESC);
CREATE TRIGGER retain_noon_snapshot_update BEFORE UPDATE ON market_noon_snapshot
BEGIN SELECT RAISE(ABORT,'retain noon immutable snapshot'); END;
CREATE TRIGGER retain_noon_snapshot_replace BEFORE INSERT ON market_noon_snapshot
WHEN EXISTS(SELECT 1 FROM market_noon_snapshot WHERE snapshot_id=NEW.snapshot_id OR trade_date=NEW.trade_date OR job_id=NEW.job_id)
BEGIN SELECT RAISE(ABORT,'retain noon immutable snapshot'); END;
CREATE TRIGGER guard_noon_snapshot_delete BEFORE DELETE ON market_noon_snapshot
WHEN noon_purge_allowed(OLD.snapshot_id)!=1
BEGIN SELECT RAISE(ABORT,'noon snapshot requires worker retention authorization'); END;

CREATE TABLE market_noon_purge (
 snapshot_id TEXT PRIMARY KEY,
 trade_date TEXT NOT NULL,
 job_id TEXT NOT NULL REFERENCES job(job_id),
 anchor_date TEXT NOT NULL,
 cutoff_date TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('planned','deleting','purged','protected','warning')),
 manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
 removed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(removed_bytes>=0),
 created_at_ms INTEGER NOT NULL,
 finished_at_ms INTEGER,
 reason TEXT
) STRICT;
CREATE INDEX noon_purge_by_date ON market_noon_purge(trade_date DESC,status);
