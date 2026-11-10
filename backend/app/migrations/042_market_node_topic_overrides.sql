CREATE TABLE market_node_topic_override (
    trade_date TEXT NOT NULL CHECK(date(trade_date, '+0 days') IS trade_date),
    instrument_id TEXT NOT NULL CHECK(
        instrument_id GLOB '[0-9][0-9][0-9][0-9][0-9][0-9].S[HZ]'
        OR instrument_id GLOB '[0-9][0-9][0-9][0-9][0-9][0-9].BJ'),
    topic_id TEXT NOT NULL CHECK(length(topic_id) BETWEEN 1 AND 200),
    topic_name TEXT NOT NULL CHECK(length(topic_name) BETWEEN 1 AND 80),
    topic_kind TEXT NOT NULL CHECK(topic_kind IN ('industry', 'concept')),
    revision TEXT NOT NULL CHECK(length(revision)=32 AND revision NOT GLOB '*[^0-9a-f]*'),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms>0),
    PRIMARY KEY (trade_date, instrument_id)
) STRICT, WITHOUT ROWID;
