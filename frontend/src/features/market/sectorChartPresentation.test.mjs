import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./sectorChartPresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  sectorChartTarget: target,
  sectorChartRequest: request,
  sectorTaskMatches: matches,
  sectorTaskProgress: progress,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const date = "2026-10-20",
  code = "881101.TI";
const industry = { topic_code: code, name: "半导体", kind: "industry" };
const direct = target({
  ...industry,
  members_available: false,
  membership_match_type: "unavailable",
});
assert.equal(
  direct.topicCode,
  code,
  "missing historical members must not gate the index chart",
);
assert.equal(direct.proxy, false);
assert.equal(
  target({ topic_id: "fuyao-885431.TI", kind: "concept", name: "概念" })
    .topicCode,
  "885431.TI",
);
const flow = { topic_code: "BK0001", name: " 半导体 ", kind: "industry" };
const proxy = target(flow, [industry, { ...industry }]);
assert.equal(proxy.topicCode, code);
assert.equal(proxy.proxy, true);
assert.match(proxy.notice, /同名同花顺行业指数/);
assert.match(proxy.notice, /分别计算/);
assert.equal(
  target(flow, [industry, { ...industry, topic_code: "884001.TI" }]).topicCode,
  null,
  "ambiguous names must not guess an index",
);
assert.equal(
  target({ ...flow, kind: "concept" }, [industry]).topicCode,
  null,
  "industry proxy is not a concept map",
);
assert.equal(
  target(flow, [{ ...industry, name: "半导体及元件" }]).topicCode,
  null,
  "partial name match is not evidence",
);
assert.equal(target(flow, []).topicCode, null);
assert.equal(target({ topic_id: 123, name: null }).topicCode, null);
assert.equal(
  target(flow, [{ name: 10, kind: "industry", topic_code: code }]).topicCode,
  null,
);
assert.equal(
  request(code, date),
  "/market/sector-chart?topic_code=881101.TI&date=2026-10-20",
);
assert.equal(request("BK0001", date), null);
assert.equal(request(code, "yesterday"), null);
const task = {
  job_id: "fixture-task",
  status: "queued",
  target_date: date,
  parameters: { operation: "sector_chart", topic_code: code, as_of: date },
};
assert.equal(matches(task, code, date), true);
for (const invalid of [
  null,
  { ...task, target_date: "2026-10-19" },
  { ...task, parameters: { ...task.parameters, topic_code: "885431.TI" } },
  { ...task, parameters: { ...task.parameters, as_of: "2026-10-19" } },
  {
    ...task,
    parameters: { ...task.parameters, operation: "market_supplement" },
  },
  { ...task, parameters: { topic_code: code, date } },
])
  assert.equal(
    matches(invalid, code, date),
    false,
    "other board/date/task scope must not be adopted",
  );
assert.equal(progress(task).progress, 0);
assert.equal(progress(task).statusLabel, "排队等待");
assert.equal(
  progress({ status: "running", progress: 42, stage: "核验日线" }).progress,
  42,
);
assert.equal(
  progress({ status: "running", progress: 42, stage: "核验日线" }).stage,
  "核验日线",
);
for (const value of [null, undefined, NaN, Infinity, -1, 101, "20", true])
  assert.equal(
    progress({ status: "running", progress: value }).progress,
    null,
    "unknown progress must never increase on its own",
  );
assert.equal(progress({ status: "running" }).progressLabel, "进度待确认");
assert.equal(progress({ status: "partial", progress: 99 }).progress, 100);
assert.equal(progress({ status: "partial" }).complete, true);
for (const status of ["failed", "paused", "cancelled", "canceled"]) {
  assert.equal(progress({ status }).active, false);
  assert.equal(progress({ status }).complete, false);
}
assert.equal(
  progress({ status: "failed", error_summary: "来源限流" }).message,
  "来源限流",
);
assert.match(progress({ status: "paused" }).message, /手动继续/);
console.log(
  "sector charts: independent price/member capability, strict unique industry proxy, task/date isolation, real progress and manual interrupted states passed",
);
