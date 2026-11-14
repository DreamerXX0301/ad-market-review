import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./chartVolumeMarkers.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { chartVolumeMarkers } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const dates = (start, count, step = 1) =>
  Array.from({ length: count }, (_, i) =>
    new Date(Date.parse(start) + i * step * 86_400_000).toISOString().slice(0, 10),
  );
const daily = dates("2026-06-01", 24).map((date, i) => ({
  date,
  volume: [320, 90, 269, 538][i - 20] ?? 100,
  trading_days_to_as_of: 23 - i,
}));

test("expansion uses the previous period and low volume uses the preceding 20", () => {
  const points = chartVolumeMarkers(daily, "D");
  assert.deepEqual(points.get(daily[20].date), {
    label: "3倍+",
    kind: "expansion",
    previousRatio: 3.2,
    averageRatio: 3.2,
  });
  assert.equal(points.get(daily[19].date).label, null, "equal volume is not a low");
  assert.equal(points.get(daily[19].date).averageRatio, null);
  assert.equal(points.get(daily[21].date).label, "地量");
  assert.equal(points.get(daily[22].date).label, "2倍量");
  assert.equal(points.get(daily[23].date).label, "2倍量", "exact multiple qualifies");
  for (const [volume, label] of [[299, "2倍量"], [300, "3倍+"], [1700, "3倍+"]]) {
    const [first, second] = daily;
    const threshold = chartVolumeMarkers([first, { ...second, volume }], "D");
    assert.equal(threshold.get(second.date).label, label);
  }
});

test("gap, invalid volume and provisional data restart comparisons", () => {
  const points = chartVolumeMarkers(
    [...daily.slice(0, 20), { ...daily[20], volume: 0 }, daily[21], {
      ...daily[22], intraday_provisional: true,
    }, daily[23]],
    "D",
  );
  assert.equal(points.has(daily[20].date), false);
  assert.equal(points.has(daily[22].date), false);
  assert.equal(points.get(daily[21].date).averageRatio, null);
  assert.equal(points.get(daily[23].date).previousRatio, null);
  assert.equal(chartVolumeMarkers(daily, "D", [
    { resume_quote_date: daily[20].date },
  ]).get(daily[20].date).previousRatio, null);
  assert.equal(chartVolumeMarkers(daily.filter((_, i) => i !== 19), "D")
    .get(daily[20].date).previousRatio, null);
});

test("weeks use comparable closed periods including a holiday-shortened week", () => {
  const bars = [
    { date: "2026-09-25", volume: 40 },
    { date: "2026-09-30", volume: 120 },
    { date: "2026-10-16", volume: 400 },
    { date: "2026-10-22", volume: 900, week_closed: false },
  ].map((bar) => ({
    complete: true, weekly_quote_coverage_complete: true, week_closed: true, ...bar,
  }));
  const points = chartVolumeMarkers(bars, "W");
  assert.equal(points.get(bars[1].date).label, "3倍+");
  assert.equal(points.get(bars[2].date).previousRatio, null);
  assert.equal(points.has(bars[3].date), false);
});
