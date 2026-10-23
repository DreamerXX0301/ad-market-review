-- Small provenance receipts for user-confirmed local support/resistance levels.
-- The price source remains immutable; these marks never modify market files.
ALTER TABLE chart_annotation ADD COLUMN origin_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(origin_json) AND json_type(origin_json)='object');
ALTER TABLE chart_annotation ADD COLUMN origin_key TEXT;
ALTER TABLE chart_annotation ADD COLUMN instrument_kind TEXT NOT NULL DEFAULT 'stock' CHECK(instrument_kind IN ('stock','index') AND (instrument_kind='stock' OR (price_basis='none' AND auto_watchlist=0 AND json_array_length(alert_modes_json)=0)));
CREATE UNIQUE INDEX chart_annotation_origin_key ON chart_annotation(instrument_id,origin_key) WHERE origin_key IS NOT NULL;
