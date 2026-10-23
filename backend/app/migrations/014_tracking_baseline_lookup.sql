CREATE INDEX tracking_round_by_instrument_date
ON tracking_round(instrument_id, entry_business_date DESC, created_at_ms DESC);
