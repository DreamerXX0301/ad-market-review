-- Preserve old strategy versions: absent rules retain the original simulator.
ALTER TABLE strategy_version ADD COLUMN trade_rules_json TEXT NOT NULL DEFAULT '{"entry":null,"exit":null}' CHECK(json_valid(trade_rules_json));
