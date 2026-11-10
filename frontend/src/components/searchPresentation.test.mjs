import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compiled = ts.transpileModule(
  await fs.readFile(
    new URL("./searchPresentation.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  instrumentSearchRequest,
  searchResultView,
  globalSectorSelection,
  loadRecentStocks,
  rememberRecentStock,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
assert.equal(
  instrumentSearchRequest("出版"),
  "/instruments?query=%E5%87%BA%E7%89%88&limit=12&fuzzy=true",
  "security-only controls retain their endpoint and enable identity matching",
);
assert.equal(
  instrumentSearchRequest(" 出版 ", true),
  "/search?query=%E5%87%BA%E7%89%88&limit=12",
);
assert.equal(instrumentSearchRequest("  ", true), null);
assert.equal(
  instrumentSearchRequest(" hyw ", false, true),
  "/search?query=hyw&limit=12&stocks_only=true",
);
assert.equal(
  instrumentSearchRequest("海洋", false, true),
  "/search?query=%E6%B5%B7%E6%B4%8B&limit=12&stocks_only=true",
);
assert.equal(
  instrumentSearchRequest("hyw"),
  "/instruments?query=hyw&limit=12&fuzzy=true",
  "个股研究在原入口开启模糊查询",
);
const stock = {
  instrument_id: "600825.SH",
  code: "600825",
  name: "新华传媒",
  kind: "stock",
  target_type: "stock",
};
const index = {
  instrument_id: "000001.SH",
  kind: "index",
  target_type: "index",
  name: "上证指数",
};
const industry = {
  instrument_id: "TI885333",
  topic_code: "TI885333",
  topic_id: "fuyao-TI885333",
  kind: "industry",
  target_type: "sector",
  name: "出版",
};
const concept = { ...industry, kind: "concept", name: "文化传媒" };
assert.equal(searchResultView(stock).sector, false);
assert.equal(searchResultView(stock).label, "股票");
assert.equal(searchResultView(index).label, "指数");
assert.equal(searchResultView(industry).sector, true);
assert.equal(searchResultView(industry).label, "行业");
assert.equal(searchResultView(concept).label, "概念");
assert.equal(
  searchResultView({ ...stock, match_type: "initials" }).matchLabel,
  "首字母",
);
const suggestion = searchResultView({
  ...stock,
  match_type: "typo",
  is_suggestion: true,
});
assert.equal(suggestion.matchLabel, "近似建议");
assert.match(suggestion.title, /^可能想找 · 股票 · 新华传媒（600825）$/);
assert.equal(
  suggestion.code,
  stock.instrument_id,
  "suggestions preserve the selectable identity",
);
assert.equal(
  searchResultView({ ...stock, match_type: "unknown-server-label" }).matchLabel,
  null,
);
assert.notEqual(searchResultView(stock).key, searchResultView(industry).key);
assert.equal(
  searchResultView({ target_type: "sector", instrument_id: "600825.SH" })
    .selectable,
  false,
  "sector missing explicit code cannot enter stock callback",
);
assert.equal(
  globalSectorSelection(stock, "2026-10-20", "2026-10-21", true),
  null,
);
assert.equal(
  globalSectorSelection(industry, "2026-10-19", "2026-10-20", true).date,
  "2026-10-19",
  "market search respects selected date",
);
assert.equal(
  globalSectorSelection(industry, "2026-10-19", "2026-10-20", false).date,
  "2026-10-20",
  "strategy/research search is independent of historical market scope",
);
assert.equal(
  globalSectorSelection(industry, "2026-10-19", undefined, false),
  null,
  "unknown business date cannot be guessed",
);
assert.equal(
  globalSectorSelection(industry, "2026-10-20", "2026-10-20", true).topic
    .topic_id,
  "fuyao-TI885333",
);
const currentUnpublished = globalSectorSelection(
  industry,
  "2026-11-10",
  "2026-11-09",
  true,
  "2026-11-10",
);
assert.equal(currentUnpublished.date, "2026-11-09");
assert.equal(currentUnpublished.pendingCloseDay, "2026-11-10");
assert.equal(currentUnpublished.topic.topic_code, industry.topic_code);
for (const selected of ["2026-11-08", "2026-11-09", "2026-11-11"]) {
  const selection = globalSectorSelection(
    industry,
    selected,
    "2026-11-09",
    true,
    "2026-11-10",
  );
  assert.equal(selection.date, selected);
  assert.equal(selection.pendingCloseDay, undefined);
}
assert.equal(
  globalSectorSelection(
    industry,
    "2026-11-10",
    "2026-11-10",
    true,
    "2026-11-10",
  ).pendingCloseDay,
  undefined,
);

const historyKey = "workbench-global-recent-stocks-v1";
const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
};
const now = Date.parse("2026-11-10T00:01:00+08:00");
const saved = (selectedAt, fields = {}) => ({
  ...stock,
  ...fields,
  selectedAt: Date.parse(selectedAt),
});
assert.deepEqual(loadRecentStocks(now), []);
for (const row of [index, industry, concept, { ...stock, instrument_id: "bad" }])
  assert.equal(rememberRecentStock(row, now), undefined);
assert.equal(storage.has(historyKey), false, "non-stock picks write nothing");
storage.set(
  historyKey,
  JSON.stringify([
    saved("2026-11-04T00:00:00+08:00", { name: "旧名" }),
    saved("2026-11-08T12:30:00+08:00", { name: "最新名称" }),
    saved("2026-11-03T23:59:59+08:00", { instrument_id: "600826.SH" }),
    saved("2026-11-04T00:00:00+08:00", { instrument_id: "600827.SH" }),
    saved("2026-11-10T00:02:00+08:00", { instrument_id: "600828.SH" }),
    saved("2026-11-09T00:00:00+08:00", { kind: "index" }),
    null,
  ]),
);
assert.deepEqual(
  loadRecentStocks(now).map((row) => [row.instrument_id, row.name]),
  [
    ["600825.SH", "最新名称"],
    ["600827.SH", "新华传媒"],
  ],
  "today and six preceding Beijing dates, deduplicated by newest pick",
);
assert.equal(
  rememberRecentStock({ ...stock, instrument_id: "600827.SH", quote: 13 }, now)[0]
    .instrument_id,
  "600827.SH",
);
assert.equal(JSON.parse(storage.get(historyKey)).length, 2);
assert.equal(JSON.parse(storage.get(historyKey))[0].code, "600827");
assert.equal(JSON.parse(storage.get(historyKey))[0].quote, undefined);

for (let index = 0; index < 16; index++) {
  const code = String(600100 + index);
  rememberRecentStock({ ...stock, instrument_id: code + ".SH" }, now + index);
}
const bounded = loadRecentStocks(now + 16);
assert.equal(bounded.length, 12);
assert.equal(bounded[0].code, "600115");
assert.equal(bounded.at(-1).code, "600104");
storage.set(historyKey, "{broken");
assert.deepEqual(loadRecentStocks(now), []);
assert.equal(storage.has(historyKey), false);
globalThis.localStorage.getItem = () => {
  throw new Error("storage is restricted");
};
globalThis.localStorage.setItem = globalThis.localStorage.getItem;
assert.deepEqual(loadRecentStocks(now), []);
assert.doesNotThrow(() => rememberRecentStock(stock, now));
delete globalThis.localStorage;
console.log(
  "global search: type separation, independent dates and recent-stock expiration passed",
);
