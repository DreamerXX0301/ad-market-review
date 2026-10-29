import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./auctionAssociationCloseStats.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { auctionAssociationCloseStats: stats } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const date = "2026-10-29";
const codes = ["600001.SH", "000001.SZ", "830001.BJ", "688001.SH"];
const group = { instrument_ids: codes.slice(0, 3), average_auction_pct: 999 };
const row = (index, close_pct, state = "observed") => ({
  instrument_id: codes[index], close_pct, state,
});
const view = (items, changes = {}) => ({
  version: "auction-close-review-v1", date, state: "available", items, ...changes,
});
const close = view([row(0, 0), row(1, 6), row(2, null), row(3, 100)]);
assert.deepEqual(stats(group, close, date), { average: 3, valid: 2, requested: 3 },
  "zero is valid; missing members and unrelated rows cannot dilute the mean");
for (const state of ["available", "partial", "unknown"])
  assert.deepEqual(stats(group, { ...close, state }, date), { average: 3, valid: 2, requested: 3 });
for (const state of ["pending", "purged", "not_collected", "failed", "running", undefined, { toString: () => "available" }])
  assert.deepEqual(stats(group, { ...close, state }, date), { average: null, valid: 0, requested: 3 },
    "noncomparable view states reject embedded numeric decoys");
for (const bad of [null, undefined, {}, { ...close, date: "2026-10-28" },
  { ...close, version: "other" }, { ...close, items: null }, { ...close, items: {} }])
  assert.deepEqual(stats(group, bad, date), { average: null, valid: 0, requested: 3 });
assert.deepEqual(stats(group, { ...close, date: "" }, ""), { average: null, valid: 0, requested: 3 });

for (const bad of [null, undefined, "6", true, false, NaN, Infinity, -Infinity])
  assert.deepEqual(stats(group, view([row(0, 0), row(1, bad)]), date),
    { average: 0, valid: 1, requested: 3 }, "invalid numeric values never become zero or a coerced number");
for (const state of ["mismatch", "unknown", "pending", undefined])
  assert.deepEqual(stats(group, view([row(0, 0), { ...row(1, 99), state }]), date),
    { average: 0, valid: 1, requested: 3 }, "only comparable observed rows enter close statistics");
assert.deepEqual(stats(group, view([row(0, 0), { ...row(1, 99), open_check: "mismatch" }]), date),
  { average: 0, valid: 1, requested: 3 }, "a contradictory opening-mismatch flag cannot become a comparable close");
assert.deepEqual(stats(group, view([row(0, -3), row(1, 3), row(2, 0)]), date),
  { average: 0, valid: 3, requested: 3 });
assert.deepEqual(stats(group, view([]), date), { average: null, valid: 0, requested: 3 });
assert.deepEqual(stats(group, view([row(3, 999)]), date), { average: null, valid: 0, requested: 3 });

for (const duplicate of [row(0, 999), row(0, null, "unknown"), row(0, -999, "mismatch")]) {
  const values = [row(0, 8), row(1, 0), duplicate];
  assert.deepEqual(stats(group, view(values), date), { average: 0, valid: 1, requested: 3 });
  assert.deepEqual(stats(group, view([...values].reverse()), date), { average: 0, valid: 1, requested: 3 },
    "duplicate identity isolation cannot depend on row order or choose the last observed row");
}
assert.deepEqual(stats({ instrument_ids: [codes[0], codes[0], codes[1]] }, close, date),
  { average: 3, valid: 2, requested: 2 }, "original membership duplicates count once");
for (const ids of [undefined, null, [], {}, ["600001"], ["600001.sh"],
  ["600001.SH "], ["881001.TI"], [codes[0], null], Array(101).fill(codes[0])])
  assert.deepEqual(stats({ instrument_ids: ids, items: [row(0, 9)] }, close, date),
    { average: null, valid: 0, requested: 0 }, "missing or invalid members are never guessed from other group fields");
assert.deepEqual(stats(group, view([row(0, 7), ...Array(100).fill(row(3, 99))]), date),
  { average: null, valid: 0, requested: 3 }, "the 100-row limit applies before intersection or deduplication");
assert.deepEqual(stats(group, view([null, false, [], {}, row(0, 0)]), date),
  { average: 0, valid: 1, requested: 3 });

const hundred = Array.from({ length: 100 }, (_, index) => `${600001 + index}.SH`);
const hundredRows = hundred.map(instrument_id => ({ instrument_id, state: "observed", close_pct: Number.MAX_VALUE }));
assert.deepEqual(stats({ instrument_ids: hundred }, view(hundredRows), date),
  { average: Number.MAX_VALUE, valid: 100, requested: 100 }, "a finite equal-weight mean must not overflow while summing");
assert.deepEqual(stats(group, view([row(0, 1e308), row(1, -1e308), row(2, 0)]), date),
  { average: 0, valid: 3, requested: 3 });

function deepFreeze(value) {
  if (value && typeof value === "object") {
    Object.freeze(value);
    for (const item of Object.values(value)) deepFreeze(item);
  }
  return value;
}
const originalGroup = structuredClone(group), originalClose = structuredClone(close);
deepFreeze(group); deepFreeze(close);
const oldFetch = globalThis.fetch;
globalThis.fetch = () => assert.fail("pure association statistics cannot fetch");
try {
  assert.deepEqual(stats(group, close, date), { average: 3, valid: 2, requested: 3 });
} finally {
  globalThis.fetch = oldFetch;
}
assert.deepEqual(group, originalGroup); assert.deepEqual(close, originalClose);
console.log("Auction association close stats: dated scope, comparable finite means, coverage, duplicate isolation, capacity and immutable inputs passed");
