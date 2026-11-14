import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const moduleUrl = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const read = (name) => readFile(new URL(`./${name}.ts`, import.meta.url), "utf8");
const compile = (source) => ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const imports = await Promise.all(["chartSessionDistance", "chartSinceReturn"].map(async (name) => [name, moduleUrl(compile(await read(name)))]));
let source = await read("chartRangeStats");
for (const [name, url] of imports) source = source.replace(`"./${name}"`, `"${url}"`);
const { chartRangeStats } = await import(moduleUrl(compile(source)));

const dates = ["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11"];
const bars = dates.map((date, i) => ({
  date, close: [10, 11, 12, 15][i], high: 11 + i, low: 9 + i,
  volume: 100 + i, amount: 1000 + i, trading_days_to_as_of: 3 - i,
  corporate_action_coverage_known: true,
}));
const data = {
  bars, as_of: dates[3], period: "D", instrument: { kind: "stock" },
  price_basis: "forward_event_affine", adjustment: { anchor: dates[3], usable: true },
  trading_day_distance: { anchor_date: dates[3], basis: "market_calendar", state: "available" },
};
const stats = chartRangeStats(data, [dates[2], dates[0]], "D");
assert.deepEqual([stats.start, stats.end, stats.sessions, stats.count], [dates[0], dates[2], 2, 3]);
assert.ok(Math.abs(stats.change.value - 20) < 1e-10);
assert.deepEqual([stats.high, stats.low, stats.volume, stats.turnover], [13, 9, 303, 3003]);

const gap = chartRangeStats({ ...data, bars: [bars[0], bars[2], bars[3]] }, [dates[0], dates[2]], "D");
assert.ok(gap.gaps && gap.change.value !== null);
assert.deepEqual([gap.high, gap.low, gap.volume, gap.turnover], [null, null, null, null]);

const raw = chartRangeStats({ ...data, price_basis: "none", bars: [bars[0], { ...bars[1], corporate_action: true, volume: null }, ...bars.slice(2)] }, [dates[0], dates[2]], "D");
assert.deepEqual([raw.change.value, raw.high, raw.volume, raw.turnover], [null, null, null, 3003]);
assert.equal(chartRangeStats({ ...data, bars: [bars[0], { ...bars[1], complete: false }] }, [dates[0], dates[1]], "D"), null);
assert.equal(chartRangeStats(data, [dates[0], dates[0]], "D"), null);
