import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { renderToStaticMarkup } from "react-dom/server";
import { jsx } from "react/jsx-runtime";
const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const compile = async (path) =>
  ts.transpileModule(
    await fs.readFile(new URL(path, import.meta.url), "utf8"),
    {
      fileName: path,
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
const metrics = encode(await compile("./stockExtraMetrics.ts"));
const quote = encode(await compile("./stockQuotePresentation.ts"));
const visibility = encode(await compile("../lib/indicatorVisibility.ts"));
const format = encode(
  (await compile("../lib/format.ts")).replaceAll(
    'from "./indicatorVisibility"',
    `from ${JSON.stringify(visibility)}`,
  ),
);
const source = (await compile("./StockCompactMetrics.tsx"))
  .replaceAll(
    'from "react/jsx-runtime"',
    `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
  )
  .replaceAll('from "./stockExtraMetrics"', `from ${JSON.stringify(metrics)}`)
  .replaceAll(
    'from "./stockQuotePresentation"',
    `from ${JSON.stringify(quote)}`,
  )
  .replaceAll('from "../lib/format"', `from ${JSON.stringify(format)}`);
const { default: Metrics } = await import(encode(source));
const id = "002724.SZ",
  asOf = "2026-10-23";
const detail = {
  instrument: { instrument_id: id },
  data_date: asOf,
  date: asOf,
  close: 16.22,
  change_pct: 9.99,
  price_basis: "none",
  snapshot_id: "current",
};
const summary = {
  instrument_id: id,
  data_date: asOf,
  snapshot_id: "current",
  quote_available: true,
  quote_date: asOf,
  change_pct_date: asOf,
  change_pct: 9.99,
  change_30d_pct: 72.12,
  change_30d_coverage: {
    start: "2026-09-03",
    end: asOf,
    price_basis: "forward_event_affine",
  },
  last_limit_up_date: asOf,
  last_limit_up_complete: true,
  sessions_since_limit_up: 0,
  limit_up_ytd: 6,
  limit_up_ytd_coverage: {
    complete: true,
    covered_days: 193,
    expected_days: 193,
  },
};
const render = (s = summary, d = detail, extra = {}) =>
  renderToStaticMarkup(
    jsx(Metrics, { summary: s, detail: d, instrumentId: id, asOf, ...extra }),
  );
const html = render();
for (const text of [
  "今日收盘",
  "16.22 元",
  "今日涨幅",
  "+9.99%",
  "近30交易日",
  "+72.12%",
  "最近一次涨停",
  asOf,
  "年内涨停",
  "6 次",
])
  assert.ok(html.includes(text), text);
assert.equal((html.match(/class="entry-header-metric"/g) || []).length, 5);
for (const s of [
  null,
  { ...summary, instrument_id: "600519.SH" },
  { ...summary, data_date: "2026-10-22" },
  { ...summary, snapshot_id: "old" },
  { ...summary, snapshot_id: undefined },
]) {
  const text = render(s);
  assert.ok(!text.includes("+72.12%") && !text.includes("6 次"));
  assert.ok(
    text.includes("+9.99%"),
    "daily quote can reuse its independent verified detail",
  );
}
const stale = render(null, { ...detail, date: "2026-10-22", change_pct: 55 });
assert.ok(
  !stale.includes("55.00%"),
  "older daily return never masquerades as today's",
);
assert.match(stale, /今日涨幅/);
assert.ok(!stale.includes("16.22 元"));
assert.match(stale, /待核验/);
const partial = render({
  ...summary,
  limit_up_ytd: null,
  limit_up_ytd_observed: 3,
  limit_up_ytd_coverage: { complete: false },
  last_limit_up_complete: false,
});
assert.ok(partial.includes("已确认≥3次"));
assert.ok(partial.includes("最近已确认涨停"));
const index = render(null, detail, { isIndex: true });
assert.ok(index.includes("指数无涨停统计"));
assert.ok(index.includes("16.22 点"));
assert.ok(!index.includes("年内涨停"));
console.log(
  "compact fixed metrics: dated close and returns, independent daily quote, same-snapshot gate, partial bounds and index scope passed",
);
