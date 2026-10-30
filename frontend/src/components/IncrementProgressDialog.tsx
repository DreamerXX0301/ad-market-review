import WorkspaceTaskSummary from "./WorkspaceTaskSummary";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CheckCircle2,
  CircleAlert,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { api, post } from "../lib/api";
import type { Row } from "../lib/api";
import { dateTime, duration, statusText } from "../lib/format";
import {
  acknowledgeIncrementSession,
  INCREMENT_SESSION_EVENT,
  INCREMENT_SESSION_KEY,
  readIncrementSession,
  restoreIncrementSession,
} from "./incrementSession";
import type { IncrementSession } from "./incrementSession";
import {
  incrementProgressView,
  incrementReceiptForSession,
} from "./incrementProgress";
import "./IncrementProgressDialog.css";
import NoonProgressContent, { useNoonRunProgress } from "./NoonProgressContent";
import AuctionProgressContent, {
  useAuctionRunProgress,
} from "./AuctionProgressContent";

export default function IncrementProgressDialog({
  onFinished,
  onBlockedChange,
  onNoonFinished,
  onAuctionFinished,
}: {
  onFinished: () => void;
  onBlockedChange?: (blocked: boolean) => void;
  onNoonFinished?: (task: Row) => void;
  onAuctionFinished?: (task: Row) => void;
}) {
  const [session, setSession] = useState(readIncrementSession);
  const [savedData, setData] = useState<Row | null>(null);
  const [failure, setFailure] = useState<{
    identifier: string;
    error: string;
    missing: boolean;
  } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [checking, setChecking] = useState(true);
  const [discoveryError, setDiscoveryError] = useState("");
  const [actionBusy, setActionBusy] = useState("");
  const [, tick] = useState(0);
  const clock = useRef({ received: performance.now(), elapsed: 0 });
  const dialog = useRef<HTMLDivElement>(null);
  // Ignore responses that started before a local accept/ack/storage change.
  // Otherwise a slow discovery GET can restore an already dismissed dialog.
  const sessionRevision = useRef(0);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  const previousSession = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (session) previousSession.current = session.requestKey;
    else if (!checking && previousSession.current) {
      previousSession.current = undefined;
      finished.current();
    }
  }, [session?.requestKey, checking]);
  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function discover() {
      const revision = sessionRevision.current;
      try {
        const value = await api<Row>("/sync/session", {
          signal: controller.signal,
        });
        if (stopped) return;
        if (revision === sessionRevision.current) {
          if (value.active) restoreIncrementSession(value.active);
          setDiscoveryError("");
          setChecking(false);
        }
      } catch (cause: any) {
        if (stopped || cause.name === "AbortError") return;
        if (revision === sessionRevision.current)
          setDiscoveryError(`暂时无法确认工作台状态：${cause.message}`);
      }
      if (!stopped)
        timer = setTimeout(discover, document.hidden ? 10000 : 3000);
    }
    void discover();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, []);
  useEffect(() => {
    const changed = (event: Event) => {
      sessionRevision.current += 1;
      setSession((event as CustomEvent<IncrementSession | null>).detail);
    };
    const storage = (event: StorageEvent) => {
      if (event.key === INCREMENT_SESSION_KEY || event.key === null) {
        sessionRevision.current += 1;
        if (!readIncrementSession()) setChecking(true);
        setSession(readIncrementSession());
      }
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
  }, [session?.requestKey]);
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    async function poll() {
      const revision = sessionRevision.current;
      try {
        const path = session!.taskId
          ? `task_id=${encodeURIComponent(session!.taskId)}`
          : `request_key=${encodeURIComponent(session!.requestKey)}`;
        const value = await api<Row>(`/sync/session?${path}`, {
          signal: controller.signal,
        });
        if (stopped || revision !== sessionRevision.current) return;
        if (!incrementReceiptForSession(value, session))
          throw new Error("进度返回的任务标识不一致，请重新读取");
        setChecking(false);
        setData(value);
        if (value.acknowledged === true && value.locked === false) {
          acknowledgeIncrementSession(session!.requestKey);
          return;
        }
        if (value.locked === true) restoreIncrementSession(value);
        setFailure(null);
        clock.current = {
          received: performance.now(),
          elapsed: value.elapsed_ms || 0,
        };
      } catch (cause: any) {
        if (
          stopped ||
          cause.name === "AbortError" ||
          revision !== sessionRevision.current
        )
          return;
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
  }, [
    identifier,
    refresh,
    session?.requestKey,
    session?.epoch,
    session?.createdAt,
    session?.rejection,
  ]);
  // One portal owns the workbench lock. Increment discovery/session wins the
  // content branch; switching to noon must not restore inert or keyboard state.
  const noon = useNoonRunProgress(onNoonFinished);
  const auction = useAuctionRunProgress(onAuctionFinished);
  const blocked =
    !!session ||
    checking ||
    !!noon.session ||
    noon.checking ||
    !!auction.session;
  useEffect(() => {
    onBlockedChange?.(blocked);
  }, [blocked, onBlockedChange]);
  useEffect(() => {
    if (!blocked) return;
    const interval = setInterval(() => tick((value) => value + 1), 1000);
    const root = document.getElementById("root");
    const previousInert = root?.inert;
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement as HTMLElement | null;
    if (root) root.inert = true;
    // Business dialogs also use document.body portals. Guard those siblings,
    // including portals mounted after this overlay, rather than only #root.
    const previous = new Map<HTMLElement, boolean>();
    const guardPortals = () => {
      for (const element of document.body.children) {
        if (
          !(element instanceof HTMLElement) ||
          element.contains(dialog.current)
        )
          continue;
        if (!previous.has(element)) previous.set(element, element.inert);
        element.inert = true;
      }
    };
    guardPortals();
    const observer = new MutationObserver(guardPortals);
    observer.observe(document.body, { childList: true });
    document.body.style.overflow = "hidden";
    dialog.current?.focus();
    const keys = (event: KeyboardEvent) => {
      if (!dialog.current?.contains(event.target as Node)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        dialog.current?.focus();
        return;
      }
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
      observer.disconnect();
      for (const [element, inert] of previous)
        if (element.isConnected) element.inert = inert;
      if (root) root.inert = previousInert || false;
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [blocked]);
  useEffect(() => {
    if (!blocked || !dialog.current) return;
    const active = document.activeElement;
    if (
      !dialog.current.contains(active) ||
      (active instanceof HTMLButtonElement && active.disabled)
    )
      dialog.current.focus();
  }, [
    blocked,
    data?.status,
    actionBusy,
    noon.data?.status,
    auction.data?.status,
  ]);
  if (
    !session &&
    !checking &&
    !noon.session &&
    !noon.checking &&
    !auction.session
  )
    return null;
  const view = incrementProgressView(data);
  const elapsed = data
    ? clock.current.elapsed +
      (view.done ? 0 : Math.max(0, performance.now() - clock.current.received))
    : session
      ? Math.max(0, Date.now() - session.createdAt)
      : 0;
  const unconfirmed = !!session && missing;
  const acknowledge = async () => {
    if (!session || actionBusy) return;
    setActionBusy("acknowledge");
    try {
      const result = await post<Row>("/sync/session/acknowledge", {
        request_key: session.requestKey,
        task_id: session.taskId,
        session_epoch: session.epoch,
      });
      if (result.acknowledged === true)
        acknowledgeIncrementSession(session.requestKey);
    } catch (cause: any) {
      setFailure({ identifier: identifier!, missing, error: cause.message });
      setRefresh((value) => value + 1);
    } finally {
      setActionBusy("");
    }
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
        {!session && !checking && (noon.session || noon.checking) ? (
          <NoonProgressContent value={noon} />
        ) : !session && !checking && auction.session ? (
          <AuctionProgressContent value={auction} />
        ) : (
          <>
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
                  {!session
                    ? "正在确认工作台状态"
                    : unconfirmed
                      ? "本次请求尚未确认"
                      : data?.status === "stopping"
                        ? "正在安全停止本次更新"
                        : view.title}
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
                {session
                  ? `开始于 ${dateTime(data?.created_at_ms || session.createdAt)}`
                  : "正在读取服务端增量会话"}
              </span>
            </div>
            {!view.done && (
              <div className="increment-progress-estimate">
                {error || discoveryError ? (
                  "进度连接暂时中断，任务可能仍在后台执行；连接恢复后更新预计时间"
                ) : view.estimate ? (
                  <>
                    预计还需 {duration(view.estimate.remaining_min_ms)}～
                    {duration(view.estimate.remaining_max_ms)}
                    <small>
                      预计结束 {dateTime(view.estimate.expected_finish_at_ms)} ·
                      基于 {view.estimate.sample_count} 次同类完整任务，仅供参考
                    </small>
                  </>
                ) : (
                  data?.estimate?.reason ||
                  "正在确认本次任务，暂无法估计剩余时间"
                )}
              </div>
            )}
            {(error || discoveryError) && (
              <div className="increment-progress-error" role="alert">
                {error || discoveryError}
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
              <div className="increment-progress-stage-heading">
                <strong>
                  {(view.active
                    ? `${view.active.title}${view.active.strategy_name ? " · " + view.active.strategy_name : ""}`
                    : "") ||
                    data?.message ||
                    "正在确认本次增量请求"}
                </strong>
                {!view.done && view.progress !== null && (
                  <b>{view.progress}%</b>
                )}
              </div>
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
                <p className="increment-progress-plan">
                  {data?.attempt_task_id
                    ? "本次仅处理所选重试步骤及其已授权后续分支；原链其它未完成阶段仍保留。"
                    : "采集与核验 → 策略筛选与跟踪 → 划线提醒；以下按本次任务实际记录展示，旧冻结任务保持原执行顺序"}
                </p>
                <small>
                  {view.completedLabel}
                  ；百分比只表示当前实际阶段，后续阶段尚未排队时继续等待
                </small>
                <details className="increment-progress-detail">
                  <summary>查看阶段明细</summary>
                  <ol className="increment-progress-tasks">
                    {view.tasks.map((task) => (
                      <li key={task.job_id}>
                        <div>
                          <strong>
                            {task.title}
                            {task.strategy_name
                              ? ` · ${task.strategy_name}`
                              : ""}
                          </strong>
                          <span>
                            {task.historical_attempt ? "历史尝试 · " : ""}
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
                </details>
              </>
            )}
            <WorkspaceTaskSummary workspace={data?.workspace} />
            {!!data?.issues?.length && (
              <div className="increment-progress-error">
                {data.issues.join("；")}
              </div>
            )}
            {view.done && (
              <p className="increment-progress-result">
                {data?.attempt_task_id
                  ? "本次仅反映重试步骤及其已授权分支的结果；原增量的历史失败与其它未完成阶段仍保留。"
                  : data?.status === "partial"
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
                没有把失联或404当作完成，也不会重复提交。可以请服务端核查并关闭未接收的请求；若原请求迟到，将被服务端拒绝，不会另建任务。已有任务仍在执行时保持锁定。
              </p>
            )}
            {!view.done && (
              <p className="increment-progress-result">
                本次采集及后续计算不提供暂停或取消，避免误触中断。关闭浏览器不会停止后台任务。
              </p>
            )}
            {(view.done || unconfirmed) && (
              <footer>
                <button
                  className="btn primary"
                  disabled={!!actionBusy}
                  onClick={acknowledge}
                >
                  {actionBusy === "acknowledge"
                    ? "服务端确认中…"
                    : unconfirmed
                      ? "核查并关闭未接收请求"
                      : "确认结果，继续使用"}
                </button>
              </footer>
            )}
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
