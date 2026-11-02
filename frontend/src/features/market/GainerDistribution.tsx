import { useState } from "react";
import type { Row } from "../../lib/api";
import { integer } from "../../lib/format";
import "./GainerDistribution.css";

export function distributionRows(value: unknown) {
  return (Array.isArray(value) ? value : [])
    .filter(
      (item) =>
        item &&
        typeof item.id === "string" &&
        typeof item.name === "string" &&
        typeof item.count === "number" &&
        Number.isFinite(item.count) &&
        item.count > 0 &&
        typeof item.share_pct === "number" &&
        Number.isFinite(item.share_pct) &&
        item.share_pct >= 0 &&
        item.share_pct <= 100,
    )
    .slice(0, 5);
}

export default function GainerDistribution({ data }: { data?: Row }) {
  const [kind, setKind] = useState<"topics" | "industries">("topics");
  if (!data) return null;
  const group = data[kind] || {};
  const entries = distributionRows(group.top);
  const repeatedNames = new Set(
    entries
      .filter((entry, index) =>
        entries.some(
          (other, position) => position !== index && other.name === entry.name,
        ),
      )
      .map((entry) => entry.name),
  );
  const hasSample =
    typeof data.sample_count === "number" && data.sample_count > 0;
  return (
    <section
      className="gainer-distribution"
      aria-label="近30日涨幅前1000分类分布"
    >
      <div className="gainer-distribution-heading">
        <div>
          <strong>强势股集中在哪些方向</strong>
          <span>
            原始涨幅排名前 {integer(data.sample_limit)} · 实际{" "}
            {integer(data.sample_count)} 只
          </span>
        </div>
        <div className="segmented" aria-label="分布分类">
          {(
            [
              ["topics", "题材"],
              ["industries", "行业"],
            ] as const
          ).map(([value, title]) => (
            <button
              type="button"
              key={value}
              className={kind === value ? "active" : ""}
              aria-pressed={kind === value}
              onClick={() => setKind(value)}
            >
              {title}
            </button>
          ))}
        </div>
      </div>
      {entries.length ? (
        <ol className="gainer-distribution-bars">
          {entries.map((item, index) => (
            <li key={item.id}>
              <span
                className="gainer-distribution-name"
                title={`${item.name} · ${item.source || "来源待核验"}`}
              >
                <small>{index + 1}</small>
                <span>
                  {item.name}
                  {repeatedNames.has(item.name) &&
                  (item.source_code || item.source)
                    ? ` · ${item.source_code || item.source}`
                    : ""}
                </span>
              </span>
              <span className="gainer-distribution-track" aria-hidden="true">
                <span style={{ width: `${item.share_pct}%` }} />
              </span>
              <span className="gainer-distribution-value">
                <b>{integer(item.count)} 只</b>
                <span>{item.share_pct.toFixed(1)}%</span>
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="gainer-distribution-empty">
          {hasSample
            ? "该样本的分类尚未核实，暂不展示分布。"
            : "所选日没有可排名样本，暂不展示分布。"}
        </p>
      )}
      <div className="gainer-distribution-footnote">
        <span>
          {data.date} · 已知 {integer(group.classified_count)} 只 · 标签未知{" "}
          {integer(group.unknown_count)} 只
          {group.attribute_only_count > 0
            ? ` · 仅资格标签 ${integer(group.attribute_only_count)} 只`
            : ""}
        </span>
        <span title={data.classification_scope}>
          最多展示 5 类 · 多分类可重叠，合计可能超过 100%
        </span>
        {kind === "topics" && data.excluded_attributes && (
          <span title={group.definition}>
            未计入融资融券、沪深股通等交易资格标签
          </span>
        )}
      </div>
    </section>
  );
}
