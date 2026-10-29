import type { Row } from "../../lib/api";
import AuctionHeightBadge from "./AuctionHeightBadge";
import {
  auctionStockBoard,
  auctionStockEvent,
} from "./auctionStockBadgePresentation";
import "./AuctionStockBadges.css";

export default function AuctionStockBadges({
  row,
  showEvent = true,
}: {
  row: Row;
  showEvent?: boolean;
}) {
  const event = showEvent ? auctionStockEvent(row) : null;
  const board = auctionStockBoard(row);
  return (
    <span className="auction-stock-badges">
      {event?.kind === "height" ? (
        <AuctionHeightBadge row={row} />
      ) : event ? (
        <span
          className={`auction-stock-event ${event.kind}`}
          data-previous-event={event.kind}
          title="严格前一交易日已核验的收盘事件，不代表今天已涨停或炸板"
        >
          {event.label}
        </span>
      ) : null}
      <span
        className={`auction-stock-board${board.key === "unknown" ? " is-unknown" : ""}`}
        data-board={board.key}
        title={board.title}
      >
        {board.label}
      </span>
    </span>
  );
}
