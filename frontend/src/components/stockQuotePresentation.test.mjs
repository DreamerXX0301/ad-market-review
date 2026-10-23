import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import React from "react";
import { jsx, jsxs } from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const transpile = (text, fileName = "part.tsx") =>
  ts.transpileModule(text, {
    fileName,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const module = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const { stockHeaderQuote } = await import(
  module(
    transpile(
      await fs.readFile(
        new URL("./stockQuotePresentation.ts", import.meta.url),
        "utf8",
      ),
    ),
  )
);
const id = "601900.SH",
  asOf = "2026-10-23";
const detail = {
  instrument: { instrument_id: id, name: "南方传媒", kind: "stock" },
  data_date: asOf,
  date: asOf,
  close: 12.87,
  change_pct: -1.6055,
  price_basis: "none",
  price_continuity: true,
  corporate_action: false,
};
assert.equal(stockHeaderQuote(detail, id, asOf).price, 12.87);
assert.equal(stockHeaderQuote(detail, id, asOf).change, -1.6055);
assert.match(stockHeaderQuote(detail, id, asOf).label, /2026-10-23 收盘/);
const previous = stockHeaderQuote({ ...detail, date: "2026-10-22" }, id, asOf);
assert.equal(previous.price, detail.close);
assert.equal(previous.stale, true);
assert.match(previous.label, /截至 2026-10-23 最近收盘.*报价 2026-10-22/);
for (const invalid of [
  null,
  { ...detail, close: null },
  { ...detail, close: 0 },
  { ...detail, close: "12.87" },
  { ...detail, close: Infinity },
  { ...detail, date: "2026-10-24" },
  { ...detail, date: "2026-02-30" },
  { ...detail, data_date: "2026-10-22" },
  { ...detail, price_basis: "forward_event_affine" },
  { ...detail, instrument: { instrument_id: "600519.SH" } },
]) {
  const result = stockHeaderQuote(invalid, id, asOf);
  assert.equal(result.price, null);
  assert.equal(result.change, null);
}
for (const invalidChange of [
  { change_pct: null },
  { change_pct: 0, corporate_action: true },
  { change_pct: 5, price_continuity: false },
]) {
  const result = stockHeaderQuote({ ...detail, ...invalidChange }, id, asOf);
  assert.equal(result.price, detail.close);
  assert.equal(result.change, null);
}
assert.equal(
  stockHeaderQuote({ ...detail, change_pct: 0 }, id, asOf).change,
  0,
);

function declaration(source, name) {
  const ast = ts.createSourceFile(
    "part.tsx",
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const node = ast.statements.find(
    (node) => ts.isFunctionDeclaration(node) && node.name?.text === name,
  );
  return transpile(node.getText(ast).replace(/^export (default )?/, ""))
    .replace(/^import .+;\n/gm, "")
    .replace(/export \{\};?\n?/g, "");
}
const quoteComponent = declaration(
  await fs.readFile(new URL("./StockHeaderQuote.tsx", import.meta.url), "utf8"),
  "StockHeaderQuote",
);
const num = (value) => (value == null ? "—" : Number(value).toFixed(2));
const percent = (value) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${num(value)}%`;
const tone = (value) => (value > 0 ? "up" : value < 0 ? "down" : "muted");
const StockHeaderQuote = new Function(
  "_jsx",
  "_jsxs",
  "stockHeaderQuote",
  "num",
  "percent",
  "tone",
  quoteComponent + ";return StockHeaderQuote;",
)(jsx, jsxs, stockHeaderQuote, num, percent, tone);
const chartSource = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
const drawerCode = declaration(chartSource, "StockDrawer");
let loaded = null;
const requests = [];
const scopeValues = {
  React,
  _jsx: jsx,
  _jsxs: jsxs,
  ...React,
  useState: () => [loaded, () => {}],
  useRef: (current) => ({ current }),
  useEffect: () => {},
  useApi: (path) => {
    requests.push(path);
    return { data: detail };
  },
  stockPayloadMatches: (payload, symbol, observed) =>
    payload?.instrument?.instrument_id === symbol &&
    (payload.as_of || payload.data_date) === observed,
  stockNavigationPosition: () => null,
  useStockEventBaseline: () => ({}),
  StockHeaderQuote,
  num,
  percent,
};
const scope = new Proxy(scopeValues, {
  has: () => true,
  get: (target, key) => {
    if (key === Symbol.unscopables) return undefined;
    if (key in target) return target[key];
    if (key in globalThis) return globalThis[key];
    return () => null;
  },
});
const Drawer = new Function(
  "scope",
  `with(scope){${drawerCode};return StockDrawer;}`,
)(scope);
for (const period of ["D", "W", "M"]) {
  for (const price_basis of ["none", "forward_event_affine"]) {
    loaded = {
      id,
      asOf,
      data: {
        instrument: detail.instrument,
        as_of: asOf,
        period,
        price_basis,
        bars: [{ date: "2020-01-01", close: 9999, change_pct: 888 }],
      },
    };
    const html = renderToStaticMarkup(
      jsx(Drawer, { id, asOf, onClose: () => {} }),
    );
    assert.match(html, /stock-title-row/);
    assert.match(html, /12\.87/);
    assert.match(html, /-1\.61%/);
    assert.match(html, /2026-10-23 收盘.*未复权.*非实时/);
    assert.ok(
      !html.includes("9999") && !html.includes("888%"),
      "hover/aggregate chart prices never replace header quote",
    );
  }
}
assert.deepEqual(
  [...new Set(requests)],
  [`/instruments/${id}?as_of=${asOf}`],
  "header uses existing daily detail request only",
);
console.log(
  "stock header: fixed real daily detail, dated older quote, unknown guards, D/W/M and raw/forward chart independence passed",
);
