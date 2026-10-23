import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./chartBoll.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { chartBoll, bollCandleValues, BOLL_PERIOD, BOLL_STD_MULTIPLIER } =
  await import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );

const dateAt = (offset) =>
  new Date(Date.UTC(2026, 0, offset + 1)).toISOString().slice(0, 10);
const closeTo = (actual, expected, tolerance = 1e-10) =>
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${actual} != ${expected}`,
  );
function fixture(closes, extra = {}) {
  return {
    as_of: dateAt(closes.length - 1),
    period: "D",
    price_basis: "none",
    indicator_continuity: "quotes",
    bars: closes.map((close, index) => ({
      date: dateAt(index),
      close,
      indicator_warmup_bars: index + 1,
      display_indicator_warmup_bars: index + 1,
      price_continuity: true,
      corporate_action: false,
      ...extra,
    })),
  };
}

assert.equal(BOLL_PERIOD, 20);
assert.equal(BOLL_STD_MULTIPLIER, 2);
assert.deepEqual(chartBoll(null), []);
assert.deepEqual(chartBoll({ bars: null }), []);
const candle = {
  date: "2026-10-22",
  open: 10,
  close: 10.5,
  low: 9.7,
  high: 11,
  original_close: 999,
};
assert.deepEqual(bollCandleValues(candle), [10, 10.5, 9.7, 11]);
for (const invalid of [
  { open: null },
  { close: undefined },
  { high: Infinity },
  { low: NaN },
  { close: "10.5" },
  { open: 0 },
  { low: 10.1 },
  { high: 10.4 },
])
  assert.deepEqual(
    bollCandleValues({ ...candle, ...invalid }),
    ["-", "-", "-", "-"],
    JSON.stringify(invalid),
  );
assert.equal(
  candle.original_close,
  999,
  "candles use adjusted chart OHLC, never original close",
);
const sequence = fixture(Array.from({ length: 25 }, (_, index) => index + 1));
const frozen = structuredClone(sequence);
const points = chartBoll(sequence);
assert.equal(points.length, sequence.bars.length);
assert.ok(points.slice(0, 19).every((point) => point.middle === null));
assert.equal(points[18].warmupBars, 19);
assert.match(points[18].reason, /20根.*19根/);
assert.equal(points[19].reason, "");
closeTo(points[19].middle, 10.5);
closeTo(points[19].upper, 10.5 + 2 * Math.sqrt(33.25));
closeTo(points[19].lower, 10.5 - 2 * Math.sqrt(33.25));
closeTo(points[24].middle, 15.5);
closeTo(points[24].upper, 15.5 + 2 * Math.sqrt(33.25));
assert.notEqual(points[19].upper, 10.5 + 2 * Math.sqrt(35));
assert.deepEqual(
  sequence,
  frozen,
  "display BOLL never writes API bars or signals",
);

const constant = chartBoll(fixture(Array(20).fill(0.25))).at(-1);
assert.equal(constant.middle, 0.25);
assert.equal(constant.upper, 0.25);
assert.equal(constant.lower, 0.25);
const large = chartBoll(
  fixture(Array.from({ length: 20 }, (_, index) => 1e12 + index + 1)),
).at(-1);
closeTo(large.middle, 1e12 + 10.5);
closeTo(large.upper, 1e12 + 10.5 + 2 * Math.sqrt(33.25));
closeTo(large.lower, 1e12 + 10.5 - 2 * Math.sqrt(33.25));

// A server MA20 or high warm-up count cannot reveal variance of omitted prices.
const truncated = fixture(Array(20).fill(12));
truncated.bars.forEach((bar, index) => {
  bar.display_indicator_warmup_bars = index + 100;
  bar.indicator_warmup_bars = index + 100;
  bar.ma20 = 12;
});
assert.equal(chartBoll(truncated)[0].middle, null);
assert.equal(chartBoll(truncated)[18].middle, null);
assert.equal(chartBoll(truncated)[19].middle, 12);

// Strict mode restarts where its server count restarts; quote mode preserves
// actual quote continuity across the same omitted market session.
const gap = fixture(Array.from({ length: 45 }, (_, index) => index + 1));
gap.bars.forEach((bar, index) => {
  if (index >= 22) bar.indicator_warmup_bars = index - 21;
});
Object.assign(gap.bars[22], {
  price_continuity: false,
  indicator_reset_reason: "缺少1个市场交易日报价",
  missing_market_sessions_before: 1,
});
assert.equal(chartBoll(gap)[22].middle, 13.5);
const strictGap = structuredClone(gap);
strictGap.indicator_continuity = "market_sessions";
strictGap.bars.forEach((bar) => {
  bar.display_indicator_warmup_bars = bar.indicator_warmup_bars;
  bar.display_indicator_reset_reason = bar.indicator_reset_reason;
});
const strictPoints = chartBoll(strictGap);
assert.equal(strictPoints[21].middle, 12.5);
assert.equal(strictPoints[22].middle, null);
assert.equal(strictPoints[40].middle, null);
assert.equal(strictPoints[41].middle, 32.5);

const action = fixture(Array(45).fill(10));
action.bars.forEach((bar, index) => {
  if (index >= 22) {
    bar.close = 5;
    bar.indicator_warmup_bars = index - 21;
    bar.display_indicator_warmup_bars = index - 21;
  }
});
action.bars[22].corporate_action = true;
assert.equal(chartBoll(action)[22].middle, null);
assert.equal(chartBoll(action)[40].middle, null);
assert.equal(chartBoll(action)[41].middle, 5);

// Verified adjusted closes already clear corporate_action; raw-close shadows
// and original-action flags must never enter the BOLL price window.
const adjusted = fixture(Array(25).fill(5));
adjusted.price_basis = "forward_event_affine";
adjusted.adjustment = { usable: true, anchor: adjusted.as_of };
adjusted.bars.forEach((bar) => {
  bar.raw_close = 10;
  bar.corporate_action_original = true;
  bar.price_basis = adjusted.price_basis;
  bar.adjustment_anchor = adjusted.as_of;
});
assert.equal(chartBoll(adjusted)[19].middle, 5);
for (const adjustment of [
  { usable: false, anchor: adjusted.as_of },
  { usable: true, anchor: dateAt(0) },
]) {
  const invalid = { ...adjusted, adjustment };
  assert.ok(chartBoll(invalid).every((point) => point.middle === null));
}

for (const period of ["W", "M"]) {
  const aggregate = fixture(Array(43).fill(10), {
    complete: true,
    weekly_quote_coverage_complete: true,
    monthly_quote_coverage_complete: true,
    monthly_price_comparable: true,
    monthly_indicator_eligible: true,
  });
  aggregate.period = period;
  aggregate.indicator_continuity = "market_sessions";
  aggregate.bars[20].complete = false;
  aggregate.bars[20][period === "W" ? "week_closed" : "month_closed"] = false;
  const actual = chartBoll(aggregate);
  assert.equal(actual[19].middle, 10);
  assert.equal(
    actual[20].middle,
    null,
    "forming aggregate never confirms BOLL",
  );
  assert.equal(actual[39].middle, null);
  assert.equal(actual[40].middle, 10, "needs 20 new comparable full periods");

  aggregate.bars[20].complete = true;
  aggregate.bars[20][period === "W" ? "week_closed" : "month_closed"] = true;
  aggregate.bars[20].corporate_action = true;
  assert.equal(chartBoll(aggregate)[20].middle, null);
  assert.equal(chartBoll(aggregate)[39].middle, null);
  assert.equal(chartBoll(aggregate)[40].middle, 10);
}

// Explicitly invalid input breaks the window rather than being skipped,
// coerced to zero, interpolated, or connected through by the helper.
for (const invalidClose of [
  null,
  undefined,
  NaN,
  Infinity,
  0,
  -1,
  "10",
  true,
]) {
  const invalid = fixture(Array(41).fill(10));
  invalid.bars[20].close = invalidClose;
  const result = chartBoll(invalid);
  assert.equal(result[20].middle, null);
  assert.equal(result[39].middle, null);
  assert.equal(result[40].middle, 10);
}
for (const invalidCount of [undefined, null, 0, -1, NaN, 20.5, "21"]) {
  const invalid = fixture(Array(41).fill(10));
  invalid.bars[20].display_indicator_warmup_bars = invalidCount;
  assert.equal(chartBoll(invalid)[20].middle, null);
  assert.equal(chartBoll(invalid)[39].middle, null);
}
const skipped = fixture(Array(41).fill(10));
skipped.bars.forEach((bar, index) => {
  if (index >= 20) bar.display_indicator_warmup_bars += 1;
});
assert.equal(chartBoll(skipped)[20].middle, null);
assert.equal(chartBoll(skipped)[38].middle, null);
assert.equal(chartBoll(skipped)[39].middle, 10);
const duplicate = fixture(Array(41).fill(10));
duplicate.bars[20].date = duplicate.bars[19].date;
assert.equal(chartBoll(duplicate)[20].middle, null);
assert.equal(chartBoll(duplicate)[39].middle, null);
assert.equal(chartBoll(duplicate)[40].middle, 10);
const backwards = fixture(Array(45).fill(10));
for (let index = 20; index < 25; index += 1)
  backwards.bars[index].date = dateAt(index - 20);
assert.equal(
  chartBoll(backwards)[40].middle,
  null,
  "an earlier invalid date cannot move the chronology watermark backwards",
);
assert.equal(chartBoll(backwards)[44].middle, 10);

for (const patch of [
  { price_basis: "unknown" },
  { period: "Q" },
  { as_of: "2026-02-30" },
])
  assert.ok(
    chartBoll({ ...sequence, ...patch }).every(
      (point) => point.middle === null,
    ),
  );
const missingPriceBasis = structuredClone(sequence);
missingPriceBasis.bars[20].price_basis = "forward_event_affine";
assert.equal(chartBoll(missingPriceBasis)[20].middle, null);
const strictWithoutDisplay = structuredClone(sequence);
strictWithoutDisplay.indicator_continuity = "market_sessions";
strictWithoutDisplay.bars.forEach((bar) => {
  delete bar.display_indicator_warmup_bars;
});
assert.deepEqual(chartBoll(strictWithoutDisplay), points);

// Every prefix is identical to a standalone chart for that observation date;
// future prices cannot alter already-computed bands.
for (let length = 1; length <= sequence.bars.length; length += 1) {
  const prefix = {
    ...sequence,
    bars: sequence.bars.slice(0, length),
    as_of: sequence.bars[length - 1].date,
  };
  assert.deepEqual(chartBoll(prefix), points.slice(0, length));
}
const future = structuredClone(sequence);
future.as_of = dateAt(19);
future.bars[20].close = 1e9;
assert.deepEqual(chartBoll(future).slice(0, 20), points.slice(0, 20));
assert.equal(chartBoll(future)[20].middle, null);

console.log(
  "BOLL calculation, continuity, complete-period and provenance tests passed",
);
