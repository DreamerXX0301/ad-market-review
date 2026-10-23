-- Personal-pool performance has its own first-added closing-price baseline.
-- Existing rows are resolved by exact added_business_date against local
-- immutable published quotes; neither tracking prices nor latest prices backfill it.
ALTER TABLE watchlist_item ADD COLUMN watchlist_entry_price_e8 INTEGER
  CHECK(watchlist_entry_price_e8 IS NULL OR watchlist_entry_price_e8>0);
ALTER TABLE watchlist_item ADD COLUMN watchlist_entry_basis_json TEXT NOT NULL DEFAULT '{}'
  CHECK(json_valid(watchlist_entry_basis_json));

CREATE TRIGGER watchlist_entry_baseline_frozen BEFORE UPDATE ON watchlist_item
WHEN OLD.watchlist_entry_price_e8 IS NOT NULL AND (
  NEW.watchlist_entry_price_e8 IS NOT OLD.watchlist_entry_price_e8
  OR NEW.watchlist_entry_basis_json IS NOT OLD.watchlist_entry_basis_json
  OR NEW.added_business_date IS NOT OLD.added_business_date
  OR NEW.instrument_id IS NOT OLD.instrument_id
  OR NEW.created_at_ms IS NOT OLD.created_at_ms
) BEGIN
  SELECT RAISE(ABORT,'watchlist first-added closing baseline is immutable');
END;
