-- Annual manual observations have no strategy/watchlist side effects.
CREATE TABLE zodiac_pool (
 observation_year INTEGER PRIMARY KEY CHECK(observation_year BETWEEN 1900 AND 2199),
 target_year INTEGER NOT NULL CHECK(target_year=observation_year+1),
 revision INTEGER NOT NULL DEFAULT 1,
 created_at_ms INTEGER NOT NULL
);
CREATE TABLE zodiac_pool_member (
 member_id TEXT PRIMARY KEY,
 observation_year INTEGER NOT NULL REFERENCES zodiac_pool(observation_year),
 instrument_id TEXT NOT NULL,
 name TEXT NOT NULL,
 round_number INTEGER NOT NULL CHECK(round_number>0),
 status TEXT NOT NULL CHECK(status IN ('tracking','ended')),
 relation_type TEXT NOT NULL CHECK(relation_type IN ('name','homophone','branch','culture','business','custom')),
 custom_label TEXT NOT NULL DEFAULT '' CHECK(length(custom_label)<=30),
 reason TEXT NOT NULL DEFAULT '' CHECK(length(reason)<=1000),
 links_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(links_json)),
 focused INTEGER NOT NULL DEFAULT 0 CHECK(focused IN (0,1)),
 added_business_date TEXT NOT NULL,
 entry_price_e8 INTEGER CHECK(entry_price_e8>0),
 entry_basis_json TEXT NOT NULL CHECK(json_valid(entry_basis_json)),
 end_business_date TEXT,
 end_reason TEXT,
 revision INTEGER NOT NULL DEFAULT 1,
 created_at_ms INTEGER NOT NULL,
 updated_at_ms INTEGER NOT NULL,
 UNIQUE(observation_year,instrument_id,round_number),
 CHECK((status='tracking' AND end_business_date IS NULL) OR
       (status='ended' AND end_business_date>=added_business_date))
);
CREATE UNIQUE INDEX zodiac_active_member ON zodiac_pool_member(observation_year,instrument_id) WHERE status='tracking';
CREATE INDEX zodiac_members_by_pool ON zodiac_pool_member(observation_year,status,created_at_ms);
CREATE TABLE zodiac_pool_audit (
 audit_id TEXT PRIMARY KEY,
 member_id TEXT NOT NULL REFERENCES zodiac_pool_member(member_id),
 action TEXT NOT NULL,
 occurred_at_ms INTEGER NOT NULL,
 before_json TEXT NOT NULL CHECK(json_valid(before_json)),
 after_json TEXT NOT NULL CHECK(json_valid(after_json))
);
CREATE INDEX zodiac_audit_member ON zodiac_pool_audit(member_id,occurred_at_ms);
CREATE TABLE zodiac_pool_request (
 request_key TEXT PRIMARY KEY,
 arguments_sha256 TEXT NOT NULL,
 response_json TEXT NOT NULL CHECK(json_valid(response_json)),
 created_at_ms INTEGER NOT NULL
);
CREATE TRIGGER zodiac_member_identity_immutable BEFORE UPDATE ON zodiac_pool_member
WHEN NEW.member_id IS NOT OLD.member_id OR NEW.observation_year IS NOT OLD.observation_year
 OR NEW.instrument_id IS NOT OLD.instrument_id OR NEW.round_number IS NOT OLD.round_number
 OR NEW.added_business_date IS NOT OLD.added_business_date OR NEW.created_at_ms IS NOT OLD.created_at_ms
 OR (OLD.entry_price_e8 IS NOT NULL AND (NEW.entry_price_e8 IS NOT OLD.entry_price_e8 OR NEW.entry_basis_json IS NOT OLD.entry_basis_json))
 OR (OLD.status='ended' AND (NEW.status IS NOT OLD.status OR NEW.end_business_date IS NOT OLD.end_business_date OR NEW.end_reason IS NOT OLD.end_reason))
BEGIN SELECT RAISE(ABORT,'生肖成员身份、已确认基准和结束事实不可改写'); END;
CREATE TRIGGER zodiac_member_no_delete BEFORE DELETE ON zodiac_pool_member
BEGIN SELECT RAISE(ABORT,'生肖成员仅可结束观察，不能删除历史'); END;
CREATE TRIGGER zodiac_audit_no_update BEFORE UPDATE ON zodiac_pool_audit
BEGIN SELECT RAISE(ABORT,'生肖审计不可改写'); END;
CREATE TRIGGER zodiac_audit_no_delete BEFORE DELETE ON zodiac_pool_audit
BEGIN SELECT RAISE(ABORT,'生肖审计不可删除'); END;
CREATE TRIGGER zodiac_request_no_update BEFORE UPDATE ON zodiac_pool_request
BEGIN SELECT RAISE(ABORT,'生肖幂等回执不可改写'); END;
CREATE TRIGGER zodiac_request_no_delete BEFORE DELETE ON zodiac_pool_request
BEGIN SELECT RAISE(ABORT,'生肖幂等回执不可删除'); END;
