type Row = Record<string, any>;
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
function day(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const stamp = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(stamp.getTime()) &&
    stamp.toISOString().slice(0, 10) === value
  );
}
/** Missing current values and unplottable history must not become empty tools. */
export function flowDisplayState(
  data: Row | null | undefined,
  route: string,
  kind: string,
  observation: string,
) {
  const items: Row[] = Array.isArray(data?.items) ? data.items : [];
  const observed = items.filter((row) => finite(row.net_inflow));
  const fundFlowAvailable =
    route === "flow" &&
    data?.summary?.net_flow_available === true &&
    observed.length > 0;
  const hasFundFlow = fundFlowAvailable && kind === "industry";
  const codes = new Set(
    observed
      .map((row) => row.topic_code)
      .filter((value) => typeof value === "string" && !!value),
  );
  const hasFundTrend =
    hasFundFlow &&
    day(observation) &&
    Array.isArray(data?.history) &&
    data.history.some(
      (row: Row) =>
        codes.has(row.topic_code) &&
        finite(row.net_inflow) &&
        day(row.date) &&
        row.date <= observation,
    );
  return { fundFlowAvailable, hasFundFlow, hasFundTrend: !!hasFundTrend };
}
