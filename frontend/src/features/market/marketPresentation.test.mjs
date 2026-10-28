/** Feature-local regression cases; root runs this with Node from frontend/. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./marketPresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  marketMoney,
  sortMarketRows,
  describeDataCoverage,
  amountChangeText,
  topicMembersRequest,
  emotionStageSummary,
  filterMarketMembers,
  sectorQuoteSummary,
  industryFundsSummary,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
// Explorer ratios must compare numerically, rather than as decimal text parts.
assert.deepEqual(
  sortMarketRows(
    [
      { _sector_up_ratio: 10.11 },
      { _sector_up_ratio: null },
      { _sector_up_ratio: 10.2 },
      { _sector_up_ratio: "99" },
    ],
    { key: "_sector_up_ratio", order: "desc" },
  ).map((row) => row._sector_up_ratio),
  [10.2, 10.11, null, "99"],
);
assert.deepEqual(
  sortMarketRows(
    [
      { _sector_activity_count: null },
      { _sector_activity_count: 2 },
      { _sector_activity_count: 0 },
    ],
    { key: "_sector_activity_count", order: "asc" },
  ).map((row) => row._sector_activity_count),
  [0, 2, null],
);
assert.equal(emotionStageSummary("普涨"), "上涨股票较多，市场广度较强。");
assert.match(emotionStageSummary("待核验"), /不足/);
assert.equal(/[≥≤<>]/.test(emotionStageSummary("高潮")), false);
assert.equal(amountChangeText(null), "较昨日增减待核验");
assert.equal(amountChangeText(NaN), "较昨日增减待核验");
assert.equal(amountChangeText(0), "较昨日持平 · 0.00亿元");
assert.equal(amountChangeText(1.3e10), "较昨日放量 130.00亿元");
assert.equal(amountChangeText(-2.51e8), "较昨日缩量 2.51亿元");
const boardData = {
  items: [
    {
      name: "样本概念甲",
      kind: "concept",
      change_pct: 2,
      amount: 1e8,
      quote_available: true,
      quote_date: "2026-10-20",
      quote_basis: "provider_index",
      catalog_observed_date: "2026-10-21",
      members_available: false,
      member_count: null,
    },
    {
      name: "样本概念乙",
      kind: "concept",
      change_pct: -3,
      amount: 2e8,
      quote_available: true,
      quote_date: "2026-10-20",
      quote_basis: "provider_index",
      catalog_observed_date: "2026-10-21",
      members_available: false,
      member_count: null,
    },
    {
      name: "样本概念丙",
      kind: "concept",
      change_pct: null,
      amount: null,
      quote_available: false,
      catalog_observed_date: "2026-10-21",
      members_available: false,
    },
    {
      name: "样本行业",
      kind: "industry",
      change_pct: 1,
      quote_available: true,
      classification_level: 1,
      members_available: false,
    },
    {
      name: "细分行业",
      kind: "industry",
      change_pct: -1,
      quote_available: true,
      classification_level: 2,
      members_available: false,
    },
  ],
  summary: {
    concept_count: 3,
    industry_count: 2,
    quoted_concepts: 2,
    quoted_industries: 2,
    index_quote_count: 4,
  },
  membership_coverage: { selected_date_available: false },
  data_coverage: { selected_date_available: true },
};
const quotesWithoutMembers = sectorQuoteSummary(boardData, "concept");
assert.equal(quotesWithoutMembers.catalogCount, 3);
assert.equal(
  quotesWithoutMembers.quotedCount,
  2,
  "historical membership absence must not erase real board quotes",
);
assert.equal(quotesWithoutMembers.membersAvailable, 0);
assert.equal(quotesWithoutMembers.up, 1);
assert.equal(quotesWithoutMembers.down, 1);
assert.equal(
  quotesWithoutMembers.unknown,
  1,
  "missing change stays unknown, not flat",
);
assert.equal(quotesWithoutMembers.sourceIndexCount, 2);
assert.equal(quotesWithoutMembers.catalogDate, "2026-10-21");
assert.match(quotesWithoutMembers.membershipNotice, /日行情可独立展示/);
const sameLevel = sectorQuoteSummary(boardData, "industry", "1");
assert.equal(sameLevel.catalogCount, 1);
assert.equal(sameLevel.quotedCount, 1);
assert.equal(sameLevel.down, 0);
assert.equal(sectorQuoteSummary(undefined, "concept").quotedCount, null);
assert.equal(
  sectorQuoteSummary({ items: [], summary: { concept_count: 710 } }, "concept")
    .quotedCount,
  null,
  "no quote flags or count is not verified zero",
);
assert.equal(
  sectorQuoteSummary({ items: [], summary: { concept_count: 710 } }, "concept")
    .membersAvailable,
  null,
);
const funds = {
  items: [{ net_inflow: 2e8 }, { net_inflow: -3e8 }, { net_inflow: null }],
  summary: { net_flow_available: true, history_observed_days: 20 },
  coverage: { observed_count: 128, selected_count: 128 },
};
const preservedFunds = JSON.stringify(funds);
assert.deepEqual(industryFundsSummary(funds), {
  available: true,
  observed: 128,
  total: 128,
  days: 20,
});
sectorQuoteSummary(boardData, "concept");
assert.equal(
  JSON.stringify(funds),
  preservedFunds,
  "switching concept quote context cannot mutate the industry fund dataset",
);
assert.equal(industryFundsSummary(undefined).observed, null);
assert.equal(
  industryFundsSummary({
    items: [{ net_inflow: null }, { net_inflow: 0 }, { net_inflow: -1 }],
  }).observed,
  2,
  "known zero is a valid funds record; null is not",
);
const memberRows = [
  { code: "600001", name: "样本甲", board: "main" },
  { code: "300001", name: "样本乙", board: null },
  { code: "688001", name: "样本丙", board: "star" },
  { code: "300002", name: "其他", board: "chinext" },
];
assert.deepEqual(
  filterMarketMembers(memberRows, "样本", "all").map((row) => row.code),
  ["600001", "300001", "688001"],
);
assert.deepEqual(
  filterMarketMembers(memberRows, "样本", "unknown").map((row) => row.code),
  ["300001"],
  "board unknown is not inferred from code prefix",
);
assert.deepEqual(
  filterMarketMembers(memberRows, "样本", "star").map((row) => row.code),
  ["688001"],
);
assert.equal(filterMarketMembers(memberRows, "样本", "chinext").length, 0);
for (const key of ["return_ytd_pct", "return_mtd_pct"]) {
  assert.deepEqual(
    sortMarketRows(
      [
        { id: "u", [key]: null },
        { id: "low", [key]: -3 },
        { id: "high", [key]: 7 },
      ],
      { key, order: "desc" },
    ).map((row) => row.id),
    ["high", "low", "u"],
  );
}
assert.equal(topicMembersRequest({}, "2026-10-19"), null);
const memberRequest = new URL(
  "http://local" +
    topicMembersRequest({ topic_id: "fuyao-concept-test" }, "2026-10-19"),
);
assert.equal(memberRequest.pathname, "/market/topic-members");
assert.equal(memberRequest.searchParams.get("date"), "2026-10-19");
assert.equal(memberRequest.searchParams.get("topic_id"), "fuyao-concept-test");
const fundMemberRequest = new URL(
  "http://local" +
    topicMembersRequest(
      {
        topic_id: "eastmoney-BK001",
        topic_code: "BK001",
        name: "证券",
        kind: "industry",
      },
      "2026-10-19",
    ),
);
assert.equal(fundMemberRequest.searchParams.get("source_code"), "BK001");
assert.equal(fundMemberRequest.searchParams.get("source_name"), "证券");
assert.equal(
  fundMemberRequest.searchParams.has("topic_id"),
  false,
  "cross-provider code is not a local membership identity",
);
const explicitMemberRequest = new URL(
  "http://local" +
    topicMembersRequest(
      {
        topic_id: "wrong-series-id",
        member_lookup: { topic_id: "fuyao-real", kind: "industry" },
      },
      "2026-10-16",
    ),
);
assert.equal(explicitMemberRequest.searchParams.get("topic_id"), "fuyao-real");
assert.equal(explicitMemberRequest.searchParams.get("date"), "2026-10-16");
for (const [value, expected] of [
  [null, "—"],
  [NaN, "—"],
  [0, "0.00元"],
  [9999, "9,999.00元"],
  [10000, "1.00万元"],
  [1e8, "1.00亿元"],
  [1e12, "1.00万亿元"],
  [-1.25e12, "-1.25万亿元"],
])
  assert.equal(marketMoney(value), expected);
const rows = [
  { id: "million", seal_money: 8e6 },
  { id: "unknown", seal_money: null },
  { id: "zero", seal_money: 0 },
  { id: "billion", seal_money: 1e9 },
  { id: "missing" },
  { id: "equal", seal_money: 8e6 },
];
assert.deepEqual(
  sortMarketRows(rows, { key: "seal_money", order: "desc" }).map(
    (row) => row.id,
  ),
  ["billion", "million", "equal", "zero", "unknown", "missing"],
);
assert.deepEqual(
  sortMarketRows(rows, { key: "seal_money", order: "asc" }).map(
    (row) => row.id,
  ),
  ["zero", "million", "equal", "billion", "unknown", "missing"],
);
assert.equal(rows[0].id, "million");
const percentages = [
  { id: "up", change_pct: 3 },
  { id: "down", change_pct: -4 },
  { id: "flat", change_pct: 0 },
  { id: "unknown", change_pct: null },
];
assert.deepEqual(
  sortMarketRows(percentages, { key: "change_pct", order: "asc" }).map(
    (row) => row.id,
  ),
  ["down", "flat", "up", "unknown"],
);
assert.deepEqual(
  sortMarketRows(
    [
      { id: 1, limit_up_ytd: null, limit_up_ytd_observed: 99 },
      { id: 2, limit_up_ytd: 5 },
    ],
    { key: "limit_up_ytd", order: "desc" },
  ).map((row) => row.id),
  [2, 1],
);
const unknownCoverage = describeDataCoverage(null);
assert.equal(unknownCoverage.known, false);
assert.equal(unknownCoverage.range, "覆盖信息待核验");
assert.equal(unknownCoverage.selectedAvailable, null);
assert.deepEqual(unknownCoverage.dates, []);
const sectorCoverage = describeDataCoverage({
  label: "行业成员快照",
  start: null,
  end: null,
  basis: "仅使用所选日留存成员",
  notice: "尚未开始留存该日快照，不回填当前成员",
  available_dates: [],
  selected_date_available: false,
  partial: true,
});
assert.equal(sectorCoverage.range, "暂无已确认留存日期");
assert.equal(sectorCoverage.selectedAvailable, false);
assert.equal(sectorCoverage.partial, true);
assert.match(sectorCoverage.notice, /不回填当前成员/);
const sparseCoverage = describeDataCoverage({
  label: "已留存来源事件",
  start: "2026-10-16",
  end: "2026-10-20",
  basis: "按明确来源日期",
  notice: "封板细节仅近20日；年度事件另有边界",
  available_dates: [
    "2026-10-20",
    "2026-10-16",
    "2026-10-16",
    "2026-02-31",
    null,
  ],
  partial: true,
});
assert.deepEqual(sparseCoverage.dates, ["2026-10-16", "2026-10-20"]);
assert.equal(
  sparseCoverage.selectedAvailable,
  null,
  "bounds never imply the selected date is available",
);
assert.equal(sparseCoverage.range, "已留存 2026-10-16 → 2026-10-20");
assert.match(sparseCoverage.notice, /年度事件另有边界/);
assert.equal(
  describeDataCoverage({
    label: "当日存档",
    start: "2026-10-20",
    end: "2026-10-20",
  }).range,
  "已留存 2026-10-20",
);
assert.equal(
  describeDataCoverage({
    label: "范围",
    start: "2026-02-31",
    end: "2026-10-20",
  }).range,
  "起点待核验 · 截至 2026-10-20",
);
assert.equal(
  describeDataCoverage({
    label: "范围",
    start: "2026-10-20",
    end: "2026-10-16",
  }).partial,
  true,
);
console.log(
  "market presentation: money units, raw ordering/null-last, partial counts, page-specific coverage, sparse dates, missing snapshots and boundary validation passed",
);
