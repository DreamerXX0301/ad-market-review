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
};
/** A previous stock or an unsupported rule cannot populate an editable form. */
export function automaticAlertConfig(
  data: Row | null,
  instrumentId: string,
): Row | null {
  const rule = data?.rule;
  return data?.instrument_id === instrumentId &&
    rule?.algorithm === "structure-volume-levels-v1" &&
    Number.isSafeInteger(rule.revision) &&
    rule.revision >= 0 &&
    typeof rule.enabled === "boolean" &&
    typeof rule.require_volume === "boolean" &&
    Array.isArray(rule.modes) &&
    rule.modes.every((mode: string) =>
      Object.hasOwn(automaticPriceAlertModes, mode),
    )
    ? data
    : null;
}
export function automaticAlertDraft(rule: Row): AutomaticAlertDraft {
  return {
    enabled: rule.enabled,
    modes: [...rule.modes],
    require_volume: rule.require_volume,
  };
}
export function automaticAlertPayload(draft: AutomaticAlertDraft, config: Row) {
  if (draft.enabled && !draft.modes.length)
    throw new Error("启用提醒时请至少选择一种条件");
  return {
    ...draft,
    modes: [...draft.modes],
    expected_revision: config.rule.revision,
  };
}
