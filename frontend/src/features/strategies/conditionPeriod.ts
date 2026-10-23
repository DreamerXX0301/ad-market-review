type Field = Record<string, any>;
const fixedDailyFields = new Set([
  "limit_up",
  "prev_limit_up",
  "limit_up_1y",
  "limit_up_count_1y",
  "close_breakout_high",
  "intraday_high_change_pct",
  "close_above_entry_high",
  "close_below_entry_low",
  "close_below_entry",
  "trading_age",
  "tracking_change_pct",
]);
export function periodLabel(period: string): string {
  return (
    ({ D: "日K", W: "完整周K", M: "完整月K" } as Record<string, string>)[
      period
    ] || period
  );
}
export function conditionPeriodView(
  node: Field,
  field: Field,
  defaultPeriod = "D",
  dailyContext = false,
) {
  const fixed =
    dailyContext ||
    node.right_event_field ||
    fixedDailyFields.has(node.field) ||
    fixedDailyFields.has(node.right_field)
      ? "D"
      : field.fixed_period;
  const identity =
    field.type === "topic" ||
    ["board", "is_st", "topic_ids"].includes(node.field);
  return {
    fixed,
    identity,
    effective: fixed || node.period || defaultPeriod,
    explicit: node.period != null,
  };
}
/** Changing the picker is the only operation that adds an explicit override. */
export function withConditionPeriod(node: Field, value: string): Field {
  const next = { ...node };
  if (value === "inherit") delete next.period;
  else next.period = value;
  return next;
}
export function conditionPeriodSummary(node: Field): string {
  return node.period && !node.right_event_field
    ? `${periodLabel(node.period)} · `
    : "";
}
export function metricCycleLabel(
  field: string,
  fallback: string,
  period: string,
): string {
  const cycle = period === "M" ? "月" : period === "W" ? "周" : "日";
  if (field === "volume_ratio")
    return cycle === "日"
      ? "较前一交易日成交量倍数"
      : `较前一完整${cycle}成交量倍数`;
  if (field === "change_pct")
    return cycle === "日" ? "当日涨跌幅 (%)" : `完整${cycle}涨跌幅 (%)`;
  const cross = /^cross_above_ma(\d+)$/.exec(field);
  if (cross)
    return `${cycle === "日" ? "今日" : `当${cycle}`}收盘上穿 MA${cross[1]}`;
  if (field === "volume_expand")
    return cycle === "日"
      ? "成交量大于前一交易日（放量）"
      : `成交量大于前一完整${cycle}（放量）`;
  if (field === "volume_shrink")
    return cycle === "日"
      ? "成交量小于前一交易日（缩量）"
      : `成交量小于前一完整${cycle}（缩量）`;
  return fallback;
}
