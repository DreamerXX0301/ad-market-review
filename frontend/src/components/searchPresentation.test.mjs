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
const { instrumentSearchRequest, searchResultView, globalSectorSelection } =
  await import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );
assert.equal(
  instrumentSearchRequest("出版"),
  "/instruments?query=%E5%87%BA%E7%89%88&limit=12",
  "stock-only controls retain original endpoint",
);
assert.equal(
  instrumentSearchRequest(" 出版 ", true),
  "/search?query=%E5%87%BA%E7%89%88&limit=12",
);
assert.equal(instrumentSearchRequest("  ", true), null);
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
console.log(
  "global search: type separation, code identity and independent date scopes passed",
);
