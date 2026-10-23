import type { ChartEventBaseline } from "./chartDisplay";

/** Keep markers and a user-selected round together; an empty selected round
 * must not fall back to a previous navigation's event proofs.
 */
export function resolveStockHistoricalReference(
  rounds: Record<string, any>[],
  sourceId: string | undefined,
  navigationChecks: unknown,
  referenceChanged: boolean,
) {
  if (navigationChecks !== undefined && !referenceChanged)
    return { checks: navigationChecks, reason: undefined };
  const round = rounds.find((item) => item.round_id === sourceId);
  return {
    checks: round?.historical_event_checks,
    reason: round?.historical_event_checks_reason,
  };
}

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
