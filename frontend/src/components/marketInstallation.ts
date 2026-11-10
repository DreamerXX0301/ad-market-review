export type MarketInstallationState =
  "loading" | "error" | "unavailable" | "empty" | "ready";

/** An empty installation is a successful, well-formed response, never a failed request. */
export function marketInstallationState(
  data: Record<string, any> | null,
  loading: boolean,
  error: string,
): MarketInstallationState {
  const unresolved = error ? "error" : loading ? "loading" : "unavailable";
  if (
    !data ||
    !Object.hasOwn(data, "data_date") ||
    !Array.isArray(data.market_dates)
  )
    return unresolved;
  const validDate = (value: unknown) =>
    typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  if (
    data.data_date != null &&
    data.data_date !== "" &&
    !validDate(data.data_date)
  )
    return unresolved;
  if (data.market_dates.some((date: unknown) => !validDate(date)))
    return unresolved;
  if (validDate(data.data_date) || data.market_dates.length > 0)
    return "ready";
  return error || loading ? unresolved : "empty";
}
