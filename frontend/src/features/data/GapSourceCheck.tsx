import { useEffect, useRef, useState } from "react";
import { CloudDownload, RefreshCw } from "lucide-react";
import { post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime, integer } from "../../lib/format";
import { ErrorBox, Spinner } from "../../components/UI";
import {
  finishTaskRequest,
  startTaskRequest,
  useTaskActivity,
} from "../../components/taskActivity";
import { activityPresentation } from "../../components/taskActivityPresentation";

const operation = "quote_gap_source_check";
const related = (task: Row) =>
  task.operation === operation || task.parameters?.operation === operation;
const activeStatus = (task: Row) =>
  ["queued", "running", "paused", "submitting"].includes(task.status);

export default function GapSourceCheck({
  disabled,
  remoteConfigured,
  onTask,
  onChanged,
  planKey,
}: {
  disabled: boolean;
  remoteConfigured: boolean;
  onTask: (task: Row) => void;
  onChanged: () => void;
  planKey?: string;
}) {
  const [active, setActive] = useState(false),
    [pending, setPending] = useState(false),
    [error, setError] = useState(""),
    [submitted, setSubmitted] = useState<Row | null>(null);
  const result = useApi<Row>(active ? "/data/quote-gap-source" : null);
  const activity = useTaskActivity();
  const callbacks = useRef({ onTask, onChanged });
  const previousPlan = useRef(planKey);
  callbacks.current = { onTask, onChanged };
  const request = useRef<{ plan: string; key: string } | null>(null),
    inFlight = useRef(false),
    seen = useRef(new Set<string>());
  const known = activity.tasks.find(
    (task) => task.job_id === submitted?.job_id,
  );
  const savedJob = useApi<Row>(
    active && submitted?.job_id && !known
      ? `/tasks/${encodeURIComponent(submitted.job_id)}`
      : null,
  );
  const ownTask = known || savedJob.data || submitted;
  const current =
    [...activity.pending, ...activity.tasks].find(
      (task) => related(task) && activeStatus(task),
    ) || (ownTask && activeStatus(ownTask) ? ownTask : null);
  const taskView = current ? activityPresentation(current) : null;
  const data = result.data,
    evidence = data?.evidence;
  useEffect(() => {
    if (previousPlan.current !== planKey) {
      previousPlan.current = planKey;
      if (active) result.reload();
    }
  }, [active, planKey, result.reload]);
  useEffect(() => {
    if (active && submitted?.job_id && !known && savedJob.data)
      savedJob.reload();
  }, [active, activity.tasks, submitted?.job_id, savedJob.reload]);
  useEffect(() => {
    const visible = () =>
      setActive(
        !document.hidden && location.hash.slice(1).split("?")[0] === "data",
      );
    visible();
    window.addEventListener("hashchange", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.removeEventListener("hashchange", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, []);
  useEffect(() => {
    if (!active) return;
    let changed = false;
    for (const task of [
      ...activity.tasks,
      ...(savedJob.data ? [savedJob.data] : []),
    ]) {
      if (!related(task)) continue;
      if (request.current?.key === task.request_key) {
        request.current = null;
        setError("");
      }
      if (activeStatus(task)) continue;
      const key = `${task.job_id}:${task.finished_at_ms || task.status}`;
      if (!seen.current.has(key)) {
        seen.current.add(key);
        changed = true;
      }
    }
    if (changed) {
      result.reload();
      callbacks.current.onChanged();
    }
  }, [active, activity.tasks, savedJob.data, result.reload]);
  const submit = async () => {
    if (
      inFlight.current ||
      disabled ||
      !remoteConfigured ||
      current ||
      data?.available !== true ||
      !data.plan_id
    )
      return;
    const previous = request.current;
    const key =
      previous && previous.plan === data.plan_id ? previous.key : requestKey();
    request.current = { plan: data.plan_id, key };
    inFlight.current = true;
    setPending(true);
    setError("");
    startTaskRequest({
      request_key: key,
      kind: "source_check",
      operation,
      title: "向来源核验报价缺口",
    });
    try {
      const task = await post<Row>("/data/quote-gap-source", {
        plan_id: data.plan_id,
        request_key: key,
      });
      setSubmitted(task);
      request.current = null;
      callbacks.current.onTask(task);
    } catch (problem: any) {
      if (request.current?.key === key) setError(problem.message);
    } finally {
      inFlight.current = false;
      setPending(false);
      finishTaskRequest(key);
    }
  };
  return (
    <section className="coverage-source-check" aria-label="向来源核验报价缺口">
      <div className="coverage-section-heading">
        <div>
          <h3>向来源核验报价缺口</h3>
          <p>
            显式联网比对 {integer(data?.stock_count)} 只股票、
            {integer(data?.missing_sessions)}{" "}
            个无报价交易日；只核验并留证，不改写已发布行情。
          </p>
        </div>
        <button
          className="btn small primary"
          disabled={
            disabled ||
            pending ||
            !!current ||
            !remoteConfigured ||
            data?.available !== true
          }
          onClick={() => void submit()}
        >
          {pending ? <Spinner /> : <CloudDownload size={14} />}{" "}
          {pending
            ? "提交中…"
            : current
              ? taskView?.statusLabel || "核验进行中"
              : "向来源核验"}
        </button>
        <button
          className="icon-btn"
          aria-label="刷新来源缺口核验"
          onClick={result.reload}
        >
          <RefreshCw size={14} />
        </button>
      </div>
      {!remoteConfigured && (
        <p className="muted">需先配置可用的行情来源，再显式发起联网核验。</p>
      )}
      {data?.available === false && (
        <p className="muted">
          {data.reason || "请先完成当前代际的本地核验，确认完整缺口范围。"}
        </p>
      )}
      {data?.stale === true && (
        <p className="coverage-plan-caveat">
          来源核验属于旧行情或旧缺口计划，未用于当前结论；可按当前范围重新核验。
        </p>
      )}
      {taskView && (
        <div className="coverage-source-task" role="status">
          <strong>{taskView.statusLabel}</strong>
          <span>
            {taskView.stage} · {taskView.progressLabel}
            {taskView.countLabel ? ` · ${taskView.countLabel}` : ""}
          </span>
          <button
            className="link"
            onClick={() => callbacks.current.onTask(current!)}
          >
            查看任务进度
          </button>
        </div>
      )}
      {evidence && (
        <>
          <div className="coverage-diagnosis-stats coverage-source-stats">
            <div>
              <span>本次股票核验</span>
              <strong>
                {integer(evidence.checked_stocks)}
                <small> / {integer(evidence.planned_stocks)}</small>
              </strong>
            </div>
            <div>
              <span>已核验缺口</span>
              <strong>
                {integer(evidence.checked_sessions)}
                <small>
                  {" "}
                  /{" "}
                  {integer(
                    evidence.total_gap_sessions ?? data?.missing_sessions,
                  )}{" "}
                  个交易日
                </small>
              </strong>
            </div>
            <div>
              <span>来源有可补报价</span>
              <strong>
                {integer(evidence.fillable_sessions)}
                <small>个交易日</small>
              </strong>
            </div>
            <div>
              <span>来源同样无报价</span>
              <strong>
                {integer(evidence.source_absent_sessions)}
                <small>个交易日</small>
              </strong>
            </div>
          </div>
          <p className="muted">
            全部缺口涉及{" "}
            {integer(evidence.gap_stock_count ?? data?.stock_count)}{" "}
            只股票；本次计划 {integer(evidence.planned_stocks)} 只。 核验于{" "}
            {dateTime(evidence.checked_at_ms)} · 失败股票{" "}
            {integer(evidence.failed_stocks)} 只 · 重叠报价差异{" "}
            {integer(evidence.overlap_difference_sessions)}{" "}
            日。来源无报价不等于停牌证明；有可补报价仍需单独核验并发布新代际。
          </p>
        </>
      )}
      {(error || result.error) && (
        <ErrorBox message={error || result.error} retry={result.reload} />
      )}
    </section>
  );
}
