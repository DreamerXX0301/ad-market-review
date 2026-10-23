-- Explicit user rules; legacy visual marks remain unarmed reference lines.
ALTER TABLE chart_annotation ADD COLUMN line_type TEXT NOT NULL DEFAULT 'reference' CHECK(line_type IN ('reference','support','resistance'));
ALTER TABLE chart_annotation ADD COLUMN alert_modes_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(alert_modes_json) AND json_type(alert_modes_json)='array');
ALTER TABLE chart_annotation ADD COLUMN auto_watchlist INTEGER NOT NULL DEFAULT 0 CHECK(auto_watchlist IN (0,1));
ALTER TABLE chart_annotation ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0);
ALTER TABLE chart_annotation ADD COLUMN effective_business_date TEXT CHECK(effective_business_date IS NULL OR date(effective_business_date,'+0 days') IS effective_business_date);

CREATE TABLE line_alert_event (
 event_id TEXT PRIMARY KEY,
 annotation_id TEXT NOT NULL,
 annotation_revision INTEGER NOT NULL,
 instrument_id TEXT NOT NULL,
 line_type TEXT NOT NULL CHECK(line_type IN ('support','resistance')),
 rule TEXT NOT NULL,
 trade_date TEXT NOT NULL CHECK(date(trade_date,'+0 days') IS trade_date),
 state TEXT NOT NULL CHECK(state IN ('triggered','invalidated')),
 job_id TEXT NOT NULL REFERENCES job(job_id),
 event_json TEXT NOT NULL CHECK(json_valid(event_json)),
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>0),
 UNIQUE(annotation_id,annotation_revision,rule,trade_date)
) STRICT;
CREATE INDEX line_alert_by_stock ON line_alert_event(instrument_id,trade_date DESC,created_at_ms DESC);
CREATE INDEX line_alert_by_day ON line_alert_event(trade_date DESC,created_at_ms DESC);
CREATE TRIGGER retain_line_alert_update BEFORE UPDATE ON line_alert_event BEGIN SELECT RAISE(ABORT,'retain line alert evidence'); END;
CREATE TRIGGER retain_line_alert_delete BEFORE DELETE ON line_alert_event BEGIN SELECT RAISE(ABORT,'retain line alert evidence'); END;

CREATE TABLE line_alert_evaluation (
 job_id TEXT NOT NULL REFERENCES job(job_id),
 annotation_id TEXT NOT NULL,
 annotation_revision INTEGER NOT NULL,
 rule TEXT NOT NULL,
 trade_date TEXT NOT NULL CHECK(date(trade_date,'+0 days') IS trade_date),
 state TEXT NOT NULL CHECK(state IN ('triggered','invalidated','not_triggered','unknown')),
 reason TEXT,
 event_id TEXT REFERENCES line_alert_event(event_id),
 recorded_at_ms INTEGER NOT NULL,
 PRIMARY KEY(job_id,annotation_id,annotation_revision,rule,trade_date)
) STRICT;
CREATE INDEX line_alert_evaluation_progress ON line_alert_evaluation(annotation_id,annotation_revision,rule,trade_date);
CREATE TABLE line_alert_check (
 job_id TEXT PRIMARY KEY REFERENCES job(job_id),
 business_date TEXT NOT NULL,
 market_snapshot_id TEXT NOT NULL,
 rules_signature TEXT NOT NULL,
 checked_rules INTEGER NOT NULL,
 trigger_count INTEGER NOT NULL,
 unknown_count INTEGER NOT NULL,
 checked_at_ms INTEGER NOT NULL
) STRICT;

-- A single monotonic token avoids hashing all enabled rules under every event
-- transaction. Pure label/color edits do not invalidate a frozen check.
CREATE TABLE line_alert_rule_clock (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 revision INTEGER NOT NULL CHECK(revision>0)
) STRICT;
CREATE TRIGGER line_alert_rule_insert AFTER INSERT ON chart_annotation
WHEN NEW.line_type!='reference' AND json_array_length(NEW.alert_modes_json)>0
BEGIN INSERT INTO line_alert_rule_clock VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER line_alert_rule_delete AFTER DELETE ON chart_annotation
WHEN OLD.line_type!='reference' AND json_array_length(OLD.alert_modes_json)>0
BEGIN INSERT INTO line_alert_rule_clock VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER line_alert_rule_update AFTER UPDATE OF price,price_basis,anchor_date,line_type,alert_modes_json,auto_watchlist,revision,effective_business_date ON chart_annotation
WHEN (OLD.line_type!='reference' AND json_array_length(OLD.alert_modes_json)>0 OR NEW.line_type!='reference' AND json_array_length(NEW.alert_modes_json)>0)
AND (OLD.price IS NOT NEW.price OR OLD.price_basis IS NOT NEW.price_basis OR OLD.anchor_date IS NOT NEW.anchor_date
 OR OLD.line_type IS NOT NEW.line_type OR OLD.alert_modes_json IS NOT NEW.alert_modes_json
 OR OLD.auto_watchlist IS NOT NEW.auto_watchlist OR OLD.revision IS NOT NEW.revision OR OLD.effective_business_date IS NOT NEW.effective_business_date)
BEGIN INSERT INTO line_alert_rule_clock VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET revision=revision+1; END;
