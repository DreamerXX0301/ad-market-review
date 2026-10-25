import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./chartSessionDistance.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { chartSessionDistance: distance } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

const data = {
  as_of: "2026-10-23",
  period: "D",
  trading_day_distance: {
    anchor_date: "2026-10-23",
    basis: "market_calendar",
    state: "available",
    reason: "按本图市场交易日日历。",
  },
  // A halted/missing quote sequence has only two rows, but that is not the calendar.
  bars: [
    { date: "2026-09-18", trading_days_to_as_of: 19 },
    { date: "2026-10-23", trading_days_to_as_of: 0 },
  ],
};
const original = structuredClone(data);
assert.equal(distance(data.bars[0], data).days, 19);
assert.equal(
  distance(data.bars[0], data).label,
  "距行情截止日 2026-10-23 · 19个交易日",
);
assert.equal(distance(data.bars[1], data).days, 0);
assert.equal(
  distance({ date: "2026-10-22", trading_days_to_as_of: 1 }, data).label,
  "距行情截止日 2026-10-23 · 1个交易日",
);
assert.equal(
  distance(
    { date: "2026-09-17", trading_days_to_as_of: 1 },
    {
      ...data,
      as_of: "2026-09-18",
      trading_day_distance: {
        ...data.trading_day_distance,
        anchor_date: "2026-09-18",
      },
    },
  ).label,
  "距行情截止日 2026-09-18 · 1个交易日",
  "historical views retain their own cutoff, independently of the computer day",
);
assert.match(distance(data.bars[0], data).reason, /不是电脑当天/);
assert.equal(
  distance(data.bars[0], { ...data, as_of: "2026-10-22" }).days,
  null,
  "date switches must not reuse the previous anchor proof",
);
assert.equal(
  distance(data.bars[0], { ...data, trading_day_distance: undefined }).days,
  null,
  "old frozen reports never borrow a current calendar",
);
assert.match(
  distance(data.bars[0], { ...data, trading_day_distance: undefined }).label,
  /待核验/,
);
assert.equal(
  distance(data.bars[0], {
    ...data,
    trading_day_distance: { ...data.trading_day_distance, basis: "quote_rows" },
  }).days,
  null,
);
assert.equal(
  distance(data.bars[0], {
    ...data,
    trading_day_distance: {
      ...data.trading_day_distance,
      state: "unknown",
      reason: "日历重复",
    },
  }).reason,
  "日历重复",
);
for (const value of [
  -1,
  1.5,
  NaN,
  Infinity,
  "19",
  undefined,
  null,
  false,
  Number.MAX_SAFE_INTEGER + 1,
])
  assert.equal(
    distance({ date: "2026-09-18", trading_days_to_as_of: value }, data).days,
    null,
  );
for (const day of ["2026-10-24", "2026-02-30", "2026-9-18", null])
  assert.equal(
    distance({ date: day, trading_days_to_as_of: 19 }, data).days,
    null,
  );
assert.equal(
  distance({ date: data.as_of, trading_days_to_as_of: 1 }, data).days,
  null,
);
assert.equal(
  distance({ date: "2026-10-22", trading_days_to_as_of: 0 }, data).days,
  null,
);
for (const period of ["W", "M"]) {
  const result = distance(
    { date: "2026-10-16", trading_days_to_as_of: 5, complete: false },
    { ...data, period },
  );
  assert.match(result.label, /^末根报价距行情截止日 2026-10-23 · 5个交易日$/);
  assert.match(result.reason, /实际报价日 2026-10-16/);
  assert.match(result.reason, /原形成中及缺报价状态保持/);
}
assert.deepEqual(
  data,
  original,
  "display helper never mutates the frozen chart or preferences",
);
const chartSource = await fs.readFile(
  new URL("./Charts.tsx", import.meta.url),
  "utf8",
);
assert.match(
  chartSource,
  /const activeSessionDistance = chartSessionDistance\(activeBar, data\)/,
);
assert.match(
  chartSource,
  /const sessionDistance = chartSessionDistance\(b, data\)/,
);
assert.match(chartSource, /title=\{activeSessionDistance.reason\}/);
assert.match(chartSource, /escape\(sessionDistance.label\)/);
assert.doesNotMatch(source, /Date\.now|new Date\(\)|\.bars\.length|\.getDay\(/);
console.log(
  "Chart session distance: frozen market-calendar evidence, historical anchors, quote gaps, same-day zero, strict unknowns and daily/weekly/monthly labels passed",
);
