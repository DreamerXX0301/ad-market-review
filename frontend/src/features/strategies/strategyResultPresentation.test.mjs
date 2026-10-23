import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./strategyResultPresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  readStrategySelection: read,
  rememberStrategySelection: remember,
  resolvedStrategyId: resolve,
  savedRunNotice: notice,
  strategyDecisionCounts,
  nextDecisionTab,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, value),
};
remember("stock", "stock-2");
remember("sector", "sector-1");
remember("target", "sector");
assert.equal(read("stock"), "stock-2");
assert.equal(read("sector"), "sector-1");
assert.equal(read("target"), "sector");
assert.deepEqual(
  [...storage.values()],
  ["stock-2", "sector-1", "sector"],
  "only non-sensitive selection IDs are persisted",
);
assert.equal(
  resolve(
    [{ strategy_id: "stock-1" }, { strategy_id: "stock-2" }],
    read("stock"),
  ),
  "stock-2",
);
assert.equal(
  resolve([{ strategy_id: "stock-1" }], "deleted-strategy"),
  "stock-1",
);
assert.equal(resolve([], "stock-2"), "");
const run = {
  strategy_id: "stock-2",
  business_date: "2026-10-20",
  strategy_version: 7,
};
assert.equal(notice(null, { version: 7 }, "2026-10-20"), null);
const current = notice(run, { version: 7 }, "2026-10-20");
assert.equal(current.stale, false);
assert.equal(
  current.attention,
  false,
  "a current result does not repeat metadata in a full-width notice",
);
assert.match(current.context, /已保存结果.*2026-10-20.*V7/);
const dated = notice(run, { version: 7 }, "2026-10-21");
assert.equal(dated.stale, true);
assert.match(dated.message, /当前行情日 2026-10-21/);
const edited = notice(run, { version: 8 }, "2026-10-20");
assert.match(edited.message, /当前规则 V8/);
assert.match(
  notice(run, { version: 8 }, "2026-10-21", true).message,
  /上次已保存/,
);
assert.equal(dated.attention, true);
assert.deepEqual(
  strategyDecisionCounts({
    matched_count: 0,
    counts: { matched: 9, unknown: 2 },
    unmatched_count: 100,
    items: [],
  }),
  { matched: 0, unknown: 2, unmatched: 100 },
);
assert.deepEqual(
  strategyDecisionCounts({
    items: [
      { decision: "matched" },
      { decision: "unknown" },
      { decision: "unmatched" },
      { decision: "unknown" },
    ],
  }),
  { matched: 1, unknown: 2, unmatched: 1 },
);
assert.deepEqual(strategyDecisionCounts({}), {
  matched: null,
  unknown: null,
  unmatched: null,
});
assert.equal(strategyDecisionCounts({ matched_count: "5" }).matched, null);
assert.equal(nextDecisionTab(2, "ArrowRight"), 0);
assert.equal(nextDecisionTab(0, "ArrowLeft"), 2);
assert.equal(nextDecisionTab(1, "Home"), 0);
assert.equal(nextDecisionTab(1, "End"), 2);
assert.equal(nextDecisionTab(1, "Enter"), null);
globalThis.localStorage = {
  getItem() {
    throw Error("blocked");
  },
  setItem() {
    throw Error("blocked");
  },
};
assert.equal(read("stock"), "");
assert.doesNotThrow(() => remember("stock", "stock-2"));
console.log(
  "Saved strategy results: isolated preferences, deleted selection fallback, dated/versioned history and retained prior result passed",
);
