-- Explicit manual retention of a server-computed context, never backdated knowledge.
CREATE TABLE market_sentiment_snapshot (
 snapshot_id TEXT PRIMARY KEY,
 trade_date TEXT NOT NULL CHECK(date(trade_date,'+0 days') IS trade_date),
 recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms>0),
 record_mode TEXT NOT NULL CHECK(record_mode='manual_retained_capture'),
 model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 128),
 rule_version TEXT NOT NULL CHECK(length(rule_version) BETWEEN 1 AND 128),
 input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64 AND input_fingerprint NOT GLOB '*[^0-9a-f]*'),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object' AND length(payload_json)<=1048576),
 UNIQUE(trade_date,rule_version,input_fingerprint),
 CHECK(json_extract(payload_json,'$.date') IS trade_date),
 CHECK(json_extract(payload_json,'$.model') IS model),
 CHECK(json_extract(payload_json,'$.provenance.rule_version') IS rule_version),
 CHECK(json_extract(payload_json,'$.provenance.input_fingerprint') IS input_fingerprint),
 CHECK(json_extract(payload_json,'$.provenance.mode') IS 'retained_retrospective')
) STRICT;
CREATE INDEX sentiment_snapshot_by_date ON market_sentiment_snapshot(trade_date,recorded_at_ms DESC,snapshot_id DESC);
CREATE INDEX sentiment_snapshot_by_known ON market_sentiment_snapshot(recorded_at_ms DESC,snapshot_id DESC);
CREATE TRIGGER retain_sentiment_snapshot_update BEFORE UPDATE ON market_sentiment_snapshot
BEGIN SELECT RAISE(ABORT,'retain manual sentiment snapshot'); END;
CREATE TRIGGER retain_sentiment_snapshot_delete BEFORE DELETE ON market_sentiment_snapshot
BEGIN SELECT RAISE(ABORT,'retain manual sentiment snapshot'); END;
-- Prevent INSERT OR REPLACE from deleting an old record even if an external
-- connection disabled recursive delete triggers. The service uses plain INSERT.
CREATE TRIGGER retain_sentiment_snapshot_replace BEFORE INSERT ON market_sentiment_snapshot
WHEN EXISTS(SELECT 1 FROM market_sentiment_snapshot WHERE snapshot_id=NEW.snapshot_id
 OR (trade_date=NEW.trade_date AND rule_version=NEW.rule_version AND input_fingerprint=NEW.input_fingerprint))
BEGIN SELECT RAISE(ABORT,'retain manual sentiment snapshot'); END;
CREATE TRIGGER sentiment_snapshot_clock BEFORE INSERT ON market_sentiment_snapshot
WHEN NEW.recorded_at_ms<coalesce((SELECT max(recorded_at_ms) FROM market_sentiment_snapshot),0)
BEGIN SELECT RAISE(ABORT,'sentiment snapshot clock moved backwards'); END;
