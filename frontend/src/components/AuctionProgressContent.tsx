import { useEffect, useRef, useState } from "react";
import { LoaderCircle, RefreshCw } from "lucide-react";
import { api, post } from "../lib/api";
import type { Row } from "../lib/api";
import { dateTime, duration } from "../lib/format";
import { refreshTaskActivity, useTaskActivity } from "./taskActivity";
import {
  AUCTION_RUN_EVENT,
  AUCTION_RUN_KEY,
  isTerminalAuctionTask,
  auctionSessionFromTask,
  readAuctionRun,
  reconcileAuctionRun,
  releaseAuctionRun,
  restoreAuctionRun,
} from "./auctionRunLock";
import type { AuctionRunSession } from "./auctionRunLock";

/** Runs outside page-activity gating; it only reads the existing local task. */
export function useAuctionRunProgress(onFinished?: (task: Row) => void) {
  const activity = useTaskActivity();
  const [session, setSession] = useState(readAuctionRun);
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const previous = useRef<AuctionRunSession | null>(session);
  const currentSession = useRef(session);
  currentSession.current = session;
  const finishedTasks = useRef(new Map<string, unknown>());
  const latestActivity = useRef(activity);
  latestActivity.current = activity;
  const revision = useRef(0);
  const [automation, setAutomation] = useState<Row | null>(null);
  const finishedRequests = useRef(new Set<string>());
  const latestData = useRef<Row | null>(null);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  useEffect(() => {
    const changed = (event: Event) => {
      revision.current += 1;
      const value = (event as CustomEvent<AuctionRunSession | null>).detail;
      if (value) {
        finishedRequests.current.delete(value.requestKey);
        if (value.taskId) finishedTasks.current.delete(value.taskId);
      } else if (currentSession.current) {
        finishedRequests.current.add(currentSession.current.requestKey);
        if (currentSession.current.taskId)
          finishedTasks.current.set(
            currentSession.current.taskId,
            latestActivity.current.clock,
          );
        while (finishedRequests.current.size > 64)
          finishedRequests.current.delete(
            finishedRequests.current.values().next().value!,
          );
        while (finishedTasks.current.size > 64)
          finishedTasks.current.delete(
            finishedTasks.current.keys().next().value!,
          );
      }
      setSession(value);
    };
    const storage = (event: StorageEvent) => {
      if (event.key === AUCTION_RUN_KEY || event.key === null) {
        revision.current += 1;
        setSession(readAuctionRun());
      }
    };
    window.addEventListener(AUCTION_RUN_EVENT, changed);
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener(AUCTION_RUN_EVENT, changed);
      window.removeEventListener("storage", storage);
    };
  }, []);
  const discoverable = activity.tasks.find(
    (task) =>
      auctionSessionFromTask(task) &&
      (!finishedTasks.current.has(task.task_id || task.job_id) ||
        finishedTasks.current.get(task.task_id || task.job_id) !==
          activity.clock) &&
      (!finishedRequests.current.has(task.request_key) ||
        !finishedTasks.current.has(task.task_id || task.job_id) ||
        finishedTasks.current.get(task.task_id || task.job_id) !==
          activity.clock),
  );
  useEffect(() => {
    if (
      discoverable &&
      (!session || discoverable.request_key === session.requestKey)
    )
      restoreAuctionRun(discoverable);
  }, [activity.tasks, session?.requestKey, session?.taskId]);
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function readAutomatic() {
      const startedRevision = revision.current;
      try {
        const value = await api<Row>("/market/auction/automation", {
          signal: controller.signal,
        });
        if (stopped) return;
        if (startedRevision !== revision.current)
          throw Error("已发生更新，忽略旧自动状态");
        if (value.version !== "auction-automation-v1")
          throw Error("自动竞价设置版本不符");
        setAutomation(value);
        const run = value.last_run;
        if (
          !currentSession.current &&
          run?.date === value.today &&
          ["queued", "running", "waiting_retry"].includes(run.state) &&
          typeof run.job_id === "string"
        ) {
          const task = await api<Row>(
            `/tasks/${encodeURIComponent(run.job_id)}`,
            { signal: controller.signal },
          );
          if (stopped) return;
          if (startedRevision !== revision.current || currentSession.current)
            throw Error("已有新会话，忽略旧自动发现");
          if (
            (task.task_id || task.job_id) !== run.job_id ||
            task.target_date !== run.date ||
            task.kind !== "source_check" ||
            (task.operation || task.parameters?.operation) !==
              "auction_snapshot"
          )
            throw Error("自动竞价任务身份未通过核验");
          restoreAuctionRun(task, run.state === "waiting_retry");
        }
      } catch (cause: any) {
        // A settings read alone never locks the workbench or invents an active task.
        if (stopped || cause.name === "AbortError") return;
      }
      if (!stopped)
        timer = setTimeout(readAutomatic, document.hidden ? 15000 : 5000);
    }
    void readAutomatic();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh]);
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
            value.parameters?.operation !== "auction_snapshot" ||
            value.target_date !== session!.date
          )
            throw new Error("返回的任务身份与本次竞价不一致，请重新读取");
          setData(value);
          latestData.current = value;
          setError("");
          if (isTerminalAuctionTask(value)) {
            if (
              value.status === "failed" &&
              (value.parameters?.automatic === true || value.automatic === true)
            ) {
              const settings = await api<Row>("/market/auction/automation", {
                signal: controller.signal,
              });
              if (stopped) return;
              if (settings.version !== "auction-automation-v1")
                throw Error("自动竞价状态尚未核验");
              setAutomation(settings);
              const run = settings.last_run;
              if (
                settings.enabled === true &&
                run?.automatic_control === true &&
                run?.date === session!.date &&
                run.job_id === session!.taskId &&
                ["queued", "running", "waiting_retry"].includes(run.state)
              ) {
                setData({
                  ...value,
                  status: "waiting_retry",
                  message: run.reason || "来源尚未就绪，等待窗口内有限重试。",
                  automatic_run: run,
                });
              } else {
                releaseAuctionRun(session!.requestKey);
                return;
              }
            } else {
              releaseAuctionRun(session!.requestKey);
              return;
            }
          }
          if (!["queued", "running", "failed"].includes(value.status))
            throw new Error("竞价任务状态尚未通过核验");
        } else {
          // Resolve a lost POST response without resubmitting or treating a
          // network failure as completion. This path stops after identity binds.
          const value = await api<Row>(
            `/market/auction?date=${encodeURIComponent(session!.date)}`,
            { signal: controller.signal },
          );
          if (stopped) return;
          if (value.date !== session!.date)
            throw new Error("竞价日期不一致，请重新读取");
          if (value.task) latestData.current = value.task;
          if (reconcileAuctionRun(session!.requestKey, value)) return;
          setError("正在确认服务端是否已接收竞价请求，不会重复提交。");
        }
      } catch (cause: any) {
        if (stopped || cause.name === "AbortError") return;
        setError(
          `暂时无法读取竞价进度：${cause.message}。保留锁定，连接恢复后继续确认。`,
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
    checking: false,
    automation,
    data: currentData,
    error: error || activity.error,
    retry: () => {
      setRefresh((value) => value + 1);
      refreshTaskActivity();
    },
  };
}

export default function AuctionProgressContent({
  value,
}: {
  value: ReturnType<typeof useAuctionRunProgress>;
}) {
  const { session, data, error, retry } = value;
  const [busy, setBusy] = useState("");
  const [controlError, setControlError] = useState("");
  const inFlight = useRef(false);
  const progress =
    typeof data?.progress === "number" &&
    Number.isFinite(data.progress) &&
    data.progress >= 0 &&
    data.progress <= 100
      ? data.progress
      : null;
  const waiting = data?.status === "waiting_retry";
  const run = data?.automatic_run;
  const taskId = session?.taskId;
  const retrySeconds =
    typeof run?.retry_after_at_ms === "number" &&
    Number.isFinite(run.retry_after_at_ms)
      ? Math.max(0, Math.ceil((run.retry_after_at_ms - Date.now()) / 1000))
      : null;
  async function control(action: "pause" | "cancel") {
    if (!taskId || inFlight.current) return;
    inFlight.current = true;
    setBusy(action);
    setControlError("");
    try {
      await post(`/tasks/${encodeURIComponent(taskId)}/${action}`);
      retry();
    } catch (cause: any) {
      setControlError(cause.message || "操作未完成，仍保留任务进度。");
    } finally {
      inFlight.current = false;
      setBusy("");
    }
  }
  return (
    <>
      <header>
        <LoaderCircle size={30} className="spin" />
        <div>
          <h2 id="increment-progress-title">
            {waiting ? "来源未就绪，等待自动重试" : "正在采集盘前竞价"}
          </h2>
          <p>采集期间暂时锁定工作台，结果就绪后自动恢复并打开竞价结果。</p>
        </div>
      </header>
      {session && (
        <div className="increment-progress-time">
          <strong>
            已耗时{" "}
            {duration(
              Math.max(
                0,
                Date.now() - (data?.created_at_ms || session.createdAt),
              ),
            )}
          </strong>
          <span>
            {session.date} · 开始于{" "}
            {dateTime(data?.created_at_ms || session.createdAt)}
          </span>
        </div>
      )}
      <div className="increment-progress-current" aria-live="polite">
        <div className="increment-progress-stage-heading">
          <strong>
            {data?.status === "queued"
              ? "等待唯一工作进程执行"
              : data?.message || "正在确认本日原竞价任务"}
          </strong>
          {!waiting && progress != null && <b>{progress}%</b>}
        </div>
        <span>
          {waiting
            ? `本日原任务，已尝试 ${run?.attempts ?? "未知"} / ${run?.max_attempts ?? "未知"} 次${retrySeconds != null ? (retrySeconds > 0 ? `；约${retrySeconds}秒后自动重试` : "；等待工作进程检查下一次尝试") : ""}`
            : data?.stage || ""}
        </span>
      </div>
      <progress
        aria-label="竞价采集进度"
        max={100}
        {...(waiting || progress == null ? {} : { value: progress })}
      />
      {(error || controlError) && (
        <div className="increment-progress-error" role="alert">
          {controlError || error}
          <button className="btn small" onClick={retry}>
            <RefreshCw size={14} />
            重新读取
          </button>
        </div>
      )}
      <p className="increment-progress-result">
        沿用已提交的同日原任务，不重复采集；暂停或取消后不自动继续。来源未就绪的旧数值不会当作结果。
      </p>
      {taskId &&
        ["queued", "running", "waiting_retry"].includes(data?.status) && (
          <footer className="increment-progress-controls">
            <span>等待有效结果；可以显式停止本次采集。</span>
            <button
              className="btn"
              disabled={!!busy}
              onClick={() => control("pause")}
            >
              {busy === "pause" ? "正在暂停…" : "暂停本次采集"}
            </button>
            <button
              className="btn"
              disabled={!!busy}
              onClick={() => control("cancel")}
            >
              {busy === "cancel" ? "正在取消…" : "取消本次采集"}
            </button>
          </footer>
        )}
    </>
  );
}
