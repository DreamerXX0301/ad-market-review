import { useState } from "react";
import { Check, X } from "lucide-react";
import { post } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Badge, ErrorBox, Modal, Spinner, useToast } from "../../components/UI";
import { matchesTopic, topicKindLabel } from "./TopicDirectory";

export default function AssociationDialog({
  instruments,
  topics,
  onSaved,
  onClose,
}: {
  instruments: Row[];
  topics: Row[];
  onSaved: () => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(""),
    [kind, setKind] = useState("all"),
    [ids, setIds] = useState<string[]>([]),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const toast = useToast();
  const visible = topics.filter((topic) => matchesTopic(topic, query, kind));
  const selected = topics.filter((topic) => ids.includes(topic.topic_id));
  function toggle(id: string) {
    setError("");
    if (ids.includes(id)) setIds(ids.filter((value) => value !== id));
    else if (ids.length >= 20)
      setError("一次最多关联20个分类，请减少目标分类。");
    else setIds([...ids, id]);
  }
  async function save() {
    if (!ids.length || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await post("/topic-members/associate", {
        instrument_ids: instruments.map((row) => row.instrument_id),
        topic_ids: ids,
        reason: reason.trim() || "用户在题材库批量追加归属",
      });
      toast(
        `关联完成：新增 ${result.added_count ?? result.added?.length ?? 0} 项，已有归属 ${result.unchanged_count ?? result.unchanged?.length ?? 0} 项保持不变`,
      );
      onSaved();
      onClose();
    } catch (failure: any) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="关联多个题材 / 行业" wide onClose={() => !busy && onClose()}>
      <div className="modal-body topic-association-body">
        <div className="topic-association-summary">
          <strong>已选 {instruments.length} 只股票</strong>
          <span>仅追加归属，保留原有题材与行业；已存在的关系会跳过。</span>
        </div>
        <details className="topic-associated-stocks">
          <summary>查看待关联股票</summary>
          <div className="topic-selected-chips">
            {instruments.map((row) => (
              <Badge key={row.instrument_id}>
                {row.name || row.code || row.instrument_id} ·{" "}
                {row.code || row.instrument_id}
              </Badge>
            ))}
          </div>
        </details>
        <div className="topic-association-filters">
          <input
            type="search"
            aria-label="搜索关联分类"
            placeholder="搜索目标题材、行业或别名"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <select
            aria-label="关联分类类型"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="all">全部分类</option>
            <option value="concept">概念题材</option>
            <option value="industry">行业分类</option>
            <option value="manual">人工研究</option>
          </select>
        </div>
        <div className="topic-selected-chips" aria-label="待关联分类">
          {!selected.length ? (
            <span className="muted">尚未选择目标分类，请勾选后提交。</span>
          ) : (
            selected.map((topic) => (
              <button
                key={topic.topic_id}
                className="topic-selection-chip"
                onClick={() => toggle(topic.topic_id)}
                disabled={busy}
              >
                {topic.name}
                <X size={12} />
              </button>
            ))
          )}
        </div>
        <div className="topic-association-list">
          {visible.map((topic) => (
            <label
              key={topic.topic_id}
              className={topic.archived ? "archived" : ""}
            >
              <input
                type="checkbox"
                checked={ids.includes(topic.topic_id)}
                disabled={busy || !!topic.archived}
                onChange={() => toggle(topic.topic_id)}
              />
              <span>
                <strong>{topic.name}</strong>
                <small>{topicKindLabel(topic)}</small>
              </span>
              {!!topic.archived && <Badge tone="warn">已归档，不可关联</Badge>}
            </label>
          ))}
          {!visible.length && (
            <p className="muted">没有匹配分类；已选分类仍保留。</p>
          )}
        </div>
        <label>
          关联说明（可选）
          <input
            value={reason}
            maxLength={500}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            placeholder="记录研究依据或本次操作原因"
          />
        </label>
        {selected.length > 0 && (
          <div className="topic-association-review">
            将为 {instruments.length} 只股票追加 {selected.length}{" "}
            个分类，最多核对 {instruments.length * selected.length}{" "}
            项关系。归档分类不可写入，任一目标无效时整次操作失败。
          </div>
        )}
        {error && <ErrorBox message={error} />}
      </div>
      <div className="modal-footer">
        <button className="btn" disabled={busy} onClick={onClose}>
          取消
        </button>
        <button
          className="btn primary"
          disabled={busy || !ids.length}
          onClick={save}
        >
          {busy ? <Spinner /> : <Check size={14} />}确认追加归属
        </button>
      </div>
    </Modal>
  );
}
