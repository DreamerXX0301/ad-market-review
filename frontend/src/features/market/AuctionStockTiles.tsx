import AuctionClassifications from "./AuctionClassifications";
import AuctionExplorerSelect from "./AuctionExplorerSelect";
import {
  AUCTION_HEIGHT_OPTIONS,
  auctionHeightMatches,
  type AuctionHeightKey,
} from "./auctionHeightPresentation";
import { auctionStockEvent } from "./auctionStockBadgePresentation";
import { auctionNumber } from "./auctionPresentation";
import { useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type { Row } from "../../lib/api";
import CopyStockCode from "../../components/CopyStockCode";
import { amount, num, integer, percent, tone } from "../../lib/format";
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
  explorer,
}: {
  ids: string[];
  quotes: Row[];
  onStock?: (id: string) => void;
  chartDate?: string;
  topIds?: Set<string>;
  explorer?: {
    date: string;
    topics: Row | null;
    visibleIds?: ReadonlySet<string>;
    anchorId?: string;
    group?: Row;
  };
}) {
  const [board, setBoard] = useState<AuctionStockBoardKey>("all");
  const [sort, setSort] = useState<{
    key: AuctionStockSortKey;
    direction: "asc" | "desc";
  }>({ key: "height", direction: "desc" });
  const [query, setQuery] = useState("");
  const [event, setEvent] = useState("all");
  const boardCounts = auctionStockBoardCounts(ids, quotes);
  const total = boardCounts.find((item) => item.key === "all")?.count ?? 0;
  const rows = auctionStockListRows(
    ids,
    quotes,
    board,
    sort.key,
    sort.direction,
  ).filter((row) => {
    if (!explorer) return true;
    if (explorer.visibleIds && !explorer.visibleIds.has(row.instrument_id))
      return false;
    if (
      event === "broken"
        ? auctionStockEvent(row)?.kind !== "limit_break"
        : !auctionHeightMatches(row, event as AuctionHeightKey)
    )
      return false;
    const related =
      explorer.topics?.date === explorer.date
        ? explorer.topics?.stock_classifications?.[row.instrument_id]
        : null;
    const labels = [
      ...(related?.industries || []),
      ...(related?.topics || []),
    ].map((item: Row) => item.name);
    return [
      row.name,
      row.instrument_id,
      ...labels,
      auctionStockEvent(row)?.label,
    ]
      .join(" ")
      .toLowerCase()
      .includes(query.trim().toLowerCase());
  });
  function selectSort(key: AuctionStockSortKey) {
    setSort((previous) => ({
      key,
      direction:
        previous.key === key && previous.direction === "desc" ? "asc" : "desc",
    }));
  }
  return (
    <div className="auction-stock-list">
      {explorer && (
        <div className="auction-explorer-stock-filters">
          <label>
            组内搜索
            <input
              aria-label="组内搜索"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="个股名称、代码或行业概念标签"
            />
          </label>
          <label>
            昨日事件
            <AuctionExplorerSelect
              aria-label="昨日事件"
              value={event}
              onChange={(e) => setEvent(e.target.value)}
            >
              {AUCTION_HEIGHT_OPTIONS.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
              <option value="broken">昨日炸板</option>
            </AuctionExplorerSelect>
          </label>
        </div>
      )}
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
            {explorer?.anchorId === row.instrument_id && (
              <small className="auction-explorer-height">观察锚股 · 手选</small>
            )}
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
            {explorer && (
              <>
                <p className="auction-explorer-quote">
                  竞价价{" "}
                  {num(
                    row.state === "observed"
                      ? auctionNumber(row.auction_price)
                      : null,
                  )}
                  元 · 竞价额{" "}
                  {amount(
                    row.state === "observed"
                      ? auctionNumber(row.auction_amount)
                      : null,
                  )}
                  <br />
                  来源量比{" "}
                  {num(
                    row.state === "observed"
                      ? auctionNumber(row.auction_volume_ratio)
                      : null,
                  )}
                  倍
                </p>
                <AuctionClassifications
                  view={explorer.topics}
                  date={explorer.date}
                  instrumentId={row.instrument_id}
                  focusGroup={explorer.group}
                />
              </>
            )}
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
          {explorer
            ? "本组没有符合当前条件的股票，可清空搜索或切换到“全部”。"
            : "本组没有符合当前上市板块的股票，可切换到“全部”。"}
        </p>
      )}
    </div>
  );
}
