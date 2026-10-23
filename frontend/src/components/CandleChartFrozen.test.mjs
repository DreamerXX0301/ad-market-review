/** Render the actual CandleChart with an isolated hook/transport environment. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import React from "react";
import { jsx, jsxs } from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
async function helper(name) {
  const text = await fs.readFile(new URL(name, import.meta.url), "utf8");
  return import(
    encode(
      ts.transpileModule(text, {
        compilerOptions: {
          module: ts.ModuleKind.ES2022,
          target: ts.ScriptTarget.ES2022,
        },
      }).outputText,
    )
  );
}
const preferences = await helper("./chartPreferences.ts");
const { chartBoll, bollCandleValues } = await helper("./chartBoll.ts");
const { annotationColors, lineLabel } = await helper("./chartAnnotations.ts");
const { chartPaneLayout } = await helper("./chartPaneLayout.ts");
const { candleChartEndpoint } = await helper("./chartEndpoint.ts");
const source = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "Charts.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const candle = ast.statements.find(
  (node) => ts.isFunctionDeclaration(node) && node.name?.text === "CandleChart",
);
const compiled = ts
  .transpileModule(candle.getText(ast).replace("export function", "function"), {
    fileName: "candle.tsx",
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  .outputText.replace(/^import .+;\n/gm, "")
  .replace(/export \{\};?\n?/g, "");
const paths = [],
  annotations = [];
let period = "D",
  option,
  allowLive = false,
  annotationViews = [];
const settings = preferences.normalizeChartPreferences({
  version: 4,
  stockMas: [],
  indexMas: [],
  stockPanels: [],
  indexPanels: [],
  stockShowBoll: true,
});
const scopeValues = {
  React,
  _jsx: jsx,
  _jsxs: jsxs,
  ...React,
  useState: (initial) => {
    const value = typeof initial === "function" ? initial() : initial;
    return [value === "D" ? period : value, () => {}];
  },
  useMemo: (fn) => fn(),
  useEffect: () => {},
  useRef: (current) => ({ current }),
  ...preferences,
  chartBoll,
  bollCandleValues,
  annotationColors,
  lineLabel,
  chartPaneLayout,
  candleChartEndpoint,
  useChartPreferences: () => ({
    preferences: settings,
    persistent: true,
    update: () => {
      throw Error("render cannot update preferences");
    },
    reset: () => {},
  }),
  useApi: (path) => {
    paths.push(path);
    if (allowLive) return { data: chartData, loading: false, error: "" };
    assert.equal(path, null, "frozen chart must disable chart GET");
    return {
      data: { bars: [{ date: "2099-01-01", close: 999 }] },
      loading: true,
      error: "LIVE_ERROR",
    };
  },
  useChartAnnotations: (_id, _data, enabled) => {
    annotations.push(enabled);
    return { enabled, visible: enabled ? annotationViews : [], drawing: false };
  },
  chartPalette: () => ({
    text: "#fff",
    line: "#333",
    tooltip: "#111",
    up: "#f00",
    down: "#0f0",
    bollUpper: "#f66",
    bollMiddle: "#6ff",
    bollLower: "#6f6",
  }),
  maColors: ["#1", "#2", "#3", "#4", "#5", "#6"],
  defaultChartWindow: () => ({ start: 0, end: 100 }),
  projectEventBaseline: () => ({ price: null, reason: "" }),
  historicalChartEvents: () => [],
  projectHistoricalChartEvent: () => null,
  wavePatterns: () => [],
  waveTurningPoints: () => [],
  waveCandidates: () => [],
  selectedWavePatterns: () => [],
  wavePatternWindow: () => null,
  wavePlotSeries: () => [],
  candleChange: () => ({ value: null, reason: "", label: "日涨跌幅" }),
  chartSinceReturn: () => ({ value: null, reason: "" }),
  chartIndicatorAvailability: () => ({ missing: [], reason: "", label: "" }),
  chartDataNotes: () => [],
  ChartToggle: ({ label }) => jsx("button", { children: label }),
  confirmedBarLimits: () => [],
  num: (value) => (value == null ? "—" : String(value)),
  percent: (value) => (value == null ? "—" : String(value) + "%"),
  amount: (value) => String(value ?? "—"),
  textValue: (value) => String(value ?? "—"),
  EChart: ({ option: next }) => {
    option = next;
    return jsx("div", { "data-test-chart": true });
  },
  Empty: ({ title, description }) =>
    jsxs("div", { children: [title, description] }),
};
const scope = new Proxy(scopeValues, {
  has: () => true,
  get: (target, key) => {
    if (key === Symbol.unscopables) return undefined;
    if (key in target) return target[key];
    if (key in globalThis) return globalThis[key];
    if (typeof key === "string" && /^[A-Z]/.test(key)) return () => null;
    if (["post", "patch", "api"].includes(key))
      return () => {
        throw Error("render must not mutate or fetch");
      };
    return () => undefined;
  },
});
const Chart = new Function(
  "scope",
  `with(scope){return (${compiled.replace(/;\s*$/, "")});}`,
)(scope);
const chartData = {
  instrument: { instrument_id: "000001.SZ", kind: "stock" },
  period: "D",
  as_of: "2026-10-22",
  price_basis: "none",
  indicator_continuity: "market_sessions",
  bars: [
    {
      date: "2026-10-22",
      open: 10,
      high: 11,
      low: 9,
      close: 10,
      indicator_warmup_bars: 1,
    },
  ],
};
const html = renderToStaticMarkup(
  jsx(Chart, {
    instrumentId: "000001.SZ",
    instrumentKind: "stock",
    frozenChartsByPeriod: { D: chartData },
    showDataNotes: false,
  }),
);
assert.ok(html.includes("报告冻结图"));
assert.ok(!html.includes("LIVE_ERROR") && !html.includes("2099-01-01"));
assert.deepEqual(paths, [null]);
assert.deepEqual(annotations, [false]);
assert.equal(
  option.series.filter((row) => row.name.startsWith("BOLL ")).length,
  4,
);
assert.ok(
  option.series
    .filter((row) => row.name.startsWith("BOLL "))
    .every((row) => row.yAxisIndex === 1),
);
assert.deepEqual(option.series.find((row) => row.name === "BOLL K线").data, [
  [10, 10, 9, 11],
]);
assert.deepEqual(option.series.find((row) => row.name === "K线").data, [
  [10, 10, 9, 11],
]);
assert.ok(option.grid[1].height >= 140);
period = "M";
const missing = renderToStaticMarkup(
  jsx(Chart, {
    instrumentId: "000001.SZ",
    frozenChartsByPeriod: { D: chartData },
    showDataNotes: false,
  }),
);
assert.ok(missing.includes("报告中没有留存该周期K线"));
assert.ok(missing.includes("不回退到当前行情"));
assert.deepEqual(paths, [null, null]);
allowLive = true;
period = "D";
renderToStaticMarkup(
  jsx(Chart, {
    instrumentId: "000001.SZ",
    instrumentKind: "stock",
    showDataNotes: false,
  }),
);
assert.ok(paths.at(-1).startsWith("/instruments/000001.SZ/chart?"));
assert.equal(
  annotations.at(-1),
  true,
  "normal stock charts retain annotations",
);
const savedLine = {
  annotation_id: "saved-line",
  instrument_id: "000001.SZ",
  price: 12.5,
  color: "green",
  label: "支撑",
  line_type: "support",
  price_basis: "forward_event_affine",
  anchor_date: "2026-10-21",
};
annotationViews = [
  {
    line: savedLine,
    price: 9.84,
    anchor: "2026-10-22",
    converted: true,
    reason: "verified projection",
  },
];
renderToStaticMarkup(
  jsx(Chart, {
    instrumentId: "000001.SZ",
    instrumentKind: "stock",
    showDataNotes: false,
  }),
);
const plotted = option.series.find((row) => row.name === "我的水平线").markLine
  .data[0];
assert.equal(plotted.yAxis, 9.84);
assert.match(plotted.label.formatter, /9\.84/);
assert.equal(
  savedLine.price,
  12.5,
  "plotting a projected price cannot rewrite the saved original",
);
assert.equal(savedLine.anchor_date, "2026-10-21");
annotationViews = [];
// A saved set may arrive in any order. Layout and the actual switches always
// follow volume → BOLL → KDJ → MACD, including when any pane is disabled.
const paneSeries = {
  volume: "成交量",
  boll: "BOLL K线",
  kdj: "K",
  macd: "MACD",
};
for (let mask = 0; mask < 16; mask++) {
  const enabled = ["volume", "boll", "kdj", "macd"].filter(
    (_key, index) => mask & (1 << index),
  );
  settings.stockPanels = enabled.filter((key) => key !== "boll").reverse();
  settings.stockShowBoll = enabled.includes("boll");
  const rendered = renderToStaticMarkup(
    jsx(Chart, {
      instrumentId: "000001.SZ",
      instrumentKind: "stock",
      frozenChartsByPeriod: { D: chartData },
      showDataNotes: false,
    }),
  );
  enabled.forEach((key, index) => {
    const series = option.series.find((row) => row.name === paneSeries[key]);
    assert.equal(series.xAxisIndex, index + 1, `${enabled}: ${key} x axis`);
    assert.equal(series.yAxisIndex, index + 1, `${enabled}: ${key} y axis`);
    if (key === "boll") assert.ok(option.grid[index + 1].height >= 140);
  });
  assert.ok(
    rendered.indexOf(">成交量</button>") < rendered.indexOf(">BOLL</button>"),
  );
  assert.ok(
    rendered.indexOf(">BOLL</button>") < rendered.indexOf(">KDJ</button>"),
  );
  assert.ok(
    rendered.indexOf(">KDJ</button>") < rendered.indexOf(">MACD</button>"),
  );
  assert.deepEqual(
    option.dataZoom[0].xAxisIndex,
    option.grid.map((_grid, index) => index),
  );
  assert.deepEqual(option.axisPointer.link, [{ xAxisIndex: "all" }]);
}
console.log(
  "actual frozen CandleChart disables live GET/annotations, uses frozen BOLL pane and preserves missing-period placeholders",
);
