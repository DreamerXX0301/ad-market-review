type Row = Record<string, any>;
export type StrategyTarget = "stock" | "sector";
export const batchActive = (batch?: Row) =>
  !!batch && ["queued", "running", "paused"].includes(batch.status);
export const batchPolling = (batches: Row[]) =>
  batches.some((batch) => ["queued", "running"].includes(batch.status));
export const batchStatusLabel = (status: string) =>
  ({
    queued: "等待执行",
    running: "执行中",
    paused: "已暂停",
    succeeded: "已完成",
    no_change: "已完成 · 无更新",
    partial: "部分成功",
    failed: "失败",
    cancelled: "已取消",
    pending: "等待执行",
    skipped: "已跳过",
  })[status] || "待确认";
const trackingCounts = [
  "matched_count",
  "added_count",
  "skipped_count",
  "ignored_count",
] as const;
const validCount = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
export function batchTrackingSummary(batch: Row) {
  if (batch.auto_add !== true)
    return {
      automatic: false,
      label: "仅筛选的历史批次",
      text: "本批次仅保存筛选结果，未自动加入跟踪池。",
    };
  const items: Row[] = Array.isArray(batch.items) ? batch.items : [];
  const counts = Object.fromEntries(
    trackingCounts.map((field) => [
      field,
      validCount(batch[field])
        ? batch[field]
        : items.some((item) => validCount(item[field]))
          ? items.reduce(
              (sum, item) => sum + (validCount(item[field]) ? item[field] : 0),
              0,
            )
          : null,
    ]),
  );
  const text = [
    ["matched_count", "命中"],
    ["added_count", "新增跟踪"],
    ["skipped_count", "已跳过"],
    ["ignored_count", "其中手工忽略"],
  ]
    .filter(([field]) => counts[field] !== null)
    .map(([field, label]) => `${label} ${counts[field]}`)
    .join(" · ");
  return {
    automatic: true,
    label: "筛选并自动跟踪",
    text: text
      ? text + (counts.added_count === null ? " · 入池统计待回报" : "")
      : "入池统计待回报；已跟踪和手工忽略的命中会跳过。",
  };
}
export function batchItemTrackingText(item: Row, automatic: boolean) {
  if (!automatic) return "仅筛选，未自动入池";
  const counts = [
    ["matched_count", "命中"],
    ["added_count", "新增"],
    ["skipped_count", "跳过"],
    ["ignored_count", "其中手工忽略"],
  ]
    .filter(([field]) => validCount(item[field]))
    .map(([field, label]) => `${label} ${item[field]}`);
  return [
    `筛选：${batchStatusLabel(item.selection_status || "unknown")}`,
    `入池：${item.tracking_status ? batchStatusLabel(item.tracking_status) : "待回报"}`,
    ...counts,
  ].join(" · ");
}
export function batchRequest(
  previous: Row | null,
  target: StrategyTarget,
  scope: "selected" | "all",
  ids: string[],
  day: string | undefined,
  createKey: () => string,
) {
  const selected = [...new Set(ids)];
  const fingerprint = JSON.stringify([
    target,
    scope,
    scope === "selected" ? selected : [],
    day || "",
  ]);
  if (previous?.fingerprint === fingerprint) return previous;
  return {
    fingerprint,
    body: {
      target_type: target,
      scope,
      ...(scope === "selected" ? { strategy_ids: selected } : {}),
      request_key: createKey(),
    },
  };
}
export function nextStrategySelection(
  selected: Set<string>,
  ids: string[],
  checked: boolean,
) {
  const next = new Set(selected);
  for (const id of ids) checked ? next.add(id) : next.delete(id);
  return next;
}
export function mergeBatch(batches: Row[], batch: Row) {
  return batch?.batch_id
    ? [
        batch,
        ...batches.filter((item) => item.batch_id !== batch.batch_id),
      ].slice(0, 10)
    : batches;
}

/** GET-only serial feed. Hidden routes abort reads and never queue another poll. */
export function createBatchFeed({
  read,
  onData,
  onError,
  onProgress,
  schedule = (callback: () => void, delay: number) =>
    setTimeout(callback, delay),
  cancel = (timer: any) => clearTimeout(timer),
}: {
  read: (signal: AbortSignal) => Promise<Row[]>;
  onData: (batches: Row[]) => void;
  onError: (message: string) => void;
  onProgress: () => void;
  schedule?: (callback: () => void, delay: number) => any;
  cancel?: (timer: any) => void;
}) {
  let active = false,
    controller: AbortController | null = null,
    timer: any = null,
    refreshAgain = false;
  const completed = new Map<string, number>();
  function stopTimer() {
    if (timer !== null) cancel(timer);
    timer = null;
  }
  async function refresh() {
    stopTimer();
    if (!active) return;
    if (controller) {
      refreshAgain = true;
      return;
    }
    const request = new AbortController();
    controller = request;
    let keepPolling = false;
    try {
      const batches = await read(request.signal);
      if (!active || request.signal.aborted) return;
      onData(batches);
      onError("");
      keepPolling = batchPolling(batches);
      let changed = false;
      for (const batch of batches) {
        const count = Number.isInteger(batch.completed_count)
          ? batch.completed_count
          : 0;
        if (count > (completed.get(batch.batch_id) ?? 0)) changed = true;
        completed.set(
          batch.batch_id,
          Math.max(completed.get(batch.batch_id) ?? 0, count),
        );
      }
      if (changed) onProgress();
    } catch (error: any) {
      if (active && !request.signal.aborted) {
        onError(error.message);
        keepPolling = true;
      }
    } finally {
      if (controller === request) controller = null;
      if (active) {
        const again = refreshAgain;
        refreshAgain = false;
        if (again || keepPolling)
          timer = schedule(() => void refresh(), again ? 0 : 2000);
      }
    }
  }
  return {
    refresh,
    setActive(next: boolean) {
      if (active === next) return;
      active = next;
      stopTimer();
      if (!next) {
        refreshAgain = false;
        controller?.abort();
      } else void refresh();
    },
    dispose() {
      active = false;
      refreshAgain = false;
      stopTimer();
      controller?.abort();
    },
  };
}
