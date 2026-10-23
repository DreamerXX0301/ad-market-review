-- An independent, user-managed stock pool. Entry evidence is copied once from
-- the original tracking round; watchlist removal never removes that round.
CREATE TABLE watchlist_item (
  instrument_id TEXT PRIMARY KEY,
  source_kind TEXT NOT NULL CHECK(source_kind IN ('stock','tracking','sector','topic')),
  source_id TEXT,
  source_name TEXT NOT NULL,
  observed_date TEXT CHECK(observed_date IS NULL OR date(observed_date,'+0 days') IS observed_date),
  added_business_date TEXT NOT NULL CHECK(date(added_business_date,'+0 days') IS added_business_date),
  tracking_round_id TEXT REFERENCES tracking_round(round_id),
  tracking_entry_date TEXT CHECK(tracking_entry_date IS NULL OR date(tracking_entry_date,'+0 days') IS tracking_entry_date),
  event_price_e8 INTEGER CHECK(event_price_e8 IS NULL OR event_price_e8>0),
  event_basis_json TEXT NOT NULL CHECK(json_valid(event_basis_json)),
  created_at_ms INTEGER NOT NULL CHECK(created_at_ms>0),
  CHECK((tracking_round_id IS NULL AND tracking_entry_date IS NULL AND event_price_e8 IS NULL)
     OR (tracking_round_id IS NOT NULL AND tracking_entry_date IS NOT NULL AND event_price_e8 IS NOT NULL))
) STRICT;
CREATE INDEX watchlist_created ON watchlist_item(created_at_ms DESC,instrument_id);
