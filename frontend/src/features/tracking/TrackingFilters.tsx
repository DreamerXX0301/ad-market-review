import { useState } from "react";
import { X } from "lucide-react";
import type { Row } from "../../lib/api";
import { Badge } from "../../components/UI";
import "./Tracking.css";

export default function TrackingFilters({
  topics,
  selected,
  onSelected,
  match,
  onMatch,
  query,
  onQuery,
  includeUnknown,
  onIncludeUnknown,
  unknownCount,
  scope,
}: {
  topics: Row[];
  selected: string[];
  onSelected: (ids: string[]) => void;
  match: "any" | "all";
  onMatch: (value: "any" | "all") => void;
  query: string;
  onQuery: (value: string) => void;
  includeUnknown: boolean;
  onIncludeUnknown: (value: boolean) => void;
  unknownCount: number;
  scope?: string;
}) {
  const [topicQuery, setTopicQuery] = useState("");
  const needle = topicQuery.trim().toLocaleLowerCase();
  const visible = topics.filter(
    (topic) =>
      !selected.includes(topic.topic_id) &&
      [topic.name, ...(topic.aliases || [])]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle),
  );
  return (
    <div className="tracking-filters">
      <div className="tracking-filter-fields">
        <label>
          搜索股票 / 策略
          <input
            type="search"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="名称、代码或策略名称"
          />
        </label>
        <label>
          搜索题材 / 行业
          <input
            type="search"
            value={topicQuery}
            onChange={(event) => setTopicQuery(event.target.value)}
            placeholder="题材、行业或别名"
          />
        </label>
        <label>
          添加筛选分类（最多20项）
          <select
            value=""
            disabled={selected.length >= 20}
            onChange={(event) => {
              if (event.target.value && !selected.includes(event.target.value))
                onSelected([...selected, event.target.value]);
            }}
          >
            <option value="">
              {visible.length
                ? `选择分类 · ${visible.length}项`
                : "没有匹配的未选分类"}
            </option>
            {visible.map((topic) => (
              <option key={topic.topic_id} value={topic.topic_id}>
                {topic.name} ·{" "}
                {topic.kind === "industry"
                  ? "行业"
                  : topic.origin === "manual"
                    ? "人工题材"
                    : "概念"}
                {topic.members_complete !== true ? " · 成员未齐" : ""}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!!selected.length && (
        <div className="tracking-topic-selection">
          {selected.map((id) => (
            <button
              className="btn small"
              key={id}
              onClick={() =>
                onSelected(selected.filter((value) => value !== id))
              }
              title="移除筛选分类"
            >
              {topics.find((topic) => topic.topic_id === id)?.name || id}
              <X size={11} />
            </button>
          ))}
          <button className="link" onClick={() => onSelected([])}>
            清空分类
          </button>
          {selected.length > 1 && (
            <div className="segmented">
              <button
                className={match === "any" ? "active" : ""}
                onClick={() => onMatch("any")}
              >
                任一满足
              </button>
              <button
                className={match === "all" ? "active" : ""}
                onClick={() => onMatch("all")}
              >
                全部满足
              </button>
            </div>
          )}
        </div>
      )}
      <div className="tracking-filter-scope">
        <span title={scope}>
          按当前业务日有效题材 /
          行业归属筛选；表头排序覆盖当前筛选的全部已加载记录。
        </span>
        {!!selected.length && (
          <>
            <Badge tone={unknownCount ? "warn" : "neutral"}>
              归属待核验 {unknownCount} 条
            </Badge>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={includeUnknown}
                onChange={(event) => onIncludeUnknown(event.target.checked)}
              />
              包含归属待核验
            </label>
          </>
        )}
      </div>
    </div>
  );
}
