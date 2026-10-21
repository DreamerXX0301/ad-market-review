export type RuleNode = Record<string, any>;
export const HIDDEN_CONDITION_TEXT = "已隐藏的指标条件（保留原配置）";
export const isHiddenConditionField = (field: unknown): boolean =>
  field === "kdj_j";
export function conditionReasonText(reason: unknown): string {
  if (typeof reason !== "string") return "";
  return /\bkdj_j\b|\bKDJ[\s_-]*J\b|\bJ\s*[值线]/i.test(reason)
    ? "部分指标条件已隐藏"
    : reason;
}
export function isHiddenConditionRule(node: RuleNode | undefined): boolean {
  return (
    !!node &&
    [node.field, node.right_field, node.right_event_field].some(
      isHiddenConditionField,
    )
  );
}
/** Inspect without rewriting saved conditions or changing their execution. */
export function hasHiddenConditionRule(node: unknown): boolean {
  if (Array.isArray(node)) return node.some(hasHiddenConditionRule);
  if (!node || typeof node !== "object") return false;
  const rule = node as RuleNode;
  return (
    isHiddenConditionRule(rule) ||
    Object.values(rule).some(
      (value) =>
        value !== null &&
        typeof value === "object" &&
        hasHiddenConditionRule(value),
    )
  );
}
export const historyFieldsFallback = new Set([
  "open",
  "high",
  "low",
  "close",
  "volume",
  "amount",
  "change_pct",
  "intraday_high_change_pct",
  "volume_ratio",
  "limit_up",
  "macd_dif",
  "macd_dea",
  "macd_hist",
  "kdj_k",
  "kdj_d",
  "macd_cross",
  "macd_dead_cross",
  "kd_cross",
  "kd_dead_cross",
  "close_breakout_high",
  "bullish_ma_alignment",
  ...[5, 10, 20, 30, 60, 250].flatMap((n) => ["ma" + n, "cross_above_ma" + n]),
]);
export function normalizeRuleTree(node?: RuleNode): RuleNode {
  if (!node || !Object.keys(node).length) return { all: [] };
  if (node.history_event)
    return {
      ...node,
      history_event: {
        ...node.history_event,
        where: normalizeRuleTree(node.history_event.where),
        then: normalizeRuleTree(node.history_event.then),
      },
    };
  if (node.conditions)
    return {
      [node.logic === "OR" ? "any" : "all"]:
        node.conditions.map(normalizeRuleTree),
    };
  if (node.all || node.any) {
    const key = node.any ? "any" : "all";
    return { ...node, [key]: node[key].map(normalizeRuleTree) };
  }
  return { ...node };
}
export function newHistoryRule(): RuleNode {
  return {
    history_event: {
      window: { from: 1, to: 30 },
      match: "latest",
      where: {
        all: [{ field: "volume_ratio", op: ">=", value: 3, period: "D" }],
      },
      then: {
        all: [{ field: "change_pct", op: ">", value: 0, period: "D" }],
      },
    },
  };
}
export type HistoryWindowMode = "recent" | "range" | "exact";
export function historyWindowMode(window: any): HistoryWindowMode {
  return window?.from === window?.to
    ? "exact"
    : window?.from === 1
      ? "recent"
      : "range";
}
export function historyWindowForMode(window: any, mode: HistoryWindowMode) {
  const from =
    Number.isInteger(window?.from) && window.from >= 1 ? window.from : 1;
  const to = Number.isInteger(window?.to) && window.to >= from ? window.to : 30;
  return mode === "exact"
    ? { from: to, to }
    : mode === "recent"
      ? { from: 1, to }
      : { from, to };
}
export function historyEventPreset(
  where: RuleNode,
): "volume" | "limit" | "custom" {
  const leaves = where?.all || [where];
  if (leaves.length !== 1) return "custom";
  const leaf = leaves[0];
  if (
    leaf?.field === "volume_ratio" &&
    leaf.op === ">=" &&
    typeof leaf.value === "number"
  )
    return "volume";
  if (leaf?.field === "limit_up" && leaf.op === "==" && leaf.value === true)
    return "limit";
  return "custom";
}
export function historyPresetWhere(preset: "volume" | "limit"): RuleNode {
  return {
    all: [
      preset === "limit"
        ? { field: "limit_up", op: "==", value: true, period: "D" }
        : { field: "volume_ratio", op: ">=", value: 3, period: "D" },
    ],
  };
}
export function appendTodayCondition(then: RuleNode, leaf: RuleNode): RuleNode {
  return {
    all: [
      ...(Array.isArray(then?.all)
        ? then.all
        : then?.field || then?.any
          ? [then]
          : []),
      { ...leaf, period: "D" },
    ],
  };
}
export function historyWindowText(window: any): string {
  const from = window?.from ?? "?",
    to = window?.to ?? "?";
  return from === to ? `恰好${from}个交易日前` : `距今${from}—${to}个交易日`;
}
export function ruleCount(node: RuleNode | undefined): number {
  if (!node) return 0;
  if (node.field || node.history_event) return 1;
  return (node.all || node.any || node.conditions || []).reduce(
    (sum: number, child: RuleNode) => sum + ruleCount(child),
    0,
  );
}
export function historyRuleSummary(
  node: RuleNode,
  label: (field: string) => string = (field) => field,
): string {
  if (isHiddenConditionRule(node)) return HIDDEN_CONDITION_TEXT;
  if (node.history_event) {
    const event = node.history_event;
    return `${historyWindowText(event.window)}${event.window?.from === event.window?.to ? "" : "内"}（不含今日），选择${event.match === "earliest" ? "最早" : "最近"}一次满足「${historyRuleSummary(event.where, (field) => (field === "limit_up" ? "事件日收盘涨停" : label(field)))}」的事件；同时满足今日条件「${historyRuleSummary(event.then, label)}」`;
  }
  const group = node.all || node.any || node.conditions;
  if (Array.isArray(group))
    return group.length
      ? "（" +
          group
            .map((child) => historyRuleSummary(child, label))
            .join(node.any || node.logic === "OR" ? " 或 " : " 且 ") +
          "）"
      : "";
  if (!node.field) return "未识别规则（保留原结构）";
  const boards: Record<string, string> = {
    main: "主板",
    chinext: "创业板",
    star: "科创板",
    beijing: "北交所",
  };
  const value = node.right_event_field
    ? `事件日${label(node.right_event_field)}`
    : node.right_field
      ? label(node.right_field)
      : typeof node.value === "boolean"
        ? node.value
          ? "是"
          : "否"
        : Array.isArray(node.value)
          ? node.value
              .map((item: string) =>
                node.field === "board" ? boards[item] || item : item,
              )
              .join("、")
          : String(node.value ?? "未填写");
  const operators: Record<string, string> = {
    new_high: "回看",
    "==": "等于",
    "!=": "不等于",
    in: "仅包含",
    not_in: "剔除",
    contains: "包含",
    not_contains: "不包含",
  };
  const cycle =
    node.period && !node.right_event_field
      ? `${({ D: "日K", W: "完整周K", M: "完整月K" } as Record<string, string>)[node.period] || node.period} · `
      : "";
  return `${cycle}${node.right_event_field ? "观察日收盘价（日K）" : label(node.field)} ${operators[node.op] || node.op} ${value}${node.field === "close_breakout_high" ? "个交易日" : ""}`;
}
export function historyCheckText(check: RuleNode): string {
  const state =
    check.result === true
      ? "满足"
      : check.result === false
        ? "未满足"
        : "待核验";
  const event = check.selected_event;
  return `历史事件（${historyWindowText(check.window)}）：${event?.date ? `${event.date}${Number.isInteger(event.offset) ? ` · ${event.offset}个交易日前` : ""}；当前条件${state}` : state}${check.reason ? "；" + conditionReasonText(check.reason) : ""}`;
}
export function validateVisualRule(
  node: RuleNode,
  context: "current" | "where" | "then" = "current",
): string | null {
  if (node.history_event) {
    if (context !== "current") return "历史事件内不能再次嵌套历史事件";
    const { window, match, where, then } = node.history_event;
    if (
      ![window?.from, window?.to].every(
        (value) => Number.isInteger(value) && value >= 1 && value <= 750,
      ) ||
      window.from > window.to
    )
      return "历史范围请输入1—750的整数，最近距离不能大于最远距离";
    if (!["latest", "earliest"].includes(match))
      return "请选择历史事件的匹配顺序";
    return (
      validateVisualRule(where || {}, "where") ||
      validateVisualRule(then || {}, "then")
    );
  }
  const group = node.all || node.any || node.conditions;
  if (Array.isArray(group)) {
    if (!group.length) return "请为每个条件组添加至少一项条件";
    for (const child of group) {
      const error = validateVisualRule(child, context);
      if (error) return error;
    }
    return null;
  }
  if (!node.field) return "未识别的规则请在结构化编辑中核对";
  if (node.right_event_field && context !== "then")
    return "事件价格只能用于历史事件的当前条件";
  if (node.right_event_field && node.field !== "close")
    return "事件价格比较须使用观察日收盘价（日K）";
  if (
    node.right_event_field &&
    !["close", "high", "low"].includes(node.right_event_field)
  )
    return "事件比较价格只支持收盘价、最高价或最低价";
  if (
    [
      node.value !== undefined,
      !!node.right_field,
      !!node.right_event_field,
    ].filter(Boolean).length !== 1
  )
    return "每个条件只能选择一种比较对象";
  if (node.value === "") return "请填写条件数值";
  return null;
}
