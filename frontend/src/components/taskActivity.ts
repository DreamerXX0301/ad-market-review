import { useSyncExternalStore } from "react";
import { api, notifyReadChanges, observeReadScopes } from "../lib/api";
import type { ReadChange, Row } from "../lib/api";
import {
  LINE_ALERT_CHECK_FINISHED,
  MARKET_INCREMENT_FINISHED,
} from "../lib/workbenchEvents";
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
let reviewDate = "";
const revisions = new Map<string, string | null>();
const dateRevisions = new Map<string, Record<string, string | null>>();

function updateReadRevisions(data: Row) {
  const current = data.read_revisions;
  if (!current || typeof current !== "object" || current.market_locked) return;
  const changes: ReadChange[] = [];
  for (const [scope, value] of Object.entries({
    ...(current.domains || {}),
    market: current.market,
    calendar: current.today,
  })) {
    if (value !== null && typeof value !== "string") continue;
    if (revisions.has(scope) && revisions.get(scope) !== value)
      changes.push({ scope });
    revisions.set(scope, value);
  }
  for (const [date, raw] of Object.entries(current.dates || {})) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !raw || typeof raw !== "object")
      continue;
    const previous = dateRevisions.get(date);
    const next: Record<string, string | null> = {};
    for (const [scope, value] of Object.entries(raw)) {
      if (value !== null && typeof value !== "string") continue;
      if (
        previous &&
        Object.hasOwn(previous, scope) &&
        previous[scope] !== value
      )
        changes.push({ scope, date });
      next[scope] = value;
    }
    dateRevisions.delete(date);
    dateRevisions.set(date, next);
  }
  while (dateRevisions.size > 32)
    dateRevisions.delete(dateRevisions.keys().next().value!);
  observeReadScopes([...revisions.keys()]);
  notifyReadChanges(changes);
}

const notifiedLineChecks = new Set<string>();
const notifiedIncrements = new Set<string>();
let hasIncrementBaseline = false;
let previousIncrementKeys = new Set<string>();
const successfulIncrement = (task: Row, observedAtMs: number) =>
  task.kind === "increment" &&
  ["succeeded", "partial", "no_change"].includes(task.status) &&
  typeof task.job_id === "string" &&
  task.job_id.length > 0 &&
  task.job_id.length <= 200 &&
  task.job_id.trim() === task.job_id &&
  typeof task.finished_at_ms === "number" &&
  Number.isFinite(task.finished_at_ms) &&
  task.finished_at_ms > 0 &&
  task.finished_at_ms <= observedAtMs;
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
    const path =
      "/tasks/activity" +
      (reviewDate ? "?date=" + encodeURIComponent(reviewDate) : "");
    const data = await api<Row>(path, { signal: request.signal });
    if (request.signal.aborted) return;
    const items: Row[] = Array.isArray(data.items) ? data.items : [];
    const observedAtMs =
      typeof data.observed_at_ms === "number" &&
      Number.isFinite(data.observed_at_ms)
        ? data.observed_at_ms
        : Date.now();
    const finishedSelection =
      !data.read_revisions?.domains &&
      items.some((task) => {
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
    const incrementCompletions: Row[] = [];
    // An invalid/missing server clock must not borrow the browser's clock to
    // assert completion. The first accepted poll only establishes a baseline.
    if (
      Array.isArray(data.items) &&
      typeof data.observed_at_ms === "number" &&
      Number.isFinite(data.observed_at_ms) &&
      data.observed_at_ms > 0
    ) {
      const currentKeys = new Set<string>();
      for (const task of items) {
        if (!successfulIncrement(task, data.observed_at_ms)) continue;
        const key = `${task.job_id}:${task.finished_at_ms}`;
        currentKeys.add(key);
        if (
          hasIncrementBaseline &&
          !notifiedIncrements.has(key) &&
          !previousIncrementKeys.has(key)
        )
          incrementCompletions.push({
            job_id: task.job_id,
            target_date:
              typeof task.target_date === "string" ? task.target_date : null,
            finished_at_ms: task.finished_at_ms,
            status: task.status,
          });
        notifiedIncrements.add(key);
      }
      hasIncrementBaseline = true;
      previousIncrementKeys = currentKeys;
      while (notifiedIncrements.size > 256)
        notifiedIncrements.delete(notifiedIncrements.values().next().value!);
    }
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
    updateReadRevisions(data);
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
    if (incrementCompletions.length)
      window.dispatchEvent(
        new CustomEvent(MARKET_INCREMENT_FINISHED, {
          detail: { tasks: incrementCompletions },
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
export function setTaskActivityReviewDate(date: string) {
  if (date === reviewDate) return;
  reviewDate = date;
  schedule(0);
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
