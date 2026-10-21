-- Research-only access TTL. Existing reports keep their original age.
ALTER TABLE research_report ADD COLUMN last_accessed_at_ms INTEGER NOT NULL DEFAULT 0 CHECK(last_accessed_at_ms>=0);
ALTER TABLE research_report ADD COLUMN expires_at_ms INTEGER NOT NULL DEFAULT 0 CHECK(expires_at_ms>=0);
UPDATE research_report SET last_accessed_at_ms=created_at_ms,expires_at_ms=created_at_ms+604800000;
CREATE INDEX research_cache_expiry ON research_report(expires_at_ms);
