type Row = Record<string, any>;
const day = (value: unknown): value is string =>
  typeof value === "string" &&
  /^\d{4}-\d{2}-\d{2}$/.test(value) &&
  Number.isFinite(new Date(value + "T00:00:00Z").valueOf()) &&
  new Date(value + "T00:00:00Z").toISOString().slice(0, 10) === value;
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

/** Browsing only: never derive a return from a raw quote and a transformed reference. */
export function trackingPriceContext(row: Row) {
  const forward =
    row.entry_basis?.signal_price_basis === "forward" ||
    row.tracking_reference_price_basis === "forward_event_affine";
  const anchor = day(row.tracking_comparison_anchor)
    ? row.tracking_comparison_anchor
    : null;
  const reference =
    row.tracking_reference_price_basis === "forward_event_affine" &&
    anchor &&
    anchor === row.valuation_date &&
    row.tracking_reference_evidence?.state === "ready" &&
    positive(row.tracking_reference_price)
      ? row.tracking_reference_price
      : null;
  return {
    forward,
    label: forward ? "同口径价格变化" : "跟踪涨幅",
    anchor,
    reference,
    change: forward && reference === null ? null : row.tracking_change_pct,
    reason:
      row.comparison_reason ||
      (forward
        ? row.tracking_reference_evidence?.reason ||
          (reference === null
            ? "当前观察日的前复权参照价尚未完成手动核对，价格变化保持待核验。"
            : row.tracking_comparison_scope) ||
          "同观察日前复权价格比较。"
        : "冻结入池基准，按同口径比较"),
    scope: forward
      ? row.tracking_comparison_scope ||
        "同观察日前复权价格变化；不含分红到账、送转股数或配股权益记账。"
      : "",
  };
}
