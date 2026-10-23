import type { Row } from "../../lib/api";
import type { ChartEventBaseline } from "../../components/chartDisplay";

export function watchlistEventBaseline(
  row: Row,
): ChartEventBaseline | undefined {
  if (
    !row.tracking_round_id ||
    typeof row.event_date !== "string" ||
    typeof row.event_close !== "number" ||
    !Number.isFinite(row.event_close) ||
    row.event_close <= 0
  )
    return undefined;
  return {
    date: row.event_date,
    rawClose: row.event_close,
    priceBasis: row.event_price_basis || "unknown",
    label: "事件日收盘",
    roundId: row.tracking_round_id,
  };
}

export const watchlistSourceLabel: Record<string, string> = {
  stock: "个股详情",
  tracking: "策略跟踪",
  sector: "行业与题材",
  topic: "题材库",
};

export function watchlistPerformance(row: Row) {
  const finite = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);
  const entryPrice =
    finite(row.watchlist_entry_price) && row.watchlist_entry_price > 0
      ? row.watchlist_entry_price
      : null;
  const value =
    row.watchlist_change_state === "ready" &&
    entryPrice !== null &&
    finite(row.watchlist_change_pct)
      ? row.watchlist_change_pct
      : null;
  return {
    value,
    entryPrice,
    entryDate: row.watchlist_entry_date || row.added_business_date || "待核验",
    reason:
      value === null
        ? row.watchlist_change_reason ||
          row.watchlist_entry_reason ||
          "加入后涨幅待核验"
        : null,
  };
}

export function filterWatchlistRows(
  rows: Row[],
  query: string,
  source = "all",
) {
  const search = query.trim().toLocaleLowerCase();
  return rows.filter((row) => {
    const sourceMatches =
      source === "all" ||
      (source === "sector"
        ? row.source_kind === "sector" || row.source_kind === "topic"
        : row.source_kind === source);
    const names = Array.isArray(row.current_topics)
      ? row.current_topics.map((topic: Row) => topic.name || "")
      : [];
    const industries = Array.isArray(row.current_industries)
      ? row.current_industries.map((topic: Row) => topic.name || "")
      : [];
    return (
      sourceMatches &&
      (!search ||
        [
          row.name,
          row.code,
          row.instrument_id,
          row.source_name,
          ...names,
          ...industries,
        ]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase()
          .includes(search))
    );
  });
}
