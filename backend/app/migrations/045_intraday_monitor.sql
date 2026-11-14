-- A separate sampling stream. Closing-price alerts and tracking rounds are unchanged.
CREATE TABLE intraday_monitor_batch (
 batch_id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
 trade_date TEXT NOT NULL CHECK(date(trade_date,'+0 days') IS trade_date),
 slot_key TEXT NOT NULL,
 config_revision INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed','missed','no_change')),
 requested_count INTEGER NOT NULL DEFAULT 0,
 valid_count INTEGER NOT NULL DEFAULT 0,
 unknown_count INTEGER NOT NULL DEFAULT 0,
 event_count INTEGER NOT NULL DEFAULT 0,
 level_unknown_count INTEGER NOT NULL DEFAULT 0,
 started_at_ms INTEGER,
 completed_at_ms INTEGER,
 reason TEXT,
 manifest_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(manifest_json)),
 cleanup_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(cleanup_json)),
 purged_at_ms INTEGER,
 UNIQUE(trade_date,slot_key)
) STRICT;
CREATE INDEX intraday_monitor_batch_day ON intraday_monitor_batch(trade_date DESC,slot_key DESC);

CREATE TABLE intraday_monitor_baseline (
 trade_date TEXT NOT NULL,
 instrument_id TEXT NOT NULL,
 market_snapshot_id TEXT NOT NULL,
 baseline_json TEXT NOT NULL CHECK(json_valid(baseline_json)),
 PRIMARY KEY(trade_date,instrument_id)
) STRICT;
CREATE TABLE intraday_monitor_state (
 instrument_id TEXT PRIMARY KEY,
 trade_date TEXT NOT NULL,
 slot_key TEXT NOT NULL,
 config_revision INTEGER NOT NULL,
 state_json TEXT NOT NULL CHECK(json_valid(state_json)),
 quote_json TEXT NOT NULL CHECK(json_valid(quote_json)),
 levels_json TEXT NOT NULL CHECK(json_valid(levels_json)),
 updated_at_ms INTEGER NOT NULL
) STRICT;

-- Mutable card projections are backed by immutable, versioned source receipts.
CREATE TABLE intraday_monitor_event (
 event_id TEXT PRIMARY KEY,
 instrument_id TEXT NOT NULL,
 name TEXT NOT NULL,
 trade_date TEXT NOT NULL CHECK(date(trade_date,'+0 days') IS trade_date),
 type TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('observed','holding')),
 lower_price REAL NOT NULL CHECK(lower_price>0),
 upper_price REAL NOT NULL CHECK(upper_price>=lower_price),
 first_seen_at_ms INTEGER NOT NULL,
 last_seen_at_ms INTEGER NOT NULL,
 confirmed_at_ms INTEGER,
 latest_price REAL NOT NULL CHECK(latest_price>0),
 change_pct REAL,
 read_at_ms INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX intraday_monitor_event_day ON intraday_monitor_event(trade_date DESC,last_seen_at_ms DESC,event_id);
CREATE INDEX intraday_monitor_event_stock ON intraday_monitor_event(instrument_id,trade_date,type,last_seen_at_ms DESC);
CREATE TABLE intraday_monitor_evidence (
 receipt_id TEXT PRIMARY KEY,
 event_id TEXT NOT NULL REFERENCES intraday_monitor_event(event_id),
 batch_id TEXT NOT NULL REFERENCES intraday_monitor_batch(batch_id),
 rule_key TEXT NOT NULL,
 episode TEXT NOT NULL,
 type TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('observed','holding')),
 observed_at_ms INTEGER NOT NULL,
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 UNIQUE(rule_key,episode,type,phase)
) STRICT;
CREATE INDEX intraday_monitor_evidence_event ON intraday_monitor_evidence(event_id,observed_at_ms,receipt_id);
CREATE INDEX intraday_monitor_evidence_batch ON intraday_monitor_evidence(batch_id);
CREATE TRIGGER intraday_monitor_evidence_no_update BEFORE UPDATE ON intraday_monitor_evidence
BEGIN SELECT RAISE(ABORT,'retain intraday monitor source evidence'); END;
CREATE TRIGGER intraday_monitor_evidence_no_delete BEFORE DELETE ON intraday_monitor_evidence
WHEN intraday_monitor_expiry_allowed(OLD.event_id)!=1
BEGIN SELECT RAISE(ABORT,'retain intraday monitor source evidence'); END;
CREATE TRIGGER intraday_monitor_event_no_delete BEFORE DELETE ON intraday_monitor_event
WHEN intraday_monitor_expiry_allowed(OLD.event_id)!=1
BEGIN SELECT RAISE(ABORT,'retain intraday monitor card until authorized expiry'); END;
