import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./monitorHeat.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ES2022,
  },
}).outputText;
const { buildMonitorHeat } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const topicDate = "2026-11-13",
  tradeDate = "2026-11-16";
const tag = (id, kind = "industry", name = id) => ({
  topic_id: id,
  name,
  kind,
});
const options = (extra = {}) => ({
  topicDate,
  tradeDate,
  period: "intraday",
  kind: "industry",
  minSamples: 1,
  ...extra,
});
function stock(code, value, topics, extra = {}) {
  return {
    instrument_id: `${code}.SH`,
    code,
    name: `股票${code}`,
    change_pct: value,
    change_30d_pct: value,
    return_ytd_pct: value,
    topic_date: topicDate,
    metrics_date: topicDate,
    current_topics: topics,
    today_alert_count: 0,
    ...extra,
  };
}
const a = stock(
  "600001",
  3,
  [
    tag("industry-a"),
    tag("industry-a"),
    tag("concept-a", "concept"),
    tag("concept-b", "concept"),
  ],
  { today_alert_count: 1 },
);
const b = stock("600002", 0, [tag("industry-a"), tag("concept-a", "concept")]);
const c = stock("600003", null, [tag("industry-a")], { today_alert_count: 2 });
const d = stock("600004", -2, [tag("industry-b"), tag("concept-a", "concept")]);
const e = stock("600005", 5, [tag("industry-b"), tag("concept-b", "concept")]);
const f = stock("600006", -1, [tag("industry-b")]);
const manual = stock("600007", 2, [
  tag("manual-a", "manual"),
  tag("unknown-a", "theme"),
]);
const duplicateA = {
  ...a,
  instrument_id: "600001.sh",
  current_topics: [tag("industry-a"), ...a.current_topics],
};
const stocks = [a, b, c, d, e, f, manual, duplicateA];
const events = [
  {
    event_id: "event-b",
    instrument_id: b.instrument_id,
    trade_date: tradeDate,
  },
  {
    event_id: "event-b-again",
    instrument_id: b.instrument_id,
    trade_date: tradeDate,
  },
];
const before = structuredClone({ stocks, events });
const result = buildMonitorHeat(stocks, events, options());
assert.equal(
  result.stock_count,
  7,
  "duplicate securities and duplicate memberships count once",
);
assert.equal(result.valid_stock_count, 6);
assert.equal(
  result.unclassified_count,
  1,
  "concept/manual-only stock is unclassified for industry view",
);
assert.equal(result.alert_scope, "complete");
assert.deepEqual(
  result.groups.map((group) => group.topic_id),
  ["industry-a", "industry-b"],
);
const groupA = result.groups[0];
assert.equal(groupA.stock_count, 3);
assert.equal(groupA.valid_count, 2);
assert.equal(groupA.unknown_count, 1);
assert.equal(groupA.up_count, 1);
assert.equal(
  groupA.up_ratio_pct,
  50,
  "ratios use known quote count, excluding missing samples",
);
assert.equal(groupA.strong_count, 1, "3 percent is included in the >=3% count");
assert.equal(groupA.median_pct, 1.5);
assert.equal(groupA.mean_pct, 1.5);
assert.equal(
  groupA.alert_stock_count,
  2,
  "exact today's counts override limited event list, including exact zero",
);
assert.deepEqual(
  groupA.stocks.map((row) => row.instrument_id),
  [a.instrument_id, b.instrument_id, c.instrument_id],
);
assert.deepEqual(
  groupA.leaders.map((row) => row.heat_change_pct),
  [3, 0],
);
assert.ok(Math.abs(result.groups[1].up_ratio_pct - 100 / 3) < 1e-12);
assert.deepEqual(
  buildMonitorHeat(stocks, events, options({ minSamples: 3 })).groups.map(
    (group) => group.topic_id,
  ),
  ["industry-b"],
);
assert.equal(
  buildMonitorHeat(stocks, events, options({ minSamples: 5 })).groups.length,
  0,
);

const concepts = buildMonitorHeat(stocks, events, options({ kind: "concept" }));
assert.equal(
  concepts.allGroups.find((group) => group.topic_id === "concept-a")
    .stock_count,
  3,
);
assert.equal(
  concepts.allGroups.find((group) => group.topic_id === "concept-b")
    .stock_count,
  2,
);
assert.deepEqual(
  concepts.allGroups.find((group) => group.topic_id === "manual-a")
    .source_kinds,
  ["manual"],
);
assert.equal(
  concepts.allGroups.some((group) => group.topic_id === "unknown-a"),
  false,
  "unrecognized kind does not become a concept",
);
assert.ok(
  concepts.allGroups.reduce((sum, group) => sum + group.stock_count, 0) >
    concepts.stock_count - concepts.unclassified_count,
  "concept memberships overlap and are not a whole-market sum",
);

// Missing or unverified metrics remain unknown; a genuine numeric zero is valid.
const noQuotes = [
  null,
  undefined,
  Number.NaN,
  Number.POSITIVE_INFINITY,
  "3",
].map((value, index) => stock(`60100${index}`, value, [tag("empty")]));
const empty = buildMonitorHeat(noQuotes, [], options());
assert.equal(empty.valid_stock_count, 0);
assert.equal(empty.groups.length, 0);
assert.equal(empty.allGroups[0].valid_count, 0);
for (const field of [
  "up_count",
  "up_ratio_pct",
  "strong_count",
  "median_pct",
  "mean_pct",
])
  assert.equal(
    empty.allGroups[0][field],
    null,
    `no samples cannot invent zero for ${field}`,
  );
const actualZero = buildMonitorHeat(
  [stock("602000", 0, [tag("zero")])],
  [],
  options(),
).groups[0];
for (const field of [
  "up_count",
  "up_ratio_pct",
  "strong_count",
  "median_pct",
  "mean_pct",
])
  assert.equal(actualZero[field], 0);

const currentButNoLevels = stock("603001", 4, [tag("observed")], {
  state: "unknown",
  reason: "无可用支撑参考",
});
const wrongTradeDay = stock("603002", 100, [tag("observed")], {
  trade_date: "2026-11-13",
});
const quotes = buildMonitorHeat(
  [currentButNoLevels, wrongTradeDay],
  [],
  options(),
).allGroups[0];
assert.equal(
  quotes.valid_count,
  1,
  "missing levels do not invalidate a known current numeric quote",
);
assert.equal(quotes.median_pct, 4);

const dateRows = [
  stock("604001", 2, [tag("dated")], {
    change_30d_pct: 30,
    return_ytd_pct: 80,
  }),
  stock("604002", 5, [tag("dated")], {
    metrics_date: "2026-11-12",
    change_30d_pct: 300,
    return_ytd_pct: 800,
  }),
  stock("604003", 9, [tag("dated")], { topic_date: "2026-11-12" }),
];
const month = buildMonitorHeat(dateRows, [], options({ period: "30d" }));
assert.equal(
  month.groups[0].stock_count,
  2,
  "stale memberships never enter a current group",
);
assert.equal(
  month.groups[0].valid_count,
  1,
  "stale metric day remains unknown rather than entering the denominator",
);
assert.equal(
  month.groups[0].median_pct,
  30,
  "API percent values are not multiplied by 100",
);
assert.equal(
  buildMonitorHeat(dateRows, [], options({ period: "ytd" })).groups[0]
    .median_pct,
  80,
);
assert.equal(
  buildMonitorHeat(dateRows, [], options({ topicDate: "2026-02-30" })).allGroups
    .length,
  0,
);
assert.equal(
  buildMonitorHeat(dateRows, [], options({ topicDate: undefined })).allGroups
    .length,
  0,
);

// The optional loaded-event fallback deduplicates stocks and only uses the requested day.
const oldPayload = [
  stock("605001", 1, [tag("alerts")], { today_alert_count: undefined }),
  stock("605002", 1, [tag("alerts")], { today_alert_count: 0 }),
  stock("605003", 1, [tag("alerts")], { today_alert_count: 7 }),
  stock("605004", 1, [tag("alerts")], { today_alert_count: -1 }),
];
const loadedEvents = [
  { instrument_id: "605001.SH", trade_date: tradeDate },
  { instrument_id: "605001.sh", trade_date: tradeDate },
  { instrument_id: "605002.SH", trade_date: tradeDate },
  { instrument_id: "605004.SH", trade_date: topicDate },
];
const fallback = buildMonitorHeat(oldPayload, loadedEvents, options());
assert.equal(fallback.alert_scope, "loaded_events");
assert.equal(fallback.groups[0].alert_stock_count, 2);
assert.equal(
  buildMonitorHeat(oldPayload, loadedEvents, options({ tradeDate: undefined }))
    .groups[0].alert_stock_count,
  1,
  "without a trade day only the explicit exact count is known",
);

// Duplicate source conflicts cannot choose a value based on first appearance.
const conflict = stock("606001", 3, [tag("conflict")]);
const conflicted = [conflict, { ...conflict, change_pct: 9 }];
for (const rows of [conflicted, [...conflicted].reverse()]) {
  const value = buildMonitorHeat(rows, [], options()).allGroups[0];
  assert.equal(value.stock_count, 1);
  assert.equal(value.valid_count, 0);
  assert.equal(value.median_pct, null);
  assert.equal(value.stocks[0].heat_change_pct, null);
}
assert.equal(
  buildMonitorHeat([conflict, { ...conflict, change_pct: null }], [], options())
    .allGroups[0].valid_count,
  0,
);

// Ties follow breadth, coverage and a stable ID rather than response order.
const tied = [
  ...[0, 2, 4].map((value, index) =>
    stock(`60700${index}`, value, [tag("wide")]),
  ),
  ...[2, 2].map((value, index) => stock(`60701${index}`, value, [tag("two")])),
  ...[2, 2, 2].map((value, index) =>
    stock(`60702${index}`, value, [tag("three-b"), tag("three-a")]),
  ),
  stock("607030", null, [tag("unknown-last")]),
];
for (const rows of [tied, [...tied].reverse()]) {
  const ranked = buildMonitorHeat(rows, [], options());
  assert.deepEqual(
    ranked.allGroups.map((group) => group.topic_id),
    ["three-a", "three-b", "two", "wide", "unknown-last"],
  );
  assert.deepEqual(
    ranked.groups[0].leaders.map((row) => row.instrument_id),
    ["607020.SH", "607021.SH", "607022.SH"],
  );
}
const many = [6, 3, 0, -1, null].map((value, index) =>
  stock(`60800${index}`, value, [tag("leaders")]),
);
assert.equal(buildMonitorHeat(many, [], options()).groups[0].leaders.length, 3);
assert.deepEqual(
  buildMonitorHeat(many, [], options()).groups[0].stocks.map(
    (row) => row.heat_change_pct,
  ),
  [6, 3, 0, -1, null],
);
assert.throws(
  () => buildMonitorHeat(stocks, [], options({ minSamples: 2 })),
  RangeError,
);
assert.throws(
  () => buildMonitorHeat(stocks, [], options({ period: "5d" })),
  RangeError,
);
assert.throws(
  () => buildMonitorHeat(stocks, [], options({ kind: "fund" })),
  RangeError,
);
assert.deepEqual(
  { stocks, events },
  before,
  "pure heat aggregation does not mutate source quotes, memberships or events",
);
console.log(
  "monitor heat: dated memberships and metrics, stock/topic deduplication, known zero and unknown coverage, percent units, exact-alert preference, stable ranking and minimum samples passed",
);
