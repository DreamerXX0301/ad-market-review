CREATE TABLE market_news_snapshot (
    trade_date TEXT NOT NULL CHECK(date(trade_date, '+0 days') IS trade_date),
    stage TEXT NOT NULL CHECK(stage IN ('auction','noon','after_close')),
    status TEXT NOT NULL CHECK(status IN ('complete','partial','unavailable')),
    cutoff_at TEXT NOT NULL,
    window_start_at TEXT,
    captured_at TEXT NOT NULL,
    reason TEXT,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='array'
        AND length(CAST(payload_json AS BLOB))<=2097152),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (trade_date, stage)
) STRICT;

CREATE INDEX market_news_snapshot_cutoff ON market_news_snapshot(cutoff_at DESC);

CREATE TRIGGER market_news_snapshot_no_update BEFORE UPDATE ON market_news_snapshot
BEGIN SELECT RAISE(ABORT, '新闻快照归档后不可修改'); END;

CREATE TABLE market_news_calendar_archive (
    source TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    coverage_start TEXT CHECK(coverage_start IS NULL OR date(coverage_start, '+0 days') IS coverage_start),
    coverage_end TEXT CHECK(coverage_end IS NULL OR date(coverage_end, '+0 days') IS coverage_end),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='array'
        AND length(CAST(payload_json AS BLOB))<=2097152),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (source, captured_at),
    CHECK((coverage_start IS NULL AND coverage_end IS NULL)
        OR (coverage_start IS NOT NULL AND coverage_end IS NOT NULL AND coverage_start<=coverage_end))
) STRICT;

CREATE TRIGGER market_news_calendar_no_update BEFORE UPDATE ON market_news_calendar_archive
BEGIN SELECT RAISE(ABORT, '事件日历来源版本不可修改'); END;
