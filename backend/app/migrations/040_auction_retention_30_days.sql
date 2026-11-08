-- Preserve existing receipts exactly while recognizing both frozen cutoffs.
CREATE TABLE market_auction_purge_040 (
    snapshot_id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL UNIQUE REFERENCES market_auction_capture(trade_date),
    source_job_id TEXT NOT NULL REFERENCES job(job_id),
    cleanup_job_id TEXT NOT NULL REFERENCES job(job_id),
    anchor_date TEXT NOT NULL,
    cutoff_date TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_bytes INTEGER NOT NULL CHECK(payload_bytes>0 AND payload_bytes<=4194304),
    evidence_count INTEGER NOT NULL CHECK(evidence_count BETWEEN 0 AND 3),
    status TEXT NOT NULL CHECK(status IN ('protected','warning','purged')),
    reason TEXT,
    check_sequence INTEGER NOT NULL CHECK(check_sequence>0),
    checked_at_ms INTEGER NOT NULL CHECK(checked_at_ms>0),
    purged_at_ms INTEGER,
    CHECK(date(trade_date,'+0 days') IS trade_date AND date(anchor_date,'+0 days') IS anchor_date
      AND date(cutoff_date,'+0 days') IS cutoff_date),
    CHECK(trade_date<cutoff_date AND cutoff_date IN (date(anchor_date,'-27 days'),date(anchor_date,'-29 days'))),
    CHECK((status='purged' AND purged_at_ms IS NOT NULL AND reason IS NULL)
       OR (status<>'purged' AND purged_at_ms IS NULL))
) STRICT;

INSERT INTO market_auction_purge_040
    SELECT * FROM market_auction_purge;
DROP TRIGGER guard_auction_snapshot_after_purge;
DROP TABLE market_auction_purge;
ALTER TABLE market_auction_purge_040 RENAME TO market_auction_purge;
CREATE INDEX auction_purge_date ON market_auction_purge(trade_date,status);

CREATE TRIGGER guard_auction_purge_insert BEFORE INSERT ON market_auction_purge
WHEN auction_purge_allowed(NEW.snapshot_id)<>1 OR NOT EXISTS(
    SELECT 1 FROM market_auction_capture c WHERE c.trade_date=NEW.trade_date AND c.job_id=NEW.source_job_id)
 OR NOT EXISTS(
    SELECT 1 FROM job j WHERE j.job_id=NEW.cleanup_job_id AND j.kind='increment'
     AND j.status IN ('succeeded','partial','no_change')
     AND NEW.cutoff_date IS CASE json_extract(j.parameters_json,'$.auction_retention_policy')
        WHEN 'auction-retention-28d-v1' THEN date(NEW.anchor_date,'-27 days')
        WHEN 'auction-retention-30d-v2' THEN date(NEW.anchor_date,'-29 days')
     END)
BEGIN SELECT RAISE(ABORT,'auction purge receipt requires exact worker authorization and frozen retention'); END;
CREATE TRIGGER guard_auction_purge_update BEFORE UPDATE ON market_auction_purge
WHEN auction_purge_allowed(OLD.snapshot_id)<>1 OR OLD.status='purged'
 OR NEW.snapshot_id<>OLD.snapshot_id OR NEW.trade_date<>OLD.trade_date
 OR NEW.source_job_id<>OLD.source_job_id OR NEW.content_sha256<>OLD.content_sha256
 OR NOT EXISTS(
    SELECT 1 FROM job j WHERE j.job_id=NEW.cleanup_job_id AND j.kind='increment'
     AND j.status IN ('succeeded','partial','no_change')
     AND NEW.cutoff_date IS CASE json_extract(j.parameters_json,'$.auction_retention_policy')
        WHEN 'auction-retention-28d-v1' THEN date(NEW.anchor_date,'-27 days')
        WHEN 'auction-retention-30d-v2' THEN date(NEW.anchor_date,'-29 days')
     END)
BEGIN SELECT RAISE(ABORT,'auction purge receipt is immutable outside exact worker authorization and frozen retention'); END;
CREATE TRIGGER retain_auction_purge_delete BEFORE DELETE ON market_auction_purge
BEGIN SELECT RAISE(ABORT,'auction purge receipt is permanent'); END;
CREATE TRIGGER retain_auction_purge_replace BEFORE INSERT ON market_auction_purge
WHEN EXISTS(SELECT 1 FROM market_auction_purge p WHERE p.snapshot_id=NEW.snapshot_id AND p.status='purged')
BEGIN SELECT RAISE(ABORT,'auction purge receipt is permanent'); END;
CREATE TRIGGER guard_auction_snapshot_after_purge BEFORE INSERT ON market_auction_snapshot
WHEN EXISTS(SELECT 1 FROM market_auction_purge p WHERE p.trade_date=NEW.trade_date AND p.status='purged')
BEGIN SELECT RAISE(ABORT,'purged auction date cannot be recaptured'); END;
