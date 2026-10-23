-- 当前热点范围独立于技术条件；旧版本保留 NULL，不改变已有策略。
ALTER TABLE strategy_version ADD COLUMN hot_universe_json TEXT
  CHECK(hot_universe_json IS NULL OR json_valid(hot_universe_json));
