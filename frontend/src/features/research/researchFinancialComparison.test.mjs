import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./researchFinancialComparison.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { researchFinancialComparison: compare } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const dayMs = (day) => Date.parse(day + "T00:00:00+08:00");
function row(year, value = 100, patch = {}) {
  const date = `${year}-06-30`;
  return {
    source: "fuyao",
    thscode: "600000.SH",
    currency: "CNY",
    unit: "CNY；basic_eps为元/股",
    period: "quarterly",
    fiscal_year: year,
    fiscal_period: "Q2",
    report_period: date,
    period_end_ms: dayMs(date),
    report_date_ms: dayMs(`${year}-08-20`),
    published_at_ms: dayMs(`${year}-08-20`),
    operating_income: value,
    parent_holder_net_profit: value,
    net_profit: value,
    basic_eps: value,
    act_cash_flow_net: value,
    invest_cash_flow_net: value,
    financing_cash_flow_net: value,
    assets_total: value,
    total_debt: value,
    holder_equity_total: value,
    ...patch,
  };
}
const prior = row(2024, 100);
const current = row(2025, 120);
const run = (a = current, b = prior, field = "operating_income", extras = []) =>
  compare([b, ...extras, a], a, field);
assert.deepEqual(run(), {
  delta: 20,
  percent: 20,
  previousDate: "2024-06-30",
  reason: null,
  label: null,
});
assert.equal(run(row(2025, 80)).percent, -20);
assert.equal(run(row(2025, 100)).delta, 0);
assert.equal(run(row(2025, 100)).percent, 0);
assert.equal(Object.is(run(row(2025, -0), row(2024, 0)).delta, -0), false);
assert.equal(run(row(2025, 100), row(2024, 0)).percent, null);
assert.equal(run(row(2025, 100), row(2024, 0)).delta, 100);
assert.match(run(row(2025, 100), row(2024, 0)).reason, /为零/);
for (const field of ["parent_holder_net_profit", "net_profit", "basic_eps"]) {
  assert.equal(run(row(2025, -50), row(2024, -100), field).label, "亏损收窄");
  assert.equal(run(row(2025, -150), row(2024, -100), field).label, "亏损扩大");
  assert.equal(run(row(2025, -100), row(2024, -100), field).label, "亏损持平");
  assert.equal(run(row(2025, 20), row(2024, -100), field).label, "扭亏为盈");
  assert.equal(run(row(2025, -20), row(2024, 100), field).label, "转亏");
  assert.equal(run(row(2025, -20), row(2024, 0), field).label, "转亏");
  assert.equal(run(row(2025, 0), row(2024, -100), field).label, "亏损收窄");
  assert.equal(run(row(2025, -50), row(2024, -100), field).percent, null);
  assert.equal(run(row(2025, -50), row(2024, -100), field).delta, 50);
  assert.equal(run(row(2025, -20), row(2024, 100), field).percent, -120);
}
for (const field of [
  "act_cash_flow_net",
  "invest_cash_flow_net",
  "financing_cash_flow_net",
  "assets_total",
  "total_debt",
  "holder_equity_total",
]) {
  assert.equal(run(current, prior, field).percent, 20);
  assert.equal(run(row(2025, -50), row(2024, -100), field).label, null);
}
assert.equal(run(current, prior, "pe_ttm").delta, null);
assert.equal(run(current, prior, "toString").delta, null);
for (const value of [null, undefined, NaN, Infinity, -Infinity, "120", true]) {
  assert.equal(run(row(2025, 120, { operating_income: value })).delta, null);
  assert.equal(
    run(current, row(2024, 100, { operating_income: value })).delta,
    null,
  );
}
assert.equal(
  run(row(2025, Number.MAX_VALUE), row(2024, -Number.MAX_VALUE)).delta,
  null,
);
assert.equal(
  run(row(2025, Number.MAX_VALUE), row(2024, Number.MIN_VALUE)).delta,
  Number.MAX_VALUE,
);
assert.equal(
  run(row(2025, Number.MAX_VALUE), row(2024, Number.MIN_VALUE)).percent,
  null,
);

// Match the previous year, never the previous row or another quarter.
const q1 = row(2025, 999, {
  fiscal_period: "Q1",
  report_period: "2025-03-31",
  period_end_ms: dayMs("2025-03-31"),
});
assert.equal(run(current, prior, "operating_income", [q1]).delta, 20);
assert.equal(compare([q1, current], current, "operating_income").delta, null);
assert.equal(
  compare([prior, current].reverse(), current, "operating_income").delta,
  20,
);
assert.equal(
  run(current, prior, "operating_income", [{ ...prior }]).delta,
  null,
);
assert.equal(
  run(current, prior, "operating_income", [{ ...current }]).delta,
  null,
);
assert.equal(
  compare(
    [prior, { ...current, operating_income: 121 }],
    current,
    "operating_income",
  ).delta,
  null,
);

for (const key of [
  "source",
  "thscode",
  "currency",
  "unit",
  "period",
  "fiscal_period",
  "fiscal_year",
  "report_period",
  "period_end_ms",
  "report_date_ms",
  "published_at_ms",
]) {
  const missing = { ...current };
  delete missing[key];
  assert.equal(run(missing).delta, null, `missing current ${key}`);
  const oldMissing = { ...prior };
  delete oldMissing[key];
  assert.equal(run(current, oldMissing).delta, null, `missing prior ${key}`);
}
for (const patch of [
  { source: "other" },
  { thscode: "000001.SZ" },
  { currency: "USD" },
  { unit: "万元" },
  { period: "annual" },
  { fiscal_period: "Q1" },
  { fiscal_year: 2023 },
  { report_period: "2024-06-31" },
  { period_end_ms: dayMs("2024-06-30") + 1 },
  { published_at_ms: dayMs("2024-08-21") },
  { report_date_ms: dayMs("2024-06-29"), published_at_ms: dayMs("2024-06-29") },
])
  assert.equal(run(current, row(2024, 100, patch)).delta, null);
assert.equal(run(row(2025, 120, { source: " " })).delta, null);
assert.equal(run(row(2025, 120, { fiscal_year: "2025" })).delta, null);
assert.equal(
  run(
    current,
    row(2024, 100, {
      report_date_ms: dayMs("2026-08-20"),
      published_at_ms: dayMs("2026-08-20"),
    }),
  ).delta,
  null,
);

function yearEnd(year, period, fiscal_period, value) {
  return row(year, value, {
    period,
    fiscal_period,
    report_period: `${year}-12-31`,
    period_end_ms: dayMs(`${year}-12-31`),
    report_date_ms: dayMs(`${year + 1}-03-20`),
    published_at_ms: dayMs(`${year + 1}-03-20`),
  });
}
const annual = yearEnd(2025, "annual", "FY", 120);
const oldAnnual = yearEnd(2024, "annual", "FY", 100);
assert.equal(run(annual, oldAnnual).percent, 20);
assert.equal(
  run(
    yearEnd(2025, "quarterly", "Q4", 120),
    yearEnd(2024, "quarterly", "Q4", 100),
  ).percent,
  20,
);
assert.equal(run(annual, yearEnd(2024, "annual", "Q4", 100)).delta, null);
assert.equal(
  run(yearEnd(2025, "quarterly", "Q4", 120), yearEnd(2024, "annual", "Q4", 100))
    .delta,
  null,
);
for (const unsupported of ["ytd", "cumulative", "single_quarter"])
  assert.equal(
    run(
      row(2025, 120, { period: unsupported }),
      row(2024, 100, { period: unsupported }),
    ).delta,
    null,
  );
assert.equal(
  run(
    yearEnd(2025, "quarterly", "FY", 120),
    yearEnd(2024, "quarterly", "FY", 100),
  ).delta,
  null,
);
assert.equal(compare(null, current, "operating_income").delta, null);
assert.equal(compare([], null, "operating_income").delta, null);

// No mutation or implicit source conversion is part of this display helper.
const frozen = Object.freeze([
  Object.freeze({ ...prior }),
  Object.freeze({ ...current }),
]);
assert.equal(compare(frozen, frozen[1], "operating_income").delta, 20);
console.log(
  "research financial comparison: strict same-year period metadata, missing/conflicting sources, zero/negative bases, profit labels, annual/quarterly isolation and immutable inputs passed",
);
