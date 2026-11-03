import { useEffect } from "react";
import { useApi, usePageActive } from "../../lib/api";
import { aiReason, terminalTask } from "./aiState";
import type { AITask } from "./aiTypes";
import { aiChangeAfterWrite, publishAIChange } from "./aiTabSync";

// Never show a supplier response body: it may contain the submitted material.
export async function aiWrite<T = any>(
  path: string,
  body?: unknown,
  method = "POST",
): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api/v1/ai" + path, {
      method,
      headers:
        body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error(aiReason("network_error"));
  }
  let data: any;
  try {
    data = response.status === 204 ? null : await response.json();
  } catch {
    throw new Error("服务响应格式无法读取，请先核对本地任务；不会自动重发。");
  }
  if (!response.ok) {
    const detail = data?.detail;
    const reason = detail?.reason_code || data?.reason_code || detail?.code;
    const localMessage =
      detail?.origin === "local" &&
      typeof detail?.message === "string" &&
      detail.message.length <= 2000
        ? detail.message
        : "";
    throw Object.assign(
      new Error(
        reason === "budget_exceeded" || reason === "session_busy"
          ? aiReason(reason)
          : localMessage || aiReason(reason, response.status),
      ),
      { reason, status: response.status },
    );
  }
  const change = aiChangeAfterWrite(path, method, data);
  if (change) publishAIChange(change);
  return data;
}
export function useAITask(taskId: string) {
  const task = useApi<AITask>(
    taskId ? `/ai/tasks/${encodeURIComponent(taskId)}` : null,
  );
  const active = usePageActive();
  useEffect(() => {
    if (
      !active ||
      !taskId ||
      task.loading ||
      task.error ||
      terminalTask(task.data?.state)
    )
      return;
    const timer = setTimeout(task.reload, 1500);
    return () => clearTimeout(timer);
  }, [active, taskId, task.loading, task.error, task.data, task.reload]);
  return { ...task, pending: !!taskId && !terminalTask(task.data?.state) };
}
