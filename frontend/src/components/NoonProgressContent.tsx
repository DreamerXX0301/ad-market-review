import CollectionEstimate from "./CollectionEstimate";
import { useEffect, useRef, useState } from "react";
import { LoaderCircle, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import type { Row } from "../lib/api";
import { dateTime, duration } from "../lib/format";
import { refreshTaskActivity, useTaskActivity } from "./taskActivity";
import {
  NOON_RUN_EVENT,
  NOON_RUN_KEY,
  isTerminalNoonTask,
  noonSessionFromTask,
  readNoonRun,
  reconcileNoonRun,
  releaseNoonRun,
  restoreNoonRun,
} from "./noonRunLock";
import type { NoonRunSession } from "./noonRunLock";

/** Runs outside page-activity gating; it only reads the existing local task. */
export function useNoonRunProgress(onFinished?: (task: Row) => void) {
  const activity = useTaskActivity();
  const [session, setSession] = useState(readNoonRun);
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const previous = useRef<NoonRunSession | null>(session);
  const currentSession = useRef(session);
  currentSession.current = session;
  const finishedTasks = useRef(new Set<string>());
  const finishedRequests = useRef(new Set<string>());
  const latestData = useRef<Row | null>(null);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  useEffect(() => {
    const changed = (event: Event) => {
      const value = (event as CustomEvent<NoonRunSession | null>).detail;
      if (value) {
        finishedRequests.current.delete(value.requestKey);
        if (value.taskId) finishedTasks.current.delete(value.taskId);
      } else if (currentSession.current) {
        finishedRequests.current.add(currentSession.current.requestKey);
        if (currentSession.current.taskId)
          finishedTasks.current.add(currentSession.current.taskId);
        while (finishedRequests.current.size > 64)
          finishedRequests.current.delete(
            finishedRequests.current.values().next().value!,
          );
        while (finishedTasks.current.size > 64)
          finishedTasks.current.delete(
            finishedTasks.current.values().next().value!,
          );
      }
      setSession(value);
    };
    const storage = (event: StorageEvent) => {
      if (event.key === NOON_RUN_KEY || event.key === null)
        setSession(readNoonRun());
    };
    window.addEventListener(NOON_RUN_EVENT, changed);
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener(NOON_RUN_EVENT, changed);
      window.removeEventListener("storage", storage);
    };
  }, []);
  const discoverable = activity.tasks.find(
    (task) =>
      noonSessionFromTask(task) &&
      !finishedTasks.current.has(task.task_id || task.job_id) &&
      !finishedRequests.current.has(task.request_key),
  );
  useEffect(() => {
    if (
      discoverable &&
      (!session || discoverable.request_key === session.requestKey)
    )
      restoreNoonRun(discoverable);
  }, [activity.tasks, session?.requestKey, session?.taskId]);
  useEffect(() => {
    if (session) previous.current = session;
    else if (previous.current) {
      const last = previous.current;
      previous.current = null;
      const value = latestData.current;
      finished.current?.(
        value && (value.task_id || value.job_id) === last.taskId
          ? value
          : { target_date: last.date },
      );
    }
  }, [session]);
  useEffect(() => {
    if (!session) {
      setData(null);
      setError("");
      return;
    }
    const controller = new AbortController();
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        if (session!.taskId) {
          const value = await api<Row>(
            `/tasks/${encodeURIComponent(session!.taskId)}`,
            { signal: controller.signal },
          );
          if (stopped) return;
          if (
            (value.task_id || value.job_id) !== session!.taskId ||
            value.kind !== "source_check" ||
            value.parameters?.operation !== "noon_snapshot" ||
            value.target_date !== session!.date
          )
            throw new Error("返回的任务身份与本次午评不一致，请重新读取");
          setData(value);
          latestData.current = value;
          setError("");
          if (isTerminalNoonTask(value)) {
            releaseNoonRun(session!.requestKey);
            return;
          }
          if (!["queued", "running"].includes(value.status))
            throw new Error("午评任务状态尚未通过核验");
        } else {
          // Resolve a lost POST response without resubmitting or treating a
          // network failure as completion. This path stops after identity binds.
          const value = await api<Row>(
            `/market/noon?date=${encodeURIComponent(session!.date)}`,
            { signal: controller.signal },
          );
          if (stopped) return;
          if (value.date !== session!.date)
            throw new Error("午评日期不一致，请重新读取");
          if (value.task) latestData.current = value.task;
          if (reconcileNoonRun(session!.requestKey, value)) return;
          setError("正在确认服务端是否已接收午评请求，不会重复提交。");
        }
      } catch (cause: any) {
        if (stopped || cause.name === "AbortError") return;
        setError(
          `暂时无法读取午评进度：${cause.message}。保留锁定，连接恢复后继续确认。`,
        );
      }
      if (!stopped) timer = setTimeout(poll, document.hidden ? 10000 : 1800);
    }
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [session?.requestKey, session?.taskId, session?.date, refresh]);
  const currentData =
    session?.taskId && (data?.task_id || data?.job_id) === session.taskId
      ? data
      : null;
  return {
    session,
    checking: !activity.clock || (!session && !!discoverable),
    data: currentData,
    error: error || activity.error,
    retry: () => {
      setRefresh((value) => value + 1);
      refreshTaskActivity();
    },
  };
}

export default function NoonProgressContent({
  value,
}: {
  value: ReturnType<typeof useNoonRunProgress>;
}) {
  const { session, data, error, retry } = value;
  const progress =
    typeof data?.progress === "number" &&
    Number.isFinite(data.progress) &&
    data.progress >= 0 &&
    data.progress <= 100
      ? data.progress
      : null;
  const elapsed = Math.max(
    0,
    Date.now() - (data?.created_at_ms || session?.createdAt || Date.now()),
  );
  return (
    <>
      <header>
        <LoaderCircle size={30} className="spin" />
        <div>
          <h2 id="increment-progress-title">
            {session ? "正在采集午评快照" : "正在确认午评任务状态"}
          </h2>
          <p>
            {session
              ? "采集期间暂时锁定工作台，任务结束后自动恢复操作。"
              : "正在读取本地活动任务，确认没有午评采集后恢复操作。"}
          </p>
        </div>
      </header>
      {session && (
        <div className="increment-progress-time">
          <strong>已耗时 {duration(elapsed)}</strong>
          <span>
            {session?.date} · 开始于{" "}
            {dateTime(data?.created_at_ms || session?.createdAt)}
          </span>
        </div>
      )}
      <CollectionEstimate estimate={data?.estimate} />
      <div className="increment-progress-current" aria-live="polite">
        <div className="increment-progress-stage-heading">
          <strong>
            {data?.status === "queued"
              ? "等待唯一采集任务执行"
              : data?.message || "正在确认午评采集任务"}
          </strong>
          {progress != null && <b>{progress}%</b>}
        </div>
        <span>{data?.stage || ""}</span>
      </div>
      <progress
        aria-label="午评采集进度"
        max={100}
        {...(progress == null ? {} : { value: progress })}
      />
      {error && (
        <div className="increment-progress-error" role="alert">
          {error}
          <button type="button" className="btn small" onClick={retry}>
            <RefreshCw size={14} />
            重新读取
          </button>
        </div>
      )}
      <p className="increment-progress-result">
        本次采集不提供暂停或取消，避免误触中断。关闭浏览器不会停止后台任务；暂时失联时保留锁定。
      </p>
    </>
  );
}
