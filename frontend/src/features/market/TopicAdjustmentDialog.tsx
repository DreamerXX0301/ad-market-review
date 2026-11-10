import { useRef, useState } from "react";
import { Empty, ErrorBox, Loading, Modal, useToast } from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import { post, useApi } from "../../lib/api";
import { ladderSearch } from "./ladderPresentation";
import type { LadderSearchNames } from "./ladderPresentation";
import "./TopicAdjustmentDialog.css";

export const topicKinds = { concept: "概念", industry: "行业" } as const;
export type AdjustmentTopic = {
  topic_id: string;
  name: string;
  kind: keyof typeof topicKinds;
};
export type ManualTopicOverride = AdjustmentTopic & { revision: string };
type AdjustmentStock = {
  instrument_id: string;
  name: string;
  height: number | null;
  mainboard: boolean;
  in_scope?: boolean;
  reason: string | null;
  automatic: AdjustmentTopic | null;
  override: ManualTopicOverride | null;
};
type EditorData = {
  date: string;
  stocks: AdjustmentStock[];
  topics: AdjustmentTopic[];
  search_names?: LadderSearchNames;
};
type Target =
  | { topic_id: string; kind: null; name: null }
  | { topic_id: null; kind: AdjustmentTopic["kind"]; name: string }
  | { topic_id: null; kind: null; name: null };

const endpoint = "/market/ladder/topic-adjustments";
const topicLabel = (value?: AdjustmentTopic | null) =>
  value ? topicKinds[value.kind] + " · " + value.name : "无";

function AdjustmentFields({
  stock,
  topics,
  searchNames,
  disabled,
  onSave,
}: {
  stock: AdjustmentStock;
  topics: AdjustmentTopic[];
  searchNames?: LadderSearchNames;
  disabled: boolean;
  onSave: (target: Target) => void;
}) {
  const [selection, setSelection] = useState(stock.override?.topic_id ?? "");
  const [topicQuery, setTopicQuery] = useState("");
  const [kind, setKind] = useState<AdjustmentTopic["kind"]>("concept");
  const [name, setName] = useState("");
  const existing = topics.some((topic) => topic.topic_id === selection)
    ? topics
    : stock.override && stock.override.topic_id === selection
      ? [stock.override, ...topics]
      : topics;
  const topicSearch = ladderSearch(topicQuery, searchNames);
  const matches = topicSearch.results(
    existing,
    (topic, typo) =>
      topicSearch.label(topic.name, typo) ||
      topicSearch.label(topicKinds[topic.kind]),
  );
  const selectedTopic = existing.find((topic) => topic.topic_id === selection);
  const choices =
    selectedTopic && !matches.includes(selectedTopic)
      ? [selectedTopic, ...matches]
      : matches;
  const target: Target | null =
    selection === "new"
      ? name.trim()
        ? { topic_id: null, kind, name: name.trim() }
        : null
      : existing.some((topic) => topic.topic_id === selection) &&
          stock.override?.topic_id !== selection
        ? { topic_id: selection, kind: null, name: null }
        : null;
  return (
    <div className="topic-adjustment-fields">
      <dl className="topic-adjustment-evidence">
        <div>
          <dt>自动归类</dt>
          <dd>{topicLabel(stock.automatic)}</dd>
        </div>
        <div>
          <dt>现有手工归类</dt>
          <dd>{topicLabel(stock.override)}</dd>
        </div>
        <div>
          <dt>原始涨停原因</dt>
          <dd>{stock.reason?.trim() || "来源暂无明确原因"}</dd>
        </div>
      </dl>
      {stock.in_scope === false && (
        <p className="topic-adjustment-caption">
          这只股票已不在当天确认样本中，仍可恢复自动归类。
        </p>
      )}
      <div className="topic-adjustment-options">
        <label>
          搜索已有归类
          <input
            type="search"
            value={topicQuery}
            disabled={disabled || stock.in_scope === false}
            onChange={(event) => setTopicQuery(event.target.value)}
            placeholder="题材、拼音或首字母"
          />
        </label>
        <label>
          调整到
          <select
            value={selection}
            onChange={(event) => setSelection(event.target.value)}
            disabled={disabled || stock.in_scope === false}
          >
            <option value="">
              {matches.length ? "选择已有方向" : "暂无匹配，可新建归类"}
            </option>
            {choices.map((topic) => (
              <option key={topic.topic_id} value={topic.topic_id}>
                {topicLabel(topic)}
              </option>
            ))}
            <option value="new">新建归类名称…</option>
          </select>
        </label>
        {selection === "new" && (
          <div className="form-grid">
            <label>
              新建类型
              <select
                value={kind}
                disabled={disabled}
                onChange={(event) =>
                  setKind(event.target.value as AdjustmentTopic["kind"])
                }
              >
                <option value="concept">概念</option>
                <option value="industry">行业</option>
              </select>
            </label>
            <label>
              新建名称
              <input
                value={name}
                disabled={disabled}
                onChange={(event) => setName(event.target.value)}
                autoComplete="off"
              />
            </label>
          </div>
        )}
      </div>
      <div className="topic-adjustment-actions">
        <button
          type="button"
          className="btn"
          disabled={disabled || !stock.override}
          onClick={() => onSave({ topic_id: null, kind: null, name: null })}
        >
          恢复自动归类
        </button>
        <button
          type="button"
          className="btn primary"
          disabled={disabled || !target || stock.in_scope === false}
          onClick={() => target && onSave(target)}
        >
          保存调整
        </button>
      </div>
    </div>
  );
}

export default function TopicAdjustmentDialog({
  date,
  initialStock,
  mainboardOnly = false,
  onClose,
}: {
  date: string;
  initialStock?: string;
  mainboardOnly?: boolean;
  onClose: () => void;
}) {
  const { data, error, loading, reload } = useApi<EditorData>(
    endpoint + "?" + new URLSearchParams({ date }),
  );
  const [stockId, setStockId] = useState(initialStock ?? "");
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState(false);
  const inFlight = useRef(false);
  const toast = useToast();
  const current = data?.date === date ? data : null;
  const stocks = (current?.stocks ?? []).filter(
    (stock) => !mainboardOnly || stock.mainboard,
  );
  const stock = stocks.find((item) => item.instrument_id === stockId);
  const search = ladderSearch(query, current?.search_names);
  const matches = search.results(
    stocks,
    (item, typo) =>
      search.label(item.name, typo) || search.contains(item.instrument_id),
  );
  const choices =
    stock && !matches.includes(stock) ? [stock, ...matches] : matches;

  async function save(target: Target) {
    if (!stock || inFlight.current || conflict || loading || error) return;
    inFlight.current = true;
    setSaving(true);
    setSaveError("");
    try {
      await post(endpoint, {
        date,
        instrument_id: stock.instrument_id,
        ...target,
        expected_revision: stock.override?.revision ?? null,
      });
      toast(
        target.topic_id || target.name ? "已保存手工归类" : "已恢复自动归类",
      );
      onClose();
    } catch (cause) {
      const changed =
        cause instanceof Error && "status" in cause && cause.status === 409;
      setConflict(changed);
      setSaveError(
        changed
          ? "归类已发生变化。请先读取最新归类，再核对并提交。"
          : cause instanceof Error
            ? cause.message
            : "无法保存手工归类",
      );
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  return (
    <Modal
      title="手工调整归类"
      onClose={() => {
        if (!inFlight.current) onClose();
      }}
    >
      <div className="modal-body topic-adjustment-body" aria-busy={saving}>
        <p className="topic-adjustment-caption">
          <time dateTime={date}>{date}</time> ·
          保存后同步到「当日主线」和「节点回放」。
        </p>
        {error && <ErrorBox message={error} retry={reload} />}
        {loading && !current && <Loading />}
        {current && (
          <>
            {stocks.length > 0 ? (
              <div className="form-grid">
                <label>
                  搜索股票
                  <input
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    disabled={saving}
                    placeholder="名称、代码、拼音或首字母"
                  />
                </label>
                <label>
                  选择股票
                  <select
                    value={stock?.instrument_id ?? ""}
                    disabled={saving}
                    onChange={(event) => {
                      setStockId(event.target.value);
                      setSaveError("");
                      if (conflict) reload();
                      setConflict(false);
                    }}
                  >
                    <option value="">
                      {matches.length ? "选择当天样本" : "暂无匹配"}
                    </option>
                    {choices.map((item) => (
                      <option
                        key={item.instrument_id}
                        value={item.instrument_id}
                      >
                        {item.name || item.instrument_id} · {item.instrument_id}
                        {item.height != null ? " · " + item.height + "板" : ""}
                        {item.override ? " · 手工" : ""}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ) : (
              <Empty title="当天暂无可调整的样本" />
            )}
            {stock && (
              <>
                <div className="topic-adjustment-stock">
                  <strong>{stock.name || stock.instrument_id}</strong>
                  <CopyStockCode
                    instrumentId={stock.instrument_id}
                    variant="full"
                  />
                  {stock.height != null && <span>{stock.height}板</span>}
                </div>
                <AdjustmentFields
                  key={
                    stock.instrument_id + ":" + (stock.override?.revision ?? "")
                  }
                  stock={stock}
                  topics={current.topics}
                  searchNames={current.search_names}
                  disabled={saving || loading || !!error || conflict}
                  onSave={save}
                />
              </>
            )}
          </>
        )}
        {saveError && (
          <ErrorBox
            message={saveError}
            retry={
              conflict
                ? () => {
                    setConflict(false);
                    setSaveError("");
                    reload();
                  }
                : undefined
            }
          />
        )}
        {saving && (
          <p role="status" className="topic-adjustment-caption">
            正在保存…
          </p>
        )}
      </div>
    </Modal>
  );
}
