type DataRow = Record<string, any>;

export function dataTaskTitle(task: DataRow) {
  const operation = task.parameters?.operation;
  if (task.kind === "source_check" && operation === "sector_charts")
    return task.parameters?.mode === "increment"
      ? "板块K线尾部增量"
      : "行业与概念全库K线";
  if (task.kind === "source_check" && operation === "sector_chart") {
    const name =
      task.parameters?.instrument?.name || task.parameters?.topic_code;
    return `单板块历史K线${name ? " · " + name : ""}`;
  }
  if (task.kind === "source_check" && operation === "market_supplement")
    return "盘面细节与年内涨跌停回补";
  return (
    task.title ||
    (
      {
        initialise: "首次初始化",
        increment: "盘后增量同步",
        initial_sync: "首次初始化",
        increment_sync: "盘后增量同步",
        topic_refresh: "完整题材库同步",
        topic_init: "本地题材导入",
        research: "个股研究",
        backtest: "策略回测",
        selection: "策略选股",
        tracking_review: "跟踪条件核对",
        source_check: "来源能力检查",
      } as DataRow
    )[task.mode || task.kind] ||
    task.stage ||
    "后台任务"
  );
}

const countOrUnknown = (value: unknown): number | null =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;

export function sectorChartsInitializationView(
  initialization: DataRow | null | undefined,
) {
  const initialized = initialization?.initialized === true;
  const known =
    typeof initialization?.initialized === "boolean" &&
    typeof initialization?.can_initialize === "boolean";
  return {
    initialized,
    known,
    canInitialize:
      known && !initialized && initialization?.can_initialize === true,
    label: initialized
      ? "板块K线已初始化"
      : known
        ? "补齐全部板块K线"
        : "正在确认板块初始化状态",
    reason:
      initialization?.reason ||
      (initialized
        ? "板块历史K线已完成首次初始化，后续使用盘后增量追加尾部报价。"
        : known
          ? "仅首次补齐；真正失败或未完成时可继续补缺。"
          : "尚未取得板块初始化状态，请刷新后再操作。"),
    initializedDate: initialization?.initialized_date || null,
  };
}

export function isSectorChartsInitialTask(task: DataRow) {
  return (
    task.kind === "source_check" &&
    task.parameters?.operation === "sector_charts" &&
    task.parameters?.mode !== "increment"
  );
}

export function sectorChartsTaskBlocked(
  task: DataRow,
  initialization: DataRow | null | undefined,
) {
  return (
    isSectorChartsInitialTask(task) &&
    sectorChartsInitializationView(initialization).initialized
  );
}

/** Counts come from worker receipts, never from progress percentages. */
export function sectorChartsTaskProgress(task: DataRow) {
  if (
    task.kind !== "source_check" ||
    task.parameters?.operation !== "sector_charts"
  )
    return null;
  const counts: DataRow = {};
  let coverage: DataRow = {};
  for (const event of task.logs || []) {
    const details = event.details || {};
    for (const key of [
      "completed_count",
      "total_count",
      "available_count",
      "request_count",
      "cache_hit_count",
      "failed_count",
      "unrequested_count",
    ])
      if (countOrUnknown(details[key]) !== null) counts[key] = details[key];
    if (details.coverage && typeof details.coverage === "object")
      coverage = details.coverage;
  }
  return {
    completed: countOrUnknown(counts.completed_count),
    total: countOrUnknown(counts.total_count ?? coverage.catalog_count),
    available: countOrUnknown(counts.available_count),
    requested: countOrUnknown(counts.request_count),
    cached: countOrUnknown(counts.cache_hit_count),
    failed: countOrUnknown(counts.failed_count),
    unrequested: countOrUnknown(counts.unrequested_count),
    complete: countOrUnknown(coverage.complete_count),
    partial: countOrUnknown(coverage.partial_count),
    collectionComplete:
      typeof coverage.collection_complete === "boolean"
        ? coverage.collection_complete
        : null,
    historyComplete:
      typeof coverage.complete === "boolean" ? coverage.complete : null,
  };
}

/** Absent batch receipts are unknown even when a few single-code charts exist. */
export function sectorChartsStatusView(
  configuration: DataRow | null | undefined,
) {
  return {
    targetDate:
      typeof configuration?.target_date === "string"
        ? configuration.target_date
        : null,
    catalog: countOrUnknown(configuration?.catalog_count),
    available: countOrUnknown(configuration?.available_count),
    complete: countOrUnknown(configuration?.complete_count),
    partial: countOrUnknown(configuration?.partial_count),
    failed: countOrUnknown(configuration?.failed_count),
    unrequested: countOrUnknown(configuration?.unrequested_count),
    requested: countOrUnknown(configuration?.request_count),
    cached: countOrUnknown(configuration?.cache_hit_count),
    collectionComplete:
      typeof configuration?.collection_complete === "boolean"
        ? configuration.collection_complete
        : null,
    historyComplete:
      typeof configuration?.complete === "boolean"
        ? configuration.complete
        : null,
    lastJob: configuration?.last_job || null,
    verifiedAt: configuration?.verified_at ?? null,
  };
}

/** Publication and source readiness are separate; partial follow-ups cannot reopen initialization. */
export function initializationWindowView(
  configuration: DataRow | null | undefined,
) {
  const window =
    typeof configuration?.default_window === "string" &&
    configuration.default_window.trim()
      ? configuration.default_window
      : null;
  const years =
    typeof configuration?.default_years === "number" &&
    Number.isFinite(configuration.default_years) &&
    configuration.default_years > 0
      ? configuration.default_years
      : null;
  return {
    window,
    years,
    known: !!window && years !== null,
    label:
      typeof configuration?.default_label === "string" &&
      configuration.default_label
        ? configuration.default_label
        : years
          ? `近${years}年`
          : "已配置历史窗口",
    notice:
      configuration?.notice ||
      "实际覆盖以发布后逐项核验为准，其他模块各有独立留存范围。",
  };
}
export function initializationView(
  initialization: DataRow | null | undefined,
  window?: DataRow | null,
) {
  const mainPublished = initialization?.main_published === true;
  const known =
    typeof initialization?.main_published === "boolean" &&
    typeof initialization?.can_initialize === "boolean";
  return {
    mainPublished,
    known,
    canInitialize:
      known && !mainPublished && initialization?.can_initialize === true,
    label: mainPublished
      ? "主行情已初始化"
      : known
        ? `开始${initializationWindowView(window).label}初始化`
        : "正在确认初始化状态",
    reason:
      initialization?.reason ||
      (known
        ? "仅在首次使用、尚未发布主行情时运行。"
        : "尚未取得后端初始化状态，请刷新后再操作。"),
  };
}

export function initializationTaskBlocked(
  task: DataRow,
  initialization: DataRow | null | undefined,
) {
  return (
    initializationView(initialization).mainPublished &&
    ["initialise", "initial_sync"].includes(task.kind || task.mode)
  );
}

export function visibleWarnings(items: unknown): unknown[] {
  return (Array.isArray(items) ? items : items ? [items] : []).filter(Boolean);
}

/** Server Beijing time is authoritative; missing clock metadata never opens sync. */
export function incrementWindowView(window: DataRow | null | undefined) {
  const allowed = window?.increment_allowed === true;
  return {
    allowed,
    reason: allowed
      ? "北京时间18:00后可运行，行情完成后追加行业与概念板块K线尾部，再衔接目标日盘面补充。"
      : window?.reason || "正在确认北京时间盘后更新窗口，请稍后重试。",
    label: allowed ? "开始盘后增量" : "18:00后可运行",
  };
}
