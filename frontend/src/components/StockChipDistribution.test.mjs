import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const compiled = ts
  .transpileModule(
    await fs.readFile(new URL("./StockChipDistribution.tsx", import.meta.url), "utf8"),
    {
      fileName: "StockChipDistribution.tsx",
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  )
  .outputText.replace(/from ["'](react(?:\/jsx-runtime)?)["']/g, (_, specifier) =>
    "from " + JSON.stringify(import.meta.resolve(specifier)),
  )
  .replace(/import ["']\.\/StockChipDistribution.css["'];/, "");
const { default: StockChipDistribution, parseStockChipCsv } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const date = "2026-10-20";
const symbol = "600519.SH";
const parse = (text) => parseStockChipCsv(text, symbol, date);
const header = "ts_code,trade_date,price,percent\n";
const sample = [
  "600519.SH,20261020,1000,25",
  '"600519.SH","2026-10-20","1001.25","40.5"',
  "600519.SH,20261021,2000,100",
  "000001.SZ,20261020,3000,100",
].join("\r\n");
assert.deepEqual(parse("\uFEFF" + header + sample), [
  { price: 1001.25, percent: 40.5 },
  { price: 1000, percent: 25 },
]);
assert.deepEqual(parse(",percent,price,trade_date,ts_code\n0,100,20,20261020,600519.SH"), [
  { price: 20, percent: 100 },
]);
assert.throws(() => parse(header + "600519.SH,20261021,2000,100"), /找不到/);
assert.throws(() => parse(header + "600519.SZ,20261020,2000,100"), /找不到/);
assert.throws(() => parse("ts_code,trade_date,price,percent,percent\n" + sample), /四列/);
assert.throws(() => parse(header + "600519.SH,20261020,20"), /完整/);
assert.throws(() => parse(header + "600519.SH,20261020,20,101"), /无效/);
assert.throws(() => parse(header + "600519.SH,20261020,20,-1"), /无效/);
assert.throws(() => parse(header + "600519.SH,20261020,0,80"), /无效/);
assert.throws(() => parse(header + "600519.SH,20261020,=1+2,80"), /无效/);
assert.throws(() => parse(header + "600519.SH,20261020,20,0"), /合计/);
assert.throws(() => parse(header + "600519.SH,20261020,20,80\n600519.SH,20261020,21,30"), /合计/);
assert.throws(() => parse(header + "600519.SH,20261020,20,50\n600519.SH,20261020,20.00,50"), /重复/);
assert.throws(() => parseStockChipCsv(header + sample, symbol, "2026-02-31"), /行情日/);
assert.throws(() => parse(header + Array(6001).fill("600519.SH,20261020,1,0").join("\n")), /6000/);
assert.throws(
  () => parse(header + Array.from({ length: 501 }, (_, i) => `600519.SH,20261020,${i + 1},0.1`).join("\n")),
  /500 档/,
);
const html = renderToStaticMarkup(createElement(StockChipDistribution, { instrumentId: symbol, date }));
assert.match(html, /筹码分布 · 本地 CSV/);
assert.match(html, /选择已获授权的 UTF-8 CSV/);
assert.match(html, /独立于 K 线复权坐标/);
assert.doesNotMatch(html, /<ul/);
