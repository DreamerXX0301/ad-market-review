import type { Row } from "../../lib/api";
import CopyStockCode from "../../components/CopyStockCode";
import { percent, tone } from "../../lib/format";
import { auctionNumber, auctionSortRows } from "./auctionPresentation";

export default function AuctionStockTiles({
  ids,
  quotes,
  onStock,
  chartDate,
  topIds,
}: {
  ids: string[];
  quotes: Row[];
  onStock?: (id: string) => void;
  chartDate?: string;
  topIds?: Set<string>;
}) {
  const byCode = new Map(quotes.map((row) => [row.instrument_id, row]));
  const rows = auctionSortRows(
    ids.map((id) => {
      const row = byCode.get(id);
      return {
        instrument_id: id,
        name: row?.name || id,
        auction_pct:
          row?.state === "observed" ? auctionNumber(row.auction_pct) : null,
      };
    }),
    "auction_pct",
    "desc",
  );
  return (
    <div className="auction-table-stock-grid">
      {rows.map((row) => (
        <div className="auction-table-stock-item" key={row.instrument_id}>
          <div className="auction-table-stock-name">
            <button
              className="auction-stock"
              disabled={!onStock || !chartDate}
              onClick={() => onStock?.(row.instrument_id)}
            >
              {row.name}
            </button>
            {topIds?.has(row.instrument_id) && (
              <span className="auction-table-top-mark">前列</span>
            )}
          </div>
          <div className="auction-table-stock-footer">
            <CopyStockCode instrumentId={row.instrument_id} />
            <strong title="本次留存竞价涨幅" className={tone(row.auction_pct)}>
              {percent(row.auction_pct)}
            </strong>
          </div>
        </div>
      ))}
    </div>
  );
}
