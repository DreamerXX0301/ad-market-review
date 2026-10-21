import { useCallback, useEffect, useRef, useState } from "react";
import { post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  finishTaskRequest,
  startTaskRequest,
  useTaskActivity,
} from "../../components/taskActivity";
import {
  isLineCheck,
  lineCheckBusy,
  lineCheckRefreshKeys,
} from "./lineAlertPresentation";
import {
  refreshWatchlistMembership,
  WATCHLIST_CHANGED,
} from "./watchlistStore";

export function useLineAlerts(asOf?: string) {
  const [active, setActive] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState<Row | null>(null);
  const activity = useTaskActivity();
  const attempt = useRef<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const observedCompletions = useRef(new Set<string>());
  const path = `/watchlist/line-alerts?limit=100${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`;
  const result = useApi<Row>(active ? path : null);
  useEffect(() => {
    const accepted = activity.tasks.find(
      (task) => isLineCheck(task) && task.request_key === attempt.current,
    );
    if (accepted) {
      attempt.current = null;
      setSubmitted(accepted);
      setError("");
    }
  }, [activity.tasks]);
  const current = activity.tasks.find(
    (task) => task.job_id === submitted?.job_id,
  );
  // The compact activity feed retains only five completed tasks. A submitted
  // job can age out while this page is hidden; recover its exact durable status.
  const savedJob = useApi<Row>(
    active && submitted?.job_id && !current
      ? `/tasks/${encodeURIComponent(submitted.job_id)}`
      : null,
  );
  useEffect(() => {
    if (active && submitted?.job_id && !current && savedJob.data)
      savedJob.reload();
  }, [activity.tasks, active, submitted?.job_id, savedJob.reload]);
  useEffect(() => {
    mounted.current = true;
    const visible = () =>
      setActive(
        !document.hidden &&
          location.hash.slice(1).split("?")[0] === "watchlist",
      );
    visible();
    window.addEventListener("hashchange", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false;
      window.removeEventListener("hashchange", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, []);
  useEffect(() => {
    const refresh = () => {
      if (active) result.reload();
    };
    window.addEventListener("chart-annotations-changed", refresh);
    window.addEventListener(WATCHLIST_CHANGED, refresh);
    return () => {
      window.removeEventListener("chart-annotations-changed", refresh);
      window.removeEventListener(WATCHLIST_CHANGED, refresh);
    };
  }, [active, result.reload]);
  useEffect(() => {
    if (!active) return;
    const finished = lineCheckRefreshKeys([
      ...activity.tasks,
      ...(savedJob.data ? [savedJob.data] : []),
    ]);
    const changed = finished.some(
      (key) => !observedCompletions.current.has(key),
    );
    for (const key of finished) observedCompletions.current.add(key);
    if (!changed) return;
    result.reload();
    void refreshWatchlistMembership();
    window.dispatchEvent(new Event(WATCHLIST_CHANGED));
  }, [activity.tasks, savedJob.data, active, result.reload]);
  const activeTask = [...activity.pending, ...activity.tasks].find(
    (task) => isLineCheck(task) && lineCheckBusy(task),
  );
  const task = activeTask || current || savedJob.data || submitted;
  const busy = pending || lineCheckBusy(task);
  const evaluate = useCallback(async () => {
    if (inFlight.current || busy) return;
    inFlight.current = true;
    setPending(true);
    setError("");
    const key = attempt.current || requestKey();
    attempt.current = key;
    startTaskRequest({
      request_key: key,
      kind: "line_alert_check",
      title: "检查划线提醒",
    });
    try {
      const job = await post<Row>("/watchlist/line-alerts/evaluate", {
        request_key: key,
      });
      attempt.current = null;
      if (mounted.current) setSubmitted({ ...job, kind: "line_alert_check" });
    } catch (problem: any) {
      if (mounted.current && attempt.current === key) setError(problem.message);
      // Preserve this key if the response was uncertain; retry cannot duplicate a job.
    } finally {
      inFlight.current = false;
      finishTaskRequest(key);
      if (mounted.current) setPending(false);
    }
  }, [busy]);
  return { result, evaluate, busy, pending, task, error };
}
