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
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
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
