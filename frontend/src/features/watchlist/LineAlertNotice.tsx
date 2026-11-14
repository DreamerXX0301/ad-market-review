import { useEffect, useRef, useState } from "react";
import { BellRing, X } from "lucide-react";
import { list, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import { useToast } from "../../components/UI";
import {
  AUTOMATIC_PRICE_ALERTS_CHANGED,
  LINE_ALERT_CHECK_FINISHED,
} from "../../lib/workbenchEvents";
import {
  LINE_ALERTS_OPEN,
  currentLineAlertSummary,
} from "./lineAlertNotification";
import { lineAlertSource, lineAlertView } from "./lineAlertPresentation";
import "./LineAlertNotice.css";

export function currentIntradayNoticeSummary(
  value: Row | null,
  today?: string,
): Row | null {
  const serverClock = value?.server_now_ms;
  const serverStamp =
    typeof serverClock === "number" &&
    Number.isSafeInteger(serverClock) &&
    serverClock > 0
      ? new Date(serverClock)
      : null;
  const serverDay =
    serverStamp && Number.isFinite(serverStamp.valueOf())
      ? serverStamp.toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" })
      : undefined;
  const expectedDay = serverDay || today;
  const dayTime = expectedDay
    ? Date.parse(`${expectedDay}T00:00:00Z`)
    : Number.NaN;
  if (
    !value ||
    (Object.hasOwn(value, "server_now_ms") && !serverDay) ||
    !expectedDay ||
    (serverDay && today && serverDay < today) ||
    value.date !== expectedDay ||
    !/^\d{4}-\d{2}-\d{2}$/.test(expectedDay) ||
    !Number.isFinite(dayTime) ||
    new Date(dayTime).toISOString().slice(0, 10) !== expectedDay ||
    typeof value.enabled !== "boolean" ||
    !Number.isSafeInteger(value.total) ||
    value.total < 0 ||
    !Number.isSafeInteger(value.unread_count) ||
    value.unread_count < 0 ||
    value.unread_count > value.total ||
    !Array.isArray(value.items) ||
    value.items.length > 5 ||
    value.items.length > value.total ||
    value.items.some(
      (event: Row) =>
        !event ||
        typeof event.event_id !== "string" ||
        !event.event_id ||
        event.trade_date !== expectedDay ||
        typeof event.instrument_id !== "string" ||
        !event.instrument_id ||
        !Array.isArray(event.sources),
    )
  )
    return null;
  return value;
}

export const intradayNoticeTime = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) && value > 0
    ? new Date(value).toLocaleTimeString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "—";

export default function LineAlertNotice({
  businessDate,
  today,
  onNavigate,
}: {
  businessDate?: string;
  today?: string;
  onNavigate: (route: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tabChoice, setTabChoice] = useState<"intraday" | "daily" | null>(null);
  const [visible, setVisible] = useState(
    () =>
      typeof document === "undefined" || document.visibilityState === "visible",
  );
  const [arrivalId, setArrivalId] = useState(0);
  const [arrivalAccent, setArrivalAccent] = useState(false);
  const accentedArrival = useRef(0);
  const active = usePageActive();
  const toast = useToast();
  const seen = useRef({
    date: "",
    ids: new Set<string>(),
    needsBaseline: true,
    lastSummary: null as Row | null,
    pausedSummary: null as Row | null,
  });
  const root = useRef<HTMLDivElement>(null);
  const result = useApi<Row>("/watchlist/line-alerts/summary", [businessDate]);
  const intraday = useApi<Row>(
    active && visible ? "/intraday-monitor/summary" : null,
    [today],
  );
  useEffect(() => {
    const visibility = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", visibility);
    return () => document.removeEventListener("visibilitychange", visibility);
  }, []);
  useEffect(() => {
    if (!active || !visible) return;
    const refresh = () => {
      if (document.visibilityState === "visible") intraday.reload();
    };
    const timer = window.setInterval(refresh, 30_000);
    window.addEventListener("intraday-monitor-changed", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("intraday-monitor-changed", refresh);
    };
  }, [active, visible, intraday.reload]);
  useEffect(() => {
    const refresh = () => result.reload();
    window.addEventListener(LINE_ALERT_CHECK_FINISHED, refresh);
    window.addEventListener("chart-annotations-changed", refresh);
    window.addEventListener(AUTOMATIC_PRICE_ALERTS_CHANGED, refresh);
    return () => {
      window.removeEventListener(LINE_ALERT_CHECK_FINISHED, refresh);
      window.removeEventListener("chart-annotations-changed", refresh);
      window.removeEventListener(AUTOMATIC_PRICE_ALERTS_CHANGED, refresh);
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
  const liveSummary = currentIntradayNoticeSummary(intraday.data, today);
  const liveReady = !!liveSummary && !intraday.error;
  useEffect(() => {
    const state = seen.current;
    if (!active || !visible || document.visibilityState !== "visible") {
      state.needsBaseline = true;
      state.pausedSummary = state.lastSummary;
      return;
    }
    if (!liveReady || !liveSummary) return;
    if (state.needsBaseline && liveSummary === state.pausedSummary) return;
    const identifiers: string[] = liveSummary.items.map(
      (event: Row) => event.event_id,
    );
    if (state.needsBaseline || state.date !== liveSummary.date) {
      if (state.date !== liveSummary.date) state.ids = new Set<string>();
      state.date = liveSummary.date;
      identifiers.forEach((id) => state.ids.add(id));
      state.needsBaseline = false;
      state.pausedSummary = null;
      state.lastSummary = liveSummary;
      return;
    }
    const fresh = identifiers.filter((id) => !state.ids.has(id));
    identifiers.forEach((id) => state.ids.add(id));
    state.lastSummary = liveSummary;
    if (fresh.length) {
      setArrivalId((value) => value + 1);
      toast(`新增盘中提醒 ${fresh.length} 条，点击铃铛查看。`);
    }
  }, [active, visible, liveReady, liveSummary, toast]);
  const hasUnread = liveReady && liveSummary.unread_count > 0;
  const canAnimate = hasUnread && !open && active && visible;
  useEffect(() => {
    if (!canAnimate) {
      accentedArrival.current = arrivalId;
      setArrivalAccent(false);
      return;
    }
    if (accentedArrival.current === arrivalId) return;
    accentedArrival.current = arrivalId;
    setArrivalAccent(true);
    const timeout = window.setTimeout(() => setArrivalAccent(false), 6_000);
    return () => window.clearTimeout(timeout);
  }, [arrivalId, canAnimate]);
  const tab =
    tabChoice || (hasUnread || liveSummary?.enabled ? "intraday" : "daily");
  const total = summary?.total;
  const liveLabel = intraday.error
    ? "盘中未读读取失败"
    : !liveSummary
      ? "盘中未读状态待确认"
      : `盘中未读 ${liveSummary.unread_count} 条（${liveSummary.date} · ${liveSummary.enabled ? "已开启" : "未开启"}）`;
  const dailyLabel = result.error
    ? "盘后提醒读取失败"
    : !summary
      ? "盘后提醒状态待确认"
      : `盘后行情提醒 ${total} 条（业务日 ${summary.business_date}）`;
  const noticeLabel = `${liveLabel}；${dailyLabel}`;
  const primaryCount = liveReady
    ? hasUnread || liveSummary.enabled
      ? liveSummary.unread_count
      : total
    : undefined;
  const badgeError =
    !!intraday.error ||
    (!hasUnread && liveReady && !liveSummary.enabled && !!result.error);
  const badgePending =
    !liveReady || (!hasUnread && !liveSummary.enabled && !summary);
  const badge = badgeError
    ? "!"
    : badgePending
      ? intraday.loading || result.loading
        ? "…"
        : "?"
      : primaryCount > 0
        ? primaryCount > 99
          ? "99+"
          : primaryCount
        : null;
  return (
    <div className="line-alert-notice" ref={root}>
      <button
        className={`icon-btn line-alert-notice-trigger${hasUnread ? " has-unread" : !badgeError && !badgePending && primaryCount > 0 ? " has-daily" : ""}${canAnimate ? " can-animate" : ""}${canAnimate && arrivalAccent ? " has-new-arrival" : ""}`}
        onClick={() => {
          setOpen(!open);
          if (!open) {
            result.reload();
            if (active && visible) intraday.reload();
          }
        }}
        aria-label={noticeLabel}
        aria-expanded={open}
        aria-controls="today-line-alerts-popover"
        title={noticeLabel}
      >
        <span
          className="line-alert-notice-icon"
          key={arrivalId}
          aria-hidden="true"
        >
          <BellRing size={20} />
        </span>
        {hasUnread ? (
          <span className="line-alert-notice-label" aria-hidden="true">
            未读
          </span>
        ) : !badgeError && !badgePending && primaryCount > 0 ? (
          <span className="line-alert-notice-label" aria-hidden="true">
            盘后
          </span>
        ) : null}
        {badge !== null && (
          <b
            className={`line-alert-notice-badge${badgeError ? " is-error" : badgePending ? " is-pending" : hasUnread ? " is-unread" : " is-daily"}`}
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
          aria-label="盘中与盘后行情提醒"
        >
          <header>
            <strong>
              行情提醒
              <small>
                盘中 {liveSummary?.date || today || "日期待确认"} · 盘后{" "}
                {summary?.business_date || "业务日待确认"}
              </small>
            </strong>
            <button
              className="icon-btn"
              aria-label="关闭提醒预览"
              onClick={() => setOpen(false)}
            >
              <X size={16} />
            </button>
          </header>
          <div
            className="line-alert-notice-tabs"
            role="tablist"
            aria-label="提醒时段"
          >
            <button
              type="button"
              role="tab"
              aria-selected={tab === "intraday"}
              onClick={() => setTabChoice("intraday")}
            >
              盘中{" "}
              <small>{liveReady ? liveSummary.unread_count : "—"} 未读</small>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "daily"}
              onClick={() => setTabChoice("daily")}
            >
              盘后 <small>{summary && !result.error ? total : "—"} 条</small>
            </button>
          </div>
          {tab === "intraday" ? (
            <>
              <p>
                {liveReady
                  ? `${liveSummary.enabled ? "监控已开启" : "监控未开启"} · 今日共 ${liveSummary.total} 条，未读 ${liveSummary.unread_count} 条。`
                  : "盘中摘要尚未核验，当前未读数量未知。"}
              </p>
              {intraday.error ? (
                <p className="line-alert-notice-error">
                  盘中提醒暂未读取，请稍后重新打开；盘后结果单独保留。
                </p>
              ) : !liveSummary && intraday.loading ? (
                <p>正在读取盘中提醒…</p>
              ) : !liveSummary ? (
                <p>尚无当前日期的有效盘中摘要，请重新读取。</p>
              ) : liveSummary.total === 0 ? (
                <p>
                  {liveSummary.enabled
                    ? "今天还没有已保存的盘中提醒；请结合采样状态查看。"
                    : "盘中监控尚未开启，可在盘中监控页设置。"}
                </p>
              ) : liveSummary.items.length === 0 ? (
                <p>数量已读取，近期提醒明细尚未返回。</p>
              ) : (
                <ul
                  className="line-alert-notice-scroll line-alert-notice-intraday-list"
                  aria-label="近期盘中提醒，可在列表内滚动"
                  tabIndex={0}
                >
                  {liveSummary.items.map((event: Row) => (
                    <li key={event.event_id}>
                      <div>
                        <strong
                          title={`${event.name || event.code || event.instrument_id} ${event.code || ""}`}
                        >
                          {event.name || event.code || event.instrument_id}
                          <small>{event.code}</small>
                        </strong>
                        <b title={event.title || "盘中提醒"}>
                          {event.title || "盘中提醒"}
                        </b>
                      </div>
                      <small
                        title={`首次采样 ${intradayNoticeTime(event.first_seen_at_ms)} · 最近采样 ${intradayNoticeTime(event.last_seen_at_ms)} · ${event.sources.length} 项依据`}
                      >
                        首次采样 {intradayNoticeTime(event.first_seen_at_ms)} ·
                        最近采样 {intradayNoticeTime(event.last_seen_at_ms)} ·{" "}
                        {event.sources.length} 项依据
                      </small>
                    </li>
                  ))}
                </ul>
              )}
              <div className="line-alert-notice-actions">
                <button
                  type="button"
                  className="btn primary small"
                  onClick={() => {
                    setOpen(false);
                    onNavigate("monitor");
                  }}
                >
                  查看盘中监控
                </button>
              </div>
              {liveReady && liveSummary.items.length > 0 && (
                <small className="line-alert-notice-range">
                  最近 {liveSummary.items.length} 条摘要 ·
                  列表内滚动，全部记录见监控页。
                </small>
              )}
              <small>
                {liveReady
                  ? liveSummary.notice ||
                    "每10分钟采样，与盘后收盘确认分别保存。"
                  : "仅展示服务端当前日期的有效摘要，不用旧记录填零。"}
              </small>
            </>
          ) : (
            <>
              <p>
                按最新已发布业务日统计，不是电脑日期。共 {total ?? "—"} 条。
              </p>
              {result.error ? (
                <p className="line-alert-notice-error">
                  提醒暂未读取，可稍后重新打开。上方如有旧数量，仅为上次结果。
                </p>
              ) : !summary && result.loading ? (
                <p>正在读取提醒…</p>
              ) : !summary ? (
                <p>尚无当前业务日的提醒摘要；行情发布后可重新查看。</p>
              ) : total === 0 ? (
                <p>今日暂无已确认的行情提醒。</p>
              ) : (
                <ul
                  className="line-alert-notice-scroll"
                  aria-label="近期盘后提醒，可在列表内滚动"
                  tabIndex={0}
                >
                  {list(summary).map((event) => (
                    <li key={event.event_id}>
                      <span>
                        {event.name || event.code}
                        <small>
                          {event.code} · {lineAlertSource(event)}
                        </small>
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
            </>
          )}
        </section>
      )}
    </div>
  );
}
