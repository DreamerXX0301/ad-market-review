import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

async function load(path) {
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
const { watchlistEventBaseline, filterWatchlistRows, watchlistPerformance } =
  await load("./watchlistPresentation.ts");
const {
  createStockNavigation,
  stockSelectionForOpen,
  stockNavigationPosition,
  stepStockSelection,
} = await load("../../components/stockNavigation.ts");

const rows = [
  {
    instrument_id: "600001.SH",
    tracking_round_id: "original-round",
    event_date: "2026-10-15",
    event_close: 12,
    event_price_basis: "none",
  },
  {
    instrument_id: "600002.SH",
    source_kind: "sector",
    observed_date: "2026-10-20",
    event_close: null,
  },
  {
    instrument_id: "600003.SH",
    tracking_round_id: "second-round",
    event_date: "2026-10-16",
    event_close: 8.5,
    event_price_basis: "none",
  },
];
const independent = {
  ...rows[0],
  added_business_date: "2026-10-21",
  watchlist_entry_price: 23.47,
  watchlist_change_pct: 0,
  watchlist_change_state: "ready",
};
assert.deepEqual(watchlistPerformance(independent), {
  value: 0,
  entryPrice: 23.47,
  entryDate: "2026-10-21",
  reason: null,
});
for (const value of [null, undefined, Number.NaN, Infinity, "5", true]) {
  assert.equal(
    watchlistPerformance({ ...independent, watchlist_change_pct: value }).value,
    null,
  );
}
assert.equal(
  watchlistPerformance({
    ...independent,
    watchlist_change_state: "unknown",
    watchlist_change_pct: 5,
  }).value,
  null,
);
assert.equal(
  watchlistPerformance({ ...independent, watchlist_entry_price: null }).value,
  null,
  "tracking event_close cannot substitute for the personal added-date baseline",
);
assert.equal(
  watchlistPerformance({ ...independent, watchlist_change_pct: -5 }).value,
  -5,
);
assert.equal(
  watchlistEventBaseline(rows[1]),
  undefined,
  "sector membership cannot become a member stock's event baseline",
);
assert.equal(
  watchlistEventBaseline({ ...rows[0], event_close: Number.NaN }),
  undefined,
);
assert.equal(watchlistEventBaseline({ ...rows[0], event_close: 0 }), undefined);
const navigation = createStockNavigation(
  rows.map((row) => ({ ...row, eventBaseline: watchlistEventBaseline(row) })),
  {
    source: "watchlist",
    scopeKey: "personal-watchlist",
    label: "我的自选",
    asOf: "2026-10-21",
  },
);
const opened = stockSelectionForOpen("600001.SH", navigation, "2026-09-01");
assert.equal(opened.asOf, "2026-10-21");
assert.equal(
  stockNavigationPosition(navigation, opened.id).current.eventBaseline.rawClose,
  12,
);
const next = stepStockSelection(opened, 1);
assert.equal(next.id, "600002.SH");
assert.equal(
  stockNavigationPosition(navigation, next.id).current.eventBaseline,
  undefined,
  "plain personal item cannot inherit the previous tracking anchor",
);
const third = stepStockSelection(next, 1);
assert.equal(
  stockNavigationPosition(navigation, third.id).current.eventBaseline.roundId,
  "second-round",
);
assert.equal(
  stockNavigationPosition(navigation, third.id).current.eventBaseline.rawClose,
  8.5,
);
assert.equal(stockNavigationPosition(navigation, third.id).nextId, undefined);
rows[0].event_close = 999;
assert.equal(
  stockNavigationPosition(navigation, opened.id).current.eventBaseline.rawClose,
  12,
  "navigation keeps immutable per-stock evidence",
);
console.log(
  "watchlist: manual origins preserve unknown event baseline; previous/next retain each stock's frozen tracking anchor and business date",
);

const searchable = [
  {
    instrument_id: "600001.SH",
    name: "样本一",
    code: "600001",
    source_kind: "tracking",
    source_name: "样本策略",
    current_topics: [
      { name: "概念甲", kind: "concept" },
      { name: "概念乙", kind: "concept" },
      { name: "折叠概念", kind: "concept" },
      { name: "行业一", kind: "industry" },
    ],
  },
  {
    instrument_id: "600002.SH",
    name: "样本二",
    code: "600002",
    source_kind: "topic",
    source_name: "来源板块",
    current_industries: [{ name: "行业二" }],
  },
  {
    instrument_id: "600003.SH",
    name: "样本三",
    code: "600003",
    source_kind: "stock",
    current_topics: [],
  },
];
assert.equal(
  filterWatchlistRows(searchable, "折叠概念").length,
  1,
  "search includes every concept beyond the two visible chips",
);
assert.equal(
  filterWatchlistRows(searchable, " 行业一 ")[0].instrument_id,
  "600001.SH",
);
assert.equal(
  filterWatchlistRows(searchable, "行业二")[0].instrument_id,
  "600002.SH",
);
assert.equal(filterWatchlistRows(searchable, "样本策略", "tracking").length, 1);
assert.equal(
  filterWatchlistRows(searchable, "", "sector").length,
  1,
  "topic and sector sources share the industry/topic filter",
);
assert.equal(filterWatchlistRows(searchable, "600001", "stock").length, 0);
assert.equal(filterWatchlistRows(searchable, "", "stock").length, 1);
assert.equal(filterWatchlistRows(searchable, "600001.sh").length, 1);
