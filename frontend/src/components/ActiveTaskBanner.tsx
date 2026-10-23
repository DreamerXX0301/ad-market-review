import {
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  LoaderCircle,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { Row } from "../lib/api";
import {
  activityPresentation,
  activityClockNow,
  nextActivityNoticeDelay,
  visibleActivity,
} from "./taskActivityPresentation";
import { useTaskActivity } from "./taskActivity";
import "./ActiveTaskBanner.css";

const storageKey = "workbench-dismissed-task-notices";
function readDismissed(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(storageKey) || "[]"));
  } catch {
    return new Set();
  }
}
export default function ActiveTaskBanner({
  onNavigate,
}: {
  onNavigate: (route: string) => void;
}) {
  const activity = useTaskActivity();
  const [dismissed, setDismissed] = useState(readDismissed);
  const [expanded, setExpanded] = useState(false);
  const [clockTick, setClockTick] = useState(0);
  const now = activityClockNow(activity.clock, Date.now(), performance.now());
  useEffect(() => {
    const delay = nextActivityNoticeDelay(activity.tasks, dismissed, now);
    const timer =
      delay === null
        ? null
        : window.setTimeout(
            () => setClockTick((tick) => tick + 1),
            Math.max(
              1,
              now +
                delay -
                activityClockNow(activity.clock, Date.now(), performance.now()),
            ),
          );
    const visible = () => {
      if (!document.hidden) setClockTick((tick) => tick + 1);
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [activity.tasks, activity.clock, dismissed, clockTick]);
  const pending = activity.pending.filter(
    (task) =>
      !activity.tasks.some((known) => known.request_key === task.request_key),
  );
  const rows = [...pending, ...visibleActivity(activity.tasks, dismissed, now)];
  if (!rows.length) return null;
  function dismiss(task: Row) {
    const next = new Set([...dismissed, task.job_id].slice(-50));
    setDismissed(next);
    try {
      sessionStorage.setItem(storageKey, JSON.stringify([...next]));
    } catch {
      /* UI preference only. */
    }
  }
  return (
    <section className="active-task-banner" aria-label="当前任务与最近结果">
      {(expanded ? rows : rows.slice(0, 2)).map((task) => {
        const view = activityPresentation(task);
        return (
          <div
            className={`active-task-row ${view.tone}`}
            key={task.job_id || task.request_key}
          >
            <span className="active-task-symbol">
              {view.active ? (
                <LoaderCircle className="active-task-spinner" size={19} />
              ) : view.tone === "good" ? (
                <CheckCircle2 size={19} />
              ) : (
                <CircleAlert size={19} />
              )}
            </span>
            <div className="active-task-copy">
              <div className="active-task-heading">
                <strong>{view.title}</strong>
                <span>{view.statusLabel}</span>
                {task.target_date && <small>{task.target_date}</small>}
              </div>
              <div className="active-task-stage">
                <span>
                  {task.status === "submitting"
                    ? "正在提交请求，等待服务端确认"
                    : view.stage}
                </span>
                {view.countLabel && <span>{view.countLabel}</span>}
                <b>{view.progressLabel}</b>
              </div>
              {task.message && (
                <small className="active-task-message">{task.message}</small>
              )}
              {view.active && (
                <progress
                  aria-label={`${view.title}进度`}
                  max={100}
                  {...(view.progress === null ? {} : { value: view.progress })}
                />
              )}
            </div>
            <button
              className="active-task-details"
              onClick={() => onNavigate("data")}
              aria-label={`查看${view.title}任务记录`}
            >
              <span>任务记录</span>
              <ExternalLink size={14} />
            </button>
            {!view.active && (
              <button
                className="icon-btn"
                onClick={() => dismiss(task)}
                aria-label="收起任务结果"
              >
                <X size={15} />
              </button>
            )}
          </div>
        );
      })}
      {rows.length > 2 && (
        <button
          className="active-task-more"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起" : `展开其余 ${rows.length - 2} 项任务`}
        </button>
      )}
      {activity.error && (
        <small className="active-task-read-error">
          任务状态暂未更新：{activity.error}。保留上次状态，稍后重试读取。
        </small>
      )}
    </section>
  );
}
