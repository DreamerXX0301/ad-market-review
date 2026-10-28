-- 午后观察只追加修订，不改写午评行情或昨日复盘；随授权到期快照删除。
CREATE TABLE market_noon_note (
 note_id TEXT PRIMARY KEY,
 snapshot_id TEXT NOT NULL REFERENCES market_noon_snapshot(snapshot_id) ON DELETE CASCADE,
 revision INTEGER NOT NULL CHECK(revision>0),
 body TEXT NOT NULL CHECK(length(body)<=4000),
 recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms>0),
 UNIQUE(snapshot_id,revision)
) STRICT;
CREATE TRIGGER append_noon_note BEFORE INSERT ON market_noon_note
BEGIN
 SELECT CASE WHEN NEW.revision!=coalesce((SELECT max(revision) FROM market_noon_note WHERE snapshot_id=NEW.snapshot_id),0)+1
 THEN RAISE(ABORT,'noon note revision must append') END;
 SELECT CASE WHEN NEW.recorded_at_ms<coalesce((SELECT max(recorded_at_ms) FROM market_noon_note WHERE snapshot_id=NEW.snapshot_id),0)
 THEN RAISE(ABORT,'noon note clock moved backwards') END;
END;
CREATE TRIGGER retain_noon_note_update BEFORE UPDATE ON market_noon_note
BEGIN SELECT RAISE(ABORT,'retain noon note audit'); END;
CREATE TRIGGER guard_noon_note_delete BEFORE DELETE ON market_noon_note
WHEN noon_purge_allowed(OLD.snapshot_id)!=1
BEGIN SELECT RAISE(ABORT,'noon notes require worker retention authorization'); END;
