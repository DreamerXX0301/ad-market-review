-- User statements are retained as user evidence, never rewritten as source dates.
CREATE TABLE listing_status_confirmation (
 confirmation_id TEXT PRIMARY KEY,
 instrument_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','unknown')),
 source_kind TEXT NOT NULL CHECK(source_kind='manual_user_confirmation'),
 observed_business_date TEXT NOT NULL CHECK(date(observed_business_date,'+0 days') IS observed_business_date),
 reason TEXT NOT NULL,
 evidence TEXT NOT NULL,
 primary_snapshot_id TEXT NOT NULL REFERENCES dataset_snapshot(snapshot_id),
 market_snapshot_id TEXT NOT NULL,
 quote_presence_json TEXT NOT NULL CHECK(json_valid(quote_presence_json)),
 request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>0),
 UNIQUE(request_key,instrument_id)
) STRICT;
CREATE INDEX listing_confirmation_by_stock ON listing_status_confirmation(instrument_id,created_at_ms DESC);
CREATE TRIGGER retain_listing_confirmation_update BEFORE UPDATE ON listing_status_confirmation BEGIN SELECT RAISE(ABORT,'retain listing confirmation audit'); END;
CREATE TRIGGER retain_listing_confirmation_delete BEFORE DELETE ON listing_status_confirmation BEGIN SELECT RAISE(ABORT,'retain listing confirmation audit'); END;

CREATE TABLE listing_status_revision (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 revision INTEGER NOT NULL CHECK(revision>0)
) STRICT;
CREATE TRIGGER advance_listing_status_revision AFTER INSERT ON listing_status_confirmation
BEGIN INSERT INTO listing_status_revision VALUES(1,1) ON CONFLICT(singleton) DO UPDATE SET revision=revision+1; END;

-- Small derived receipts are keyed by both immutable market generation and
-- manual evidence revision. They do not change source snapshots or their quality.
CREATE TABLE listing_status_summary (
 summary_id TEXT PRIMARY KEY,
 market_snapshot_id TEXT NOT NULL,
 primary_snapshot_id TEXT NOT NULL REFERENCES dataset_snapshot(snapshot_id),
 business_date TEXT NOT NULL CHECK(date(business_date,'+0 days') IS business_date),
 manual_revision INTEGER NOT NULL CHECK(manual_revision>=0),
 reference_hash TEXT NOT NULL,
 identities_json TEXT NOT NULL CHECK(json_valid(identities_json)),
 presence_json TEXT NOT NULL CHECK(json_valid(presence_json)),
 checked_at_ms INTEGER NOT NULL,
 UNIQUE(primary_snapshot_id,market_snapshot_id,business_date,manual_revision,reference_hash)
) STRICT;
CREATE INDEX listing_summary_lookup ON listing_status_summary(market_snapshot_id,business_date,manual_revision);
