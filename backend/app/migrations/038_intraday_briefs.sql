-- 同日快照派生的累计简报，来源变更后由唯一工作进程重新核验。
CREATE TABLE market_intraday_brief (
    trade_date TEXT NOT NULL,
    slot TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
    body_json TEXT NOT NULL CHECK(json_valid(body_json) AND length(body_json) <= 393216),
    generated_at_ms INTEGER NOT NULL,
    model_attempted INTEGER NOT NULL DEFAULT 0 CHECK(model_attempted IN (0, 1)),
    PRIMARY KEY(trade_date, slot)
) STRICT;
