-- Local AI configuration and conversations. No provider keys or market writes.
CREATE TABLE ai_provider_config (
 provider_id TEXT PRIMARY KEY CHECK(provider_id IN ('deepseek','openai')),
 model TEXT NOT NULL, credential_ref TEXT, credential_revision INTEGER NOT NULL DEFAULT 0,
 config_revision INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 verification_json TEXT NOT NULL DEFAULT '{}', models_json TEXT NOT NULL DEFAULT '[]',
 updated_at_ms INTEGER NOT NULL DEFAULT 0
);
INSERT INTO ai_provider_config(provider_id,model) VALUES('deepseek','deepseek-flash'),('openai','gpt-6.1-sol');
CREATE TABLE ai_instance_limits (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), daily_budget_nusd INTEGER NOT NULL DEFAULT 500000000,
 daily_request_limit INTEGER NOT NULL DEFAULT 20, timeout_seconds INTEGER NOT NULL DEFAULT 45
);
INSERT INTO ai_instance_limits(singleton) VALUES(1);
CREATE TABLE ai_session (
 session_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('stock','market')),
 report_id TEXT, instrument_id TEXT, as_of TEXT NOT NULL,
 provider_id TEXT NOT NULL REFERENCES ai_provider_config(provider_id), model TEXT NOT NULL,
 config_signature TEXT NOT NULL, context_digest TEXT NOT NULL, context_json TEXT NOT NULL,
 protocol_json TEXT NOT NULL,
 keep INTEGER NOT NULL DEFAULT 0 CHECK(keep IN (0,1)),
 created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL
);
CREATE INDEX ai_session_scope ON ai_session(kind,updated_at_ms DESC,session_id DESC);
CREATE INDEX ai_session_expiry ON ai_session(keep,expires_at_ms);
CREATE TABLE ai_task (
 task_id TEXT PRIMARY KEY, operation TEXT NOT NULL CHECK(operation IN ('verify','message')),
 session_id TEXT REFERENCES ai_session(session_id) ON DELETE SET NULL,
 provider_id TEXT NOT NULL REFERENCES ai_provider_config(provider_id), model TEXT NOT NULL,
 config_signature TEXT NOT NULL, credential_ref TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE,
 request_digest TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', reason_code TEXT, result_json TEXT,
 created_at_ms INTEGER NOT NULL, started_at_ms INTEGER, sent_at_ms INTEGER, finished_at_ms INTEGER,
 owner TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
 request_json TEXT NOT NULL, day TEXT, reservation_nusd INTEGER NOT NULL DEFAULT 0,
 cost_nusd INTEGER, cost_state TEXT NOT NULL DEFAULT 'not_sent', usage_json TEXT NOT NULL DEFAULT '{}',
 price_version TEXT, response_model TEXT
);
CREATE INDEX ai_task_queue ON ai_task(state,created_at_ms);
CREATE INDEX ai_task_budget ON ai_task(day,sent_at_ms);
CREATE UNIQUE INDEX ai_only_one_running ON ai_task((1)) WHERE state='running';
CREATE TABLE ai_message (
 message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES ai_session(session_id) ON DELETE CASCADE,
 task_id TEXT REFERENCES ai_task(task_id) ON DELETE SET NULL, role TEXT NOT NULL CHECK(role IN ('user','assistant')),
 text TEXT NOT NULL, interpretation_json TEXT, state TEXT NOT NULL, created_at_ms INTEGER NOT NULL
);
CREATE INDEX ai_message_history ON ai_message(session_id,created_at_ms,message_id);
