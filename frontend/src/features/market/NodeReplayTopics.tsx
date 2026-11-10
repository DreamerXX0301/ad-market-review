import { useId, useLayoutEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import { Badge } from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import { usePageActive } from "../../lib/api";
import { isStructuralLadderTopic } from "./ladderBoardPresentation";
import { ladderSearch } from "./ladderPresentation";
import type { LadderSearchNames } from "./ladderPresentation";
import { intradayReadingProps } from "./intradayReadingNavigation";
import TopicAdjustmentDialog from "./TopicAdjustmentDialog";
import type { ManualTopicOverride } from "./TopicAdjustmentDialog";

export type ReasonMatch = "primary" | "related" | null;
type TopicStock = {
  instrument_id: string;
  name: string;
  height: number;
  reason: string | null;
  reason_match?: ReasonMatch;
  manual_override?: ManualTopicOverride | null;
};
type Topic = {
  topic_id: string;
  name: string;
  kind: "industry" | "concept";
  basis: "limit_up_reason" | "membership_fallback" | "manual";
  manual_count?: number;
  observed_date: string | null;
  members_complete: boolean | null;
  buckets: {
    height: number;
    stock_ids: string[];
    fallback_stock_ids?: string[];
  }[];
};
export type TopicLadders = {
  state: "observed" | "partial" | "unknown";
  reason: string | null;
  stocks: TopicStock[];
  items: Topic[];
  search_names?: LadderSearchNames;
};

type Selection = { key: string; height: number | null };
const kinds = { concept: "概念", industry: "行业" } as const;
const basisLabels = {
  limit_up_reason: "原因归类",
  membership_fallback: "成员补充",
  manual: "手工归类",
};
const pageSize = 10;
const topicKey = (topic: Topic) =>
  `${topic.kind}:${topic.basis}:${topic.topic_id}`;
const boardLabel = (height: number) => (height === 1 ? "首板" : `${height} 板`);
const columnLabel = (height: number) =>
  height === 4 ? "4 板及以上" : boardLabel(height);
const inColumn = (bucket: Topic["buckets"][number], height: number | null) =>
  height === null ||
  (height === 4 ? bucket.height >= 4 : bucket.height === height);

function topicRank(topic: Topic) {
  const heights = new Set(
    topic.buckets
      .filter((bucket) => bucket.stock_ids.length)
      .map((bucket) => bucket.height),
  );
  return {
    full: [1, 2, 3].every((height) => heights.has(height)),
    count: heights.size,
  };
}

export function ReplayOriginalReason({ reason }: { reason?: string | null }) {
  const text = reason?.trim();
  if (!text) return null;
  return text.length <= 42 ? (
    <span className="node-replay-original-reason">原始原因 · {text}</span>
  ) : (
    <details className="node-replay-original-reason">
      <summary>{text.slice(0, 42)}… 查看原始原因</summary>
      <p>{text}</p>
    </details>
  );
}

export function ReplayStockIdentity({
  id,
  name,
  onStock,
}: {
  id: string;
  name?: string;
  onStock: (id: string) => void;
}) {
  return (
    <div className="node-replay-stock-identity">
      <button
        type="button"
        className="node-replay-stock-name"
        onClick={() => onStock(id)}
      >
        {name || id}
      </button>
      <CopyStockCode instrumentId={id} variant="full" />
    </div>
  );
}

export default function NodeReplayTopics({
  date,
  value,
  adjustmentsEnabled,
  onStock,
}: {
  date: string;
  value?: TopicLadders;
  adjustmentsEnabled: boolean;
  onStock: (id: string) => void;
}) {
  const pageActive = usePageActive();
  const [kind, setKind] = useState<Topic["kind"]>("concept");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [adjustment, setAdjustment] = useState<{ stockId?: string } | null>(
    null,
  );
  const rosterId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const adjustmentButton = useRef<HTMLButtonElement>(null);
  const topics = (value?.items ?? [])
    .filter(
      (item) =>
        item.kind === kind &&
        (kind === "industry" ||
          !!item.manual_count ||
          !isStructuralLadderTopic(item)),
    )
    .map((topic) => ({ topic, ...topicRank(topic) }))
    .sort(
      (left, right) =>
        Number(right.full) - Number(left.full) ||
        right.count - left.count ||
        left.topic.name.localeCompare(right.topic.name, "zh-CN") ||
        left.topic.topic_id.localeCompare(right.topic.topic_id),
    );
  const search = ladderSearch(query, value?.search_names);
  const results = search.results(topics, ({ topic }, typo) =>
    search.label(topic.name, typo),
  );
  const pages = Math.max(1, Math.ceil(results.length / pageSize));
  const shownPage = Math.min(page, pages - 1);
  const start = shownPage * pageSize;
  const end = Math.min(start + pageSize, results.length);
  const rows = results.slice(start, end);
  const columns = topics.some(({ topic }) =>
    topic.buckets.some(
      (bucket) => bucket.height >= 4 && bucket.stock_ids.length,
    ),
  )
    ? [4, 3, 2, 1]
    : [3, 2, 1];
  const selected = rows.find(
    ({ topic }) => topicKey(topic) === selection?.key,
  )?.topic;
  const stocks = new Map(
    value?.stocks.map((stock) => [stock.instrument_id, stock]),
  );
  const showRoster = pageActive && !!selected;

  useLayoutEffect(() => {
    const panel = popup.current;
    const anchor = trigger.current;
    if (!panel || !anchor) return;
    if (!showRoster) {
      if (panel.matches(":popover-open")) panel.hidePopover();
      return;
    }
    if (!panel.matches(":popover-open")) panel.showPopover();
    const viewport = window.visualViewport;
    function place() {
      if (!panel || !anchor) return;
      const width = viewport?.width ?? window.innerWidth;
      const height = viewport?.height ?? window.innerHeight;
      const left = (viewport?.offsetLeft ?? 0) + 8;
      const top = (viewport?.offsetTop ?? 0) + 8;
      panel.style.maxWidth = `${Math.max(0, width - 16)}px`;
      panel.style.maxHeight = `${Math.max(0, Math.min(480, height - 16))}px`;
      const bounds = anchor.getBoundingClientRect();
      const maxLeft = left + width - panel.offsetWidth - 16;
      const maxTop = top + height - panel.offsetHeight - 16;
      const below = bounds.bottom + 6;
      const above = bounds.top - panel.offsetHeight - 6;
      const preferredTop = below <= maxTop || above < top ? below : above;
      panel.style.left = `${Math.max(left, Math.min(bounds.left, maxLeft))}px`;
      panel.style.top = `${Math.max(top, Math.min(preferredTop, maxTop))}px`;
    }
    place();
    closeButton.current?.focus({ preventScroll: true });
    const observer = new ResizeObserver(place);
    observer.observe(panel);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    viewport?.addEventListener("scroll", place);
    viewport?.addEventListener("resize", place);
    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
      viewport?.removeEventListener("scroll", place);
      viewport?.removeEventListener("resize", place);
    };
  }, [showRoster, selection]);

  function movePage(next: number) {
    setPage(next);
    setSelection(null);
  }
  function openAdjustment(stockId?: string) {
    if (popup.current?.matches(":popover-open")) popup.current.hidePopover();
    setSelection(null);
    adjustmentButton.current?.focus({ preventScroll: true });
    setAdjustment({ stockId });
  }

  function toggleTopic(
    topic: Topic,
    height: number | null,
    element: HTMLButtonElement,
  ) {
    const key = topicKey(topic);
    trigger.current = element;
    setSelection(
      selection?.key === key && selection.height === height
        ? null
        : { key, height },
    );
  }

  return (
    <section
      {...intradayReadingProps("nodeTopics")}
      className="node-replay-section node-replay-topics"
      aria-label="行业与概念梯队"
    >
      <header className="node-replay-section-head">
        <h3>行业与概念梯队</h3>
        <div className="node-replay-labels">
          {value?.state !== "observed" && (
            <Badge tone="warn">
              {value?.state === "partial" ? "部分覆盖" : "待核验"}
            </Badge>
          )}
          {adjustmentsEnabled && (
            <button
              type="button"
              className="btn small"
              ref={adjustmentButton}
              onClick={() => openAdjustment()}
            >
              <Pencil size={13} />
              手工调整归类
            </button>
          )}
        </div>
      </header>
      <p className="node-replay-note">
        沿用「当日主线」的涨停原因归类。主因未成组时，仅当原始原因明确提及一个已成组的次要方向，才按「原因关联」纳入。原因不可用时按来源行业或节点日留存成员补充。自动分组至少两只已核验样本。手工归类按当天设置生效。
      </p>
      {value?.reason && <p className="node-replay-note">{value.reason}</p>}
      <div className="node-replay-topic-controls">
        <div className="ladder-view-switch" role="group" aria-label="分组类型">
          {(["concept", "industry"] as const).map((key) => (
            <button
              key={key}
              type="button"
              className={kind === key ? "active" : ""}
              aria-pressed={kind === key}
              onClick={() => {
                setKind(key);
                movePage(0);
              }}
            >
              {kinds[key]}
            </button>
          ))}
        </div>
        <input
          type="search"
          aria-label={`搜索${kinds[kind]}名称`}
          placeholder={`${kinds[kind]}、拼音或首字母`}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            movePage(0);
          }}
        />
      </div>
      {rows.length ? (
        <div
          className="table-wrap node-replay-scroll"
          tabIndex={0}
          role="region"
          aria-label={`${kinds[kind]}各板高对照`}
        >
          <table className="node-replay-table node-replay-topic-table">
            <thead>
              <tr>
                <th scope="col">{kinds[kind]}</th>
                {columns.map((height) => (
                  <th scope="col" key={height}>
                    {columnLabel(height)}
                  </th>
                ))}
                <th scope="col">归类依据</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ topic, full }) => {
                const key = topicKey(topic);
                const expanded = selection?.key === key;
                return (
                  <tr
                    key={key}
                    className={
                      expanded ? "node-replay-topic-selected" : undefined
                    }
                  >
                    <th scope="row">
                      <button
                        type="button"
                        className="node-replay-topic-name"
                        aria-label={`查看${kinds[kind]}${topic.name}全部已核验股票`}
                        aria-expanded={expanded && selection.height === null}
                        aria-haspopup="dialog"
                        aria-controls={rosterId}
                        onClick={(event) =>
                          toggleTopic(topic, null, event.currentTarget)
                        }
                      >
                        {topic.name}
                      </button>
                      {full && (
                        <span className="node-replay-topic-full">1–3板齐</span>
                      )}
                    </th>
                    {columns.map((height) => {
                      const count = topic.buckets
                        .filter((bucket) => inColumn(bucket, height))
                        .reduce(
                          (total, bucket) => total + bucket.stock_ids.length,
                          0,
                        );
                      return (
                        <td key={height}>
                          {count ? (
                            <button
                              type="button"
                              className="node-replay-topic-count"
                              aria-label={`${topic.name}${columnLabel(height)} ${count} 只`}
                              aria-expanded={
                                expanded && selection.height === height
                              }
                              aria-haspopup="dialog"
                              aria-controls={rosterId}
                              onClick={(event) =>
                                toggleTopic(topic, height, event.currentTarget)
                              }
                            >
                              {count}
                            </button>
                          ) : (
                            <span className="node-replay-missing">—</span>
                          )}
                        </td>
                      );
                    })}
                    <td className="node-replay-note">
                      {topic.basis === "membership_fallback" &&
                      topic.members_complete === null
                        ? "来源行业"
                        : basisLabels[topic.basis] || "待核验"}
                      {!!topic.manual_count && topic.basis !== "manual" && (
                        <small> · 手工 {topic.manual_count} 只</small>
                      )}
                      {topic.basis === "membership_fallback" &&
                        topic.observed_date && (
                          <small className="node-replay-topic-source-date">
                            {topic.members_complete === null
                              ? "来源日期"
                              : "成员采集"}{" "}
                            <time dateTime={topic.observed_date}>
                              {topic.observed_date}
                            </time>
                          </small>
                        )}
                      {topic.basis === "membership_fallback" &&
                        topic.members_complete === false && (
                          <small> · 部分成员</small>
                        )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="node-replay-note">
          {search.term
            ? `暂无匹配的${kinds[kind]}。`
            : `暂无可核验的${kinds[kind]}梯队。`}
        </p>
      )}
      <div
        id={rosterId}
        ref={popup}
        popover="auto"
        role="dialog"
        aria-labelledby={`${rosterId}-heading`}
        className="node-replay-topic-roster"
        onToggle={(event) => {
          if (event.currentTarget.matches(":popover-open")) return;
          setSelection(null);
          const focused = document.activeElement;
          if (
            pageActive &&
            (focused === document.body || event.currentTarget.contains(focused))
          )
            trigger.current?.focus({ preventScroll: true });
        }}
      >
        {selected && selection && (
          <>
            <header className="node-replay-section-head">
              <h4 id={`${rosterId}-heading`}>
                {selected.name} ·{" "}
                {selection.height === null
                  ? "已核验匹配名单"
                  : columnLabel(selection.height)}
              </h4>
              <button
                type="button"
                className="btn small"
                ref={closeButton}
                onClick={() => setSelection(null)}
              >
                收起名单
              </button>
            </header>
            <div className="node-replay-topic-roster-rows">
              {selected.buckets
                .filter((bucket) => inColumn(bucket, selection.height))
                .sort((left, right) => right.height - left.height)
                .map((bucket) => (
                  <div
                    key={bucket.height}
                    className="node-replay-topic-roster-row"
                  >
                    <strong>{boardLabel(bucket.height)}</strong>
                    <div>
                      {bucket.stock_ids.map((id) => {
                        const stock = stocks.get(id);
                        const fallback =
                          selected.basis === "membership_fallback" ||
                          bucket.fallback_stock_ids?.includes(id);
                        const sourceLabel = stock?.manual_override
                          ? "手工归类"
                          : fallback
                            ? selected.members_complete === null
                              ? "来源行业"
                              : "成员补充"
                            : stock?.reason_match === "related"
                              ? "原因关联"
                              : null;
                        return (
                          <div
                            key={id}
                            className="node-replay-topic-stock-entry"
                          >
                            <div className="node-replay-labels">
                              <ReplayStockIdentity
                                id={id}
                                name={stock?.name}
                                onStock={(value) => {
                                  setSelection(null);
                                  onStock(value);
                                }}
                              />
                              {sourceLabel && (
                                <small className="node-replay-note">
                                  {sourceLabel}
                                </small>
                              )}
                              {adjustmentsEnabled && (
                                <button
                                  type="button"
                                  className="topic-adjustment-edit"
                                  title="调整归类"
                                  aria-label={`调整${stock?.name || id}的归类`}
                                  onClick={() => openAdjustment(id)}
                                >
                                  <Pencil size={13} />
                                </button>
                              )}
                            </div>
                            <ReplayOriginalReason reason={stock?.reason} />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
            </div>
          </>
        )}
      </div>
      {pages > 1 && (
        <nav
          className="node-replay-topic-pagination"
          aria-label="行业与概念梯队分页"
        >
          <button
            type="button"
            className="btn small"
            disabled={shownPage === 0}
            onClick={() => movePage(shownPage - 1)}
          >
            上一页
          </button>
          <span className="node-replay-note">
            第 {start + 1 === end ? end : `${start + 1}–${end}`} 组 / 共{" "}
            {results.length} 组
          </span>
          <button
            type="button"
            className="btn small"
            disabled={shownPage + 1 === pages}
            onClick={() => movePage(shownPage + 1)}
          >
            下一页
          </button>
        </nav>
      )}
      {!!rows.length && (
        <p className="node-replay-note">
          数量按节点日普通主板确认涨停样本的归类结果统计。— 表示暂无已核验匹配。
        </p>
      )}
      {pageActive && adjustmentsEnabled && adjustment && (
        <TopicAdjustmentDialog
          date={date}
          initialStock={adjustment.stockId}
          mainboardOnly
          onClose={() => setAdjustment(null)}
        />
      )}
    </section>
  );
}
