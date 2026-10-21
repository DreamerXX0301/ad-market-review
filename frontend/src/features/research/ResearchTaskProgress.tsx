import type { Row } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { Badge, Panel, Spinner } from "../../components/UI";
import { researchTaskProgress } from "./researchProgress";

export default function ResearchTaskProgress({
  task,
  pollError,
  onRetry,
}: {
  task: Row;
  pollError?: string;
  onRetry?: () => void;
}) {
  const view = researchTaskProgress(task);
  return (
    <Panel
      title="研究任务"
      className="research-task-panel"
      actions={<Badge tone={view.tone}>{view.statusLabel}</Badge>}
    >
      <div className="research-task-body">
        <div className="research-task-heading" role="status" aria-live="polite">
          <div>
            {view.active && <Spinner />}
            <strong>{view.stage}</strong>
          </div>
          <span>{view.progressLabel}</span>
        </div>
        <div
          className={
            "research-task-meter " +
            (view.progress === null ? "indeterminate" : "")
          }
          role="progressbar"
          aria-label="个股研究阶段进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={view.progress ?? undefined}
          aria-valuetext={view.progressLabel + "，" + view.stage}
        >
          <i
            style={
              view.progress === null
                ? undefined
                : { width: view.progress + "%" }
            }
          />
        </div>
        <p className={view.status === "failed" ? "research-task-error" : ""}>
          {view.message}
        </p>
        {view.active && (
          <small>
            百分比来自任务阶段回报，不代表材料完整率或预计剩余时间。
          </small>
        )}
        {pollError && (
          <div className="research-task-poll-error" role="status">
            <span>
              任务状态读取暂时失败：{pollError}。已保留上次进度，将继续重试。
            </span>
            {onRetry && (
              <button className="link" onClick={onRetry}>
                立即重试
              </button>
            )}
          </div>
        )}
        {!!view.logs.length && (
          <details className="research-task-records">
            <summary>执行记录 · {view.logs.length} 条</summary>
            <ol>
              {view.logs.map((event) => (
                <li key={event.key}>
                  <time>{dateTime(event.time)}</time>
                  <strong>{event.stage}</strong>
                  <span>{event.message}</span>
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
    </Panel>
  );
}
