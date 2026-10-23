import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./indexPriceProfile.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { indexPriceProfile: profile } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);

const bar = (day, low, high, extra = {}) => ({
  date: `2026-10-${String(day).padStart(2, "0")}`,
  low,
  high,
  open: low,
  close: high,
  ...extra,
});
const options = { asOf: "2026-10-04", lookback: 4, bins: 4 };
const bars = [bar(1, 10, 14), bar(2, 10, 12), bar(3, 12, 14), bar(4, 11, 13)];
const frozen = structuredClone(bars);
const actual = profile(bars, options);
assert.equal(actual.state, "ready");
assert.deepEqual(
  actual.rows.map((row) => row.count),
  [2, 3, 3, 2],
);
assert.deepEqual(
  actual.rows.map((row) => [row.low, row.high]),
  [
    [10, 11],
    [11, 12],
    [12, 13],
    [13, 14],
  ],
);
assert.equal(actual.totalVisits, 10);
assert.equal(
  actual.centerIndex,
  1,
  "equal POC counts use range center, then lower",
);
assert.equal(actual.center, 11.5);
assert.equal(actual.val, 10);
assert.equal(actual.vah, 13);
assert.equal(actual.valueAreaVisits, 8);
assert.equal(actual.valueAreaFraction, 0.7);
assert.equal(actual.valueAreaStartIndex, 0);
assert.equal(actual.valueAreaEndIndex, 2);
assert.equal(actual.observed, 4);
assert.equal(actual.startDate, "2026-10-01");
assert.equal(actual.endDate, options.asOf);
assert.equal(actual.latestClose, 13);
assert.match(actual.reason, /近似/);
assert.deepEqual(bars, frozen, "calculating a profile never mutates API rows");

// Higher-volume days must not receive extra weight; absent volume is allowed.
assert.deepEqual(
  profile(
    bars.map((row, i) => ({ ...row, volume: 10 ** i, amount: i * 1e9 })),
    options,
  ),
  actual,
);
assert.deepEqual(profile([...bars].reverse(), options), actual);
assert.deepEqual(
  profile([...bars, bar(5, 1, 999)], options),
  actual,
  "a future range must not move the grid or POC",
);
assert.deepEqual(
  profile([...bars, bar(5, NaN, Infinity)], options),
  actual,
  "unselected future OHLC is not consulted",
);
assert.deepEqual(
  profile([bar(1, -5, 0), ...bars.slice(1)], { ...options, lookback: 3 }).state,
  "ready",
  "old OHLC outside the selected window cannot alter it",
);

// Each daily range visits a bucket once, not once per tick, volume or close.
const boundaries = profile(
  [
    bar(1, 10, 14),
    bar(2, 10, 11),
    bar(3, 11, 12),
    bar(4, 11, 11),
    bar(5, 14, 14),
  ],
  { asOf: "2026-10-05", lookback: 5, bins: 4 },
);
assert.deepEqual(
  boundaries.rows.map((row) => row.count),
  [2, 3, 1, 2],
);
assert.ok(boundaries.rows.every((row) => row.count <= boundaries.observed));
assert.equal(boundaries.totalVisits, 8);

const flat = profile([bar(1, 12.5, 12.5), bar(2, 12.5, 12.5)], {
  asOf: "2026-10-02",
  lookback: 2,
});
assert.equal(flat.state, "ready");
assert.deepEqual(flat.rows, [
  { index: 0, low: 12.5, high: 12.5, price: 12.5, count: 2 },
]);
assert.equal(flat.center, 12.5);
assert.equal(flat.val, 12.5);
assert.equal(flat.vah, 12.5);
assert.equal(flat.totalVisits, 2);
assert.equal(flat.valueAreaVisits, 2);

const exactly70 = profile(
  Array.from({ length: 10 }, (_, index) => {
    const price = index === 0 ? 10 : index < 8 ? 11.5 : 13;
    return bar(index + 1, price, price);
  }),
  { asOf: "2026-10-10", lookback: 10, bins: 3 },
);
assert.deepEqual(
  exactly70.rows.map((row) => row.count),
  [1, 7, 2],
);
assert.equal(exactly70.valueAreaVisits, 7);
assert.equal(exactly70.val, 11);
assert.equal(
  exactly70.vah,
  12,
  "exactly 70% stops without adding a side bucket",
);

const sparse = profile(
  Array.from({ length: 10 }, (_, index) => {
    const price = index < 3 ? 10 : index < 7 ? 12.5 : 15;
    return bar(index + 1, price, price);
  }),
  { asOf: "2026-10-10", lookback: 10, bins: 5 },
);
assert.deepEqual(
  sparse.rows.map((row) => row.count),
  [3, 0, 4, 0, 3],
);
assert.equal(sparse.valueAreaVisits, 7);
assert.equal(sparse.val, 10);
assert.equal(
  sparse.vah,
  13,
  "value area remains continuous across empty buckets",
);

const partial = profile(bars, { ...options, lookback: 60 });
assert.equal(partial.state, "partial");
assert.equal(partial.requested, 60);
assert.equal(partial.observed, 4);
assert.match(partial.reason, /60.*4/);
assert.equal(partial.totalVisits, actual.totalVisits);
// A skipped date cannot be classified as a missing market session without a
// calendar: this reports quote counts and actual dates only, with no day fills.
const spaced = profile([bar(1, 10, 12), bar(4, 11, 13)], {
  ...options,
  lookback: 2,
});
assert.equal(spaced.state, "ready");
assert.equal(spaced.observed, 2);
assert.match(spaced.reason, /不代表完整交易日覆盖/);
assert.equal(spaced.startDate, "2026-10-01");
assert.equal(spaced.endDate, "2026-10-04");

for (const invalid of [
  { open: null },
  { close: "11" },
  { high: Infinity },
  { low: NaN },
  { open: 0 },
  { close: 15 },
  { open: 9 },
  { high: 9 },
  { low: 15 },
]) {
  const failed = profile(
    [{ ...bars[0], ...invalid }, ...bars.slice(1)],
    options,
  );
  assert.equal(failed.state, "unavailable", JSON.stringify(invalid));
  assert.deepEqual(failed.rows, []);
  assert.equal(failed.center, null);
  assert.equal(failed.totalVisits, 0);
  assert.match(failed.reason, /价格无效/);
}
for (const invalid of [
  { date: "2026-02-30" },
  { date: "not-date" },
  { date: null },
]) {
  const failed = profile(
    [{ ...bars[0], ...invalid }, ...bars.slice(1)],
    options,
  );
  assert.equal(failed.state, "unavailable");
  assert.match(failed.reason, /日期无效/);
}
assert.match(profile([...bars, bars[0]], options).reason, /日期重复/);
assert.match(profile([], options).reason, /没有可用/);
assert.equal(profile([bar(5, 10, 11)], options).state, "unavailable");
assert.equal(profile(null, options).state, "unavailable");
assert.equal(profile([null], options).state, "unavailable");
assert.equal(
  profile(bars, { ...options, asOf: "2026-02-30" }).state,
  "unavailable",
);
for (const lookback of [0, -1, 751, 1.5, NaN, Infinity, "60"])
  assert.equal(profile(bars, { ...options, lookback }).state, "unavailable");
for (const bins of [0, -1, 129, 1.5, NaN, Infinity, "32"])
  assert.equal(profile(bars, { ...options, bins }).state, "unavailable");
assert.equal(profile(bars, { ...options, bins: 1 }).rows.length, 1);
assert.equal(profile(bars, { ...options, bins: undefined }).rows.length, 32);
assert.equal(
  profile([bar(1, 1e16, 1e16 + 2)], {
    asOf: "2026-10-01",
    lookback: 1,
    bins: 32,
  }).state,
  "unavailable",
  "unrepresentable bucket boundaries cannot manufacture precision",
);

// Independent price-bin enumeration verifies the calculation across varied
// ranges, points and bucket counts, including exactly 70% and sparse profiles.
for (let bins = 1; bins <= 16; bins++) {
  const sequence = Array.from({ length: 20 }, (_, i) => {
    const low = 100 + ((i * 7) % 13);
    return bar(i + 1, low, low + ((i * 3) % 9));
  });
  const result = profile(sequence, { asOf: "2026-10-20", lookback: 20, bins });
  assert.equal(result.state, "ready");
  const counts = Array(bins).fill(0);
  for (const day of sequence) {
    if (day.low === day.high) {
      const row = result.rows.findIndex(
        (bucket, index) =>
          day.low >= bucket.low &&
          (day.low < bucket.high || index === bins - 1),
      );
      counts[row]++;
    } else {
      result.rows.forEach((bucket, index) => {
        if (Math.min(day.high, bucket.high) - Math.max(day.low, bucket.low) > 0)
          counts[index]++;
      });
    }
  }
  assert.deepEqual(
    result.rows.map((row) => row.count),
    counts,
  );
  assert.equal(
    result.totalVisits,
    counts.reduce((sum, count) => sum + count, 0),
  );
  const chosen = result.rows.slice(
    result.valueAreaStartIndex,
    result.valueAreaEndIndex + 1,
  );
  assert.equal(
    chosen.reduce((sum, row) => sum + row.count, 0),
    result.valueAreaVisits,
  );
  assert.ok(result.valueAreaVisits >= result.totalVisits * 0.7);
  assert.ok(result.val <= result.center && result.center <= result.vah);
}

console.log(
  "index price profile: daily range visits, deterministic POC/70% area, causal quote windows, partial coverage and invalid-data guards passed",
);
