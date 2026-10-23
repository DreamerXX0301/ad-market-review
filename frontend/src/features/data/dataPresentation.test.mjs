import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./dataPresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const {
  initializationView,
  initializationTaskBlocked,
  visibleWarnings,
  incrementWindowView,
  initializationWindowView,
  dataTaskTitle,
  sectorChartsTaskProgress,
  sectorChartsStatusView,
  sectorChartsInitializationView,
  sectorChartsTaskBlocked,
  isSectorChartsInitialTask,
  datasetCoverageView,
  listingIdentityView,
  listingConfirmationDraft,
  listingEvidenceLines,
  filterGapDiagnosisStocks,
  gapEpisodeView,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
assert.equal(
  listingIdentityView({
    catalog_count: 5580,
    listed_count: 5575,
    pending_listing_count: 5,
    unknown_count: 0,
  }).complete,
  true,
);
assert.equal(
  listingIdentityView({
    catalog_count: 5580,
    listed_count: 5575,
    pending_listing_count: 4,
    unknown_count: 1,
  }).expected,
  5576,
  "unknown remains in the denominator",
);
assert.equal(
  listingIdentityView({
    catalog_count: 5580,
    listed_count: 5575,
    pending_listing_count: 5,
  }).expected,
  null,
  "absent unknown count cannot mean zero",
);
assert.equal(
  listingIdentityView({
    catalog_count: 5580,
    listed_count: 5575,
    pending_listing_count: 6,
    unknown_count: 0,
  }).complete,
  false,
  "contradictory totals cannot show 100%",
);
const confirmation = listingConfirmationDraft(
  "000001.sz，000001.SZ\n600000.SH",
  "pending",
  " 人工核验说明 ",
  " 补充公告 ",
  "2026-10-21",
);
assert.deepEqual(confirmation.payload, {
  instrument_ids: ["000001.SZ", "600000.SH"],
  status: "pending",
  reason: "人工核验说明",
  evidence: "补充公告",
  observed_business_date: "2026-10-21",
});
for (const args of [
  ["000001", "pending", "有依据", "", "2026-10-21"],
  ["000001.SZ", "listed", "依据", "", "2026-10-21"],
  ["000001.SZ", "pending", "", "", "2026-10-21"],
  ["000001.SZ", "pending", "依据", "", ""],
  ["000001.SZ", "pending", "依据", "a".repeat(4001), "2026-10-21"],
])
  assert.ok(listingConfirmationDraft(...args).error);
assert.equal(
  listingEvidenceLines({
    reference: { quote_presence: { valid_quote_count: 0 } },
  })[0],
  "本代有效成交报价 0 条",
);
const gapRows = [
  {
    instrument_id: "B",
    missing_sessions: 2,
    episode_count: 2,
    longest_episode_sessions: 1,
    episodes: [
      {
        reason: "来源也未返回",
        start_date: "2026-10-20",
        dates: ["2026-10-21"],
      },
    ],
  },
  {
    instrument_id: "A",
    missing_sessions: 9,
    episode_count: 1,
    longest_episode_sessions: 9,
    episodes: [],
  },
];
assert.deepEqual(
  filterGapDiagnosisStocks({ stocks: gapRows }, "", "missing_sessions").map(
    (x) => x.instrument_id,
  ),
  ["A", "B"],
);
assert.equal(
  filterGapDiagnosisStocks(
    { stocks: gapRows },
    "2026-10-21",
    "episode_count",
  )[0].instrument_id,
  "B",
);
assert.equal(
  filterGapDiagnosisStocks({ stocks: gapRows }, "来源也未返回", "episode_count")
    .length,
  1,
);
assert.deepEqual(
  gapRows.map((x) => x.instrument_id),
  ["B", "A"],
  "sorting does not mutate persisted diagnostic order",
);
assert.equal(
  gapEpisodeView({
    type_label: "本地缺口",
    source_verification: { status: "source_no_quote", reason: "源也无报价" },
  }).label,
  "来源同样无报价",
);
assert.equal(
  dataTaskTitle({
    kind: "source_check",
    parameters: { operation: "quote_gap_source_check" },
  }),
  "向来源核验报价缺口",
);
assert.equal(
  datasetCoverageView({ row_count: 6000000, expected_rows: 6000000 }).percent,
  null,
  "record share cannot become a coverage denominator",
);
assert.equal(
  datasetCoverageView({
    coverage_progress: {
      state: "known",
      observed_count: 80,
      expected_count: 100,
      dimension: "listing_dates",
      unit: "只",
    },
  }).percent,
  80,
);
for (const progress of [
  { state: "known", observed_count: 0, expected_count: 0 },
  { state: "known", observed_count: 101, expected_count: 100 },
  { state: "unknown", observed_count: 100, expected_count: 100 },
  { state: "known", observed_count: null, expected_count: 100 },
])
  assert.equal(
    datasetCoverageView({ coverage_progress: progress }).percent,
    null,
    "unknown/conflicting evidence does not show a percentage",
  );
const coveredMeasure = datasetCoverageView({
  status: "partial",
  coverage_progress: {
    state: "known",
    observed_count: 5,
    expected_count: 5,
    coverage_status: "covered",
    coverage_label: "当前范围已覆盖",
  },
});
assert.equal(
  coveredMeasure.displayState,
  "covered",
  "snapshot quality cannot override the narrower coverage dimension",
);
assert.equal(coveredMeasure.displayLabel, "当前范围已覆盖");
assert.equal(coveredMeasure.percentText, "100%");
const partialMeasure = datasetCoverageView({
  status: "ready",
  coverage_progress: {
    state: "known",
    observed_count: 9999999,
    expected_count: 10000000,
  },
});
assert.equal(partialMeasure.displayState, "partial");
assert.equal(
  partialMeasure.percentText,
  "<100%",
  "rounding cannot paint an incomplete measure as 100%",
);
const contradictoryMeasure = datasetCoverageView({
  coverage_progress: {
    state: "unknown",
    observed_count: 100,
    expected_count: null,
    coverage_status: "covered",
    coverage_label: "当前范围已覆盖",
  },
});
assert.equal(contradictoryMeasure.displayState, "unknown");
assert.notEqual(contradictoryMeasure.displayLabel, "当前范围已覆盖");
const onDemandMeasure = datasetCoverageView({
  module: "indicators",
  row_count: 6000000,
  coverage_progress: {
    state: "unknown",
    observed_count: null,
    expected_count: null,
    coverage_status: "on_demand",
    coverage_label: "按需计算",
  },
});
assert.equal(onDemandMeasure.displayState, "on_demand");
assert.equal(onDemandMeasure.percent, null);
assert.equal(initializationView(undefined).canInitialize, false);
assert.equal(
  initializationView({ ready: true }).known,
  false,
  "market ready is not initialization proof",
);
assert.equal(
  initializationView({ main_published: false, can_initialize: true })
    .canInitialize,
  true,
);
const published = {
  main_published: true,
  can_initialize: false,
  reason: "已有主行情",
};
assert.equal(initializationView(published).canInitialize, false);
assert.equal(initializationView(published).label, "主行情已初始化");
assert.equal(
  initializationView({ ...published, can_initialize: true }).canInitialize,
  false,
  "contradictory metadata cannot reopen import",
);
assert.equal(
  initializationTaskBlocked(
    { kind: "initialise", status: "failed" },
    published,
  ),
  true,
);
assert.equal(
  initializationTaskBlocked(
    { mode: "initial_sync", status: "paused" },
    published,
  ),
  true,
);
for (const kind of ["topic_refresh", "source_check", "increment"])
  assert.equal(
    initializationTaskBlocked({ kind, status: "failed" }, published),
    false,
  );
assert.equal(
  initializationTaskBlocked(
    { kind: "initialise", status: "failed" },
    { main_published: false, can_initialize: true },
  ),
  false,
);

assert.equal(
  dataTaskTitle({
    kind: "source_check",
    mode: "initial",
    parameters: { operation: "sector_charts" },
  }),
  "行业与概念全库K线",
);
assert.equal(
  dataTaskTitle({
    kind: "source_check",
    parameters: { operation: "sector_chart", instrument: { name: "银行" } },
  }),
  "单板块历史K线 · 银行",
);
assert.equal(
  dataTaskTitle({
    kind: "source_check",
    parameters: { operation: "market_supplement" },
  }),
  "盘面细节与年内涨跌停回补",
);
assert.equal(
  dataTaskTitle({ kind: "source_check", parameters: {} }),
  "来源能力检查",
);
assert.equal(
  sectorChartsTaskProgress({
    kind: "source_check",
    parameters: { operation: "sector_chart" },
  }),
  null,
);
const bulkTask = {
  kind: "source_check",
  parameters: { operation: "sector_charts" },
};
const initialBulkTask = {
  ...bulkTask,
  parameters: { operation: "sector_charts", mode: "initial" },
};
const tailTask = {
  ...bulkTask,
  parameters: { operation: "sector_charts", mode: "increment" },
};
const sectorInitialized = {
  initialized: true,
  can_initialize: false,
  initialized_date: "2026-10-20",
  available_count: 710,
  catalog_count: 710,
};
assert.equal(sectorChartsInitializationView(undefined).known, false);
assert.equal(sectorChartsInitializationView(undefined).canInitialize, false);
assert.equal(
  sectorChartsInitializationView({ collection_complete: true, complete: false })
    .canInitialize,
  false,
  "collection progress is not explicit once-state metadata",
);
assert.equal(
  sectorChartsInitializationView(sectorInitialized).initialized,
  true,
);
assert.equal(
  sectorChartsInitializationView(sectorInitialized).canInitialize,
  false,
);
assert.equal(
  sectorChartsInitializationView(sectorInitialized).label,
  "板块K线已初始化",
);
assert.equal(
  sectorChartsInitializationView({ ...sectorInitialized, can_initialize: true })
    .canInitialize,
  false,
  "contradictory flags cannot reopen full initialization",
);
assert.equal(
  sectorChartsInitializationView({
    ...sectorInitialized,
    initialized_date: "2026-10-20",
    catalog_count: 711,
  }).canInitialize,
  false,
  "a new catalog or observation day cannot reopen completed initialization",
);
assert.equal(
  sectorChartsInitializationView({ initialized: false, can_initialize: true })
    .canInitialize,
  true,
);
assert.equal(
  sectorChartsInitializationView({
    initialized: false,
    can_initialize: false,
    reason: "已有首次任务待运行",
  }).canInitialize,
  false,
);
assert.equal(
  sectorChartsInitializationView({
    initialized: false,
    can_initialize: false,
    reason: "已有首次任务待运行",
  }).reason,
  "已有首次任务待运行",
);
assert.equal(isSectorChartsInitialTask(initialBulkTask), true);
assert.equal(isSectorChartsInitialTask(tailTask), false);
assert.equal(sectorChartsTaskBlocked(initialBulkTask, sectorInitialized), true);
assert.equal(
  sectorChartsTaskBlocked(
    { ...initialBulkTask, status: "partial" },
    sectorInitialized,
  ),
  true,
  "all boards available with a leading historical gap is still once-successful",
);
assert.equal(
  sectorChartsTaskBlocked(
    { ...initialBulkTask, status: "failed" },
    { initialized: false, can_initialize: true },
  ),
  false,
);
for (const status of ["paused", "failed", "partial", "cancelled"])
  assert.equal(
    sectorChartsTaskBlocked({ ...tailTask, status }, sectorInitialized),
    false,
    "tail increment retries/resume remain available",
  );
assert.equal(dataTaskTitle(tailTask), "板块K线尾部增量");
const queued = sectorChartsTaskProgress({
  ...bulkTask,
  progress: 99,
  logs: [],
});
assert.equal(queued.total, null);
assert.equal(queued.available, null, "progress percent is not a board count");
const progress = sectorChartsTaskProgress({
  ...bulkTask,
  logs: [
    {
      details: {
        completed_count: 1,
        total_count: 710,
        available_count: 1,
        request_count: 1,
        cache_hit_count: 0,
        failed_count: 0,
        unrequested_count: 709,
      },
    },
    {
      details: {
        completed_count: 2,
        total_count: 710,
        available_count: 2,
        request_count: 1,
        cache_hit_count: 1,
        failed_count: 0,
        unrequested_count: 708,
      },
    },
  ],
});
assert.equal(progress.completed, 2);
assert.equal(progress.total, 710);
assert.equal(progress.available, 2);
assert.equal(progress.cached, 1);
assert.equal(progress.failed, 0);
assert.equal(progress.unrequested, 708);
const full = sectorChartsTaskProgress({
  ...bulkTask,
  logs: [
    {
      details: {
        completed_count: 710,
        total_count: 710,
        available_count: 710,
        failed_count: 0,
        coverage: {
          catalog_count: 710,
          complete_count: 0,
          partial_count: 710,
          collection_complete: true,
          complete: false,
        },
      },
    },
  ],
});
assert.equal(full.collectionComplete, true);
assert.equal(full.historyComplete, false);
assert.equal(full.complete, 0);
assert.equal(full.partial, 710);
assert.equal(
  full.failed,
  0,
  "leading-boundary partial coverage is not a failed board",
);
const unverified = sectorChartsStatusView({
  target_date: "2026-10-20",
  catalog_count: 710,
  available_count: null,
  failed_count: null,
  last_job: null,
});
assert.equal(unverified.catalog, 710);
assert.equal(unverified.available, null);
assert.equal(unverified.failed, null);
assert.equal(unverified.collectionComplete, null);
const invalid = sectorChartsStatusView({
  catalog_count: -1,
  available_count: "710",
  complete_count: false,
  partial_count: NaN,
  failed_count: 0,
  complete: false,
});
assert.equal(invalid.catalog, null);
assert.equal(invalid.available, null);
assert.equal(invalid.complete, null);
assert.equal(invalid.partial, null);
assert.equal(invalid.failed, 0);
assert.equal(invalid.historyComplete, false);
const completeStatus = sectorChartsStatusView({
  target_date: "2026-10-20",
  catalog_count: 710,
  available_count: 710,
  complete_count: 0,
  partial_count: 710,
  failed_count: 0,
  unrequested_count: 0,
  request_count: 708,
  cache_hit_count: 2,
  collection_complete: true,
  complete: false,
  last_job: { task_id: "bulk-fixture", status: "partial" },
});
assert.equal(completeStatus.available, 710);
assert.equal(completeStatus.partial, 710);
assert.equal(completeStatus.complete, 0);
assert.equal(completeStatus.failed, 0);
assert.equal(completeStatus.collectionComplete, true);
assert.equal(completeStatus.historyComplete, false);
assert.equal(completeStatus.lastJob.task_id, "bulk-fixture");
const gap = { reason: "来源日期不完整" };
assert.deepEqual(visibleWarnings([null, "缺日报价", gap]), ["缺日报价", gap]);
assert.deepEqual(visibleWarnings(null), []);
console.log(
  "data controls: publication guard, failed retry, independent supplements and retained warning details passed",
);

assert.equal(incrementWindowView(undefined).allowed, false);
assert.equal(
  incrementWindowView({ increment_allowed: false, reason: "18点后" }).reason,
  "18点后",
);
assert.equal(incrementWindowView({ increment_allowed: true }).allowed, true);
assert.equal(incrementWindowView({ increment_allowed: "true" }).allowed, false);
for (const years of [5, 10]) {
  const config = {
    default_window: `near-${years}-years`,
    default_years: years,
    default_label: `近${years}年`,
  };
  assert.equal(initializationWindowView(config).window, `near-${years}-years`);
  assert.equal(
    initializationView({ main_published: false, can_initialize: true }, config)
      .label,
    `开始近${years}年初始化`,
  );
}
assert.equal(initializationWindowView(undefined).known, false);
assert.equal(
  initializationWindowView({
    default_window: "near-ten-years",
    default_years: null,
  }).known,
  false,
);
