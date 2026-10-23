import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

async function moduleAt(path) {
  const source = await fs.readFile(new URL(path, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );
}
const {
  createStockNavigation,
  stockNavigationPosition,
  stockSelectionForOpen,
  stepStockSelection,
  stockPayloadMatches,
  trackingEventBaseline,
} = await moduleAt("./stockNavigation.ts");
const { sortTableRows } = await moduleAt("./tableSort.ts");

const rows = Array.from({ length: 48 }, (_, index) => ({
  instrument_id: `${600000 + index}.SH`,
  name: `样本${index}`,
  code: String(600000 + index),
  change_pct: index === 4 ? null : index - 20,
  decision: index % 4 === 0 ? "unknown" : "matched",
}));
const visible = rows.filter((row) => row.decision === "matched");
const ordered = sortTableRows(
  visible,
  { key: "change_pct", order: "desc" },
  (row) => row.change_pct,
);
const navigation = createStockNavigation(ordered, {
  scopeKey: "run-1:matched",
  label: "样本策略 · 满足条件",
  asOf: "2026-10-16",
});
assert.equal(navigation.items.length, visible.length);
assert.deepEqual(
  navigation.items.map((item) => item.id),
  ordered.map((row) => row.instrument_id),
);
assert(
  navigation.items.every((item) =>
    visible.some((row) => row.instrument_id === item.id),
  ),
);

// Opening the final item on page one continues into page two's first item.
const pageOneLast = ordered.slice(0, 15).at(-1);
const selected = stockSelectionForOpen(
  pageOneLast.instrument_id,
  navigation,
  "2026-10-20", // The independent market view date must not leak in.
);
assert.equal(selected.asOf, "2026-10-16");
assert.equal(stockNavigationPosition(navigation, selected.id).position, 15);
const next = stepStockSelection(selected, 1);
assert.equal(next.id, ordered[15].instrument_id);
assert.equal(next.asOf, "2026-10-16");
assert.equal(stockNavigationPosition(next.navigation, next.id).position, 16);
assert.equal(stepStockSelection(next, -1).id, selected.id);

const first = stockSelectionForOpen(ordered[0].instrument_id, navigation);
const last = stockSelectionForOpen(ordered.at(-1).instrument_id, navigation);
assert.equal(
  stockNavigationPosition(navigation, first.id).previousId,
  undefined,
);
assert.equal(stockNavigationPosition(navigation, last.id).nextId, undefined);
assert.equal(stepStockSelection(first, -1), first);
assert.equal(stepStockSelection(last, 1), last);
assert.equal(
  stockNavigationPosition(navigation, last.id).position,
  visible.length,
);

// A row snapshot must not change when a later query, sorting action or row update arrives.
const originalName = navigation.items[0].name;
ordered[0].name = "后续返回的名称";
ordered.reverse();
assert.equal(navigation.items[0].name, originalName);
assert.equal(navigation.items[0].id, first.id);

const clean = createStockNavigation(
  [
    { instrument_id: "600001.sh", name: "首条" },
    { instrument_id: "600001.SH", name: "重复" },
    { instrument_id: "" },
    { name: "缺代码" },
    { instrument_id: "000001.SZ" },
  ],
  { scopeKey: "run-2:unknown", label: "待核验" },
);
assert.deepEqual(
  clean.items.map((item) => item.id),
  ["600001.SH", "000001.SZ"],
);
assert.equal(clean.items[0].name, "首条");
assert.equal(stockNavigationPosition(clean, "600001.sh").position, 1);
assert.equal(stockNavigationPosition(clean, "not-in-the-result"), null);
assert.equal(stockNavigationPosition(undefined, "600001.SH"), null);
const one = createStockNavigation([{ instrument_id: "600001.SH" }], {
  scopeKey: "single",
  label: "单只结果",
});
assert.equal(stockNavigationPosition(one, "600001.SH").total, 1);
assert.equal(stockNavigationPosition(one, "600001.SH").nextId, undefined);
assert.equal(stockNavigationPosition(one, "600001.SH").previousId, undefined);

// Existing one-argument entry points remain latest or retain their market date.
assert.deepEqual(stockSelectionForOpen("600001.SH"), {
  id: "600001.SH",
  asOf: undefined,
  navigation: undefined,
});
assert.equal(
  stockSelectionForOpen("600001.SH", undefined, "2026-10-19").asOf,
  "2026-10-19",
);
assert.equal(
  stockSelectionForOpen("not-in-the-result", navigation).navigation,
  undefined,
);
assert.equal(stepStockSelection({ id: "600001.SH" }, 1).id, "600001.SH");

// Tracking anchors belong to a particular stock/round and cannot follow navigation.
const withOldBaseline = {
  ...first,
  eventBaseline: {
    roundId: "previous-round",
    date: "2026-10-01",
    rawClose: 10,
  },
};
assert(!("eventBaseline" in stepStockSelection(withOldBaseline, 1)));
const tracked = { id: "600001.SH", eventBaseline: { roundId: "unchanged" } };
assert.equal(stepStockSelection(tracked, 1), tracked);

// In-flight responses cannot provide a previous stock's name, prices or date.
const payload = {
  instrument: { instrument_id: "600001.SH", name: "当前股" },
  as_of: "2026-10-16",
  bars: [{ close: 10 }],
};
assert(stockPayloadMatches(payload, "600001.SH", "2026-10-16"));
assert(!stockPayloadMatches(payload, "000001.SZ", "2026-10-16"));
assert(!stockPayloadMatches(payload, "600001.SH", "2026-10-19"));
assert(
  stockPayloadMatches(
    { instrument_id: "600001.SH", data_date: "2026-10-16" },
    "600001.SH",
    "2026-10-16",
  ),
);
assert(!stockPayloadMatches({ instrument: { code: "600001" } }, "600001.SH"));
assert(!stockPayloadMatches(null, "600001.SH"));

// Tracking follows only this filtered/sorted loaded view, with one frozen
// baseline per security. A clicked duplicate uses that exact round's anchor.
const firstRound = {
  round_id: "r-old",
  entry_business_date: "2026-09-01",
  entry_price: 8,
  entry_basis: { price_basis: "none" },
};
const clickedRound = {
  round_id: "r-clicked",
  entry_business_date: "2026-10-01",
  entry_price: 12,
  entry_basis: { price_basis: "none" },
};
const otherRound = {
  round_id: "r-other",
  entry_business_date: "2026-09-15",
  entry_price: 20,
  entry_basis: { price_basis: "none" },
};
const trackingRows = [
  {
    instrument_id: "600001.SH",
    name: "甲",
    eventBaseline: trackingEventBaseline(firstRound),
  },
  {
    instrument_id: "600002.SH",
    name: "乙",
    eventBaseline: trackingEventBaseline(otherRound),
  },
  {
    instrument_id: "600001.SH",
    name: "甲",
    eventBaseline: trackingEventBaseline(clickedRound),
  },
];
const tracking = createStockNavigation(
  trackingRows,
  {
    source: "tracking",
    scopeKey: "alerts:filtered",
    label: "跟踪池 · 当前筛选 · 已去重股票",
    asOf: "2026-10-20",
  },
  trackingRows[2],
);
assert.deepEqual(
  tracking.items.map((item) => item.id),
  ["600001.SH", "600002.SH"],
);
assert.equal(tracking.items[0].eventBaseline.roundId, "r-clicked");
assert.equal(tracking.items[0].eventBaseline.rawClose, 12);
const trackedOpen = stockSelectionForOpen("600001.SH", tracking, "2026-10-16");
assert.equal(
  trackedOpen.asOf,
  "2026-10-20",
  "market view date does not replace tracking business day",
);
const trackedNext = stepStockSelection(
  { ...trackedOpen, eventBaseline: trackingEventBaseline(clickedRound) },
  1,
);
assert(
  !("eventBaseline" in trackedNext),
  "a previous round must never remain in the next selection fallback",
);
assert.equal(
  stockNavigationPosition(trackedNext.navigation, trackedNext.id).current
    .eventBaseline.roundId,
  "r-other",
);
assert.equal(
  stockNavigationPosition(trackedNext.navigation, trackedNext.id).current
    .eventBaseline.rawClose,
  20,
);
trackingRows[2].eventBaseline.rawClose = 999;
assert.equal(
  tracking.items[0].eventBaseline.rawClose,
  12,
  "navigation keeps a baseline snapshot",
);
const plainStrategy = createStockNavigation(trackingRows, {
  scopeKey: "strategy-plain",
  label: "选股",
});
for (const source of ["market", "members"]) {
  const ordinary = createStockNavigation(
    [
      {
        ...trackingRows[0],
        checks: { all: [{ field: "close", result: true }] },
        entry_basis: { entry_checks: { result: true } },
      },
      trackingRows[1],
    ],
    {
      source,
      scopeKey: source + ":visible",
      label: "普通列表",
      asOf: "2026-10-16",
    },
  );
  const selectedOrdinary = stockSelectionForOpen(
    "600001.SH",
    ordinary,
    "2026-10-20",
  );
  assert.equal(selectedOrdinary.asOf, "2026-10-16");
  assert.equal(stockNavigationPosition(ordinary, selectedOrdinary.id).total, 2);
  assert.equal(stepStockSelection(selectedOrdinary, 1).id, "600002.SH");
  assert.ok(!("eventBaseline" in ordinary.items[0]));
  assert.ok(
    !("historicalChecks" in ordinary.items[0]),
    "ordinary list cannot project accidental strategy checks",
  );
}
assert(
  !("eventBaseline" in plainStrategy.items[0]),
  "strategy navigation cannot inherit tracking baselines",
);
console.log(
  "stock navigation: filtered/sorted full result, cross-page order, boundaries, date scope, per-round tracking anchors, immutable snapshot and stale response guards passed",
);
