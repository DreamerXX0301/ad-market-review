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
  origin?: {
    algorithm?: string;
    period?: string;
    as_of?: string;
    snapshot_id?: string;
    manual_override?: boolean;
    evidence?: Array<{ date: string; confirmed_on: string; price: number }>;
  };
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
/** Algorithmic suggestions and all index lines are deliberately unarmed. */
export function quietLineSettings(line_type: ChartLineType = "support") {
  return { line_type, alert_modes: [] as string[], auto_watchlist: false };
}
export function levelOriginLabel(line: Partial<ChartLine>) {
  const origin = line.origin;
  if (!origin?.algorithm) return "手动划线";
  const period =
    ({ D: "日K", W: "完整周K", M: "完整月K" } as Record<string, string>)[
      origin.period || ""
    ] || origin.period;
  return `${origin.manual_override ? "已手动修改 · " : ""}${period}候选 · 观察 ${origin.as_of} · ${origin.evidence?.length || 0}处历史分型`;
}
/** Never flash a candidate from the previous stock, period or generation. */
export function levelPreviewMatchesChart(
  preview: Record<string, any> | null,
  chart: Record<string, any> | null,
  instrumentId: string,
) {
  const parameters = preview?.parameters;
  const scope = annotationScope(chart);
  return (
    !!preview &&
    !!chart &&
    !!scope &&
    typeof chart.snapshot_id === "string" &&
    !!chart.snapshot_id &&
    (chart.instrument?.instrument_id || chart.instrument?.thscode) ===
      instrumentId &&
    preview.instrument_id === instrumentId &&
    parameters?.snapshot_id === chart.snapshot_id &&
    parameters?.as_of === chart.as_of &&
    parameters?.period === chart.period &&
    parameters?.price_basis ===
      (scope.price_basis === "none" ? "none" : "forward")
  );
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
export type ChartLineProjection = {
  line: ChartLine;
  price: number | null;
  anchor: string | null;
  converted: boolean;
  reason: string;
};
function validDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const date = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
}
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const nonnegative = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

/** Project a saved coordinate for this chart only; never rewrite the mark or alert. */
export function projectChartLine(
  line: ChartLine,
  chart: Record<string, any> | null,
): ChartLineProjection {
  const unavailable = (reason: string): ChartLineProjection => ({
    line,
    price: null,
    anchor: null,
    converted: false,
    reason,
  });
  const instrument =
    chart?.instrument?.instrument_id || chart?.instrument?.thscode;
  if (
    typeof instrument !== "string" ||
    typeof line.instrument_id !== "string" ||
    instrument.toUpperCase() !== line.instrument_id.toUpperCase()
  )
    return unavailable("划线与本图证券不匹配，本图未显示");
  const scope = annotationScope(chart);
  if (!positive(line.price)) return unavailable("原保存线价无效，本图未显示");
  if (!scope) return unavailable("本图价格口径尚未核验，本图未显示");
  if (line.price_basis !== scope.price_basis)
    return unavailable(
      `价格口径不同：原线${line.price_basis === "none" ? "未复权" : "前复权"}，本图${scope.price_basis === "none" ? "未复权" : "前复权"}；本图未显示`,
    );
  if (scope.price_basis === "none")
    return {
      line,
      price: line.price,
      anchor: null,
      converted: false,
      reason: "未复权原保存线价；日／周／月共享",
    };
  if (
    !validDate(line.anchor_date) ||
    !validDate(scope.anchor_date) ||
    chart?.as_of !== scope.anchor_date
  )
    return unavailable("原锚或图表观察日无效，本图未显示");
  if (line.anchor_date > scope.anchor_date)
    return unavailable("原保存锚晚于本图观察日，不将未来锚换算回历史图");
  const plan = chart?.adjustment;
  if (
    plan?.usable !== true ||
    plan.model !== "event_affine" ||
    plan.anchor !== scope.anchor_date ||
    !Array.isArray(plan.events) ||
    !plan.events.every(
      (event: unknown) =>
        event != null && typeof event === "object" && !Array.isArray(event),
    ) ||
    !Array.isArray(plan.quarantined) ||
    plan.quarantined.length ||
    plan.events_count !== plan.events.length
  )
    return unavailable("当前公司行动或复权锚未通过校验，本图未显示");
  if (
    !Array.isArray(chart?.bars) ||
    !chart.bars.length ||
    chart.bars.some(
      (bar: Record<string, any>) =>
        !bar ||
        !validDate(bar.date) ||
        bar.date > scope.anchor_date! ||
        (bar.adjustment_anchor != null &&
          bar.adjustment_anchor !== scope.anchor_date) ||
        (bar.price_basis != null && bar.price_basis !== scope.price_basis),
    )
  )
    return unavailable("本图报价与复权锚不一致，本图未显示");
  if (line.anchor_date !== scope.anchor_date) {
    const proof = chart?.annotation_projection_evidence;
    if (
      proof?.version !== "chart-annotation-projection-v1" ||
      proof.available !== true ||
      proof.source !== "published_corporate_actions" ||
      typeof chart.snapshot_id !== "string" ||
      !chart.snapshot_id ||
      proof.snapshot_id !== chart.snapshot_id ||
      !validDate(proof.start_date) ||
      !validDate(proof.end_date) ||
      proof.end_date !== scope.anchor_date ||
      proof.start_date > line.anchor_date
    )
      return unavailable(
        proof?.start_date &&
          validDate(proof.start_date) &&
          line.anchor_date < proof.start_date
          ? `原锚 ${line.anchor_date} 早于本图可核验区间 ${proof.start_date}，本图未显示`
          : "缺少覆盖原锚至本图观察日的同代际公司行动依据，本图未显示",
      );
  }
  let price = line.price;
  let previous: string | null = null;
  let applied = 0;
  for (const event of [...plan.events].sort((a, b) =>
    String(a.date).localeCompare(String(b.date)),
  )) {
    if (
      !validDate(event.date) ||
      event.date > scope.anchor_date ||
      event.date === previous ||
      ![event.cash, event.bonus, event.rights, event.rights_price].every(
        nonnegative,
      )
    )
      return unavailable("公司行动换算字段、日期或重复事件未核验，本图未显示");
    previous = event.date;
    if (event.date <= line.anchor_date) continue;
    price =
      (price - event.cash + event.rights_price * event.rights) /
      (1 + event.bonus + event.rights);
    if (!positive(price)) return unavailable("原线换算后价格无效，本图未显示");
    applied++;
  }
  const converted = line.anchor_date !== scope.anchor_date;
  return {
    line,
    price,
    anchor: scope.anchor_date,
    converted,
    reason: converted
      ? `原锚 ${line.anchor_date} → 本图 ${scope.anchor_date}；${applied ? `按${applied}项已核验公司行动换算` : "核验区间无新增公司行动，线价不变"}；原保存价格与提醒证据保留`
      : "与原保存复权锚一致；日／周／月共享",
  };
}
export function lineLabel(line: ChartLine, displayPrice = line.price) {
  const type = savedLineSettings(line).line_type;
  return `${line.label || lineTypeLabels[type]}${line.label && type !== "reference" ? `（${lineTypeLabels[type]}）` : ""} · ${displayPrice.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}`;
}
