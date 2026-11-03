import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const moduleUrl = (source) =>
  "data:text/javascript;base64," +
  Buffer.from(
    ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText,
  ).toString("base64");
const read = (file) => fs.readFile(new URL(file, import.meta.url), "utf8");
const distanceUrl = moduleUrl(await read("./chartSessionDistance.ts"));
const metricsUrl = moduleUrl(await read("./stockExtraMetrics.ts"));
const reactUrl = moduleUrl(
  "export const useEffect = () => {}; export const useRef = () => {}; export const useState = () => {};",
);
const source = (await read("./SinglePageEntry.ts"))
  .replace('from "react"', `from ${JSON.stringify(reactUrl)}`)
  .replace(
    'from "./chartSessionDistance"',
    `from ${JSON.stringify(distanceUrl)}`,
  )
  .replace('from "./stockExtraMetrics"', `from ${JSON.stringify(metricsUrl)}`);
const { singlePageDefaultWindow: windowFor, singlePagePaneLayout: layoutFor } =
  await import(moduleUrl(source));

// Synthetic market-calendar evidence, with intentional missing quote rows.
// The helper must use proved session distances rather than count quote rows.
const calendar = [];
for (
  let time = Date.parse("2035-12-28T00:00:00Z");
  calendar.length < 1800;
  time -= 86400000
) {
  const day = new Date(time);
  if (![0, 6].includes(day.getUTCDay()))
    calendar.push(day.toISOString().slice(0, 10));
}
const anchor = calendar[0];
const evidence = (period) => ({
  as_of: anchor,
  period,
  trading_day_distance: {
    state: "available",
    basis: "market_calendar",
    anchor_date: anchor,
  },
});
const barsFor = (distances) =>
  distances.map((distance) => ({
    date: calendar[distance],
    trading_days_to_as_of: distance,
    open: 10,
    close: 11,
    low: 9,
    high: 12,
    complete: true,
  }));

let windowCases = 0;
for (const [period, sessions] of [
  ["D", 120],
  ["W", 800],
  ["M", 1500],
]) {
  const data = evidence(period);
  // Include 0..N-1, exclude N. This applies equally to daily quotes and
  // complete aggregate candles whose last quote falls in the window.
  for (let distance = 1; distance < sessions; distance++) {
    const result = windowFor(
      period,
      barsFor([sessions + 1, sessions, distance, 0]),
      data,
    );
    assert.equal(result.sessions, sessions);
    assert.equal(result.verified, true);
    assert.equal(result.firstDate, calendar[distance]);
    assert.equal(result.start, (2 / 3) * 100);
    assert.equal(result.end, 100);
    windowCases++;
  }
  const bars = barsFor([sessions + 5, sessions, sessions - 1, 5, 0]);
  const original = structuredClone(bars);
  const result = windowFor(period, bars, data);
  assert.equal(result.firstDate, calendar[sessions - 1]);
  assert.equal(result.start, 50);
  assert.doesNotMatch(result.label, /不足/);
  assert.deepEqual(
    bars,
    original,
    "display crop never edits OHLC, completeness or full indicator history",
  );
  if (period !== "D") {
    assert.match(result.label, new RegExp(`${period === "W" ? "周" : "月"}K`));
    assert.match(result.reason, /周\/月保留完整柱/);
    assert.equal(
      bars.length,
      5,
      "do not manufacture daily rows from aggregate candles",
    );
  }
  assert.match(
    windowFor(period, barsFor([sessions - 2, 0]), data).label,
    /现有留存不足全窗/,
  );
  assert.equal(windowFor(period, barsFor([0]), data).start, 0);
  assert.equal(windowFor(period, barsFor([0]), data).verified, true);
  const stale = windowFor(period, barsFor([sessions + 2, sessions]), data);
  assert.equal(stale.verified, false);
  assert.match(stale.label, /无报价/);
  for (const invalidData of [
    null,
    { ...data, period: period === "D" ? "W" : "D" },
    { ...data, as_of: "2035-02-30" },
    { ...data, trading_day_distance: undefined },
    ...[
      { state: "unknown" },
      { basis: "quote_rows" },
      { anchor_date: calendar[1] },
    ].map((change) => ({
      ...data,
      trading_day_distance: { ...data.trading_day_distance, ...change },
    })),
  ]) {
    const unknown = windowFor(period, bars, invalidData);
    assert.equal(unknown.verified, false);
    assert.equal(unknown.start, 0);
    assert.equal(unknown.end, 100);
    assert.equal(unknown.firstDate, null);
  }
  const beforeWindow = barsFor([sessions + 2, sessions, sessions - 1, 0]);
  delete beforeWindow[0].trading_days_to_as_of;
  assert.equal(
    windowFor(period, beforeWindow, data).verified,
    true,
    "a known later row can prove an unknown prefix outside the window",
  );
  const uncertainBoundary = barsFor([sessions + 2, sessions - 1, 0]);
  delete uncertainBoundary[0].trading_days_to_as_of;
  assert.equal(windowFor(period, uncertainBoundary, data).verified, false);
  const unknownMiddle = barsFor([sessions + 2, sessions, sessions - 1, 0]);
  delete unknownMiddle[2].trading_days_to_as_of;
  assert.equal(windowFor(period, unknownMiddle, data).verified, false);
  assert.equal(
    windowFor(period, [{ date: calendar[1] }], data).verified,
    false,
  );
  for (const badBars of [
    [bars[1], bars[0], bars[2], bars[3], bars[4]],
    [...bars, bars.at(-1)],
    bars.map((bar, i) =>
      i === 1 ? { ...bar, trading_days_to_as_of: sessions + 6 } : bar,
    ),
    ...[
      { date: "2035-02-30" },
      { date: "2036-01-01" },
      { trading_days_to_as_of: "3" },
      { trading_days_to_as_of: -1 },
      { trading_days_to_as_of: 1.5 },
    ].map((change) =>
      bars.map((bar, i) => (i === 2 ? { ...bar, ...change } : bar)),
    ),
  ])
    assert.equal(windowFor(period, badBars, data).verified, false);
  assert.match(windowFor(period, [], data).label, /暂无已留存报价/);
}

// Every subset of the four panes fits the canvas, including short screens.
let layoutCases = 0;
const panes = ["volume", "boll", "kdj", "macd"];
for (let height = 150; height <= 1400; height++) {
  for (let mask = 0; mask < 16; mask++) {
    const selected = panes.filter((_, i) => mask & (1 << i));
    const original = [...selected];
    const layout = layoutFor(height, selected);
    assert.ok(layout.actualHeight >= Math.max(480, height));
    assert.ok(layout.mainHeight >= 300);
    assert.equal(layout.paneHeights.length, selected.length);
    assert.equal(layout.paneTops.length, selected.length);
    let bottom = 24 + layout.mainHeight;
    selected.forEach((key, i) => {
      assert.ok(Math.abs(layout.paneTops[i] - (bottom + 22)) < 1e-8);
      assert.ok(
        layout.paneHeights[i] >=
          { volume: 96, boll: 145, kdj: 104, macd: 104 }[key],
      );
      bottom = layout.paneTops[i] + layout.paneHeights[i];
    });
    assert.ok(
      bottom <= layout.actualHeight - 50 + 1e-8,
      `${height}px ${selected} clipped below canvas`,
    );
    assert.deepEqual(selected, original);
    layoutCases++;
  }
}
const all = layoutFor(620, panes);
const fitted = layoutFor(620, panes, true);
assert.equal(fitted.actualHeight, 620);
assert.ok(fitted.actualHeight < all.actualHeight);
const tiny = layoutFor(360, panes, true);
assert.ok(tiny.mainHeight >= 135);
assert.ok(tiny.paneTops[3] + tiny.paneHeights[3] <= tiny.actualHeight - 50);
assert.equal(all.paneTops.length, 4);
assert.ok(
  all.paneHeights[1] > all.paneHeights[2],
  "BOLL keeps room for its own candles",
);
console.log(
  `Single page chart: ${windowCases} session-boundary cases and ${layoutCases} pane-layout cases passed`,
);
