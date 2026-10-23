import { stockCurrentMemberships } from "./stockMembershipPresentation";
import "./StockCurrentTopics.css";

export default function StockCurrentTopics({
  data,
  instrumentId,
}: {
  data: Record<string, any> | null;
  instrumentId: string;
}) {
  const view = stockCurrentMemberships(data, instrumentId);
  const chip = (tag: Record<string, any>) => (
    <span
      className="stock-current-topic"
      data-kind={tag.kind}
      key={tag.topic_id || tag.name}
      title={`${tag.kind === "manual" ? "人工研究归属" : `来源采集日 ${tag.source_observed_date || "待核验"}`} · ${tag.members_complete === true ? "当前分类成员完整" : "成员覆盖待核验"}；不是历史归因`}
    >
      {tag.name}
    </span>
  );
  return (
    <section
      className="stock-current-topics"
      aria-label="当前行业与题材"
      title={view.scope}
    >
      <div className="stock-current-topics-heading">
        <span>行业 / 题材</span>
        <small>当前归属</small>
      </div>
      <div className="stock-current-topic-list">
        {view.visible.map(chip)}
        {!view.visible.length && (
          <span className="muted">
            {view.known
              ? "暂无已知归属，不代表不属于任何分类"
              : "归属资料待核验"}
          </span>
        )}
        {!!view.additional.length && (
          <details>
            <summary>另 {view.additional.length} 项</summary>
            <div className="stock-current-topic-list">
              {view.additional.map(chip)}
            </div>
          </details>
        )}
      </div>
      <small className="stock-current-topics-date">
        关系有效日 {view.observed || "待核验"} ·
        当前资料，非历史归因；悬停标签查看来源采集日
      </small>
    </section>
  );
}
