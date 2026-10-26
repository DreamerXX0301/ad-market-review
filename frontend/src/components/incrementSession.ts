/** Only opaque task identifiers and UI state are persisted; no source settings. */
export const INCREMENT_SESSION_EVENT = "workbench-increment-session";
export const INCREMENT_SESSION_KEY = "workbench-increment-session-v1";
export type IncrementSession = {
  requestKey: string;
  taskId?: string;
  createdAt: number;
  rejection?: string;
  epoch?: string;
};
let volatileSession: IncrementSession | null = null;
let storageWritable = true;
export function readIncrementSession(): IncrementSession | null {
  try {
    const value = JSON.parse(
      localStorage.getItem(INCREMENT_SESSION_KEY) || "null",
    );
    const valid =
      value &&
      typeof value.requestKey === "string" &&
      value.requestKey.length > 0 &&
      value.requestKey.length <= 200 &&
      typeof value.createdAt === "number" &&
      Number.isFinite(value.createdAt) &&
      value.createdAt >= 0 &&
      (value.taskId === undefined ||
        (typeof value.taskId === "string" &&
          value.taskId.length > 0 &&
          value.taskId.length <= 200)) &&
      (value.rejection === undefined ||
        (typeof value.rejection === "string" &&
          value.rejection.length <= 2000)) &&
      (value.epoch === undefined ||
        (typeof value.epoch === "string" && value.epoch.length <= 200));
    if (!storageWritable) return volatileSession;
    return valid
      ? {
          requestKey: value.requestKey,
          createdAt: value.createdAt,
          ...(value.taskId ? { taskId: value.taskId } : {}),
          ...(value.rejection ? { rejection: value.rejection } : {}),
          ...(value.epoch ? { epoch: value.epoch } : {}),
        }
      : null;
  } catch (error) {
    return error instanceof SyntaxError ? null : volatileSession;
  }
}
function update(value: IncrementSession | null) {
  volatileSession = value;
  try {
    if (value)
      localStorage.setItem(INCREMENT_SESSION_KEY, JSON.stringify(value));
    else localStorage.removeItem(INCREMENT_SESSION_KEY);
    storageWritable = true;
  } catch {
    storageWritable = false;
    /* The current tab remains blocked when browser storage is restricted. */
  }
  window.dispatchEvent(
    new CustomEvent(INCREMENT_SESSION_EVENT, { detail: value }),
  );
}
export function beginIncrementSession(requestKey: string) {
  if (!requestKey || requestKey.length > 200)
    throw new Error("增量请求标识无效，请刷新后重试");
  if (readIncrementSession())
    throw new Error("已有盘后增量尚待确认，请先查看当前进度和结果");
  const value = { requestKey, createdAt: Date.now() };
  update(value);
  return value;
}
export function acceptIncrementSession(
  value: IncrementSession,
  taskId: string,
  canonicalRequestKey?: string,
) {
  if (readIncrementSession()?.requestKey !== value.requestKey) return;
  update({
    ...value,
    requestKey: canonicalRequestKey || value.requestKey,
    taskId,
  });
}
export function rejectIncrementSession(
  value: IncrementSession,
  message: string,
) {
  if (readIncrementSession()?.requestKey !== value.requestKey) return;
  update({ ...value, rejection: message });
}
/** Adopt only opaque, validated identities from the server's durable gate. */
export function restoreIncrementSession(value: Record<string, any>) {
  if (
    typeof value?.request_key !== "string" ||
    !value.request_key ||
    value.request_key.length > 200 ||
    typeof value.root_task_id !== "string" ||
    !value.root_task_id ||
    value.root_task_id.length > 200 ||
    !Number.isFinite(value.created_at_ms) ||
    value.created_at_ms < 0
  )
    return false;
  const next: IncrementSession = {
    requestKey: value.request_key,
    taskId: value.root_task_id,
    createdAt: value.created_at_ms,
    ...(typeof (value.session_epoch || value.epoch) === "string"
      ? { epoch: value.session_epoch || value.epoch }
      : {}),
  };
  const current = readIncrementSession();
  if (
    current?.requestKey !== next.requestKey ||
    current?.taskId !== next.taskId ||
    current?.createdAt !== next.createdAt ||
    current?.epoch !== next.epoch ||
    current?.rejection
  )
    update(next);
  return true;
}
export function acknowledgeIncrementSession(requestKey: string) {
  if (readIncrementSession()?.requestKey !== requestKey) return false;
  update(null);
  return true;
}
