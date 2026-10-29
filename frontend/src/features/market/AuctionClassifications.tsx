import type { Row } from "../../lib/api";
import { useState } from "react";
import { Modal } from "../../components/UI";
import { percent, tone } from "../../lib/format";
import { auctionNumber } from "./auctionPresentation";
import { noonRows } from "./noonPresentation";
import AuctionCopyText from "./AuctionCopyText";

export default function AuctionClassifications({
  view,
  date,
  instrumentId,
  compact = false,
}: {
  view: Row | null;
  date: string;
  instrumentId: string;
  compact?: boolean;
}) {
  const [opened, setOpened] = useState("");
  const identity = `${date}:${instrumentId}`;
  const item =
    view?.date === date && view.version === "auction-topic-summary-v1"
      ? view.stock_classifications?.[instrumentId]
      : null;
  const groups = new Map(
    noonRows(view?.date === date ? view.groups : []).map((row) => [
      `${row.source}:${row.kind}:${row.topic_code}`,
      row,
    ]),
  );
  function average(row: Row, kind: string) {
    return groups.get(`${row.source}:${kind}:${row.topic_code}`);
  }
  const industries = noonRows(item?.industries),
    concepts = noonRows(item?.topics);
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
        title={`${row.source} · ${row.topic_code} · 成员档案 ${row.members_observed_date} · 本次样本均幅，有效${average(row, kind)?.valid_count ?? "未知"}只`}
      >
        <span>{row.name}</span>
        <b
          className={tone(
            auctionNumber(average(row, kind)?.average_auction_pct),
          )}
        >
          {percent(auctionNumber(average(row, kind)?.average_auction_pct))}
        </b>
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
  if (compact)
    return (
      <>
        <button
          type="button"
          className="auction-affiliations-preview"
          onClick={() => setOpened(identity)}
          title="查看完整行业概念、样本均幅与代码"
        >
          <span className="industry">
            行业 {industries[0]?.name || "待核验"}
          </span>
          <span className="concept">概念 {concepts[0]?.name || "待核验"}</span>
          <small>+{Math.max(0, industries.length + concepts.length - 2)}</small>
        </button>
        {opened === identity && (
          <Modal
            title="行业 / 概念 · 本次样本均幅"
            onClose={() => setOpened("")}
          >
            <p className="auction-topic-note">
              成员档案归属，非上涨原因。均幅仅统计本次候选交集。
            </p>
            <div className="auction-affiliations">
              {tags(industries, "行业", "industry", industries.length)}
              {tags(concepts, "概念", "concept", concepts.length)}
            </div>
          </Modal>
        )}
      </>
    );
  return (
    <div className="auction-affiliations" aria-label="行业与概念归属">
      {tags(item?.industries, "行业", "industry", 1)}
      {tags(item?.topics, "概念", "concept", 3)}
    </div>
  );
}
