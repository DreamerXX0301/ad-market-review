import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = ts.transpileModule(
  await fs.readFile(new URL("./memberDailyLimit.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const { memberHasConfirmedDailyLimit: confirmed } = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);

const day = "2026-10-27";
// Synthetic proof fixtures are confined to tests, never used as product data.
const proof = Object.freeze({
  complete: true,
  state: "confirmed_latest",
  since: day,
  until: day,
  covered_days: 1,
  expected_days: 1,
  unknown_days: 0,
});
const row = Object.freeze({
  quote_available: true,
  quote_date: day,
  close: 10,
  last_limit_up_date: day,
  last_limit_up_complete: true,
  sessions_since_limit_up: 0,
  last_limit_up_coverage: proof,
  limit_up_ytd: null,
  limit_up_ytd_observed: 1,
});
assert.equal(
  confirmed(row, day),
  true,
  "partial yearly coverage does not erase a confirmed daily event",
);
assert.equal(
  confirmed({ ...row, change_pct: null }, day),
  true,
  "the event is not derived from a percentage",
);
for (const change_pct of [4.99, 9.99, 10, 19.99, 20, 30]) {
  assert.equal(
    confirmed({ change_pct, quote_date: day, close: 10 }, day),
    false,
  );
}
for (const patch of [
  { quote_available: false },
  { quote_available: "true" },
  { quote_date: "2026-10-26" },
  { quote_date: "2026-10-28" },
  { quote_date: null },
  { close: null },
  { close: 0 },
  { close: -1 },
  { close: Infinity },
  { close: "10" },
  { last_limit_up_date: "2026-10-26" },
  { last_limit_up_date: "2026-10-28" },
  { last_limit_up_complete: false },
  { last_limit_up_complete: "true" },
  { sessions_since_limit_up: 1 },
  { sessions_since_limit_up: null },
  { sessions_since_limit_up: "0" },
  { last_limit_up_coverage: null },
  { last_limit_up_coverage: [] },
]) {
  assert.equal(
    confirmed({ ...row, ...patch }, day),
    false,
    JSON.stringify(patch),
  );
}
for (const patch of [
  { complete: false },
  { state: "latest_observed" },
  { state: "no_confirmed_event" },
  { since: "2026-10-26" },
  { until: "2026-10-28" },
  { unknown_days: 1 },
  { covered_days: 0 },
  { expected_days: 2 },
]) {
  assert.equal(
    confirmed({ ...row, last_limit_up_coverage: { ...proof, ...patch } }, day),
    false,
    JSON.stringify(patch),
  );
}
for (const invalidDay of ["", "2026-10-2", "2026-02-30", "2026-13-01"]) {
  assert.equal(confirmed(row, invalidDay), false);
}
assert.equal(
  confirmed(row, "2026-10-26"),
  false,
  "future events do not annotate an older view",
);
assert.equal(
  confirmed(row, "2026-10-28"),
  false,
  "old events do not annotate the next view",
);
assert.deepEqual(row.last_limit_up_coverage, proof);
console.log(
  "Daily member limit badge: dated source proof, annual partial coverage, unknown/missing quotes and future/past isolation passed",
);
