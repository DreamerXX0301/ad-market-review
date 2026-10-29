import type { Row } from "../../lib/api";
import { integer, percent, tone } from "../../lib/format";
import { noonRows } from "./noonPresentation";
import { auctionNumber } from "./auctionPresentation";
import { auctionAssociationCloseStats } from "./auctionAssociationCloseStats";

export default function AuctionAssociationSummary({
  view,
  date,
  closeReview,
  selectedKey = "",
  onSelect,
}: {
  view: Row | null;
  date: string;
  closeReview?: Row | null;
  selectedKey?: string;
  onSelect?: (key: string) => void;
}) {
  const closeMode = closeReview !== undefined;
  const groups =
    view?.date === date && view.version === "auction-topic-summary-v1"
      ? noonRows(view.groups)
      : [];
  const rows = ["industry", "concept"].flatMap((kind) =>
    groups
      .filter((row) => row.kind === kind && row.valid_count >= 3)
      .sort(
        (a, b) =>
          b.sample_count - a.sample_count ||
          (auctionNumber(b.average_auction_pct) ?? -Infinity) -
            (auctionNumber(a.average_auction_pct) ?? -Infinity) ||
          String(a.topic_code).localeCompare(String(b.topic_code)),
      )
      .slice(0, 2),
  );
  return (
    <div
      className="auction-association-summary"
      aria-label={closeMode ? "盘后行业概念收盘均幅" : "主要关联行业概念均幅"}
    >
      {rows.length ? (
        rows.map((row) => {
          const close = closeMode
            ? auctionAssociationCloseStats(row, closeReview, date)
            : null;
          const average = closeMode
            ? close!.average
            : auctionNumber(row.average_auction_pct);
          return (
            <button
              type="button"
              className={`auction-association-item${selectedKey === `${row.source}:${row.kind}:${row.topic_code}` ? " is-selected" : ""}`}
              key={`${row.source}:${row.kind}:${row.topic_code}`}
              aria-pressed={
                selectedKey === `${row.source}:${row.kind}:${row.topic_code}`
              }
              aria-label={`${row.name}，关联${integer(row.sample_count)}只，点击查看对应股票`}
              onClick={() =>
                onSelect?.(`${row.source}:${row.kind}:${row.topic_code}`)
              }
              title={`按本次候选关联股数选取，每类前2组；成员档案 ${row.members_observed_date}，非上涨原因或全板块涨幅；${close ? `收盘涨幅有效${close.valid}/${close.requested}只，相对原竞价来源前收` : "本次候选竞价均幅"}`}
            >
              <span className="auction-association-kind">
                {row.kind === "industry" ? "行业" : "概念"}
              </span>
              <strong>{row.name}</strong>
              <span className="auction-association-count">
                关联{integer(row.sample_count)}只
              </span>
              <b className={tone(average)}>
                {closeMode ? "收盘均" : "均"}
                {percent(average)}
              </b>
            </button>
          );
        })
      ) : (
        <span className="auction-muted">行业概念均幅待核验</span>
      )}
    </div>
  );
}
