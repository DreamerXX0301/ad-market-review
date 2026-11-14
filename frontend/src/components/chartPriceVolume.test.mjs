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
const { chartPriceVolume, priceVolumeLineLabels } = await import(
  encode(await compile("./chartPriceVolumePresentation.ts"))
);
const visibility = encode(await compile("../lib/indicatorVisibility.ts"));
const format = encode(
  (await compile("../lib/format.ts")).replaceAll(
    'from "./indicatorVisibility"',
    `from ${JSON.stringify(visibility)}`,
  ),
);
const { default: Panel } = await import(
  encode(
    (await compile("./ChartPriceVolume.tsx"))
      .replaceAll(
        'from "react/jsx-runtime"',
        `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
      )
      .replaceAll('from "../lib/format"', `from ${JSON.stringify(format)}`)
      .replace('import "./ChartPriceVolume.css";', ""),
  )
);
const id = "000001.SZ";
const day = "2026-11-10";
const bars = [
  { date: "2026-11-03", close: 9 },
  { date: "2026-11-04", close: 10 },
  { date: "2026-11-05", close: 11 },
  { date: "2026-11-06", close: 9.8 },
  { date: "2026-11-09", close: 10.1 },
  { date: day, close: 10.5 },
];
const level = (price, source = "confirmed_pivot_low") => ({
  price,
  source,
  formed_at: "2026-11-03",
  confirmed_on: "2026-11-05",
  distance_pct: (price / 10.5 - 1) * 100,
});
const chart = {
  instrument: { instrument_id: id, kind: "stock" },
  period: "D",
  as_of: day,
  snapshot_id: "market-one",
  price_basis: "none",
  price_unit: "CNY",
  volume_unit: "股",
  bars,
  price_volume_levels: {
    algorithm: "structure-volume-levels-v1",
    instrument_id: id,
    period: "D",
    as_of: day,
    snapshot_id: "market-one",
    price_basis: "none",
    state: "ready",
    reason: null,
    reference_date: day,
    reference_price: 10.5,
    support: level(9),
    resistance: level(12, "confirmed_pivot_high"),
    stop: {
      ...level(8.5),
      buffer_atr: 0.5,
      buffer_value: 0.5,
      base_price: 9,
      atr_reference_date: day,
    },
    breakout: {
      ...level(12.2, "confirmed_pivot_high"),
      buffer_atr: 0.2,
      buffer_value: 0.2,
      base_price: 12,
      atr_reference_date: day,
    },
    signal: {
      state: "watching",
      label: "观察结构与量能",
      reason: "尚无收盘突破",
    },
    volume: {
      current: 18000,
      mean_previous20: 10000,
      ratio: 1.8,
      state: "expanded",
      reason: null,
      mean_start: "2026-11-03",
      mean_end: "2026-11-09",
    },
    atr: {
      period: 14,
      value: 1,
      state: "ready",
      reason: null,
      window_start: "2026-11-03",
      window_end: day,
    },
  },
};
const view = chartPriceVolume(chart, id, "D", day);
assert.equal(view.stop.price, 8.5);
assert.equal(view.volume.ratio, 1.8);
assert.equal(view.atr.value, 1);
const lines = priceVolumeLineLabels(view);
assert.deepEqual(
  lines.map((line) => line.id),
  ["price-volume:stop", "price-volume:support", "price-volume:breakout"],
);
assert.deepEqual(
  lines.map((line) => line.shortLabel),
  ["自止", "自撑", "自破"],
);
const render = (value = view, pressureZone) =>
  renderToStaticMarkup(
    jsx(Panel, {
      view: value,
      pressureZone,
      visibleIds: new Set(lines.map((line) => line.id)),
      onToggle: () => {
        throw Error("render cannot change data");
      },
    }),
  );
const html = render();
for (const text of [
  "结构失效参考",
  "支撑参考",
  "压力参考",
  "突破参考",
  "8.50 元",
  "12.20 元",
  "1.80 倍",
  "均量不含本日",
  "2026-11-05 确认",
  "14日平均真实波幅",
])
  assert.ok(html.includes(text), text);
assert.doesNotMatch(html, /<details[^>]*open/);
assert.match(html, /role="group" aria-label="压力与突破参考"/);
assert.match(html, /突破参考 12\.20 元，切换图上标注/);
assert.equal(render(null), "");
const noStructure = chartPriceVolume(
  {
    ...chart,
    price_volume_levels: {
      ...chart.price_volume_levels,
      support: null,
      resistance: null,
      stop: null,
      breakout: null,
    },
  },
  id,
  "D",
  day,
);
const noStructureHtml = render(noStructure);
assert.match(noStructureHtml, /chart-price-volume-unavailable">暂无确认支撑/);
assert.match(noStructureHtml, /chart-price-volume-unavailable">暂无确认压力/);
assert.doesNotMatch(noStructureHtml, /class="chart-price-volume-level breakout"/);
assert.match(noStructureHtml, /当前结构窗口暂无有效的单点压力及突破参考/);
const noReferenceHtml = render({
  ...noStructure,
  referenceDate: null,
  referencePrice: null,
});
assert.doesNotMatch(noReferenceHtml, /暂无确认支撑|暂无确认压力/);
assert.match(noReferenceHtml, /待核验/);
for (const [state, label] of Object.entries({
  expanded_breakout: "突破且放量",
  breakout_without_expansion: "突破 · 未达放量门槛",
  breakout_volume_unknown: "突破 · 量能待核验",
})) {
  const historicalBreakoutHtml = render({
    ...view,
    signal: { state, label: "原始突破摘要", reason: "历史突破周期状态" },
    recentBreakout: {
      date: "2026-11-05",
      price: 10,
      volumeRatio: null,
    },
  });
  const heading = historicalBreakoutHtml.split("chart-price-volume-levels")[0];
  assert.ok(heading.includes(`2026-11-05 ${label}`), state);
  assert.match(heading, /2026-11-10 · 完整日K/);
  assert.match(heading, /本日放量/);
}
for (const [state, label] of Object.entries({
  retest_holding: "回踩后保持",
  failed_breakout: "突破后回落",
})) {
  const currentStateHtml = render({
    ...view,
    signal: { state, label, reason: "当前周期状态" },
    recentBreakout: { date: "2026-11-05", price: 10, volumeRatio: null },
  });
  const heading = currentStateHtml.split("chart-price-volume-levels")[0];
  assert.ok(heading.includes(label), state);
  assert.doesNotMatch(heading, /2026-11-05/, state);
}
for (const [key, value] of Object.entries({
  instrument_id: "600519.SH",
  period: "W",
  as_of: "2026-11-09",
  snapshot_id: "market-old",
  price_basis: "forward_event_affine",
  algorithm: "unknown",
})) {
  const stale = {
    ...chart,
    price_volume_levels: { ...chart.price_volume_levels, [key]: value },
  };
  assert.equal(chartPriceVolume(stale, id, "D", day), null, key);
}
assert.equal(chartPriceVolume(chart, "600519.SH", "D", day), null);
assert.equal(chartPriceVolume(chart, id, "D", "2026-11-09"), null);
assert.equal(
  chartPriceVolume(
    {
      ...chart,
      price_volume_levels: {
        ...chart.price_volume_levels,
        reference_price: 999,
      },
    },
    id,
    "D",
    day,
  ),
  null,
);
assert.equal(
  chartPriceVolume(
    { ...chart, bars: [...bars, { date: "2099-01-01", close: 999 }] },
    id,
    "D",
    day,
  ),
  null,
);
const missingVolume = chartPriceVolume(
  {
    ...chart,
    price_volume_levels: {
      ...chart.price_volume_levels,
      volume: {
        state: "unknown",
        current: null,
        ratio: null,
        reason: "成交量单位未核验",
      },
    },
  },
  id,
  "D",
  day,
);
assert.equal(missingVolume.volume.ratio, null);
const unknownHtml = render(missingVolume);
assert.doesNotMatch(unknownHtml, /1\.80 倍/);
assert.match(unknownHtml, /成交量单位未核验/);
const futurePivot = chartPriceVolume(
  {
    ...chart,
    price_volume_levels: {
      ...chart.price_volume_levels,
      stop: { ...chart.price_volume_levels.stop, confirmed_on: "2026-11-11" },
    },
  },
  id,
  "D",
  day,
);
assert.equal(futurePivot.stop, null);
assert.equal(priceVolumeLineLabels(futurePivot).length, 2);
const index = chartPriceVolume(
  {
    ...chart,
    instrument: { ...chart.instrument, kind: "index" },
    price_unit: "点",
  },
  id,
  "D",
  day,
);
assert.match(render(index), /8\.50 点/);
assert.doesNotMatch(render(index), /8\.50 元/);
const weekly = {
  ...chart,
  period: "W",
  bars: bars.map((bar) => ({ ...bar, week_closed: true })),
  price_volume_levels: { ...chart.price_volume_levels, period: "W" },
};
assert.equal(chartPriceVolume(weekly, id, "W", day).cycle, "周");
weekly.bars.at(-1).week_closed = false;
assert.equal(
  chartPriceVolume(weekly, id, "W", day),
  null,
  "forming candle cannot masquerade as completed reference",
);

const shortChart = structuredClone(chart);
shortChart.price_volume_levels.short_term = {
  ...structuredClone(chart.price_volume_levels),
  algorithm: "short-week-risk-levels-v1",
  profile: "short_week_v1",
  max_risk_pct: 5,
  support: null,
  support_reason: "最近20日没有距离上限内的确认支撑",
  notice: "试算参数尚未完成收益有效性回测",
  stop: {
    price: 10,
    distance_pct: (10 / 10.5 - 1) * 100,
    source: "short_risk_boundary",
    formed_at: day,
    confirmed_on: day,
    buffer_atr: 0.5,
    buffer_value: 0.5,
    base_price: 10.5,
    atr_reference_date: day,
  },
};
const shortView = chartPriceVolume(shortChart, id, "D", day, "short_week_v1");
assert.equal(shortView.profile, "short_week_v1");
assert.equal(shortView.maxRiskPct, 5);
assert.equal(shortView.support, null);
assert.equal(shortView.stop.price, 10);
const shortHtml = render(shortView);
assert.match(shortHtml, /短线风险线/);
assert.match(shortHtml, /暂无确认支撑/);
assert.match(shortHtml, /最近20日没有距离上限内的确认支撑/);
assert.match(shortHtml, /近20个完整交易日结构/);
assert.match(shortHtml, /公式基准/);
assert.match(shortHtml, /不是确认支撑或个人成本止损/);
assert.match(shortHtml, /试算参数/);
const shortWithZone = render(
  { ...shortView, resistance: null, breakout: null },
  { low: 7.29, high: 7.328 },
);
assert.match(shortWithZone, /7\.29–7\.33 元/);
assert.match(shortWithZone, /水平压力区/);
assert.match(shortWithZone, /近20根完整日K暂无有效的单点压力及突破参考/);
assert.doesNotMatch(shortWithZone, /class="chart-price-volume-level breakout"/);
const onlyPriorBreakout = render({
  ...shortView,
  resistance: null,
  breakout: { ...shortView.breakout, state: "held" },
});
assert.match(onlyPriorBreakout, /原突破参考 12\.20 元，切换图上标注/);
assert.doesNotMatch(onlyPriorBreakout, /class="chart-price-volume-level resistance"/);
assert.doesNotMatch(shortHtml, /短线风险线以支撑位/);
const collectElements = (node) =>
  Array.isArray(node)
    ? node.flatMap(collectElements)
    : node?.props
      ? [node, ...collectElements(node.props.children)]
      : [];
const selectedProfiles = [];
const shortPanelTree = Panel({
  view: shortView,
  visibleIds: new Set(),
  onToggle: () => {},
  onProfileChange: (profile) => selectedProfiles.push(profile),
});
const profileSelect = collectElements(shortPanelTree).find(
  (node) => node.type === "select",
);
assert.equal(profileSelect.props.value, "short_week_v1");
profileSelect.props.onChange({ target: { value: "structure" } });
assert.deepEqual(selectedProfiles, ["structure"]);
assert.equal(
  shortView.profile,
  "short_week_v1",
  "mode selection cannot mutate the source view",
);
const shortRiskLabel = priceVolumeLineLabels(shortView)[0];
assert.equal(shortRiskLabel.shortLabel, "短险");
assert.match(shortRiskLabel.detail, /风险公式基准/);
assert.doesNotMatch(shortRiskLabel.detail, /拐点/);
assert.equal(chartPriceVolume(shortChart, id, "D", day).stop.price, 8.5);
assert.equal(chartPriceVolume(chart, id, "D", day, "short_week_v1"), null);
for (const [key, value] of Object.entries({
  algorithm: "structure-volume-levels-v1",
  profile: "structure",
  max_risk_pct: 999,
  as_of: "2026-11-09",
  snapshot_id: "old",
})) {
  const invalid = structuredClone(shortChart);
  invalid.price_volume_levels.short_term[key] = value;
  assert.equal(
    chartPriceVolume(invalid, id, "D", day, "short_week_v1"),
    null,
    key,
  );
}
const wrongRiskSource = structuredClone(shortChart);
wrongRiskSource.price_volume_levels.short_term.stop.source =
  "confirmed_pivot_low";
assert.equal(
  chartPriceVolume(wrongRiskSource, id, "D", day, "short_week_v1").stop,
  null,
);
const foreignKind = structuredClone(shortChart);
foreignKind.instrument.kind = "index";
assert.equal(
  chartPriceVolume(foreignKind, id, "D", day, "short_week_v1"),
  null,
);

// Display real structural levels even when reminder eligibility excludes them.
const displayChart = structuredClone(shortChart);
displayChart.price_volume_levels.short_term.display_support = {
  ...level(9),
  distance_pct: -1, // The displayed distance is derived from verified prices.
};
displayChart.price_volume_levels.short_term.display_scope = "recent_20";
displayChart.price_volume_levels.short_term.display_distance_state =
  "within_risk_distance";
displayChart.price_volume_levels.short_term.reason =
  "暂无近端支撑；短线风险边界按波动与风险距离计算，不代表真实支撑";
const displayBefore = structuredClone(displayChart);
const displayView = chartPriceVolume(
  displayChart,
  id,
  "D",
  day,
  "short_week_v1",
);
assert.equal(displayView.support.price, 9);
assert.equal(displayView.support.distancePct, (9 / 10.5 - 1) * 100);
assert.equal(displayView.supportScope, "recent_20");
assert.equal(displayView.supportDistanceState, "far");
assert.equal(displayView.stop.price, shortView.stop.price);
assert.deepEqual(displayView.signal, shortView.signal);
assert.equal(displayChart.price_volume_levels.short_term.support, null);
assert.deepEqual(displayChart, displayBefore);
const displayHtml = render(displayView);
assert.match(displayHtml, /近20日结构支撑/);
assert.match(displayHtml, /9\.00 元/);
assert.match(displayHtml, /-14\.29%/);
assert.match(displayHtml, /距离较远，不能替代短线风险线/);
assert.match(displayHtml, /支撑类提醒以设置中已保存近端基准为准/);
assert.doesNotMatch(displayHtml, /暂无近端支撑|暂无确认支撑/);
const displayLine = priceVolumeLineLabels(displayView).find(
  (line) => line.id === "price-volume:support",
);
assert.equal(displayLine.price, 9);
assert.match(displayLine.detail, /近20日结构支撑/);
assert.match(displayLine.detail, /距离较远/);
const displayToggles = [];
const displayPanel = Panel({
  view: displayView,
  visibleIds: new Set(),
  onToggle: (...args) => displayToggles.push(args),
});
const supportButton = collectElements(displayPanel).find(
  (node) =>
    node.type === "button" &&
    node.props["aria-label"]?.startsWith("近20日结构支撑"),
);
supportButton.props.onClick();
assert.deepEqual(displayToggles, [["price-volume:support", true]]);

const historicalDisplay = structuredClone(displayChart);
historicalDisplay.price_volume_levels.short_term.display_scope =
  "historical_120";
const historicalDisplayView = chartPriceVolume(
  historicalDisplay,
  id,
  "D",
  day,
  "short_week_v1",
);
assert.match(render(historicalDisplayView), /历史结构支撑（120根）/);
assert.match(
  priceVolumeLineLabels(historicalDisplayView).find(
    (line) => line.id === "price-volume:support",
  ).detail,
  /历史结构支撑（120根）/,
);
const nearDisplay = structuredClone(displayChart);
nearDisplay.price_volume_levels.short_term.display_support = level(10.2);
const nearDisplayView = chartPriceVolume(
  nearDisplay,
  id,
  "D",
  day,
  "short_week_v1",
);
assert.equal(nearDisplayView.supportDistanceState, "within_risk_distance");
assert.match(render(nearDisplayView), /结构观察，不等于短线风险线/);
assert.doesNotMatch(render(nearDisplayView), /距离较远/);

for (const invalidLevel of [
  { ...level(9), formed_at: "2026-11-11" },
  { ...level(9), confirmed_on: "2026-11-11" },
  { ...level(9), confirmed_on: "2026-11-02" },
  level(11),
  level(9, "short_risk_boundary"),
  null,
]) {
  const invalid = structuredClone(displayChart);
  invalid.price_volume_levels.short_term.support = level(10.2);
  invalid.price_volume_levels.short_term.display_support = invalidLevel;
  const invalidView = chartPriceVolume(invalid, id, "D", day, "short_week_v1");
  assert.equal(invalidView.support, null);
  assert.equal(invalidView.stop.price, shortView.stop.price);
  assert.equal(invalidView.supportScope, null);
  assert.equal(
    priceVolumeLineLabels(invalidView).some(
      (line) => line.id === "price-volume:support",
    ),
    false,
  );
}
const invalidDisplayScope = structuredClone(displayChart);
invalidDisplayScope.price_volume_levels.short_term.display_scope = "guessed";
assert.equal(
  chartPriceVolume(invalidDisplayScope, id, "D", day, "short_week_v1").support,
  null,
);
const foreignDisplay = structuredClone(displayChart);
foreignDisplay.price_volume_levels.short_term.instrument_id = "000678.SZ";
assert.equal(
  chartPriceVolume(foreignDisplay, id, "D", day, "short_week_v1"),
  null,
  "display-only support cannot bypass chart identity checks",
);
const legacyShort = structuredClone(shortChart);
legacyShort.price_volume_levels.short_term.support = level(10.2);
assert.equal(
  chartPriceVolume(legacyShort, id, "D", day, "short_week_v1").support.price,
  10.2,
  "servers without display fields retain their original support compatibility",
);

console.log(
  "chart price-volume: exact chart identity, complete reference, dated pivots, unknown volume, points and folded evidence passed",
);
