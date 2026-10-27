/** A supplied read endpoint shares the chart controls without stock lookups. */
export function candleChartEndpoint({
  instrumentId,
  period,
  priceBasis,
  indicatorContinuity,
  asOf,
  dataEndpoint,
}: {
  instrumentId: string;
  period: "D" | "W" | "M";
  priceBasis: string;
  indicatorContinuity?: "quotes" | "market_sessions";
  asOf?: string;
  dataEndpoint?: string;
}): string {
  const endpoint =
    dataEndpoint || `/instruments/${encodeURIComponent(instrumentId)}/chart`;
  const question = endpoint.indexOf("?");
  const path = question < 0 ? endpoint : endpoint.slice(0, question);
  const query = new URLSearchParams(
    question < 0 ? "" : endpoint.slice(question + 1),
  );
  query.set("period", period);
  if (!dataEndpoint) query.set("price_basis", priceBasis);
  if (indicatorContinuity)
    query.set("indicator_continuity", indicatorContinuity);
  if (asOf) query.set("as_of", asOf);
  return `${path}?${query}`;
}

/** Reuse only the owner's exact, already completed first daily sector read. */
export function initialChartResponse(
  initial: { endpoint: string; data: Record<string, any> } | undefined,
  endpoint: string | undefined,
  instrumentId: string,
  asOf: string | undefined,
  period: "D" | "W" | "M",
): Record<string, any> | null {
  const value = initial?.data;
  if (
    !endpoint ||
    initial?.endpoint !== endpoint ||
    !asOf ||
    period !== "D" ||
    value?.period !== "D" ||
    value.as_of !== asOf ||
    value.instrument?.instrument_id !== instrumentId ||
    value.price_basis !== "none" ||
    value.available !== true ||
    typeof value.snapshot_id !== "string" ||
    !value.snapshot_id ||
    !Array.isArray(value.bars) ||
    !value.bars.length
  )
    return null;
  return value;
}
