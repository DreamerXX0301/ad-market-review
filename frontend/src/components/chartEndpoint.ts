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
