import WorkspaceTaskSummary from "../../components/WorkspaceTaskSummary";
import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Layers3,
  ShieldAlert,
  Database,
  Download,
  FileClock,
  Pause,
  Play,
  RefreshCw,
  X,
} from "lucide-react";
import {
  api,
  list,
  post,
  requestKey,
  useApi,
  usePageActive,
} from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  dateTime,
  duration,
  integer,
  label,
  num,
  statusText,
  textValue,
} from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Panel,
  Spinner,
  Stat,
  Status,
  Table,
  useToast,
  Warning,
  Warnings,
} from "../../components/UI";
import {
  dataTaskTitle,
  dataNoonTaskControls,
  sectorChartsTaskProgress,
  sectorChartsStatusView,
  sectorChartsInitializationView,
  sectorChartsTaskBlocked,
  isSectorChartsInitialTask,
  initializationTaskBlocked,
  initializationView,
  initializationWindowView,
  incrementWindowView,
  supplementCoverageView,
  visibleWarnings,
} from "./dataPresentation";
import "./DataCenter.css";
import AutomaticReviewSettings from "./AutomaticReviewSettings";
import IntradayPageNavigation from "../market/IntradayPageNavigation";
import { intradayReadingProps } from "../market/intradayReadingNavigation";
import type { AutomaticPlan } from "./automaticReviewPresentation";
import CoveragePanel from "./CoveragePanel";
import PublishedStorageDetails from "./PublishedStorageDetails";
import { publishedStorageView } from "./storagePresentation";
import {
  acceptIncrementSession,
  beginIncrementSession,
  rejectIncrementSession,
} from "../../components/incrementSession";
const activeStatuses = ["queued", "pending", "running"];
const publishedStatuses = [
  "completed",
  "success",
  "succeeded",
  "no_change",
  "partial",
];
function taskChainState(task?: Row | null) {
  let state: "none" | "pending" | "queued" | "blocked" = "none";
  let nextTaskId: string | null = null,
    reason: string | null = null;
  for (const event of task?.logs || []) {
    if (event.stage === "chain_authorized" && state === "none")
      state = "pending";
    if (
      event.stage === "followup_queued" &&
      typeof event.details?.next_task_id === "string" &&
      event.details.next_task_id
    ) {
      state = "queued";
      nextTaskId = event.details.next_task_id;
      reason = null;
    }
    if (event.stage === "chain_blocked") {
      state = "blocked";
      nextTaskId = null;
      reason =
        event.details?.reason ||
        event.message ||
        "后续任务暂未排队，请查看阶段日志";
    }
  }
  return { state, nextTaskId, reason };
}
function TaskProgress({
  task,
  onAction,
  initialization,
  sectorInitialization,
}: {
  task: Row;
  onAction?: (id: string, action: string) => void;
  initialization?: Row;
  sectorInitialization?: Row;
}) {
  const blockedInitialization = initializationTaskBlocked(task, initialization);
  const sectorInitial = sectorChartsInitializationView(sectorInitialization);
  const sectorInitialTask = isSectorChartsInitialTask(task);
  const blockedSectorInitialization = sectorChartsTaskBlocked(
    task,
    sectorInitialization,
  );
  let progress = Number(task.progress ?? task.progress_pct ?? 0);
  progress = Math.min(100, Math.max(0, progress));
  const logs = task.logs || task.events || [];
  const sectors = sectorChartsTaskProgress(task);
  const noonControls = dataNoonTaskControls(task);
  return (
    <div className="task-progress">
      <div className="task-progress-head">
        <div className="task-icon">
          {activeStatuses.includes(task.status) ? (
            <Spinner />
          ) : task.status === "failed" ? (
            <X size={22} className="danger-text" />
          ) : task.status === "paused" || task.status === "cancelled" ? (
            <Pause size={22} />
          ) : (
            <CheckCircle2 size={22} />
          )}
        </div>
        <div>
          <h3>
            {dataTaskTitle(task)}{" "}
            <Status value={task.display_status || task.status} />
          </h3>
          <p>
            <code>{(task.task_id || task.job_id || "").slice(0, 16)}</code> ·{" "}
            {task.target_date ||
              task.business_date ||
              dateTime(task.created_at ?? task.created_at_ms)}
          </p>
        </div>
        <strong>
          {num(progress, 0)}
          <small>%</small>
        </strong>
      </div>
      <div className="progress-bar large">
        <i style={{ width: progress + "%" }} />
      </div>
      <div className="progress-caption">
        <span>
          当前阶段：
          {activeStatuses.includes(task.status)
            ? task.message || statusText[task.stage] || "正在等待任务回报"
            : "此任务步骤已结束，结果以实际回执为准"}
        </span>
        {task.stage_progress != null && (
          <span>{num(Number(task.stage_progress), 0)}%</span>
        )}
      </div>
      <WorkspaceTaskSummary workspace={task.workspace} />
      {task.coverage_summary && (
        <p className="muted">{task.coverage_summary}</p>
      )}
      {sectors && (
        <div className="data-sector-progress" aria-live="polite">
          <span>
            已处理{" "}
            <b>
              {integer(sectors.completed)} / {integer(sectors.total)}
            </b>{" "}
            个板块
          </span>
          <span>
            已接受 <b>{integer(sectors.available)}</b>
          </span>
          <span>
            失败 <b>{integer(sectors.failed)}</b>
          </span>
          <span>
            缓存复验 <b>{integer(sectors.cached)}</b>
          </span>
          <span>
            实际请求 <b>{integer(sectors.requested)}</b>
          </span>
          <span>
            来源阻断未请求 <b>{integer(sectors.unrequested)}</b>
          </span>
          {(sectors.complete !== null || sectors.partial !== null) && (
            <span>
              完整 {integer(sectors.complete)} · 部分覆盖{" "}
              {integer(sectors.partial)}
            </span>
          )}
          {sectors.collectionComplete === true &&
            sectors.historyComplete === false && (
              <small>
                全库均已取得可用K线；部分覆盖包括来源滚动边界等缺口，不等于下载失败。
              </small>
            )}
        </div>
      )}
      {logs.length > 0 && (
        <details
          className="data-task-log-details"
          open={activeStatuses.includes(task.status)}
        >
          <summary>
            <ChevronRight size={13} />
            阶段日志{" "}
            <span>
              最近 {Math.min(logs.length, 100)} 条 / 共 {logs.length} 条
            </span>
          </summary>
          <div className="task-logs">
            {logs.slice(-100).map((log: any, i: number) => (
              <div key={log.seq || i}>
                <time>
                  {dateTime(log.time || log.created_at || log.recorded_at)}
                </time>
                <span className="log-stage">
                  {log.stage ? "[" + log.stage + "]" : ""}
                </span>
                <span>
                  {typeof log === "string"
                    ? log
                    : log.message || log.content || textValue(log)}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
      {(task.error || task.error_summary) && (
        <ErrorBox message={textValue(task.error || task.error_summary)} />
      )}
      <div className="task-footer">
        <span>
          {task.actual_total_batches != null && (
            <>
              实际批次 {integer(task.actual_completed_batches)} /{" "}
              {integer(task.actual_total_batches)} ·{" "}
            </>
          )}
          {sectors ? (
            <>
              接受板块 {integer(sectors.available)} / {integer(sectors.total)}
            </>
          ) : (
            <>接受记录 {integer(task.row_count ?? task.accepted_rows)}</>
          )}
        </span>
        {onAction && (
          <div className="button-group">
            {activeStatuses.includes(task.status) &&
              task.interactive_stop_allowed !== false &&
              task.kind !== "increment" &&
              !["auction_snapshot", "noon_snapshot"].includes(
                task.operation || task.parameters?.operation,
              ) && (
                <>
                  <button
                    className="btn small"
                    onClick={() =>
                      onAction(task.task_id || task.job_id, "pause")
                    }
                  >
                    <Pause size={13} />
                    暂停
                  </button>
                  <button
                    className="btn small"
                    onClick={() =>
                      onAction(task.task_id || task.job_id, "cancel")
                    }
                  >
                    <X size={13} />
                    取消
                  </button>
                </>
              )}
            {task.status === "paused" &&
              noonControls.canResumeHere &&
              !blockedSectorInitialization && (
                <button
                  className="btn small"
                  disabled={
                    blockedInitialization ||
                    (sectorInitialTask && !sectorInitial.known)
                  }
                  title={
                    blockedInitialization
                      ? "主行情已发布，请使用增量或补充任务"
                      : sectorInitialTask && !sectorInitial.known
                        ? sectorInitial.reason
                        : undefined
                  }
                  onClick={() =>
                    onAction(task.task_id || task.job_id, "resume")
                  }
                >
                  <Play size={13} />
                  继续原任务
                </button>
              )}
            {["failed", "partial", "cancelled"].includes(task.status) &&
              noonControls.canRetry &&
              task.display_status !== "succeeded" &&
              !blockedSectorInitialization && (
                <button
                  className="btn small"
                  disabled={
                    blockedInitialization ||
                    (sectorInitialTask && !sectorInitial.canInitialize)
                  }
                  title={
                    blockedInitialization
                      ? "主行情已发布，请使用增量或补充任务"
                      : sectorInitialTask && !sectorInitial.canInitialize
                        ? sectorInitial.reason
                        : undefined
                  }
                  onClick={() => onAction(task.task_id || task.job_id, "retry")}
                >
                  <RefreshCw size={13} />
                  创建重试任务
                </button>
              )}
            {noonControls.noon && (
              <a className="btn small" href="#noon">
                查看午评状态
              </a>
            )}
          </div>
        )}
      </div>
      {blockedInitialization &&
        ["failed", "partial", "cancelled", "paused"].includes(task.status) && (
          <p className="data-task-published-note">
            主行情已发布，此初始化任务不能重复运行；后续缺口请使用增量、题材刷新或盘面补充。
          </p>
        )}
      {blockedSectorInitialization && (
        <p className="data-task-published-note">
          板块K线已初始化，此整库任务不再重复运行；部分历史边界缺口保留说明，之后通过盘后增量追加尾部报价。
        </p>
      )}
      {noonControls.notice && (
        <p className="data-task-published-note">{noonControls.notice}</p>
      )}
    </div>
  );
}
function DataWarnings({ items }: { items: unknown }) {
  const warnings = visibleWarnings(items);
  if (!warnings.length) return null;
  return (
    <details className="data-warning-summary">
      <summary>
        <ShieldAlert size={15} />
        <strong>{warnings.length} 项数据说明与缺口</strong>
        <span>
          查看详情
          <ChevronRight size={13} />
        </span>
      </summary>
      <div>
        <Warnings items={warnings} />
      </div>
    </details>
  );
}
function linkedTaskId() {
  if (typeof location === "undefined" || !location.hash.startsWith("#data?"))
    return "";
  const id =
    new URLSearchParams(location.hash.split("?")[1] || "").get("task") || "";
  return /^job_[a-zA-Z0-9_-]{1,180}$/.test(id) ? id : "";
}

export default function DataCenter({
  onPublished,
  initialTaskId,
}: {
  onPublished?: () => void;
  initialTaskId?: string;
}) {
  const result = useApi<Row>("/data/status"),
    history = useApi<Row>("/tasks?limit=200&summary=true");
  const active = usePageActive();
  const [selectedId, setSelectedId] = useState(linkedTaskId),
    [followChain, setFollowChain] = useState(() => !linkedTaskId()),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [showAllJobs, setShowAllJobs] = useState(false);
  const [afterClose, setAfterClose] = useState<AutomaticPlan | null>(null);
  useEffect(() => {
    const followLink = () => {
      const id = linkedTaskId();
      if (id) {
        setSelectedId(id);
        setFollowChain(false);
      }
    };
    window.addEventListener("hashchange", followLink);
    return () => window.removeEventListener("hashchange", followLink);
  }, []);
  const task = useApi<Row>(selectedId ? `/tasks/${selectedId}` : null);
  const toast = useToast();
  const data = result.data,
    summary = data?.summary || data?.data_summary || {};
  const storage = publishedStorageView(summary);
  const recent = list(history.data);
  const previousHistoryStatuses = useRef<Map<string, string> | null>(null);
  const chain = taskChainState(task.data);
  const selectedActive = activeStatuses.includes(task.data?.status || "");
  const activeHistory = recent.some((item) =>
    activeStatuses.includes(item.status),
  );
  const pendingHistoryChain = recent.some(
    (item) =>
      publishedStatuses.includes(item.status) &&
      taskChainState(item).state === "pending",
  );
  const waitingForChild =
    followChain &&
    publishedStatuses.includes(task.data?.status || "") &&
    chain.state === "pending";
  const syncKinds = new Set([
    "initialise",
    "increment",
    "topic_init",
    "topic_refresh",
    "source_check",
  ]);
  const displayedHistory = recent.filter(
    (r) => showAllJobs || syncKinds.has(r.kind),
  );
  useEffect(() => {
    if (initialTaskId) setSelectedId(initialTaskId);
  }, [initialTaskId]);
  useEffect(() => {
    const latest =
      recent.find(
        (x) => syncKinds.has(x.kind) && activeStatuses.includes(x.status),
      ) || data?.latest_task;
    if (latest && !selectedId) setSelectedId(latest.task_id || latest.job_id);
  }, [data, recent]);
  useEffect(() => {
    if (!active || !task.data) return;
    if (
      followChain &&
      chain.state === "queued" &&
      chain.nextTaskId &&
      publishedStatuses.includes(task.data.status)
    ) {
      setSelectedId(chain.nextTaskId);
      history.reload();
      return;
    }
    if (task.loading || (!selectedActive && !waitingForChild)) return;
    const timer = setTimeout(task.reload, 2000);
    return () => clearTimeout(timer);
  }, [active, task.data, task.loading, task.error, followChain]);
  // Viewing a completed historical task must not freeze a currently running
  // job's status, button availability or its newly queued follow-up.
  useEffect(() => {
    if (
      !active ||
      history.loading ||
      (!activeHistory &&
        !selectedActive &&
        !pendingHistoryChain &&
        !waitingForChild)
    )
      return;
    const timer = setTimeout(history.reload, 2000);
    return () => clearTimeout(timer);
  }, [
    active,
    history.data,
    history.loading,
    history.error,
    selectedActive,
    pendingHistoryChain,
    waitingForChild,
  ]);
  useEffect(() => {
    const previous = previousHistoryStatuses.current;
    const publishedElsewhere = recent.some((item) => {
      const id = item.task_id || item.job_id;
      return (
        previous !== null &&
        id !== selectedId &&
        publishedStatuses.includes(item.status) &&
        (!previous.has(id) ||
          !publishedStatuses.includes(previous.get(id) || ""))
      );
    });
    previousHistoryStatuses.current = new Map(
      recent.map((item) => [item.task_id || item.job_id, item.status]),
    );
    if (publishedElsewhere) {
      result.reload();
      onPublished?.();
    }
  }, [history.data]);
  useEffect(() => {
    if (
      task.data &&
      ["completed", "success", "succeeded", "no_change", "partial"].includes(
        task.data.status,
      )
    ) {
      result.reload();
      history.reload();
      onPublished?.();
    }
  }, [task.data?.status, selectedId]);
  async function start(mode: string) {
    setBusy(mode);
    setError("");
    const key = requestKey();
    let incrementSession: ReturnType<typeof beginIncrementSession> | null =
      null;
    try {
      if (mode === "increment") incrementSession = beginIncrementSession(key);
      const initialWindow = initializationWindowView(
        data?.initialization_window,
      );
      if (mode === "initialise" && !initialWindow.known)
        throw new Error("尚未确认初始化历史窗口，请刷新后重试");
      if (mode === "sector_charts") {
        const sectorInitial = sectorChartsInitializationView(
          data?.sector_charts?.initialization,
        );
        if (!sectorInitial.canInitialize) throw new Error(sectorInitial.reason);
      }
      const r =
        mode === "sector_charts"
          ? await post("/sync/sector-charts", { request_key: requestKey() })
          : mode === "market_supplement"
            ? await post("/sync/market-supplement", {
                year_history: true,
                recent_days: 1,
                request_key: requestKey(),
              })
            : await post("/sync/tasks", {
                mode,
                ...(mode === "initialise"
                  ? { window: initialWindow.window }
                  : {}),
                request_key: key,
              });
      if (incrementSession)
        acceptIncrementSession(
          incrementSession,
          r.task_id || r.job_id,
          r.request_key,
        );
      setSelectedId(r.task_id || r.job_id);
      setFollowChain(true);
      history.reload();
      result.reload();
      toast("同步任务已创建，可查看实际阶段与批次进度");
    } catch (e: any) {
      setError(e.message);
      if (incrementSession && e.status >= 400 && e.status < 500)
        rejectIncrementSession(incrementSession, e.message);
    } finally {
      setBusy("");
    }
  }
  async function taskAction(id: string, action: string) {
    try {
      const actingTask =
        task.data?.task_id === id || task.data?.job_id === id
          ? task.data
          : recent.find((item) => (item.task_id || item.job_id) === id);
      if (
        actingTask &&
        dataNoonTaskControls(actingTask).noon &&
        ["retry", "resume"].includes(action)
      )
        throw new Error("午评每天只提交一次，请到午评页查看原任务与可用窗口。");
      if (
        ["retry", "resume"].includes(action) &&
        actingTask &&
        sectorChartsTaskBlocked(actingTask, data?.sector_charts?.initialization)
      )
        throw new Error(
          sectorChartsInitializationView(data?.sector_charts?.initialization)
            .reason,
        );
      const r = await post(`/tasks/${id}/${action}`);
      if (r.task_id || r.job_id) {
        setSelectedId(r.task_id || r.job_id);
        setFollowChain(true);
      }
      task.reload();
      history.reload();
      toast(
        (
          {
            pause: "已请求暂停，显式暂停不会自行恢复",
            cancel: "已请求取消，在安全批次边界停止",
            resume: "继续原授权任务",
            retry: "重试任务已创建",
          } as Row
        )[action] || "操作已提交",
      );
    } catch (e: any) {
      setError(e.message);
    }
  }
  const running =
    activeHistory || selectedActive || pendingHistoryChain || waitingForChild;
  const syncConfiguration = data?.sync_configuration;
  const needsSource = syncConfiguration?.ready !== true;
  const initialization = initializationView(
    data?.initialization,
    data?.initialization_window,
  );
  const initializationWindow = initializationWindowView(
    data?.initialization_window,
  );
  const incrementWindow = incrementWindowView(data?.sync_window);
  const incrementComplete = data?.sync_operations?.increment;
  const supplementComplete = data?.sync_operations?.supplement;
  const supplementCoverage = supplementCoverageView(data?.public_supplement);
  const sectorStatus = sectorChartsStatusView(data?.sector_charts);
  const sectorInitialization = sectorChartsInitializationView(
    data?.sector_charts?.initialization,
  );
  const latestSectorJob =
    recent.find(
      (item) =>
        item.kind === "source_check" &&
        item.parameters?.operation === "sector_charts" &&
        item.target_date === sectorStatus.targetDate,
    ) || sectorStatus.lastJob;
  const latestSectorId = latestSectorJob?.task_id || latestSectorJob?.job_id;
  const liveSectorJob =
    latestSectorId &&
    (task.data?.task_id === latestSectorId ||
      task.data?.job_id === latestSectorId)
      ? task.data
      : latestSectorJob;
  const sectorActive = activeStatuses.includes(liveSectorJob?.status || "");
  const sectorIncrement = liveSectorJob?.parameters?.mode === "increment";
  const sectorProgress =
    sectorActive && liveSectorJob
      ? sectorChartsTaskProgress(liveSectorJob)
      : null;
  const sectorCounts = sectorProgress || sectorStatus;
  const sectorTotal = sectorProgress
    ? sectorProgress.total
    : sectorStatus.catalog;
  useEffect(() => {
    // Recheck the server clock while this page stays open across 18:00.
    if (!active) return;
    const timer = setTimeout(result.reload, 60000);
    return () => clearTimeout(timer);
  }, [active, data?.sync_window?.now, result.loading]);
  return (
    <IntradayPageNavigation
      className="data-center"
      scopeKey="data-center"
      pageTitle="数据中心"
      pageActive={active}
    >
      <div className="data-center-heading">
        <div>
          <span>数据工作区</span>
          <p>同步与任务设置，按阶段核验，保留每次运行记录。</p>
        </div>
      </div>
      <div {...intradayReadingProps("dataSummary")} className="stats-grid">
        <Stat
          label="行情标的"
          value={integer(summary.stock_count)}
          sub="当前数据集实际覆盖"
        />
        <Stat
          label="日K记录"
          value={integer(summary.row_count)}
          sub={`${summary.first_date || "—"} → ${summary.last_date || data?.business_date || "—"}`}
        />
        <Stat
          label="本实例已发布文件"
          value={storage.value}
          sub={storage.sub}
        />
        <Stat
          label="当前业务日"
          value={data?.business_date || summary.last_date || "—"}
          sub="各模块分别发布覆盖水位"
        />
      </div>
      <PublishedStorageDetails view={storage} />
      <div {...intradayReadingProps("automatic")}>
        <AutomaticReviewSettings
          onAfterCloseSaved={setAfterClose}
          onSelectTask={(id) => {
            setSelectedId(id);
            setFollowChain(false);
          }}
        />
      </div>
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      <DataWarnings items={data?.warnings} />
      {error && <ErrorBox message={error} />}
      {syncConfiguration && (
        <div className="data-source-bar">
          <div className="data-source-label">
            <Database size={16} />
            <div>
              <span>同步来源</span>
              <strong>{syncConfiguration.name || "尚未选择"}</strong>
            </div>
            <Badge tone={syncConfiguration.ready ? "good" : "warn"}>
              {syncConfiguration.ready ? "已配置" : "待配置"}
            </Badge>
          </div>
          <span className="data-source-description">
            {syncConfiguration.ready
              ? "各阶段分别核验权限与完整性"
              : syncConfiguration.reason}
          </span>
          <a className="btn small" href="#sources">
            配置数据源
            <ChevronRight size={12} />
          </a>
        </div>
      )}
      <div {...intradayReadingProps("syncActions")} className="data-operations">
        <section
          className={`data-operation data-operation-initial ${initialization.mainPublished ? "is-published" : ""}`}
        >
          <div className="data-operation-top">
            <span className="data-operation-number">01</span>
            <Badge tone={initialization.mainPublished ? "good" : "neutral"}>
              {initialization.mainPublished ? "主行情已发布" : "首次使用"}
            </Badge>
          </div>
          <h2>
            <Database size={17} />
            首次初始化
          </h2>
          <p
            className="data-operation-description"
            title={initializationWindow.notice}
          >
            目标{initializationWindow.label}
            个股、指数日K与基础资料，衔接题材、目标日盘面及行业/概念近五年K线；周K、月K在本地生成，实际范围逐项核验。
          </p>
          <button
            className="btn primary"
            disabled={
              !!busy ||
              running ||
              needsSource ||
              !initialization.canInitialize ||
              !initializationWindow.known ||
              result.loading
            }
            onClick={() => start("initialise")}
            title={
              initializationWindow.known
                ? initialization.reason
                : "正在确认初始化历史窗口，请刷新后重试"
            }
          >
            {busy === "initialise" ? (
              <Spinner />
            ) : initialization.mainPublished ? (
              <CheckCircle2 size={14} />
            ) : (
              <Download size={14} />
            )}
            {initialization.label}
          </button>
          <small className="data-operation-note">
            {initialization.mainPublished
              ? "主行情完成；初始化链已包含后续补充，失败部分可单独修补。"
              : initialization.reason}
          </small>
        </section>
        <section className="data-operation data-operation-increment">
          <div className="data-operation-top">
            <span className="data-operation-number">02</span>
            <Badge>日常更新</Badge>
          </div>
          <h2>
            <RefreshCw size={17} />
            盘后增量
          </h2>
          <div className="data-operation-description">
            <p>更新主行情和板块 K 线尾部，盘面数据只补目标交易日。</p>
            <p className="muted">
              {incrementWindow.cutoff
                ? `手动最早 ${incrementWindow.cutoff}（北京时间）`
                : "手动窗口待服务确认"}
              {afterClose &&
                (afterClose.enabled
                  ? ` · 自动执行 ${afterClose.time}`
                  : " · 自动盘后已关闭")}
            </p>
          </div>
          <button
            className="btn"
            disabled={
              !!busy ||
              running ||
              needsSource ||
              !initialization.mainPublished ||
              !incrementWindow.allowed ||
              incrementComplete?.completed === true
            }
            title={incrementComplete?.reason || incrementWindow.reason}
            onClick={() => start("increment")}
          >
            {busy === "increment" ? <Spinner /> : <Play size={14} />}
            {incrementComplete?.completed
              ? "本日主行情已更新"
              : incrementWindow.label}
          </button>
          <small className="data-operation-note">
            {initialization.mainPublished
              ? incrementComplete?.reason || incrementWindow.reason
              : "主行情初始化发布后可用。"}
          </small>
        </section>
        <section className="data-operation data-operation-supplement">
          <div className="data-operation-top">
            <span className="data-operation-number">03</span>
            <Badge>失败修补</Badge>
          </div>
          <h2>
            <Layers3 size={17} />
            盘面补充
          </h2>
          <p className="data-operation-description">
            修补封板、行业与概念的板块日行情及年内统计；历史成分股单独核验。
          </p>
          <button
            className="btn"
            disabled={
              !!busy ||
              running ||
              !initialization.mainPublished ||
              supplementComplete?.completed === true
            }
            title={supplementComplete?.reason || undefined}
            onClick={() => start("market_supplement")}
          >
            {busy === "market_supplement" ? (
              <Spinner />
            ) : (
              <RefreshCw size={14} />
            )}
            {supplementComplete?.completed
              ? "本日盘面补充已完成"
              : "修补盘面缺口"}
          </button>
          <small className="data-operation-note">
            {supplementComplete?.reason ||
              supplementCoverage.missing ||
              "补充范围以当前行情日为准，已核验批次会复用。"}
          </small>
        </section>
        <section
          className={`data-operation data-operation-sector${sectorInitialization.initialized ? " is-published" : ""}`}
        >
          <div className="data-operation-top">
            <span className="data-operation-number">04</span>
            <Badge
              tone={
                sectorActive
                  ? "info"
                  : sectorInitialization.initialized
                    ? "good"
                    : "neutral"
              }
            >
              {sectorActive
                ? sectorIncrement
                  ? "尾部增量中"
                  : "首次核验中"
                : sectorInitialization.initialized
                  ? "首次初始化已完成"
                  : "独立补齐"}
            </Badge>
          </div>
          <h2>
            <Layers3 size={17} />
            行业与概念历史K线
          </h2>
          <div className="data-operation-description">
            <p>
              当前目录 {integer(sectorStatus.catalog)}{" "}
              个板块，目标近五年日K；周K、月K本地生成。
              {sectorInitialization.initialized
                ? "历史已留存，之后随盘后增量追加尾部，不重复整库下载。"
                : "首次独立补齐，不重新初始化主行情。"}
            </p>
            <div className="data-sector-counts" aria-live="polite">
              <span>
                已接受{" "}
                <b>
                  {integer(sectorCounts.available)} / {integer(sectorTotal)}
                </b>
              </span>
              <span>
                完整 <b>{integer(sectorCounts.complete)}</b>
              </span>
              <span>
                部分覆盖 <b>{integer(sectorCounts.partial)}</b>
              </span>
              <span>
                失败 <b>{integer(sectorCounts.failed)}</b>
              </span>
            </div>
          </div>
          <button
            className="btn"
            disabled={
              !!busy ||
              running ||
              !sectorInitialization.canInitialize ||
              needsSource ||
              !initialization.mainPublished ||
              sectorStatus.catalog === null ||
              sectorStatus.catalog === 0 ||
              !sectorStatus.targetDate
            }
            title={sectorInitialization.reason}
            onClick={() => start("sector_charts")}
          >
            {sectorInitialization.initialized ? (
              <CheckCircle2 size={14} />
            ) : busy === "sector_charts" || sectorActive ? (
              <Spinner />
            ) : (
              <Download size={14} />
            )}
            {sectorInitialization.label}
          </button>
          <div className="data-operation-note">
            <small>
              观察日 {sectorStatus.targetDate || "待确认"}。
              {!sectorInitialization.initialized &&
                "全库首次补齐可能需要20分钟以上；"}
              来源近五年按获取日滚动，首日边界等部分覆盖单列，不计下载失败。
              {sectorInitialization.initialized &&
                ` 首次初始化已完成${sectorInitialization.initializedDate ? "（" + sectorInitialization.initializedDate + "）" : ""}，后续使用盘后增量。`}
              {sectorStatus.available === null &&
                !sectorActive &&
                !sectorInitialization.initialized &&
                " 尚无整批核验记录，计数保持未知。"}
            </small>
            {latestSectorId && (
              <button
                className="link data-sector-job-link"
                onClick={() => {
                  setSelectedId(latestSectorId);
                  setFollowChain(false);
                }}
              >
                查看{sectorActive ? "运行进度" : "最近核验记录"}
                <ChevronRight size={12} />
                {liveSectorJob?.status && (
                  <Status
                    value={liveSectorJob.display_status || liveSectorJob.status}
                  />
                )}
              </button>
            )}
          </div>
        </section>
      </div>
      <div className="data-supplement-summary">
        <span>
          <i className="data-summary-dot" />
          补充覆盖
        </span>
        <span>
          封板明细 <b>{integer(data?.public_supplement?.seal_rows)}</b> 条
        </span>
        <span>
          板块日行情{" "}
          <b>{integer(data?.public_supplement?.sector_index_rows)}</b> /{" "}
          {integer(data?.public_supplement?.sector_index_catalog_count)} 项
        </span>
        <span>
          涨停完整日{" "}
          <b>{integer(data?.public_supplement?.annual_covered_days)}</b> /{" "}
          {integer(data?.public_supplement?.annual_expected_days)}
        </span>
        <span>
          跌停完整日{" "}
          <b>{integer(data?.public_supplement?.annual_down_covered_days)}</b> /{" "}
          {integer(data?.public_supplement?.annual_down_expected_days)} 日
        </span>
        <details>
          <summary>
            <CircleHelp size={12} />
            口径与限制
          </summary>
          <p>
            完整日为来源池核验结果；个股覆盖另行核验。可在题材库同步新增分类，已有成员保留各自采集日。连续封单稳定性需要逐时盘口序列。
          </p>
        </details>
      </div>
      {task.data && (
        <div {...intradayReadingProps("task")}>
          <Panel
            title="当前任务"
            actions={
              <button
                className="icon-btn"
                aria-label="刷新任务"
                onClick={task.reload}
              >
                <RefreshCw size={14} />
              </button>
            }
          >
            <TaskProgress
              task={task.data}
              onAction={taskAction}
              initialization={data?.initialization}
              sectorInitialization={data?.sector_charts?.initialization}
            />
            {waitingForChild && (
              <Warning>
                本阶段已发布，链式初始化仍待后台确认下一阶段。正在根据已授权链日志检查排队状态；不会因超过固定等待时间而宣告全部完成。
              </Warning>
            )}
            {chain.state === "blocked" && (
              <Warning>
                后续阶段暂未排队：{chain.reason}
                。当前已发布数据保留，请查看日志及可重试任务。
              </Warning>
            )}
            {!followChain && (activeHistory || pendingHistoryChain) && (
              <div className="panel-footnote">
                正在查看历史记录；运行任务与链式状态仍每2秒刷新，页面不会自动跳转。
                <button
                  className="btn small"
                  onClick={() => {
                    const current =
                      recent.find((item) =>
                        activeStatuses.includes(item.status),
                      ) ||
                      recent.find(
                        (item) =>
                          publishedStatuses.includes(item.status) &&
                          taskChainState(item).state === "pending",
                      );
                    if (current) {
                      setSelectedId(current.task_id || current.job_id);
                      setFollowChain(true);
                    }
                  }}
                >
                  查看当前运行链
                </button>
              </div>
            )}
          </Panel>
        </div>
      )}
      {task.error && <ErrorBox message={task.error} retry={task.reload} />}
      <div {...intradayReadingProps("dataCoverage")}>
        <CoveragePanel
          datasets={data?.datasets || []}
          audit={data?.coverage_check}
          loading={result.loading}
          disabled={!!busy || running}
          remoteConfigured={!needsSource}
          onReload={result.reload}
          onTask={(next) => {
            setSelectedId(next.task_id || next.job_id);
            setFollowChain(false);
            history.reload();
            result.reload();
          }}
        />
      </div>
      <div {...intradayReadingProps("taskHistory")}>
        <Panel
          title={
            <>
              <FileClock size={18} />
              同步历史
            </>
          }
          subtitle="最近 200 条后台任务中的同步记录；可点任务查看进度、日志与失败证据"
          actions={
            <button
              className="icon-btn"
              aria-label="刷新同步历史"
              onClick={history.reload}
            >
              <RefreshCw size={14} />
            </button>
          }
        >
          <div className="result-toolbar">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={showAllJobs}
                onChange={(e) => setShowAllJobs(e.target.checked)}
              />
              包括策略、回测与研究后台任务
            </label>
          </div>
          {history.error ? (
            <ErrorBox message={history.error} retry={history.reload} />
          ) : (
            <Table
              rows={displayedHistory}
              columns={[
                {
                  key: "status",
                  title: "执行状态",
                  render: (v, r) => <Status value={r.display_status || v} />,
                },
                {
                  key: "task_id",
                  title: "任务",
                  render: (v, r) => (
                    <button
                      className="link mono"
                      onClick={() => {
                        setFollowChain(false);
                        setSelectedId(v || r.job_id);
                      }}
                    >
                      {(v || r.job_id || "").slice(0, 12)}
                    </button>
                  ),
                },
                {
                  key: "kind",
                  title: "类型",
                  render: (_v, r) => dataTaskTitle(r),
                },
                {
                  key: "created_at",
                  title: "开始时间",
                  sortValue: (row) =>
                    row.created_at_ms ?? row.created_at ?? null,
                  render: (v, r) => dateTime(v ?? r.created_at_ms),
                },
                { key: "target_date", title: "目标日期" },
                {
                  key: "progress",
                  title: "进度",
                  render: (v) => (v == null ? "—" : `${num(Number(v), 0)}%`),
                },
                {
                  key: "duration_ms",
                  title: "总历时",
                  description:
                    "从任务提交到完成或当前时刻，包含排队、暂停等等待时间，不等于纯执行耗时。",
                  render: (v) => duration(v),
                },
                {
                  key: "row_count",
                  title: "已接受记录",
                  render: (v, r) => integer(v ?? r.accepted_rows),
                },
                {
                  key: "message",
                  title: "结果与缺口",
                  render: (v, r) => r.coverage_summary || v,
                },
              ]}
              emptyTitle="尚无同步历史"
              emptyDescription="第一次手动同步后可查看阶段、批次与完整日志。"
            />
          )}
        </Panel>
      </div>
      <p className="data-center-footnote">
        <CircleHelp size={12} />
        服务启动仅按规则恢复先前已授权的中断任务，不自动发起新取数；关闭浏览器不会取消后台任务。
      </p>
    </IntradayPageNavigation>
  );
}
