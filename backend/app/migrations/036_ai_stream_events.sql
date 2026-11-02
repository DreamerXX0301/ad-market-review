-- Per-request accounting and bounded resumable output for agent rounds.
CREATE TABLE ai_task_event (
 task_id TEXT NOT NULL REFERENCES ai_task(task_id) ON DELETE CASCADE,
 seq INTEGER NOT NULL CHECK(seq>0), kind TEXT NOT NULL CHECK(kind IN ('status','tool','delta','done','error')),
 data_json TEXT NOT NULL, at_ms INTEGER NOT NULL,
 PRIMARY KEY(task_id,seq)
);
CREATE INDEX ai_task_event_cleanup ON ai_task_event(at_ms,task_id);
CREATE TABLE ai_request_receipt (
 request_id TEXT PRIMARY KEY,
 task_id TEXT NOT NULL REFERENCES ai_task(task_id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 3),
 day TEXT NOT NULL, provider_id TEXT NOT NULL, model TEXT NOT NULL,
 state TEXT NOT NULL, reservation_nusd INTEGER NOT NULL DEFAULT 0,
 cost_nusd INTEGER, cost_state TEXT NOT NULL DEFAULT 'not_sent',
 usage_json TEXT NOT NULL DEFAULT '{}', price_json TEXT NOT NULL,
 created_at_ms INTEGER NOT NULL, sent_at_ms INTEGER, finished_at_ms INTEGER, reason_code TEXT,
 UNIQUE(task_id,ordinal)
);
CREATE INDEX ai_request_receipt_budget ON ai_request_receipt(day,sent_at_ms);
CREATE INDEX ai_request_receipt_task ON ai_request_receipt(task_id,ordinal);
