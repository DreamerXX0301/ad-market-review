import type { Row } from "../lib/api";

/** The heading belongs to the observation day, independently of chart hover/period. */
export function stockPatternHeader(
  data?: Row | null,
  asOf?: string,
): Row | undefined {
  if (
    !data ||
    !["stock", "equity", "a_share", "A"].includes(
      data.instrument?.kind || data.kind || data.asset_type,
    )
  )
    return;
  const day = asOf || data.data_date;
  if (
    !day ||
    data.pattern_as_of !== day ||
    data.pattern_date !== day ||
    data.date !== day ||
    !data.snapshot_id
  )
    return;
  return {
    date: day,
    pattern_events: (Array.isArray(data.pattern_events)
      ? data.pattern_events
      : []
    ).filter(
      (event: Row) =>
        event &&
        typeof event === "object" &&
        event.date === day &&
        event.bar_date === day &&
        event.snapshot_id === data.snapshot_id,
    ),
    pattern_status: data.pattern_status,
  };
}
