import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(new URL("./taskActivityPresentation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { activityPresentation: view, activityPollDelay: delay, visibleActivity, activityNoticeExpiry, nextActivityNoticeDelay, activityClockNow } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
assert.equal(view({ status: "queued", progress: 0 }).progressLabel, "0%");
assert.equal(view({ status: "running", progress: 0 }).progress, 0);
assert.equal(view({ status: "running" }).progress, null);
for (const progress of [null, undefined, "20", NaN, Infinity, -1, 101]) assert.equal(view({ status: "running", progress, duration_ms: 999999 }).progress, null);
assert.equal(view({ status: "running", progress: 33, title: "板块策略筛选", strategy_name: "周线金叉" }).title, "板块策略筛选 · 周线金叉");
assert.equal(view({ status: "running", stage: "核验个股条件", actual_completed_batches: 0, actual_total_batches: 5574 }).countLabel, "0 / 5574");
assert.equal(view({ status: "running", stage: "evaluating" }).stage, "核验策略条件");
for (const status of ["succeeded", "no_change", "partial"]) assert.equal(view({ status }).progress, 100);
assert.equal(view({ status: "failed", progress: 33 }).progress, 33);
assert.equal(view({ status: "failed" }).tone, "danger");
assert.equal(view({ status: "partial" }).tone, "warn");
assert.equal(delay(1, false), 2000);
assert.equal(delay(0, false), 30000);
assert.equal(delay(1, true), null);
assert.equal(delay(1, false, true), 15000);
const now = 1000000;
const tasks = [{ job_id: "active", status: "running", created_at_ms: 1 }, { job_id: "failed", status: "failed", finished_at_ms: now }, { job_id: "done", status: "succeeded", finished_at_ms: now - 1 }, { job_id: "old", status: "failed", finished_at_ms: 1 }];
assert.deepEqual(visibleActivity(tasks, new Set(), now).map(task => task.job_id), ["active", "failed"]);
assert.deepEqual(visibleActivity(tasks, new Set(["failed"]), now).map(task => task.job_id), ["active", "done"]);
assert.deepEqual(visibleActivity(tasks, new Set(["active", "failed", "done"]), now).map(task => task.job_id), ["active"]);
for (const status of ["succeeded", "no_change", "partial"]) {
  const task = { job_id: status, status, finished_at_ms: now };
  assert.equal(activityNoticeExpiry(task), now + 6000);
  assert.equal(visibleActivity([task], new Set(), now + 5999).length, 1);
  assert.equal(visibleActivity([task], new Set(), now + 6000).length, 0);
}
for (const status of ["failed", "cancelled"]) assert.equal(activityNoticeExpiry({ status, finished_at_ms: now }), now + 10000);
for (const status of ["queued", "running", "submitting"]) {
  const task = { job_id: status, status, created_at_ms: 1, finished_at_ms: 2 };
  assert.equal(activityNoticeExpiry(task), null);
  assert.equal(visibleActivity([task], new Set([status]), now + 999999).length, 1, "active work cannot expire or be dismissed");
}
assert.equal(nextActivityNoticeDelay(tasks, new Set(), now), 5999);
assert.equal(nextActivityNoticeDelay(tasks, new Set(), now + 6000), 4000);
assert.equal(nextActivityNoticeDelay(tasks, new Set(), now + 10000), null);
assert.deepEqual(visibleActivity(tasks.filter(task => task.status !== "running"), new Set(), now + 30000), [], "idle polling must not resurrect old terminals");
assert.equal(visibleActivity([{ status: "succeeded" }], new Set(), now).length, 0, "missing finish timestamps cannot create fresh notices");
const clock = { observedAtMs: now, receivedAtMs: 1_000_000_000, receivedMonotonicMs: 500 };
assert.equal(activityClockNow(clock, 1_000_002_000, 2500), now + 2000, "absolute client/server clock offsets cancel");
assert.equal(activityClockNow(clock, 999_000_000, 3500), now + 3000, "local clock rollback cannot revive expired notices");
assert.equal(activityClockNow(clock, 1_000_030_000, 600), now + 30000, "wall-clock elapsed handles a suspended monotonic clock");
console.log("Task activity: queue/zero/unknown/real counts, completion/failure, bounded notices and visibility-aware polling passed");
