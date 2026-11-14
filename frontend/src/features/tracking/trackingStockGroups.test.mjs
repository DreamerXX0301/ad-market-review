import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./trackingStockGroups.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ES2022,
  },
}).outputText;
const { groupTrackingStocks } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

const businessDate = "2026-11-13";
const first = {
  instrument_id: "600001.SH",
  name: "甲股票",
  code: "600001",
  round_id: "round-a",
  strategy_id: "strategy-a",
  strategy_name: "策略甲",
  valuation_date: businessDate,
  latest_price: 12,
  today_change_pct: 2.5,
  entry_business_date: "2026-11-04",
  entry_price: 10,
  tracking_change_pct: 20,
  policy: { duration_sessions: 30 },
  entry_basis: { entry_checks: [{ field: "kd_cross", result: true }] },
  alerts: [
    { event_id: "today-a", business_date: businessDate, event_type: "alert" },
    { event_id: "today-b", business_date: businessDate, event_type: "alert" },
  ],
};
const second = {
  ...first,
  round_id: "round-b",
  strategy_id: "strategy-b",
  strategy_name: "策略乙",
  entry_business_date: "2026-11-10",
  entry_price: 12.5,
  tracking_change_pct: -4,
  policy: { duration_sessions: 5 },
  entry_basis: { entry_checks: [{ field: "volume_ratio", result: true }] },
  alerts: [{ event_id: "old", business_date: "2026-11-12" }],
};
const other = {
  ...first,
  instrument_id: "000002.SZ",
  name: "乙股票",
  code: "000002",
  round_id: "round-c",
  alerts: [],
};
const rows = [first, other, second];
const before = structuredClone(rows);
const grouped = groupTrackingStocks(rows, businessDate);
assert.deepEqual(
  grouped.map((row) => row.instrument_id),
  ["600001.SH", "000002.SZ"],
  "stock order follows first appearance, not an entry date or strategy choice",
);
const stock = grouped[0];
assert.equal(stock.strategy_count, 2);
assert.equal(stock.round_count, 2);
assert.deepEqual(stock.strategy_names, ["策略甲", "策略乙"]);
assert.deepEqual(stock.rounds, [first, second]);
assert.equal(stock.rounds[0], first);
assert.equal(stock.rounds[1], second);
assert.equal(stock.rounds[0].policy, first.policy);
assert.equal(stock.rounds[1].entry_basis, second.entry_basis);
assert.equal(stock.earliest_entry_date, "2026-11-04");
assert.equal(stock.last_entry_date, "2026-11-10");
assert.equal(stock.latest_price, 12);
assert.equal(stock.today_change_pct, 2.5);
assert.equal(stock.valuation_date, businessDate);
assert.equal(stock.quote_reason, "");
assert.equal(
  stock.triggered_round_count,
  1,
  "two alerts are one triggered round",
);
for (const field of [
  "round_id",
  "strategy_id",
  "strategy_name",
  "entry_business_date",
  "entry_price",
  "tracking_change_pct",
  "policy",
  "entry_basis",
  "alerts",
  "remaining_sessions",
  "trading_age",
])
  assert.equal(
    Object.hasOwn(stock, field),
    false,
    `parent must not impersonate one child through ${field}`,
  );
assert.deepEqual(
  rows,
  before,
  "grouping cannot mutate source rounds or their evidence",
);
stock.rounds.pop();
assert.deepEqual(rows, before, "group array is separate from the source array");

// Filtering must be completed first. A matching stock never drags unfiltered
// strategy rounds, old alerts, or policies back into the visible child list.
const selected = groupTrackingStocks(
  rows.filter((row) => row.strategy_id === "strategy-b"),
  businessDate,
);
assert.equal(selected.length, 1);
assert.equal(selected[0].round_count, 1);
assert.equal(selected[0].strategy_count, 1);
assert.deepEqual(selected[0].rounds, [second]);
assert.equal(selected[0].triggered_round_count, 0);
assert.deepEqual(selected[0].strategy_names, ["策略乙"]);

const repeatedRound = groupTrackingStocks(
  [first, first, second],
  businessDate,
)[0];
assert.equal(
  repeatedRound.round_count,
  3,
  "grouping must not discard source rounds",
);
assert.equal(repeatedRound.strategy_count, 2);
assert.equal(
  repeatedRound.triggered_round_count,
  1,
  "same round is not counted twice",
);
const sameStrategy = groupTrackingStocks(
  [
    first,
    {
      ...second,
      strategy_id: first.strategy_id,
      strategy_name: first.strategy_name,
    },
  ],
  businessDate,
)[0];
assert.equal(sameStrategy.round_count, 2);
assert.equal(sameStrategy.strategy_count, 1);
assert.deepEqual(sameStrategy.strategy_names, ["策略甲"]);
assert.equal(sameStrategy.rounds[1].entry_price, 12.5);

const mismatchedDate = groupTrackingStocks(
  [first, { ...second, valuation_date: "2026-11-12" }],
  businessDate,
)[0];
assert.equal(mismatchedDate.valuation_date, null);
assert.equal(mismatchedDate.latest_price, null);
assert.equal(mismatchedDate.today_change_pct, null);
assert.match(mismatchedDate.quote_reason, /不同日期/);
for (const date of [undefined, "", "2026-02-30", "2026-11-14"])
  assert.equal(groupTrackingStocks([first], date)[0].latest_price, null);

const priceConflict = groupTrackingStocks(
  [first, { ...second, latest_price: 12.01 }],
  businessDate,
)[0];
assert.equal(priceConflict.latest_price, null);
assert.match(priceConflict.quote_reason, /收盘价不一致/);
const changeConflict = groupTrackingStocks(
  [first, { ...second, today_change_pct: -1 }],
  businessDate,
)[0];
assert.equal(changeConflict.today_change_pct, null);
assert.equal(
  changeConflict.latest_price,
  12,
  "a return conflict cannot erase agreed real price",
);
assert.match(changeConflict.quote_reason, /当日涨幅不一致/);
for (const invalid of [null, undefined, NaN, Infinity, 0, -1, "12", true]) {
  const value = groupTrackingStocks(
    [first, { ...second, latest_price: invalid }],
    businessDate,
  )[0];
  assert.equal(
    value.latest_price,
    null,
    `invalid price ${String(invalid)} remains unknown`,
  );
  assert.match(value.quote_reason, /缺少有效收盘价/);
}
for (const invalid of [null, undefined, NaN, Infinity, "2.5", true]) {
  const value = groupTrackingStocks(
    [first, { ...second, today_change_pct: invalid }],
    businessDate,
  )[0];
  assert.equal(value.today_change_pct, null);
  assert.equal(value.latest_price, 12);
}
const flat = groupTrackingStocks(
  [first, second].map((row) => ({ ...row, today_change_pct: 0 })),
  businessDate,
)[0];
assert.equal(
  flat.today_change_pct,
  0,
  "verified flat change is not an unknown",
);

const alertScopes = groupTrackingStocks(
  [
    {
      ...first,
      alerts: [{ business_date: businessDate, event_type: "review" }],
    },
    {
      ...second,
      alerts: [{ business_date: businessDate, round_id: "unrelated" }],
    },
  ],
  businessDate,
)[0];
assert.equal(alertScopes.triggered_round_count, 0);
assert.equal(
  groupTrackingStocks(
    [{ ...first, alerts: [{ event_id: "undated" }] }],
    businessDate,
  )[0].triggered_round_count,
  0,
);
assert.equal(groupTrackingStocks([first])[0].triggered_round_count, 0);

const missingEntry = groupTrackingStocks(
  [first, { ...second, entry_business_date: null }],
  businessDate,
)[0];
assert.equal(missingEntry.earliest_entry_date, null);
assert.equal(missingEntry.last_entry_date, null);
assert.equal(
  groupTrackingStocks(
    [first, { ...second, entry_business_date: "2026-02-30" }],
    businessDate,
  )[0].earliest_entry_date,
  null,
);
assert.deepEqual(groupTrackingStocks([], businessDate), []);
assert.equal(
  groupTrackingStocks(
    [{}, { instrument_id: "" }, { instrument_id: 600001 }, first],
    businessDate,
  ).length,
  1,
  "unknown identities are not grouped into a fake stock",
);
const unnamed = groupTrackingStocks(
  [{ ...first, name: "", code: "", strategy_name: "" }, second],
  businessDate,
)[0];
assert.equal(unnamed.name, second.name);
assert.equal(unnamed.code, second.code);
assert.deepEqual(unnamed.strategy_names, [
  first.strategy_id,
  second.strategy_name,
]);

console.log(
  "tracking stock groups: filtered identity grouping, untouched round evidence, scoped counts, exact quote consensus and unknowns passed",
);
