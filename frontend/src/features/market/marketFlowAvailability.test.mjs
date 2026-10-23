import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./marketFlowAvailability.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { flowDisplayState } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const current = {
  summary: { net_flow_available: true },
  items: [{ topic_code: "test", net_inflow: 0 }],
  history: [{ topic_code: "test", date: "2026-10-22", net_inflow: -100 }],
};
assert.deepEqual(flowDisplayState(current, "flow", "industry", "2026-10-23"), {
  fundFlowAvailable: true,
  hasFundFlow: true,
  hasFundTrend: true,
});
assert.equal(
  flowDisplayState(current, "flow", "concept", "2026-10-23").hasFundTrend,
  false,
);
assert.equal(
  flowDisplayState(current, "sectors", "industry", "2026-10-23").hasFundFlow,
  false,
);
for (const invalid of [
  null,
  {},
  { ...current, summary: { net_flow_available: false } },
  { ...current, items: [] },
  { ...current, items: [{ topic_code: "test", net_inflow: null }] },
]) {
  assert.equal(
    flowDisplayState(invalid, "flow", "industry", "2026-10-23").hasFundTrend,
    false,
  );
}
for (const history of [
  [],
  [{ topic_code: "test", date: "2026-10-22", net_inflow: null }],
  [{ topic_code: "other", date: "2026-10-22", net_inflow: 1 }],
  [{ topic_code: "test", date: "2026-10-24", net_inflow: 1 }],
  [{ topic_code: "test", date: "2026-02-30", net_inflow: 1 }],
]) {
  const state = flowDisplayState(
    { ...current, history },
    "flow",
    "industry",
    "2026-10-23",
  );
  assert.equal(state.hasFundFlow, true);
  assert.equal(
    state.hasFundTrend,
    false,
    "unknown, unrelated or future values do not create empty trends",
  );
}
const view = await fs.readFile(
  new URL("./Market.tsx", import.meta.url),
  "utf8",
);
assert.match(view, /hasFundTrend\s*\?\s*<FundTrend/);
assert.doesNotMatch(
  view,
  /<Panel title="全天资金轮动时序"|setSealMode|封单最稳/,
);
assert.match(view, /title="题材"|title: "题材"/);
assert.match(view, /title: "行业"/);
console.log(
  "Market flow: verified zero remains visible; missing/future/unrelated history and unavailable tools stay hidden",
);
