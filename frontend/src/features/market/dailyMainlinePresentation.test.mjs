import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./dailyMainlinePresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { buildDailyMainlineDisplay, confirmedMainlineHeight } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const date = "2026-01-05",
  snapshot = "market-synthetic";
const stock = (id, reason, height = 1, name = id) => ({
  instrument_id: id,
  name,
  date,
  snapshot_id: snapshot,
  reason,
  limit_up_state: true,
  close: 10,
  volume: 100,
  price_basis: "none",
  board_count: 999,
  topics: [{ name: "不得自动使用的热门目录" }],
  board_presentation: {
    version: "dated-stock-board-presentation-v1",
    date,
    snapshot_id: snapshot,
    instrument_id: id,
    height: {
      value: height,
      label: height == null ? null : `${height}板`,
      state: height == null ? "unknown" : "confirmed",
      source_date: date,
    },
  },
});
const rows = [
  stock("600001.SH", "专业照明+AI照明", 6, "合成海洋王"),
  stock("600002.SH", "业绩增长+人形机器人+机器人电池", 6, "合成大业"),
  stock("600003.SH", "具身智能+机械设备", 2),
  stock("600004.SH", "机器人+汽车", null),
  stock("600005.SH", "商贸零售+消费", 2),
  stock("600006.SH", "零售促销+消费", 1),
  stock("600007.SH", "业绩增长+订单", 2),
  stock("600008.SH", "业绩预增+出口", 1),
  stock("600009.SH", "原文很长的叙述".repeat(400), null),
  stock("600010.SH", null, 1),
  stock("600011.SH", "未知", 1),
];
const member = (index, tags = []) => ({
  instrument_id: rows[index].instrument_id,
  source_reason: rows[index].reason,
  matched_tag: tags[0] || null,
  matched_tags: tags,
  source: null,
  source_ref: {
    date,
    snapshot_id: snapshot,
    endpoint: "/api/v1/market/ladder",
    field_path: `items[instrument_id="${rows[index].instrument_id}"].reason`,
  },
  closing_pool_refs: [
    {
      source: "合成闭市池",
      path: "private.internal.path",
      raw_sha256: "a".repeat(64),
      purpose: "closing_event_only",
    },
  ],
});
const group = (key, name, kind, members) => ({
  key,
  name,
  kind,
  state: "candidate",
  grouping_rule: "synthetic-rule-v2",
  labels: [...new Set(members.flatMap((item) => item.matched_tags))],
  count: members.length,
  max_known_height: 999,
  high_stock_ids: ["600004.SH"],
  members,
});
const payload = {
  algorithm_version: "source-reason-directions-v2",
  source_kind: "local_source_synthesis",
  state: "candidate",
  date,
  snapshot_id: snapshot,
  groups: [
    group("robot", "机器人相关", "direction", [
      member(1, ["人形机器人", "机器人电池"]),
      member(2, ["具身智能"]),
      member(3, ["机器人"]),
    ]),
  ],
  other_groups: [
    group("retail", "零售", "direction", [
      member(4, ["商贸零售"]),
      member(5, ["零售促销"]),
    ]),
    group("earnings", "业绩线索", "catalyst", [
      member(6, ["业绩增长"]),
      member(7, ["业绩预增"]),
    ]),
  ],
  individual_cues: [member(0, ["专业照明"]), member(8)],
  missing_reason: [member(9), member(10)],
  coverage: {
    scope: "local_confirmed_sample_only",
    confirmed_sample_count: 11,
    focus_count: 3,
    other_count: 4,
    individual_count: 2,
    missing_reason_count: 2,
    rejected_row_count: 0,
    market_complete: false,
    source_pool_complete: false,
  },
  reason: null,
  notice: "合成测试提示，不用于正文",
};
const build = (data = payload, items = rows, gen = snapshot) =>
  buildDailyMainlineDisplay(date, items, data, gen);
const before = structuredClone({ rows, payload });
const plan = build();
assert.equal(plan.available, true);
assert.equal(plan.total, 11);
assert.equal(plan.classifiedCount, 11);
assert.equal(
  plan.groups[0].name,
  "机器人相关",
  "the frontend accepts backend grouping names without guessing labels from Chinese text",
);
assert.equal(plan.groups[0].maxKnownHeight, 6);
assert.equal(plan.otherGroups[1].kind, "catalyst");
assert.equal(plan.individualCues[1].sourceReason, rows[8].reason);
assert.equal(
  plan.individualCues[1].evidence.matched_tag,
  null,
  "long narrative and missing reasons retain the real nullable protocol",
);
assert.equal(plan.missingReasonCount, 2);
assert.equal(plan.sourceGapCount, 0);
assert.deepEqual(
  plan.globalHigh.stocks.map((item) => item.name),
  ["合成海洋王", "合成大业"],
  "a high outside the first three directions remains visible globally",
);
assert.equal(plan.globalHigh.height, 6);
assert.equal(plan.globalHigh.unknownCount, 2);
assert.deepEqual(
  { rows, payload },
  before,
  "display cannot mutate published rows or source evidence",
);
assert.equal(
  build(payload, [...rows, structuredClone(rows[0])]).total,
  11,
  "repeated identical rows are counted once",
);
assert.equal(
  build(payload, [...rows, { ...rows[0], reason: "conflicting source" }])
    .available,
  false,
);
for (const mutate of [
  (value) => {
    value.algorithm_version = "source-reason-exact-tags-v1";
  },
  (value) => {
    value.state = "unavailable";
  },
  (value) => {
    value.source_kind = "manual_user_entry";
  },
  (value) => {
    value.date = "2026-01-02";
  },
  (value) => {
    value.snapshot_id = "other";
  },
  (value) => {
    value.coverage.scope = "all_market";
  },
  (value) => {
    value.coverage.focus_count++;
  },
  (value) => {
    value.coverage.confirmed_sample_count--;
  },
  (value) => {
    value.coverage.rejected_row_count = -1;
  },
  (value) => {
    value.other_groups = null;
  },
  (value) => {
    value.groups[0].kind = "catalyst";
  },
  (value) => {
    value.groups[0].count--;
  },
  (value) => {
    value.groups[0].labels.push("不是实际命中标签");
  },
  (value) => {
    value.other_groups[0].key = "robot";
  },
  (value) => {
    value.individual_cues.push(structuredClone(value.groups[0].members[0]));
  },
  (value) => {
    value.missing_reason[0].matched_tag = "假的标签";
  },
]) {
  const value = structuredClone(payload);
  mutate(value);
  const result = build(value);
  assert.equal(
    result.available,
    false,
    "inconsistent partition must fail closed rather than reclassify in the browser",
  );
  assert.deepEqual(result.groups, []);
}
for (const mutate of [
  (value) => {
    value.source_reason = "不是本行原文";
  },
  (value) => {
    value.matched_tags = ["伪造命中词"];
    value.matched_tag = "伪造命中词";
  },
  (value) => {
    value.matched_tag = "机器人电池";
  },
  (value) => {
    value.source_ref.date = "2026-01-02";
  },
  (value) => {
    value.source_ref.snapshot_id = "other";
  },
  (value) => {
    value.source_ref.endpoint = "/other";
  },
  (value) => {
    value.source_ref.field_path = "items[another].reason";
  },
]) {
  const value = structuredClone(payload);
  mutate(value.groups[0].members[0]);
  assert.equal(build(value).available, false);
}
for (const update of [
  { date: "2026-01-02" },
  { snapshot_id: "other" },
  { evidence_date: "2026-01-02" },
  { trade_date: "2026-01-02" },
  { trade_date: null },
  { evidence_date: null },
  { kind: null },
  { limit_up_state: null },
  { close: 0 },
  { volume: 0 },
  { volume: Number.NaN },
  { price_basis: "forward" },
  { price_basis: null },
]) {
  const changedRows = structuredClone(rows);
  Object.assign(changedRows[1], update);
  assert.equal(build(payload, changedRows).available, false);
}
assert.equal(
  buildDailyMainlineDisplay("2026-02-30", rows, payload, snapshot).available,
  false,
);
assert.equal(
  buildDailyMainlineDisplay(date, rows, undefined, snapshot).available,
  false,
);
assert.equal(
  buildDailyMainlineDisplay(date, rows, payload, undefined).available,
  false,
);
const duplicatePartition = structuredClone(payload);
duplicatePartition.groups[0].members.push(
  structuredClone(duplicatePartition.groups[0].members[0]),
);
duplicatePartition.groups[0].count++;
assert.equal(build(duplicatePartition).available, false);
const singleOther = structuredClone(payload);
singleOther.other_groups[0].members.pop();
singleOther.other_groups[0].count = 1;
assert.equal(
  build(singleOther).available,
  false,
  "a one-stock category is not a grouped other direction",
);
for (const sourceReason of [
  "专业照明",
  "未知+专业照明",
  "其他+123",
  "robotics",
]) {
  const value = structuredClone(payload),
    items = structuredClone(rows);
  items[9].reason = sourceReason;
  value.missing_reason[0].source_reason = sourceReason;
  assert.equal(
    build(value, items).available,
    false,
    "provided source text cannot masquerade as missing with empty tags",
  );
}
for (const sourceReason of [
  null,
  "",
  "  —–- ",
  "未知+待核验+其他",
  "N/A+NULL+unknown",
  "＋，。！",
]) {
  const value = structuredClone(payload),
    items = structuredClone(rows);
  items[9].reason = sourceReason;
  value.missing_reason[0].source_reason = sourceReason;
  assert.equal(
    build(value, items).available,
    true,
    "only blank, punctuation and complete placeholders are genuine missing source reasons",
  );
}
const noEvidence = structuredClone(payload);
noEvidence.groups[0].members[0].closing_pool_refs = null;
assert.equal(
  build(noEvidence).available,
  true,
  "optional closing-pool metadata is not reason verification",
);
assert.deepEqual(
  build(noEvidence).groups[0].items[0].evidence.closing_pool_refs,
  [],
);
const withRejected = structuredClone(payload);
withRejected.coverage.rejected_row_count = 1;
const rejectedQuote = {
  ...stock("600099.SH", "同日来源但原始报价被后端排除", 99),
  volume: 0,
};
const excluded = build(withRejected, [...rows, rejectedQuote]);
assert.equal(excluded.available, true);
assert.equal(
  excluded.total,
  11,
  "the confirmed sample count uses the backend's complete four-way partition",
);
assert.equal(excluded.sourceGapCount, 1);
assert.equal(
  excluded.globalHigh.height,
  6,
  "a quote rejected by the confirmed-source partition cannot become the global high",
);
assert.doesNotMatch(
  excluded.globalHigh.stocks.map((item) => item.instrument_id).join(),
  /600099/,
);
for (const mutate of [
  (row) => {
    row.volume = 0;
  },
  (row) => {
    row.close = -1;
  },
  (row) => {
    row.volume = null;
  },
  (row) => {
    row.trade_date = "2026-01-02";
  },
  (row) => {
    row.board_presentation.snapshot_id = "other";
  },
  (row) => {
    row.board_presentation.date = "2026-01-02";
  },
  (row) => {
    row.board_presentation.instrument_id = "600099.SH";
  },
  (row) => {
    row.board_presentation.height.state = "unknown";
  },
  (row) => {
    row.board_presentation.height.source_date = "2026-01-02";
  },
  (row) => {
    row.board_presentation.height.label = null;
  },
  (row) => {
    row.board_presentation.height.value = 6.5;
  },
  (row) => {
    row.board_presentation.height.value = true;
  },
  (row) => {
    delete row.board_presentation;
  },
]) {
  const changed = structuredClone(rows[0]);
  mutate(changed);
  assert.equal(
    confirmedMainlineHeight(changed, date, snapshot),
    null,
    "raw 999 must never replace an unknown dated height",
  );
}
const staleHeights = rows.map((item) => ({
  ...item,
  board_presentation: { ...item.board_presentation, snapshot_id: "other" },
}));
const unknownHighs = build(payload, staleHeights);
assert.equal(unknownHighs.available, true);
assert.equal(unknownHighs.globalHigh.height, null);
assert.deepEqual(unknownHighs.globalHigh.stocks, []);
assert.equal(unknownHighs.groups[0].maxKnownHeight, null);
console.log(
  "automatic mainlines v2: four-way partition, real nullable tags, source bindings, immutable inputs and all-sample verified highs passed",
);
