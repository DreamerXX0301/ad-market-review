import { useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { useApi } from "../lib/api";
import type { Row } from "../lib/api";
import { Badge, ErrorBox } from "./UI";
import {
  defaultHotUniverse,
  hotUniverseSummary,
  validateHotUniverse,
} from "../features/strategies/HotUniverse";
import type { HotUniverseValue } from "../features/strategies/HotUniverse";
import {
  filterHotBrowseRows,
  hotBrowseConfigMatches,
  hotBrowseEndpoint,
} from "./hotBrowsePresentation";
import "./HotBrowseFilter.css";
import type { HotBrowseMode } from "./hotBrowsePresentation";

export function useHotBrowseFilter(
  rows: Row[],
  scope: Row | undefined,
  visible = true,
) {
  const [enabled, setEnabled] = useState(false);
  const [draft, setDraft] = useState<HotUniverseValue>({
    ...defaultHotUniverse,
  });
  const [applied, setApplied] = useState<HotUniverseValue>({
    ...defaultHotUniverse,
  });
  const [includeUnknown, setIncludeUnknown] = useState(false);
  const [mode, setMode] = useState<HotBrowseMode>("observed");
  const endpoint = hotBrowseEndpoint(scope, applied, enabled && visible);
  const result = useApi<Row>(endpoint);
  const filtered = filterHotBrowseRows(
    rows,
    result.loading || result.error ? null : result.data,
    scope,
    applied,
    enabled && visible,
    includeUnknown,
    mode,
  );
  return {
    ...filtered,
    enabled,
    setEnabled,
    draft,
    setDraft,
    applied,
    setApplied,
    includeUnknown,
    setIncludeUnknown,
    mode,
    setMode,
    result,
    endpoint,
  };
}

type HotBrowseModel = ReturnType<typeof useHotBrowseFilter>;

export default function HotBrowseFilter({
  model,
  onRefreshList,
}: {
  model: HotBrowseModel;
  onRefreshList: () => void;
}) {
  const problem = validateHotUniverse(model.draft);
  const dirty = !hotBrowseConfigMatches(model.draft, model.applied);
  return (
    <section className="hot-browse-filter" aria-label="列表热点题材筛选">
      <div className="hot-browse-heading">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={model.enabled}
            onChange={(event) => model.setEnabled(event.target.checked)}
          />
          <strong>近期热点题材 / 行业</strong>
          <small>
            {model.enabled
              ? hotUniverseSummary(model.applied)
              : "未启用 · 显示全部范围"}
          </small>
        </label>
        {model.enabled && (
          <button
            className="link"
            onClick={() => {
              model.setEnabled(false);
              model.setDraft({ ...defaultHotUniverse });
              model.setApplied({ ...defaultHotUniverse });
              model.setIncludeUnknown(false);
              model.setMode("observed");
            }}
          >
            <X size={12} />
            清空热点筛选
          </button>
        )}
      </div>
      {model.enabled && (
        <>
          <div className="hot-browse-fields">
            <label>
              浏览口径
              <select
                aria-label="热点浏览口径"
                value={model.mode}
                onChange={(event) =>
                  model.setMode(event.target.value as HotBrowseMode)
                }
              >
                <option value="observed">已观察热点归属</option>
                <option value="verified">仅已完整核验匹配</option>
              </select>
            </label>
            <label>
              最近交易日
              <input
                aria-label="列表热点最近交易日"
                type="number"
                min={1}
                max={60}
                value={model.draft.window_sessions}
                onChange={(event) =>
                  model.setDraft({
                    ...model.draft,
                    window_sessions: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              排名前N名
              <input
                aria-label="列表热点排名前N名"
                type="number"
                min={1}
                max={50}
                value={model.draft.top_n}
                onChange={(event) =>
                  model.setDraft({
                    ...model.draft,
                    top_n: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              分类范围
              <select
                aria-label="列表热点分类范围"
                value={model.draft.kind}
                onChange={(event) =>
                  model.setDraft({
                    ...model.draft,
                    kind: event.target.value as HotUniverseValue["kind"],
                  })
                }
              >
                <option value="all">题材与行业</option>
                <option value="concept">题材（含人工题材）</option>
                <option value="industry">行业</option>
              </select>
            </label>
            <button
              className="btn small"
              disabled={!!problem || !dirty}
              onClick={() => model.setApplied({ ...model.draft })}
            >
              应用参数
            </button>
            <button
              className="btn small"
              onClick={() => {
                onRefreshList();
                model.result.reload();
              }}
            >
              <RefreshCw size={12} />
              刷新列表与热点
            </button>
          </div>
          {problem && (
            <p className="hot-browse-warning" role="alert">
              {problem}
            </p>
          )}
          {dirty && (
            <p className="muted" role="status">
              参数尚未应用，当前仍按上方已应用范围筛选。
            </p>
          )}
          <div className="hot-browse-status" role="status">
            <Badge>已观察归属 {model.observed}</Badge>
            <Badge tone="up">已完整核验匹配 {model.matched}</Badge>
            <Badge>已排除 {model.unmatched}</Badge>
            <Badge tone={model.unknown ? "warn" : "neutral"}>
              已观察归属待核验 {model.observedUnknown}
            </Badge>
            <Badge
              tone={model.unknown - model.observedUnknown ? "warn" : "neutral"}
            >
              其余归属待核验 {model.unknown - model.observedUnknown}
            </Badge>
            {model.mode === "observed" && (
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={model.includeUnknown}
                  onChange={(event) =>
                    model.setIncludeUnknown(event.target.checked)
                  }
                />
                同时显示其余归属待核验
              </label>
            )}
            {model.result.loading && <span>正在核验已留存热点…</span>}
          </div>
          {!model.endpoint && (
            <p className="hot-browse-warning">
              列表日期、行情发布或题材修订尚未核验，请刷新列表；热点保持待核验。
            </p>
          )}
          {model.result.error && (
            <ErrorBox
              message={model.result.error}
              retry={() => {
                onRefreshList();
                model.result.reload();
              }}
            />
          )}
          {model.ready && !model.result.data?.complete && (
            <p className="hot-browse-warning">
              排名或成员覆盖不足，完整榜待核验。
              {model.mode === "observed"
                ? "当前按真实已观察热点归属收窄，保留待核验标识，不代表完整热点匹配。"
                : "当前仅显示已完整核验匹配；其余记录保持待核验，不作为未满足。"}
            </p>
          )}
          <small className="muted">
            按当前业务日的已确认收盘涨停股票日数排名，同股同日去重；使用与选股相同的当前有效成员代理。已观察模式仅供浏览，完整核验判断沿用策略口径；不改历史入池、触发事件或策略条件。
          </small>
          {model.ready && (
            <details className="hot-browse-ranking">
              <summary>查看本次已留存排名与覆盖</summary>
              <p>
                {model.result.data?.window?.start || "—"} →{" "}
                {model.result.data?.business_date || "—"} · 完整来源日{" "}
                {model.result.data?.coverage?.source_complete_days ?? "—"}/
                {model.result.data?.window?.expected_trade_days ?? "—"} ·
                当前成员代理
              </p>
              <div className="tags">
                {(model.result.data?.items || []).map((topic: Row) => (
                  <Badge key={topic.topic_id}>
                    {topic.rank}. {topic.name} · {topic.event_count} 股票日
                  </Badge>
                ))}
              </div>
              {model.result.data?.reason && <p>{model.result.data.reason}</p>}
            </details>
          )}
        </>
      )}
    </section>
  );
}

export function HotBrowseBadge({ row }: { row: Row }) {
  if (!row._hot_filter_state) return null;
  return (
    <span
      className="hot-browse-row-state"
      title="当前业务日热点观察；不代表入池日或事件日的历史题材归属"
    >
      <Badge tone={row._hot_filter_state === "matched" ? "up" : "warn"}>
        {row._hot_filter_state === "matched"
          ? "当前热点范围"
          : row._hot_observed_match
            ? "已观察热点归属 · 待核验"
            : "热点待核验"}
      </Badge>
    </span>
  );
}
