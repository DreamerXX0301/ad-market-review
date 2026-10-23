export type ChartLineType = "reference" | "support" | "resistance";
export type ChartLine = {
  annotation_id: string;
  instrument_id: string;
  price: number;
  label: string;
  color: string;
  price_basis: string;
  anchor_date: string | null;
  line_type?: ChartLineType;
  alert_modes?: string[];
  auto_watchlist?: boolean;
  revision?: number;
};
export const lineTypeLabels: Record<ChartLineType, string> = {
  reference: "参考线",
  support: "支撑线",
  resistance: "压力线",
};
export const lineAlertRules = {
  support_reclaim: {
    title: "回踩收回",
    hint: "前收盘不低于支撑，日内回踩，收盘收回支撑线（含等号）",
  },
  support_breakdown: {
    title: "收盘失守",
    hint: "前收盘不低于支撑，收盘跌破支撑线；失守仅作风险提醒，不自动入自选",
  },
  pressure_close_breakout: {
    title: "收盘突破",
    hint: "前收盘未站上压力线，本日收盘严格站上",
  },
  pressure_touch: {
    title: "盘中触碰",
    hint: "本日日线最低价至最高价包含压力线；不等同收盘突破",
  },
};
export const lineTypeModes: Record<ChartLineType, string[]> = {
  reference: [],
  support: ["support_reclaim", "support_breakdown"],
  resistance: ["pressure_close_breakout", "pressure_touch"],
};
// Saved names never imply a type. All legacy marks remain quiet reference lines.
export function savedLineSettings(line: Partial<ChartLine>) {
  const line_type: ChartLineType =
    line.line_type === "support" || line.line_type === "resistance"
      ? line.line_type
      : "reference";
  return {
    line_type,
    alert_modes: lineTypeModes[line_type].filter((mode) =>
      Array.isArray(line.alert_modes) ? line.alert_modes.includes(mode) : false,
    ),
    auto_watchlist: line_type !== "reference" && line.auto_watchlist === true,
  };
}
export function newLineSettings(line_type: ChartLineType = "support") {
  return {
    line_type,
    alert_modes:
      line_type === "support"
        ? ["support_reclaim"]
        : [...lineTypeModes[line_type]],
    auto_watchlist: line_type !== "reference",
  };
}
export const annotationColors: Record<string, string> = {
  blue: "#79a9ff",
  amber: "#e8c16b",
  red: "#f17c80",
  green: "#45c6a4",
  violet: "#b9a0ff",
};
export function annotationScope(data: Record<string, any> | null) {
  if (!data) return null;
  if (data.price_basis === "none")
    return { price_basis: "none", anchor_date: null };
  if (["forward", "forward_event_affine"].includes(data.price_basis)) {
    const anchor = data.adjustment?.anchor || data.as_of;
    if (typeof anchor === "string" && /^\d{4}-\d{2}-\d{2}$/.test(anchor))
      return { price_basis: "forward_event_affine", anchor_date: anchor };
  }
  return null;
}
export function lineMatchesScope(
  line: ChartLine,
  scope: ReturnType<typeof annotationScope>,
) {
  return (
    !!scope &&
    line.price_basis === scope.price_basis &&
    (line.price_basis === "none" || line.anchor_date === scope.anchor_date)
  );
}
export function lineLabel(line: ChartLine) {
  const type = savedLineSettings(line).line_type;
  return `${line.label || lineTypeLabels[type]}${line.label && type !== "reference" ? `（${lineTypeLabels[type]}）` : ""} · ${line.price.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}`;
}
