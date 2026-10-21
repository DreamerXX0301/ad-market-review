type Entry = Record<string, any>;
export type SectorChartSelection = {
  topicCode: string;
  name: string;
  proxy: boolean;
  notice: string;
};

const validCode = (value: unknown): value is string =>
  typeof value === "string" && /^\d{6}\.(TI|SH|SZ)$/.test(value);

function directCode(row: Entry) {
  for (const value of [
    row.topic_code,
    row.thscode,
    typeof row.topic_id === "string"
      ? row.topic_id.replace(/^fuyao-/, "")
      : null,
  ])
    if (validCode(value)) return value;
  return null;
}

export function sectorChartTarget(row: Entry, candidates: Entry[] = []) {
  const direct = directCode(row);
  if (direct)
    return {
      topicCode: direct,
      name: typeof row.name === "string" ? row.name : direct,
      proxy: false,
      notice: "同花顺板块指数历史日K；历史成分股覆盖另行判断",
      reason: "",
    };
  const matches = candidates.filter(
    (candidate) =>
      row.kind === "industry" &&
      candidate.kind === "industry" &&
      typeof row.name === "string" &&
      row.name.trim() !== "" &&
      typeof candidate.name === "string" &&
      candidate.name.trim() === row.name.trim() &&
      directCode(candidate),
  );
  const unique = [
    ...new Set(matches.map((candidate) => directCode(candidate))),
  ];
  if (unique.length === 1)
    return {
      topicCode: unique[0],
      name: row.name,
      proxy: true,
      notice: "同名同花顺行业指数；与东财资金分类分别计算，不代表相同成分",
      reason: "",
    };
  return {
    topicCode: null,
    name: typeof row.name === "string" ? row.name : "板块",
    proxy: false,
    notice: "",
    reason:
      unique.length > 1
        ? "同名行业对应多个同花顺指数，尚不能唯一定位板块K线"
        : "尚无可核验的同名同花顺指数标识，不能用资金分类代码猜测板块K线",
  };
}

export function sectorChartRequest(topicCode: string, date: string) {
  if (!validCode(topicCode) || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  return (
    "/market/sector-chart?" +
    new URLSearchParams({ topic_code: topicCode, date })
  );
}

export function sectorTaskMatches(
  task: Entry | null | undefined,
  topicCode: string,
  date: string,
) {
  return (
    !!task &&
    task.parameters?.operation === "sector_chart" &&
    task.parameters?.topic_code === topicCode &&
    task.parameters?.as_of === date &&
    task.target_date === date &&
    typeof (task.task_id || task.job_id) === "string"
  );
}

const success = new Set([
  "completed",
  "succeeded",
  "success",
  "no_change",
  "partial",
]);
const active = new Set(["submitting", "queued", "pending", "running"]);
const captions: Record<string, string> = {
  submitting: "正在提交",
  queued: "排队等待",
  pending: "排队等待",
  running: "加载进行中",
  completed: "加载已完成",
  succeeded: "加载已完成",
  success: "加载已完成",
  no_change: "缓存已可用",
  partial: "已完成 · 数据有缺口",
  failed: "加载失败",
  paused: "已暂停",
  cancelled: "已取消",
  canceled: "已取消",
};
const stages: Record<string, string> = {
  submitting: "提交单板块历史任务",
  queued: "等待工作进程",
  pending: "等待工作进程",
  running: "读取并核验板块历史",
  starting: "准备板块历史",
  recovery: "恢复已授权的板块历史任务",
  fetching: "读取板块历史",
  saving: "保存已核验板块历史",
  succeeded: "板块历史已留存",
  completed: "板块历史已留存",
  success: "板块历史已留存",
  no_change: "已核验缓存可用",
  partial: "板块历史已留存，缺口见说明",
  failed: "板块历史未能完成",
  paused: "等待手动继续",
  cancelled: "任务已停止",
  canceled: "任务已停止",
};

export function sectorTaskProgress(task: Entry) {
  const status = typeof task.status === "string" ? task.status : "unknown";
  const reported = task.progress;
  const numeric =
    typeof reported === "number" &&
    Number.isFinite(reported) &&
    reported >= 0 &&
    reported <= 100;
  const complete = success.has(status);
  const progress = complete
    ? 100
    : status === "queued" || status === "pending"
      ? 0
      : numeric
        ? reported
        : null;
  const stage =
    typeof (task.stage || task.phase) === "string"
      ? task.stage || task.phase
      : status;
  const message = task.error_summary || task.message;
  return {
    status,
    statusLabel: captions[status] || "任务状态待确认",
    active: active.has(status),
    complete,
    progress,
    progressLabel:
      progress === null ? "进度待确认" : `${Number(progress.toFixed(1))}%`,
    stage:
      stages[stage] ||
      (/[\u3400-\u9fff]/.test(stage) ? stage : "读取并核验板块历史"),
    message:
      typeof message === "string" && message.trim()
        ? message
        : status === "paused"
          ? "任务已暂停，可到数据中心手动继续。"
          : complete
            ? "实际历史范围和缺口以图表说明为准。"
            : "进度来自后台任务回报，不按等待时间估算。",
    tone:
      status === "failed"
        ? "danger"
        : ["partial", "paused", "cancelled", "canceled"].includes(status)
          ? "warn"
          : complete
            ? "good"
            : "neutral",
  };
}
