type Row = Record<string, unknown>;
type CloseStats = { average: number | null; valid: number; requested: number };

const stockCode = /^[0-9]{6}\.(SH|SZ|BJ)$/;
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

/** Compare only the original members with dated, comparable close observations. */
export function auctionAssociationCloseStats(
  group: Row,
  closeView: Row | null | undefined,
  date: string,
): CloseStats {
  const ids = group?.instrument_ids;
  if (
    !Array.isArray(ids) ||
    ids.length < 1 ||
    ids.length > 100 ||
    ids.some((id) => typeof id !== "string" || !stockCode.test(id))
  )
    return { average: null, valid: 0, requested: 0 };
  const members = new Set<string>(ids);
  const empty: CloseStats = {
    average: null,
    valid: 0,
    requested: members.size,
  };
  if (
    !date ||
    closeView?.date !== date ||
    closeView.version !== "auction-close-review-v1" ||
    typeof closeView.state !== "string" ||
    !["available", "partial", "unknown"].includes(closeView.state) ||
    !Array.isArray(closeView.items) ||
    closeView.items.length > 100
  )
    return empty;
  const rows: Row[] = closeView.items.filter(
    (row): row is Row =>
      row !== null && typeof row === "object" && !Array.isArray(row),
  );
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (typeof row.instrument_id === "string")
      counts.set(row.instrument_id, (counts.get(row.instrument_id) || 0) + 1);
  }
  const values: number[] = [];
  for (const row of rows) {
    if (
      typeof row.instrument_id === "string" &&
      members.has(row.instrument_id) &&
      counts.get(row.instrument_id) === 1 &&
      row.state === "observed" &&
      row.open_check !== "mismatch" &&
      finite(row.close_pct)
    )
      values.push(row.close_pct);
  }
  if (!values.length) return empty;
  // Scale before summing: valid finite inputs must not overflow their mean.
  const scale = Math.max(...values.map((value) => Math.abs(value)));
  const normalized = scale
    ? values.reduce((sum, value) => sum + value / scale, 0) / values.length
    : 0;
  const average = scale * Math.max(-1, Math.min(1, normalized));
  return {
    average: finite(average) ? average : null,
    valid: values.length,
    requested: members.size,
  };
}
