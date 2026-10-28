-- 午评与同日盘后事实的追加核验；派生证据留在业务库，不复制原始行情。
CREATE TABLE market_noon_close_review (
 review_id TEXT PRIMARY KEY,
 snapshot_id TEXT NOT NULL REFERENCES market_noon_snapshot(snapshot_id) ON DELETE CASCADE,
 trade_date TEXT NOT NULL CHECK(date(trade_date,'+0 days') IS trade_date),
 revision INTEGER NOT NULL CHECK(revision>0),
 rule_version TEXT NOT NULL CHECK(rule_version='noon-close-v1'),
 generated_at_ms INTEGER NOT NULL CHECK(generated_at_ms>0),
 source_job_id TEXT NOT NULL REFERENCES job(job_id),
 source_snapshot_id TEXT NOT NULL,
 noon_sha256 TEXT NOT NULL CHECK(length(noon_sha256)=64 AND noon_sha256 NOT GLOB '*[^0-9a-f]*'),
 close_input_sha256 TEXT NOT NULL CHECK(length(close_input_sha256)=64 AND close_input_sha256 NOT GLOB '*[^0-9a-f]*'),
 result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*'),
 state TEXT NOT NULL CHECK(state IN('available','partial')),
 backfilled INTEGER NOT NULL CHECK(backfilled IN(0,1)),
 input_json TEXT NOT NULL CHECK(json_valid(input_json) AND json_type(input_json)='object' AND length(input_json)<=8388608),
 provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json) AND json_type(provenance_json)='object' AND length(provenance_json)<=262144),
 result_json TEXT NOT NULL CHECK(json_valid(result_json) AND json_type(result_json)='object' AND length(result_json)<=2097152),
 UNIQUE(snapshot_id,revision),
 UNIQUE(snapshot_id,noon_sha256,close_input_sha256,rule_version)
) STRICT;
CREATE INDEX noon_close_by_date ON market_noon_close_review(trade_date DESC,revision DESC);
CREATE TRIGGER guard_noon_close_identity BEFORE INSERT ON market_noon_close_review
WHEN NOT EXISTS(SELECT 1 FROM market_noon_snapshot s WHERE s.snapshot_id=NEW.snapshot_id
 AND s.trade_date=NEW.trade_date AND s.content_sha256=NEW.noon_sha256)
BEGIN SELECT RAISE(ABORT,'noon close identity mismatch'); END;
CREATE TRIGGER retain_noon_close_update BEFORE UPDATE ON market_noon_close_review
BEGIN SELECT RAISE(ABORT,'retain immutable noon close review'); END;
CREATE TRIGGER retain_noon_close_replace BEFORE INSERT ON market_noon_close_review
WHEN EXISTS(SELECT 1 FROM market_noon_close_review WHERE review_id=NEW.review_id)
BEGIN SELECT RAISE(ABORT,'retain immutable noon close review'); END;
CREATE TRIGGER guard_noon_close_delete BEFORE DELETE ON market_noon_close_review
WHEN noon_purge_allowed(OLD.snapshot_id)!=1
BEGIN SELECT RAISE(ABORT,'noon close requires parent retention authorization'); END;

CREATE TABLE market_noon_close_item (
 review_id TEXT NOT NULL REFERENCES market_noon_close_review(review_id) ON DELETE CASCADE,
 snapshot_id TEXT NOT NULL REFERENCES market_noon_snapshot(snapshot_id) ON DELETE CASCADE,
 instrument_id TEXT NOT NULL,
 name TEXT NOT NULL,
 board_key TEXT NOT NULL CHECK(board_key IN('main','chinext','star','beijing','unknown')),
 transition TEXT NOT NULL,
 final_board_count INTEGER,
 afternoon_change_pct REAL,
 close_change_pct REAL,
 noon_change_pct REAL,
 amount REAL,
 sections_json TEXT NOT NULL CHECK(json_valid(sections_json) AND json_type(sections_json)='array'),
 classification_keys_json TEXT NOT NULL CHECK(json_valid(classification_keys_json) AND json_type(classification_keys_json)='array'),
 item_json TEXT NOT NULL CHECK(json_valid(item_json) AND json_type(item_json)='object' AND length(item_json)<=65536),
 item_sha256 TEXT NOT NULL CHECK(length(item_sha256)=64 AND item_sha256 NOT GLOB '*[^0-9a-f]*'),
 PRIMARY KEY(review_id,instrument_id)
) STRICT;
CREATE INDEX noon_close_items_filter ON market_noon_close_item(review_id,board_key,transition,final_board_count DESC,instrument_id);
CREATE TRIGGER guard_noon_close_item_identity BEFORE INSERT ON market_noon_close_item
WHEN NOT EXISTS(SELECT 1 FROM market_noon_close_review r WHERE r.review_id=NEW.review_id AND r.snapshot_id=NEW.snapshot_id)
BEGIN SELECT RAISE(ABORT,'noon close item identity mismatch'); END;
CREATE TRIGGER retain_noon_close_item_update BEFORE UPDATE ON market_noon_close_item
BEGIN SELECT RAISE(ABORT,'retain immutable noon close item'); END;
CREATE TRIGGER retain_noon_close_item_replace BEFORE INSERT ON market_noon_close_item
WHEN EXISTS(SELECT 1 FROM market_noon_close_item WHERE review_id=NEW.review_id AND instrument_id=NEW.instrument_id)
BEGIN SELECT RAISE(ABORT,'retain immutable noon close item'); END;
CREATE TRIGGER guard_noon_close_item_delete BEFORE DELETE ON market_noon_close_item
WHEN noon_purge_allowed(OLD.snapshot_id)!=1
BEGIN SELECT RAISE(ABORT,'noon close item requires parent retention authorization'); END;

CREATE TABLE market_noon_close_attempt (
 snapshot_id TEXT PRIMARY KEY REFERENCES market_noon_snapshot(snapshot_id) ON DELETE CASCADE,
 checked_sequence INTEGER NOT NULL CHECK(checked_sequence>0),
 checked_at_ms INTEGER NOT NULL CHECK(checked_at_ms>0),
 source_job_id TEXT NOT NULL REFERENCES job(job_id),
 status TEXT NOT NULL CHECK(status IN('available','partial','waiting_close','warning')),
 reason TEXT
) STRICT;
CREATE TRIGGER guard_noon_close_attempt_delete BEFORE DELETE ON market_noon_close_attempt
WHEN noon_purge_allowed(OLD.snapshot_id)!=1
BEGIN SELECT RAISE(ABORT,'noon close attempt requires parent retention authorization'); END;
