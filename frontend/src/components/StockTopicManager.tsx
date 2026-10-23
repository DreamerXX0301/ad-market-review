import { useState } from "react";
import { list, post, useApi } from "../lib/api";
import type { Row } from "../lib/api";

/** Additive current-day research edits; source catalog membership is preserved. */
export default function StockTopicManager({
  instrumentId,
  currentIds,
  onSaved,
  onClose,
}: {
  instrumentId: string;
  currentIds: Set<string>;
  onSaved: () => void;
  onClose: () => void;
}) {
  const topics = useApi<Row>("/topics");
  const [mode, setMode] = useState("existing"),
    [query, setQuery] = useState(""),
    [selected, setSelected] = useState(""),
    [name, setName] = useState(""),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const choices = list(topics.data).filter(
    (topic) =>
      !topic.archived &&
      !currentIds.has(topic.topic_id) &&
      [topic.name, ...(topic.aliases || [])]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query.trim().toLocaleLowerCase()),
  );
  async function save() {
    setError("");
    if (mode === "existing" && !selected) {
      setError("请先选择要补充的题材或行业");
      return;
    }
    if (mode === "new" && !name.trim()) {
      setError("请填写新题材名称");
      return;
    }
    setBusy(true);
    try {
      if (mode === "new")
        await post("/topics", {
          name: name.trim(),
          description: reason.trim(),
          instrument_ids: [instrumentId],
        });
      else
        await post("/topic-members/associate", {
          topic_ids: [selected],
          instrument_ids: [instrumentId],
          reason: reason.trim() || "用户在个股详情补充当前题材",
        });
      onSaved();
    } catch (failure: any) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="stock-topic-manager" aria-label="补充个股题材">
      <div className="stock-topic-manager-heading">
        <strong>补充题材 · {instrumentId.split(".")[0]}</strong>
        <button
          type="button"
          className="btn small"
          disabled={busy}
          onClick={onClose}
        >
          收起
        </button>
      </div>
      <label>
        补充方式
        <select
          aria-label="补充题材方式"
          value={mode}
          disabled={busy}
          onChange={(event) => {
            setMode(event.target.value);
            setError("");
          }}
        >
          <option value="existing">加入已有题材 / 行业</option>
          <option value="new">创建人工题材并加入</option>
        </select>
      </label>
      {mode === "existing" ? (
        <>
          <input
            type="search"
            aria-label="搜索可加入题材"
            placeholder="搜索题材、行业或别名"
            value={query}
            disabled={busy}
            onChange={(event) => setQuery(event.target.value)}
          />
          <select
            aria-label="要补充的题材"
            value={selected}
            disabled={busy || topics.loading}
            onChange={(event) => setSelected(event.target.value)}
          >
            <option value="">
              {topics.loading
                ? "正在读取本地目录…"
                : "选择题材 / 行业（已归属项不重复添加）"}
            </option>
            {selected &&
              !choices.some((topic) => topic.topic_id === selected) && (
                <option value={selected}>
                  {list(topics.data).find(
                    (topic) => topic.topic_id === selected,
                  )?.name || selected}{" "}
                  · 已选择
                </option>
              )}
            {choices.map((topic) => (
              <option value={topic.topic_id} key={topic.topic_id}>
                {topic.name} ·{" "}
                {topic.kind === "industry"
                  ? "行业"
                  : topic.kind === "manual"
                    ? "人工题材"
                    : "概念"}
              </option>
            ))}
          </select>
          {topics.error && (
            <p role="alert">
              {topics.error}{" "}
              <button
                type="button"
                className="btn small"
                onClick={topics.reload}
              >
                重试
              </button>
            </p>
          )}
        </>
      ) : (
        <label>
          新题材名称
          <input
            aria-label="新题材名称"
            maxLength={100}
            placeholder="例如：自主研究方向"
            disabled={busy}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
      )}
      <label>
        依据 / 备注（选填）
        <input
          maxLength={500}
          placeholder="记录补充依据或资料出处"
          value={reason}
          disabled={busy}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      <small>
        从当前业务日生效，标记为人工补充；保留原有归属，不改写历史事件或历史回测。
      </small>
      {error && <p role="alert">{error}</p>}
      <div>
        <button
          type="button"
          className="btn primary small"
          disabled={
            busy || (mode === "existing" && (topics.loading || !!topics.error))
          }
          onClick={save}
        >
          {busy
            ? "正在保存…"
            : mode === "new"
              ? "创建题材并加入"
              : "保存题材归属"}
        </button>
      </div>
    </section>
  );
}
