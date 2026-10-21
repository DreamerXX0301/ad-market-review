-- Evaluate replayed tracking events against the immutable policy effective on that business date.
-- Service persists/validates evidence.policy_version; trigger events without explicit evidence are rejected.
DROP TRIGGER tracking_event_date_guard;
CREATE TRIGGER tracking_event_date_guard BEFORE INSERT ON tracking_event BEGIN
 SELECT CASE WHEN NEW.event_type IN ('alert','condition_end','expire') AND (json_type(NEW.evidence_json,'$.policy_version') IS NOT 'integer' OR NOT EXISTS(SELECT 1 FROM tracking_round r JOIN tracking_policy_version p ON p.strategy_id=r.strategy_id AND p.policy_version=json_extract(NEW.evidence_json,'$.policy_version') WHERE r.round_id=NEW.round_id)) THEN RAISE(ABORT,'trigger event requires explicit valid historical policy evidence') END;
 SELECT CASE WHEN NEW.business_date<(SELECT entry_business_date FROM tracking_round WHERE round_id=NEW.round_id) THEN RAISE(ABORT,'event predates tracking round') END;
 SELECT CASE WHEN NEW.trading_age=0 AND NEW.event_type='expire' THEN RAISE(ABORT,'day zero cannot expire') END;
 SELECT CASE WHEN NEW.event_type IN ('alert','condition_end') AND (NEW.trading_age=0 OR NEW.business_date=(SELECT entry_business_date FROM tracking_round WHERE round_id=NEW.round_id)) AND NOT EXISTS(
  SELECT 1 FROM tracking_round r JOIN tracking_policy_version p ON p.strategy_id=r.strategy_id AND p.policy_version=json_extract(NEW.evidence_json,'$.policy_version') WHERE r.round_id=NEW.round_id
  AND ((NEW.event_type='alert' AND p.allow_entry_day_alerts=1) OR (NEW.event_type='condition_end' AND p.allow_entry_day_exits=1))) THEN RAISE(ABORT,'day-zero signal needs explicit policy opt-in') END;
 SELECT CASE WHEN NEW.event_type IN ('alert','condition_end') AND (NEW.data_state<>'ready' OR NEW.outcome<>'satisfied') THEN RAISE(ABORT,'unknown data cannot trigger an alert or condition exit') END;
END;

DROP TRIGGER tracking_terminal_event_gate;
CREATE TRIGGER tracking_terminal_event_gate BEFORE INSERT ON tracking_event WHEN NEW.event_type IN ('alert','condition_end','expire') BEGIN
 SELECT CASE WHEN (SELECT status FROM tracking_round WHERE round_id=NEW.round_id)<>'tracking' THEN RAISE(ABORT,'ended round does not emit new live triggers') END;
 SELECT CASE WHEN NEW.event_type='expire' AND NOT EXISTS(SELECT 1 FROM tracking_round r JOIN tracking_policy_version p ON p.strategy_id=r.strategy_id AND p.policy_version=json_extract(NEW.evidence_json,'$.policy_version') WHERE r.round_id=NEW.round_id AND NEW.trading_age>=p.duration_sessions) THEN RAISE(ABORT,'expiry requires policy trading-session age') END;
END;
