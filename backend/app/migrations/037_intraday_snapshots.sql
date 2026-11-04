CREATE TABLE market_intraday_capture (
    trade_date TEXT NOT NULL,
    slot TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
    snapshot_id TEXT UNIQUE,
    payload_json TEXT,
    content_sha256 TEXT,
    PRIMARY KEY (trade_date, slot),
    CHECK ((snapshot_id IS NULL AND payload_json IS NULL AND content_sha256 IS NULL)
        OR (snapshot_id IS NOT NULL AND payload_json IS NOT NULL AND content_sha256 IS NOT NULL))
);
