import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = ts.transpileModule(
  await fs.readFile(
    new URL("./brokenBoardPresentation.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  brokenBoardContext: context,
  brokenBoardCurrentItems: currentItems,
  brokenBoardDrawdown: drawdown,
  brokenBoardPeriod: period,
  brokenBoardHistory: history,
  brokenBoardFeedback: feedback,
  brokenBoardStrings: strings,
  brokenBoardTopicKey: topicKey,
  brokenBoardMembership: membership,
  brokenBoardDistribution: distribution,
  brokenBoardDistributionSample: distributionSample,
  brokenBoardMembershipSummary: membershipSummary,
  brokenBoardFilterItems: filterItems,
  brokenBoardDetailContext: detailContext,
  brokenBoardPriceFeedback: priceFeedback,
  brokenBoardVisibleTopicCount: visibleTopicCount,
  brokenBoardDetailCount: detailCount,
  brokenBoardBoard: board,
  BROKEN_BOARD_BOARD_FILTERS: boardOptions,
} = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);

// Synthetic examples remain in tests, never in production pages.
assert.equal(detailCount("complete", 0, true), "读取中");
assert.equal(detailCount("unknown", 0, true), "读取中");
assert.equal(detailCount("unknown", 0), "待核验");
assert.equal(detailCount(undefined, 0), "待核验");
assert.equal(detailCount("partial", 0), "已核验0只");
assert.equal(detailCount("partial", 12), "已核验12只");
assert.equal(detailCount("unknown", 2), "已核验2只");
assert.equal(detailCount("complete", 0), "0只");
assert.equal(detailCount("complete", 12), "12只");
assert.equal(detailCount("complete", -1), "待核验");
assert.equal(detailCount("complete", NaN), "待核验");
assert.equal(visibleTopicCount(300, [100, 200, 300, 400]), 3);
assert.equal(
  visibleTopicCount(299.5, [100, 200, 300, 400]),
  2,
  "a partly clipped chip must be fully hidden",
);
assert.equal(
  visibleTopicCount(90, [100, 200]),
  0,
  "a narrow row must not expose a fragment of its first chip",
);
assert.equal(
  visibleTopicCount(500, [100, 200, 300, 400]),
  4,
  "expanding the viewport restores complete chips in ranking order",
);
assert.equal(visibleTopicCount(0, [100]), 0);
assert.equal(visibleTopicCount(500, [100, NaN, 300]), 1);
const date = "2026-10-27";
const previous = "2026-10-26";
const concept = {
  topic_id: "fuyao-robot",
  topic_code: "885517.TI",
  name: "机器人概念",
  source: "fuyao",
  kind: "concept",
  observed_date: "2026-10-21",
  members_complete: true,
};
const industry = {
  topic_id: "fuyao-equipment",
  topic_code: "881001.TI",
  name: "通用设备",
  source: "fuyao",
  kind: "industry",
  classification_level: 1,
  observed_date: "2026-10-21",
  members_complete: true,
};
const memberRow = {
  instrument_id: "600001.SH",
  topics_date: previous,
  topics_state: "dated_partial",
  topics: [
    concept,
    { ...concept },
    { ...concept, source: "other", topic_id: "other-robot" },
    {
      ...concept,
      topic_id: "future",
      topic_code: "885999.TI",
      observed_date: date,
    },
  ],
  industries: [industry, { ...industry, classification_level: 2 }],
};
assert.equal(
  membership(memberRow, previous, "concept").length,
  2,
  "same-source duplicates deduplicate, another source stays separate, future collection is excluded",
);
assert.equal(
  membership(memberRow, previous, "industry").length,
  2,
  "same-named industry levels are independent identities",
);
assert.equal(
  membership(memberRow, date, "concept").length,
  0,
  "a yesterday cohort must not silently acquire today's membership scope",
);
assert.equal(
  membership({ ...memberRow, topics_state: "unknown" }, previous, "concept")
    .length,
  0,
);
assert.equal(
  membership(
    { ...memberRow, topics: [{ ...concept, source: null }] },
    previous,
    "concept",
  ).length,
  0,
);
assert.notEqual(
  topicKey(industry),
  topicKey({ ...industry, classification_level: 2 }),
);
const group = {
  ...concept,
  topics_date: previous,
  membership_date: concept.observed_date,
  observed_count: 2,
  cohort_count: 3,
  sample_share_pct: 200 / 3,
};
const groups = distribution(
  [
    group,
    { ...group },
    { ...group, source: "other" },
    { ...group, topic_code: "885998.TI", topics_date: date },
  ],
  previous,
  "concept",
);
assert.equal(groups.length, 2);
assert.equal(groups[0].sample_share_pct, 200 / 3);
assert.equal(
  distribution([{ ...group, sample_share_pct: 99 }], previous, "concept")[0]
    .sample_share_pct,
  null,
  "a displayed share must reconcile to the unchanged whole cohort denominator",
);
assert.equal(
  distribution([group, { ...group, observed_count: 1 }], previous, "concept")
    .length,
  0,
  "conflicting duplicates do not select one arbitrary count",
);
assert.equal(
  distribution([{ ...group, membership_date: date }], previous, "concept")
    .length,
  0,
);
assert.equal(
  distribution([{ ...group, observed_count: -1 }], previous, "concept").length,
  0,
);
const sampleGroup = {
  ...group,
  sample_date: date,
  sample_basis: "cohort_equal_weight",
  sample_avg_change_pct: 9.16,
  sample_valid_count: 2,
  sample_unknown_count: 0,
};
for (const [change, expectedTone] of [
  [9.16, "up"],
  [-7.6, "down"],
  [0, "neutral"],
]) {
  const result = distributionSample(
    { ...sampleGroup, sample_avg_change_pct: change },
    date,
  );
  assert.equal(result.change, change);
  assert.equal(result.tone, expectedTone);
  assert.equal(result.valid, 2);
  assert.equal(result.total, 2);
  assert.equal(result.partial, false);
}
assert.equal(
  distributionSample(sampleGroup, date).change,
  9.16,
  "yesterday's memberships keep this fixed cohort's viewed-day average",
);
const partialSample = distributionSample(
  { ...sampleGroup, observed_count: 3, sample_unknown_count: 1 },
  date,
);
assert.equal(partialSample.change, 9.16);
assert.equal(partialSample.partial, true);
assert.equal(partialSample.valid, 2);
assert.equal(partialSample.total, 3);
const missingSample = distributionSample(
  {
    ...sampleGroup,
    sample_avg_change_pct: null,
    sample_valid_count: 0,
    sample_unknown_count: 2,
  },
  date,
);
assert.equal(missingSample.change, null);
assert.equal(missingSample.valid, 0);
assert.equal(missingSample.total, 2);
assert.equal(missingSample.partial, true);
assert.match(missingSample.reason, /有效0\/2/);
assert.equal(
  distributionSample(sampleGroup, previous).change,
  null,
  "another day's average must not be displayed under the selected day",
);
for (const patch of [
  { sample_date: null },
  { sample_basis: "index_return" },
  { sample_valid_count: 1 },
  { sample_valid_count: -1, sample_unknown_count: 3 },
  { sample_valid_count: 1.5, sample_unknown_count: 0.5 },
  { sample_valid_count: "2" },
  { sample_unknown_count: null },
  { observed_count: 0, sample_valid_count: 0 },
  { sample_avg_change_pct: NaN },
  { sample_avg_change_pct: Infinity },
  { sample_avg_change_pct: "9.16" },
  { sample_avg_change_pct: -101 },
  { sample_avg_change_pct: null },
  { sample_valid_count: 0, sample_unknown_count: 2, sample_avg_change_pct: 0 },
  { source: null },
])
  assert.equal(
    distributionSample({ ...sampleGroup, ...patch }, date).change,
    null,
    "inconsistent coverage, date, identity or average must remain unknown",
  );
assert.equal(
  distributionSample(
    {
      ...sampleGroup,
      sample_avg_change_pct: null,
      change_pct: 8,
      avg_change_pct: 7,
    },
    date,
  ).change,
  null,
  "index returns and other averages never replace the fixed cohort average",
);
assert.equal(
  distributionSample({ ...sampleGroup, sample_avg_change_pct: 1 / 3 }, date)
    .change,
  1 / 3,
  "keep full precision until the UI formats the sample average",
);
const memberRows = [
  memberRow,
  { ...memberRow, instrument_id: "600002.SH", topics: [], industries: [] },
];
assert.equal(
  filterItems(memberRows, previous, { kind: "concept", key: topicKey(concept) })
    .length,
  1,
);
assert.equal(
  filterItems(memberRows, previous, null),
  memberRows,
  "clearing a classification restores the original whole cohort without mutating it",
);
assert.equal(memberRows.length, 2);
assert.deepEqual(
  boardOptions.map((option) => option.value),
  ["all", "main", "chinext", "star", "beijing", "unknown"],
);
const boardRows = [
  { ...memberRow, instrument_id: "600001.SH", board: "main" },
  { ...memberRow, instrument_id: "300001.SZ", board: "chinext" },
  { ...memberRow, instrument_id: "688001.SH", board: "star", topics: [] },
  { ...memberRow, instrument_id: "920001.BJ", board: "beijing", topics: [] },
  { ...memberRow, instrument_id: "600002.SH", board: null },
];
assert.equal(board(boardRows[0]), "main");
assert.equal(board({ board: "unexpected" }), "unknown");
assert.equal(
  board({ instrument_id: "600002.SH" }),
  "unknown",
  "missing API board identity is not inferred from the name or ticker in the browser",
);
for (const value of ["main", "chinext", "star", "beijing", "unknown"])
  assert.equal(filterItems(boardRows, previous, null, value).length, 1);
assert.equal(filterItems(boardRows, previous, null, "all"), boardRows);
assert.equal(
  filterItems(
    boardRows,
    previous,
    { kind: "concept", key: topicKey(concept) },
    "main",
  ).length,
  1,
);
assert.equal(
  filterItems(
    boardRows,
    previous,
    { kind: "concept", key: topicKey(concept) },
    "star",
  ).length,
  0,
  "board and concept filters intersect without refilling absent membership",
);
assert.equal(
  boardRows.length,
  5,
  "detail filters leave the full statistics cohort intact",
);
const summary = {
  topics_date: previous,
  cohort_count: 3,
  industry_count: 2,
  concept_count: 11,
  stocks_with_industry: 2,
  stocks_with_concept: 3,
  unclassified_count: 0,
  reason: null,
};
assert.equal(membershipSummary(summary, previous), summary);
assert.equal(
  membershipSummary({ ...summary, cohort_count: -1 }, previous),
  null,
);
assert.equal(
  membershipSummary({ ...summary, stocks_with_industry: 4 }, previous),
  null,
);
assert.equal(membershipSummary(summary, date), null);
for (const [change, code, title, expectedTone] of [
  [3, "positive", "正反馈", "up"],
  [-2, "negative", "负反馈", "down"],
  [-5, "large_drop", "大面", "down"],
  [0, "flat", "平盘", "muted"],
]) {
  const result = priceFeedback({
    change_pct: change,
    price_feedback: { change_pct: change, code, threshold_pct: -5 },
  });
  assert.equal(result.label, title);
  assert.equal(result.tone, expectedTone);
}
assert.equal(
  priceFeedback({
    change_pct: -2,
    price_feedback: { change_pct: -2, code: "positive", threshold_pct: -5 },
  }).code,
  "unknown",
);
assert.equal(
  priceFeedback({ change_pct: 2 }).code,
  "unknown",
  "missing backend price evidence is not silently synthesized",
);
assert.equal(
  priceFeedback({
    change_pct: null,
    price_feedback: { change_pct: -9, code: "large_drop", threshold_pct: -5 },
  }).code,
  "unknown",
);
const report = Object.freeze({
  version: "broken-board-analysis-v1",
  date,
  snapshot_id: "generation-a",
});
assert.equal(
  detailContext(report, date, null),
  report,
  "current details reuse their existing report without an extra fetch",
);
const pastReport = { ...report, date: previous };
assert.equal(
  detailContext(report, previous, { broken_board_analysis: pastReport }),
  pastReport,
);
assert.equal(
  detailContext(report, previous, { broken_board_analysis: report }),
  null,
  "the previous request's date cannot flash under a new historical selection",
);
assert.equal(
  detailContext(report, previous, {
    broken_board_analysis: { ...pastReport, snapshot_id: "generation-b" },
  }),
  null,
);
assert.equal(
  detailContext(report, "2099-01-01", { broken_board_analysis: pastReport }),
  null,
);
assert.equal(context(report, date, "generation-a"), report);
for (const [input, viewDate, generation] of [
  [null, date, "generation-a"],
  [{ ...report, version: "other" }, date, "generation-a"],
  [report, previous, "generation-a"],
  [report, "2026-02-30", "generation-a"],
  [report, date, "generation-b"],
  [{ ...report, snapshot_id: null }, date, "generation-a"],
  [report, date, 1],
  [
    { ...report, current: { date: previous, snapshot_id: "generation-a" } },
    date,
    "generation-a",
  ],
  [
    { ...report, current: { date, snapshot_id: "generation-b" } },
    date,
    "generation-a",
  ],
])
  assert.equal(context(input, viewDate, generation), null);

const complete = Object.freeze({
  state: "complete",
  limit_up_count: 80,
  broken_count: 20,
  touch_count: 100,
  broken_rate_pct: 20,
  observed_limit_up_count: 80,
  observed_broken_count: 20,
});
assert.equal(period(complete).rate, 20);
assert.equal(period(complete).state, "complete");
assert.equal(period({ ...complete, state: "partial" }).rate, null);
assert.equal(period({ ...complete, state: "partial" }).broken, null);
assert.equal(period({ ...complete, state: "partial" }).observedBroken, 20);
for (const patch of [
  { limit_up_count: null },
  { broken_count: -1 },
  { broken_count: "20" },
  { broken_count: true },
  { touch_count: 99 },
  { broken_rate_pct: 40 },
  { broken_rate_pct: Infinity },
]) {
  const result = period({ ...complete, ...patch });
  assert.equal(result.state, "partial", JSON.stringify(patch));
  assert.equal(result.rate, null);
}
assert.equal(period({ ...complete, broken_rate_pct: "20" }).rate, null);
assert.equal(
  period({
    state: "complete",
    limit_up_count: 0,
    broken_count: 0,
    touch_count: 0,
    broken_rate_pct: 0,
  }).rate,
  null,
);
assert.equal(
  period({
    state: "complete",
    limit_up_count: 0,
    broken_count: 0,
    touch_count: 0,
  }).state,
  "complete",
);
assert.equal(period({ state: "unknown", observed_broken_count: 0 }).rate, null);
assert.equal(period({}).observedBroken, null);
assert.deepEqual(
  drawdown({
    drawdown_valid_count: 3,
    avg_drawdown_from_high_pct: -4.5,
    large_drawdown_count: 1,
  }),
  { valid: 3, average: -4.5, large: 1 },
);
assert.equal(
  drawdown({ drawdown_valid_count: 0, avg_drawdown_from_high_pct: 0 }).average,
  null,
);
assert.equal(
  drawdown({ drawdown_valid_count: 3, avg_drawdown_from_high_pct: 4.5 })
    .average,
  null,
);
assert.equal(
  drawdown({ drawdown_valid_count: 3, large_drawdown_count: 4 }).large,
  null,
);
const todayRows = currentItems(
  {
    items: [
      { instrument_id: "600001.SH", outcome: "limit_up", change_pct: 10 },
      {
        instrument_id: "600002.SH",
        outcome: "broken",
        date,
        change_pct: 2,
        drawdown_from_high_pct: -7.2,
      },
      {
        instrument_id: "600003.SH",
        outcome: "broken",
        date: previous,
        change_pct: 3,
      },
      {
        instrument_id: "600004.SH",
        outcome: "broken",
        date,
        drawdown_from_high_pct: 2,
      },
    ],
  },
  date,
);
assert.equal(todayRows.length, 2);
assert.equal(todayRows[0].instrument_id, "600002.SH");
assert.equal(todayRows[0].drawdown_from_high_pct, -7.2);
assert.equal(todayRows[1].drawdown_from_high_pct, null);
assert.equal(
  currentItems(
    {
      items: [
        {
          instrument_id: "600002.SH",
          outcome: "broken",
          date,
          snapshot_id: "generation-b",
        },
      ],
    },
    date,
    "generation-a",
  ).length,
  0,
);

const series = history(
  [
    { ...complete, date: previous },
    { state: "unknown", date, reason: "来源未留存" },
    { ...complete, date: "2099-01-01" },
    { ...complete, date: "2026-02-30" },
  ],
  date,
);
assert.equal(series.length, 2);
assert.equal(series[1].date, date);
assert.equal(series[1].rate, null);
assert.equal(series[1].reason, "来源未留存");
const duplicated = history(
  [
    { ...complete, date: previous },
    { ...complete, date: previous, broken_count: 0 },
  ],
  date,
);
assert.equal(duplicated[0].state, "unknown");
assert.equal(duplicated[0].rate, null);
assert.equal(
  history(
    [{ ...complete, date, snapshot_id: "generation-b" }],
    date,
    "generation-a",
  )[0].rate,
  null,
);
assert.equal(
  history(
    Array.from({ length: 12 }, (_, i) => ({
      ...complete,
      date: `2026-10-${String(i + 1).padStart(2, "0")}`,
    })),
    date,
  ).length,
  7,
);

const prior = Object.freeze({
  date,
  previous_date: previous,
  state: "complete",
  source_count: 3,
  verified_count: 3,
  valid_count: 2,
  unknown_count: 1,
  up_count: 1,
  down_count: 1,
  flat_count: 0,
  avg_change_pct: 0.5,
  median_change_pct: 0.5,
  return_limit_rate_pct: 100 / 3,
  limit_up_count: 1,
  limit_down_count: 0,
  items: [
    {
      instrument_id: "600001.SH",
      name: "测试甲",
      change_pct: 3,
      current_state: "limit_up",
    },
    {
      instrument_id: "300001.SZ",
      name: "测试乙",
      change_pct: -2,
      current_state: "not_listed",
    },
    {
      instrument_id: "920001.BJ",
      name: "测试丙",
      change_pct: null,
      current_state: "unknown",
    },
  ],
});
const view = feedback(prior, date);
assert.equal(view.items.length, 3);
assert.equal(view.items[0].code, "600001");
assert.equal(view.items[0].current_state_label, "涨停反包");
assert.equal(view.items[2].change_pct, null);
assert.equal(view.average, 0.5);
assert.equal(view.unknown, 1);
assert.equal(view.returnLimitRate, 100 / 3);
assert.equal(
  feedback({ ...prior, state: "partial" }, date).returnLimitRate,
  100 / 3,
  "missing comparable prices do not erase an independently confirmed event rate",
);
for (const patch of [
  { source_count: null },
  { source_count: 4 },
  { limit_up_count: null },
  { limit_down_count: 3 },
  { return_limit_rate_pct: 50 },
])
  assert.equal(
    feedback({ ...prior, state: "partial", ...patch }, date).returnLimitRate,
    null,
  );
assert.equal(
  feedback({ ...prior, state: "partial", limit_down_count: null }, date)
    .returnLimitRate,
  100 / 3,
  "unavailable down-limit evidence does not erase the independently proven up-limit rate",
);
assert.equal(
  feedback({ ...prior, snapshot_id: "generation-b" }, date, "generation-a")
    .valid,
  false,
);
assert.equal(
  feedback(
    {
      ...prior,
      snapshot_id: "generation-a",
      items: prior.items.map((row) => ({
        ...row,
        snapshot_id: "generation-b",
      })),
    },
    date,
    "generation-a",
  ).items.length,
  0,
);
assert.equal(feedback({ ...prior, valid_count: 0 }, date).average, null);
for (const patch of [
  { date: previous },
  { previous_date: date },
  { previous_date: "2099-01-01" },
  { previous_date: "2026-02-30" },
]) {
  const result = feedback({ ...prior, ...patch }, date);
  assert.equal(result.valid, false);
  assert.equal(result.items.length, 0);
  assert.equal(result.average, null);
}
const rejectedItems = feedback(
  {
    ...prior,
    items: [
      ...prior.items,
      { instrument_id: "600001.SH", change_pct: 99 },
      { instrument_id: "600999.SH", date: "2099-01-01", change_pct: 99 },
      { instrument_id: "600998.SH", quote_date: previous, change_pct: 99 },
      { instrument_id: "INVALID", change_pct: 99 },
    ],
  },
  date,
);
assert.equal(rejectedItems.items.length, 2);
assert.equal(rejectedItems.hiddenItems, 5);
assert.deepEqual(strings(["事实", " ", null, { note: "不能变成文本" }, 1]), [
  "事实",
]);
assert.deepEqual(prior.items[0], {
  instrument_id: "600001.SH",
  name: "测试甲",
  change_pct: 3,
  current_state: "limit_up",
});
console.log(
  "Broken-board presentation: dated generation, complete/partial pools, zero denominator, history gaps, invalid cohorts and stock navigation identity passed",
);
