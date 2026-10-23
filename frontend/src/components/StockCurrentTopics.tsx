import { stockCurrentMemberships } from "./stockMembershipPresentation";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import StockTopicManager from "./StockTopicManager";
import "./StockCurrentTopics.css";

export default function StockCurrentTopics({
  data,
  instrumentId,
}: {
  data: Record<string, any> | null;
  instrumentId: string;
}) {
  const [manage, setManage] = useState(false),
    [updated, setUpdated] = useState<Record<string, any> | null>(null),
    [notice, setNotice] = useState("");
  const activeInstrument = useRef(instrumentId);
  activeInstrument.current = instrumentId;
  useEffect(() => {
    setManage(false);
    setUpdated(null);
    setNotice("");
  }, [instrumentId]);
  const current = updated?.instrument_id === instrumentId ? updated : data;
  const view = stockCurrentMemberships(current, instrumentId);
  async function saved() {
    window.dispatchEvent(
      new CustomEvent("workbench-topics-changed", {
        detail: { instrument_id: instrumentId },
      }),
    );
    if (activeInstrument.current !== instrumentId) return;
    setManage(false);
    setNotice("题材归属已保存");
    try {
      const fresh = await api(
        `/stock-topic-memberships/${encodeURIComponent(instrumentId)}`,
      );
      if (activeInstrument.current === instrumentId) setUpdated(fresh);
    } catch (failure: any) {
      if (activeInstrument.current !== instrumentId) return;
      setNotice(
        `已保存，刷新归属失败：${failure.message}；可重新打开个股详情查看`,
      );
    }
  }
  const chip = (tag: Record<string, any>) => (
    <span
      className="stock-current-topic"
      data-kind={tag.kind}
      key={tag.topic_id || tag.name}
      title={`${tag.origin === "manual" || tag.kind === "manual" ? "人工研究归属" : `来源采集日 ${tag.source_observed_date || "待核验"}`} · ${tag.members_complete === true ? "当前分类成员完整" : "成员覆盖待核验"}；不是历史归因`}
    >
      {tag.name}
      {(tag.origin === "manual" || tag.kind === "manual") && (
        <small className="stock-topic-manual-label">人工</small>
      )}
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
        <button
          type="button"
          className="btn small"
          onClick={() => {
            setManage((value) => !value);
            setNotice("");
          }}
        >
          补充题材
        </button>
      </div>
      {manage && (
        <StockTopicManager
          key={instrumentId}
          instrumentId={instrumentId}
          currentIds={
            new Set(
              [...view.visible, ...view.additional].map((tag) => tag.topic_id),
            )
          }
          onClose={() => setManage(false)}
          onSaved={saved}
        />
      )}
      {notice && <small role="status">{notice}</small>}
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
