import type { Row } from "../../lib/api";
import type { RuleNode } from "./historicalRules";

export type TechnicalPresetId =
  | "volume-expand"
  | "volume-shrink"
  | "macd-above-zero"
  | "kd-low-cross"
  | "kd-above"
  | "kd-cross-range"
  | "kd-range"
  | "macd-dead-cross"
  | "macd-above-zero-dead"
  | "kd-dead-cross"
  | "kd-high-dead"
  | "kd-below";
export const technicalConditionPresets: {
  id: TechnicalPresetId;
  label: string;
  description: string;
  requirements: [string, string][];
}[] = [
  {
    id: "volume-expand",
    label: "成交量大于前一周期（放量）",
    description:
      "当期成交量严格大于前一可比周期，相等不算放量，无需填写倍数。日K比较前一市场交易日，周/月K比较前一完整周/月；缺报价或零量停牌时待核验。若要至少1.5倍，另选成交量倍数条件。",
    requirements: [["volume_expand", "=="]],
  },
  {
    id: "volume-shrink",
    label: "成交量小于前一周期（缩量）",
    description:
      "当期成交量严格小于前一可比周期，相等不算缩量，无需填写倍数。日K比较前一市场交易日，周/月K比较前一完整周/月；不放量不等于缩量，缺报价或零量停牌时待核验。",
    requirements: [["volume_shrink", "=="]],
  },
  {
    id: "macd-above-zero",
    label: "MACD 水上金叉",
    description: "当期 DIF 上穿 DEA，且当期 DIF、DEA 都严格大于 0。",
    requirements: [
      ["macd_cross", "=="],
      ["macd_dif", ">"],
      ["macd_dea", ">"],
    ],
  },
  {
    id: "kd-low-cross",
    label: "KDJ 低位金叉",
    description: "当期 K 上穿 D，且当期 K、D 都低于所设上限；等于上限不满足。",
    requirements: [
      ["kd_cross", "=="],
      ["kdj_k", "<"],
      ["kdj_d", "<"],
    ],
  },
  {
    id: "kd-above",
    label: "KDJ K > D（持续状态）",
    description: "当期 K 大于 D；不要求当期刚发生金叉。",
    requirements: [
      ["kdj_k", ">"],
      ["kdj_d", ">"],
    ],
  },
  {
    id: "kd-cross-range",
    label: "KDJ金叉＋K值范围",
    description: "当期 K 上穿 D，且当期 K 在所填闭区间内；只限制 K，不限制 D。",
    requirements: [
      ["kd_cross", "=="],
      ["kdj_k", ">="],
      ["kdj_k", "<="],
    ],
  },
  {
    id: "kd-range",
    label: "K值范围（持续状态）",
    description: "当期 K 在所填闭区间内，不要求刚发生金叉，也不限制 D。",
    requirements: [
      ["kdj_k", ">="],
      ["kdj_k", "<="],
    ],
  },
  {
    id: "macd-dead-cross",
    label: "MACD 死叉",
    description: "前一期 DIF 不低于 DEA，当期 DIF 下穿 DEA；不限制零轴位置。",
    requirements: [["macd_dead_cross", "=="]],
  },
  {
    id: "macd-above-zero-dead",
    label: "MACD 水上死叉",
    description: "当期 DIF 下穿 DEA，且当期 DIF、DEA 都严格大于 0。",
    requirements: [
      ["macd_dead_cross", "=="],
      ["macd_dif", ">"],
      ["macd_dea", ">"],
    ],
  },
  {
    id: "kd-dead-cross",
    label: "KDJ 死叉",
    description: "前一期 K 不低于 D，当期 K 下穿 D；不限制 K、D 的高低位置。",
    requirements: [["kd_dead_cross", "=="]],
  },
  {
    id: "kd-high-dead",
    label: "KDJ 高位死叉",
    description:
      "当期 K 下穿 D，且当期 K、D 都严格高于所设下限；等于下限不满足。",
    requirements: [
      ["kd_dead_cross", "=="],
      ["kdj_k", ">"],
      ["kdj_d", ">"],
    ],
  },
  {
    id: "kd-below",
    label: "KDJ K < D（持续状态）",
    description: "当期 K 小于 D；不要求当期刚发生死叉。",
    requirements: [
      ["kdj_k", "<"],
      ["kdj_d", "<"],
    ],
  },
];
export const kdRangeStartingPoints = [
  { label: "低位", min: 0, max: 20 },
  { label: "中位", min: 20, max: 80 },
  { label: "高位", min: 80, max: 100 },
];
export function validateKdRange(min: unknown, max: unknown): string | null {
  if (
    typeof min !== "number" ||
    typeof max !== "number" ||
    !Number.isFinite(min) ||
    !Number.isFinite(max) ||
    min < 0 ||
    max > 100 ||
    min > max
  )
    return "K值上下限请输入0—100的有限数字，且下限不能大于上限";
  return null;
}

export function availableTechnicalPresets(
  fields: Row[],
  period: string,
  scope?: string,
) {
  const available = technicalConditionPresets.filter((preset) =>
    preset.requirements.every(([name, operator]) => {
      const field = fields.find((item) => item.field === name);
      return (
        field &&
        field.operators?.includes(operator) &&
        (!field.supported_periods || field.supported_periods.includes(period))
      );
    }),
  );
  if (scope !== "trade-exit") return available;
  const priority: TechnicalPresetId[] = [
    "macd-dead-cross",
    "kd-dead-cross",
    "macd-above-zero-dead",
    "kd-high-dead",
    "kd-below",
  ];
  return [...available].sort((a, b) => {
    const rank = (id: TechnicalPresetId) =>
      priority.includes(id) ? priority.indexOf(id) : priority.length;
    return rank(a.id) - rank(b.id);
  });
}

export function createTechnicalPreset(
  id: TechnicalPresetId,
  period: string,
  upper = id === "kd-high-dead" ? 80 : 20,
  range: { min: number; max: number } = { min: 0, max: 100 },
): RuleNode {
  if (!["D", "W", "M"].includes(period))
    throw new Error("请选择日K、完整周K或完整月K");
  if (id === "volume-expand" || id === "volume-shrink") {
    return {
      field: id === "volume-expand" ? "volume_expand" : "volume_shrink",
      op: "==",
      value: true,
      period,
    };
  }
  if (id === "macd-above-zero")
    return {
      all: [
        { field: "macd_cross", op: "==", value: true, period },
        { field: "macd_dif", op: ">", value: 0, period },
        { field: "macd_dea", op: ">", value: 0, period },
      ],
    };
  if (id === "kd-low-cross") {
    if (!Number.isFinite(upper) || upper < 0 || upper > 100)
      throw new Error("KDJ低位上限请输入0—100的有限数值");
    return {
      all: [
        { field: "kd_cross", op: "==", value: true, period },
        { field: "kdj_k", op: "<", value: upper, period },
        { field: "kdj_d", op: "<", value: upper, period },
      ],
    };
  }
  if (id === "kd-above")
    return { field: "kdj_k", op: ">", right_field: "kdj_d", period };
  if (id === "macd-dead-cross" || id === "kd-dead-cross")
    return {
      field: id === "macd-dead-cross" ? "macd_dead_cross" : "kd_dead_cross",
      op: "==",
      value: true,
      period,
    };
  if (id === "macd-above-zero-dead")
    return {
      all: [
        { field: "macd_dead_cross", op: "==", value: true, period },
        { field: "macd_dif", op: ">", value: 0, period },
        { field: "macd_dea", op: ">", value: 0, period },
      ],
    };
  if (id === "kd-high-dead") {
    if (!Number.isFinite(upper) || upper < 0 || upper > 100)
      throw new Error("KDJ高位下限请输入0—100的有限数值");
    return {
      all: [
        { field: "kd_dead_cross", op: "==", value: true, period },
        { field: "kdj_k", op: ">", value: upper, period },
        { field: "kdj_d", op: ">", value: upper, period },
      ],
    };
  }
  if (id === "kd-below")
    return { field: "kdj_k", op: "<", right_field: "kdj_d", period };
  if (id === "kd-cross-range" || id === "kd-range") {
    const error = validateKdRange(range.min, range.max);
    if (error) throw new Error(error);
    return {
      all: [
        ...(id === "kd-cross-range"
          ? [{ field: "kd_cross", op: "==", value: true, period }]
          : []),
        { field: "kdj_k", op: ">=", value: range.min, period },
        { field: "kdj_k", op: "<=", value: range.max, period },
      ],
    };
  }
  throw new Error("未识别的技术组合");
}

/** Append the whole preset as one child so an OR parent cannot split its AND. */
export function appendTechnicalPreset(
  value: RuleNode,
  preset: RuleNode,
): RuleNode {
  const logic = Array.isArray(value.any) ? "any" : "all";
  const children = Array.isArray(value[logic])
    ? value[logic]
    : value.field || value.history_event
      ? [value]
      : [];
  return { [logic]: [...children, preset] };
}

export function ruleTreeSize(value: RuleNode): {
  count: number;
  depth: number;
} {
  const children: RuleNode[] = value.history_event
    ? [value.history_event.where, value.history_event.then].filter(Boolean)
    : value.all || value.any || value.conditions || [];
  const sizes = children.map(ruleTreeSize);
  return {
    count: 1 + sizes.reduce((sum, child) => sum + child.count, 0),
    depth: sizes.length
      ? 1 + Math.max(...sizes.map((child) => child.depth))
      : 0,
  };
}

export function technicalPresetInsertionError(
  root: RuleNode,
  target: RuleNode,
  preset: RuleNode,
  targetDepth: number,
): string | null {
  const children = target.all || target.any;
  const isGroup = Array.isArray(children);
  if (isGroup && children.length >= 50) return "此条件组已达50项，请先精简条件";
  const rootSize = ruleTreeSize(root),
    presetSize = ruleTreeSize(preset);
  if (rootSize.count + presetSize.count + (isGroup ? 0 : 1) > 100)
    return "规则已接近100节点，请先精简条件";
  // Keep new shortcuts within six levels, including history's extra groups.
  if (Math.max(rootSize.depth, targetDepth + 1 + presetSize.depth) > 6)
    return "当前嵌套过深，请在较外层添加技术组合";
  return null;
}
export function ruleReplacementError(
  root: RuleNode,
  before: RuleNode,
  after: RuleNode,
  targetDepth: number,
): string | null {
  const rootSize = ruleTreeSize(root),
    oldSize = ruleTreeSize(before),
    nextSize = ruleTreeSize(after);
  if (rootSize.count - oldSize.count + nextSize.count > 100)
    return "规则已接近100节点，请先精简条件";
  if (Math.max(rootSize.depth, targetDepth + nextSize.depth) > 6)
    return "当前嵌套过深，请在较外层设置历史模板";
  const oversized = (node: RuleNode): boolean => {
    if (!node || typeof node !== "object") return false;
    const children = node.history_event
      ? [node.history_event.where, node.history_event.then]
      : node.all || node.any || [];
    return children.length > 50 || children.some(oversized);
  };
  return oversized(after) ? "条件组最多50项，请先精简条件" : null;
}
