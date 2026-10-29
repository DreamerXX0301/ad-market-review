import type { Row } from "../../lib/api";
import { auctionSortByHeight } from "./auctionHeightPresentation";
import { auctionNumber, auctionSortRows } from "./auctionPresentation";

export type AuctionStockBoardKey =
  "all" | "main" | "chinext" | "star" | "beijing" | "unknown";
export type AuctionStockSortKey = "height" | "change";

const BOARDS: ReadonlyArray<{ key: AuctionStockBoardKey; label: string }> = [
  { key: "all", label: "全部" },
  { key: "main", label: "主板" },
  { key: "chinext", label: "创业板" },
  { key: "star", label: "科创板" },
  { key: "beijing", label: "北交所" },
  { key: "unknown", label: "待核验" },
];
const boardOf = (row: Row): AuctionStockBoardKey =>
  ["main", "chinext", "star", "beijing"].includes(row.board)
    ? row.board
    : "unknown";

/** Frozen IDs define membership; missing quotations never remove a stock. */
function stockRows(ids: string[], quotes: Row[]): Row[] {
  const byCode = new Map(quotes.map((row) => [row.instrument_id, row]));
  return [...new Set(ids)].map((id) => {
    const row = byCode.get(id);
    return {
      ...row,
      instrument_id: id,
      name: row?.name || id,
      ...(row ? {} : { state: "unknown", board: "unknown" }),
      auction_pct:
        row?.state === "observed" ? auctionNumber(row.auction_pct) : null,
    };
  });
}

/** Filters and sorting are local views; neither input array nor metadata changes. */
export function auctionStockListRows(
  ids: string[],
  quotes: Row[],
  board: AuctionStockBoardKey = "all",
  sort: AuctionStockSortKey = "height",
  direction: "asc" | "desc" = "desc",
): Row[] {
  const rows = stockRows(ids, quotes).filter(
    (row) => board === "all" || boardOf(row) === board,
  );
  return sort === "height"
    ? auctionSortByHeight(rows, direction)
    : auctionSortRows(rows, "auction_pct", direction);
}

/** Counts always refer to the original deduplicated expanded group. */
export function auctionStockBoardCounts(ids: string[], quotes: Row[]) {
  const rows = stockRows(ids, quotes);
  return BOARDS.map(({ key, label }) => ({
    key,
    label,
    count:
      key === "all"
        ? rows.length
        : rows.filter((row) => boardOf(row) === key).length,
  }));
}
