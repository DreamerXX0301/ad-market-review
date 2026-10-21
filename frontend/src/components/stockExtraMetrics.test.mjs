import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compiled = ts.transpileModule(
  await fs.readFile(new URL("./stockExtraMetrics.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  stockMetricDate,
  stockSummaryEndpoint,
  stockSummaryMatches,
  stockExtraMetrics,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
assert.equal(stockMetricDate("2026-02-30"), null);
assert.equal(stockMetricDate("2024-02-29"), "2024-02-29");
assert.equal(
  stockSummaryEndpoint("ti883900", "2026-10-20"),
  null,
  "sector codes never request stock summary",
);
assert.equal(
  stockSummaryEndpoint("600825.SH", "2026-10-20"),
  "/instruments/600825.SH/summary?as_of=2026-10-20",
);
assert.equal(stockSummaryEndpoint("600825.SH", "bad-date"), null);
const row = {
  instrument_id: "600825.SH",
  data_date: "2026-10-20",
  limit_up_ytd: 0,
  limit_up_ytd_observed: 0,
  limit_up_ytd_coverage: { complete: true },
  last_limit_up_date: "2026-10-20",
  sessions_since_limit_up: 0,
  last_limit_up_complete: true,
  return_ytd_pct: 0,
  return_ytd_coverage: {
    start: "2025-12-31",
    end: "2026-10-20",
    price_basis: "none",
  },
  change_30d_pct: -10,
  change_30d_coverage: {
    start: "2026-08-31",
    end: "2026-10-20",
    price_basis: "forward_event_affine",
  },
};
const exact = stockExtraMetrics(row, "600825.SH", "2026-10-20");
assert.equal(exact.annual.value, "0 次", "verified exact zero is retained");
assert.equal(exact.recent.note, "距观察日 0 个交易日");
assert.equal(exact.recent.qualifier, "最近一次");
assert.equal(exact.ytd.value, "0.00%");
assert.equal(exact.thirty.value, "-10.00%");
assert.equal(exact.thirty.tone, "down");
const partial = stockExtraMetrics(
  {
    ...row,
    limit_up_ytd: null,
    limit_up_ytd_observed: 3,
    last_limit_up_complete: false,
  },
  "600825.SH",
  "2026-10-20",
);
assert.equal(partial.annual.value, "已确认≥3次");
assert.equal(partial.recent.qualifier, "最近已确认");
assert.equal(
  stockExtraMetrics(
    { ...row, limit_up_ytd: null, limit_up_ytd_observed: 0 },
    "600825.SH",
  ).annual.value,
  "待核验",
  "unknown observed zero is not exact zero",
);
assert.equal(
  stockExtraMetrics(
    { ...row, limit_up_ytd_coverage: { complete: false } },
    "600825.SH",
  ).annual.known,
  false,
);
assert.equal(
  stockSummaryMatches(row, "600000.SH", "2026-10-20"),
  false,
  "switching stocks hides the previous response",
);
assert.equal(
  stockSummaryMatches(row, "600825.SH", "2026-10-19"),
  false,
  "historical views reject newer statistics",
);
assert.equal(
  stockExtraMetrics(row, "600825.SH", "2026-10-19").annual.known,
  false,
);
const future = stockExtraMetrics(
  {
    ...row,
    last_limit_up_date: "2026-10-21",
    return_ytd_coverage: { end: "2026-10-21" },
  },
  "600825.SH",
  "2026-10-20",
);
assert.equal(future.recent.known, false);
assert.equal(future.ytd.known, false);
assert.equal(
  stockExtraMetrics({ ...row, return_ytd_pct: Infinity }, "600825.SH").ytd
    .known,
  false,
);
assert.equal(
  stockExtraMetrics({ ...row, return_ytd_pct: 15.5 }, "600825.SH").ytd.tone,
  "up",
);
console.log(
  "stock extras: exact/lower-bound/unknown zero, stock/date isolation, 30-session returns and future guards passed",
);
