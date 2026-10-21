import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./researchProgress.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  researchTaskProgress: progress,
  researchTaskStage,
  researchTaskError,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
for (const status of ["queued", "pending"]) {
  const task = progress({ status, stage: status });
  assert.equal(task.statusLabel, "排队等待");
  assert.equal(task.stage, "等待工作进程");
  assert.equal(task.progress, 0);
}
assert.equal(
  progress({ status: "queued", progress: 60 }).progress,
  60,
  "a paused then requeued task retains reported progress",
);
assert.equal(progress({ status: "submitting" }).progress, null);
const running = progress({
  status: "running",
  stage: "财报与估值",
  progress: 39,
  message: "查询现金流实际披露材料",
});
assert.equal(running.statusLabel, "研究进行中");
assert.equal(running.stage, "财报与估值");
assert.equal(running.progressLabel, "39%");
assert.equal(running.message, "查询现金流实际披露材料");
assert.equal(
  progress({ status: "running", phase: "starting", progress: 0 }).stage,
  "准备研究材料",
);
for (const invalid of [undefined, null, "60", true, NaN, Infinity, -1, 101]) {
  const task = progress({
    status: "running",
    stage: "unmapped_phase",
    progress: invalid,
    duration_ms: 99_999,
  });
  assert.equal(
    task.progress,
    null,
    "no fabricated percentage from invalid/missing progress or elapsed time",
  );
  assert.equal(task.stage, "查询与核对研究材料");
}
assert.equal(
  progress({ status: "running", progress: 60, duration_ms: 1 }).progress,
  progress({ status: "running", progress: 60, duration_ms: 9_999_999 })
    .progress,
);
for (const status of ["completed", "succeeded", "success", "partial"]) {
  const task = progress({ status });
  assert.equal(task.progress, 100);
  assert.equal(task.active, false);
}
assert.match(progress({ status: "partial" }).statusLabel, /材料有缺口/);
for (const status of ["failed", "cancelled", "canceled", "paused"]) {
  const task = progress({ status, progress: 60 });
  assert.equal(task.progress, 60, "interrupted work must not be shown as 100%");
  assert.equal(task.active, false);
  assert.equal(/[a-z]/i.test(task.statusLabel), false);
}
assert.equal(
  progress({ status: "failed", error_summary: { message: "来源请求超时" } })
    .message,
  "来源请求超时",
);
assert.equal(
  progress({ status: "failed", error: {} }).message,
  "研究任务未能完成，请查看执行记录后手动重试",
);
assert.equal(progress({ status: "new_status" }).statusLabel, "任务状态待确认");
assert.equal(researchTaskStage("recovery"), "恢复已授权研究任务");
assert.equal(researchTaskError({ detail: "证券未通过核验" }), "证券未通过核验");
const logs = progress({
  status: "running",
  logs: [
    { seq: 1, phase: "queued", occurred_at_ms: 123, message: "等待工作进程" },
    { seq: 2, phase: "财报与估值", time: 234, message: "查询利润表" },
  ],
}).logs;
assert.deepEqual(
  logs.map((row) => [row.key, row.time, row.stage]),
  [
    [1, 123, "等待工作进程"],
    [2, 234, "财报与估值"],
  ],
);
assert.deepEqual(progress({ status: "queued", logs: null }).logs, []);
console.log(
  "research progress: Chinese states, reported percentages, indeterminate running, terminal states and dated records passed",
);
