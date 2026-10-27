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
const { annotationColors, lineLabel, savedLineSettings, levelOriginLabel } = await helper("./chartAnnotations.ts");
const lineLabels = await helper("./chartLineLabelLayout.ts");
const { chartPaneLayout } = await helper("./chartPaneLayout.ts");
const { candleChartEndpoint, initialChartResponse } = await helper("./chartEndpoint.ts");
const { candleExtremeChange } = await helper("./candleExtremeChange.ts");
const { chartSessionDistance } = await helper("./chartSessionDistance.ts");
const { chartPriceBasis } = await helper("./chartPriceBasis.ts");
const waveHelpers = await helper("./wavePresentation.ts");
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
const overlayReads = { boll: 0, patterns: 0, turns: 0 };
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
  chartBoll: (data) => {
    overlayReads.boll++;
    return chartBoll(data);
  },
  bollCandleValues,
  annotationColors,
  lineLabel,
  savedLineSettings,
  levelOriginLabel,
  ...lineLabels,
  EMPTY_LINE_IDS: new Set(),
  chartPaneLayout,
  candleChartEndpoint,
  initialChartResponse,
  candleExtremeChange,
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
    return { enabled, visible: enabled ? annotationViews : [], views: enabled ? annotationViews : [], selectedCandidates: [], preview: null, show: true, result: { loading: false }, drawing: false };
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
  singlePageDefaultWindow: (_period, bars) => ({ start: 0, end: 100, firstDate: bars[0]?.date || null, sessions: 120, label: "测试冻结显示范围" }),
  projectEventBaseline: () => ({ price: null, reason: "" }),
  historicalChartEvents: () => [],
  projectHistoricalChartEvent: () => null,
  ...waveHelpers,
  wavePatterns: (...args) => {
    overlayReads.patterns++;
    return waveHelpers.wavePatterns(...args);
  },
  waveTurningPoints: (...args) => {
    overlayReads.turns++;
    return waveHelpers.waveTurningPoints(...args);
  },
  candleChange: () => ({ value: null, reason: "", label: "日涨跌幅" }),
  chartSinceReturn: () => ({ value: null, reason: "" }),
  chartSessionDistance,
  chartPriceBasis,
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
assert.match(html, /最高 .*待核验/);
assert.match(html, /最低 .*待核验/);
const frozenTooltip = option.tooltip.formatter([
  { axisValue: "2026-10-22", seriesName: "K线", dataIndex: 0 },
]);
assert.match(frozenTooltip, /当日最高涨幅.*待核验/);
assert.match(frozenTooltip, /当日最低涨幅.*待核验/);
assert.ok(html.includes("距行情截止日 2026-10-22 · 交易日距离待核验"));
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
const liveMarkup = renderToStaticMarkup(
  jsx(Chart, {
    instrumentId: "000001.SZ",
    instrumentKind: "stock",
    showDataNotes: false,
  }),
);
assert.match(liveMarkup, /图表价格：未复权/);
chartData.price_basis = "forward_event_affine";
const forwardMarkup = renderToStaticMarkup(
  jsx(Chart, {
    instrumentId: "000001.SZ",
    instrumentKind: "stock",
    showDataNotes: false,
  }),
);
assert.match(forwardMarkup, /图表价格：本地前复权/);
chartData.price_basis = "none";
Object.assign(chartData.bars[0], {
  chart_high_change_pct: 10,
  chart_low_change_pct: -10,
  chart_extreme_change_period: "D",
  chart_extreme_change_reference_date: "2026-10-21",
  chart_high_change_pct_reason: "同图价格口径与上一市场交易日前收",
  chart_low_change_pct_reason: "同图价格口径与上一市场交易日前收",
});
const extremeMarkup = renderToStaticMarkup(
  jsx(Chart, {
    instrumentId: "000001.SZ",
    instrumentKind: "stock",
    showDataNotes: false,
  }),
);
assert.match(extremeMarkup, /高 <b>11<\/b>/);
assert.match(extremeMarkup, /最高 10%/);
assert.match(extremeMarkup, /低 <b>9<\/b>/);
assert.match(extremeMarkup, /最低 -10%/);
const liveTooltip = option.tooltip.formatter([
  { axisValue: "2026-10-22", seriesName: "K线", dataIndex: 0 },
]);
assert.match(liveTooltip, /高 11 .*当日最高涨幅 10%/);
assert.match(liveTooltip, /低 9 .*当日最低涨幅 -10%/);
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
const plotted = option.series.find((row) => row.name === "支撑压力与事件参照").markLine
  .data[0];
assert.equal(plotted.yAxis, 9.84);
assert.equal(plotted.label.show, false, "full descriptions belong in the independent label rail");
assert.match(plotted.name, /9\.84/);
assert.ok(option.grid[0].right >= 100, "right rail never covers the last candles");
assert.ok(option.grid.every((grid) => grid.right === option.grid[0].right));
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
// The real chart skips disabled overlays and unused wave modes. Enabling one
// immediately uses the current frozen payload, without a GET or a cached quote.
const waveDates = Array.from(
  { length: 6 },
  (_, index) => `2026-10-${17 + index}`,
);
chartData.bars = waveDates.map((date, index) => ({
  ...chartData.bars[0],
  date,
  close: 10 + index / 10,
}));
const wavePoints = waveDates.map((date, index) => ({
  date,
  confirmed_at: "2026-10-22",
  price: 10 + index / 10,
  kind: index % 2 ? "high" : "low",
  label: index ? `${index}?` : "0",
  phase: "impulse",
  segment_id: "verified-segment",
}));
chartData.waves = {
  points: wavePoints,
  patterns: [
    {
      pattern_id: "verified-pattern",
      direction: "up",
      start_date: waveDates[0],
      end_date: waveDates.at(-1),
      confirmed_at: "2026-10-22",
      has_correction: false,
      points: wavePoints,
    },
  ],
};
function overlayRender({ boll = false, waves = false, mode = "turning" } = {}) {
  settings.stockShowBoll = boll;
  settings.stockShowWaves = waves;
  settings.stockWaveMode = mode;
  Object.keys(overlayReads).forEach((key) => {
    overlayReads[key] = 0;
  });
  const count = paths.length;
  renderToStaticMarkup(
    jsx(Chart, {
      instrumentId: "000001.SZ",
      instrumentKind: "stock",
      frozenChartsByPeriod: { D: chartData },
      showDataNotes: false,
    }),
  );
  assert.deepEqual(paths.slice(count), [null]);
  return { ...overlayReads };
}
assert.deepEqual(overlayRender(), { boll: 0, patterns: 0, turns: 0 });
assert.deepEqual(overlayRender({ boll: true, waves: true }), {
  boll: 1,
  patterns: 0,
  turns: 1,
});
assert.ok(option.series.some((series) => series.name === "高低点转折候选"));
assert.deepEqual(overlayRender({ waves: true, mode: "latest" }), {
  boll: 0,
  patterns: 1,
  turns: 0,
});
assert.ok(option.series.some((series) => series.name.includes("上涨五浪候选")));
assert.ok(!option.series.some((series) => series.name === "高低点转折候选"));
assert.deepEqual(overlayRender(), { boll: 0, patterns: 0, turns: 0 });
assert.ok(!option.series.some((series) => series.id?.startsWith("wave-")));
console.log(
  "actual frozen CandleChart disables live GET/annotations, preserves missing periods, and calculates only enabled BOLL/wave overlays",
);
