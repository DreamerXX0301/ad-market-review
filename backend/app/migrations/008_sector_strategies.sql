-- Board-index strategies are deliberately separate from stock strategies.
CREATE TABLE sector_strategy (
 strategy_id TEXT PRIMARY KEY,
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
 current_version INTEGER NOT NULL DEFAULT 1 CHECK(current_version>0),
 archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
 created_at_ms INTEGER NOT NULL
);
CREATE TABLE sector_strategy_version (
 strategy_id TEXT NOT NULL REFERENCES sector_strategy(strategy_id),
 version INTEGER NOT NULL CHECK(version>0),
 period TEXT NOT NULL CHECK(period IN ('D','W','M')),
 sector_kind TEXT NOT NULL CHECK(sector_kind IN ('all','industry','concept')),
 definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),
 definition_sha256 TEXT NOT NULL,
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 evaluator_version TEXT NOT NULL,
 recorded_at_ms INTEGER NOT NULL,
 PRIMARY KEY(strategy_id,version)
);
CREATE TABLE sector_selection_run (
 run_id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
 strategy_id TEXT NOT NULL,
 strategy_version INTEGER NOT NULL,
 business_date TEXT NOT NULL,
 market_snapshot_id TEXT NOT NULL,
 catalog_signature TEXT NOT NULL,
 input_sha256 TEXT NOT NULL,
 cache_refs_json TEXT NOT NULL CHECK(json_valid(cache_refs_json)),
 status TEXT NOT NULL CHECK(status IN ('running','completed')),
 created_at_ms INTEGER NOT NULL,
 completed_at_ms INTEGER,
 FOREIGN KEY(strategy_id,strategy_version) REFERENCES sector_strategy_version(strategy_id,version)
);
CREATE INDEX sector_selection_run_strategy ON sector_selection_run(strategy_id,created_at_ms);
CREATE TABLE sector_selection_candidate (
 run_id TEXT NOT NULL REFERENCES sector_selection_run(run_id),
 topic_code TEXT NOT NULL,
 sector_kind TEXT NOT NULL CHECK(sector_kind IN ('industry','concept')),
 name TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('matched','unmatched','unknown')),
 close_price_e8 INTEGER,
 metrics_json TEXT NOT NULL CHECK(json_valid(metrics_json)),
 checks_json TEXT NOT NULL CHECK(json_valid(checks_json)),
 cache_refs_json TEXT NOT NULL CHECK(json_valid(cache_refs_json)),
 PRIMARY KEY(run_id,topic_code)
);
