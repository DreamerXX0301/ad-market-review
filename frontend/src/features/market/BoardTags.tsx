import type { Row } from "../../lib/api";
import { boardTags } from "./boardPresentation";
import type { BoardTag, BoardTagOptions } from "./boardPresentation";
import "./BoardTags.css";

export default function BoardTags({
  row,
  date,
  snapshotId,
  emptyLabel,
  ...options
}: BoardTagOptions & {
  row: Row;
  date: string;
  snapshotId: unknown;
  emptyLabel?: string;
}) {
  const tags = boardTags(row, date, snapshotId, options);
  if (!tags.length)
    return emptyLabel ? (
      <span className="board-tags-empty">{emptyLabel}</span>
    ) : null;
  const priority = [
    "one_price_limit_up",
    "one_price_limit_down",
    "earth_to_sky",
    "broken_pool_source",
    "limit_down",
    "repeated_break",
    "open_count",
    "pullback_reclaim",
    "non_one_price_limit_up",
  ];
  const types = tags
    .filter((tag) => tag.dimension === "type")
    .sort(
      (left, right) =>
        priority.indexOf(left.key.slice(5)) -
        priority.indexOf(right.key.slice(5)),
    );
  const extra = types.slice(3);
  const visible = tags.filter(
    (tag) => tag.dimension !== "type" || types.slice(0, 3).includes(tag),
  );
  const renderTag = (tag: BoardTag) => (
    <span
      key={tag.key}
      className={`board-tag ${tag.dimension} ${tag.tone}`}
      data-board-dimension={tag.dimension}
      data-board-tag={tag.key}
      title={tag.detail}
    >
      {tag.label}
    </span>
  );
  return (
    <div className="board-tags" aria-label="连板高度、当日板型、量能与反馈">
      {visible.map(renderTag)}
      {extra.length > 0 && (
        <details className="board-tags-more">
          <summary>另{extra.length}项</summary>
          <div>{extra.map(renderTag)}</div>
        </details>
      )}
    </div>
  );
}
