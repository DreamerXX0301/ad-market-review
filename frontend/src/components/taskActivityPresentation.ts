type Task = Record<string, any>;

export const activeTaskStatuses = new Set(["queued", "running", "submitting"]);
const completed = new Set(["succeeded", "no_change", "partial"]);
const labels: Record<string, string> = {
  submitting: "正在提交",
  queued: "排队等待",
  running: "正在运行",
  paused: "已暂停",
  succeeded: "已完成",
  no_change: "已完成 · 无新增",
  partial: "已完成 · 有缺口",
  failed: "运行失败",
  cancelled: "已取消",
};
const stages: Record<string, string> = {
  queued: "等待工作进程领取",
  starting: "准备任务数据",
  evaluating: "核验策略条件",
  reviewing: "核对跟踪条件",
  completed: "结果已保存",
  succeeded: "任务已完成",
  failed: "任务未完成",
  interrupted: "计算进程中断",
  cancelled: "任务已停止",
  paused: "等待手动继续",
};

export function activityPresentation(task: Task) {
  const status = task.status || "unknown";
  const raw = task.progress;
  const measured =
    typeof raw === "number" && Number.isFinite(raw) && raw >= 0 && raw <= 100;
  const progress = completed.has(status) ? 100 : measured ? raw : null;
  const stage = task.stage || task.phase;
  const count = task.actual_completed_batches,
    total = task.actual_total_batches;
  return {
    active: activeTaskStatuses.has(status),
    statusLabel: labels[status] || "状态待确认",
    progress,
    progressLabel:
      progress === null ? "进度待回报" : `${Number(progress.toFixed(1))}%`,
    stage:
      stages[stage] ||
      (typeof stage === "string" && /[\u3400-\u9fff]/.test(stage)
        ? stage
        : "等待服务端回报阶段"),
    title: `${task.title || "后台任务"}${task.strategy_name ? " · " + task.strategy_name : ""}`,
    countLabel:
      Number.isSafeInteger(count) &&
      Number.isSafeInteger(total) &&
      count >= 0 &&
      total >= count
        ? `${count} / ${total}`
        : "",
    tone:
      status === "failed"
        ? "danger"
        : status === "partial" || status === "paused" || status === "cancelled"
          ? "warn"
          : completed.has(status)
            ? "good"
            : "active",
  };
}

export function activityPollDelay(
  activeCount: number,
  hidden: boolean,
  failed = false,
) {
  return hidden ? null : failed ? 15000 : activeCount ? 2000 : 30000;
}

export function visibleActivity(
  tasks: Task[],
  dismissed: Set<string>,
  now: number,
) {
  const active = tasks.filter((task) => activeTaskStatuses.has(task.status));
  const recent = tasks.filter(
    (task) =>
      !activeTaskStatuses.has(task.status) &&
      !dismissed.has(task.job_id) &&
      task.finished_at_ms <= now &&
      (activityNoticeExpiry(task) ?? 0) > now,
  );
  return [...active, ...recent.slice(0, active.length ? 1 : 2)];
}

/** Absolute server finish time prevents polling/navigation from renewing a notice. */
export function activityNoticeExpiry(task: Task): number | null {
  if (activeTaskStatuses.has(task.status)) return null;
  const finished = task.finished_at_ms;
  if (
    typeof finished !== "number" ||
    !Number.isFinite(finished) ||
    finished <= 0
  )
    return null;
  return finished + (completed.has(task.status) ? 6000 : 10000);
}

export function nextActivityNoticeDelay(
  tasks: Task[],
  dismissed: Set<string>,
  now: number,
) {
  const expiries = tasks
    .filter((task) => !dismissed.has(task.job_id) && task.finished_at_ms <= now)
    .map(activityNoticeExpiry)
    .filter((value): value is number => value !== null && value > now);
  return expiries.length ? Math.max(1, Math.min(...expiries) - now) : null;
}

export type ActivityClock = {
  observedAtMs: number;
  receivedAtMs: number;
  receivedMonotonicMs: number;
};
export function activityClockNow(
  clock: ActivityClock | null,
  wallNow: number,
  monotonicNow: number,
) {
  if (!clock) return wallNow;
  // Both elapsed clocks handle tab suspension; the monotonic clock also guards
  // local wall-clock rollback. Their absolute offsets never affect server time.
  return (
    clock.observedAtMs +
    Math.max(
      0,
      wallNow - clock.receivedAtMs,
      monotonicNow - clock.receivedMonotonicMs,
    )
  );
}
