import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

async function load(name) {
  const source = await fs.readFile(new URL(name, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );
}
const {
  sectorExplorerId: identity,
  sectorExplorerGroups: groups,
  sectorExplorerRows: prepare,
  sectorExplorerDefaultSort: defaultSort,
  sectorBrokenBoard: brokenView,
} = await load("./sectorExplorerPresentation.ts");
const { sortMarketRows: sort } = await load("./marketPresentation.ts");

const base = {
  kind: "concept",
  source: "fuyao",
  quote_source: "fuyao",
  normalization_version: "test-normalization-v1",
  quote_basis: "provider_index",
  quote_date: "2026-10-27",
};
const row = (code, extra = {}) => ({
  ...base,
  topic_id: "fuyao-" + code,
  topic_code: code,
  name: "合成题材" + code,
  ...extra,
});
const activity = (extra = {}) => ({
  source: "fuyao",
  quote_basis: "provider_index",
  group_kind: "concept",
  group_level: null,
  metric: "change_pct",
  expected_days: 7,
  observed_days: 7,
  complete: true,
  state: "complete",
  count: 2,
  window_end: "2026-10-27",
  ...extra,
});
const decorated = (extra = {}, metric = "change_pct") =>
  prepare([row("885001.TI", extra)], {
    kind: "concept",
    activityMetric: metric,
  })[0];

// The UI key is source/code scoped and survives a presentation-only rename.
assert.equal(
  identity(row("885001.TI")),
  identity(row("885001.TI", { name: "改名" })),
);
assert.notEqual(
  identity(row("885001.TI")),
  identity(row("885001.TI", { source: "other", quote_source: "other" })),
);
assert.notEqual(
  identity(row("885001.TI")),
  identity(row("885002.TI", { name: "同名" })),
);

// No combined ranking across provider, normalization, quote basis or level.
const universes = [
  row("885001.TI"),
  row("885002.TI"),
  row("885003.TI", { source: "other", quote_source: "other" }),
  row("885004.TI", { normalization_version: "test-normalization-v2" }),
  row("885005.TI", { quote_basis: "member_sample" }),
  row("885006.TI", { source: null, quote_source: null }),
  row("885007.TI", { normalization_version: null }),
  row("885008.TI", { normalization_version: null }),
  row("885009.TI", { source: "conflicting-provider" }),
  row("881001.TI", { kind: "industry", classification_level: 1 }),
  row("881002.TI", { kind: "industry", classification_level: 2 }),
  row("881003.TI", { kind: "industry" }),
];
const concepts = groups(universes, "concept");
assert.equal(concepts.length, 8);
assert.equal(concepts[0].verified, true);
assert.equal(concepts.find((g) => g.count === 2)?.source, "fuyao");
assert.deepEqual(
  prepare(universes, { kind: "concept" }).map((r) => r.topic_code),
  ["885001.TI", "885002.TI"],
);
assert.deepEqual(
  prepare(universes, { kind: "concept", groupKey: "obsolete-scope" }).map(
    (r) => r.topic_code,
  ),
  ["885001.TI", "885002.TI"],
);
const industries = groups(universes, "industry");
assert.equal(industries.length, 3);
assert.deepEqual(
  new Set(industries.map((g) => g.level)),
  new Set(["1", "2", null]),
);
assert.match(industries.find((g) => g.level === null).label, /层级未知/);
for (const group of industries)
  assert.equal(
    prepare(universes, { kind: "industry", groupKey: group.key }).length,
    1,
  );

// Search is deliberate name/code matching, not a hidden proof/JSON search.
const prices = [
  row("885001.TI", { name: "减速器", change_pct: 2, reason: "隐藏词" }),
  row("885002.TI", { name: "机器人", change_pct: -3 }),
  row("885003.TI", { change_pct: 0 }),
  row("885004.TI", { change_pct: null }),
  row("885005.TI", { change_pct: NaN }),
  row("885006.TI", { change_pct: Infinity }),
  row("885007.TI", { change_pct: "8" }),
];
assert.equal(prepare(prices, { kind: "concept", mode: "all" }).length, 7);
assert.deepEqual(
  prepare(prices, { kind: "concept", mode: "rising" }).map((r) => r.topic_code),
  ["885001.TI"],
);
assert.deepEqual(
  prepare(prices, { kind: "concept", mode: "falling" }).map(
    (r) => r.topic_code,
  ),
  ["885002.TI"],
);
assert.equal(prepare(prices, { kind: "concept", query: "  减速  " }).length, 1);
assert.equal(
  prepare(prices, { kind: "concept", query: "885002.ti" }).length,
  1,
);
assert.equal(prepare(prices, { kind: "concept", query: "隐藏词" }).length, 0);
assert.equal(
  prepare(prices, { kind: "concept", query: "change_pct" }).length,
  0,
);
const noMembers = decorated({
  members_available: false,
  member_count: null,
  change_pct: 4,
});
assert.equal(
  noMembers.change_pct,
  4,
  "real index quotes do not depend on historic members",
);

// Exact zero and incomplete observation are materially different facts.
let result = decorated({
  sector_activity_7d: { change_pct: activity({ count: 0 }) },
});
assert.equal(result._sector_activity_count, 0);
assert.equal(result._sector_activity_complete, true);
assert.equal(result._sector_activity_label, "近7日0次");
result = decorated({
  sector_activity_7d: {
    change_pct: activity({
      state: "observed_partial",
      complete: false,
      observed_days: 4,
      count: 2,
    }),
  },
});
assert.equal(result._sector_activity_count, 2);
assert.equal(result._sector_activity_state, "observed_partial");
assert.match(result._sector_activity_label, /已观察2次.*样本不完整/);
assert.doesNotMatch(result._sector_activity_label, /至少|≥/);
for (const invalid of [
  { count: null },
  { count: -1 },
  { count: 1.5 },
  { count: 8 },
  { count: true },
  { expected_days: 10 },
  { observed_days: 1, count: 2 },
  { complete: false },
  { state: "unknown" },
  { source: "other" },
  { group_kind: "industry" },
  { quote_basis: "member_sample" },
  { metric: "amount" },
  { window_end: "2026-10-28" },
  { state: "observed_partial", complete: false, observed_days: 4, count: 0 },
]) {
  assert.equal(
    decorated({ sector_activity_7d: { change_pct: activity(invalid) } })
      ._sector_activity_count,
    null,
    JSON.stringify(invalid),
  );
}
assert.equal(
  decorated({
    normalization_version: null,
    sector_activity_7d: { change_pct: activity() },
  })._sector_activity_count,
  null,
);
assert.equal(
  decorated({
    quote_basis: "member_sample",
    sector_activity_7d: { change_pct: activity() },
  })._sector_activity_count,
  null,
);
const dual = {
  sector_activity_7d: {
    change_pct: activity({ count: 2 }),
    amount: activity({ metric: "amount", count: 5 }),
  },
};
assert.equal(decorated(dual)._sector_activity_count, 2);
assert.equal(decorated(dual, "amount")._sector_activity_count, 5);
const industryActivity = row("881001.TI", {
  kind: "industry",
  level: 1,
  sector_activity_7d: {
    change_pct: activity({ group_kind: "industry", group_level: 2 }),
  },
});
assert.equal(
  prepare([industryActivity], { kind: "industry" })[0]._sector_activity_count,
  null,
);

// Only integral, non-negative, internally consistent observed counts yield a ratio.
assert.equal(
  decorated({ up_count: 3, observed_count: 4 })._sector_up_ratio,
  75,
);
assert.equal(decorated({ up_count: 0, observed_count: 4 })._sector_up_ratio, 0);
for (const [up_count, observed_count] of [
  [1, 0],
  [5, 4],
  [-1, 4],
  [1.5, 4],
  [true, 4],
  [1, "4"],
  [1, Infinity],
  [null, 4],
])
  assert.equal(decorated({ up_count, observed_count })._sector_up_ratio, null);
assert.equal(
  decorated({ limit_up_count: null, limit_up_observed_count: 99 })
    ._sector_limit_sort,
  null,
);
assert.equal(decorated({ limit_up_count: 0 })._sector_limit_sort, 0);

// The existing whole-result sorter preserves unknown-last for both directions.
const ranked = [0, 2, 7, null].map((n, i) =>
  row("88500" + i + ".TI", {
    sector_activity_7d: { change_pct: activity({ count: n }) },
  }),
);
const prepared = prepare(ranked, { kind: "concept" });
assert.deepEqual(
  sort(prepared, defaultSort("activity")).map((r) => r._sector_activity_count),
  [7, 2, 0, null],
);
assert.deepEqual(
  sort(prepared, { key: "_sector_activity_count", order: "asc" }).map(
    (r) => r._sector_activity_count,
  ),
  [0, 2, 7, null],
);
assert.deepEqual(defaultSort("falling"), { key: "change_pct", order: "asc" });
assert.deepEqual(defaultSort("limit"), {
  key: "_sector_limit_sort",
  order: "desc",
});
assert.deepEqual(defaultSort("amount"), { key: "amount", order: "desc" });

// Broken-board facts are dated backend results, not inferred from opening counts.
const broken = (extra = {}) => ({
  date: base.quote_date,
  snapshot_id: "generation-1",
  state: "complete",
  limit_up_count: 3,
  broken_count: 2,
  touch_count: 5,
  broken_rate_pct: 40,
  observed_limit_up_count: 3,
  observed_broken_count: 2,
  membership_date: "2026-10-21",
  details: [],
  ...extra,
});
let brokenResult = brokenView(
  row("885001.TI", { broken_board: broken() }),
  base.quote_date,
  "generation-1",
);
assert.equal(brokenResult.state, "complete");
assert.equal(brokenResult.broken_count, 2);
assert.equal(brokenResult.broken_rate_pct, 40);
assert.equal(brokenResult.membership_date, "2026-10-21");
brokenResult = brokenView(
  row("885001.TI", {
    broken_board: broken({ state: "partial", observed_broken_count: 9 }),
  }),
  base.quote_date,
  "generation-1",
);
assert.equal(brokenResult.state, "partial");
assert.equal(brokenResult.broken_count, null);
assert.equal(brokenResult.broken_rate_pct, null);
assert.equal(brokenResult.observed_broken_count, 9);
assert.equal(
  brokenView(row("885001.TI", { open_times: 5, limit_up_count: 20 }))
    .broken_count,
  null,
);
const noAttempts = brokenView(
  row("885001.TI", {
    broken_board: broken({
      limit_up_count: 0,
      broken_count: 0,
      touch_count: 0,
      broken_rate_pct: null,
    }),
  }),
);
assert.equal(noAttempts.broken_count, 0);
assert.equal(noAttempts.broken_rate_pct, null);
for (const invalid of [
  { date: "2026-10-26" },
  { snapshot_id: "older-generation" },
  { date: null },
  { state: "unknown", reason: "成员缺档" },
  { broken_count: -1 },
  { broken_count: true },
  { touch_count: 6 },
  { broken_rate_pct: 50 },
  { broken_rate_pct: Infinity },
]) {
  const stale = brokenView(
    row("885001.TI", { broken_board: broken(invalid) }),
    base.quote_date,
    "generation-1",
  );
  assert.equal(stale.state, "unknown", JSON.stringify(invalid));
  assert.equal(stale.broken_count, null);
  assert.equal(stale.broken_rate_pct, null);
  assert.deepEqual(stale.details, []);
}
assert.equal(
  brokenView(
    row("885001.TI", { broken_board: broken() }),
    "2026-10-28",
    "generation-1",
  ).state,
  "unknown",
);
const sample = (id, extra = {}) => ({
  instrument_id: id,
  date: base.quote_date,
  snapshot_id: "generation-1",
  outcome: "broken",
  ...extra,
});
const details = [
  sample("600001.SH"),
  sample("600001.SH"),
  sample("600002.SH", { date: "2026-10-26" }),
  sample("600003.SH", { snapshot_id: "old" }),
  sample("600004.SH", { outcome: "unknown" }),
  sample("600005.SH"),
  sample("600005.SH", { outcome: "limit_up" }),
  sample("600006.SH", { outcome: "limit_up" }),
];
assert.deepEqual(
  brokenView(
    row("885001.TI", { broken_board: broken({ details }) }),
  ).details.map((item) => item.instrument_id),
  ["600001.SH", "600006.SH"],
);
const brokenRows = [
  row("885001.TI", {
    broken_board: broken({
      limit_up_count: 0,
      broken_count: 10,
      touch_count: 10,
      broken_rate_pct: 100,
    }),
  }),
  row("885002.TI", { broken_board: broken() }),
  row("885003.TI", {
    broken_board: broken({ state: "partial", observed_broken_count: 99 }),
  }),
  row("885004.TI", {
    broken_board: broken({
      limit_up_count: 0,
      broken_count: 0,
      touch_count: 0,
      broken_rate_pct: null,
    }),
  }),
  row("885005.TI", { broken_board: broken({ date: "2026-10-26" }) }),
];
const preparedBroken = prepare(brokenRows, {
  kind: "concept",
  mode: "broken",
  observationDate: base.quote_date,
  snapshotId: "generation-1",
});
assert.equal(
  preparedBroken.length,
  5,
  "missing membership or observations do not remove real index rows",
);
assert.deepEqual(defaultSort("broken"), {
  key: "_sector_broken_sort",
  order: "desc",
});
assert.deepEqual(
  sort(preparedBroken, defaultSort("broken")).map(
    (item) => item._sector_broken_sort,
  ),
  [10, 2, 0, null, null],
);
assert.deepEqual(
  sort(preparedBroken, { key: "_sector_broken_sort", order: "asc" }).map(
    (item) => item._sector_broken_sort,
  ),
  [0, 2, 10, null, null],
);
assert.deepEqual(
  sort(preparedBroken, { key: "_sector_broken_rate_sort", order: "desc" }).map(
    (item) => item._sector_broken_rate_sort,
  ),
  [100, 40, null, null, null],
);
assert.equal(
  prepare(brokenRows, {
    kind: "concept",
    observationDate: "2026-10-28",
    snapshotId: "generation-1",
  }).every((item) => item._sector_broken_sort === null),
  true,
);
const frozenBroken = Object.freeze(broken());
assert.equal(
  brokenView(Object.freeze(row("885001.TI", { broken_board: frozenBroken })))
    .broken_count,
  2,
);

const frozen = row("885001.TI", { up_count: 1, observed_count: 2 });
Object.freeze(frozen);
const frozenRows = Object.freeze([frozen]);
const output = prepare(frozenRows, { kind: "concept" });
assert.notEqual(output[0], frozen);
assert.equal("_sector_up_ratio" in frozen, false);
assert.equal(output[0]._sector_up_ratio, 50);

console.log(
  "Sector explorer: separate source/normalization/level/basis scopes, deliberate search, original rank modes, exact/partial seven-day facts, strict sample ratio, unknown-last and immutable input passed",
);
