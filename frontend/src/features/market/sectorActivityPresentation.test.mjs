import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const compile = (text, tsx = false) =>
  ts
    .transpileModule(text, {
      fileName: tsx ? "sector.tsx" : "sector.ts",
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    })
    .outputText.replaceAll(
      '"react/jsx-runtime"',
      JSON.stringify(import.meta.resolve("react/jsx-runtime")),
    );
const helper = encode(
  compile(
    await fs.readFile(
      new URL("./amountActivityPresentation.ts", import.meta.url),
      "utf8",
    ),
  ),
);
const source = (
  await fs.readFile(new URL("./SectorActivity.tsx", import.meta.url), "utf8")
)
  .replace('"./amountActivityPresentation"', JSON.stringify(helper))
  .replace('import "./AmountActivity.css";', "");
const { SectorActivityCell, SectorActivityCoverage } = await import(
  encode(compile(source, true))
);
const base = {
  count: 3,
  state: "complete",
  complete: true,
  expected_days: 7,
  observed_days: 7,
  ranks: [{ date: "2026-10-22", rank: 2, value: 8.3 }],
  window_start: "2026-10-14",
  window_end: "2026-10-22",
};
let html = renderToStaticMarkup(
  createElement(SectorActivityCell, {
    activity: { change_pct: base, amount: { ...base, count: 1 } },
  }),
);
assert.ok(html.includes("上涨前10") && html.includes("成交额前10"));
assert.ok(
  html.includes("3次") && html.includes("1次") && html.includes("持续活跃"),
);
assert.ok(html.includes("第2名") && !html.includes("<details open"));
assert.ok(
  !html.includes("净流入"),
  "price ranking appearances never become fund-flow appearances",
);
html = renderToStaticMarkup(
  createElement(SectorActivityCell, {
    activity: {
      change_pct: {
        ...base,
        count: 2,
        state: "observed_partial",
        complete: false,
        observed_days: 3,
      },
    },
  }),
);
assert.ok(
  html.includes("已观察2次") &&
    html.includes("已观察3/7日") &&
    html.includes("待核验"),
);
html = renderToStaticMarkup(createElement(SectorActivityCell, {}));
assert.ok(html.includes("待核验") && !html.includes("0次"));
const summary = {
  expected_days: 7,
  groups: [
    {
      kind: "industry",
      level: 1,
      metric: "change_pct",
      observed_days: 7,
      complete: true,
    },
    {
      kind: "industry",
      level: 1,
      metric: "amount",
      observed_days: 2,
      complete: false,
      reason: "历史日期未留存",
    },
    {
      kind: "industry",
      level: 2,
      metric: "amount",
      observed_days: 6,
      complete: false,
    },
    {
      kind: "concept",
      level: null,
      metric: "amount",
      observed_days: 5,
      complete: false,
    },
  ],
};
html = renderToStaticMarkup(
  createElement(SectorActivityCoverage, {
    summary,
    kind: "industry",
    level: "1",
  }),
);
assert.ok(html.includes("核验范围7/7日") && html.includes("已观察2/7日"));
assert.ok(
  !html.includes("已观察6/7日") && !html.includes("已观察5/7日"),
  "industry layers and concepts have separate coverage",
);
assert.ok(html.includes("样本不完整") && html.includes("不随下方排序改变"));
assert.ok(!/[≥]|至少|下界/.test(html));
assert.ok(html.includes("归档成员样本不混入"));
assert.ok(html.includes("历史日期未留存"));
const market = await fs.readFile(
  new URL("./Market.tsx", import.meta.url),
  "utf8",
);
assert.ok(
  market.includes('.concat(isQuoteBoard ? ["sector_activity_7d"] : [])'),
  "fund-flow rows do not silently inherit index ranking counts",
);
assert.ok(market.includes('defaultSort={{ key: "amount", order: "desc" }}'));
console.log(
  "sector seven-day rise/amount rankings: separate labels/layers, observed partial samples, folded dates and unchanged live sorting passed",
);
