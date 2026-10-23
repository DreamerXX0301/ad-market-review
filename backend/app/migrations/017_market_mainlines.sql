-- A user-entered daily attribution is not a directory membership or provider fact.
-- Each revision is a complete replacement state; revoke appends an empty state.
CREATE TABLE market_mainline_revision (
 revision_id TEXT PRIMARY KEY,
 trade_date TEXT NOT NULL CHECK(date(trade_date,'+0 days') IS trade_date),
 instrument_id TEXT NOT NULL,
 instrument_name TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>0),
 action TEXT NOT NULL CHECK(action IN ('replace','revoke')),
 source_kind TEXT NOT NULL CHECK(source_kind='manual_user_entry'),
 change_reason TEXT NOT NULL CHECK(length(trim(change_reason))>0),
 attributions_json TEXT NOT NULL CHECK(json_valid(attributions_json) AND json_type(attributions_json)='array'),
 recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms>0),
 supersedes_revision_id TEXT REFERENCES market_mainline_revision(revision_id),
 UNIQUE(trade_date,instrument_id,revision),
 CHECK((action='revoke' AND json_array_length(attributions_json)=0)
    OR (action='replace' AND json_array_length(attributions_json) BETWEEN 1 AND 12))
) STRICT;
CREATE INDEX mainline_by_date_known ON market_mainline_revision(trade_date,recorded_at_ms,instrument_id,revision);
CREATE TRIGGER retain_mainline_revision_update BEFORE UPDATE ON market_mainline_revision
BEGIN SELECT RAISE(ABORT,'retain daily mainline audit'); END;
CREATE TRIGGER retain_mainline_revision_delete BEFORE DELETE ON market_mainline_revision
BEGIN SELECT RAISE(ABORT,'retain daily mainline audit'); END;
CREATE TRIGGER chain_mainline_revision BEFORE INSERT ON market_mainline_revision
BEGIN
 SELECT CASE WHEN NEW.revision!=coalesce((SELECT max(revision) FROM market_mainline_revision WHERE trade_date=NEW.trade_date AND instrument_id=NEW.instrument_id),0)+1
 THEN RAISE(ABORT,'daily mainline revision must append') END;
 SELECT CASE WHEN NEW.supersedes_revision_id IS NOT (SELECT revision_id FROM market_mainline_revision WHERE trade_date=NEW.trade_date AND instrument_id=NEW.instrument_id ORDER BY revision DESC LIMIT 1)
 THEN RAISE(ABORT,'daily mainline predecessor mismatch') END;
 SELECT CASE WHEN NEW.recorded_at_ms<coalesce((SELECT recorded_at_ms FROM market_mainline_revision WHERE trade_date=NEW.trade_date AND instrument_id=NEW.instrument_id ORDER BY revision DESC LIMIT 1),0)
 THEN RAISE(ABORT,'daily mainline clock moved backwards') END;
END;
