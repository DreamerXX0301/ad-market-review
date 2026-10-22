import type { Row } from "../../lib/api";
import type { RuleNode } from "./historicalRules";

export type TechnicalPresetId = "macd-above-zero" | "kd-low-cross" | "kd-above";
export const technicalConditionPresets: {
  id: TechnicalPresetId;
  label: string;
  description: string;
  requirements: [string, string][];
}[] = [
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
];

export function availableTechnicalPresets(fields: Row[], period: string) {
  return technicalConditionPresets.filter((preset) =>
    preset.requirements.every(([name, operator]) => {
      const field = fields.find((item) => item.field === name);
      return (
        field &&
        field.operators?.includes(operator) &&
        (!field.supported_periods || field.supported_periods.includes(period))
      );
    }),
  );
}

export function createTechnicalPreset(
  id: TechnicalPresetId,
  period: string,
  upper = 20,
): RuleNode {
  if (!["D", "W", "M"].includes(period))
    throw new Error("请选择日K、完整周K或完整月K");
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
