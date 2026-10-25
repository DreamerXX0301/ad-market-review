import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./candleExtremeChange.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { candleExtremeChange } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const bar = {
  date: "2026-10-23",
  close: 624,
  high: 624,
  low: 502,
  price_continuity: true,
  corporate_action: false,
  chart_extreme_change_period: "D",
  chart_extreme_change_reference_date: "2026-10-22",
  chart_high_change_pct: 20,
  chart_low_change_pct: -3.461538461538,
  chart_high_change_pct_reason: "同锚前收",
  chart_low_change_pct_reason: "同锚前收",
};
for (const period of ["D", "W", "M"]) {
  const input = { ...bar, chart_extreme_change_period: period };
  const before = structuredClone(input);
  const view = candleExtremeChange(input, period);
  assert.equal(view.high.value, 20);
  assert.equal(view.low.value, -3.461538461538);
  assert.match(view.high.label, /最高涨幅/);
  assert.match(view.low.label, /最低涨幅/);
  assert.equal(view.high.reason, "同锚前收");
  assert.deepEqual(input, before);
  for (const bad of [
    undefined,
    { ...input, chart_extreme_change_period: "legacy" },
    { ...input, price_continuity: false },
    { ...input, corporate_action: true },
    { ...input, chart_extreme_change_reference_date: "2026-10-23" },
    { ...input, chart_extreme_change_reference_date: "2026-10-24" },
    { ...input, chart_extreme_change_reference_date: "2026-02-30" },
    { ...input, date: "bad-date" },
    { ...input, chart_extreme_change_reference_date: undefined },
  ]) {
    const unknown = candleExtremeChange(bad, period);
    assert.equal(unknown.high.value, null);
    assert.equal(unknown.low.value, null);
  }
  for (const value of [null, undefined, "20", true, NaN, Infinity, -Infinity]) {
    assert.equal(
      candleExtremeChange({ ...input, chart_high_change_pct: value }, period)
        .high.value,
      null,
    );
    assert.equal(
      candleExtremeChange({ ...input, chart_low_change_pct: value }, period).low
        .value,
      null,
    );
  }
  assert.equal(
    candleExtremeChange(
      { ...input, chart_high_change_pct: 0, chart_low_change_pct: 0 },
      period,
    ).low.value,
    0,
  );
}
// Old frozen charts cannot borrow the daily strategy field or visible neighbor.
assert.equal(
  candleExtremeChange(
    { ...bar, chart_high_change_pct: undefined, intraday_high_change_pct: 20 },
    "D",
  ).high.value,
  null,
);
console.log(
  "chart extreme presentation: dated same-period proof, signed/zero values, unknown and legacy isolation passed",
);
