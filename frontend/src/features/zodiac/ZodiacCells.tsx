import type { Row } from "../../lib/api";
import { num, percent, tone } from "../../lib/format";
import {
  YearLimitCountCell,
  RecentLimitCell,
} from "../topics/MemberHistoryCells";
import type { Column } from "./zodiacPresentation";

export function Memberships({ row }: { row: Row }) {
  const tags: Row[] = row.current_topics || [];
  const industries = tags.filter((t) => t.kind === "industry"),
    concepts = tags.filter((t) => t.kind !== "industry");
  const chip = (tag: Row) => (
    <span
      className="zodiac-tag"
      key={tag.topic_id}
      title={`来源采集日 ${tag.source_observed_date || "未知"}；当前归属，非历史成员证明`}
    >
      {tag.name}
      {tag.manual_override === "include" ? " · 人工" : ""}
    </span>
  );
  return (
    <div className="zodiac-memberships" title={row.current_topics_scope}>
      <div>
        <small>行业</small>
        {industries.length ? (
          industries.map(chip)
        ) : (
          <span className="muted">未知</span>
        )}
      </div>
      <div>
        <small>概念</small>
        {concepts.length ? (
          concepts.slice(0, 2).map(chip)
        ) : (
          <span className="muted">暂无已知归属</span>
        )}
      </div>
      {concepts.length > 2 && (
        <details>
          <summary>全部 {concepts.length} 项</summary>
          {concepts.map(chip)}
        </details>
      )}
    </div>
  );
}
export function MetricCell({ row, column }: { row: Row; column: Column }) {
  const value = row[column.key];
  const reason = column.reason
    ? row[column.reason]
    : column.key === "entry_price"
      ? row.entry_basis?.reason
      : undefined;
  if (column.type === "topics") return <Memberships row={row} />;
  if (column.type === "limit") return <YearLimitCountCell row={row} />;
  if (column.type === "recent") return <RecentLimitCell row={row} />;
  if (
    column.key === "limit_up_30d" &&
    value == null &&
    row.limit_up_30d_observed > 0
  )
    return <span title={reason}>已确认≥{row.limit_up_30d_observed}次</span>;
  const known =
    value !== null &&
    value !== undefined &&
    (typeof value !== "number" || Number.isFinite(value));
  const shown = !known
    ? "—"
    : column.type === "percent"
      ? percent(value)
      : column.type === "number"
        ? `${num(column.unit === "亿元" ? value / 1e8 : value, column.unit === "股" || column.unit === "交易日" ? 0 : 2)}${column.unit ? " " + column.unit : ""}`
        : typeof value === "boolean"
          ? value
            ? "已确认"
            : column.key === "today_limit_down"
              ? "已核验未跌停"
              : "已核验未涨停"
          : String(value);
  return (
    <div
      className="zodiac-metric"
      title={[
        reason,
        `截止 ${row.metrics_as_of || "待发布"}`,
        column.key.startsWith("ma") || column.key.startsWith("boll")
          ? `技术价格口径 ${row.technical_price_basis || "待核验"}`
          : null,
      ]
        .filter(Boolean)
        .join("；")}
    >
      <span
        className={
          known && column.type === "percent"
            ? tone(value)
            : known
              ? ""
              : "muted"
        }
      >
        {shown}
      </span>
      {!known && <small>待核验</small>}
    </div>
  );
}
