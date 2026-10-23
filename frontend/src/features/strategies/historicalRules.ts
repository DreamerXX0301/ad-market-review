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
  "volume_expand",
  "volume_shrink",
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
):
  | "volume"
  | "limit"
  | "change"
  | "any-signal"
  | "macd-cross"
  | "kdj-cross"
  | "custom" {
  if (sameRule(where, historyPresetWhere("any-signal"))) return "any-signal";
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
  if (
    leaf?.field === "change_pct" &&
    leaf.op === ">" &&
    typeof leaf.value === "number"
  )
    return "change";
  if (pureGoldenCross(where) === "macd") return "macd-cross";
  if (pureGoldenCross(where) === "kdj") return "kdj-cross";
  return "custom";
}
export function historyPresetWhere(
  preset: "volume" | "limit" | "change" | "any-signal",
): RuleNode {
  if (preset === "any-signal")
    return {
      any: [
        { field: "volume_ratio", op: ">=", value: 3, period: "D" },
        { field: "limit_up", op: "==", value: true, period: "D" },
        { field: "change_pct", op: ">", value: 5, period: "D" },
      ],
    };
  return {
    all: [
      preset === "limit"
        ? { field: "limit_up", op: "==", value: true, period: "D" }
        : preset === "change"
          ? { field: "change_pct", op: ">", value: 5, period: "D" }
          : { field: "volume_ratio", op: ">=", value: 3, period: "D" },
    ],
  };
}
function sameRule(a: any, b: any): boolean {
  if (a === b) return true;
  if (
    !a ||
    !b ||
    typeof a !== "object" ||
    typeof b !== "object" ||
    Array.isArray(a) !== Array.isArray(b)
  )
    return false;
  const keys = Object.keys(a);
  return (
    keys.length === Object.keys(b).length &&
    keys.every((key) => Object.hasOwn(b, key) && sameRule(a[key], b[key]))
  );
}
export function appendTodayRule(
  then: RuleNode | undefined,
  rule: RuleNode,
): RuleNode {
  if (
    sameRule(then, rule) ||
    then?.all?.some((child: RuleNode) => sameRule(child, rule))
  )
    return then!;
  return {
    all: [
      ...(Array.isArray(then?.all)
        ? then.all
        : then?.field || then?.any
          ? [then]
          : []),
      rule,
    ],
  };
}
export function appendTodayCrossAlternative(event: RuleNode): RuleNode {
  return {
    ...event,
    then: appendTodayRule(event.then, {
      any: [
        { field: "macd_cross", op: "==", value: true, period: "D" },
        { field: "kd_cross", op: "==", value: true, period: "D" },
      ],
    }),
  };
}
export function pureGoldenCross(
  where: RuleNode | undefined,
): "macd" | "kdj" | null {
  if (
    Array.isArray(where?.all) &&
    Object.keys(where!).some((key) => key !== "all")
  )
    return null;
  const leaf =
    Array.isArray(where?.all) && where.all.length === 1 ? where.all[0] : where;
  if (
    !leaf ||
    leaf.op !== "==" ||
    leaf.value !== true ||
    leaf.period !== "D" ||
    Object.keys(leaf).some(
      (key) => !["field", "op", "value", "period"].includes(key),
    )
  )
    return null;
  return leaf.field === "macd_cross"
    ? "macd"
    : leaf.field === "kd_cross"
      ? "kdj"
      : null;
}
export function previousCrossConfigurationError(
  event: RuleNode,
): string | null {
  const cycle = event.price_path?.cross_cycle;
  if (!cycle) return null;
  if (
    event.window?.from !== 1 ||
    event.match !== "latest" ||
    pureGoldenCross(event.where) !== cycle.indicator
  )
    return "上次金叉过程须从昨日回看、选择最近一次，历史条件只保留同类日K金叉；请使用上次金叉模板，或先关闭金叉周期过程再编辑普通事件";
  return null;
}
export function applyPreviousCrossPreset(
  event: RuleNode,
  indicator: "macd" | "kdj",
): RuleNode {
  const cross = {
    field: indicator === "macd" ? "macd_cross" : "kd_cross",
    op: "==",
    value: true,
    period: "D",
  };
  const then = appendTodayRule(appendTodayRule(event.then, cross), {
    field: "close",
    op: ">",
    right_event_field: "close",
    period: "D",
  });
  return {
    ...event,
    window: { from: 1, to: 60 },
    match: "latest",
    where: { all: [cross] },
    then,
    price_path: {
      ...event.price_path,
      cross_cycle: { indicator, require_dead_cross: true },
    },
  };
}
export function newPreviousCrossRule(indicator: "macd" | "kdj"): RuleNode {
  return {
    history_event: applyPreviousCrossPreset(
      {
        window: { from: 1, to: 60 },
        match: "latest",
        where: { all: [] },
        then: { all: [] },
      },
      indicator,
    ),
  };
}
export type ComparisonObject = "value" | "field" | "event" | "event-percentage";
export function conditionComparisonObject(
  condition: RuleNode,
): ComparisonObject {
  return condition.right_event_field
    ? Object.hasOwn(condition, "right_event_change_pct")
      ? "event-percentage"
      : "event"
    : condition.right_field
      ? "field"
      : "value";
}
export function withConditionComparison(
  condition: RuleNode,
  kind: ComparisonObject,
): RuleNode {
  const next = { ...condition };
  delete next.right_event_field;
  delete next.right_event_change_pct;
  if (kind === "event" || kind === "event-percentage") {
    delete next.value;
    delete next.right_field;
    next.field = "close";
    next.period = "D";
    next.right_event_field = ["close", "high", "low"].includes(
      condition.right_event_field,
    )
      ? condition.right_event_field
      : "close";
    if (kind === "event-percentage")
      next.right_event_change_pct = Object.hasOwn(
        condition,
        "right_event_change_pct",
      )
        ? condition.right_event_change_pct
        : 0;
  } else if (kind === "field") {
    delete next.value;
    next.right_field = condition.right_field || "ma30";
  } else {
    delete next.right_field;
    next.value = condition.value ?? 3;
  }
  return next;
}
export function eventPriceOffsetError(value: unknown): string | null {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value > -100 &&
    value <= 10000
    ? null
    : "事件价偏移请输入大于−100且不超过10000的有限百分比";
}
export function eventPriceTargetText(
  node: RuleNode,
  label: (field: string) => string = (field) =>
    historyEventPriceLabels[field] || field,
): string {
  const base = `事件日${label(node.right_event_field)}`;
  if (!Object.hasOwn(node, "right_event_change_pct")) return base;
  const pct = node.right_event_change_pct;
  return `${base} × (1 ${typeof pct === "number" && pct < 0 ? "−" : "+"} ${typeof pct === "number" && Number.isFinite(pct) ? Math.abs(pct) : "未填写"}/100)`;
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
export const historyReclaimModes = [
  { value: "close_at_or_above", label: "今日收盘不低于事件价（持续状态）" },
  { value: "close_reclaim", label: "今日重新收复（昨收低于、今收不低于）" },
  { value: "first_close_reclaim", label: "事件后首次重新收复（只看价格首次）" },
  { value: "high_at_or_above", label: "今日最高价达到或超过事件价" },
  { value: "touch", label: "今日实际触及事件价（最低≤事件价≤最高）" },
];
export const historyEventPriceLabels: Record<string, string> = {
  close: "收盘价",
  high: "最高价",
  low: "最低价",
};
export function defaultHistoryGuard(): RuleNode {
  return { max_drawdown_pct: 5, require_pullback: true };
}
export function defaultHistoryReclaim(): RuleNode {
  return { event_field: "close", mode: "close_reclaim" };
}
/** Only an explicit edit adds the optional path; disabling its last part removes it. */
export function withHistoryPricePart(
  event: RuleNode,
  part: "guard" | "reclaim" | "cross_cycle",
  value?: RuleNode,
): RuleNode {
  const next = { ...event },
    path = { ...event.price_path };
  if (value === undefined) delete path[part];
  else path[part] = value;
  if (Object.keys(path).length) next.price_path = path;
  else delete next.price_path;
  return next;
}
export function applyHistoryPricePreset(
  event: RuleNode,
  preset: "guard-rebound" | "reclaim",
): RuleNode {
  if (preset === "reclaim")
    return withHistoryPricePart(event, "reclaim", {
      ...defaultHistoryReclaim(),
      ...event.price_path?.reclaim,
      mode: "close_reclaim",
    });
  const next = withHistoryPricePart(event, "guard", {
    ...defaultHistoryGuard(),
    ...event.price_path?.guard,
    require_pullback: true,
  });
  const gain = { field: "change_pct", op: ">=", value: 2, period: "D" };
  const isGain = (leaf: RuleNode) =>
    leaf.field === gain.field &&
    leaf.op === gain.op &&
    leaf.value === gain.value &&
    leaf.period === "D";
  if (!(isGain(event.then || {}) || event.then?.all?.some(isGain)))
    next.then = appendTodayCondition(event.then, gain);
  return next;
}
export function historyPricePathSummary(path?: RuleNode): string {
  if (!path) return "";
  const parts: string[] = [];
  if (path.guard) {
    const guard = path.guard;
    parts.push(
      `事件后至今日每次收盘≥保护价（事件收盘×(1−${guard.max_drawdown_pct ?? "?"}%)）`,
    );
    if (guard.require_pullback) parts.push("事件后至昨日曾收盘低于事件收盘");
    if (guard.near_floor) {
      const near = guard.near_floor;
      parts.push(
        `${near.sessions === 1 ? "昨日" : `截至昨日近${near.sessions ?? "?"}个交易日内`}曾收盘落在保护价至其上方${near.band_pct ?? "?"}%、且低于事件收盘`,
      );
    }
  }
  if (path.reclaim) {
    const reclaim = path.reclaim;
    const mode =
      historyReclaimModes.find((item) => item.value === reclaim.mode)?.label ||
      "收复方式待核验";
    parts.push(
      `${mode}；参照事件日${historyEventPriceLabels[reclaim.event_field] || "价格待选择"}`,
    );
  }
  if (path.cross_cycle)
    parts.push(
      `${path.cross_cycle.indicator === "macd" ? "MACD" : "KDJ"}金叉→中间死叉→今日再金叉；仅核验周期，价格高低另看今日条件`,
    );
  return parts.length ? `价格过程（日K）：${parts.join("；且")}` : "";
}
export function validateHistoryPricePath(path: unknown): string | null {
  if (path === undefined) return null;
  if (!path || typeof path !== "object" || Array.isArray(path))
    return "价格过程需要有效配置";
  const rule = path as RuleNode;
  if (
    !Object.keys(rule).length ||
    Object.keys(rule).some(
      (key) => !["guard", "reclaim", "cross_cycle"].includes(key),
    )
  )
    return "价格过程请选择守位、事件价收复或金叉周期";
  const finiteRange = (
    value: unknown,
    min: number,
    max: number,
    inclusiveMax = true,
  ) =>
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= min &&
    (inclusiveMax ? value <= max : value < max);
  if ("guard" in rule) {
    const guard = rule.guard;
    if (
      !guard ||
      typeof guard !== "object" ||
      Array.isArray(guard) ||
      Object.keys(guard).some(
        (key) =>
          !["max_drawdown_pct", "require_pullback", "near_floor"].includes(key),
      )
    )
      return "保护价配置无效";
    if (!finiteRange(guard.max_drawdown_pct, 0, 100, false))
      return "允许回撤请输入0至小于100的百分比";
    if (
      guard.require_pullback !== undefined &&
      typeof guard.require_pullback !== "boolean"
    )
      return "请选择是否要求实际回落";
    if (guard.near_floor !== undefined) {
      const near = guard.near_floor;
      if (
        !near ||
        typeof near !== "object" ||
        Array.isArray(near) ||
        Object.keys(near).some((key) => !["sessions", "band_pct"].includes(key))
      )
        return "近保护价配置无效";
      if (
        !Number.isInteger(near.sessions) ||
        near.sessions < 1 ||
        near.sessions > 750
      )
        return "回踩范围请输入1—750个市场交易日的整数";
      if (!finiteRange(near.band_pct, 0, 100))
        return "保护价上方区间请输入0—100的百分比";
    }
  }
  if ("reclaim" in rule) {
    const reclaim = rule.reclaim;
    if (
      !reclaim ||
      typeof reclaim !== "object" ||
      Array.isArray(reclaim) ||
      Object.keys(reclaim).some((key) => !["event_field", "mode"].includes(key))
    )
      return "事件价收复配置无效";
    if (!Object.hasOwn(historyEventPriceLabels, reclaim.event_field))
      return "请选择事件日收盘、最高或最低价";
    if (!historyReclaimModes.some((item) => item.value === reclaim.mode))
      return "请选择有效的事件价收复方式";
  }
  if ("cross_cycle" in rule) {
    const cycle = rule.cross_cycle;
    if (
      !cycle ||
      typeof cycle !== "object" ||
      Array.isArray(cycle) ||
      Object.keys(cycle).some(
        (key) => !["indicator", "require_dead_cross"].includes(key),
      ) ||
      !["macd", "kdj"].includes(cycle.indicator) ||
      cycle.require_dead_cross !== true
    )
      return "金叉周期请选择MACD或KDJ，并要求中间确实发生死叉";
  }
  return null;
}
export function historyWindowText(window: any): string {
  const from = window?.from ?? "?",
    to = window?.to ?? "?";
  return from === to ? `恰好${from}个交易日前` : `距今${from}—${to}个交易日`;
}
export const historyMatchOptions = [
  {
    value: "latest",
    label: "选择最近一次",
    description:
      "先按历史条件绑定最近一次事件，再检查该事件的价格过程与今日条件；整体不满足时不会改选另一天。更近日期待核验时保留未知。",
  },
  {
    value: "earliest",
    label: "选择最早一次",
    description:
      "先按历史条件绑定最早一次事件，再检查该事件的价格过程与今日条件；整体不满足时不会改选另一天。更早日期待核验时保留未知。",
  },
  {
    value: "any",
    label: "范围内任意一次满足",
    description:
      "从近到远逐个检查事件的历史条件、价格过程与今日条件；有一次整体满足即可，绑定从近到远找到的第一个已确认整体满足事件。全部条件使用同一事件，不跨日期拼接。没有已满足事件，但仍有事件无法判断整体是否满足时，结果为待核验。",
  },
] as const;
export function historyMatchDescription(match: unknown): string {
  return (
    historyMatchOptions.find((item) => item.value === match)?.description ||
    "请选择有效的历史事件匹配方式"
  );
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
    if (event.match === "any")
      return `${historyWindowText(event.window)}${event.window?.from === event.window?.to ? "" : "内"}（不含今日），范围内任意一次满足：历史条件「${historyRuleSummary(event.where, (field) => (field === "limit_up" ? "事件日收盘涨停" : label(field)))}」${event.price_path ? `；${historyPricePathSummary(event.price_path)}` : ""}；同时满足今日条件「${historyRuleSummary(event.then, label)}」；从近到远逐次完整核验，绑定从近到远找到的第一个已确认整体满足事件，全部条件使用同一事件`;
    return `${historyWindowText(event.window)}${event.window?.from === event.window?.to ? "" : "内"}（不含今日），选择${event.match === "earliest" ? "最早" : "最近"}一次满足「${historyRuleSummary(event.where, (field) => (field === "limit_up" ? "事件日收盘涨停" : label(field)))}」的事件${event.price_path ? `；${historyPricePathSummary(event.price_path)}` : ""}；同时满足今日条件「${historyRuleSummary(event.then, label)}」`;
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
  if (node.field === HIGHER_LOWS_FIELD) return higherLowsSummary(node);
  if (node.field === "boll_signal") return bollSummary(node);
  const boards: Record<string, string> = {
    main: "主板",
    chinext: "创业板",
    star: "科创板",
    beijing: "北交所",
  };
  const value = node.right_event_field
    ? eventPriceTargetText(node, label)
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
  const anyMatch = check.history_event?.match === "any";
  const state =
    check.result === true
      ? "满足"
      : check.result === false
        ? "未满足"
        : "待核验";
  const event = anyMatch && check.result !== true ? null : check.selected_event;
  const path = check.price_path_checks;
  const pathText = path
    ? `；价格过程${path.result === true ? "满足" : path.result === false ? "未满足" : "待核验"}`
    : "";
  return `历史事件（${historyWindowText(check.window)}${anyMatch ? "；范围内任意一次" : ""}）：${event?.date ? `${event.date}${Number.isInteger(event.offset) ? ` · ${event.offset}个交易日前` : ""}${anyMatch ? " · 最近一次已确认整体满足" : ""}；${path || anyMatch ? "整体条件" : "当前条件"}${state}` : state}${pathText}${check.reason ? "；" + conditionReasonText(check.reason) : ""}`;
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
    if (!historyMatchOptions.some((item) => item.value === match))
      return "请选择历史事件的匹配方式";
    return (
      validateHistoryPricePath(node.history_event.price_path) ||
      previousCrossConfigurationError(node.history_event) ||
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
  const bollError = validateBollRule(node, context);
  if (bollError) return bollError;
  const higherLowsError = validateHigherLowsRule(node, context);
  if (higherLowsError) return higherLowsError;
  if (Object.hasOwn(node, "right_event_change_pct")) {
    if (!node.right_event_field || context !== "then")
      return "事件价百分比偏移仅用于历史事件的今日事件价比较";
    const offsetError = eventPriceOffsetError(node.right_event_change_pct);
    if (offsetError) return offsetError;
  }
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

export const HIGHER_LOWS_FIELD = "higher_lows";
export const HIGHER_LOWS_OPERATOR = "confirmed_rising_lows";
export const HIGHER_LOWS_DEFAULT = 60;
export const HIGHER_LOWS_MIN = 8;
export const HIGHER_LOWS_MAX = 750;

export function higherLowsWindowError(value: unknown): string | null {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value >= HIGHER_LOWS_MIN &&
    value <= HIGHER_LOWS_MAX
    ? null
    : "底部抬高回看窗口请输入8—750的整数";
}

export function higherLowsWindowUnit(period?: string): string {
  return period === "D"
    ? "个交易日"
    : period === "W"
      ? "根完整周K"
      : period === "M"
        ? "根完整月K"
        : "个默认周期";
}

export function higherLowsSummary(
  node: RuleNode,
  effectivePeriod?: string,
): string {
  const period = node.period || effectivePeriod;
  return `${period ? (({ D: "日K", W: "完整周K", M: "完整月K" } as Record<string, string>)[period] || period) + " · " : ""}底部抬高（回看${node.value ?? "N"}${higherLowsWindowUnit(period)}，左右各2根确认，确认后收盘未失守）`;
}

export function validateHigherLowsRule(node: RuleNode, context = "current") {
  if (node.field !== HIGHER_LOWS_FIELD) return null;
  if (context === "where")
    return "底部抬高请放在外层独立条件或今日条件，不作为历史事件日筛选条件";
  if (
    node.op !== HIGHER_LOWS_OPERATOR ||
    node.right_field ||
    node.right_event_field
  )
    return "底部抬高使用独立结构判断，只接受回看窗口参数";
  return higherLowsWindowError(node.value);
}

export const BOLL_MODES = [
  {
    value: "squeeze",
    label: "带宽收口",
    description: "当期带宽不高于历史分位阈值",
  },
  {
    value: "breakout_up",
    label: "收盘向上突破上轨",
    description: "前期收盘不高于上轨，当期收盘严格高于上轨",
  },
  {
    value: "breakout_down",
    label: "收盘向下跌破下轨",
    description: "前期收盘不低于下轨，当期收盘严格低于下轨",
  },
  {
    value: "reentry_from_above",
    label: "从上轨外回到带内",
    description: "前期收盘在上轨外，当期收盘回到上下轨之间（含边界）",
  },
  {
    value: "reentry_from_below",
    label: "从下轨外回到带内",
    description: "前期收盘在下轨外，当期收盘回到上下轨之间（含边界）",
  },
] as const;
export const BOLL_DEFAULTS = {
  mode: "breakout_up",
  band_period: 20,
  deviations: 2,
  rank_window: 125,
  squeeze_percentile: 10,
  recent_squeeze_periods: 5,
  require_squeeze: true,
  middle_direction: "any",
  slope_periods: 5,
  require_expansion: false,
};
export type BollConfig = Record<string, any>;
/** Supply missing defaults without changing the saved rule. Invalid input remains visible for repair. */
export function bollConfig(value: unknown): BollConfig {
  return {
    ...BOLL_DEFAULTS,
    ...(value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {}),
  };
}
export function bollConfigError(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return "请填写 BOLL 参数对象";
  const supplied = value as BollConfig;
  if (Object.keys(supplied).some((key) => !Object.hasOwn(BOLL_DEFAULTS, key)))
    return "BOLL 参数包含不支持的字段，请在结构化编辑中核对";
  const config = bollConfig(value);
  if (!BOLL_MODES.some((mode) => mode.value === config.mode))
    return "请选择有效的 BOLL 信号";
  for (const [key, label, min, max] of [
    ["band_period", "BOLL 周期", 2, 250],
    ["rank_window", "分位回看窗口", 10, 750],
    ["recent_squeeze_periods", "近期收口窗口", 1, 60],
    ["slope_periods", "中轨方向比较间隔", 1, 60],
  ] as const) {
    const number = config[key];
    if (
      typeof number !== "number" ||
      !Number.isInteger(number) ||
      number < min ||
      number > max
    )
      return `${label}请输入${min}—${max}的整数`;
  }
  if (
    typeof config.deviations !== "number" ||
    !Number.isFinite(config.deviations) ||
    config.deviations < 0.1 ||
    config.deviations > 10
  )
    return "标准差倍数请输入0.1—10的有限数值";
  if (
    typeof config.squeeze_percentile !== "number" ||
    !Number.isFinite(config.squeeze_percentile) ||
    config.squeeze_percentile < 1 ||
    config.squeeze_percentile > 100
  )
    return "收口分位请输入1—100的有限百分比";
  if (!["any", "rising", "falling"].includes(config.middle_direction))
    return "请选择有效的中轨方向";
  if (
    typeof config.require_squeeze !== "boolean" ||
    typeof config.require_expansion !== "boolean"
  )
    return "BOLL 收口与扩张开关须为是或否";
  return null;
}
export function validateBollRule(
  node: RuleNode,
  context = "current",
): string | null {
  if (node.field !== "boll_signal") return null;
  if (context === "where")
    return "BOLL 请放在外层独立条件或今日条件，不作为历史事件日筛选条件";
  if (
    node.op !== "matches" ||
    node.right_field ||
    node.right_event_field ||
    Object.hasOwn(node, "right_event_change_pct")
  )
    return "BOLL 使用信号参数，不支持字段或事件价比较";
  return bollConfigError(node.value);
}
export function bollSummary(node: RuleNode, effectivePeriod?: string): string {
  const config = bollConfig(node.value);
  const period = node.period || effectivePeriod;
  const cycle = period
    ? `${({ D: "日K", W: "完整周K", M: "完整月K" } as Record<string, string>)[period] || period} · `
    : "";
  const mode =
    BOLL_MODES.find((item) => item.value === config.mode)?.label ||
    "未识别信号";
  const filters = [];
  if (config.mode === "squeeze")
    filters.push(
      `带宽≤前${config.rank_window}期的${config.squeeze_percentile}%分位`,
    );
  else if (config.require_squeeze)
    filters.push(
      `此前${config.recent_squeeze_periods}期曾收口（前${config.rank_window}期的${config.squeeze_percentile}%分位，不含信号当期）`,
    );
  if (config.middle_direction !== "any")
    filters.push(
      `中轨较${config.slope_periods}期前${config.middle_direction === "rising" ? "上行" : "下行"}`,
    );
  if (config.require_expansion) filters.push("带宽较前期扩大");
  return `${cycle}BOLL ${mode}（周期${config.band_period}，标准差${config.deviations}倍${filters.length ? "；" + filters.join("；") : ""}）`;
}
