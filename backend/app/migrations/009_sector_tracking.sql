-- Sector index rounds never reference or populate the stock tracking tables.
CREATE TABLE sector_tracking_round (
 round_id TEXT PRIMARY KEY,
 strategy_id TEXT NOT NULL,
 entry_strategy_version INTEGER NOT NULL,
 origin_run_id TEXT NOT NULL,
 target_id TEXT NOT NULL,
 topic_code TEXT NOT NULL CHECK(topic_code=target_id),
 topic_id TEXT NOT NULL,
 name TEXT NOT NULL,
 sector_kind TEXT NOT NULL CHECK(sector_kind IN ('industry','concept')),
 entry_period TEXT NOT NULL CHECK(entry_period IN ('D','W','M')),
 entry_business_date TEXT NOT NULL CHECK(date(entry_business_date,'+0 days') IS entry_business_date),
 entry_price_e8 INTEGER NOT NULL CHECK(entry_price_e8>0),
 entry_high_e8 INTEGER CHECK(entry_high_e8>0),
 entry_low_e8 INTEGER CHECK(entry_low_e8>0),
 entry_market_snapshot_id TEXT NOT NULL,
 entry_cache_refs_json TEXT NOT NULL CHECK(json_valid(entry_cache_refs_json)),
 entry_basis_json TEXT NOT NULL CHECK(json_valid(entry_basis_json)),
 entry_policy_json TEXT NOT NULL CHECK(json_valid(entry_policy_json)),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 policy_revision INTEGER NOT NULL DEFAULT 1 CHECK(policy_revision>0),
 duration_sessions INTEGER NOT NULL CHECK(duration_sessions BETWEEN 1 AND 750),
 status TEXT NOT NULL DEFAULT 'tracking' CHECK(status IN ('tracking','expired','condition_ended','manual_removed')),
 end_business_date TEXT,
 end_reason TEXT,
 created_at_ms INTEGER NOT NULL,
 updated_at_ms INTEGER NOT NULL,
 FOREIGN KEY(strategy_id,entry_strategy_version) REFERENCES sector_strategy_version(strategy_id,version),
 FOREIGN KEY(origin_run_id,topic_code) REFERENCES sector_selection_candidate(run_id,topic_code),
 CHECK(entry_high_e8 IS NULL OR entry_high_e8>=entry_price_e8),
 CHECK(entry_low_e8 IS NULL OR entry_low_e8<=entry_price_e8),
 CHECK((status='tracking' AND end_business_date IS NULL) OR
       (status<>'tracking' AND end_business_date>=entry_business_date))
);
CREATE UNIQUE INDEX one_active_sector_tracking ON sector_tracking_round(strategy_id,target_id) WHERE status='tracking';
CREATE INDEX sector_tracking_status ON sector_tracking_round(status,strategy_id,created_at_ms);

CREATE TABLE sector_tracking_event (
 event_id TEXT PRIMARY KEY,
 event_key TEXT NOT NULL UNIQUE,
 round_id TEXT NOT NULL REFERENCES sector_tracking_round(round_id),
 event_type TEXT NOT NULL CHECK(event_type IN ('entry','review','alert','condition_end','expire','manual_remove','policy_change')),
 business_date TEXT NOT NULL CHECK(date(business_date,'+0 days') IS business_date),
 policy_revision INTEGER NOT NULL CHECK(policy_revision>0),
 data_state TEXT NOT NULL CHECK(data_state IN ('ready','unknown','not_applicable')),
 outcome TEXT NOT NULL CHECK(outcome IN ('satisfied','not_satisfied','unknown','not_applicable')),
 trading_age INTEGER CHECK(trading_age>=0),
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 recorded_at_ms INTEGER NOT NULL
);
CREATE INDEX sector_tracking_event_list ON sector_tracking_event(business_date DESC,event_type,round_id);

CREATE TRIGGER sector_tracking_entry_gate BEFORE INSERT ON sector_tracking_round BEGIN
 SELECT CASE WHEN NOT EXISTS(
  SELECT 1 FROM sector_selection_run r JOIN sector_selection_candidate c ON c.run_id=r.run_id
   JOIN sector_strategy s ON s.strategy_id=r.strategy_id
   JOIN sector_strategy_version v ON v.strategy_id=r.strategy_id AND v.version=r.strategy_version
  WHERE r.run_id=NEW.origin_run_id AND r.status='completed' AND r.strategy_id=NEW.strategy_id
   AND r.strategy_version=NEW.entry_strategy_version AND r.business_date=NEW.entry_business_date
   AND r.business_date=(SELECT business_date FROM business_clock WHERE singleton=1)
   AND r.market_snapshot_id=NEW.entry_market_snapshot_id AND s.current_version=r.strategy_version
   AND s.archived=0 AND c.topic_code=NEW.topic_code AND c.decision='matched'
   AND c.close_price_e8=NEW.entry_price_e8 AND c.name=NEW.name AND c.sector_kind=NEW.sector_kind
   AND c.cache_refs_json=NEW.entry_cache_refs_json AND v.period=NEW.entry_period
 ) THEN RAISE(ABORT,'sector entry requires a current matched run and frozen daily close') END;
END;
CREATE TRIGGER sector_tracking_entry_immutable BEFORE UPDATE ON sector_tracking_round
 WHEN NEW.strategy_id<>OLD.strategy_id OR NEW.entry_strategy_version<>OLD.entry_strategy_version
  OR NEW.origin_run_id<>OLD.origin_run_id OR NEW.target_id<>OLD.target_id OR NEW.topic_code<>OLD.topic_code
  OR NEW.topic_id<>OLD.topic_id OR NEW.name<>OLD.name OR NEW.sector_kind<>OLD.sector_kind
  OR NEW.entry_period<>OLD.entry_period OR NEW.entry_business_date<>OLD.entry_business_date
  OR NEW.entry_price_e8<>OLD.entry_price_e8 OR NEW.entry_high_e8 IS NOT OLD.entry_high_e8
  OR NEW.entry_low_e8 IS NOT OLD.entry_low_e8 OR NEW.entry_market_snapshot_id<>OLD.entry_market_snapshot_id
  OR NEW.entry_cache_refs_json<>OLD.entry_cache_refs_json OR NEW.entry_basis_json<>OLD.entry_basis_json
  OR NEW.entry_policy_json<>OLD.entry_policy_json
 BEGIN SELECT RAISE(ABORT,'sector tracking entry is immutable'); END;
CREATE TRIGGER sector_tracking_no_reactivation BEFORE UPDATE OF status ON sector_tracking_round
 WHEN OLD.status<>'tracking' AND NEW.status<>OLD.status
 BEGIN SELECT RAISE(ABORT,'ended sector rounds cannot be reactivated'); END;
CREATE TRIGGER sector_tracking_event_immutable_update BEFORE UPDATE ON sector_tracking_event
 BEGIN SELECT RAISE(ABORT,'sector tracking events are immutable'); END;
CREATE TRIGGER sector_tracking_event_immutable_delete BEFORE DELETE ON sector_tracking_event
 BEGIN SELECT RAISE(ABORT,'sector tracking events are immutable'); END;
CREATE TRIGGER sector_tracking_event_date_gate BEFORE INSERT ON sector_tracking_event
 WHEN NEW.business_date<(SELECT entry_business_date FROM sector_tracking_round WHERE round_id=NEW.round_id)
 BEGIN SELECT RAISE(ABORT,'sector tracking event predates entry'); END;
