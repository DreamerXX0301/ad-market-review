import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./hotBrowsePresentation.ts", import.meta.url),
  "utf8",
);
const url =
  "data:text/javascript;base64," +
  Buffer.from(
    ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText,
  ).toString("base64");
const {
  filterHotBrowseRows: filter,
  hotBrowseEndpoint: endpoint,
  hotBrowseScopeMatches: sameScope,
} = await import(url);
const config = { window_sessions: 7, top_n: 10, kind: "all" };
const scope = {
  business_date: "2026-10-23",
  snapshot_id: "generation-a",
  topic_revision_signature: "a".repeat(64),
};
const evidence = {
  config,
  version: "current-hot-universe-v1",
  business_date: scope.business_date,
  snapshot_id: scope.snapshot_id,
  browse_scope: scope,
  complete: true,
  window: { complete: true },
  coverage: { complete: true },
  member_ids: ["600001.SH"],
};
const rows = [
  {
    instrument_id: "600001.SH",
    round_id: "r1",
    entry_basis: { entry_topics: ["old-entry-topic"] },
    current_topics: [{ topic_id: "current-topic" }],
  },
  { instrument_id: "000001.SZ", round_id: "r2" },
  { instrument_id: "600001.SH", round_id: "r3" },
  { instrument_id: "invalid", round_id: "r4" },
];
const original = structuredClone({ rows, scope, config, evidence });
assert.equal(filter(rows, null, scope, config, false, false).rows, rows);
assert.equal(
  endpoint(scope, config, false),
  null,
  "disabled browsing must not request a ranking",
);
const validPath = endpoint(scope, config, true);
assert.ok(validPath.startsWith("/hot-universe?"));
const query = new URL(validPath, "http://test.invalid").searchParams;
assert.deepEqual(Object.fromEntries(query), {
  as_of: scope.business_date,
  snapshot_id: scope.snapshot_id,
  topic_revision_signature: scope.topic_revision_signature,
  window_sessions: "7",
  top_n: "10",
  kind: "all",
});
assert.equal(sameScope(scope, scope), true);
for (const invalid of [
  undefined,
  {},
  { ...scope, business_date: "2026-02-30" },
  { ...scope, snapshot_id: "" },
  { ...scope, topic_revision_signature: "" },
]) {
  assert.equal(endpoint(invalid, config, true), null);
}

const matched = filter(rows, evidence, scope, config, true, false);
assert.deepEqual(
  matched.rows.map((row) => row.round_id),
  ["r1", "r3"],
  "filter all loaded rows before table sorting/page slicing; keep independent rounds",
);
assert.deepEqual(
  [
    matched.matched,
    matched.unmatched,
    matched.unknown,
    matched.observed,
    matched.ready,
  ],
  [2, 1, 1, 2, true],
);
assert.equal(matched.rows[0]._hot_filter_state, "matched");
assert.equal(
  matched.rows[0].entry_basis,
  rows[0].entry_basis,
  "historical entry evidence is carried through unchanged",
);
const included = filter(rows, evidence, scope, config, true, true);
assert.deepEqual(
  included.rows.map((row) => row.round_id),
  ["r1", "r3", "r4"],
);
assert.equal(included.rows.at(-1)._hot_filter_state, "unknown");

for (const bad of [
  null,
  {},
  { ...evidence, business_date: "2026-10-22" },
  { ...evidence, snapshot_id: "generation-b" },
  {
    ...evidence,
    browse_scope: { ...scope, topic_revision_signature: "b".repeat(64) },
  },
  { ...evidence, browse_scope: { ...scope, business_date: "2026-10-22" } },
  { ...evidence, config: { ...config, kind: "industry" } },
  { ...evidence, config: { ...config, top_n: 9 } },
  { ...evidence, config: { ...config, window_sessions: 6 } },
  { ...evidence, version: "old-unverified-version" },
  { ...evidence, member_ids: undefined },
  { ...evidence, member_ids: ["600001.SH", "wrong"] },
]) {
  const kept = filter(rows, bad, scope, config, true, true);
  assert.equal(kept.rows.length, rows.length);
  assert.equal(kept.unknown, rows.length);
  assert.equal(kept.matched + kept.unmatched, 0);
  assert.equal(kept.ready, false);
  assert.equal(filter(rows, bad, scope, config, true, false).rows.length, 0);
}
for (const partial of [
  { ...evidence, complete: false },
  { ...evidence, window: { complete: false } },
  { ...evidence, coverage: { complete: false } },
]) {
  const result = filter(rows, partial, scope, config, true, true);
  assert.equal(result.unknown, 4);
  assert.equal(
    result.matched + result.unmatched,
    0,
    "observed membership cannot certify an incomplete Top N ranking",
  );
  assert.equal(result.observed, 2);
  assert.equal(result.rows[0]._hot_observed_match, true);
  assert.equal(result.observedUnknown, 2);
  const observedOnly = filter(
    rows,
    partial,
    scope,
    config,
    true,
    false,
    "observed",
  );
  assert.deepEqual(
    observedOnly.rows.map((row) => row.round_id),
    ["r1", "r3"],
    "real observed membership can narrow browsing even while complete rank is unknown",
  );
  assert.ok(
    observedOnly.rows.every((row) => row._hot_filter_state === "unknown"),
  );
  const strict = filter(rows, partial, scope, config, true, true, "verified");
  assert.equal(
    strict.rows.length,
    0,
    "strict mode must not display uncertified memberships even with an old include-unknown toggle",
  );
  assert.equal(strict.unknown, 4);
}
const emptyKnown = filter(
  rows,
  { ...evidence, member_ids: [] },
  scope,
  config,
  true,
  true,
);
assert.deepEqual(
  [emptyKnown.matched, emptyKnown.unmatched, emptyKnown.unknown],
  [0, 3, 1],
);
assert.deepEqual(
  { rows, scope, config, evidence },
  original,
  "filtering never edits source rows or frozen receipts",
);
console.log(
  "hot browse: full-list tri-state filtering, all current scope/config guards, incomplete ranking, clear/no-request and original entry evidence passed",
);
