-- 竞价与日线发布隔离；一次成功或显式取消后不创建第二个当天任务。
CREATE TABLE market_auction_capture (
 trade_date TEXT PRIMARY KEY CHECK(date(trade_date,'+0 days') IS trade_date),
 job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
 request_key TEXT NOT NULL UNIQUE,
 requested_at_ms INTEGER NOT NULL CHECK(requested_at_ms>0)
) STRICT;
CREATE TRIGGER retain_auction_capture_update BEFORE UPDATE ON market_auction_capture
BEGIN SELECT RAISE(ABORT,'retain auction daily capture ledger'); END;
CREATE TRIGGER retain_auction_capture_delete BEFORE DELETE ON market_auction_capture
BEGIN SELECT RAISE(ABORT,'retain auction daily capture ledger'); END;
CREATE TRIGGER retain_auction_capture_replace BEFORE INSERT ON market_auction_capture
WHEN EXISTS(SELECT 1 FROM market_auction_capture WHERE trade_date=NEW.trade_date OR job_id=NEW.job_id OR request_key=NEW.request_key)
BEGIN SELECT RAISE(ABORT,'retain auction daily capture ledger'); END;

CREATE TABLE market_auction_snapshot (
 snapshot_id TEXT PRIMARY KEY,
 trade_date TEXT NOT NULL UNIQUE REFERENCES market_auction_capture(trade_date),
 job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
 collected_start_at TEXT NOT NULL,
 collected_end_at TEXT NOT NULL,
 recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms>0),
 quality TEXT NOT NULL CHECK(quality IN('complete','partial')),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object' AND length(payload_json)<=4194304),
 CHECK(json_extract(payload_json,'$.date') IS trade_date),
 CHECK(json_extract(payload_json,'$.version') IS 'auction-snapshot-v1')
) STRICT;
CREATE INDEX auction_snapshot_by_date ON market_auction_snapshot(trade_date DESC);
CREATE TRIGGER guard_auction_snapshot_identity BEFORE INSERT ON market_auction_snapshot
WHEN NOT EXISTS(SELECT 1 FROM market_auction_capture c WHERE c.trade_date=NEW.trade_date AND c.job_id=NEW.job_id)
BEGIN SELECT RAISE(ABORT,'auction capture identity mismatch'); END;
CREATE TRIGGER retain_auction_snapshot_update BEFORE UPDATE ON market_auction_snapshot
BEGIN SELECT RAISE(ABORT,'retain immutable auction snapshot'); END;
CREATE TRIGGER retain_auction_snapshot_delete BEFORE DELETE ON market_auction_snapshot
BEGIN SELECT RAISE(ABORT,'retain immutable auction snapshot'); END;
CREATE TRIGGER retain_auction_snapshot_replace BEFORE INSERT ON market_auction_snapshot
WHEN EXISTS(SELECT 1 FROM market_auction_snapshot WHERE snapshot_id=NEW.snapshot_id OR trade_date=NEW.trade_date OR job_id=NEW.job_id)
BEGIN SELECT RAISE(ABORT,'retain immutable auction snapshot'); END;
