import type { Row } from "../../lib/api";
import { Badge, Empty, ErrorBox, Loading, Panel } from "../../components/UI";

export function topicKindLabel(topic: Row) {
  return topic.origin === "manual" || topic.kind === "manual"
    ? "人工研究"
    : topic.kind === "industry"
      ? "行业分类"
      : "概念题材";
}

export function matchesTopic(topic: Row, query: string, kind: string) {
  const typeMatches =
    kind === "all" ||
    (kind === "manual" ? topic.origin === "manual" : topic.kind === kind);
  const content = [topic.name, topic.description, ...(topic.aliases || [])]
    .join(" ")
    .toLocaleLowerCase();
  return (
    typeMatches &&
    (!query.trim() || content.includes(query.trim().toLocaleLowerCase()))
  );
}

export default function TopicDirectory({
  topics,
  selectedIds,
  query,
  kind,
  includeArchived,
  loading,
  error,
  onQuery,
  onKind,
  onArchived,
  onSelect,
  onToggle,
  onReload,
}: {
  topics: Row[];
  selectedIds: string[];
  query: string;
  kind: string;
  includeArchived: boolean;
  loading: boolean;
  error: string;
  onQuery: (value: string) => void;
  onKind: (value: string) => void;
  onArchived: (value: boolean) => void;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  onReload: () => void;
}) {
  const visible = topics.filter(
    (topic) =>
      (includeArchived || !topic.archived) && matchesTopic(topic, query, kind),
  );
  return (
    <Panel
      title="题材与行业目录"
      subtitle={`${visible.length} 个当前筛选分类 · 已选 ${selectedIds.length} / 20`}
      className="topic-sidebar"
    >
      <div className="panel-filter">
        <select
          aria-label="题材分类"
          value={kind}
          onChange={(event) => onKind(event.target.value)}
        >
          <option value="all">全部分类</option>
          <option value="concept">概念题材</option>
          <option value="industry">行业分类</option>
          <option value="manual">人工研究</option>
        </select>
        <input
          type="search"
          aria-label="搜索题材或行业"
          placeholder="搜索题材、行业或别名"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => onArchived(event.target.checked)}
          />
          包括已归档
        </label>
        <small className="topic-directory-help">
          点击名称单选；勾选方框联合查股。搜索与分类切换保留已选项。
        </small>
      </div>
      {error ? (
        <ErrorBox message={error} retry={onReload} />
      ) : loading ? (
        <Loading />
      ) : !visible.length ? (
        <Empty
          title="没有匹配分类"
          description={
            topics.length
              ? "调整搜索、分类或归档筛选，已选分类仍保留。"
              : "导入本地目录、同步题材库，或新增人工研究题材。"
          }
        />
      ) : (
        <div className="topic-directory-list">
          {visible.map((topic) => (
            <div
              key={topic.topic_id}
              className={
                "topic-directory-row " +
                (selectedIds.includes(topic.topic_id) ? "active" : "")
              }
            >
              <input
                type="checkbox"
                aria-label={"联合选择" + topic.name}
                checked={selectedIds.includes(topic.topic_id)}
                onChange={() => onToggle(topic.topic_id)}
              />
              <button
                onClick={() => onSelect(topic.topic_id)}
                aria-label={"查看" + topic.name + "成员"}
              >
                <span>
                  <strong>{topic.name}</strong>
                  <small>
                    {topicKindLabel(topic)}
                    {topic.archived ? " · 已归档" : ""}
                  </small>
                </span>
                <Badge>
                  {topic.member_count == null
                    ? topic.observed_member_count > 0
                      ? "未完整"
                      : "未同步"
                    : topic.member_count}
                </Badge>
              </button>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
