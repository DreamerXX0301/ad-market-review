import type { Row } from "../lib/api";

export const automaticPriceAlertModes = {
  support_reclaim: {
    title: "支撑回踩收回",
    hint: "日内回踩，收盘重新站回事前支撑",
  },
  close_breakout: {
    title: "收盘突破",
    hint: "收盘从未站上到站上事前突破参考位",
  },
  support_breakdown: {
    title: "支撑失守",
    hint: "收盘跌破事前支撑，只作风险提醒",
  },
  stop_breakdown: {
    title: "参考止损跌破",
    hint: "收盘跌破事前参考止损位，只作风险提醒",
  },
} as const;
export type AutomaticAlertMode = keyof typeof automaticPriceAlertModes;
export type AutomaticAlertDraft = {
  enabled: boolean;
  modes: AutomaticAlertMode[];
  require_volume: boolean;
  profile: "structure" | "short_week_v1";
  max_risk_pct: number;
};
const validRisk = (value: unknown): value is number =>
  typeof value === "number" &&
  Number.isFinite(value) &&
  value >= 0.5 &&
  value <= 20 &&
  Math.abs(value * 100 - Math.round(value * 100)) < 1e-8;
/** A previous stock or an unsupported rule cannot populate an editable form. */
export function automaticAlertConfig(
  data: Row | null,
  instrumentId: string,
): Row | null {
  const rule = data?.rule;
  const profile = rule?.profile ?? "structure";
  const risk = rule?.max_risk_pct ?? (rule?.profile == null ? 5 : null);
  return data?.instrument_id === instrumentId &&
    ["structure", "short_week_v1"].includes(profile) &&
    rule?.algorithm ===
      (profile === "short_week_v1"
        ? "short-week-risk-levels-v1"
        : "structure-volume-levels-v1") &&
    validRisk(risk) &&
    Number.isSafeInteger(rule.revision) &&
    rule.revision >= 0 &&
    typeof rule.enabled === "boolean" &&
    typeof rule.require_volume === "boolean" &&
    Array.isArray(rule.modes) &&
    rule.modes.every((mode: string) =>
      Object.hasOwn(automaticPriceAlertModes, mode),
    )
    ? { ...data, rule: { ...rule, profile, max_risk_pct: risk } }
    : null;
}
export function automaticAlertDraft(rule: Row): AutomaticAlertDraft {
  return {
    enabled: rule.enabled,
    modes: [...rule.modes],
    require_volume: rule.require_volume,
    profile: rule.profile ?? "structure",
    max_risk_pct: rule.max_risk_pct ?? 5,
  };
}
export function automaticAlertPayload(draft: AutomaticAlertDraft, config: Row) {
  if (draft.enabled && !draft.modes.length)
    throw new Error("启用提醒时请至少选择一种条件");
  if (!["structure", "short_week_v1"].includes(draft.profile))
    throw new Error("请选择有效的参考模式");
  if (!validRisk(draft.max_risk_pct))
    throw new Error("风险距离上限应为0.5%至20%，最多两位小数");
  return {
    ...draft,
    modes: [...draft.modes],
    expected_revision: config.rule.revision,
  };
}
