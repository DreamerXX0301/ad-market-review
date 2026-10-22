import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const helper = ts.transpileModule(
  await fs.readFile(new URL("./equityCurve.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const helperUrl =
  "data:text/javascript;base64," + Buffer.from(helper).toString("base64");
let chartSource = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
chartSource = chartSource.slice(
  chartSource.indexOf("export function EquityChart("),
);
const prelude = `import React, {useState,useEffect,useRef} from ${JSON.stringify(import.meta.resolve("react"))};
import {equityCurvePoints,equityCurveTooltip,validInitialCapital,curvePercent,curveAxisAmount} from ${JSON.stringify(helperUrl)};
let lastOption; export function options(){return lastOption;}
const chartPalette=()=>({tooltip:"#192332",line:"#31425c",text:"#dde7fa"});
const echarts={graphic:{LinearGradient:class{constructor(...args){}}}};
function EChart({option}){lastOption=option;return React.createElement("div",{"data-chart":"equity"});}
function Empty({title,description}){return React.createElement("p",null,title,description);}
`;
let chartCompiled = ts.transpileModule(prelude + chartSource, {
  fileName: "equity.tsx",
  compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX,
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
chartCompiled = chartCompiled.replaceAll(
  '"react/jsx-runtime"',
  JSON.stringify(import.meta.resolve("react/jsx-runtime")),
);
const { EquityChart, options } = await import(
  "data:text/javascript;base64," + Buffer.from(chartCompiled).toString("base64")
);
const props = {
  rows: [
    { date: "2026-01-01", equity: 1000, drawdown: 0 },
    { date: "2026-01-02", equity: null, drawdown: null },
    { date: "2026-01-03", equity: 1100, drawdown: 2 },
  ],
  initialCapital: 1000,
};
const html = renderToStaticMarkup(createElement(EquityChart, props));
assert.match(html, /aria-label="回测曲线显示方式"/);
assert.match(html, /aria-pressed="true">累计收益率/);
assert.deepEqual(options().series[0].data, [0, null, 10.000000000000009]);
assert.equal(options().series[0].connectNulls, false);
assert.equal(options().series[0].markLine.data[0].yAxis, 0);
assert.equal(options().tooltip.backgroundColor, "#192332");
assert.deepEqual(options().series[1].data, [-0, null, -2]);
assert.equal(
  options().dataZoom[0].xAxisIndex.length,
  2,
  "both chart panes zoom together",
);
assert.equal(options().dataZoom[1].id, "equity-slider");
const invalid = renderToStaticMarkup(
  createElement(EquityChart, { ...props, initialCapital: null }),
);
assert.match(invalid, /disabled=""[^>]*>累计收益率/);
assert.match(invalid, /仅展示账户权益/);
assert.equal(options().series[0].name, "账户权益");
assert.equal(options().series[0].markLine, undefined);
assert.match(
  renderToStaticMarkup(
    createElement(EquityChart, { rows: [], initialCapital: 1000 }),
  ),
  /暂无权益曲线/,
);
let confirm = await fs.readFile(
  new URL("./RecycleConfirm.tsx", import.meta.url),
  "utf8",
);
confirm = confirm
  .replace(
    /import[^;]+from "\.\/UI";/,
    `const Modal=({title,children})=>React.createElement("section",null,React.createElement("h2",null,title),children); const ErrorBox=({message})=>React.createElement("p",{role:"alert"},message); const Spinner=()=>null;`,
  )
  .replace('import "./RecycleConfirm.css";', "")
  .replace(
    'import type { Row } from "../lib/api";',
    `import React from ${JSON.stringify(import.meta.resolve("react"))};`,
  );
let confirmCompiled = ts.transpileModule(confirm, {
  fileName: "confirm.tsx",
  compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX,
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
confirmCompiled = confirmCompiled.replaceAll(
  '"react/jsx-runtime"',
  JSON.stringify(import.meta.resolve("react/jsx-runtime")),
);
const { default: RecycleConfirm } = await import(
  "data:text/javascript;base64," +
    Buffer.from(confirmCompiled).toString("base64")
);
const strategy = renderToStaticMarkup(
  createElement(RecycleConfirm, {
    kind: "strategy",
    item: { name: "测试策略", cascade_backtest_count: 7 },
    busy: false,
    onClose: () => assert.fail("render cannot close"),
    onConfirm: () => assert.fail("render cannot mutate"),
  }),
);
assert.match(strategy, /7 条尚未删除的关联回测/);
assert.match(strategy, /已有跟踪记录和基准价保留，可在跟踪页独立管理/);
assert.match(strategy, /仅恢复本次连带移入/);
assert.match(strategy, /之前独立删除的回测保持原状/);
const working = renderToStaticMarkup(
  createElement(RecycleConfirm, {
    kind: "backtest",
    item: {
      strategy_name: "测试策略",
      start_date: "2021-10-20",
      end_date: "2026-10-21",
    },
    busy: true,
    onClose: () => {},
    onConfirm: () => {},
  }),
);
assert.equal(working.match(/disabled=""/g).length, 2);
assert.match(working, /2021-10-20 → 2026-10-21/);
assert.match(working, /可从回收站恢复/);
console.log(
  "backtest/recycle SSR: default returns, missing-capital fallback, zero baseline, gaps, linked zoom and recoverable cascade confirmation passed",
);
