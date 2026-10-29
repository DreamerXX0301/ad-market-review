import type { Row } from "../../lib/api";
import { noonRows } from "./noonPresentation";
import AuctionCopyText from "./AuctionCopyText";

export default function AuctionClassifications({
  view,
  date,
  instrumentId,
}: {
  view: Row | null;
  date: string;
  instrumentId: string;
}) {
  const item =
    view?.date === date && view.version === "auction-topic-summary-v1"
      ? view.stock_classifications?.[instrumentId]
      : null;
  function tags(values: unknown, label: string, kind: string, count: number) {
    const rows = noonRows(values).filter(
      (row) => typeof row.name === "string" && row.name.trim(),
    );
    if (!rows.length)
      return (
        <div className={`auction-affiliation-line ${kind}`}>
          <span className="auction-affiliation-label">{label}</span>
          <span className="auction-muted">待核验</span>
        </div>
      );
    const chip = (row: Row) => (
      <span
        className={`auction-affiliation-chip ${kind}`}
        key={`${row.source}:${row.topic_code}`}
        title={`${row.source} · ${row.topic_code} · 成员档案 ${row.members_observed_date}`}
      >
        <span>{row.name}</span>
        <AuctionCopyText
          text={row.topic_code}
          label={`复制${label}代码 ${row.topic_code}`}
        />
      </span>
    );
    return (
      <div className={`auction-affiliation-line ${kind}`}>
        <span className="auction-affiliation-label">{label}</span>
        <div className="auction-affiliation-values">
          {rows.slice(0, count).map(chip)}
          {rows.length > count && (
            <details className="auction-affiliations-more">
              <summary>+{rows.length - count}</summary>
              <div>{rows.slice(count).map(chip)}</div>
            </details>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="auction-affiliations" aria-label="行业与概念归属">
      {tags(item?.industries, "行业", "industry", 1)}
      {tags(item?.topics, "概念", "concept", 3)}
    </div>
  );
}
