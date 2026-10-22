import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(new URL("./eventBaselineSelection.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { resolveStockEventBaseline: resolve } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const rounds = [
  { round_id: "new", entry_business_date: "2026-10-21", entry_price: 9.78, entry_basis: { price_basis: "none" } },
  { round_id: "old", entry_business_date: "2026-10-20", entry_price: 8.89, entry_basis: { price_basis: "none" } },
];
const explicit = { roundId: "old", date: "2026-10-20", rawClose: 8.89, priceBasis: "none", label: "事件日收盘" };
assert.equal(resolve(rounds, "old", explicit), explicit);
assert.equal(resolve(rounds, undefined, explicit), explicit);
assert.equal(resolve(rounds, "new", explicit).rawClose, 9.78);
assert.equal(resolve(rounds, "none", explicit), undefined);
assert.equal(resolve(rounds, "missing", explicit), undefined);
assert.equal(resolve([], "old"), undefined);
assert.deepEqual(resolve(rounds, "old"), explicit);
// Empty references for a different stock must not inherit a previous selection.
assert.equal(resolve([], "new"), undefined);
const contextOnly = { date: "2026-10-20", rawClose: 8.89, priceBasis: "none" };
assert.equal(resolve(rounds, "context", contextOnly), contextOnly);
const { resolveStockHistoricalReference: historyReference } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const navChecks = [{ selected_event: { date: "2026-10-01" } }];
const historicalRounds = [
  { round_id: "A", historical_event_checks: [{ selected_event: { date: "2026-09-15" } }] },
  { round_id: "B", historical_event_checks: [{ selected_event: { date: "2026-09-30" } }] },
  { round_id: "empty", historical_event_checks: [] },
];
assert.equal(historyReference(historicalRounds, "A", navChecks, false).checks, navChecks, "initial result context wins over the API default round");
assert.equal(historyReference(historicalRounds, "B", navChecks, true).checks, historicalRounds[1].historical_event_checks, "explicit round B replaces navigation A atomically");
assert.deepEqual(historyReference(historicalRounds, "empty", navChecks, true).checks, [], "empty B must never inherit A");
assert.equal(historyReference(historicalRounds, "missing", navChecks, true).checks, undefined, "missing selected proof is not inferred from navigation");
assert.equal(historyReference(historicalRounds, "B", undefined, false).checks, historicalRounds[1].historical_event_checks, "global search uses the chosen stored round");
assert.equal(historyReference(historicalRounds, "B", navChecks, false).checks, navChecks, "a new navigation scope resets explicit choice");
