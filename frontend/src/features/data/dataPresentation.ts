type DataRow = Record<string, any>;

export function dataTaskTitle(task: DataRow) {
  const operation = task.parameters?.operation;
  if (task.kind === "source_check" && operation === "noon_snapshot")
    return "午评快照采集";
  if (task.kind === "source_check" && operation === "local_coverage_check")
    return "本地数据核验";
  if (task.kind === "source_check" && operation === "quote_gap_source_check")
    return "向来源核验报价缺口";
  if (task.kind === "source_check" && operation === "listing_status_check")
    return "上市身份核验";
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

/** A noon day keeps its original task permanently; retries must never create another. */
export function dataNoonTaskControls(task: DataRow) {
  const noon =
    task.kind === "source_check" &&
    (task.parameters?.operation === "noon_snapshot" ||
      task.operation === "noon_snapshot");
  return {
    noon,
    canRetry: !noon,
    canResumeHere: !noon,
    notice: noon
      ? task.status === "paused"
        ? "午评只能在原交易日午休窗口继续原任务，请到午评页核对窗口；不会新建采集。"
        : "午评每天只提交一次，本日名额已占用，失败、取消或到期清理均不创建重试任务。"
      : null,
  };
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

/** Coverage percentages need their own evidence and denominator, not row share. */
export function datasetCoverageView(dataset: DataRow) {
  const progress = dataset.coverage_progress || {};
  const observed = countOrUnknown(progress.observed_count);
  const expected = countOrUnknown(progress.expected_count);
  const valid =
    progress.state === "known" &&
    observed !== null &&
    expected !== null &&
    expected > 0 &&
    observed <= expected;
  const percent = valid ? (observed / expected) * 100 : null;
  const suppliedState =
    progress.coverage_status || progress.display_coverage_state;
  const displayState =
    suppliedState === "on_demand" && !valid
      ? "on_demand"
      : !valid
        ? "unknown"
        : observed === expected
          ? "covered"
          : "partial";
  const suppliedLabel =
    progress.coverage_label || progress.display_coverage_label;
  const displayLabel =
    suppliedState === displayState &&
    typeof suppliedLabel === "string" &&
    suppliedLabel
      ? suppliedLabel
      : (
          {
            covered: "本项已覆盖",
            partial: "存在缺口",
            unknown: "待核验",
            on_demand: "按需计算",
          } as const
        )[displayState];
  const percentText =
    percent === null
      ? null
      : percent === 100
        ? "100%"
        : percent > 99.99
          ? "<100%"
          : percent > 0 && percent < 0.01
            ? "<0.01%"
            : `${percent.toFixed(2)}%`;
  return {
    label: progress.label || "覆盖完整度",
    observed,
    expected,
    percent,
    unit: progress.unit || "条",
    basis: progress.basis || "应有总量尚未核验，记录条数不用于推算完整度",
    reason: progress.reason || (valid ? null : "覆盖比例待核验"),
    known: valid,
    displayState,
    displayLabel,
    percentText,
    catalogCount: countOrUnknown(progress.catalog_count),
    pendingListingCount: countOrUnknown(progress.pending_listing_count),
    unknownIdentityCount: countOrUnknown(progress.unknown_count),
  };
}

export function listingIdentityView(identities: DataRow | undefined | null) {
  const catalog = countOrUnknown(
    identities?.catalog_count ?? identities?.stock_count,
  );
  const listed = countOrUnknown(
    identities?.listed_count ?? identities?.known_count,
  );
  const pending = countOrUnknown(identities?.pending_listing_count);
  const unknown = countOrUnknown(identities?.unknown_count);
  const consistent =
    catalog !== null &&
    listed !== null &&
    pending !== null &&
    unknown !== null &&
    listed + pending + unknown === catalog;
  const expected = consistent ? listed + unknown : null;
  return {
    catalog,
    listed,
    pending,
    unknown,
    expected,
    complete: consistent && unknown === 0 && expected !== null && expected > 0,
  };
}

export function listingConfirmationDraft(
  text: string,
  status: string,
  reason: string,
  evidence: string,
  businessDate?: string,
) {
  const identifiers = [
    ...new Set(
      text
        .toUpperCase()
        .split(/[\s,，;；]+/)
        .filter(Boolean),
    ),
  ];
  if (!identifiers.length || identifiers.length > 128)
    return { error: "每次请选择1至128只股票。" };
  if (identifiers.some((id) => !/^\d{6}\.(SH|SZ|BJ)$/.test(id)))
    return {
      error: "请填写完整股票代码，例如000001.SZ；不能仅凭六位代码猜测交易所。",
    };
  if (!["pending", "unknown"].includes(status))
    return { error: "请选择人工确认的身份状态。" };
  if (!reason.trim() || reason.trim().length > 1000)
    return { error: "请填写1至1000字的核验说明。" };
  if (evidence.trim().length > 4000) return { error: "补充依据最多4000字。" };
  if (!businessDate || !/^\d{4}-\d{2}-\d{2}$/.test(businessDate))
    return { error: "当前业务日尚未读取，请刷新后再确认。" };
  return {
    error: null,
    payload: {
      instrument_ids: identifiers,
      status,
      reason: reason.trim(),
      evidence: evidence.trim(),
      observed_business_date: businessDate,
    },
  };
}

export function listingEvidenceLines(row: DataRow) {
  const reference = row.reference;
  if (typeof reference === "string") return reference.trim() ? [reference] : [];
  if (!reference || typeof reference !== "object") return [];
  const values: string[] = [];
  const quote = reference.quote_presence;
  if (countOrUnknown(quote?.valid_quote_count) !== null)
    values.push(
      `本代有效成交报价 ${quote.valid_quote_count} 条${quote.first_quote_date ? `；首次 ${quote.first_quote_date}` : ""}${quote.last_quote_date ? `，最近 ${quote.last_quote_date}` : ""}`,
    );
  const manual = reference.manual_confirmation;
  if (typeof manual?.evidence === "string" && manual.evidence.trim())
    values.push(manual.evidence.trim());
  if (manual?.confirmation_id)
    values.push(
      `人工确认记录 ${manual.confirmation_id}${manual.observed_business_date ? ` · 观察业务日 ${manual.observed_business_date}` : ""}`,
    );
  return values;
}

export function gapEpisodeView(episode: DataRow) {
  const source = episode.source_verification;
  const labels: Record<string, string> = {
    local_missing: "来源有可补报价",
    partial: "来源部分返回报价",
    source_no_quote: "来源同样无报价",
    failed: "来源核验未完成",
  };
  const verified =
    source && typeof source === "object" && typeof source.status === "string";
  return {
    label: verified
      ? labels[source.status] || episode.type_label || "来源证据待核验"
      : episode.type_label || "原因待核验",
    reason:
      verified && source.reason
        ? source.reason
        : episode.reason || "尚无能够确定原因的证据。",
    impact: episode.impact || "该范围的严格日线信号和可比收益需要保留缺口。",
    nextAction:
      verified && source.next_action
        ? source.next_action
        : episode.next_action || "需明确来源或身份依据后再处理，不补造报价。",
    source: verified ? source : null,
  };
}

/** Only local report classifications are shown; a long blank interval never proves suspension. */
export function filterGapDiagnosisStocks(
  diagnosis: DataRow | null | undefined,
  query: string,
  sort: string,
) {
  const term = query.trim().toLowerCase();
  const rows: DataRow[] = Array.isArray(diagnosis?.stocks)
    ? diagnosis.stocks
    : [];
  const value = (row: DataRow) => countOrUnknown(row[sort]);
  return rows
    .filter(
      (row) =>
        !term ||
        [
          row.name,
          row.code,
          row.instrument_id,
          ...(row.episodes || []).flatMap((episode: DataRow) => [
            episode.start_date,
            episode.end_date,
            gapEpisodeView(episode).label,
            gapEpisodeView(episode).reason,
            ...(episode.dates || []),
          ]),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(term),
    )
    .slice()
    .sort((a, b) => {
      const av = value(a),
        bv = value(b);
      return av === null
        ? bv === null
          ? String(a.instrument_id).localeCompare(String(b.instrument_id))
          : 1
        : bv === null
          ? -1
          : bv - av ||
            String(a.instrument_id).localeCompare(String(b.instrument_id));
    });
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
