import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await fs.readFile(new URL("./chartDivergence.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { chartDivergences, axisPosition } = await import(
  "data:text/javascript;base64," + Buffer.from(js).toString("base64")
);
const bars = [10, 11, 15, 11, 10, 8, 10, 11, 17, 11, 10, 7, 10, 11].map(
  (price, index) => ({
    date: "2026-11-" + String(index + 1).padStart(2, "0"),
    high: price + 1,
    low: price - 1,
    macd_dif: ({ 2: 2, 5: -2, 8: 1, 11: -1 })[index] ?? 0,
    kdj_k: ({ 2: 90, 5: 15, 8: 78, 11: 24 })[index] ?? 50,
  }),
);

test("uses consecutive confirmed highs and lows for DIF and K", () => {
  for (const indicator of ["macd", "kdj"]) {
    const events = chartDivergences(bars, new Set(), "D", indicator);
    const top = events.get("2026-11-09")[0];
    const bottom = events.get("2026-11-12")[0];
    assert.equal(top.kind, "top");
    assert.equal(top.previous.date, "2026-11-03");
    assert.equal(top.confirmedOn, "2026-11-11");
    assert.equal(bottom.kind, "bottom");
    assert.equal(bottom.previous.date, "2026-11-06");
    assert.equal(bottom.confirmedOn, "2026-11-14");
    assert.equal(chartDivergences(bars.slice(0, 10), new Set(), "D", indicator).size, 0);
  }
});

test("gaps, missing indicators and provisional bars cannot confirm a turn", () => {
  const interrupted = bars.map((bar, i) => i === 13 ? { ...bar, noon_provisional: true } : bar);
  const result = chartDivergences(interrupted, new Set(["2026-11-07"]), "D", "macd");
  assert.equal(result.size, 0);
  assert.equal(chartDivergences(
    bars.map((bar, i) => i === 8 ? { ...bar, macd_dif: null } : bar),
    new Set(), "D", "macd",
  ).has("2026-11-09"), false);
});

test("axis state preserves zeros and missing values", () => {
  assert.equal(axisPosition(0, 0), "在轴上");
  assert.equal(axisPosition(51, 50), "轴上");
  assert.equal(axisPosition(49, 50), "轴下");
  assert.equal(axisPosition(null, 0), "—");
});
