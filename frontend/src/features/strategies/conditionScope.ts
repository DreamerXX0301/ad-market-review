import type { Row } from "../../lib/api";

export type ConditionScope =
  "selection" | "alert" | "exit" | "trade-entry" | "trade-exit";
export type ConditionFieldGroup = { label: string; fields: Row[] };

export function trackingConditionAllowed(
  field: string,
  allowTrackingAlerts = false,
  allowTrackingConditions = false,
): boolean {
  if (field === "close_above_entry_high") return allowTrackingAlerts;
  if (field === "close_below_entry_low") return allowTrackingConditions;
  return true;
}

export function conditionFieldGroup(field: Row, scope: ConditionScope): string {
  if (scope === "selection")
    return (
      field.category ||
      (field.type === "boolean"
        ? "事件信号"
        : field.type === "topic"
          ? "题材归属"
          : "数值比较")
    );
  const name = String(field.field || "");
  const entry = scope === "alert" || scope === "trade-entry";
  const upward =
    [
      "macd_cross",
      "kd_cross",
      "close_breakout_high",
      "close_above_entry_high",
      "bullish_ma_alignment",
    ].includes(name) || name.startsWith("cross_above_ma");
  const downward =
    [
      "macd_dead_cross",
      "kd_dead_cross",
      "close_below_entry",
      "close_below_entry_low",
    ].includes(name) || name.startsWith("close_below_ma");
  if (entry ? upward : downward)
    return entry ? "推荐 · 上行与突破" : "推荐 · 死叉与跌破";
  if (upward || downward) return "更多跨场景条件";
  return field.type === "number"
    ? "共用 · 数值比较"
    : field.type === "topic"
      ? "共用 · 题材归属"
      : "共用 · 其他状态";
}

/** Saved opposite-direction signals stay selectable even while more fields are collapsed. */
export function conditionFieldGroups(
  fields: Row[],
  scope: ConditionScope,
  saved: string[],
  showMore: boolean,
): ConditionFieldGroup[] {
  const grouped = new Map<string, Row[]>();
  const order =
    scope === "selection"
      ? []
      : [
          scope === "alert" || scope === "trade-entry"
            ? "推荐 · 上行与突破"
            : "推荐 · 死叉与跌破",
          "共用 · 数值比较",
          "共用 · 题材归属",
          "共用 · 其他状态",
          "已保存 · 跨场景条件",
          "更多跨场景条件",
        ];
  for (const field of fields) {
    if (field.field === "kdj_j") continue;
    let group = conditionFieldGroup(field, scope);
    if (group === "更多跨场景条件" && !showMore) {
      if (!saved.includes(field.field)) continue;
      group = "已保存 · 跨场景条件";
    }
    grouped.set(group, [...(grouped.get(group) || []), field]);
  }
  return [...grouped]
    .map(([label, values]) => ({ label, fields: values }))
    .sort((a, b) =>
      scope === "selection"
        ? 0
        : order.indexOf(a.label) - order.indexOf(b.label),
    );
}
