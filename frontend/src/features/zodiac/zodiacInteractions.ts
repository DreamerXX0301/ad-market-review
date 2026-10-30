type Stock = Record<string, any>;

export const sortableFields = new Set([
  "name",
  "instrument_id",
  "focused",
  "added_business_date",
  "round_number",
  "close",
  "change_pct",
  "return_ytd_pct",
  "change_30d_pct",
  "return_mtd_pct",
  "limit_up_ytd",
  "last_limit_up_date",
  "since_entry_pct",
  "amount",
  "volume",
  "turnover_rate",
  "volume_vs_5d",
  "holding_sessions",
  "max_drawdown_pct",
  ...[5, 10, 20, 60].map((n) => `return_${n}d_pct`),
  ...[5, 10, 20, 30, 60, 250].map((n) => `ma${n}`),
  "macd_dif",
  "macd_dea",
  "macd_hist",
  "kdj_k",
  "kdj_d",
  "kdj_j",
  "boll_upper",
  "boll_mid",
  "boll_lower",
]);

export function nextColumnSort(current: string, key: string) {
  return `${key}:${current === `${key}:desc` ? "asc" : "desc"}`;
}
export function poolSort(primary: string, secondary: string) {
  return secondary && secondary.split(":")[0] !== primary.split(":")[0]
    ? `${primary},${secondary}`
    : primary;
}
export function toggleStockSelection(
  current: Stock[],
  row: Stock,
  limit = 100,
) {
  if (current.some((r) => r.instrument_id === row.instrument_id))
    return current.filter((r) => r.instrument_id !== row.instrument_id);
  return current.length < limit ? [...current, row] : current;
}
export function additionNotice(receipt: Stock) {
  const items: Stock[] = receipt.items || [];
  const added = items.filter(
    (r) => r.state === "added" || r.state === "readded",
  ).length;
  const existing = items.filter((r) => r.state === "already_active").length;
  return `已加入 ${added} 只${existing ? `；${existing} 只已在池内，原信息保留` : ""}`;
}
