import { useState } from "react";
import type { Row } from "../../lib/api";
import { list, useApi } from "../../lib/api";

export default function TopicSelect({
  topics,
  value,
  onChange,
  label = "题材",
}: {
  topics: Row[];
  value: string;
  onChange: (value: string) => void;
  label?: string;
}) {
  const [query, setQuery] = useState("");
  const [hotRequested, setHotRequested] = useState(false),
    [hotOpen, setHotOpen] = useState(false);
  const hot = useApi<Row>(hotRequested ? "/topic-hotspots?limit=12" : null);
  const shortcuts: Row[] = list(hot.data).flatMap<Row>((item) => {
    const existing = topics.find((topic) => topic.topic_id === item.topic_id);
    return existing ? [{ ...item, name: existing.name }] : [];
  });
  const needle = query.trim().toLocaleLowerCase();
  const visible = topics.filter((topic) =>
    [
      topic.name,
      topic.topic_id,
      ...(Array.isArray(topic.aliases) ? topic.aliases : []),
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes(needle),
  );
  const selected = topics.find((topic) => topic.topic_id === value);
  const selectedHidden =
    value && !visible.some((topic) => topic.topic_id === value);
  const kind = (topic: Row) =>
    topic.kind === "industry"
      ? "行业"
      : topic.kind === "manual" || topic.origin === "manual"
        ? "人工题材"
        : "概念";
  return (
    <div style={{ minWidth: 0, flex: "1 1 220px", display: "grid", gap: 5 }}>
      <input
        type="search"
        aria-label={`搜索${label}`}
        placeholder="搜索题材、行业或别名"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <select
        aria-label={label}
        style={{ width: "100%", maxWidth: "100%" }}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">
          {visible.length ? "选择题材或行业" : "没有匹配题材，请调整搜索"}
        </option>
        {selectedHidden && (
          <option value={value}>
            {selected?.name || value} · 已选择，搜索外保留
          </option>
        )}
        {visible.map((topic) => (
          <option value={topic.topic_id} key={topic.topic_id}>
            {topic.name} · {kind(topic)}
          </option>
        ))}
      </select>
      <small className="muted" role="status">
        匹配 {visible.length} / {topics.length} 项
        {selected ? ` · 已选 ${selected.name}` : ""}
      </small>
      <div>
        <button
          type="button"
          className="btn small"
          aria-expanded={hotOpen}
          onClick={() => {
            setHotRequested(true);
            setHotOpen((open) => !open);
          }}
        >
          {hotOpen ? "收起近月热点" : "近月热点快捷选择"}
        </button>
      </div>
      {hotOpen && (
        <div style={{ display: "grid", gap: 6 }}>
          <small className="muted">
            近20个交易日已确认涨停股票日数 · 当前成员代理
          </small>
          {hot.loading ? (
            <small role="status">正在核对已留存事件…</small>
          ) : hot.error ? (
            <small role="alert">{hot.error}</small>
          ) : shortcuts.length ? (
            <div className="button-group" style={{ flexWrap: "wrap" }}>
              {shortcuts.map((item) => (
                <button
                  type="button"
                  className={
                    "btn small" + (item.topic_id === value ? " primary" : "")
                  }
                  key={item.topic_id}
                  onClick={() => onChange(item.topic_id)}
                  title={`${item.event_count}个涨停股票日、${item.active_stock_count}只股票；同股同日去重。当前有效成员映射，非当时归属。`}
                >
                  {item.name} · {item.event_count}
                </button>
              ))}
            </div>
          ) : (
            <small>暂无可选热点；不会用静态题材或资金猜测填充。</small>
          )}
          {hot.data && (
            <small className="muted">
              {hot.data.window?.start || "—"} → {hot.data.business_date || "—"}{" "}
              · 来源完整日 {hot.data.coverage?.source_complete_days ?? 0}/
              {hot.data.coverage?.expected_trade_days ?? 0}。
              历史题材成员未留存，排名仅为已确认事件下界，覆盖缺口可能改变顺序。
            </small>
          )}
          <button
            type="button"
            className="btn small"
            disabled={hot.loading}
            onClick={hot.reload}
          >
            刷新热点
          </button>
        </div>
      )}
    </div>
  );
}
