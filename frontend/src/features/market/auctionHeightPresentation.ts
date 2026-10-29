/** Yesterday's verified height belongs to the immutable auction sample. */
type AuctionHeightRow = Record<string, any>;
export type AuctionHeightKey = "all" | "1" | "2" | "3" | "4" | "5+" | "other";
export const AUCTION_HEIGHT_OPTIONS: ReadonlyArray<{
  key: AuctionHeightKey;
  label: string;
}> = [
  { key: "all", label: "全部" },
  { key: "1", label: "首板" },
  { key: "2", label: "二板" },
  { key: "3", label: "三板" },
  { key: "4", label: "四板" },
  { key: "5+", label: "五板及以上" },
  { key: "other", label: "其他 / 未核验" },
];
const number = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const validChange = (row: AuctionHeightRow) =>
  row.state == null || row.state === "observed"
    ? number(row.auction_pct)
    : null;

export function auctionPreviousHeight(row: AuctionHeightRow): number | null {
  if (row.previous_state != null && row.previous_state !== "limit_up")
    return null;
  const height = row.previous_board_count;
  return Number.isSafeInteger(height) && height >= 1 ? height : null;
}

export function auctionHeightLabel(row: AuctionHeightRow): string | null {
  const height = auctionPreviousHeight(row);
  return height == null ? null : height === 1 ? "昨日首板" : `昨日${height}板`;
}

export function auctionHeightMatches(
  row: AuctionHeightRow,
  key: AuctionHeightKey,
) {
  const height = auctionPreviousHeight(row);
  return (
    key === "all" ||
    (key === "other"
      ? height == null
      : key === "5+"
        ? height != null && height >= 5
        : height === Number(key))
  );
}

/** Contradictory duplicate identities stay unknown rather than picking a winner. */
export function auctionUniqueHeightRows(
  rows: AuctionHeightRow[],
): AuctionHeightRow[] {
  const found = new Map<string, AuctionHeightRow>();
  for (const row of rows) {
    if (!/^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id || "")) continue;
    const previous = found.get(row.instrument_id);
    if (!previous) {
      found.set(row.instrument_id, { ...row });
      continue;
    }
    if (auctionPreviousHeight(previous) !== auctionPreviousHeight(row)) {
      previous.previous_board_count = null;
      previous.previous_state = "conflict";
    }
    if (
      number(previous.auction_pct) !== number(row.auction_pct) ||
      previous.state !== row.state
    ) {
      previous.auction_pct = null;
      previous.state = "unknown";
    }
  }
  return [...found.values()];
}

export function auctionHeightSummaries(rows: AuctionHeightRow[]) {
  const unique = auctionUniqueHeightRows(rows);
  return AUCTION_HEIGHT_OPTIONS.map((option) => {
    const items = unique.filter((row) => auctionHeightMatches(row, option.key));
    const values = items
      .map(validChange)
      .filter((value): value is number => value !== null);
    const average = values.length
      ? values.reduce((sum, value) => sum + value / values.length, 0)
      : null;
    return {
      ...option,
      count: items.length,
      valid_count: values.length,
      missing_count: items.length - values.length,
      average_auction_pct:
        average != null && Number.isFinite(average) ? average : null,
    };
  });
}

/** Height first, then descending auction return; neither unknown becomes zero. */
export function auctionSortByHeight(
  rows: AuctionHeightRow[],
  direction: "asc" | "desc" = "desc",
) {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const av = auctionPreviousHeight(a.row),
        bv = auctionPreviousHeight(b.row);
      if (av == null && bv != null) return 1;
      if (av != null && bv == null) return -1;
      if (av != null && bv != null && av !== bv)
        return (av - bv) * (direction === "asc" ? 1 : -1);
      const ap = validChange(a.row),
        bp = validChange(b.row);
      if (ap == null && bp != null) return 1;
      if (ap != null && bp == null) return -1;
      return (bp ?? 0) - (ap ?? 0) || a.index - b.index;
    })
    .map(({ row }) => row);
}
