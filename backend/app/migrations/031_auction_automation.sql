-- 仅用户明确开启的内置竞价计划；新安装和恢复副本默认关闭。
CREATE TABLE market_auction_automation (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 version TEXT NOT NULL CHECK(version='auction-automation-v1'),
 revision INTEGER NOT NULL CHECK(revision>=0),
 mode TEXT NOT NULL CHECK(mode IN('off','once','daily')),
 trade_date TEXT CHECK(trade_date IS NULL OR date(trade_date,'+0 days') IS trade_date),
 instrument_ids_json TEXT NOT NULL CHECK(json_valid(instrument_ids_json) AND json_type(instrument_ids_json)='array' AND json_array_length(instrument_ids_json)<=100),
 updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms>=0),
 CHECK((mode='once' AND trade_date IS NOT NULL) OR (mode IN('off','daily') AND trade_date IS NULL))
) STRICT;
INSERT INTO market_auction_automation VALUES(1,'auction-automation-v1',0,'off',NULL,'[]',0);
CREATE TRIGGER auction_automation_revision BEFORE UPDATE ON market_auction_automation
WHEN NEW.singleton<>OLD.singleton OR NEW.version<>OLD.version OR NEW.revision<>OLD.revision+1
BEGIN SELECT RAISE(ABORT,'auction automation requires the next revision'); END;
CREATE TRIGGER retain_auction_automation_config BEFORE DELETE ON market_auction_automation
BEGIN SELECT RAISE(ABORT,'disable auction automation instead of deleting configuration'); END;

CREATE TABLE market_auction_automation_run (
 trade_date TEXT PRIMARY KEY CHECK(date(trade_date,'+0 days') IS trade_date),
 job_id TEXT UNIQUE REFERENCES job(job_id),
 schedule_revision INTEGER NOT NULL CHECK(schedule_revision>0),
 state TEXT NOT NULL CHECK(state IN('queued','running','waiting_retry','available','failed','paused','cancelled','skipped','expired')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
 automatic_control INTEGER NOT NULL DEFAULT 1 CHECK(automatic_control IN(0,1)),
 last_attempt_owner TEXT,
 last_attempt_at_ms INTEGER,
 retry_after_at_ms INTEGER,
 outcome TEXT CHECK(outcome IS NULL OR outcome IN('not_ready','failed','available','skipped','stopped')),
 reason TEXT,
 created_at_ms INTEGER NOT NULL CHECK(created_at_ms>0),
 updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms>0)
) STRICT;
CREATE INDEX auction_automation_run_latest ON market_auction_automation_run(trade_date DESC);
CREATE TRIGGER retain_auction_automation_run_identity BEFORE UPDATE ON market_auction_automation_run
WHEN NEW.trade_date<>OLD.trade_date OR NEW.schedule_revision<>OLD.schedule_revision
 OR (OLD.job_id IS NOT NULL AND NEW.job_id IS NOT OLD.job_id)
BEGIN SELECT RAISE(ABORT,'retain auction automation day and original job'); END;
CREATE TRIGGER retain_auction_automation_run_delete BEFORE DELETE ON market_auction_automation_run
BEGIN SELECT RAISE(ABORT,'retain auction automation daily receipt'); END;
