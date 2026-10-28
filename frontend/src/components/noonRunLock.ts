import type { Row } from "../lib/api";

/** Only opaque request/task identities and a date are retained in the browser. */
export const NOON_RUN_EVENT = "workbench-noon-run-session";
export const NOON_RUN_KEY = "workbench-noon-run-session-v1";
export type NoonRunSession = {
  date: string;
  requestKey: string;
  createdAt: number;
  taskId?: string;
  resumeTaskId?: string;
  resumeAfterSeq?: number;
};
const active = new Set(["queued", "running"]);
const terminal = new Set([
  "succeeded",
  "partial",
  "no_change",
  "failed",
  "cancelled",
  "paused",
  "blocked",
]);
let volatile: NoonRunSession | null = null;
let writable = true;
const id = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0 && value.length <= 200;
const valid = (value: any): value is NoonRunSession =>
  !!value &&
  /^\d{4}-\d{2}-\d{2}$/.test(value.date) &&
  id(value.requestKey) &&
  Number.isFinite(value.createdAt) &&
  value.createdAt >= 0 &&
  (value.taskId === undefined || id(value.taskId)) &&
  (value.resumeTaskId === undefined ||
    (id(value.resumeTaskId) &&
      Number.isSafeInteger(value.resumeAfterSeq) &&
      value.resumeAfterSeq >= 0));
export const isActiveNoonTask = (task: Row) =>
  task.kind === "source_check" &&
  (task.operation || task.parameters?.operation) === "noon_snapshot" &&
  active.has(task.status);
export const isTerminalNoonTask = (task: Row) => terminal.has(task.status);
export function readNoonRun(): NoonRunSession | null {
  try {
    const value = JSON.parse(localStorage.getItem(NOON_RUN_KEY) || "null");
    return writable ? (valid(value) ? value : null) : volatile;
  } catch (error) {
    return error instanceof SyntaxError ? null : volatile;
  }
}
function update(value: NoonRunSession | null) {
  volatile = value;
  try {
    if (value) localStorage.setItem(NOON_RUN_KEY, JSON.stringify(value));
    else localStorage.removeItem(NOON_RUN_KEY);
    writable = true;
  } catch {
    writable = false;
  }
  window.dispatchEvent(new CustomEvent(NOON_RUN_EVENT, { detail: value }));
}
export function beginNoonRun(
  date: string,
  requestKey: string,
  resumeTask?: Row,
) {
  const value: NoonRunSession = { date, requestKey, createdAt: Date.now() };
  if (resumeTask) {
    value.resumeTaskId = resumeTask.task_id || resumeTask.job_id;
    if (!id(value.resumeTaskId)) throw new Error("原午评任务标识无效");
    value.resumeAfterSeq = Math.max(
      0,
      ...(resumeTask.logs || []).map((event: Row) =>
        Number.isSafeInteger(event.seq) ? event.seq : 0,
      ),
    );
  }
  if (!valid(value)) throw new Error("午评请求标识或日期无效");
  const current = readNoonRun();
  if (current && current.requestKey !== requestKey)
    throw new Error("已有午评采集正在确认，请等待当前任务结束");
  update(current || value);
  return current || value;
}
export function releaseNoonRun(requestKey: string) {
  if (readNoonRun()?.requestKey !== requestKey) return false;
  update(null);
  return true;
}
export function acceptNoonRun(requestKey: string, task: Row) {
  const current = readNoonRun();
  if (!current || current.requestKey !== requestKey) return false;
  const taskId = task.task_id || task.job_id;
  if (
    !id(taskId) ||
    (current.taskId && current.taskId !== taskId) ||
    (current.resumeTaskId && current.resumeTaskId !== taskId)
  )
    return false;
  if (isTerminalNoonTask(task)) return releaseNoonRun(requestKey);
  if (!active.has(task.status)) return false;
  update({ ...current, taskId });
  return true;
}
/** A GET may overtake the POST. Missing/old terminal state is not rejection. */
export function reconcileNoonRun(requestKey: string, view: Row) {
  const current = readNoonRun();
  if (
    !current ||
    current.requestKey !== requestKey ||
    view.date !== current.date
  )
    return false;
  const task = view.task;
  if (
    !task ||
    task.kind !== "source_check" ||
    (task.operation || task.parameters?.operation) !== "noon_snapshot" ||
    task.target_date !== current.date
  )
    return false;
  if (current.resumeTaskId) {
    if ((task.task_id || task.job_id) !== current.resumeTaskId) return false;
    const resumed = (task.logs || []).some(
      (event: Row) =>
        Number.isSafeInteger(event.seq) &&
        event.seq > current.resumeAfterSeq! &&
        event.stage === "queued",
    );
    if (!active.has(task.status) && !resumed) return false;
  } else if (task.request_key !== current.requestKey) return false;
  return acceptNoonRun(requestKey, task);
}
/** These server responses reject admission; timeout/5xx/network remain unknown. */
export function noonRequestRejected(error: { status?: number; name?: string }) {
  return (
    error?.name !== "AbortError" &&
    [400, 401, 403, 404, 405, 409, 410, 422, 423, 429].includes(
      error?.status || 0,
    )
  );
}
export function noonSessionFromTask(task: Row): NoonRunSession | null {
  if (!isActiveNoonTask(task)) return null;
  const value = {
    date: task.target_date,
    requestKey: task.request_key,
    taskId: task.task_id || task.job_id,
    createdAt: task.created_at_ms,
  };
  return valid(value) ? value : null;
}
/** A rejected local request can hand the gate directly to an existing run. */
export function adoptExistingNoonRun(requestKey: string, task: Row) {
  const current = readNoonRun();
  const value = noonSessionFromTask(task);
  if (
    !current ||
    current.requestKey !== requestKey ||
    !value ||
    value.date !== current.date
  )
    return false;
  update(value);
  return true;
}
export function restoreNoonRun(task: Row) {
  const value = noonSessionFromTask(task);
  if (!value) return false;
  const current = readNoonRun();
  if (current && current.requestKey !== value.requestKey) return false;
  // Activity is a cached summary and may still describe the pre-pause run.
  // A pending resume must keep its generation until a fresh GET proves it.
  if (current?.resumeTaskId && !current.taskId) return false;
  if (current?.taskId && current.taskId !== value.taskId) return false;
  if (
    !current ||
    current.taskId !== value.taskId ||
    current.date !== value.date ||
    current.createdAt !== value.createdAt
  )
    update(value);
  return true;
}
