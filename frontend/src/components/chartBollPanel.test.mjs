import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const transpile = (text) =>
  ts.transpileModule(text, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
const layoutSource = await fs.readFile(
  new URL("./chartPaneLayout.ts", import.meta.url),
  "utf8",
);
const { chartPaneLayout } = await import(
  "data:text/javascript;base64," +
    Buffer.from(transpile(layoutSource)).toString("base64")
);
for (const requested of [100, 480, 625, 650, 1000]) {
  for (let count = 0; count <= 5; count++) {
    const layout = chartPaneLayout(requested, count, true);
    assert.ok(layout.mainHeight >= 180);
    assert.ok(layout.actualHeight >= requested);
    if (!count) continue;
    assert.ok(layout.subHeight >= 55 - 1e-8);
    const bottom =
      24 +
      layout.mainHeight +
      layout.gap +
      (layout.subHeight + layout.gap) * (count - 1) +
      layout.subHeight;
    assert.ok(
      bottom <= layout.actualHeight - 50,
      `${count} panes overflow the canvas`,
    );
  }
}
assert.ok(
  chartPaneLayout(650, 5).actualHeight > 650,
  "five panes grow the canvas rather than clip",
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
const bollBlocks = [],
  formatters = [];
function walk(node) {
  if (
    ts.isIfStatement(node) &&
    node.expression.getText(ast) === 'key === "boll"'
  )
    bollBlocks.push(node.thenStatement);
  if (
    ts.isPropertyAssignment(node) &&
    node.name.getText(ast) === "formatter" &&
    ts.isArrowFunction(node.initializer) &&
    node.initializer.getText(ast).includes("priceIndicators")
  )
    formatters.push(node.initializer);
  ts.forEachChild(node, walk);
}
walk(ast);
assert.equal(bollBlocks.length, 1);
assert.equal(formatters.length, 1);
const series = [];
const points = [
  { date: "2026-10-09", upper: 12.9, middle: 12.44, lower: 11.98 },
];
const build = new Function(
  "series",
  "line",
  "ix",
  "boll",
  "pal",
  transpile(bollBlocks[0].getText(ast)),
);
build(
  series,
  (name, field) => series.push({ name, field, xAxisIndex: 2, yAxisIndex: 2 }),
  2,
  points,
  { text: "#fff" },
);
assert.equal(series.length, 4);
assert.equal(series[0].field, "close");
assert.ok(
  series.every((item) => item.xAxisIndex === 2 && item.yAxisIndex === 2),
);
assert.deepEqual(
  series.slice(1).map((item) => item.data),
  [[12.9], [12.44], [11.98]],
);
assert.ok(series.slice(1).every((item) => item.connectNulls === false));
assert.ok(
  !bollBlocks[0].getText(ast).includes("xAxisIndex: 0"),
  "BOLL never draws on the main price axis",
);

// Exercise the actual tooltip with deliberately misindexed ECharts values.
const formatterFactory = new Function(
  "bars",
  "period",
  "data",
  "boll",
  "mas",
  "maColors",
  "MA_WINDOWS",
  "showBoll",
  "pal",
  "candleChange",
  "chartSinceReturn",
  "num",
  "percent",
  "supportsLimits",
  "limitTooltipHtml",
  transpile("return (" + formatters[0].getText(ast) + ");"),
);
const bar = {
  date: points[0].date,
  open: 12.43,
  high: 13,
  low: 12,
  close: 12.9,
  ma20: 12.44,
};
const args = [
  [bar],
  "D",
  {},
  points,
  [20],
  ["#1", "#2", "#3", "#4", "#5", "#6"],
  [5, 10, 20, 30, 60, 250],
  true,
  { text: "#fff", up: "#f00", down: "#0f0" },
  () => ({ value: 1, reason: "", label: "日涨跌幅" }),
  () => ({ value: 0, end: bar.date, reason: "" }),
  (value) => Number(value).toFixed(2),
  (value) => value + "%",
  false,
  () => "",
];
const params = [
  {
    seriesName: "BOLL 中轨",
    axisValue: bar.date,
    dataIndex: 100,
    value: 999,
    marker: "WRONG_MARKER",
  },
];
const html = formatterFactory(...args)(params);
for (const text of [
  "MA20　12.44",
  "BOLL副图 上轨　12.90",
  "BOLL副图 中轨　12.44",
  "BOLL副图 下轨　11.98",
])
  assert.ok(html.includes(text), text);
assert.ok(!html.includes("999") && !html.includes("WRONG_MARKER"));
console.log(
  "BOLL independent pane, close reference, dynamic non-clipping layout and same-date tooltip passed",
);
