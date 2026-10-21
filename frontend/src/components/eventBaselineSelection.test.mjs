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
