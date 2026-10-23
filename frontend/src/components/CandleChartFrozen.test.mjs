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
const { indexPriceProfile } = await helper("./indexPriceProfile.ts");
const { buildIndexProfileSeries } = await helper(
  "./indexPriceProfilePresentation.ts",
);
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
  chartOnReady,
  allowLive = false,
  annotationViews = [];
const chartButtons = new Map();
const chartElement = (factory) => (type, props, key) => {
  if (type === "button" && ["放大", "缩小", "全范围"].includes(props?.title))
    chartButtons.set(props.title, props.onClick);
  return factory(type, props, key);
};
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
  _jsx: chartElement(jsx),
  _jsxs: chartElement(jsxs),
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
  indexPriceProfile,
  buildIndexProfileSeries,
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
  EChart: ({ option: next, onReady }) => {
    option = next;
    chartOnReady = onReady;
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
  assert.deepEqual(option.axisPointer.link, [
    {
      xAxisIndex: option.grid.map((_grid, index) => index),
    },
  ]);
}
// The real chart must isolate count X from every time axis, while both price
// Y axes use precisely the same bounds. A saved index choice never reaches a
// stock, sector endpoint, aggregate candle or frozen research chart.
settings.indexShowPriceProfile = true;
settings.indexPriceProfileLookback = 20;
settings.indexPanels = ["volume", "kdj", "macd"];
chartData.instrument = { instrument_id: "000001.SH", kind: "index" };
const indexProps = {
  instrumentId: "000001.SH",
  instrumentKind: "index",
  showDataNotes: false,
};
renderToStaticMarkup(jsx(Chart, indexProps));
const profileSeries = option.series.find(
  (row) => row.id === "index-price-profile",
);
assert.ok(profileSeries, "live daily index exposes distribution");
const profileAxis = profileSeries.xAxisIndex;
assert.equal(option.xAxis[profileAxis].type, "value");
assert.equal(option.grid[profileAxis].height, option.grid[0].height);
assert.equal(option.yAxis[profileAxis].min, option.yAxis[0].min);
assert.equal(option.yAxis[profileAxis].max, option.yAxis[0].max);
for (const zoom of option.dataZoom) {
  assert.ok(!zoom.xAxisIndex.includes(profileAxis));
  assert.ok(
    zoom.xAxisIndex.every((index) => option.xAxis[index].type === "category"),
  );
}
assert.ok(!option.axisPointer.link[0].xAxisIndex.includes(profileAxis));
assert.equal(profileSeries.data.length, 32);
assert.ok(profileSeries.data.every((row) => row[2] === 1));
assert.ok(
  option.series.find((row) => row.name === "分布参照").markLine.data.length > 0,
);
period = "W";
const weeklyIndex = renderToStaticMarkup(jsx(Chart, indexProps));
assert.ok(!option.series.some((row) => row.id === "index-price-profile"));
assert.match(weeklyIndex, /价格分布使用日线区间/);
period = "D";
renderToStaticMarkup(
  jsx(Chart, { ...indexProps, frozenChartsByPeriod: { D: chartData } }),
);
assert.ok(!option.series.some((row) => row.id === "index-price-profile"));
renderToStaticMarkup(
  jsx(Chart, { ...indexProps, dataEndpoint: "/sectors/test/chart" }),
);
assert.ok(!option.series.some((row) => row.id === "index-price-profile"));
chartData.instrument = { instrument_id: "000001.SZ", kind: "stock" };
renderToStaticMarkup(
  jsx(Chart, {
    instrumentId: "000001.SZ",
    instrumentKind: "stock",
    showDataNotes: false,
  }),
);
assert.ok(!option.series.some((row) => row.id === "index-price-profile"));

// Exercise real chart controls and event callbacks against a long, synthetic
// quote history. Short selected windows must still zoom in the stated direction;
// time zoom never changes the fixed profile sample or resets a live gesture.
// These rows exist only in this isolated renderer, never in product transport.
chartData.instrument = { instrument_id: "000001.SH", kind: "index" };
chartData.bars = Array.from({ length: 1000 }, (_value, index) => {
  const date = new Date(Date.UTC(2022, 0, 1 + index))
    .toISOString()
    .slice(0, 10);
  const close = 3000 + index / 10;
  return { date, open: close - 2, high: close + 8, low: close - 9, close };
});
chartData.as_of = chartData.bars.at(-1).date;
settings.indexPanels = ["volume", "kdj", "macd"];
settings.indexShowBoll = false;

for (const count of [20, 60]) {
  settings.indexPriceProfileLookback = count;
  chartButtons.clear();
  renderToStaticMarkup(jsx(Chart, indexProps));
  const initialOption = option;
  const fixedProfile = initialOption.series.find(
    (row) => row.id === "index-price-profile",
  );
  const fixedProfileData = JSON.stringify(fixedProfile.data);
  const model = {
    ...initialOption,
    dataZoom: initialOption.dataZoom.map((zoom) => ({ ...zoom })),
    yAxis: initialOption.yAxis.map((axis) => ({ ...axis })),
  };
  const listeners = new Map();
  const merges = [];
  const actions = [];
  const fakeChart = {
    getZr: () => ({ on: () => {} }),
    on: (event, listener) => listeners.set(event, listener),
    getOption: () => model,
    setOption: (patch, options) => {
      merges.push({ patch, options });
      // Model the id-based merge without emulating ECharts rendering. Retaining
      // the real dataZoom model makes accidental gesture resets observable.
      for (const update of patch.yAxis || []) {
        const axis = model.yAxis.find((axis) => axis.id === update.id);
        assert.ok(axis, "price-axis updates identify an existing axis");
        Object.assign(axis, update);
      }
      if (patch.dataZoom) model.dataZoom = patch.dataZoom;
    },
    dispatchAction: (action) => {
      actions.push(action);
      assert.equal(action.type, "dataZoom");
      model.dataZoom.forEach((zoom) => {
        zoom.start = action.start;
        zoom.end = action.end;
      });
      listeners.get("datazoom")?.({});
    },
  };
  chartOnReady(fakeChart);
  assert.equal(typeof listeners.get("datazoom"), "function");
  const start =
    ((chartData.bars.length - count) / (chartData.bars.length - 1)) * 100;
  const initialWidth = 100 - start;
  const setUserWindow = () => {
    model.dataZoom.forEach((zoom) => {
      zoom.start = start;
      zoom.end = 100;
    });
    listeners.get("datazoom")({});
  };
  setUserWindow();
  assert.equal(actions.length, 0, "a user zoom event never dispatches a reset");
  assert.equal(model.dataZoom[0].start, start);
  assert.equal(model.dataZoom[0].end, 100);
  assert.ok(merges.length > 0, "zoom refreshes the linked price range");
  for (const { patch, options } of merges) {
    assert.deepEqual(Object.keys(patch), ["yAxis"]);
    assert.equal(patch.yAxis.length, 2);
    assert.ok(
      options?.notMerge !== true,
      "zoom keeps the existing chart model",
    );
    assert.equal(patch.yAxis[0].min, patch.yAxis[1].min);
    assert.equal(patch.yAxis[0].max, patch.yAxis[1].max);
  }
  chartButtons.get("放大")();
  const narrower = actions.at(-1);
  assert.ok(
    narrower.end - narrower.start < initialWidth,
    `zoom in narrows a ${count}-quote window instead of imposing a wider 5% floor`,
  );
  assert.ok(narrower.start >= 0 && narrower.end <= 100);
  setUserWindow();
  chartButtons.get("缩小")();
  const wider = actions.at(-1);
  assert.ok(wider.end - wider.start > initialWidth);
  assert.ok(wider.start >= 0 && wider.end <= 100);
  assert.equal(JSON.stringify(fixedProfile.data), fixedProfileData);
  assert.equal(
    JSON.stringify(
      model.series.find((row) => row.id === "index-price-profile").data,
    ),
    fixedProfileData,
    "zoom keeps the observation-date profile sample fixed",
  );
}
console.log(
  "actual CandleChart isolates daily index profile count axes, stock/frozen/sector scope and frozen BOLL transport; short-window zoom preserves gesture direction, fixed profile and linked price axes",
);
