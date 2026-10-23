import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CheckCircle2,
  CircleAlert,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { api } from "../lib/api";
import type { Row } from "../lib/api";
import { dateTime, duration, statusText } from "../lib/format";
import {
  acknowledgeIncrementSession,
  INCREMENT_SESSION_EVENT,
  INCREMENT_SESSION_KEY,
  readIncrementSession,
} from "./incrementSession";
import type { IncrementSession } from "./incrementSession";
import {
  incrementProgressView,
  incrementReceiptForSession,
} from "./incrementProgress";
import "./IncrementProgressDialog.css";

export default function IncrementProgressDialog({
  onFinished,
}: {
  onFinished: () => void;
}) {
  const [session, setSession] = useState(readIncrementSession);
  const [savedData, setData] = useState<Row | null>(null);
  const [failure, setFailure] = useState<{
    identifier: string;
    error: string;
    missing: boolean;
  } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [, tick] = useState(0);
  const clock = useRef({ received: performance.now(), elapsed: 0 });
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const changed = (event: Event) =>
      setSession((event as CustomEvent<IncrementSession | null>).detail);
    const storage = (event: StorageEvent) => {
      if (event.key === INCREMENT_SESSION_KEY)
        setSession(readIncrementSession());
    };
    window.addEventListener(INCREMENT_SESSION_EVENT, changed);
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener(INCREMENT_SESSION_EVENT, changed);
      window.removeEventListener("storage", storage);
    };
  }, []);
  const identifier = session?.taskId || session?.requestKey;
  const data = incrementReceiptForSession(savedData, session);
  const error =
    failure && failure.identifier === identifier ? failure.error : "";
  const missing =
    !!failure && failure.identifier === identifier && failure.missing;
  useEffect(() => {
    setData(null);
    setFailure(null);
  }, [identifier]);
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    async function poll() {
      try {
        const path = session!.taskId
          ? `task_id=${encodeURIComponent(session!.taskId)}`
          : `request_key=${encodeURIComponent(session!.requestKey)}`;
        const value = await api<Row>(`/sync/session?${path}`, {
          signal: controller.signal,
        });
        if (stopped) return;
        if (!incrementReceiptForSession(value, session))
          throw new Error("进度返回的任务标识不一致，请重新读取");
        setData(value);
        setFailure(null);
        clock.current = {
          received: performance.now(),
          elapsed: value.elapsed_ms || 0,
        };
        if (value.can_acknowledge === true) return;
      } catch (cause: any) {
        if (stopped || cause.name === "AbortError") return;
        setFailure({
          identifier: identifier!,
          missing: cause.status === 404,
          error:
            cause.status === 404
              ? session!.rejection || "正在确认服务端是否已接收本次请求…"
              : `暂时无法读取进度：${cause.message}`,
        });
      }
      if (!stopped) timer = setTimeout(poll, document.hidden ? 10000 : 2000);
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [identifier, refresh, session?.requestKey, session?.rejection]);
  useEffect(() => {
    if (!session) return;
    const interval = setInterval(() => tick((value) => value + 1), 1000);
    const root = document.getElementById("root");
    const previousInert = root?.inert;
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement as HTMLElement | null;
    if (root) root.inert = true;
    document.body.style.overflow = "hidden";
    dialog.current?.focus();
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      if (event.key !== "Tab") return;
      const buttons = [
        ...(dialog.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), [tabindex='0']",
        ) || []),
      ];
      if (!buttons.length) {
        event.preventDefault();
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === buttons[0] ||
          document.activeElement === dialog.current)
      ) {
        event.preventDefault();
        buttons.at(-1)?.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === buttons.at(-1) ||
          document.activeElement === dialog.current)
      ) {
        event.preventDefault();
        buttons[0].focus();
      }
    };
    document.addEventListener("keydown", keys, true);
    return () => {
      clearInterval(interval);
      document.removeEventListener("keydown", keys, true);
      if (root) root.inert = previousInert || false;
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [!!session]);
  if (!session) return null;
  const view = incrementProgressView(data);
  const elapsed = data
    ? clock.current.elapsed +
      (view.done ? 0 : Math.max(0, performance.now() - clock.current.received))
    : Math.max(0, Date.now() - session.createdAt);
  const unconfirmed =
    missing && (!!session.rejection || !!session.taskId || elapsed >= 30000);
  const acknowledge = () => {
    if (acknowledgeIncrementSession(session.requestKey)) onFinished();
  };
  return createPortal(
    <div className="increment-progress-backdrop">
      <div
        className="increment-progress-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="increment-progress-title"
        ref={dialog}
        tabIndex={-1}
      >
        <header>
          {view.done || unconfirmed ? (
            data?.status === "succeeded" || data?.status === "no_change" ? (
              <CheckCircle2 size={30} />
            ) : (
              <CircleAlert size={30} />
            )
          ) : (
            <LoaderCircle size={30} className="spin" />
          )}
          <div>
            <h2 id="increment-progress-title">
              {unconfirmed ? "本次请求尚未确认" : view.title}
            </h2>
            <p>
              {view.done
                ? "确认本次结果后即可继续操作"
                : "更新期间暂时锁定工作台，请等待本次数据链结束"}
            </p>
          </div>
        </header>
        <div className="increment-progress-time">
          <strong>已耗时 {duration(elapsed)}</strong>
          <span>
            开始于 {dateTime(data?.created_at_ms || session.createdAt)}
          </span>
        </div>
        {!view.done && (
          <div className="increment-progress-estimate">
            {view.estimate ? (
              <>
                预计还需 {duration(view.estimate.remaining_min_ms)}～
                {duration(view.estimate.remaining_max_ms)}
                <small>
                  预计结束 {dateTime(view.estimate.expected_finish_at_ms)} ·
                  基于 {view.estimate.sample_count} 次同类完整任务，仅供参考
                </small>
              </>
            ) : (
              data?.estimate?.reason || "正在确认本次任务，暂无法估计剩余时间"
            )}
          </div>
        )}
        {error && (
          <div className="increment-progress-error" role="alert">
            {error}
            <button
              className="btn small"
              onClick={() => setRefresh((value) => value + 1)}
            >
              <RefreshCw size={14} />
              重新读取
            </button>
          </div>
        )}
        <div className="increment-progress-current" aria-live="polite">
          <strong>
            {view.active?.title || data?.message || "正在提交增量请求"}
          </strong>
          <span>{view.active?.message || ""}</span>
        </div>
        {!view.done && (
          <progress
            aria-label="当前阶段进度"
            max={100}
            {...(view.progress === null ? {} : { value: view.progress })}
          />
        )}
        {!!view.tasks.length && (
          <>
            <small>
              {view.completedLabel}；后续阶段尚未排队时，分母会继续增加
            </small>
            <ol className="increment-progress-tasks">
              {view.tasks.map((task) => (
                <li key={task.job_id}>
                  <div>
                    <strong>{task.title}</strong>
                    <span>
                      {statusText[task.status] || task.status}
                      {typeof task.progress === "number"
                        ? ` · ${task.progress}%`
                        : ""}
                    </span>
                  </div>
                  {task.message && <small>{task.message}</small>}
                </li>
              ))}
            </ol>
          </>
        )}
        {!!data?.issues?.length && (
          <div className="increment-progress-error">
            {data.issues.join("；")}
          </div>
        )}
        {view.done && (
          <p className="increment-progress-result">
            {data?.status === "partial"
              ? "已发布的行情可以使用，来源缺口仍保留；详情可在数据中心查看。"
              : ["failed", "blocked", "cancelled", "paused"].includes(
                    data?.status,
                  )
                ? "已发布数据保留。确认后可到数据中心查看原因并手动处理，系统不会自动重启暂停或取消的任务。"
                : "本次主行情及已授权后续任务均已结束。"}
          </p>
        )}
        {unconfirmed && (
          <p className="increment-progress-result">
            未将请求标记为完成，也未重新提交任务。确认后请在数据中心核对任务记录。
          </p>
        )}
        {(view.done || unconfirmed) && (
          <footer>
            <button className="btn primary" onClick={acknowledge}>
              {unconfirmed ? "确认并核对任务记录" : "确认结果，继续使用"}
            </button>
          </footer>
        )}
      </div>
    </div>,
    document.body,
  );
}
