/** Real pattern helpers and plot branch; synthetic facts never call transport. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./CandleLimitEvents.tsx", import.meta.url),
  "utf8",
);
const transpile = (text, jsx = false) =>
  ts.transpileModule(text, {
    fileName: jsx ? "CandleLimitEvents.tsx" : "marker.ts",
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
const compiled = transpile(source, true).replace(
  /from ["']react\/jsx-runtime["']/g,
  "from " + JSON.stringify(import.meta.resolve("react/jsx-runtime")),
);
const helpers = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const {
  PATTERN_BASIS,
  confirmedBarPatterns,
  confirmedBarLimits,
  describeBarPatterns,
  PatternStatus,
  barLimitMarker,
  limitTooltipHtml,
} = helpers;
const day = "2026-10-23";
const limit = (kind, date = day) => ({
  date,
  kind,
  basis: "verified-source-close-pool",
  source: "fuyao",
});
const event = (kind, date = day, extra = {}) => ({
  date,
  bar_date: day,
  kind,
  basis: PATTERN_BASIS,
  raw_price_basis: "none",
  raw_ohlc: { open: 10, high: 10, low: 10, close: 10 },
  raw_volume: 1000,
  sources: ["fuyao"],
  evidence: { calculation_version: PATTERN_BASIS },
  ...extra,
});
const up = {
  date: day,
  high: 8,
  low: 8,
  close: 8,
  limit_events: [limit("limit_up")],
  pattern_events: [event("one_price_limit_up")],
};
const down = {
  ...up,
  limit_events: [limit("limit_down")],
  pattern_events: [event("one_price_limit_down")],
};
const earth = {
  ...up,
  pattern_events: [
    event("earth_to_sky", day, {
      raw_ohlc: { open: 9.1, high: 11, low: 9, close: 11 },
      raw_low: 9,
      raw_close: 11,
      evidence: {
        calculation_version: PATTERN_BASIS,
        limit_band: {
          basis:
            "derived-symmetric-band-from-verified-close-and-previous-session-v1",
          limit_down_price: 9,
          limit_up_price: 11,
          previous_trade_date: "2026-10-22",
          previous_raw_close: 10,
        },
      },
    }),
  ],
};

for (const [bar, text] of [
  [up, "一字涨停"],
  [down, "一字跌停"],
  [earth, "地天板"],
]) {
  const before = JSON.stringify(bar);
  assert.equal(confirmedBarPatterns(bar).length, 1);
  assert.equal(describeBarPatterns(bar, "D")[0].text, text);
  assert.match(
    renderToStaticMarkup(React.createElement(PatternStatus, { bar })),
    new RegExp(text),
  );
  assert.equal(JSON.stringify(bar), before, "rendering cannot modify facts");
}
assert.equal(
  confirmedBarLimits(up).length,
  1,
  "old limits coexist with shape facts",
);
assert.equal(barLimitMarker(up, "limit_up").text, "一字涨");
assert.equal(barLimitMarker(down, "limit_down").text, "一字跌");
assert.equal(barLimitMarker(earth, "limit_up").text, "地天");
assert.match(barLimitMarker(earth, "limit_up").name, /地天板.*收盘涨停/);
assert.equal(barLimitMarker(up, "limit_down"), null);
assert.equal(
  barLimitMarker({ ...up, pattern_events: [] }, "limit_up").text,
  "涨",
);
assert.equal(
  barLimitMarker({ ...down, pattern_events: [] }, "limit_down").text,
  "跌",
);

const malformed = [
  { basis: "guessed-from-change-pct" },
  { raw_price_basis: "forward" },
  { kind: "unknown-pattern" },
  { date: "2026-02-30" },
  { date: "2026-10-24" },
  { bar_date: "2026-10-22" },
  { raw_ohlc: { open: 10, high: 10, low: 10, close: null } },
  { raw_volume: 0 },
  { raw_volume: Number.NaN },
];
for (const extra of malformed)
  assert.deepEqual(
    confirmedBarPatterns({
      ...up,
      pattern_events: [event("one_price_limit_up", day, extra)],
    }),
    [],
    JSON.stringify(extra),
  );
for (const legacy of [
  undefined,
  {},
  { close: 10, high: 10, low: 10 },
  { pattern_events: null },
  { pattern_events: {} },
]) {
  assert.deepEqual(confirmedBarPatterns(legacy), []);
  assert.deepEqual(describeBarPatterns(legacy, "D", true), []);
  assert.equal(
    renderToStaticMarkup(React.createElement(PatternStatus, { bar: legacy })),
    "",
  );
}
const notMatched = {
  pattern_status: {
    one_price_limit_up: {
      state: false,
      status: "not_matched",
      reason: "无匹配",
    },
    earth_to_sky: { state: null, status: "unknown", reason: "跌停价依据缺失" },
  },
};
assert.deepEqual(describeBarPatterns(notMatched, "D"), []);
const unknown = describeBarPatterns(notMatched, "D", true);
assert.equal(unknown.length, 1);
assert.match(unknown[0].detail, /跌停价依据缺失/);
assert.doesNotMatch(unknown[0].text, /不是|非一字|没有/);
assert.equal(
  renderToStaticMarkup(React.createElement(PatternStatus, { bar: notMatched })),
  "",
);

for (const period of ["W", "M"]) {
  const bar = {
    date: day,
    pattern_events: [
      event("one_price_limit_up", "2026-10-21"),
      event("one_price_limit_up", "2026-10-22"),
      event("one_price_limit_up", "2026-10-22"),
    ],
    pattern_status: {
      one_price_limit_up: { state: null, status: "daily_summary" },
    },
    pattern_summary: { one_price_limit_up: { count: 2, unknown_days: 1 } },
  };
  const descriptions = describeBarPatterns(bar, period, true);
  assert.equal(
    confirmedBarPatterns(bar).length,
    2,
    "duplicate day/kind is counted once",
  );
  assert.equal(
    descriptions[0].text,
    `${period === "W" ? "周" : "月"}内2日一字涨停`,
  );
  assert.match(descriptions[0].detail, /2026-10-21.*2026-10-22/);
  assert.match(descriptions[0].detail, /不将整根.*K称为一字涨停/);
  assert.equal(
    descriptions[1].text,
    `${period === "W" ? "周" : "月"}内日形态仍有待核验`,
  );
}
const colors = { up: "#f00", down: "#0f0", text: "#aaa" };
const tip = limitTooltipHtml(earth, "D", colors);
assert.match(tip, /收盘涨停/);
assert.match(tip, /地天板/);
assert.match(tip, /原始最低 9/);
assert.match(tip, /原始收盘 11/);
assert.match(tip, /判定依据/);
assert.match(tip, /派生跌停参考价 9.*涨停参考价 11/);
assert.match(tip, /非交易所直接字段/);
assert.doesNotMatch(
  tip,
  /原始收盘 8/,
  "adjusted display price cannot become proof",
);
const unsafe = {
  ...up,
  pattern_events: [
    event("one_price_limit_up", day, { sources: ['<img src=x onerror="x">'] }),
  ],
  pattern_status: {
    earth_to_sky: {
      state: null,
      status: "unknown",
      reason: "<script>x</script>",
    },
  },
};
const safeHtml = limitTooltipHtml(unsafe, "D", colors);
assert.doesNotMatch(safeHtml, /<img|<script>/);
assert.match(safeHtml, /&lt;img/);
assert.match(safeHtml, /&lt;script&gt;/);

// Exercise the real plot branch, proving one marker per closing direction and
// that the saved limit-marker switch controls all new labels as well.
const chartSource = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Charts.tsx",
  chartSource,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const branches = [];
function walk(node) {
  if (
    ts.isIfStatement(node) &&
    node.expression.getText(ast).replace(/\s+/g, " ") ===
      'supportsLimits && showLimits && period === "D"'
  )
    branches.push(node);
  ts.forEachChild(node, walk);
}
walk(ast);
assert.equal(branches.length, 1);
const plot = new Function(
  "series",
  "bars",
  "pal",
  "supportsLimits",
  "showLimits",
  "period",
  "barLimitMarker",
  transpile(branches[0].getText(ast)),
);
const bars = [
  earth,
  {
    ...down,
    date: "2026-10-22",
    pattern_events: [
      event("one_price_limit_down", "2026-10-22", { bar_date: "2026-10-22" }),
    ],
  },
];
const series = [];
plot(series, bars, colors, true, true, "D", barLimitMarker);
assert.equal(series.length, 2);
assert.equal(series[0].data.length, 1);
assert.equal(series[0].data[0].label.formatter, "地天");
assert.equal(series[1].data.length, 1);
assert.equal(series[1].data[0].label.formatter, "一字跌");
for (const [supported, shown, period] of [
  [true, false, "D"],
  [false, true, "D"],
  [true, true, "W"],
  [true, true, "M"],
]) {
  const hidden = [];
  plot(hidden, bars, colors, supported, shown, period, barLimitMarker);
  assert.deepEqual(
    hidden,
    [],
    "hidden, index, frozen or aggregated views cannot create daily plot tags",
  );
}
console.log(
  "verified daily pattern badges/tooltip/merged markers, raw price proof, weekly/monthly day counts, unknown/legacy compatibility and shared switch passed",
);
