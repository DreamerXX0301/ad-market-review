import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./indexPriceProfilePresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { buildIndexProfileSeries, indexProfileGuides, indexProfileTooltipHtml } =
  await import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );

const palette = Object.freeze({
  center: "#gold",
  valueArea: "#blue",
  outside: "#muted",
  boundary: "#lightblue",
});
// This is deliberately a presentation fixture, never a product data source.
const profile = Object.freeze({
  state: "ready",
  reason: "",
  asOf: "2026-10-23",
  requested: 20,
  observed: 20,
  startDate: "2026-09-22",
  endDate: "2026-10-23",
  center: 102.5,
  centerIndex: 2,
  val: 101,
  vah: 103,
  valueAreaStartIndex: 1,
  valueAreaEndIndex: 2,
  totalVisits: 10,
  valueAreaVisits: 8,
  valueAreaFraction: 0.7,
  latestClose: 103,
  rows: Object.freeze([
    Object.freeze({ index: 0, low: 100, high: 101, price: 100.5, count: 2 }),
    Object.freeze({ index: 1, low: 101, high: 102, price: 101.5, count: 3 }),
    Object.freeze({ index: 2, low: 102, high: 103, price: 102.5, count: 5 }),
  ]),
});
const original = JSON.stringify(profile);
const series = buildIndexProfileSeries(profile, { axisIndex: 4, palette });
assert.equal(series.type, "custom");
assert.equal(series.xAxisIndex, 4);
assert.equal(series.yAxisIndex, 4);
assert.deepEqual(series.encode, { x: 2, y: [0, 1], tooltip: [0, 1, 2] });
assert.deepEqual(series.data, [
  [100, 101, 2, 0],
  [101, 102, 3, 1],
  [102, 103, 5, 2],
]);
const grid = { x: 10, y: 20, width: 100, height: 100 };
const render = (values) =>
  series.renderItem(
    { coordSys: grid },
    {
      value: (dimension) => values[dimension],
      coord: ([count, price]) => [10 + count * 10, 120 - (price - 100) * 10],
    },
  );
const outside = render(series.data[0]);
const area = render(series.data[1]);
const center = render(series.data[2]);
assert.equal(outside.style.fill, palette.outside);
assert.equal(area.style.fill, palette.valueArea);
assert.equal(center.style.fill, palette.center);
assert.equal(center.shape.width, 50);
assert.equal(center.shape.x, 10);
assert.ok(Math.abs(center.shape.y - 90.65) < 1e-10);
assert.ok(Math.abs(center.shape.height - 8.7) < 1e-10);
assert.equal(center.emphasis.style.stroke, palette.boundary);

// Off-screen price buckets and excessively long count bars cannot bleed into
// neighbouring price/date charts. A truly flat window is still visible.
assert.equal(render([101, 102, 30, 2]).shape.width, 100);
const clipped = render([99, 101, 2, 0]);
assert.ok(clipped.shape.y + clipped.shape.height <= grid.y + grid.height);
assert.equal(render([80, 90, 2, 0]), undefined);
assert.equal(render([100, 101, 0, 0]), undefined);
assert.equal(render([101, 100, 2, 0]), undefined);
assert.equal(render([100, Infinity, 2, 0]), undefined);
const flat = render([105, 105, 3, 2]);
assert.equal(flat.shape.y, 68.5);
assert.equal(flat.shape.height, 3);

assert.deepEqual(indexProfileGuides(profile), [
  { kind: "center", price: 102.5, label: "活动中心" },
  { kind: "val", price: 101, label: "主要区下沿" },
  { kind: "vah", price: 103, label: "主要区上沿" },
]);
assert.equal(indexProfileGuides({ ...profile, val: null }).length, 2);
const tooltip = indexProfileTooltipHtml(profile, 2);
assert.match(tooltip, /102\.00 — 103\.00 点/);
assert.match(tooltip, /5 \/ 20 个报价日/);
assert.match(tooltip, /活动中心所在档/);
assert.match(tooltip, /非成交量或筹码/);
assert.match(indexProfileTooltipHtml(profile, 1), /主要活动区/);
assert.match(indexProfileTooltipHtml(profile, 0), /区间外/);
assert.match(
  indexProfileTooltipHtml({ ...profile, startDate: "<img src=x>" }, 1),
  /&lt;img src=x&gt;/,
);
for (const index of [-1, NaN, 0.5, 999])
  assert.equal(indexProfileTooltipHtml(profile, index), "");
const unavailable = { ...profile, state: "unavailable" };
assert.deepEqual(
  buildIndexProfileSeries(unavailable, { axisIndex: 4, palette }).data,
  [],
);
assert.deepEqual(indexProfileGuides(unavailable), []);
assert.equal(indexProfileTooltipHtml(unavailable, 0), "");
assert.equal(JSON.stringify(profile), original, "presentation is read-only");
console.log(
  "index price profile presentation: price-band coordinates, centre/value-area colors, hard clipping, flat-price glyph, visit tooltip and unknown isolation passed",
);
