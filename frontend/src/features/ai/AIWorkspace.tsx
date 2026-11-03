import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  History,
  Pin,
  ArrowUp,
  ArrowDown,
  ChevronDown,
  PenSquare,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import { requestKey, useApi, usePageActive } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { ErrorBox, Loading, Modal } from "../../components/UI";
import { AIMessageView } from "./AIContent";
import { aiWrite, useAITask } from "./aiApi";
import { useAIStream } from "./useAIStream";
import { publishAIChange, subscribeAIChanges } from "./aiTabSync";
import { useAIChatFollow } from "./useAIChatFollow";
import { chatLocation, readChatLocation } from "./aiChatLocation";
import {
  aiReason,
  canGenerate,
  chatSessionTitle,
  sessionMatches,
  terminalTask,
} from "./aiState";
import type { AIKind, AIMessage, AIProvider, AISession } from "./aiTypes";
import "./AI.css";

export interface AIWorkspaceProps {
  kind: AIKind;
  reportId?: string;
  instrumentId?: string;
  asOf?: string;
  onConfigure?: () => void;
  onBusyChange?: (busy: boolean) => void;
  allHistory?: boolean;
  pageRoute?: "ai";
  pageLaunchId?: string;
}

export default function AIWorkspace({
  kind,
  reportId,
  instrumentId,
  asOf,
  onConfigure,
  onBusyChange,
  allHistory = false,
  pageRoute,
  pageLaunchId,
}: AIWorkspaceProps) {
  const active = usePageActive();
  const providers = useApi<{ items: AIProvider[] }>("/ai/providers");
  const [providerId, setProviderId] = useState("");
  const [sessionId, setSessionId] = useState(() =>
    typeof window !== "undefined"
      ? readChatLocation(window.location.hash, {
          kind,
          pageRoute,
          pageLaunchId,
          instrumentId,
          reportId,
          asOf,
        })
      : "",
  );
  const [taskId, setTaskId] = useState("");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState("");
  const [busyDraft, setBusyDraft] = useState(false);
  const [cursor, setCursor] = useState("");
  const [historyRows, setHistoryRows] = useState<AISession[]>([]);
  const [historyKind, setHistoryKind] = useState<AIKind | "">("");
  const [deleting, setDeleting] = useState<AISession | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [optimistic, setOptimistic] = useState<AIMessage | null>(null);
  const [scrollRequest, setScrollRequest] = useState(0);
  const detail = useApi<{
    session: AISession;
    messages: AIMessage[];
    active_task_id: string | null;
    config_matches: boolean;
  }>(sessionId ? `/ai/sessions/${encodeURIComponent(sessionId)}` : null);
  const historyParams = new URLSearchParams({
    limit: "20",
    ...(!allHistory ? { kind } : historyKind ? { kind: historyKind } : {}),
    ...(!allHistory && kind === "stock" && instrumentId
      ? { instrument_id: instrumentId }
      : {}),
    ...(!allHistory && kind === "market" && asOf ? { as_of: asOf } : {}),
    ...(cursor ? { cursor } : {}),
  });
  const history = useApi<{ items: AISession[]; next_cursor?: string }>(
    `/ai/sessions?${historyParams}`,
  );
  const task = useAITask(taskId);
  const live = useAIStream(taskId, sessionId, task.pending);
  const serverTask = detail.data?.active_task_id;
  const pending =
    submitting ||
    (task.pending && !live.finished) ||
    (!!serverTask && serverTask !== taskId);
  const provider = providers.data?.items.find(
    (item) => item.provider_id === providerId,
  );
  const sessionMissing = detail.errorStatus === 404;
  const session = sessionMissing ? undefined : detail.data?.session;
  const completedTask = useRef("");
  const submitGuard = useRef(false);
  const mounted = useRef(true);
  const viewGeneration = useRef(0);
  const currentTaskId = useRef(taskId);
  currentTaskId.current = taskId;
  const isCurrentView = (generation: number) =>
    mounted.current && generation === viewGeneration.current;
  const attempt = useRef<{ signature: string; key: string } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const follow = useAIChatFollow(messagesRef, active, sessionId);
  const followingTask = useRef("");
  const scrolledMessage = useRef("");
  const scrolledSession = useRef("");
  const scrolledAnswerTask = useRef("");
  const readingPositions = useRef(new Map<string, number>());
  const resumeReading = useRef("");
  const partialClock = useRef({ taskId: "", at: 0 });
  if (partialClock.current.taskId !== taskId)
    partialClock.current = { taskId, at: Date.now() };
  const binding = `${providerId}:${provider?.model || ""}:${provider?.config_revision || ""}:${sessionId}`;
  function rememberReading() {
    const box = messagesRef.current;
    if (!sessionId || !box?.clientHeight) return;
    follow.capture();
    readingPositions.current.set(sessionId, box.scrollTop);
    if (readingPositions.current.size > 100)
      readingPositions.current.delete(
        readingPositions.current.keys().next().value!,
      );
  }
  useEffect(() => {
    if (!active || !sessionId) return;
    // A route can hide the pane before the browser delivers its queued scroll
    // event. Capture the visible position before navigation handles the event.
    window.addEventListener("hashchange", rememberReading, true);
    window.addEventListener("workbench-before-route", rememberReading);
    document.addEventListener("pointerdown", rememberReading, true);
    document.addEventListener("keydown", rememberReading, true);
    document.addEventListener("visibilitychange", rememberReading, true);
    return () => {
      window.removeEventListener("hashchange", rememberReading, true);
      window.removeEventListener("workbench-before-route", rememberReading);
      document.removeEventListener("pointerdown", rememberReading, true);
      document.removeEventListener("keydown", rememberReading, true);
      document.removeEventListener("visibilitychange", rememberReading, true);
    };
  }, [active, sessionId]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    attempt.current = null;
  }, [binding]);
  useEffect(() => {
    if (!active) return;
    const next = chatLocation(
      window.location.hash,
      {
        kind: session?.kind || kind,
        pageRoute,
        pageLaunchId,
        resolvedSession: !!session,
        instrumentId: session ? session.instrument_id : instrumentId,
        reportId: session ? session.report_id : reportId,
        asOf: session ? session.as_of : asOf,
      },
      sessionId,
    );
    if (next !== window.location.hash)
      window.history.replaceState(window.history.state, "", next);
  }, [
    active,
    sessionId,
    kind,
    instrumentId,
    reportId,
    asOf,
    pageRoute,
    pageLaunchId,
    session?.instrument_id,
    session?.kind,
    session?.report_id,
    session?.as_of,
    !!session,
  ]);
  useEffect(() => {
    if (session?.provider_id) setProviderId(session.provider_id);
  }, [session?.session_id]);
  useEffect(() => {
    onBusyChange?.(pending);
  }, [pending, onBusyChange]);
  useEffect(() => {
    if (!providerId && providers.data?.items.length)
      setProviderId(
        (
          providers.data.items.find(canGenerate) ||
          providers.data.items.find((item) => item.configured) ||
          providers.data.items[0]
        ).provider_id,
      );
  }, [providerId, providers.data]);
  useEffect(() => {
    if (!history.data) return;
    setHistoryRows((old) => {
      const rows = cursor ? [...old] : [];
      for (const item of history.data!.items) {
        const index = rows.findIndex(
          (entry) => entry.session_id === item.session_id,
        );
        if (index >= 0) rows[index] = item;
        else rows.push(item);
      }
      return rows;
    });
  }, [history.data, cursor]);
  function reloadHistory() {
    setCursor("");
    history.reload();
  }
  useEffect(() => {
    if (!active) return;
    return subscribeAIChanges(
      (change) => {
        if (change.scope === "providers") {
          providers.reload();
          detail.reload();
        } else if (change.scope === "session") {
          reloadHistory();
          if (change.id === sessionId) detail.reload();
        } else if (change.id === taskId) {
          task.reload();
        }
      },
      () => {
        providers.reload();
        if (!cursor) history.reload();
        detail.reload();
        if (taskId) task.reload();
      },
      () => {
        providers.reload();
        if (!cursor) history.reload();
        if (sessionId) detail.reload();
        if (taskId && task.error) task.reload();
      },
    );
  }, [
    active,
    sessionId,
    taskId,
    cursor,
    providers.reload,
    history.reload,
    detail.reload,
    task.reload,
    task.error,
  ]);
  const verifying = providers.data?.items.some(
    (item) => item.verification?.state === "verifying",
  );
  useEffect(() => {
    if (!active || !verifying || providers.loading || providers.error) return;
    const timer = setTimeout(providers.reload, 2000);
    return () => clearTimeout(timer);
  }, [active, verifying, providers.loading, providers.error, providers.reload]);
  const terminal = terminalTask(task.data?.state)
    ? task.data!.state
    : live.finished
      ? live.terminalState
      : "";
  useEffect(() => {
    if (!taskId || !terminal || completedTask.current === taskId) return;
    completedTask.current = taskId;
    setStopping(false);
    detail.reload();
    reloadHistory();
    providers.reload();
    if (sessionId) publishAIChange({ scope: "session", id: sessionId });
    if (["failed", "interrupted_manual_retry", "stale"].includes(terminal))
      setError(
        task.data?.reason_code === "budget_exceeded"
          ? aiReason("budget_exceeded")
          : typeof task.data?.message === "string" && task.data.message
            ? task.data.message
            : aiReason(task.data?.reason_code || terminal),
      );
  }, [taskId, sessionId, terminal, detail.reload, providers.reload]);
  useEffect(() => {
    if (
      !session ||
      submitting ||
      !serverTask ||
      serverTask === taskId ||
      (task.pending && !live.finished)
    )
      return;
    setTaskId(serverTask);
    setStopping(false);
    setError("");
  }, [session, submitting, serverTask, taskId, task.pending, live.finished]);
  useEffect(() => {
    if (!sessionId || !sessionMissing) return;
    newSession(true);
    setError("这段对话已删除或到期，已保留输入，可开始新对话。");
    reloadHistory();
  }, [sessionId, sessionMissing]);
  useEffect(() => {
    if (
      optimistic?.task_id &&
      detail.data?.messages.some(
        (message) =>
          message.role === "user" && message.task_id === optimistic.task_id,
      )
    )
      setOptimistic(null);
  }, [detail.data, optimistic]);
  function resetReading() {
    follow.reset();
    resumeReading.current = "";
    scrolledMessage.current = "";
    scrolledSession.current = "";
    scrolledAnswerTask.current = "";
  }
  function newSession(keepDraft = false) {
    viewGeneration.current += 1;
    resetReading();
    setHistoryOpen(false);
    setSessionId("");
    setTaskId("");
    if (!keepDraft) setText("");
    setError("");
    setBusyDraft(false);
    setOptimistic(null);
    setStopping(false);
    setDeleting(null);
    attempt.current = null;
  }
  const matches =
    !session || sessionMatches(session, provider, detail.data?.config_matches);
  const allowed =
    canGenerate(provider) &&
    matches &&
    !providers.loading &&
    !providers.error &&
    !detail.error;
  async function send() {
    if (
      submitGuard.current ||
      !allowed ||
      pending ||
      !text.trim() ||
      (sessionId && !session)
    )
      return;
    const generation = viewGeneration.current;
    submitGuard.current = true;
    setSubmitting(true);
    setError("");
    setBusyDraft(false);
    setStopping(false);
    const question = text.trim();
    follow.resume();
    const payload = sessionId
      ? { text: question, consent: true }
      : {
          kind,
          ...(reportId ? { report_id: reportId } : {}),
          ...(instrumentId ? { instrument_id: instrumentId } : {}),
          ...(asOf ? { as_of: asOf } : {}),
          provider_id: providerId,
          expected_config_revision: provider!.config_revision,
          expected_model: provider!.model,
          text: question,
          consent: true,
        };
    const signature = JSON.stringify({ path: sessionId, ...payload });
    if (attempt.current?.signature !== signature)
      attempt.current = { signature, key: requestKey() };
    const local: AIMessage = {
      message_id: `sending-${attempt.current.key}`,
      role: "user",
      text: question,
      state: "sending",
      created_at_ms: Date.now(),
    };
    setOptimistic(local);
    try {
      const response = await aiWrite<{ session_id: string; task_id: string }>(
        sessionId ? `/sessions/${sessionId}/messages` : "/sessions",
        { ...payload, request_key: attempt.current.key },
      );
      if (!isCurrentView(generation)) return;
      setOptimistic({
        ...local,
        task_id: response.task_id,
        state: "completed",
      });
      setSessionId(response.session_id || sessionId);
      setTaskId(response.task_id);
      setText("");
      attempt.current = null;
      detail.reload();
      reloadHistory();
    } catch (e: any) {
      if (isCurrentView(generation)) {
        setOptimistic(null);
        if (e.reason === "session_busy") {
          setBusyDraft(true);
          detail.reload();
        } else {
          setError(e.message);
          if (e.reason === "configuration_changed") detail.reload();
          providers.reload();
        }
      }
    } finally {
      submitGuard.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }
  async function stop() {
    if (!taskId || stopping || !task.pending || live.finished) return;
    const generation = viewGeneration.current;
    setStopping(true);
    try {
      await aiWrite(`/tasks/${taskId}/cancel`);
      if (isCurrentView(generation) && currentTaskId.current === taskId)
        task.reload();
    } catch (e: any) {
      if (isCurrentView(generation) && currentTaskId.current === taskId) {
        setError(e.message);
        setStopping(false);
      }
    }
  }
  async function keepSession(item: AISession) {
    const generation = viewGeneration.current;
    setHistoryBusy(true);
    setError("");
    try {
      await aiWrite(
        `/sessions/${item.session_id}`,
        { keep: !item.keep },
        "PATCH",
      );
      reloadHistory();
      if (isCurrentView(generation) && item.session_id === sessionId)
        detail.reload();
    } catch (e: any) {
      if (isCurrentView(generation)) setError(e.message);
    } finally {
      if (mounted.current) setHistoryBusy(false);
    }
  }
  function suggest(question: string) {
    if (!pending) {
      setText(question.slice(0, 2000));
      inputRef.current?.focus();
    }
  }
  const stored = sessionMissing ? [] : detail.data?.messages || [];
  const displayed = [...stored];
  if (
    !sessionMissing &&
    optimistic &&
    !stored.some(
      (message) =>
        message.role === "user" &&
        optimistic.task_id &&
        message.task_id === optimistic.task_id,
    )
  )
    displayed.push(optimistic);
  const finalReply = stored.some(
    (message) => message.role === "assistant" && message.task_id === taskId,
  );
  if (!sessionMissing && live.text && !finalReply)
    displayed.push({
      message_id: `live-${taskId}`,
      task_id: taskId,
      role: "assistant",
      text: live.text,
      response_type: "agent_answer",
      state: ["cancelled", "failed", "interrupted_manual_retry"].includes(
        terminal,
      )
        ? terminal
        : "streaming",
      created_at_ms: partialClock.current.at,
    });
  const latest = displayed.at(-1);
  const lastKey = `${sessionId}:${latest?.message_id || ""}`;
  useEffect(() => {
    if (!active || !pending || !taskId || followingTask.current === taskId)
      return;
    followingTask.current = taskId;
    follow.resume();
  }, [active, pending, taskId, follow.resume]);
  useLayoutEffect(() => {
    if (!active) {
      resumeReading.current = sessionId;
      return;
    }
    if (resumeReading.current !== sessionId || !latest) return;
    const position = readingPositions.current.get(sessionId);
    if (position == null || !messagesRef.current) return;
    messagesRef.current.scrollTop = position;
    resumeReading.current = "";
    scrolledMessage.current = lastKey;
    scrolledSession.current = sessionId;
    follow.schedule();
  }, [active, sessionId, lastKey]);
  useEffect(() => {
    if (!active || !latest) return;
    if (scrolledMessage.current === lastKey) {
      follow.schedule();
      return;
    }
    const opening = scrolledSession.current !== sessionId;
    scrolledMessage.current = lastKey;
    scrolledSession.current = sessionId;
    const box = messagesRef.current;
    if (!box) return;
    const target = opening
      ? [...displayed]
          .reverse()
          .find((message) => message.role === "assistant") || latest
      : latest;
    if (follow.following.current) {
      if (target.role === "assistant")
        scrolledAnswerTask.current = target.task_id || "";
      follow.schedule();
      return;
    }
    if (
      !opening &&
      target.role === "assistant" &&
      target.task_id &&
      scrolledAnswerTask.current === target.task_id
    )
      return;
    const article = Array.from(
      box.querySelectorAll<HTMLElement>("[data-message-id]"),
    ).find((item) => item.dataset.messageId === target.message_id);
    if (target.role === "assistant" && article) {
      scrolledAnswerTask.current = target.task_id || "";
      box.scrollTop = Math.max(
        0,
        article.getBoundingClientRect().top -
          box.getBoundingClientRect().top +
          box.scrollTop -
          10,
      );
    } else box.scrollTop = box.scrollHeight;
  }, [active, lastKey, scrollRequest, live.text, follow.schedule]);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    const resize = () => {
      input.style.height = "0px";
      input.style.height = `${Math.min(144, Math.max(42, input.scrollHeight))}px`;
    };
    let width = input.clientWidth;
    let frame = 0;
    resize();
    const observer = new ResizeObserver(() => {
      if (input.clientWidth === width) return;
      width = input.clientWidth;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(resize);
    });
    observer.observe(input);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [text, active]);
  const suggestions =
    allHistory && !instrumentId
      ? ["今天市场表现怎么样？", "帮我研究一下贵州茅台", "市盈率高就一定贵吗？"]
      : kind === "stock"
        ? [
            "这家公司主要靠什么赚钱？",
            "当前有哪些值得关注的风险？",
            "帮我查一下最新的公司公告",
          ]
        : [
            "今天盘前竞价表现怎么样？",
            "从竞价到午间，市场发生了什么变化？",
            "当前哪些方向值得继续观察？",
          ];
  return (
    <div className="ai-workspace ai-chat-workspace">
      <aside
        className={`ai-chat-sidebar ${historyOpen ? "is-open" : ""}`}
        aria-label="历史对话"
      >
        <div className="ai-sidebar-heading">
          <h3 title="对话保存在本机，默认30天；保留后不自动清理。">历史对话</h3>
          <button
            className="ai-history-toggle"
            type="button"
            aria-expanded={historyOpen}
            aria-controls="ai-history-list"
            onClick={() => setHistoryOpen((value) => !value)}
          >
            <History size={15} />
            历史对话
          </button>
          <button
            className="btn small ai-new-chat"
            type="button"
            disabled={submitting}
            onClick={() => newSession()}
          >
            <PenSquare size={14} />
            新对话
          </button>
        </div>
        <div className="ai-history-panel" id="ai-history-list">
          <div className="ai-history-controls">
            {allHistory && (
              <div
                className="ai-history-filters"
                role="group"
                aria-label="历史对话分类"
              >
                {[
                  { value: "", label: "全部" },
                  { value: "stock", label: "个股" },
                  { value: "market", label: "复盘" },
                ].map((item) => (
                  <button
                    className={historyKind === item.value ? "is-selected" : ""}
                    key={item.value}
                    type="button"
                    aria-pressed={historyKind === item.value}
                    onClick={() => {
                      setHistoryKind(item.value as AIKind | "");
                      setCursor("");
                      setHistoryRows([]);
                    }}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            )}
            <button
              className="ai-icon-button"
              type="button"
              aria-label="刷新历史对话"
              title="刷新历史对话"
              disabled={history.loading}
              onClick={reloadHistory}
            >
              <RefreshCw size={13} />
            </button>
          </div>
          {history.error && (
            <ErrorBox message={history.error} retry={history.reload} />
          )}
          {history.loading && !historyRows.length && <Loading />}
          {!history.loading && !historyRows.length && (
            <p className="ai-history-empty">还没有历史对话</p>
          )}
          <ul className="ai-history-list">
            {historyRows.map((item) => (
              <li
                className={item.session_id === sessionId ? "is-selected" : ""}
                key={item.session_id}
              >
                <button
                  className="ai-history-open"
                  disabled={submitting}
                  onClick={() => {
                    if (item.session_id === sessionId) {
                      setHistoryOpen(false);
                      return;
                    }
                    viewGeneration.current += 1;
                    resetReading();
                    setScrollRequest((value) => value + 1);
                    setProviderId(item.provider_id);
                    setSessionId(item.session_id);
                    setTaskId("");
                    setText("");
                    setError("");
                    setBusyDraft(false);
                    setDeleting(null);
                    setOptimistic(null);
                    setStopping(false);
                    setHistoryOpen(false);
                  }}
                >
                  <strong>{chatSessionTitle(item)}</strong>
                  <time>{dateTime(item.updated_at_ms)}</time>
                </button>
                <div className="ai-history-actions">
                  <button
                    className={`ai-icon-button ${item.keep ? "is-kept" : ""}`}
                    type="button"
                    aria-label={`${item.keep ? "取消保留" : "保留"} ${chatSessionTitle(item)}`}
                    title={item.keep ? "取消保留" : "保留对话"}
                    disabled={historyBusy}
                    onClick={() => keepSession(item)}
                  >
                    <Pin size={12} />
                  </button>
                  <button
                    className="ai-icon-button"
                    type="button"
                    aria-label={`删除 ${item.session_id} 会话`}
                    title="删除对话"
                    disabled={
                      historyBusy || (pending && item.session_id === sessionId)
                    }
                    onClick={() => setDeleting(item)}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {history.data?.next_cursor && (
            <button
              className="link ai-more-history"
              disabled={history.loading}
              onClick={() => setCursor(history.data!.next_cursor!)}
            >
              更早的对话
            </button>
          )}
        </div>
      </aside>
      <section
        className="ai-conversation"
        aria-label={kind === "stock" ? "个股研究 AI 对话" : "盘面 AI 对话"}
      >
        {providers.error && (
          <ErrorBox message={providers.error} retry={providers.reload} />
        )}
        {detail.error && (
          <ErrorBox message={detail.error} retry={detail.reload} />
        )}
        {sessionId && detail.loading && !detail.data && <Loading />}
        <div className="ai-chat-scroll-area">
          <div
            className="ai-chat-messages"
            ref={messagesRef}
            tabIndex={0}
            aria-label="对话消息"
            onScroll={rememberReading}
            onWheel={follow.onWheel}
            onTouchStart={follow.onTouchStart}
            onTouchMove={follow.onTouchMove}
            onKeyDown={follow.onKeyDown}
            onPointerDown={follow.onPointerDown}
          >
            {displayed.map((message) => (
              <AIMessageView
                key={message.message_id}
                message={message}
                context={message.source_context || session?.context || {}}
                onAsk={suggest}
                suggestionsDisabled={pending}
              />
            ))}
            {!sessionId && !optimistic && (
              <div className="ai-chat-welcome">
                <div className="ai-welcome-icon" aria-hidden="true">
                  <Sparkles size={25} />
                </div>
                <h3>
                  {allHistory && !instrumentId
                    ? "今天想研究什么？"
                    : kind === "stock"
                      ? instrumentId
                        ? "想了解这家公司的哪一点？"
                        : "想研究哪家公司？"
                      : "想了解市场的哪一点？"}
                </h3>
                <p>股票研究、行情复盘，直接问就好。</p>
                <div className="ai-suggestions" aria-label="可以这样问">
                  {suggestions.map((question) => (
                    <button
                      type="button"
                      key={question}
                      onClick={() => suggest(question)}
                    >
                      {question}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          {follow.showLatest && (
            <button
              type="button"
              className="ai-scroll-latest"
              aria-label="回到最新"
              onClick={follow.resume}
            >
              <ArrowDown size={14} aria-hidden="true" />
              回到最新
            </button>
          )}
        </div>
        <div className="ai-chat-status" role="status">
          {stopping
            ? "正在停止…"
            : pending
              ? live.error ||
                (live.text && live.status === "正在回答…" ? "" : live.status)
              : terminal === "cancelled"
                ? "已停止，回答尚未完成"
                : ""}
          {pending && live.connection === "error" && (
            <button className="link" type="button" onClick={live.reconnect}>
              重新连接
            </button>
          )}
        </div>
        <form
          className="ai-composer"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <div className="ai-composer-box">
            <textarea
              id={`ai-question-${kind}`}
              aria-label={sessionId ? "继续提问" : "你的问题"}
              ref={inputRef}
              maxLength={2000}
              rows={1}
              value={text}
              disabled={submitting}
              placeholder={
                allHistory && !instrumentId
                  ? "输入股票、公司名称，或想了解的行情问题"
                  : kind === "stock"
                    ? "输入你想了解的公司问题"
                    : "输入你想了解的行情问题"
              }
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  (event.metaKey || event.ctrlKey) &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            {pending ? (
              <button
                className="btn ai-send-button"
                type="button"
                aria-label="停止"
                title="停止回答"
                disabled={
                  submitting || stopping || !task.pending || live.finished
                }
                onClick={stop}
              >
                <Square size={15} aria-hidden="true" />
              </button>
            ) : (
              <button
                className="btn primary ai-send-button"
                type="submit"
                aria-label="发送"
                disabled={!allowed || !text.trim() || (!!sessionId && !session)}
                title={
                  !canGenerate(provider)
                    ? "请先在模型设置中启用模型"
                    : undefined
                }
              >
                <ArrowUp size={20} aria-hidden="true" />
              </button>
            )}
          </div>
          <div className="ai-composer-footer">
            <div className="ai-model-control">
              <span className="ai-model-select">
                <select
                  aria-label="对话模型"
                  title="切换模型会开始新对话，当前对话保留在历史"
                  id={`ai-provider-${kind}`}
                  value={providerId}
                  disabled={pending || providers.loading}
                  onChange={(event) => {
                    setProviderId(event.target.value);
                    newSession(true);
                  }}
                >
                  {!providerId && <option value="">正在读取模型…</option>}
                  {(providers.data?.items || []).map((item) => (
                    <option key={item.provider_id} value={item.provider_id}>
                      {item.name} · {item.model}
                    </option>
                  ))}
                </select>
                <ChevronDown size={13} aria-hidden="true" />
              </span>
              {onConfigure && (
                <button
                  className="link ai-model-settings"
                  type="button"
                  onClick={onConfigure}
                >
                  <SlidersHorizontal size={13} aria-hidden="true" />
                  模型设置
                </button>
              )}
            </div>
            <span className="ai-input-hint">
              {text.length ? `${text.length}/2000 · ` : ""}Ctrl / ⌘ + Enter
            </span>
          </div>
          <p className="ai-egress-note">AI 可查询站内及网络资料，可能计费。</p>
          {!matches && !providers.loading && provider && (
            <p className="ai-chat-unavailable">
              模型设置已更改，请开始新对话。
            </p>
          )}
          {busyDraft && (
            <p className="ai-chat-unavailable" role="status">
              {pending || detail.loading || detail.error
                ? "这段对话正在处理另一条提问。草稿已保留，回答结束后可继续发送。"
                : "草稿已保留，可以继续发送。"}
            </p>
          )}
          {error && (
            <p className="ai-chat-unavailable" role="alert">
              {error}
            </p>
          )}
        </form>
      </section>
      {deleting && (
        <Modal title="删除本地对话" onClose={() => setDeleting(null)}>
          <div className="modal-body">
            <p>删除后将无法在本机继续阅读这段对话。</p>
          </div>
          <div className="modal-footer">
            <button
              className="btn"
              disabled={historyBusy}
              onClick={() => setDeleting(null)}
            >
              取消
            </button>
            <button
              className="btn danger"
              disabled={historyBusy}
              onClick={async () => {
                const generation = viewGeneration.current;
                setHistoryBusy(true);
                try {
                  await aiWrite(
                    `/sessions/${deleting.session_id}`,
                    undefined,
                    "DELETE",
                  );
                  if (isCurrentView(generation)) {
                    if (deleting.session_id === sessionId) newSession(true);
                    setDeleting(null);
                  }
                  reloadHistory();
                } catch (e: any) {
                  if (isCurrentView(generation)) {
                    setError(e.message);
                    if (e.reason === "session_busy") detail.reload();
                  }
                } finally {
                  if (mounted.current) setHistoryBusy(false);
                }
              }}
            >
              <Trash2 size={13} />
              删除对话
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
