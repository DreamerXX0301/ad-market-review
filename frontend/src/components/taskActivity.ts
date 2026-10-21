import { useSyncExternalStore } from "react";
import { api } from "../lib/api";
import type { Row } from "../lib/api";
import { LINE_ALERT_CHECK_FINISHED } from "../lib/workbenchEvents";
import {
  activeTaskStatuses,
  activityPollDelay,
} from "./taskActivityPresentation";
import type { ActivityClock } from "./taskActivityPresentation";

type Snapshot = {
  tasks: Row[];
  pending: Row[];
  activeCount: number;
  error: string;
  clock: ActivityClock | null;
};
let snapshot: Snapshot = {
  tasks: [],
  pending: [],
  activeCount: 0,
  error: "",
  clock: null,
};
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;
let controller: AbortController | null = null;
let refreshAgain = false;
const notifiedLineChecks = new Set<string>();
const isLineCheck = (task: Row) =>
  task.operation === "line_alert_check" || task.kind === "line_alert_check";
const emit = () => listeners.forEach((listener) => listener());
const stopTimer = () => {
  if (timer) clearTimeout(timer);
  timer = null;
};
function schedule(delay?: number) {
  stopTimer();
  if (!listeners.size) return;
  const next = document.hidden
    ? null
    : (delay ??
      activityPollDelay(
        snapshot.activeCount + snapshot.pending.length,
        false,
        !!snapshot.error,
      ));
  if (next !== null) timer = setTimeout(poll, next);
}
async function poll() {
  if (document.hidden || !listeners.size) return;
  if (controller) {
    refreshAgain = true;
    return;
  }
  const request = new AbortController();
  controller = request;
  try {
    const data = await api<Row>("/tasks/activity", { signal: request.signal });
    if (request.signal.aborted) return;
    const items: Row[] = Array.isArray(data.items) ? data.items : [];
    const observedAtMs =
      typeof data.observed_at_ms === "number" &&
      Number.isFinite(data.observed_at_ms)
        ? data.observed_at_ms
        : Date.now();
    const finishedSelection = items.some((task) => {
      if (task.kind !== "selection" || task.status !== "succeeded")
        return false;
      const previous = snapshot.tasks.find(
        (item) => item.job_id === task.job_id,
      );
      return previous
        ? activeTaskStatuses.has(previous.status)
        : typeof task.finished_at_ms === "number" &&
            observedAtMs - task.finished_at_ms >= 0 &&
            observedAtMs - task.finished_at_ms < 15000;
    });
    const lineCompletions: string[] = [];
    const previousLineKeys = new Set(
      snapshot.tasks
        .filter(isLineCheck)
        .map((task) => `${task.job_id}:${task.finished_at_ms}`),
    );
    for (const task of items) {
      if (
        !isLineCheck(task) ||
        !["succeeded", "partial", "no_change"].includes(task.status) ||
        typeof task.finished_at_ms !== "number" ||
        !Number.isFinite(task.finished_at_ms) ||
        task.finished_at_ms > observedAtMs
      )
        continue;
      const key = `${task.job_id}:${task.finished_at_ms}`;
      if (!notifiedLineChecks.has(key) && !previousLineKeys.has(key))
        lineCompletions.push(key);
      notifiedLineChecks.add(key);
    }
    // A hidden tab can miss a short task and the five retained terminal receipts.
    // Refresh only the saved IDs when a previously active check has aged out.
    for (const task of snapshot.tasks) {
      if (
        isLineCheck(task) &&
        activeTaskStatuses.has(task.status) &&
        !items.some((item) => item.job_id === task.job_id)
      ) {
        const key = `${task.job_id}:aged-out`;
        if (!notifiedLineChecks.has(key)) lineCompletions.push(key);
        notifiedLineChecks.add(key);
      }
    }
    while (notifiedLineChecks.size > 256)
      notifiedLineChecks.delete(notifiedLineChecks.values().next().value!);
    snapshot = {
      ...snapshot,
      tasks: items,
      activeCount: data.active_count ?? 0,
      error: "",
      clock: {
        observedAtMs,
        receivedAtMs: Date.now(),
        receivedMonotonicMs: performance.now(),
      },
    };
    emit();
    // A refreshed browser may have lost the original POST response. Re-read
    // saved metadata when the existing server task finishes; never re-run it.
    if (finishedSelection)
      window.dispatchEvent(
        new CustomEvent("workbench-route", { detail: "strategy" }),
      );
    if (lineCompletions.length)
      window.dispatchEvent(
        new CustomEvent(LINE_ALERT_CHECK_FINISHED, {
          detail: { keys: lineCompletions },
        }),
      );
  } catch (error: any) {
    if (!request.signal.aborted) {
      snapshot = { ...snapshot, error: error.message };
      emit();
    }
  } finally {
    if (controller === request) controller = null;
    const immediate = refreshAgain;
    refreshAgain = false;
    schedule(immediate ? 300 : undefined);
  }
}
function visibilityChanged() {
  if (document.hidden) {
    stopTimer();
    controller?.abort();
  } else schedule(0);
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    document.addEventListener("visibilitychange", visibilityChanged);
    schedule(0);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      stopTimer();
      controller?.abort();
      document.removeEventListener("visibilitychange", visibilityChanged);
    }
  };
}
export function useTaskActivity() {
  return useSyncExternalStore(subscribe, () => snapshot);
}
export function refreshTaskActivity() {
  schedule(0);
}
export function startTaskRequest(task: Row) {
  snapshot = {
    ...snapshot,
    pending: [
      ...snapshot.pending,
      { ...task, status: "submitting", created_at_ms: Date.now() },
    ],
  };
  emit();
  schedule(200);
}
export function finishTaskRequest(requestKey: string) {
  snapshot = {
    ...snapshot,
    pending: snapshot.pending.filter((task) => task.request_key !== requestKey),
  };
  emit();
  schedule(0);
}
export function strategyTaskBusy(
  activity: Snapshot,
  strategyId?: string,
  targetType = "stock",
) {
  return [...activity.tasks, ...activity.pending].some(
    (task) =>
      activeTaskStatuses.has(task.status) &&
      task.kind === "selection" &&
      task.strategy_id === strategyId &&
      (task.target_type || "stock") === targetType,
  );
}
