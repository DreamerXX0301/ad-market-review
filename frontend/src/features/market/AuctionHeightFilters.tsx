import type { Row } from "../../lib/api";
import { integer, percent, tone } from "../../lib/format";
import {
  auctionHeightSummaries,
  type AuctionHeightKey,
} from "./auctionHeightPresentation";
import "./AuctionHeightFilters.css";

export default function AuctionHeightFilters({
  rows,
  value,
  onChange,
}: {
  rows: Row[];
  value: AuctionHeightKey;
  onChange: (key: AuctionHeightKey) => void;
}) {
  return (
    <div className="auction-height-filter" aria-label="按昨日板高筛选">
      <div className="auction-height-heading">
        <strong>昨日板高</strong>
        <span>本次冻结样本 · 只统计有效竞价报价的均幅</span>
      </div>
      <div className="auction-height-options">
        {auctionHeightSummaries(rows).map((group) => (
          <button
            type="button"
            key={group.key}
            className={`auction-height-option${value === group.key ? " is-selected" : ""}`}
            aria-pressed={value === group.key}
            onClick={() =>
              onChange(
                value === group.key && group.key !== "all" ? "all" : group.key,
              )
            }
            title={`本份样本${group.count}只，竞价涨幅有效${group.valid_count}只、缺失${group.missing_count}只；不代表全市场`}
          >
            <span>
              <strong>{group.label}</strong>
              <b>{integer(group.count)}只</b>
            </span>
            <small>
              竞价均幅{" "}
              <em className={tone(group.average_auction_pct)}>
                {percent(group.average_auction_pct)}
              </em>
            </small>
          </button>
        ))}
      </div>
    </div>
  );
}
