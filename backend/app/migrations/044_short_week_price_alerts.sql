-- Existing rules keep their original structure model and frozen evidence.
ALTER TABLE automatic_price_alert_rule ADD COLUMN profile TEXT NOT NULL DEFAULT 'structure'
 CHECK(profile IN ('structure','short_week_v1'));
ALTER TABLE automatic_price_alert_rule ADD COLUMN max_risk_pct REAL NOT NULL DEFAULT 5.0
 CHECK(max_risk_pct>=0.5 AND max_risk_pct<=20.0 AND abs(max_risk_pct*100-round(max_risk_pct*100))<0.0000001);
