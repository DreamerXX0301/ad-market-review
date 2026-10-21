-- Platform settings and on-demand research metadata; values contain no plaintext keys.
CREATE TABLE platform_setting (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL CHECK(json_valid(value_json)),
  updated_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE research_report (
  report_id TEXT PRIMARY KEY,
  job_id TEXT REFERENCES job(job_id),
  instrument_id TEXT NOT NULL,
  data_cutoff TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  report_json TEXT NOT NULL CHECK(json_valid(report_json)),
  snapshot_refs_json TEXT NOT NULL CHECK(json_valid(snapshot_refs_json))
) STRICT;
CREATE INDEX research_by_instrument ON research_report(instrument_id,created_at_ms DESC);
