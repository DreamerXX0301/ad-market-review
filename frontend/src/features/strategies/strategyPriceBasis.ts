type Row = Record<string, any>;
export type StockStrategyPriceBasis = "none" | "forward";

/** A UI default must not rewrite an existing version's historical basis. */
export function initialStrategyPriceBasis(
  strategy?: Row,
): StockStrategyPriceBasis {
  return strategy
    ? strategy.price_basis === "forward"
      ? "forward"
      : "none"
    : "forward";
}

export function requestedPriceBasisLabel(value: unknown) {
  return value === "forward"
    ? "本地前复权"
    : value === "none"
      ? "未复权"
      : "价格口径待核验";
}

/** The frozen run owns this label, never the currently edited strategy. */
export function strategyRunPriceBasis(run: Row | null) {
  if (!run) return "价格口径待核验";
  return run.requested_price_basis == null
    ? "未复权（历史版本）"
    : requestedPriceBasisLabel(run.requested_price_basis);
}

export function candidatePriceBasis(row: Row, run: Row | null) {
  const metrics = row.metrics || {};
  const basis = metrics.price_basis;
  const reason =
    metrics.adjustment?.reason || metrics.adjustment?.evidence?.reason || "";
  const anchor =
    metrics.anchor_date ||
    metrics.adjustment_anchor ||
    metrics.adjustment?.anchor;
  if (basis === "forward_event_affine")
    return {
      label: "本地前复权",
      reason: `本股实际使用本地前复权价格${anchor ? `，锚定 ${anchor}` : ""}。`,
      unavailable: false,
    };
  if (basis === "none")
    return {
      label: "未复权",
      reason: reason || "本股实际使用未复权价格。",
      unavailable: false,
    };
  if (basis === "unavailable")
    return {
      label: "复权待核验",
      reason: reason || "本股公司行动或价格变换证据不足，相关条件保持待核验。",
      unavailable: true,
    };
  if (
    basis == null &&
    run &&
    (run.requested_price_basis == null || run.requested_price_basis === "none")
  )
    return {
      label: "未复权（历史结果）",
      reason: "旧版已保存结果沿用未复权口径，不随当前策略配置改变。",
      unavailable: false,
    };
  return {
    label: "实际口径待核验",
    reason: reason || "本股结果没有留存可核验的实际价格口径。",
    unavailable: true,
  };
}
