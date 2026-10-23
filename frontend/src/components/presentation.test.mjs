/** Pure presentation regressions. Root runs: node src/components/presentation.test.mjs */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
async function moduleAt(path) {
  const source = await fs.readFile(new URL(path, import.meta.url), "utf8");
  let compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  if (/indicatorVisibility/.test(compiled)) {
    const visibility = await fs.readFile(
      new URL("../lib/indicatorVisibility.ts", import.meta.url),
      "utf8",
    );
    const helper = ts.transpileModule(visibility, {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText;
    const url =
      "data:text/javascript;base64," + Buffer.from(helper).toString("base64");
    compiled = compiled.replace(
      /(["'])\.{1,2}\/[^"']*indicatorVisibility\1/g,
      JSON.stringify(url),
    );
  }
  return import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );
}
const { sortTableRows } = await moduleAt("./tableSort.ts");
const { candleChange } = await moduleAt("./candleChange.ts");
const { candleChartEndpoint } = await moduleAt("./chartEndpoint.ts");
const { chartIndicatorAvailability, chartIndicatorLabel } = await moduleAt(
  "./chartIndicatorPresentation.ts",
);
const { defaultChartWindow, projectEventBaseline } =
  await moduleAt("./chartDisplay.ts");
const {
  CHART_PREFERENCES_KEY,
  PREVIOUS_CHART_PREFERENCES_KEY,
  EARLIER_CHART_PREFERENCES_KEY,
  LEGACY_CHART_PREFERENCES_KEY,
  effectiveChartMas,
  normalizeChartPreferences,
  parseChartPreferences,
  createChartPreferenceStore,
  effectiveChartPriceBasis,
  effectiveChartWaves,
} = await moduleAt("./chartPreferences.ts");
const { amount, compactQuantity, percent } = await moduleAt("../lib/format.ts");
const { trackingTopicMatch, matchesTrackingText, trackingProgress } =
  await moduleAt("../features/tracking/trackingFilterLogic.ts");

assert.equal(
  candleChartEndpoint({
    instrumentId: "600825.SH",
    period: "D",
    priceBasis: "forward",
    asOf: "2026-10-20",
  }),
  "/instruments/600825.SH/chart?period=D&price_basis=forward&as_of=2026-10-20",
);
for (const period of ["D", "W", "M"]) {
  const endpoint = candleChartEndpoint({
    instrumentId: "sector:885728.TI",
    period,
    priceBasis: "none",
    asOf: "2026-10-20",
    dataEndpoint:
      "/market/sector-chart?topic_code=885728.TI&date=2026-10-20&period=D",
  });
  assert.equal(endpoint.split("?")[0], "/market/sector-chart");
  const params = new URLSearchParams(endpoint.split("?")[1]);
  assert.equal(params.get("topic_code"), "885728.TI");
  assert.equal(params.get("date"), "2026-10-20");
  assert.equal(params.get("as_of"), "2026-10-20");
  assert.deepEqual(params.getAll("period"), [period]);
  assert.equal(params.has("price_basis"), false);
}
const displayEndpoint = candleChartEndpoint({
  instrumentId: "600825.SH",
  period: "D",
  priceBasis: "none",
  indicatorContinuity: "quotes",
});
assert.equal(
  new URLSearchParams(displayEndpoint.split("?")[1]).get(
    "indicator_continuity",
  ),
  "quotes",
);
assert.equal(
  chartIndicatorAvailability(
    { macd_dif: 0, macd_dea: 0, macd_hist: 0 },
    ["macd_dif", "macd_dea", "macd_hist"],
    "D",
  ).missing.length,
  0,
);
const strictPreheat = chartIndicatorAvailability(
  {
    macd_dif: null,
    indicator_warmup_bars: 3,
    indicator_warmup_reason: "未核验的市场日无报价",
  },
  ["macd_dif"],
  "D",
);
assert.equal(strictPreheat.label, "预热 3/35");
assert.match(strictPreheat.reason, /未核验.*需要35根报价/);
assert.equal(
  chartIndicatorAvailability(
    {
      ma60: null,
      display_indicator_warmup_bars: 45,
      indicator_warmup_bars: 3,
      display_indicator_warmup_reason: "公司行动后预热",
    },
    ["ma60"],
    "D",
  ).label,
  "预热 45/60",
);
assert.match(
  chartIndicatorAvailability(
    { kdj_k: null, indicator_warmup_bars: 2 },
    ["kdj_k"],
    "M",
  ).reason,
  /完整月K/,
);
assert.equal(chartIndicatorLabel("macd_hist"), "MACD柱");
assert.equal(chartIndicatorLabel("ma250"), "MA250");
assert.equal(chartIndicatorLabel("kdj_j"), "");
for (const period of ["D", "W", "M"]) {
  assert.deepEqual(
    chartIndicatorAvailability(
      { kdj_k: 0, kdj_d: 0, kdj_j: null },
      ["kdj_k", "kdj_d", "kdj_j"],
      period,
    ).missing,
    [],
    "a hidden indicator cannot create a visible warmup warning",
  );
}

const rows = [
  { id: "up", pct: 10 },
  { id: "unknown", pct: null },
  { id: "down", pct: -3 },
  { id: "zero", pct: 0 },
  { id: "invalid", pct: NaN },
  { id: "equal", pct: 10 },
];
assert.deepEqual(
  sortTableRows(rows, { key: "pct", order: "asc" }, (row) => row.pct).map(
    (row) => row.id,
  ),
  ["down", "zero", "up", "equal", "unknown", "invalid"],
);
assert.deepEqual(
  sortTableRows(rows, { key: "pct", order: "desc" }, (row) => row.pct).map(
    (row) => row.id,
  ),
  ["up", "equal", "zero", "down", "unknown", "invalid"],
);
assert.equal(rows[0].id, "up");
const paged = Array.from({ length: 40 }, (_, i) => ({ value: i }));
assert.equal(
  sortTableRows(
    paged,
    { key: "value", order: "desc" },
    (row) => row.value,
  ).slice(0, 15)[0].value,
  39,
);
assert.deepEqual(
  sortTableRows(
    [{ date: "2026-10-19" }, { date: null }, { date: "2026-01-02" }],
    { key: "date", order: "asc" },
    (row) => row.date,
  ).map((row) => row.date),
  ["2026-01-02", "2026-10-19", null],
);

assert.equal(
  candleChange({ change_pct: -2.5, price_continuity: true }, "D").value,
  -2.5,
);
assert.equal(
  candleChange({ open: 1, close: 100, change_pct: null }, "D").value,
  null,
);
assert.equal(
  candleChange(
    {
      change_pct: 3,
      price_continuity: false,
      indicator_reset_reason: "缺报价",
    },
    "D",
  ).reason,
  "缺报价",
);
assert.equal(
  candleChange({ change_pct: 3, corporate_action: true }, "D").value,
  null,
);
assert.equal(
  candleChange(
    { change_pct: 3, corporate_action: false, corporate_action_original: true },
    "D",
  ).value,
  3,
);
assert.match(
  candleChange({ change_pct: 3, week_closed: false }, "W").reason,
  /上一可比周/,
);
assert.match(
  candleChange({ change_pct: 3, week_closed: false }, "W").reason,
  /形成中/,
);
assert.equal(
  candleChange({ change_pct: null, weekly_quote_coverage_complete: false }, "W")
    .value,
  null,
);
for (const [value, expected] of [
  [null, "—"],
  [NaN, "—"],
  [0, "0.00元"],
  [9999, "9,999.00元"],
  [10000, "1.00万元"],
  [1e8, "1.00亿元"],
  [1e12, "1.00万亿元"],
  [-1.25e12, "-1.25万亿元"],
])
  assert.equal(amount(value), expected);
assert.equal(compactQuantity(1e8, "股"), "1.00亿股");
assert.equal(percent(NaN), "—");

const stock = {
  name: "测试公司",
  code: "600001",
  current_topics: [{ topic_id: "a" }],
};
assert.equal(trackingTopicMatch(stock, [], "all", new Set()), "matched");
assert.equal(
  trackingTopicMatch(stock, ["a", "b"], "any", new Set()),
  "matched",
);
assert.equal(
  trackingTopicMatch(stock, ["a", "b"], "all", new Set()),
  "unknown",
);
assert.equal(
  trackingTopicMatch(stock, ["a", "b"], "all", new Set(["b"])),
  "unmatched",
);
assert.equal(
  trackingTopicMatch(stock, ["b"], "any", new Set(["b"])),
  "unmatched",
);
assert.equal(
  trackingTopicMatch(undefined, ["b"], "any", new Set(["b"])),
  "unknown",
);
assert.equal(trackingTopicMatch(stock, ["a"], "all", new Set()), "matched");
assert.equal(matchesTrackingText(stock, "600001"), true);
for (const unknown of [null, undefined, NaN, Infinity, -1, 1.5, "0"]) {
  assert.equal(
    trackingProgress({ trading_age: unknown, remaining_sessions: 30 })
      .percentage,
    null,
  );
  assert.equal(
    trackingProgress({ trading_age: 0, remaining_sessions: unknown })
      .percentage,
    null,
  );
}
assert.deepEqual(
  trackingProgress({
    trading_age: 0,
    remaining_sessions: 30,
    policy: { duration_sessions: 30 },
  }),
  { age: 0, duration: 30, percentage: 0 },
);
assert.equal(
  trackingProgress({ trading_age: 0, remaining_sessions: 0 }).percentage,
  0,
);
assert.equal(
  trackingProgress({ trading_age: 30, remaining_sessions: 0 }).percentage,
  100,
);
assert.equal(
  trackingProgress({ trading_age: 35, remaining_sessions: 0 }).percentage,
  100,
);
assert.deepEqual(
  trackingProgress({
    trading_age: 3,
    remaining_sessions: 9,
    policy: { duration_sessions: 12 },
  }),
  { age: 3, duration: 12, percentage: 25 },
);
assert.equal(
  candleChange({ change_pct: 5, month_closed: false }, "M").label,
  "月涨跌幅",
);
assert.match(
  candleChange({ change_pct: 5, month_closed: false }, "M").reason,
  /上一可比月/,
);
assert.match(
  candleChange({ change_pct: 5, month_closed: false }, "M").reason,
  /本月形成中/,
);
assert.equal(
  candleChange(
    { change_pct: null, monthly_quote_coverage_complete: false },
    "M",
  ).value,
  null,
);

const defaultPreferences = normalizeChartPreferences(null);
assert.equal(defaultPreferences.showEventBaseline, true);
assert.deepEqual(defaultPreferences.stockMas, [5, 10, 20, 30]);
assert.deepEqual(defaultPreferences.indexMas, [5, 10, 20, 30, 60, 250]);
const legacyStorage = new Map([
  [
    LEGACY_CHART_PREFERENCES_KEY,
    JSON.stringify({ version: 1, mas: [60, 250], stockPriceBasis: "forward" }),
  ],
]);
const migratedStore = createChartPreferenceStore({
  getItem: (key) => legacyStorage.get(key) ?? null,
  setItem: (key, value) => legacyStorage.set(key, value),
});
assert.deepEqual(migratedStore.getSnapshot().preferences.indexMas, [60, 250]);
assert.deepEqual(
  migratedStore.getSnapshot().preferences.stockMas,
  [5, 10, 20, 30],
);
migratedStore.update({ stockMas: [5, 30, 60] });
assert.deepEqual(
  effectiveChartMas(migratedStore.getSnapshot().preferences, true),
  [60, 250],
);
migratedStore.update({ indexMas: [250] });
assert.deepEqual(
  effectiveChartMas(migratedStore.getSnapshot().preferences, false),
  [5, 30, 60],
);
const independentReload = createChartPreferenceStore({
  getItem: (key) => legacyStorage.get(key) ?? null,
  setItem() {},
});
assert.deepEqual(
  independentReload.getSnapshot().preferences,
  migratedStore.getSnapshot().preferences,
);
assert.match(
  legacyStorage.get(LEGACY_CHART_PREFERENCES_KEY),
  /"mas":\[60,250\]/,
  "legacy key is retained and old tabs cannot overwrite the new key",
);
const isolatedCopy = effectiveChartMas(
  migratedStore.getSnapshot().preferences,
  false,
);
isolatedCopy.push(250);
assert.deepEqual(migratedStore.getSnapshot().preferences.stockMas, [5, 30, 60]);

assert.deepEqual(defaultChartWindow("D", 50), { start: 0, end: 100 });
const dailyWindow = defaultChartWindow("D", 726);
assert.ok(Math.abs((1 - dailyWindow.start / 100) * 725 + 1 - 100) < 1e-9);
assert.deepEqual(defaultChartWindow("W", 150), { start: 0, end: 100 });
assert.deepEqual(defaultChartWindow("M", 36), { start: 0, end: 100 });

const baseline = {
  date: "2026-01-05",
  rawClose: 20,
  priceBasis: "none",
  label: "入池事件价",
};
const rawChart = {
  as_of: "2026-10-20",
  period: "D",
  price_basis: "none",
  bars: [
    { date: "2026-01-02", close: 19 },
    { date: "2026-01-05", close: 20 },
    { date: "2026-10-20", close: 23 },
  ],
};
assert.equal(projectEventBaseline(baseline, rawChart).price, 20);
assert.equal(
  projectEventBaseline(baseline, {
    ...rawChart,
    requested_price_basis: "forward",
    adjustment: { usable: false },
  }).price,
  20,
  "failed forward requests use the chart's actual raw basis",
);
const affineChart = {
  ...rawChart,
  price_basis: "forward_event_affine",
  bars: rawChart.bars.map((bar) => ({
    ...bar,
    raw_close: bar.close,
    adjustment_anchor: rawChart.as_of,
  })),
  adjustment: {
    usable: true,
    model: "event_affine",
    anchor: rawChart.as_of,
    quarantined: [],
    events_count: 2,
    events: [
      {
        date: "2026-02-03",
        cash: 1,
        bonus: 0.5,
        rights: 0,
        rights_price: 0,
        factor: 0.9,
      },
      {
        date: "2026-06-03",
        cash: 0.2,
        bonus: 0,
        rights: 0.1,
        rights_price: 5,
        factor: 0.8,
      },
    ],
  },
};
const projected = projectEventBaseline(baseline, affineChart);
assert.ok(
  Math.abs(projected.price - ((20 - 1) / 1.5 - 0.2 + 0.5) / 1.1) < 1e-12,
);
assert.equal(projected.converted, true);
assert.equal(projected.anchor, rawChart.as_of);
assert.notEqual(
  projected.price,
  20 * 0.9 * 0.8,
  "the theoretical ratio is not the chart's affine model",
);
assert.equal(
  projectEventBaseline(
    { ...baseline, date: "2026-02-03" },
    { ...affineChart, period: "W" },
  ).price,
  (20 - 0.2 + 0.5) / 1.1,
  "events on or before entry day are already in the frozen raw price",
);
assert.equal(
  projectEventBaseline(baseline, { ...affineChart, period: "M" }).price,
  projected.price,
  "month view projects the entry date, never a month-end close",
);
assert.equal(
  projectEventBaseline(
    { ...baseline, date: "2026-01-01" },
    { ...affineChart, period: "M" },
  ).price,
  null,
);
assert.equal(
  projectEventBaseline(baseline, {
    ...affineChart,
    bars: affineChart.bars.map((bar) => ({ ...bar, raw_close: 999 })),
  }).price,
  null,
);
assert.equal(
  projectEventBaseline(baseline, {
    ...affineChart,
    period: "W",
    bars: [
      { date: "2026-01-02" },
      { date: "2026-10-20", weekly_missing_market_dates: [baseline.date] },
    ],
  }).price,
  null,
);
for (const invalid of [
  { ...baseline, date: "2026-02-30" },
  { ...baseline, date: "2026-10-21" },
  { ...baseline, rawClose: 0 },
  { ...baseline, rawClose: NaN },
  { ...baseline, priceBasis: "forward" },
])
  assert.equal(projectEventBaseline(invalid, affineChart).price, null);
for (const adjustment of [
  { ...affineChart.adjustment, usable: false },
  { ...affineChart.adjustment, model: "theoretical_ratio" },
  { ...affineChart.adjustment, anchor: "2026-10-19" },
  { ...affineChart.adjustment, quarantined: [{ date: "2026-09-01" }] },
  { ...affineChart.adjustment, events_count: 1 },
  {
    ...affineChart.adjustment,
    events: [
      { date: "2026-11-01", cash: 0, bonus: 0, rights: 0, rights_price: 0 },
    ],
    events_count: 1,
  },
  {
    ...affineChart.adjustment,
    events: [
      { date: "2026-02-03", cash: NaN, bonus: 0, rights: 0, rights_price: 0 },
    ],
    events_count: 1,
  },
  {
    ...affineChart.adjustment,
    events: [
      { date: "2026-02-03", cash: 50, bonus: 0, rights: 0, rights_price: 0 },
    ],
    events_count: 1,
  },
  {
    ...affineChart.adjustment,
    events: [
      affineChart.adjustment.events[0],
      affineChart.adjustment.events[0],
    ],
  },
])
  assert.equal(
    projectEventBaseline(baseline, { ...affineChart, adjustment }).price,
    null,
  );
assert.deepEqual(parseChartPreferences("{broken"), defaultPreferences);
assert.deepEqual(
  parseChartPreferences(JSON.stringify({ version: 999, mas: [250] })),
  defaultPreferences,
);
assert.deepEqual(
  normalizeChartPreferences({
    version: 1,
    mas: [250, 5, 5],
    stockPriceBasis: "forward",
    showLimits: false,
  }),
  {
    version: 4,
    stockMas: [5, 10, 20, 30],
    indexMas: [5, 250],
    stockPriceBasis: "forward",
    showLimits: false,
    showEventBaseline: true,
    stockShowWaves: true,
    indexShowWaves: true,
    stockWaveMode: "turning",
    indexWaveMode: "latest",
    stockPanels: ["volume", "kdj", "macd"],
    indexPanels: ["volume", "kdj", "macd", "breadth"],
    stockShowBoll: false,
    indexShowBoll: false,
  },
);
assert.deepEqual(
  normalizeChartPreferences({ version: 1, mas: [] }).indexMas,
  [],
);
assert.deepEqual(
  normalizeChartPreferences({ version: 1, mas: [999] }).indexMas,
  defaultPreferences.indexMas,
);
let rawPreferences = null,
  writes = 0;
const storage = {
  getItem(key) {
    assert.ok(
      [CHART_PREFERENCES_KEY, PREVIOUS_CHART_PREFERENCES_KEY, EARLIER_CHART_PREFERENCES_KEY, LEGACY_CHART_PREFERENCES_KEY].includes(key),
    );
    return key === CHART_PREFERENCES_KEY ? rawPreferences : null;
  },
  setItem(key, value) {
    assert.equal(key, CHART_PREFERENCES_KEY);
    rawPreferences = value;
    writes++;
  },
};
const chartStore = createChartPreferenceStore(storage);
let chartA = 0,
  chartB = 0;
const unsubscribe = chartStore.subscribe(() => chartA++);
chartStore.subscribe(() => chartB++);
chartStore.update({
  indexMas: [250],
  stockPriceBasis: "forward",
  showLimits: false,
  showEventBaseline: false,
  ignoredToken: "must-not-persist",
});
assert.equal(chartA, 1);
assert.equal(chartB, 1);
assert.equal(rawPreferences.includes("ignoredToken"), false);
assert.equal(rawPreferences.includes("must-not-persist"), false);
assert.equal(chartStore.getSnapshot().preferences.showEventBaseline, false);
assert.equal(rawPreferences.includes("rawClose"), false);
const reloadedStore = createChartPreferenceStore(storage);
assert.deepEqual(
  reloadedStore.getSnapshot().preferences,
  chartStore.getSnapshot().preferences,
);
const storedForward = chartStore.getSnapshot().preferences;
assert.equal(effectiveChartPriceBasis(storedForward, true), "none");
assert.equal(storedForward.stockPriceBasis, "forward");
assert.equal(effectiveChartPriceBasis(storedForward, false), "forward");
unsubscribe();
const beforeStorageEvent = writes;
chartStore.receiveStorage(
  JSON.stringify({
    version: 1,
    mas: [10, 30],
    stockPriceBasis: "none",
    showLimits: true,
  }),
);
assert.equal(
  writes,
  beforeStorageEvent,
  "storage events must not write back or loop",
);
assert.deepEqual(chartStore.getSnapshot().preferences.indexMas, [10, 30]);
assert.equal(chartA, 1);
assert.equal(chartB, 2);
chartStore.receiveStorage(null);
assert.deepEqual(chartStore.getSnapshot().preferences, defaultPreferences);
chartStore.update({ indexMas: [] });
chartStore.reset();
assert.deepEqual(chartStore.getSnapshot().preferences, defaultPreferences);
const deniedStore = createChartPreferenceStore({
  getItem() {
    throw new Error("denied");
  },
  setItem() {
    throw new Error("quota");
  },
});
assert.equal(deniedStore.getSnapshot().persistent, false);
assert.doesNotThrow(() =>
  deniedStore.update({ indexMas: [250], stockPriceBasis: "forward" }),
);
assert.deepEqual(deniedStore.getSnapshot().preferences.indexMas, [250]);
assert.equal(deniedStore.getSnapshot().persistent, false);
const memoryStore = createChartPreferenceStore(null);
assert.doesNotThrow(() => memoryStore.update({ showLimits: false }));
assert.equal(memoryStore.getSnapshot().preferences.showLimits, false);

// Wave defaults migrate existing display choices, then stay independent by kind.
const v2Raw = JSON.stringify({
  version: 2,
  stockMas: [30, 60],
  indexMas: [250],
  stockPriceBasis: "forward",
  showLimits: false,
  showEventBaseline: false,
});
const waveStorage = new Map([[EARLIER_CHART_PREFERENCES_KEY, v2Raw]]);
const waveStorageAdapter = {
  getItem: (key) => waveStorage.get(key) ?? null,
  setItem: (key, value) => waveStorage.set(key, value),
};
const waveStore = createChartPreferenceStore(waveStorageAdapter);
assert.deepEqual(waveStore.getSnapshot().preferences.stockMas, [30, 60]);
assert.deepEqual(waveStore.getSnapshot().preferences.indexMas, [250]);
assert.equal(waveStore.getSnapshot().preferences.stockPriceBasis, "forward");
assert.equal(waveStore.getSnapshot().preferences.showLimits, false);
assert.equal(waveStore.getSnapshot().preferences.showEventBaseline, false);
assert.deepEqual(effectiveChartWaves(waveStore.getSnapshot().preferences, false), {
  showWaves: true, waveMode: "turning",
});
assert.deepEqual(effectiveChartWaves(waveStore.getSnapshot().preferences, true), {
  showWaves: true, waveMode: "latest",
});
waveStore.update({ stockShowWaves: false, stockWaveMode: "down" });
waveStore.update({ indexWaveMode: "up" });
let waveReload = createChartPreferenceStore(waveStorageAdapter);
assert.deepEqual(effectiveChartWaves(waveReload.getSnapshot().preferences, false), {
  showWaves: false, waveMode: "down",
});
assert.deepEqual(effectiveChartWaves(waveReload.getSnapshot().preferences, true), {
  showWaves: true, waveMode: "up",
});
assert.equal(waveStorage.get(EARLIER_CHART_PREFERENCES_KEY), v2Raw);
// An old tab may write its v2 key; it cannot replace saved v4 wave settings.
waveStorage.set(EARLIER_CHART_PREFERENCES_KEY, JSON.stringify({ version: 2, stockMas: [] }));
waveReload = createChartPreferenceStore(waveStorageAdapter);
assert.equal(waveReload.getSnapshot().preferences.stockWaveMode, "down");
assert.deepEqual(waveReload.getSnapshot().preferences.stockMas, [30, 60]);
for (const mode of ["latest", "up", "down", "turning"]) {
  waveStore.update({ stockWaveMode: mode });
  assert.equal(createChartPreferenceStore(waveStorageAdapter).getSnapshot().preferences.stockWaveMode, mode);
}
for (const mode of ["unknown", null, {}, 1]) {
  const sanitized = normalizeChartPreferences({ version: 3, stockWaveMode: mode, indexWaveMode: mode, stockShowWaves: "false" });
  assert.deepEqual(effectiveChartWaves(sanitized, false), { showWaves: true, waveMode: "turning" });
  assert.deepEqual(effectiveChartWaves(sanitized, true), { showWaves: true, waveMode: "latest" });
}
waveStore.receiveStorage(JSON.stringify({ version: 3, stockShowWaves: false, stockWaveMode: "up" }));
assert.deepEqual(effectiveChartWaves(waveStore.getSnapshot().preferences, false), { showWaves: false, waveMode: "up" });
waveStore.reset();
assert.deepEqual(waveStore.getSnapshot().preferences, defaultPreferences);
deniedStore.update({ stockShowWaves: false, stockWaveMode: "up" });
assert.deepEqual(effectiveChartWaves(deniedStore.getSnapshot().preferences, false), { showWaves: false, waveMode: "up" });
assert.equal(deniedStore.getSnapshot().persistent, false);
// Execute the actual App theme adapters in isolation: restricted localStorage
// must not throw before the chart preference store can fall back to memory.
const appSource = await fs.readFile(
  new URL("../App.tsx", import.meta.url),
  "utf8",
);
const appAst = ts.createSourceFile(
  "App.tsx",
  appSource,
  ts.ScriptTarget.ES2022,
  true,
  ts.ScriptKind.TSX,
);
const themeFunctions = appAst.statements.filter(
  (node) =>
    ts.isFunctionDeclaration(node) &&
    ["readInitialTheme", "persistTheme"].includes(node.name?.text),
);
assert.equal(themeFunctions.length, 2);
const themeCode = ts.transpileModule(
  themeFunctions.map((node) => "export " + node.getText(appAst)).join("\n"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const themeAdapters = await import(
  "data:text/javascript;base64," + Buffer.from(themeCode).toString("base64")
);
const previousStorage = Object.getOwnPropertyDescriptor(
  globalThis,
  "localStorage",
);
try {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() {
      throw new Error("storage getter denied");
    },
  });
  assert.equal(themeAdapters.readInitialTheme(), "dark");
  assert.doesNotThrow(() => themeAdapters.persistTheme("light"));
  let savedTheme;
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: () => "light",
      setItem: (key, value) => {
        savedTheme = [key, value];
      },
    },
  });
  assert.equal(themeAdapters.readInitialTheme(), "light");
  themeAdapters.persistTheme("dark");
  assert.deepEqual(savedTheme, ["shixu-theme", "dark"]);
  globalThis.localStorage.setItem = () => {
    throw new Error("quota exceeded");
  };
  assert.doesNotThrow(() => themeAdapters.persistTheme("light"));
} finally {
  if (previousStorage)
    Object.defineProperty(globalThis, "localStorage", previousStorage);
  else delete globalThis.localStorage;
}
const dataCenterSource = await fs.readFile(
  new URL("../features/data/DataCenter.tsx", import.meta.url),
  "utf8",
);
const dataCenterAst = ts.createSourceFile(
  "DataCenter.tsx",
  dataCenterSource,
  ts.ScriptTarget.ES2022,
  true,
  ts.ScriptKind.TSX,
);
const chainFunction = dataCenterAst.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "taskChainState",
);
assert.ok(chainFunction);
const chainCode = ts.transpileModule(
  "export " + chainFunction.getText(dataCenterAst),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const { taskChainState } = await import(
  "data:text/javascript;base64," + Buffer.from(chainCode).toString("base64")
);
assert.equal(
  taskChainState({
    kind: "initialise",
    parameters: { window: "near-three-years" },
    logs: [],
  }).state,
  "none",
  "parameters alone never authorize a chain",
);
const authorized = {
  logs: [{ stage: "chain_authorized" }],
  status: "succeeded",
  finished_at_ms: 1,
};
assert.equal(
  taskChainState(authorized).state,
  "pending",
  "authorized follow-up stays pending beyond an arbitrary 15s window",
);
const blocked = {
  ...authorized,
  logs: [
    ...authorized.logs,
    { stage: "chain_blocked", details: { reason: "来源暂不可用" } },
  ],
};
assert.equal(taskChainState(blocked).state, "blocked");
assert.equal(taskChainState(blocked).reason, "来源暂不可用");
const recovered = {
  ...blocked,
  logs: [
    ...blocked.logs,
    {
      stage: "followup_queued",
      details: { next_task_id: "actual-child", child_status: "paused" },
    },
  ],
};
assert.equal(taskChainState(recovered).state, "queued");
assert.equal(taskChainState(recovered).nextTaskId, "actual-child");
assert.equal(
  taskChainState(recovered).reason,
  null,
  "a later durable child supersedes the earlier blocked event",
);
assert.equal(
  taskChainState({
    ...recovered,
    logs: [
      ...recovered.logs,
      { stage: "chain_blocked", details: { reason: "later failure" } },
    ],
  }).state,
  "blocked",
);
assert.equal(
  taskChainState({
    logs: [
      { stage: "not-a-chain-event", details: { next_task_id: "unverified" } },
    ],
  }).nextTaskId,
  null,
);
console.log(
  "presentation: raw-value sorting, comparable D/W/M returns, recent-100 daily zoom, frozen event affine projection and rejection, CNY/volume units, three-valued filters, versioned shared preferences, index isolation, reload/storage sync, and private-storage fallback passed",
);
