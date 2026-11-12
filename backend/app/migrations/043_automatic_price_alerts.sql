-- Automatic closing-price rules are independent of user drawings and their clock.
CREATE TABLE automatic_price_alert_rule (
 rule_id TEXT PRIMARY KEY,
 instrument_id TEXT NOT NULL UNIQUE,
 revision INTEGER NOT NULL CHECK(revision>0),
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 modes_json TEXT NOT NULL CHECK(json_valid(modes_json) AND json_type(modes_json)='array'),
 require_volume INTEGER NOT NULL DEFAULT 1 CHECK(require_volume IN (0,1)),
 algorithm TEXT NOT NULL CHECK(algorithm='structure-volume-levels-v1'),
 effective_after_date TEXT NOT NULL CHECK(date(effective_after_date,'+0 days') IS effective_after_date),
 created_at_ms INTEGER NOT NULL,
 updated_at_ms INTEGER NOT NULL
) STRICT;
CREATE TABLE automatic_price_alert_clock (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 revision INTEGER NOT NULL CHECK(revision>0)
) STRICT;
CREATE TRIGGER automatic_price_alert_rule_insert AFTER INSERT ON automatic_price_alert_rule
BEGIN INSERT INTO automatic_price_alert_clock VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER automatic_price_alert_rule_update AFTER UPDATE ON automatic_price_alert_rule
BEGIN INSERT INTO automatic_price_alert_clock VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER automatic_price_alert_rule_delete AFTER DELETE ON automatic_price_alert_rule
BEGIN INSERT INTO automatic_price_alert_clock VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET revision=revision+1; END;

-- A replaceable next-session baseline never overwrites immutable event evidence.
CREATE TABLE automatic_price_alert_baseline (
 rule_id TEXT PRIMARY KEY REFERENCES automatic_price_alert_rule(rule_id),
 rule_revision INTEGER NOT NULL,
 level_date TEXT NOT NULL,
 market_snapshot_id TEXT NOT NULL,
 baseline_json TEXT NOT NULL CHECK(json_valid(baseline_json)),
 recorded_at_ms INTEGER NOT NULL
) STRICT;
CREATE TABLE automatic_price_alert_event (
 event_id TEXT PRIMARY KEY,
 rule_id TEXT NOT NULL,
 rule_revision INTEGER NOT NULL,
 instrument_id TEXT NOT NULL,
 rule TEXT NOT NULL CHECK(rule IN ('support_reclaim','close_breakout','support_breakdown','stop_breakdown')),
 trade_date TEXT NOT NULL CHECK(date(trade_date,'+0 days') IS trade_date),
 state TEXT NOT NULL CHECK(state IN ('triggered','invalidated')),
 job_id TEXT NOT NULL REFERENCES job(job_id),
 event_json TEXT NOT NULL CHECK(json_valid(event_json)),
 created_at_ms INTEGER NOT NULL,
 UNIQUE(rule_id,rule_revision,rule,trade_date)
) STRICT;
CREATE INDEX automatic_price_alert_event_day ON automatic_price_alert_event(trade_date DESC,created_at_ms DESC);
CREATE INDEX automatic_price_alert_event_stock ON automatic_price_alert_event(instrument_id,trade_date DESC,created_at_ms DESC);
CREATE TRIGGER automatic_price_alert_event_no_update BEFORE UPDATE ON automatic_price_alert_event
BEGIN SELECT RAISE(ABORT,'retain automatic price alert evidence'); END;
CREATE TRIGGER automatic_price_alert_event_no_delete BEFORE DELETE ON automatic_price_alert_event
BEGIN SELECT RAISE(ABORT,'retain automatic price alert evidence'); END;
CREATE TABLE automatic_price_alert_evaluation (
 job_id TEXT NOT NULL REFERENCES job(job_id),
 rule_id TEXT NOT NULL,
 rule_revision INTEGER NOT NULL,
 instrument_id TEXT NOT NULL,
 rule TEXT NOT NULL,
 trade_date TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('triggered','invalidated','not_triggered','unknown')),
 reason TEXT,
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 event_id TEXT REFERENCES automatic_price_alert_event(event_id),
 recorded_at_ms INTEGER NOT NULL,
 PRIMARY KEY(job_id,rule_id,rule_revision,rule,trade_date)
) STRICT;
CREATE INDEX automatic_price_alert_evaluation_stock ON automatic_price_alert_evaluation(instrument_id,trade_date DESC,recorded_at_ms DESC);
CREATE TABLE automatic_price_alert_check (
 job_id TEXT PRIMARY KEY REFERENCES job(job_id),
 business_date TEXT NOT NULL,
 market_snapshot_id TEXT NOT NULL,
 rules_signature TEXT NOT NULL,
 checked_rules INTEGER NOT NULL,
 trigger_count INTEGER NOT NULL,
 unknown_count INTEGER NOT NULL,
 checked_at_ms INTEGER NOT NULL
) STRICT;
