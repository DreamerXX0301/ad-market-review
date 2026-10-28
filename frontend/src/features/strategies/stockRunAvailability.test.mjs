import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const output = ts.transpileModule(await fs.readFile(new URL("./stockRunAvailability.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { stockRunGate: gate, stockRunnableStrategies: runnable, stockSelectableStrategies: selectable } = await import("data:text/javascript;base64," + Buffer.from(output).toString("base64"));
const completed = Object.freeze({ strategy_id: "one", version: 3, run_availability: { state: "completed", can_run: false, business_date: "2026-10-28", completed_run_id: "saved", reason: "今天已运行" } });
const ready = Object.freeze({ ...completed, strategy_id: "two", run_availability: { state: "ready", can_run: true, business_date: "2026-10-28", reason: null } });
const unavailable = Object.freeze({ ...ready, strategy_id: "three", run_availability: { state: "unavailable", can_run: false, reason: "尚无已发布行情" } });
assert.equal(gate(completed, "2026-10-27").completed, true, "view date cannot override the server business day");
assert.equal(gate(completed).blocked, false, "completed results remain accessible without another computation");
assert.equal(gate(completed).runId, "saved");
assert.equal(gate(ready).blocked, false);
assert.equal(gate(unavailable).blocked, true);
assert.deepEqual(runnable([completed, ready, unavailable, { ...ready, archived: true }]).map(x => x.strategy_id), ["two"]);
assert.deepEqual(selectable([completed, ready, unavailable]).map(x => x.strategy_id), ["one", "two"]);
assert.equal(gate({ ...ready, archived: true }).blocked, true);
const saved = { strategy_id: "one", strategy_version: 3, business_date: "2026-10-28", status: "completed", run_id: "receipt" };
const legacy = { strategy_id: "one", version: 3 };
assert.equal(gate(legacy, "2026-10-28", saved).completed, true);
assert.equal(gate({ ...legacy, version: 4 }, "2026-10-28", saved).blocked, false);
assert.equal(gate(legacy, "2026-10-29", saved).blocked, false);
assert.equal(gate(legacy, "2026-10-28", { ...saved, status: "failed" }).blocked, false);
assert.equal(gate(legacy, "2026-10-28", { ...saved, strategy_id: "another" }).blocked, false);
assert.equal(gate(ready, "2026-10-28", { ...saved, strategy_id: "two" }).blocked, false, "new server input availability outranks an old saved receipt");
console.log("Stock run availability: authoritative business-day guard, rule/input refresh, legacy receipt isolation and batch eligibility passed");
