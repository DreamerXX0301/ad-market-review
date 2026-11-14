ALTER TABLE selection_candidate ADD COLUMN packed_evidence BLOB
 CHECK(packed_evidence IS NULL OR (length(packed_evidence)>0
   AND checks_json='{}' AND metrics_json='{}' AND topic_snapshot_json='[]'));

-- The corresponding UNIQUE keys cover these columns, including reverse scans.
DROP INDEX ai_request_receipt_task;
DROP INDEX intraday_monitor_batch_day;
DROP INDEX auction_automation_run_latest;
DROP INDEX auction_snapshot_by_date;
DROP INDEX noon_snapshot_by_date;
