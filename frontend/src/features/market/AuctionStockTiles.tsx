import { useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type { Row } from "../../lib/api";
import CopyStockCode from "../../components/CopyStockCode";
import { integer, percent, tone } from "../../lib/format";
import AuctionStockBadges from "./AuctionStockBadges";
import {
  auctionStockBoardCounts,
  auctionStockListRows,
  type AuctionStockBoardKey,
  type AuctionStockSortKey,
} from "./auctionStockListPresentation";

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
  const [board, setBoard] = useState<AuctionStockBoardKey>("all");
  const [sort, setSort] = useState<{
    key: AuctionStockSortKey;
    direction: "asc" | "desc";
  }>({ key: "height", direction: "desc" });
  const boardCounts = auctionStockBoardCounts(ids, quotes);
  const total = boardCounts.find((item) => item.key === "all")?.count ?? 0;
  const rows = auctionStockListRows(
    ids,
    quotes,
    board,
    sort.key,
    sort.direction,
  );
  function selectSort(key: AuctionStockSortKey) {
    setSort((previous) => ({
      key,
      direction:
        previous.key === key && previous.direction === "desc" ? "asc" : "desc",
    }));
  }
  return (
    <div className="auction-stock-list">
      <div className="auction-stock-list-toolbar">
        <div className="auction-stock-list-sort" aria-label="展开个股排序">
          <span>排序</span>
          {(
            [
              ["height", "昨日板高"],
              ["change", "竞价涨幅"],
            ] as const
          ).map(([key, label]) => (
            <button
              type="button"
              key={key}
              className={sort.key === key ? "is-selected" : ""}
              aria-pressed={sort.key === key}
              onClick={() => selectSort(key)}
              title={
                sort.key === key
                  ? `当前${sort.direction === "desc" ? "降序" : "升序"}，再次点击切换`
                  : `按${label}降序排列`
              }
            >
              {label}
              {sort.key !== key ? (
                <ArrowUpDown size={12} />
              ) : sort.direction === "desc" ? (
                <ArrowDown size={12} />
              ) : (
                <ArrowUp size={12} />
              )}
            </button>
          ))}
        </div>
        <span
          className="auction-stock-list-count"
          role="status"
          aria-live="polite"
        >
          当前 {integer(rows.length)} / 本组 {integer(total)}只
        </span>
      </div>
      <div
        className="auction-stock-list-boards"
        aria-label="展开个股上市板块筛选"
      >
        <span>上市板块</span>
        {boardCounts.map((item) => (
          <button
            type="button"
            key={item.key}
            className={board === item.key ? "is-selected" : ""}
            aria-pressed={board === item.key}
            onClick={() => setBoard(board === item.key ? "all" : item.key)}
            title="仅筛选本组展示，不改变原分组统计"
          >
            {item.label} <b>{integer(item.count)}</b>
          </button>
        ))}
      </div>
      <p className="auction-stock-list-note">
        {sort.key === "height"
          ? "同板高按竞价涨幅降序；未核验值排在末尾。"
          : "按本次竞价涨幅排列；缺报价排在末尾。"}
      </p>
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
            <AuctionStockBadges row={row} />
            <div className="auction-table-stock-footer">
              <CopyStockCode instrumentId={row.instrument_id} />
              <strong
                title="本次留存竞价涨幅"
                className={tone(row.auction_pct)}
              >
                {percent(row.auction_pct)}
              </strong>
            </div>
          </div>
        ))}
      </div>
      {!rows.length && (
        <p className="auction-stock-list-empty" role="status">
          本组没有符合当前上市板块的股票，可切换到“全部”。
        </p>
      )}
    </div>
  );
}
