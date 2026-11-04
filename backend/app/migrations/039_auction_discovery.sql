CREATE TABLE market_auction_discovery (
 trade_date TEXT PRIMARY KEY REFERENCES market_auction_capture(trade_date),
 job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
 primary_snapshot_id TEXT NOT NULL UNIQUE REFERENCES market_auction_snapshot(snapshot_id),
 recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms>0),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object' AND length(CAST(payload_json AS BLOB))<=3145728),
 CHECK(json_extract(payload_json,'$.date') IS trade_date),
 CHECK(json_extract(payload_json,'$.version') IS 'auction-discovery-v1'),
 CHECK(json_extract(payload_json,'$.state') IN ('available','partial','unavailable'))
) STRICT;
CREATE TRIGGER guard_auction_discovery_identity BEFORE INSERT ON market_auction_discovery
WHEN NOT EXISTS(SELECT 1 FROM market_auction_snapshot s WHERE s.trade_date=NEW.trade_date
 AND s.job_id=NEW.job_id AND s.snapshot_id=NEW.primary_snapshot_id)
 OR EXISTS(SELECT 1 FROM market_auction_discovery d WHERE d.trade_date=NEW.trade_date
 OR d.job_id=NEW.job_id OR d.primary_snapshot_id=NEW.primary_snapshot_id)
BEGIN SELECT RAISE(ABORT,'auction discovery identity mismatch or already retained'); END;
CREATE TRIGGER retain_auction_discovery_update BEFORE UPDATE ON market_auction_discovery
BEGIN SELECT RAISE(ABORT,'retain immutable auction discovery'); END;
CREATE TRIGGER retain_auction_discovery_delete BEFORE DELETE ON market_auction_discovery
WHEN coalesce(auction_purge_allowed(OLD.primary_snapshot_id),0)!=1
BEGIN SELECT RAISE(ABORT,'retain immutable auction discovery'); END;
