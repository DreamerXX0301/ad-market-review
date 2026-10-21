import type { ChartEventBaseline } from "./chartDisplay";

export function resolveStockEventBaseline(
  rounds: Record<string, any>[],
  selectedId: string | undefined,
  explicit?: ChartEventBaseline,
): ChartEventBaseline | undefined {
  if (selectedId === "none") return undefined;
  if (
    explicit &&
    (!selectedId ||
      explicit.roundId === selectedId ||
      (!explicit.roundId && selectedId === "context"))
  )
    return explicit;
  const round = rounds.find((item) => item.round_id === selectedId);
  if (!round) return undefined;
  return {
    date: round.entry_business_date,
    rawClose: round.entry_price,
    priceBasis: round.entry_basis?.price_basis || "unknown",
    label: "事件日收盘",
    roundId: round.round_id,
  };
}
