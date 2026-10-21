type Row = Record<string, any>;
export const lineRuleLabels: Record<string, string> = {
  support_reclaim: "回踩收回",
  support_breakdown: "收盘失守",
  pressure_close_breakout: "收盘突破",
  pressure_touch: "盘中触碰",
};
export function lineAlertView(event: Row) {
  const invalidated =
    event.state === "invalidated" || event.rule === "support_breakdown";
  return {
    title: lineRuleLabels[event.rule] || event.title || "划线提醒",
    invalidated,
    tone: invalidated
      ? "invalidated"
      : event.rule === "pressure_touch"
        ? "touch"
        : "triggered",
    stateLabel: invalidated ? "支撑失守" : "已触发",
  };
}
export function lineAlertInScope(event: Row | null, asOf?: string) {
  return !!(
    event?.event_id &&
    event.instrument_id &&
    typeof event.trade_date === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(event.trade_date) &&
    (!asOf || event.trade_date <= asOf)
  );
}
export function filterLineAlerts(
  events: Row[],
  query: string,
  state: string,
  asOf?: string,
) {
  const term = query.trim().toLocaleLowerCase();
  return events.filter(
    (event) =>
      lineAlertInScope(event, asOf) &&
      (state === "all" ||
        (state === "invalidated") === lineAlertView(event).invalidated) &&
      (!term ||
        [
          event.name,
          event.code,
          event.instrument_id,
          event.title,
          event.reason,
          lineAlertView(event).title,
        ]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase()
          .includes(term)),
  );
}
export function lineAlertWatchlistState(event: Row) {
  const labels: Record<string, string> = {
    added: "触发后已加入自选",
    already_present: "已在自选 · 保留原基准",
    disabled: "未启用自动加入自选",
    capacity_reached: "自选已达上限 · 未加入",
    not_eligible: "失守风险提醒 · 不自动入池",
    missing_current_quote: "当前收盘待核验 · 尚未加入",
  };
  return labels[event.watchlist_add_state] || "自动入池结果待确认";
}
export function lineAlertBasis(event: Row) {
  return event.price_basis === "none"
    ? "未复权原价"
    : event.price_basis === "forward_event_affine"
      ? `前复权价 · 锚定 ${event.anchor_date || "待核验"}`
      : "价格口径待核验";
}
export function lineCheckBusy(task: Row | undefined | null) {
  return (
    !!task &&
    ["queued", "running", "submitting", "paused"].includes(task.status)
  );
}
export function lineAlertPrice(event: Row): number | null {
  const value = Object.hasOwn(event, "comparison_line_price")
    ? event.comparison_line_price
    : event.line_price;
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}
export function isLineCheck(task: Row) {
  return (
    task.kind === "line_alert_check" ||
    task.operation === "line_alert_check" ||
    task.parameters?.operation === "line_alert_check"
  );
}

/** GET notifications are scoped to their captured day; no read can launch evaluation. */
export function lineCheckRefreshKeys(tasks: Row[]) {
  return tasks
    .filter(
      (task) =>
        isLineCheck(task) &&
        ["succeeded", "partial", "no_change"].includes(task.status),
    )
    .map((task) => `${task.job_id}:${task.finished_at_ms || task.status}`);
}
