import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(new URL("./incrementProgress.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { incrementProgressView: view, incrementReceiptForSession: receipt } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const prior = { request_key: "previous", root_task_id: "old-job", can_acknowledge: true, status: "succeeded" };
assert.equal(receipt(prior, { requestKey: "new" }), null);
assert.equal(receipt(prior, { requestKey: "previous", taskId: "new-job" }), null);
assert.equal(view(receipt(prior, { requestKey: "new" })).done, false, "changing sessions cannot briefly reuse an old completed receipt");
assert.equal(receipt(prior, { requestKey: "previous", taskId: "old-job" }), prior);
assert.equal(view(null).done, false);
assert.equal(view({ tasks: [{ status: "succeeded", progress: 100 }, { status: "running", progress: 0 }] }).progress, 0);
assert.equal(view({ can_acknowledge: false, status: "awaiting_followup", tasks: [{ status: "succeeded", progress: 100 }] }).progress, null);
assert.equal(view({ can_acknowledge: false, status: "awaiting_followup" }).done, false);
for (const progress of [null, undefined, "80", NaN, Infinity, -1, 101]) assert.equal(view({ tasks: [{ status: "running", progress }] }).progress, null);
for (const status of ["failed", "paused", "cancelled", "partial", "blocked", "succeeded", "no_change"]) assert.equal(view({ can_acknowledge: true, status }).done, true);
assert.equal(view({ estimate: { remaining_max_ms: null } }).estimate, null);
assert.equal(view({ estimate: { remaining_max_ms: 0 } }).estimate, null);
assert.equal(view({ estimate: { remaining_max_ms: 1000 } }).estimate.remaining_max_ms, 1000);
const retried = view({ attempt_task_id: "new-step", can_acknowledge: true, status: "succeeded", tasks: [
  { job_id: "old-step", status: "failed", historical_attempt: true },
  { job_id: "new-step", status: "succeeded", progress: 100 },
] });
assert.equal(retried.title, "本次重试步骤已完成");
assert.equal(retried.completedLabel, "已完成 1 / 1 项本次已排队任务");
const ui = await fs.readFile(new URL("./IncrementProgressDialog.tsx", import.meta.url), "utf8");
assert.match(ui, /root\.inert = true/);
assert.match(ui, /event\.key === "Escape"/);
assert.match(ui, /view\.done \|\| unconfirmed/);
assert.match(ui, /post<Row>\("\/sync\/session\/acknowledge"/, "unlock requires an actual server acknowledgement");
assert.match(ui, /post\("\/sync\/session\/control"/);
assert.doesNotMatch(ui, /post\([^\n]*(?:sync\/tasks|resume|retry)/, "restoring the dialog must never create or resume collection");
assert.doesNotMatch(ui, /elapsed\s*>=\s*30000/, "time or 404 cannot prove that a request did not arrive");
assert.match(ui, /value\.acknowledged === true && value\.locked === false/);
assert.match(ui, /MutationObserver/);
console.log("Increment dialog: full-chain acknowledgement, measured stage progress and unknown estimates passed");

const scopedReceipt = {...prior, session_epoch: "epoch-old"};
assert.equal(receipt(scopedReceipt, {requestKey: "previous", taskId: "old-job", epoch: "epoch-new"}), null, "a retry epoch cannot reuse a previous complete receipt");
assert.equal(receipt(scopedReceipt, {requestKey: "previous", taskId: "old-job", epoch: "epoch-old"}), scopedReceipt);
