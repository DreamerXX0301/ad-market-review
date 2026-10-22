-- Visibility state is separate from immutable strategy/backtest evidence.
CREATE TABLE strategy_deletion (
  strategy_id TEXT PRIMARY KEY REFERENCES strategy(strategy_id),
  deletion_id TEXT NOT NULL UNIQUE,
  deleted_at_ms INTEGER NOT NULL CHECK(deleted_at_ms>0),
  previous_archived INTEGER NOT NULL CHECK(previous_archived IN (0,1))
) STRICT;

CREATE TABLE backtest_deletion (
  backtest_id TEXT PRIMARY KEY REFERENCES backtest_run(backtest_id),
  deleted_at_ms INTEGER NOT NULL CHECK(deleted_at_ms>0),
  strategy_deletion_id TEXT REFERENCES strategy_deletion(deletion_id)
) STRICT;

CREATE INDEX backtest_deletion_strategy_batch ON backtest_deletion(strategy_deletion_id);
