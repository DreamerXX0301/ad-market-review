-- Permanent cleanup is explicitly confirmed, queued, and scoped to one worker connection.
-- Published snapshots/artifact metadata and task history remain immutable audit records.
CREATE TABLE workspace_purge (
  purge_id TEXT PRIMARY KEY,
  target_kind TEXT NOT NULL CHECK(target_kind IN ('strategy','backtest')),
  target_id TEXT NOT NULL,
  target_name TEXT NOT NULL,
  job_id TEXT NOT NULL UNIQUE REFERENCES job(job_id),
  scope_sha256 TEXT NOT NULL CHECK(length(scope_sha256)=64),
  scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
  status TEXT NOT NULL CHECK(status IN ('queued','cleaning','completed','failed')),
  business_deleted INTEGER NOT NULL DEFAULT 0 CHECK(business_deleted IN (0,1)),
  created_at_ms INTEGER NOT NULL,
  completed_at_ms INTEGER,
  error TEXT,
  superseded_by TEXT REFERENCES workspace_purge(purge_id)
) STRICT;
CREATE UNIQUE INDEX one_active_workspace_purge ON workspace_purge(target_kind,target_id) WHERE status IN ('queued','cleaning');
CREATE INDEX workspace_purge_target ON workspace_purge(target_kind,target_id,business_deleted);
CREATE TABLE workspace_purge_file (
  purge_id TEXT NOT NULL REFERENCES workspace_purge(purge_id),
  snapshot_id TEXT NOT NULL REFERENCES dataset_snapshot(snapshot_id),
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK(length(sha256)=64),
  byte_count INTEGER NOT NULL CHECK(byte_count>=0),
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','removed','absent','failed')),
  removed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(removed_bytes>=0),
  error TEXT,
  PRIMARY KEY(purge_id,object_key)
) STRICT;
CREATE TABLE workspace_purge_audit (
  purge_id TEXT NOT NULL REFERENCES workspace_purge(purge_id),
  stage TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL,
  details_json TEXT NOT NULL CHECK(json_valid(details_json)),
  PRIMARY KEY(purge_id,stage)
) STRICT;
CREATE TRIGGER workspace_purge_identity BEFORE UPDATE ON workspace_purge
WHEN NEW.purge_id<>OLD.purge_id OR NEW.target_kind<>OLD.target_kind OR NEW.target_id<>OLD.target_id OR NEW.target_name<>OLD.target_name OR NEW.job_id<>OLD.job_id OR NEW.scope_sha256<>OLD.scope_sha256 OR NEW.scope_json<>OLD.scope_json OR NEW.created_at_ms<>OLD.created_at_ms OR NEW.business_deleted<OLD.business_deleted OR (OLD.superseded_by IS NOT NULL AND NEW.superseded_by IS NOT OLD.superseded_by)
BEGIN SELECT RAISE(ABORT,'purge authorization is immutable'); END;
CREATE TRIGGER workspace_purge_retained BEFORE DELETE ON workspace_purge BEGIN SELECT RAISE(ABORT,'retain permanent deletion audit'); END;
CREATE TRIGGER workspace_purge_file_identity BEFORE UPDATE ON workspace_purge_file
WHEN NEW.purge_id<>OLD.purge_id OR NEW.snapshot_id<>OLD.snapshot_id OR NEW.object_key<>OLD.object_key OR NEW.sha256<>OLD.sha256 OR NEW.byte_count<>OLD.byte_count OR NEW.removed_bytes<OLD.removed_bytes
BEGIN SELECT RAISE(ABORT,'purge file authorization is immutable'); END;
CREATE TRIGGER workspace_purge_file_retained BEFORE DELETE ON workspace_purge_file BEGIN SELECT RAISE(ABORT,'retain permanent deletion file audit'); END;
CREATE TRIGGER workspace_purge_audit_update BEFORE UPDATE ON workspace_purge_audit BEGIN SELECT RAISE(ABORT,'retain permanent deletion audit'); END;
CREATE TRIGGER workspace_purge_audit_delete BEFORE DELETE ON workspace_purge_audit BEGIN SELECT RAISE(ABORT,'retain permanent deletion audit'); END;

-- All UPDATE guards stay unchanged. Only these eight DELETE guards accept an
-- exact entity allow-list installed on the worker's locked transaction connection.
DROP TRIGGER append_only_strategy_version_delete;
CREATE TRIGGER append_only_strategy_version_delete BEFORE DELETE ON strategy_version WHEN workspace_purge_allowed('strategy',OLD.strategy_id)=0 BEGIN SELECT RAISE(ABORT,'append-only evidence; create a new version'); END;
DROP TRIGGER append_only_tracking_policy_version_delete;
CREATE TRIGGER append_only_tracking_policy_version_delete BEFORE DELETE ON tracking_policy_version WHEN workspace_purge_allowed('strategy',OLD.strategy_id)=0 BEGIN SELECT RAISE(ABORT,'append-only evidence; create a new version'); END;
DROP TRIGGER append_only_tracking_policy_application_delete;
CREATE TRIGGER append_only_tracking_policy_application_delete BEFORE DELETE ON tracking_policy_application WHEN workspace_purge_allowed('round',OLD.round_id)=0 BEGIN SELECT RAISE(ABORT,'append-only evidence; create a new version'); END;
DROP TRIGGER append_only_tracking_event_delete;
CREATE TRIGGER append_only_tracking_event_delete BEFORE DELETE ON tracking_event WHEN workspace_purge_allowed('round',OLD.round_id)=0 BEGIN SELECT RAISE(ABORT,'append-only evidence; create a new version'); END;
DROP TRIGGER freeze_selection_run_delete;
CREATE TRIGGER freeze_selection_run_delete BEFORE DELETE ON selection_run WHEN OLD.status='completed' AND workspace_purge_allowed('selection',OLD.run_id)=0 BEGIN SELECT RAISE(ABORT,'completed run and candidates are immutable'); END;
DROP TRIGGER freeze_selection_candidate_delete;
CREATE TRIGGER freeze_selection_candidate_delete BEFORE DELETE ON selection_candidate WHEN (SELECT status FROM selection_run WHERE run_id=OLD.run_id)='completed' AND workspace_purge_allowed('selection',OLD.run_id)=0 BEGIN SELECT RAISE(ABORT,'completed run and candidates are immutable'); END;
DROP TRIGGER tracking_round_no_delete;
CREATE TRIGGER tracking_round_no_delete BEFORE DELETE ON tracking_round WHEN workspace_purge_allowed('round',OLD.round_id)=0 BEGIN SELECT RAISE(ABORT,'soft-end tracking round; retain history'); END;
DROP TRIGGER completed_backtest_no_delete;
CREATE TRIGGER completed_backtest_no_delete BEFORE DELETE ON backtest_run WHEN OLD.status='completed' AND workspace_purge_allowed('backtest',OLD.backtest_id)=0 BEGIN SELECT RAISE(ABORT,'retain completed backtest metadata'); END;
