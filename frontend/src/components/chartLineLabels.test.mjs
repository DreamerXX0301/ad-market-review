import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compile = (source) => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const helperSource = await fs.readFile(new URL("./chartLineLabelLayout.ts", import.meta.url), "utf8");
const { shortChartLineLabel, chartLineRailWidth, layoutChartLineLabels, visibleChartLineLabels, readableChartWindow } = await import("data:text/javascript;base64," + Buffer.from(compile(helperSource)).toString("base64"));
const line = (id, kind = "support", price = 12.51) => ({ id, kind, price, group: "annotation", color: "green", detail: `完整说明 ${id}`, enabled: true });
assert.equal(shortChartLineLabel(line("support")), "撑12.51");
assert.equal(shortChartLineLabel(line("pressure", "resistance", 13.88)), "压13.88");
assert.equal(shortChartLineLabel(line("event", "event", 12.98)), "事件12.98");
assert.equal(shortChartLineLabel(line("reference", "reference", .1234)), "参0.1234");
assert.equal(chartLineRailWidth(320), 92); assert.equal(chartLineRailWidth(1200), 112);
for (const height of [120, 160, 300, 500]) for (const count of [1, 3, 8, 30]) {
  const positions = Array.from({ length: count }, (_, index) => ({ id: String(index), y: 45 + index * .01 }));
  const before = structuredClone(positions), layout = layoutChartLineLabels(positions, 24, height);
  assert.deepEqual(positions, before); assert.equal(layout.rows.length + layout.overflow, count);
  layout.rows.forEach((row, index) => {
    assert.ok(row.labelY >= 24 && row.labelY + layout.rowHeight <= 24 + height);
    if (index) assert.ok(row.labelY - layout.rows[index - 1].labelY >= 24 - 1e-8);
  });
}
const outside = layoutChartLineLabels([{ id: "above", y: -100 }, { id: "below", y: 1000 }, { id: "invalid", y: NaN }], 24, 200);
assert.equal(outside.rows[0].outside, "above"); assert.equal(outside.rows[1].outside, "below");
const all = [line("one"), line("two", "resistance"), { ...line("three", "event"), enabled: false }];
const original = structuredClone(all), hidden = new Set(["one"]);
assert.deepEqual(visibleChartLineLabels(all, hidden).map(value => value.id), ["two"]);
assert.deepEqual(visibleChartLineLabels(all, new Set(all.map(value => value.id))), []);
assert.deepEqual(all, original, "visibility must never delete or rewrite saved lines/events");
for (const [period, count, maximum] of [["D", 1200, 120], ["W", 300, 160], ["M", 100, 72]]) {
  const bars = Array.from({ length: count }, (_, index) => ({ date: `fixture-${index}` }));
  const snapshot = structuredClone(bars), first = count - maximum;
  const base = { start: first / (count - 1) * 100, end: 100, firstDate: bars[first].date, verified: true, sessions: period === "D" ? 120 : period === "W" ? 800 : 1500 };
  const wide = readableChartWindow(base, period, bars, 10000);
  assert.equal(wide.visibleBars, maximum); assert.equal(wide.widthAdapted, false);
  const small = readableChartWindow(base, period, bars, 350);
  assert.ok(small.visibleBars < maximum && small.visibleBars >= 8);
  assert.equal(small.sessions, base.sessions, "verified trading-day cap remains unchanged");
  assert.equal(small.firstDate, bars[count - small.visibleBars].date);
  assert.deepEqual(bars, snapshot, "pixel cropping never truncates indicator seed history");
  assert.equal(readableChartWindow(base, period, bars, 0).start, base.start);
}
assert.equal(readableChartWindow({ start: 0, end: 100 }, "D", [], 500).visibleBars, 0);
const unknown = readableChartWindow({ start: 0, end: 100, verified: false, reason: "日历覆盖未知" }, "W", Array.from({ length: 90 }, () => ({ date: "fixture" })), 350);
assert.equal(unknown.verified, false); assert.equal(unknown.reason, "日历覆盖未知"); assert.ok(unknown.visibleBars < 90);

// Run the actual initialization branch: later resize never resets manual zoom.
const source = await fs.readFile(new URL("./Charts.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("Charts.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const branches = [], zooms = [];
function walk(node) {
  if (ts.isIfStatement(node) && node.expression.getText(ast) === "bars.length && zoomContext.current !== nextZoomContext") branches.push(node);
  if (ts.isFunctionDeclaration(node) && node.name?.text === "zoomTo") zooms.push(node);
  ts.forEachChild(node, walk);
}
walk(ast); assert.equal(branches.length, 1); assert.equal(zooms.length, 1);
const initialize = new Function("bars", "zoomContext", "nextZoomContext", "zoomWindow", "readableWindow", "plotWidth", "defaultAdapted", "userZoomed", "annotations", compile(branches[0].getText(ast)));
const bars = [{}], context = { current: "" }, window = { current: { start: 0, end: 100 } }, adapted = { current: false }, manual = { current: false };
initialize(bars, context, "D", window, { start: 70, end: 100 }, 1200, adapted, manual, { result: { loading: false } });
assert.deepEqual(window.current, { start: 70, end: 100 });
window.current = { start: 82, end: 96 }; manual.current = true;
initialize(bars, context, "D", window, { start: 92, end: 100 }, 320, adapted, manual, { result: { loading: false } });
assert.deepEqual(window.current, { start: 82, end: 96 });
initialize(bars, context, "W", window, { start: 60, end: 100 }, 800, adapted, manual, { result: { loading: false } });
assert.deepEqual(window.current, { start: 60, end: 100 }); assert.equal(manual.current, false);
const waiting = { current: false }, newContext = { current: "" }, newWindow = { current: { start: 0, end: 100 } }, user = { current: false };
initialize(bars, newContext, "M", newWindow, { start: 20, end: 100 }, 0, waiting, user, { result: { loading: false } });
initialize(bars, newContext, "M", newWindow, { start: 70, end: 100 }, 350, waiting, user, { result: { loading: false } });
assert.deepEqual(newWindow.current, { start: 70, end: 100 });
initialize(bars, newContext, "M", newWindow, { start: 90, end: 100 }, 250, waiting, user, { result: { loading: false } });
assert.deepEqual(newWindow.current, { start: 70, end: 100 });
const actions = [], instance = { current: { dispatchAction: value => actions.push(value) } };
const reset = new Function("zoomWindow", "instance", "userZoomed", compile(zooms[0].getText(ast)) + ";return zoomTo;")(newWindow, instance, user);
reset(76, 100); assert.deepEqual(newWindow.current, { start: 76, end: 100 });
assert.deepEqual(actions, [{ type: "dataZoom", start: 76, end: 100 }]);
assert.ok(source.includes("zoomOnMouseWheel: true") && source.includes("moveOnMouseWheel: false"));
console.log("Chart labels: short rail text, visibility, collision/overflow, readable D/W/M, intact seeds and manual zoom/resize/reset passed");
