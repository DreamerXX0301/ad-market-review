import type { Row } from "../../lib/api";
import {
  auctionHeightLabel,
  auctionPreviousHeight,
} from "./auctionHeightPresentation";
import "./AuctionHeightFilters.css";

export default function AuctionHeightBadge({ row }: { row: Row }) {
  const label = auctionHeightLabel(row);
  if (!label) return null;
  return (
    <span
      className="auction-height-badge"
      data-height={auctionPreviousHeight(row)}
      title="严格前一交易日已核验的收盘涨停高度，不代表今天已晋级"
    >
      {label}
    </span>
  );
}
