import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./ladderPresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  ladderTopics,
  groupLadderRows,
  filterLadderRows,
  ladderSearch,
  ladderClassificationCount,
  themeCountText,
  ladderPatternTags,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

const full = Array.from({ length: 137 }, (_, index) => ({
  instrument_id: `${index}.SH`,
  board_count: (index % 8) + 1,
  name: `股票${index}`,
}));
const original = structuredClone(full);
const grouped = groupLadderRows(full);
assert.deepEqual(
  grouped.map((group) => group.height),
  [8, 7, 6, 5, 4, 3, 2, 1],
);
assert.equal(
  grouped.flatMap((group) => group.items).length,
  137,
  "all returned stocks remain represented, including beyond one hundred",
);
assert.deepEqual(full, original, "grouping does not mutate source rows");
const incomplete = groupLadderRows([
  { board_count: null },
  { board_count: 0 },
  { board_count: 1.5 },
  { board_count: "1" },
  { board_count: 1 },
  { board_count: 3 },
]);
assert.deepEqual(
  incomplete.map((group) => group.height),
  [3, 1, null],
);
assert.equal(
  incomplete.at(-1).items.length,
  4,
  "unknown height must never become first board",
);

const topics = ladderTopics({
  topics: [
    { topic_id: "a", name: "共同题材" },
    { topic_id: "b", name: "共同题材" },
    { topic_id: "a", name: "共同题材" },
    "题材三",
    "题材四",
    "题材五",
    "题材六",
    "  ",
    null,
  ],
});
assert.equal(
  topics.length,
  6,
  "all distinct topics remain visible; equal names with distinct IDs do not collapse",
);
const rows = [
  {
    instrument_id: "600001.SH",
    name: "甲",
    topics: [{ topic_id: "a", name: "共同题材" }],
  },
  {
    instrument_id: "600002.SH",
    name: "乙",
    topics: [{ topic_id: "b", name: "共同题材" }],
  },
  { instrument_id: "600003.SH", name: "丙", topics: ["其他"] },
];
assert.deepEqual(
  filterLadderRows(rows, "", topics[0]).map((row) => row.name),
  ["甲"],
);
assert.deepEqual(
  filterLadderRows(rows, "", { key: "name:共同题材", name: "共同题材" }).map(
    (row) => row.name,
  ),
  ["甲", "乙"],
);
assert.deepEqual(
  filterLadderRows(rows, "600002", null).map((row) => row.name),
  ["乙"],
);
assert.equal(
  filterLadderRows(rows, "乙", topics[0]).length,
  0,
  "topic and text filters compose over the full list",
);
assert.equal(filterLadderRows(full, "", null).length, 137);

const searchNames = {
  东方新能: ["dongfangxinneng", "dfxn"],
  东方系: ["dongfangxi", "dfx"],
  绿色能源: ["lvsenengyuan", "lsny"],
};
const datedRows = [
  {
    name: "东方新能",
    instrument_id: "000958.SZ",
    source_industry: "绿色能源",
    topics: [{ topic_id: "oriental", name: "东方系" }],
    reason: "签下算力大单",
  },
  { name: "东方电气", instrument_id: "600875.SH" },
];
for (const query of [
  "方新",
  "ｄｆｘｎ",
  "dong-fang xin neng",
  "dongfangxingneng",
  "东方心能",
  "东新方能",
  "０ ０ ０ ９ ５ ８．ｓｚ",
  "「东方系」",
  "算力 + 大单",
  "lǜ sè néng yuán",
])
  assert.deepEqual(
    filterLadderRows(datedRows, query, null, searchNames).map(
      (row) => row.instrument_id,
    ),
    ["000958.SZ"],
    query,
  );
assert.equal(
  filterLadderRows(datedRows, "000959", null, searchNames).length,
  0,
);
assert.equal(ladderSearch("「 」").term, "");
assert.equal(ladderSearch("算立大单").contains("算力大单"), false);
assert.equal(ladderSearch("东方", searchNames).label("东方电气"), true);

assert.equal(ladderClassificationCount([], null), null);
assert.equal(
  ladderClassificationCount([], {
    examined_count: 0,
    confirmed_count: 0,
    unknown_count: 0,
  }),
  null,
  "no candidates does not prove a zero",
);
assert.equal(
  ladderClassificationCount([], {
    examined_count: 5,
    confirmed_count: 0,
    unknown_count: 2,
  }),
  null,
);
assert.equal(
  ladderClassificationCount([], {
    examined_count: 5,
    confirmed_count: 0,
    unknown_count: 0,
    complete: true,
  }),
  0,
);
assert.equal(
  ladderClassificationCount([], {
    examined_count: 5,
    confirmed_count: 0,
    unknown_count: 0,
    complete: false,
    source_scope_complete: false,
  }),
  null,
  "incomplete candidate-source scope does not prove a classification zero",
);
assert.equal(
  ladderClassificationCount(rows, { examined_count: 8, unknown_count: 5 }),
  3,
  "positive samples stay visible under partial coverage",
);
assert.equal(themeCountText({ limit_up_count: 0 }), "0只");
assert.equal(
  themeCountText({ limit_up_count: null, limit_up_observed_count: 4 }),
  "已确认≥4只",
);
assert.equal(
  themeCountText({ limit_up_count: null, limit_up_observed_count: 0 }),
  "数量待核验",
);
assert.equal(themeCountText({}), "数量待核验");
const observed = "2026-10-21";
const pattern = {
  kind: "earth_to_sky",
  date: observed,
  bar_date: observed,
  basis: "verified-source-close-pool-and-raw-ohlc-v1",
  snapshot_id: "synthetic-generation",
  raw_price_basis: "none",
  raw_volume: 100,
  raw_ohlc: { open: 10, high: 11, low: 9, close: 11 },
  source: "synthetic-source",
};
const marked = {
  date: observed,
  pattern_date: observed,
  pattern_as_of: observed,
  snapshot_id: "synthetic-generation",
  pattern_events: [pattern, structuredClone(pattern)],
  pattern_status: { earth_to_sky: { state: true, status: "confirmed" } },
};
assert.deepEqual(
  ladderPatternTags(marked, observed, "synthetic-generation").map(
    (item) => item.label,
  ),
  ["地天板"],
  "only backend-confirmed same-day shape is rendered and duplicates collapse",
);
for (const [key, value] of [
  ["date", "2026-10-20"],
  ["pattern_date", "2026-10-20"],
  ["pattern_as_of", "2026-10-22"],
  ["snapshot_id", "older-generation"],
  ["pattern_status", {}],
  ["pattern_events", null],
]) {
  assert.deepEqual(
    ladderPatternTags(
      { ...marked, [key]: value },
      observed,
      "synthetic-generation",
    ),
    [],
  );
}
for (const [key, value] of [
  ["kind", "constructor"],
  ["basis", "price-change-cutoff"],
  ["date", "2026-10-20"],
  ["bar_date", "2026-10-22"],
  ["snapshot_id", "older-generation"],
  ["raw_volume", 0],
  ["raw_price_basis", "forward"],
  ["raw_ohlc", { open: 10, high: 11, low: null, close: 11 }],
]) {
  assert.deepEqual(
    ladderPatternTags(
      { ...marked, pattern_events: [{ ...pattern, [key]: value }] },
      observed,
      "synthetic-generation",
    ),
    [],
  );
}
assert.deepEqual(
  ladderPatternTags(marked, "2026-10-22", "synthetic-generation"),
  [],
);
assert.deepEqual(ladderPatternTags(marked, observed, null), []);
assert.deepEqual(
  ladderPatternTags(
    { ...marked, pattern_events: [] },
    observed,
    "synthetic-generation",
  ),
  [],
);
assert.deepEqual(
  ladderPatternTags(
    { date: observed, change_pct: 10, open: 11, close: 11 },
    observed,
    "synthetic-generation",
  ),
  [],
  "display OHLC/10% return never invents a badge",
);
for (const kind of ["one_price_limit_up", "one_price_limit_down"]) {
  const row = {
    ...marked,
    pattern_events: [{ ...pattern, kind }],
    pattern_status: { [kind]: { state: true, status: "confirmed" } },
  };
  assert.equal(
    ladderPatternTags(row, observed, "synthetic-generation")[0].label,
    kind === "one_price_limit_up" ? "一字涨停" : "一字跌停",
  );
}
console.log("ladder presentation cases passed");
