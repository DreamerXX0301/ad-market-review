-- Existing events stay immutable except for the exact-ID cleanup capability
-- installed on the sole worker's transaction-local connection after an
-- authorized post-close increment and a verified 60-session deadline.
CREATE INDEX line_alert_first_trigger ON line_alert_event(annotation_id,trade_date);
DROP TRIGGER retain_line_alert_delete;
CREATE TRIGGER retain_line_alert_delete BEFORE DELETE ON line_alert_event
WHEN line_alert_expiry_allowed(OLD.annotation_id,OLD.event_id)=0
BEGIN SELECT RAISE(ABORT,'retain line alert evidence'); END;
