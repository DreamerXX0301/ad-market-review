import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCheck,
  Layers3,
  Pause,
  Play,
  RefreshCw,
  Square,
  X,
} from "lucide-react";
import { api, list, post, requestKey } from "../../lib/api";
import type { Row } from "../../lib/api";
import { ErrorBox, Spinner } from "../../components/UI";
import {
  finishTaskRequest,
  refreshTaskActivity,
  startTaskRequest,
} from "../../components/taskActivity";
import {
  batchActive,
  batchRequest,
  batchStatusLabel,
  createBatchFeed,
  mergeBatch,
  nextStrategySelection,
  batchTrackingSummary,
  batchItemTrackingText,
} from "./strategyBatches";
import type { StrategyTarget } from "./strategyBatches";
import "./StrategyBatchToolbar.css";

export function useBatchStrategySelection(strategies: Row[]) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const ids = strategies
    .filter((item) => !item.archived)
    .map((item) => String(item.strategy_id));
  const signature = JSON.stringify(ids);
  useEffect(
    () =>
      setSelected((old) => {
        const next = new Set([...old].filter((id) => ids.includes(id)));
        return next.size === old.size ? old : next;
      }),
    [signature],
  );
  return {
    selected,
    ids,
    toggle: (id: string) =>
      setSelected((old) => nextStrategySelection(old, [id], !old.has(id))),
    setAll: (checked: boolean) =>
      setSelected((old) => nextStrategySelection(old, ids, checked)),
  };
}
export default function StrategyBatchToolbar({
  target,
  strategies,
  selection,
  onSelectAll,
  disabled = false,
  dataDate,
  onUpdated,
  onOpenRun,
  onBusyChange,
}: {
  target: StrategyTarget;
  strategies: Row[];
  selection: Set<string>;
  onSelectAll: (checked: boolean) => void;
  disabled?: boolean;
  dataDate?: string;
  onUpdated: () => void;
  onOpenRun: (item: Row) => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [batches, setBatches] = useState<Row[]>([]),
    [loading, setLoading] = useState(true),
    [readError, setReadError] = useState(""),
    [error, setError] = useState(""),
    [pending, setPending] = useState(""),
    [selectedBatch, setSelectedBatch] = useState("");
  const ids = strategies
    .filter((item) => !item.archived)
    .map((item) => String(item.strategy_id));
  const selectedIds = ids.filter((id) => selection.has(id));
  const label = target === "stock" ? "个股" : "行业 / 概念";
  const callbacks = useRef({ onUpdated, onOpenRun, onBusyChange });
  callbacks.current = { onUpdated, onOpenRun, onBusyChange };
  const request = useRef<Row | null>(null),
    submitting = useRef(false),
    mounted = useRef(true);
  const active = batches.find(batchActive),
    current =
      batches.find((item) => item.batch_id === selectedBatch) ||
      active ||
      batches[0];
  const batchBusy = !!active || !!pending;
  const feed = useMemo(
    () =>
      createBatchFeed({
        read: async (signal) =>
          list(
            await api(`/strategy-batches?target_type=${target}`, { signal }),
          ),
        onData: (items) => {
          setBatches(items);
          setLoading(false);
          if (
            request.current &&
            items.some(
              (item) => item.request_key === request.current?.body.request_key,
            )
          ) {
            request.current = null;
            setError("");
          }
        },
        onError: (message) => {
          setReadError(message);
          setLoading(false);
        },
        onProgress: () => callbacks.current.onUpdated(),
      }),
    [target],
  );
  useEffect(() => {
    mounted.current = true;
    const visible = () =>
      feed.setActive(
        !document.hidden &&
          (location.hash.slice(1).split("?")[0] || "market") === "strategy",
      );
    visible();
    window.addEventListener("hashchange", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false;
      feed.dispose();
      window.removeEventListener("hashchange", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [feed]);
  useEffect(() => {
    callbacks.current.onBusyChange(batchBusy || loading);
    return () => callbacks.current.onBusyChange(false);
  }, [batchBusy, loading]);
  async function execute(scope: "selected" | "all") {
    if (
      submitting.current ||
      disabled ||
      batchBusy ||
      loading ||
      !ids.length ||
      (scope === "selected" && !selectedIds.length)
    )
      return;
    submitting.current = true;
    setPending("submit");
    setError("");
    const attempt = batchRequest(
      request.current,
      target,
      scope,
      selectedIds,
      dataDate,
      requestKey,
    );
    request.current = attempt;
    startTaskRequest({
      request_key: attempt.body.request_key,
      kind: "selection_batch",
      target_type: target,
      title: `批量${label}策略`,
    });
    try {
      const batch = await post("/strategy-batches", attempt.body);
      if (mounted.current) {
        setBatches((old) => mergeBatch(old, batch));
        setSelectedBatch(batch.batch_id || "");
        request.current = null;
        void feed.refresh();
      }
    } catch (problem: any) {
      if (mounted.current) setError(problem.message);
    } finally {
      finishTaskRequest(attempt.body.request_key);
      submitting.current = false;
      if (mounted.current) setPending("");
    }
  }
  async function operate(action: string) {
    if (submitting.current || !current?.job_id) return;
    submitting.current = true;
    setPending(action);
    setError("");
    try {
      await post(`/tasks/${encodeURIComponent(current.job_id)}/${action}`);
      refreshTaskActivity();
      void feed.refresh();
    } catch (problem: any) {
      if (mounted.current) setError(problem.message);
    } finally {
      submitting.current = false;
      if (mounted.current) setPending("");
    }
  }
  const measured =
    typeof current?.progress === "number" && Number.isFinite(current.progress)
      ? Math.max(0, Math.min(100, current.progress))
      : null;
  const tracking = current ? batchTrackingSummary(current) : null;
  return (
    <section
      className="strategy-batch-toolbar"
      aria-label={`${label}策略批量执行`}
    >
      <div className="strategy-batch-heading">
        <Layers3 size={20} />
        <div>
          <strong>批量筛选与跟踪 · {label}</strong>
          <small>
            逐个筛选，全部命中自动加入各自策略跟踪池；已跟踪和手工忽略的命中跳过。
          </small>
        </div>
      </div>
      <div className="strategy-batch-controls">
        <label>
          <input
            type="checkbox"
            checked={!!ids.length && selectedIds.length === ids.length}
            aria-checked={
              selectedIds.length > 0 && selectedIds.length < ids.length
                ? "mixed"
                : selectedIds.length === ids.length && !!ids.length
            }
            ref={(input) => {
              if (input)
                input.indeterminate =
                  selectedIds.length > 0 && selectedIds.length < ids.length;
            }}
            disabled={!ids.length || !!pending || batchBusy || disabled}
            onChange={(event) => onSelectAll(event.target.checked)}
          />
          <CheckCheck size={15} />
          全选当前分类{" "}
          <b>
            {selectedIds.length}/{ids.length}
          </b>
        </label>
        <button
          className="btn small"
          disabled={!selectedIds.length || !!pending || batchBusy || disabled}
          onClick={() => onSelectAll(false)}
        >
          <X size={13} />
          清空选择
        </button>
        <span className="strategy-batch-control-spacer" />
        <button
          className="btn"
          disabled={disabled || batchBusy || loading || !selectedIds.length}
          onClick={() => execute("selected")}
        >
          {pending === "submit" ? <Spinner /> : <Play size={14} />}
          执行所选并跟踪（
          {selectedIds.length}）
        </button>
        <button
          className="btn primary"
          disabled={disabled || batchBusy || loading || !ids.length}
          onClick={() => execute("all")}
        >
          <Layers3 size={14} />
          一键执行并跟踪（{ids.length}）
        </button>
      </div>
      {current && (
        <div className={`strategy-batch-progress ${current.status}`}>
          <div className="strategy-batch-progress-heading">
            <strong>{batchStatusLabel(current.status)}</strong>
            <span
              className={`strategy-batch-mode ${tracking?.automatic ? "automatic" : "legacy"}`}
            >
              {tracking?.label}
            </span>
            <span>
              {current.business_date} · 已处理 {current.completed_count ?? 0}/
              {current.total_count ?? "—"} · 成功 {current.succeeded_count ?? 0}
              {Number.isSafeInteger(current.completed_steps) &&
              Number.isSafeInteger(current.total_steps) &&
              current.total_steps > 0
                ? ` · 步骤 ${current.completed_steps}/${current.total_steps}`
                : ""}
              {current.failed_count ? ` · 失败 ${current.failed_count}` : ""}
              {current.cancelled_count
                ? ` · 取消 ${current.cancelled_count}`
                : ""}
            </span>
            <b>
              {measured === null
                ? "进度待回报"
                : `${Number(measured.toFixed(1))}%`}
            </b>
          </div>
          <p
            className={`strategy-batch-tracking-summary ${tracking?.automatic ? "automatic" : "legacy"}`}
          >
            {tracking?.text}
          </p>
          <progress
            max={100}
            {...(measured === null ? {} : { value: measured })}
            aria-label="批量策略真实进度"
          />
          <div className="strategy-batch-current">
            <span>
              {current.current_strategy
                ? `当前：${current.current_strategy.name} · ${current.current_strategy.stage || batchStatusLabel(current.current_strategy.status)}${typeof current.current_strategy.progress === "number" && Number.isFinite(current.current_strategy.progress) ? ` · ${Number(current.current_strategy.progress.toFixed(1))}%` : ""}`
                : current.status === "queued"
                  ? "等待唯一工作进程领取"
                  : current.status === "paused"
                    ? "已暂停，等待手动继续"
                    : current.status === "running"
                      ? tracking?.automatic
                        ? "正在按顺序筛选并加入各自跟踪池"
                        : "正在按顺序筛选并保存结果"
                      : "每项执行结果已独立保留，可展开查看"}
            </span>
            {current.status === "running" && (
              <button
                className="btn small"
                disabled={!!pending}
                onClick={() => operate("pause")}
              >
                <Pause size={12} />
                暂停
              </button>
            )}
            {current.status === "paused" && (
              <button
                className="btn small"
                disabled={!!pending}
                onClick={() => operate("resume")}
              >
                <Play size={12} />
                继续
              </button>
            )}
            {batchActive(current) && (
              <button
                className="btn small"
                disabled={!!pending}
                onClick={() => operate("cancel")}
              >
                <Square size={11} />
                取消批次
              </button>
            )}
            <button
              className="icon-btn"
              title="读取批次状态"
              aria-label="读取批次状态"
              disabled={!!pending}
              onClick={() => void feed.refresh()}
            >
              <RefreshCw size={13} />
            </button>
          </div>
          <details>
            <summary>查看每个策略的执行结果</summary>
            <div className="strategy-batch-items">
              {list(current.items).map((item, index) => (
                <div key={item.strategy_id}>
                  <span className="strategy-batch-sequence">{index + 1}</span>
                  <div>
                    <strong>{item.name}</strong>
                    <small>
                      规则 V{item.version} ·{" "}
                      {item.stage || batchStatusLabel(item.status)}
                      {item.error ? ` · ${item.error}` : ""}
                    </small>
                    <small className="strategy-batch-item-tracking">
                      {batchItemTrackingText(
                        item,
                        tracking?.automatic === true,
                      )}
                    </small>
                  </div>
                  <span className={`strategy-batch-item-state ${item.status}`}>
                    {batchStatusLabel(item.status)}
                  </span>
                  {item.run_id && (
                    <button
                      className="btn small"
                      disabled={!!pending}
                      onClick={() => callbacks.current.onOpenRun(item)}
                    >
                      查看结果
                    </button>
                  )}
                </div>
              ))}
            </div>
          </details>
        </div>
      )}
      {batches.length > 1 && (
        <label className="strategy-batch-history">
          批次记录
          <select
            aria-label={`${label}策略批次记录`}
            value={current?.batch_id || ""}
            onChange={(event) => setSelectedBatch(event.target.value)}
          >
            {batches.map((batch) => (
              <option key={batch.batch_id} value={batch.batch_id}>
                {batch.business_date} · {batchStatusLabel(batch.status)} ·{" "}
                {batch.completed_count}/{batch.total_count} ·{" "}
                {batch.auto_add === true ? "筛选并跟踪" : "仅筛选"}
              </option>
            ))}
          </select>
        </label>
      )}
      {loading && (
        <small className="muted">正在恢复已保存的批量执行记录…</small>
      )}
      {readError && (
        <ErrorBox
          message={`批次状态暂未更新：${readError}`}
          retry={() => void feed.refresh()}
        />
      )}
      {error && <ErrorBox message={error} />}
    </section>
  );
}
