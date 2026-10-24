import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(new URL("./stockPatternHeader.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ES2022}}).outputText;
const {stockPatternHeader} = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const event = {date: "2026-01-09", bar_date: "2026-01-09", snapshot_id: "generation-a", kind: "one_price_limit_up"};
const data = {instrument: {kind: "stock"}, date: "2026-01-09", data_date: "2026-01-09", pattern_as_of: "2026-01-09", pattern_date: "2026-01-09", snapshot_id: "generation-a", pattern_events: [event], pattern_status: {one_price_limit_up: {state: true}}};
assert.deepEqual(stockPatternHeader(data).pattern_events, [event]);
assert.equal(stockPatternHeader(data, "2026-01-08"), undefined);
assert.equal(stockPatternHeader({...data, date: "2026-01-08"}), undefined);
assert.equal(stockPatternHeader({...data, pattern_date: null}), undefined);
assert.equal(stockPatternHeader({...data, pattern_as_of: "2026-01-08"}), undefined);
assert.equal(stockPatternHeader({...data, snapshot_id: undefined}), undefined);
assert.equal(stockPatternHeader({...data, instrument: {kind: "index"}}), undefined);
assert.equal(stockPatternHeader(undefined), undefined);
for (const bad of [{snapshot_id: "old"}, {date: "2026-01-08"}, {bar_date: "2026-01-08"}]) {
  assert.deepEqual(stockPatternHeader({...data, pattern_events: [{...event, ...bad}]}).pattern_events, []);
}
assert.equal(stockPatternHeader({...data, pattern_events: null}).pattern_events.length, 0);
assert.deepEqual(stockPatternHeader({...data, pattern_events: [null, false, "invalid", event]}).pattern_events, [event]);
const chart = await fs.readFile(new URL("./Charts.tsx", import.meta.url), "utf8");
assert.match(chart, /PatternStatus bar=\{stockPatternHeader\(detailData, asOf\)\}/);
console.log("stockPatternHeader selected-day/snapshot guards passed");
