import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = ts.transpileModule(
  await fs.readFile(new URL("./savedRunCache.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  savedRunPath,
  savedStrategyScope,
  savedRunPart,
  readSavedRun,
  rememberSavedRun,
  invalidateSavedStrategy,
  clearSavedRunCache,
  savedRunCacheStats,
} = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
assert.equal(
  savedRunPath("stock", "id/with space", "unknown"),
  "/strategy-runs/id%2Fwith%20space?decision=unknown",
);
assert.equal(
  savedRunPath("sector", "s", "matched"),
  "/sector-strategy-runs/s?decision=matched",
);
const scope = savedStrategyScope(
  "stock",
  { strategy_id: "a", version: 1, latest_run: { run_id: "one" } },
  "day-a",
);
assert.notEqual(
  scope,
  savedStrategyScope(
    "stock",
    { strategy_id: "a", version: 2, latest_run: { run_id: "one" } },
    "day-a",
  ),
);
assert.notEqual(
  scope,
  savedStrategyScope(
    "stock",
    { strategy_id: "a", version: 1, latest_run: { run_id: "one" } },
    "day-b",
  ),
);
const run = (id, strategy = "a") => ({
  strategy_id: strategy,
  run_id: id,
  matched_count: 1,
  unknown_count: 8,
  unmatched_count: 5000,
  candidates: [
    { decision: "matched", instrument_id: "one" },
    { decision: "unknown", instrument_id: "two" },
  ],
});
const part = savedRunPart(run("one"), "matched");
assert.deepEqual(
  part.items.map((row) => row.instrument_id),
  ["one"],
);
assert.equal(part.unknown_count, 8);
assert.equal(part.unmatched_count, 5000);
assert.equal("candidates" in part, false);
clearSavedRunCache();
rememberSavedRun("stock", run("one"), "matched", scope, 100);
assert.equal(readSavedRun("stock", "one", "matched", scope, 200).run_id, "one");
assert.equal(readSavedRun("stock", "one", "unknown", scope, 200), null);
assert.equal(readSavedRun("sector", "one", "matched", scope, 200), null);
assert.equal(
  readSavedRun("stock", "one", "matched", "changed-inputs", 200),
  null,
);
rememberSavedRun("stock", run("one"), "matched", scope, 100);
assert.equal(readSavedRun("stock", "one", "matched", scope, 60101), null);
for (let index = 0; index < 30; index++)
  rememberSavedRun("stock", run("id-" + index), "matched", scope, 100);
assert.ok(savedRunCacheStats().entries <= 24);
assert.equal(readSavedRun("stock", "id-0", "matched", scope, 200), null);
assert.ok(readSavedRun("stock", "id-29", "matched", scope, 200));
rememberSavedRun("sector", run("sector", "a"), "matched", scope, 100);
rememberSavedRun("stock", run("other", "b"), "matched", scope, 100);
invalidateSavedStrategy("stock", "a");
assert.ok(readSavedRun("sector", "sector", "matched", scope, 200));
assert.ok(readSavedRun("stock", "other", "matched", scope, 200));
const huge = {
  ...run("huge"),
  items: [{ decision: "matched", payload: "x".repeat(5 * 1024 * 1024) }],
};
rememberSavedRun("stock", huge, "matched", scope, 100);
assert.equal(readSavedRun("stock", "huge", "matched", scope, 200), null);
assert.ok(savedRunCacheStats().bytes <= savedRunCacheStats().maxBytes);
const many = {
  ...run("many"),
  items: Array.from({ length: 30001 }, () => ({ decision: "matched" })),
};
rememberSavedRun("stock", many, "matched", scope, 100);
assert.equal(readSavedRun("stock", "many", "matched", scope, 200), null);
assert.ok(
  savedRunCacheStats().candidates <= savedRunCacheStats().maxCandidates,
);
clearSavedRunCache();
assert.deepEqual(savedRunCacheStats(), {
  entries: 0,
  bytes: 0,
  candidates: 0,
  maxEntries: 24,
  maxBytes: 8388608,
  maxCandidates: 30000,
});
console.log(
  "Saved run memory cache: decision/target/scope isolation, full counts, LRU/bytes/rows/TTL limits and targeted invalidation passed",
);
