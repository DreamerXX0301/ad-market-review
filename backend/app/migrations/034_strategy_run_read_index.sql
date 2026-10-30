-- 策略管理与最近运行恢复按策略定位，避免反复扫描全部历史运行。
-- 不改写运行、候选或策略版本；rowid 继续作为同毫秒记录的稳定次序。
CREATE INDEX selection_run_strategy_created ON selection_run(strategy_id,created_at_ms);
