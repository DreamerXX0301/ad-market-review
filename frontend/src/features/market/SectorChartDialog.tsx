import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { CandleChart } from "../../components/Charts";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Spinner,
  Warning,
} from "../../components/UI";
import {
  sectorChartRequest,
  sectorTaskMatches,
  sectorTaskProgress,
} from "./sectorChartPresentation";

export default function SectorChartDialog({
  topic,
  date,
  onClose,
  onMembers,
}: {
  topic: { topicCode: string; name: string; proxy: boolean; notice: string };
  date: string;
  onClose: () => void;
  onMembers?: () => void;
}) {
  const endpoint = sectorChartRequest(topic.topicCode, date);
  const chart = useApi<Row>(endpoint ? endpoint + "&period=D" : null);
  const [taskId, setTaskId] = useState("");
  const [taskSnapshot, setTaskSnapshot] = useState<Row | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const task = useApi<Row>(
    taskId ? `/tasks/${encodeURIComponent(taskId)}` : null,
  );
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    const saved = chart.data?.task;
    if (!sectorTaskMatches(saved, topic.topicCode, date)) return;
    setTaskSnapshot(saved);
    if (sectorTaskProgress(saved).active)
      setTaskId(saved.task_id || saved.job_id);
  }, [chart.data, topic.topicCode, date]);
  useEffect(() => {
    if (!taskId) return;
    if (task.data && !sectorTaskMatches(task.data, topic.topicCode, date)) {
      setTaskId("");
      setTaskSnapshot(null);
      setError("任务返回的板块或日期与本次选择不一致，已停止读取该任务。");
      return;
    }
    if (task.data && sectorTaskMatches(task.data, topic.topicCode, date)) {
      setTaskSnapshot(task.data);
      const view = sectorTaskProgress(task.data);
      if (!view.active) {
        setTaskId("");
        if (view.complete) chart.reload();
        return;
      }
    }
    if (!task.loading) {
      const timer = window.setTimeout(task.reload, task.error ? 3000 : 1500);
      return () => window.clearTimeout(timer);
    }
  }, [taskId, task.data, task.error, task.loading, topic.topicCode, date]);
  async function loadHistory() {
    if (!endpoint || submitting || taskId) return;
    setError("");
    setSubmitting(true);
    try {
      const result = await post<Row>("/market/sector-chart/jobs", {
        topic_code: topic.topicCode,
        date,
        request_key: requestKey(),
      });
      if (!mounted.current) return;
      if (result.cache_hit === true) chart.reload();
      else if (sectorTaskMatches(result, topic.topicCode, date)) {
        setTaskSnapshot(result);
        const view = sectorTaskProgress(result);
        if (view.active) setTaskId(result.task_id || result.job_id);
        else if (view.complete) chart.reload();
      } else
        setError("任务返回的板块或日期与本次选择不一致，已停止展示该任务。");
    } catch (reason) {
      if (mounted.current)
        setError(
          reason instanceof Error ? reason.message : "未能提交板块历史任务",
        );
    } finally {
      if (mounted.current) setSubmitting(false);
    }
  }
  const progress = taskSnapshot
    ? sectorTaskProgress(taskSnapshot)
    : submitting
      ? sectorTaskProgress({ status: "submitting" })
      : null;
  const available =
    chart.data?.available === true &&
    Array.isArray(chart.data?.bars) &&
    chart.data.bars.length > 0;
  const busy = submitting || !!taskId || progress?.active;
  const paused = progress?.status === "paused";
  return (
    <Modal title={`${topic.name} · 板块K线`} onClose={onClose} wide>
      <div className="modal-body sector-chart-dialog-body entry-page-scroll">
        {onMembers && (
          <button className="btn small entry-members-link" onClick={onMembers}>
            查看成分股
          </button>
        )}
        <div className="sector-chart-context">
          <div>
            <Badge>{date} · 观察日期</Badge>
            <span>{topic.topicCode}</span>
            <span>同花顺板块指数 · 原始指数点位</span>
          </div>
          <button
            className="icon-btn"
            aria-label="刷新板块历史缓存状态"
            onClick={chart.reload}
          >
            <RefreshCw size={15} />
          </button>
        </div>
        {topic.proxy ? (
          <Warning>{topic.notice}</Warning>
        ) : (
          <p className="muted sector-chart-scope">
            {topic.notice}。仅加载本板块，不加载全部板块。
          </p>
        )}
        {error && <ErrorBox message={error} />}
        {chart.error && <ErrorBox message={chart.error} retry={chart.reload} />}
        {progress && (
          <div className="sector-chart-task" role="status" aria-live="polite">
            <div className="sector-chart-task-heading">
              <strong>
                {progress.active && <Spinner />}
                {progress.stage}
              </strong>
              <Badge tone={progress.tone}>
                {progress.statusLabel} · {progress.progressLabel}
              </Badge>
            </div>
            <div
              className={
                "sector-chart-meter " +
                (progress.progress === null ? "indeterminate" : "")
              }
              role="progressbar"
              aria-label="板块历史加载进度"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress.progress ?? undefined}
              aria-valuetext={progress.progressLabel + "，" + progress.stage}
            >
              <i
                style={
                  progress.progress === null
                    ? undefined
                    : { width: progress.progress + "%" }
                }
              />
            </div>
            <p>{progress.message}</p>
            {task.error && (
              <small>
                状态读取暂时失败：{task.error}。已保留上次进度，将自动重试。{" "}
                <button className="link" onClick={task.reload}>
                  立即重试
                </button>
              </small>
            )}
            {taskSnapshot && taskSnapshot.logs?.length > 0 && (
              <details>
                <summary>执行记录 · {taskSnapshot.logs.length} 条</summary>
                <ol>
                  {taskSnapshot.logs.map((record: Row, index: number) => (
                    <li key={record.seq ?? index}>
                      <span>
                        {
                          sectorTaskProgress({
                            status: taskSnapshot.status,
                            stage: record.stage,
                          }).stage
                        }
                      </span>
                      <span>{record.message}</span>
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </div>
        )}
        {chart.loading && !chart.data ? (
          <Loading />
        ) : available && endpoint ? (
          <CandleChart
            key={topic.topicCode + date}
            instrumentId={topic.topicCode}
            instrumentKind="index"
            asOf={date}
            dataEndpoint={endpoint}
            initialChart={{ endpoint, data: chart.data! }}
            height={680}
            entryFocus
          />
        ) : (
          !chart.error && (
            <div className="sector-chart-empty">
              <Empty
                title={
                  busy
                    ? "正在加载这个板块的历史K线"
                    : "该观察日尚无已核验的板块历史K线"
                }
                description={
                  chart.data?.unavailable_reason ||
                  "手动加载近五年历史后，可查看日K、周K、月K及技术指标；实际范围以来源返回并核验的历史为准。"
                }
              />
              {!busy && !paused && (
                <button
                  className="btn primary"
                  onClick={loadHistory}
                  disabled={!endpoint}
                >
                  {progress?.status === "failed" ||
                  progress?.status === "cancelled" ||
                  progress?.status === "canceled"
                    ? "重新加载板块历史K线"
                    : "加载板块历史K线"}
                </button>
              )}
              {paused && (
                <p className="muted">
                  该任务已暂停，请在数据中心手动继续；这里不会自动恢复。
                </p>
              )}
            </div>
          )
        )}
      </div>
    </Modal>
  );
}
