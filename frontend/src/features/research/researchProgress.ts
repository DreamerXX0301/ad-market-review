type Task = Record<string, any>;

const statusLabels: Record<string, string> = {
  submitting: "正在提交",
  queued: "排队等待",
  pending: "排队等待",
  running: "研究进行中",
  completed: "研究已完成",
  succeeded: "研究已完成",
  success: "研究已完成",
  no_change: "研究已完成",
  partial: "已完成 · 材料有缺口",
  failed: "研究失败",
  cancelled: "研究已取消",
  canceled: "研究已取消",
  paused: "研究已暂停",
};
const stageLabels: Record<string, string> = {
  submitting: "提交研究任务",
  queued: "等待工作进程",
  pending: "等待工作进程",
  starting: "准备研究材料",
  running: "查询与核对研究材料",
  fetching: "查询研究材料",
  collecting: "汇总研究材料",
  saving: "保存研究记录",
  recovery: "恢复已授权研究任务",
  completed: "研究记录已留存",
  succeeded: "研究记录已留存",
  success: "研究记录已留存",
  no_change: "研究记录已留存",
  partial: "研究记录已留存，部分材料缺失",
  failed: "研究未能完成",
  cancelled: "任务已停止",
  canceled: "任务已停止",
  paused: "等待手动继续",
};
const completeStatuses = new Set([
  "completed",
  "succeeded",
  "success",
  "no_change",
  "partial",
]);
const activeStatuses = new Set(["submitting", "queued", "pending", "running"]);
const hasChinese = (value: string) => /[\u3400-\u9fff]/.test(value);
const stringValue = (value: unknown): string =>
  typeof value === "string" ? value.trim() : "";

export function researchTaskStage(value: unknown, fallback = "研究阶段待确认") {
  const stage = stringValue(value);
  return stageLabels[stage] || (hasChinese(stage) ? stage : fallback);
}

export function researchTaskError(
  value: unknown,
  fallback = "研究任务未能完成，请查看执行记录后手动重试",
) {
  const message =
    stringValue(value) ||
    (value && typeof value === "object"
      ? stringValue((value as Task).message) ||
        stringValue((value as Task).reason) ||
        stringValue((value as Task).detail)
      : "");
  return message || fallback;
}

export function researchTaskProgress(task: Task | null | undefined) {
  const status = stringValue(task?.status) || "unknown";
  const rawProgress = task?.progress ?? task?.progress_pct;
  const reportedProgress =
    typeof rawProgress === "number" &&
    Number.isFinite(rawProgress) &&
    rawProgress >= 0 &&
    rawProgress <= 100
      ? rawProgress
      : null;
  const complete = completeStatuses.has(status);
  // Completion and queue state have explicit semantics. Running time is never
  // converted into a percentage; retain the last real phase value until updated.
  const progress = complete
    ? 100
    : (reportedProgress ??
      (status === "queued" || status === "pending" ? 0 : null));
  const active = activeStatuses.has(status);
  const failed = status === "failed";
  const interrupted = ["cancelled", "canceled", "paused"].includes(status);
  const message = stringValue(task?.message);
  const stage = researchTaskStage(
    task?.stage || task?.phase || status,
    active ? "查询与核对研究材料" : "研究阶段待确认",
  );
  const records: Task[] = Array.isArray(task?.logs)
    ? task.logs
    : Array.isArray(task?.events)
      ? task.events
      : [];
  return {
    status,
    statusLabel: statusLabels[status] || "任务状态待确认",
    stage,
    progress,
    progressLabel:
      progress === null ? "进度待确认" : `${Number(progress.toFixed(1))}%`,
    active,
    complete,
    tone: failed
      ? "danger"
      : status === "partial" || interrupted
        ? "warn"
        : complete
          ? "good"
          : "neutral",
    message: failed
      ? researchTaskError(task?.error || task?.error_summary || message)
      : hasChinese(message)
        ? message
        : status === "queued" || status === "pending"
          ? "任务已提交，等待工作进程；开始执行后会显示实际阶段。"
          : status === "submitting"
            ? "正在提交研究请求，尚未开始查询材料。"
            : status === "paused"
              ? "任务已暂停，可在数据中心手动继续。"
              : interrupted
                ? "任务已停止，已有行情数据不受影响。"
                : complete
                  ? "研究已结束；材料是否完整请以报告的来源与缺口为准。"
                  : "未回报新阶段时进度保持不变，不按等待时长估算。",
    logs: records.map((event, index) => ({
      key: event.seq ?? event.event_id ?? index,
      time: event.time ?? event.occurred_at_ms,
      stage: researchTaskStage(
        event.phase || event.stage || event.status,
        "研究记录",
      ),
      message: stringValue(event.message) || "任务状态已更新",
      level: event.level,
    })),
  };
}
