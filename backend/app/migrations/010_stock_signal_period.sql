-- Keep the legacy storage period and its foreign keys unchanged. New signal
-- periods are additive; NULL means the original D/W value for old versions.
ALTER TABLE strategy_version ADD COLUMN signal_period TEXT
  CHECK(signal_period IS NULL OR signal_period IN ('D','W','M'));
