import { useEffect, useRef, useState } from "react";
import { BellRing, X } from "lucide-react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { LINE_ALERT_CHECK_FINISHED } from "../../lib/workbenchEvents";
import {
  LINE_ALERTS_OPEN,
  currentLineAlertSummary,
} from "./lineAlertNotification";
import { lineAlertView } from "./lineAlertPresentation";
import "./LineAlertNotice.css";

export default function LineAlertNotice({
  businessDate,
  onNavigate,
}: {
  businessDate?: string;
  onNavigate: (route: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [visible, setVisible] = useState(
    () => typeof document === "undefined" || !document.hidden,
  );
  const root = useRef<HTMLDivElement>(null);
  const result = useApi<Row>(
    visible ? "/watchlist/line-alerts/summary" : null,
    [businessDate],
  );
  useEffect(() => {
    const refresh = () => {
      if (!document.hidden) result.reload();
    };
    const visibility = () => {
      setVisible(!document.hidden);
      if (!document.hidden) refresh();
    };
    window.addEventListener(LINE_ALERT_CHECK_FINISHED, refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener(LINE_ALERT_CHECK_FINISHED, refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [result.reload]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        root.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const goToToday = () => {
    setOpen(false);
    onNavigate("watchlist");
    window.location.hash = "watchlist?alerts=today";
    window.dispatchEvent(new Event(LINE_ALERTS_OPEN));
  };
  const summary = currentLineAlertSummary(result.data, businessDate);
  const total = summary?.total;
  const noticeLabel = result.error
    ? "提醒读取失败"
    : result.loading && !summary
      ? "正在读取提醒"
      : !summary
        ? "提醒状态待确认"
        : `今日划线提醒 ${total} 条`;
  const badge = result.error
    ? "!"
    : !summary
      ? result.loading
        ? "…"
        : "?"
      : total > 0
        ? total > 99
          ? "99+"
          : total
        : null;
  return (
    <div className="line-alert-notice" ref={root}>
      <button
        className={`icon-btn line-alert-notice-trigger${total > 0 ? " has-alerts" : ""}`}
        onClick={() => {
          setOpen(!open);
          if (!open) result.reload();
        }}
        aria-label={noticeLabel}
        aria-expanded={open}
        aria-controls="today-line-alerts-popover"
        title={noticeLabel}
      >
        <BellRing size={18} />
        {badge !== null && (
          <b
            className={result.error ? "is-error" : !summary ? "is-pending" : ""}
            aria-live="polite"
          >
            {badge}
          </b>
        )}
      </button>
      {open && (
        <section
          id="today-line-alerts-popover"
          className="line-alert-notice-popover"
          aria-label="今日划线提醒"
        >
          <header>
            <strong>
              今日划线提醒{" "}
              <small>{summary?.business_date || "业务日待确认"}</small>
            </strong>
            <button
              className="icon-btn"
              aria-label="关闭提醒预览"
              onClick={() => setOpen(false)}
            >
              <X size={16} />
            </button>
          </header>
          <p>按最新已发布业务日统计，不是电脑日期。共 {total ?? "—"} 条。</p>
          {result.error ? (
            <p className="line-alert-notice-error">
              提醒暂未读取，可稍后重新打开。上方如有旧数量，仅为上次结果。
            </p>
          ) : !summary && result.loading ? (
            <p>正在读取提醒…</p>
          ) : !summary ? (
            <p>尚无当前业务日的提醒摘要；行情发布后可重新查看。</p>
          ) : total === 0 ? (
            <p>今日暂无已确认的划线触发。</p>
          ) : (
            <ul>
              {list(summary).map((event) => (
                <li key={event.event_id}>
                  <span>
                    {event.name || event.code}
                    <small>{event.code}</small>
                  </span>
                  <b className={lineAlertView(event).tone}>
                    {lineAlertView(event).title}
                  </b>
                </li>
              ))}
            </ul>
          )}
          <div className="line-alert-notice-actions">
            <button className="btn primary small" onClick={goToToday}>
              查看今日提醒
            </button>
          </div>
          <small>仅显示已核验记录；历史补录按实际事件日归档。</small>
        </section>
      )}
    </div>
  );
}
