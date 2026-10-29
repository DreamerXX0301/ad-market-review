import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useApi, type Row } from "../../lib/api";
import { Badge, Empty, ErrorBox, Loading, Panel } from "../../components/UI";
import {
  createStockNavigation,
  type StockNavigation,
} from "../../components/stockNavigation";
import { integer, num, percent, tone } from "../../lib/format";
import { SortableMarketTable } from "./MarketTable";
import { MetricHelp } from "./MetricHelp";
import { MemberBoardCell } from "./MarketMemberMetrics";
import {
  BROKEN_BOARD_BOARD_FILTERS,
  brokenBoardPreviousEvent,
  brokenBoardFilterPrevious,
  brokenBoardHeightCounts,
  brokenBoardSortByPrevious,
  brokenBoardContext,
  brokenBoardCurrentItems,
  brokenBoardDrawdown,
  brokenBoardDetailCount,
  brokenBoardDetailContext,
  brokenBoardDistribution,
  brokenBoardDistributionSample,
  brokenBoardFeedback,
  brokenBoardFilterItems,
  brokenBoardHistory,
  brokenBoardMembership,
  brokenBoardMembershipSummary,
  brokenBoardPeriod,
  brokenBoardPriceFeedback,
  brokenBoardStrings,
  brokenBoardVisibleTopicCount,
} from "./brokenBoardPresentation";
import type {
  BrokenBoardBoardFilter,
  BrokenBoardHeightFilter,
  BrokenBoardMembershipKind,
} from "./brokenBoardPresentation";
import "./BrokenBoardAnalysis.css";

type Props = {
  data: Row | null | undefined;
  date: string;
  snapshotId?: unknown;
  onStock: (id: string, navigation?: StockNavigation) => void;
};

const rateText = (value: number | null) =>
  value === null ? "—" : `${num(value, 1)}%`;
const countText = (value: number | null) =>
  value === null ? "—" : `${integer(value)}家`;

type TopicFilter = { kind: BrokenBoardMembershipKind; key: string } | null;

function MembershipTags({
  row,
  date,
  kind,
}: {
  row: Row;
  date: string;
  kind: BrokenBoardMembershipKind;
}) {
  const tags = brokenBoardMembership(row, date, kind);
  const render = (tag: Row) => (
    <Badge key={tag.key}>
      <span
        title={`${tag.source} · 成员采集 ${tag.observed_date}${kind === "industry" && tag.classification_level != null ? ` · 层级${tag.classification_level}` : ""}`}
      >
        {tag.name}
      </span>
    </Badge>
  );
  return tags.length ? (
    <div
      className={`broken-board-membership-tags broken-board-membership-${kind}`}
    >
      <div className="tags">{tags.slice(0, 3).map(render)}</div>
      {tags.length > 3 && (
        <details>
          <summary>
            另 {integer(tags.length - 3)} 项
            {kind === "industry" ? "行业" : "题材"}
          </summary>
          <div className="tags">{tags.slice(3).map(render)}</div>
        </details>
      )}
    </div>
  ) : (
    <span className={`muted broken-board-membership-${kind}`}>
      {kind === "industry" ? "行业" : "题材"}归属未留存
    </span>
  );
}

function DistributionTags({
  items,
  kind,
  sampleDate,
  selected,
  onSelect,
}: {
  items: Row[];
  kind: BrokenBoardMembershipKind;
  sampleDate: string;
  selected: TopicFilter;
  onSelect: (next: TopicFilter) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const signature = JSON.stringify([
    sampleDate,
    items.map((item) => [
      item.key,
      item.name,
      item.observed_count,
      brokenBoardDistributionSample(item, sampleDate),
    ]),
  ]);
  const [measured, setMeasured] = useState({ signature: "", count: 0 });
  const visibleCount = measured.signature === signature ? measured.count : 0;
  useLayoutEffect(() => {
    const container = ref.current;
    if (!container) return;
    const buttons = Array.from(container.querySelectorAll("button"));
    const measure = () => {
      const bounds = container.getBoundingClientRect();
      const count = brokenBoardVisibleTopicCount(
        bounds.width,
        buttons.map(
          (button) => button.getBoundingClientRect().right - bounds.left,
        ),
      );
      setMeasured((previous) =>
        previous.signature === signature && previous.count === count
          ? previous
          : { signature, count },
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    buttons.forEach((button) => observer.observe(button));
    return () => observer.disconnect();
  }, [signature]);
  return (
    <div ref={ref} className="broken-board-distribution-tags">
      {items.length ? (
        items.map((item, index) => {
          const hidden = index >= visibleCount;
          const sample = brokenBoardDistributionSample(item, sampleDate);
          return (
            <button
              type="button"
              key={item.key}
              className={selected?.key === item.key ? "active" : ""}
              aria-pressed={selected?.key === item.key}
              aria-hidden={hidden || undefined}
              inert={hidden}
              tabIndex={hidden ? -1 : 0}
              style={hidden ? { visibility: "hidden" } : undefined}
              title={`${item.source}${kind === "industry" ? ` · 层级${item.classification_level ?? "未知"}` : ""} · 已核验炸板队列中的关联家数 · 成员采集 ${item.membership_date || "未提供"} · ${sample.reason}`}
              onClick={() =>
                onSelect(
                  selected?.key === item.key ? null : { kind, key: item.key },
                )
              }
            >
              {item.name} <b>关联{integer(item.observed_count)}只</b>
              <span
                className={`broken-board-distribution-change ${sample.tone}`}
              >
                均{percent(sample.change)}
              </span>
              {sample.partial && (
                <small className="broken-board-distribution-valid">
                  有效{sample.valid}/{sample.total}
                </small>
              )}
            </button>
          );
        })
      ) : (
        <span className="muted">
          该日未留存可核验的{kind === "industry" ? "行业" : "题材"}分布
        </span>
      )}
    </div>
  );
}

function Distribution({
  data,
  date,
  sampleDate,
  selected,
  onSelect,
}: {
  data: Row | null | undefined;
  date: string;
  sampleDate: string;
  selected: TopicFilter;
  onSelect: (next: TopicFilter) => void;
}) {
  const summary = brokenBoardMembershipSummary(data?.membership_summary, date);
  return (
    <div className="broken-board-distribution">
      {summary && (
        <p className="broken-board-distribution-summary">
          {summary.reason ? (
            <>
              {integer(summary.cohort_count)}只已核验炸板样本 · 行业 /
              概念归属待核验
            </>
          ) : (
            <>
              {integer(summary.cohort_count)}只样本 · 已知关联{" "}
              <strong>{integer(summary.industry_count)}</strong> 个行业分类 /{" "}
              <strong>{integer(summary.concept_count)}</strong> 个概念 ·
              未有归属 {integer(summary.unclassified_count)}只
            </>
          )}
        </p>
      )}
      {(["industry", "concept"] as const).map((kind) => {
        const items = brokenBoardDistribution(
          data?.[`${kind}_distribution`],
          date,
          kind,
        );
        return (
          <div key={kind} className="broken-board-distribution-row">
            <strong>{kind === "industry" ? "行业分类" : "概念题材"}</strong>
            <DistributionTags
              items={items}
              kind={kind}
              sampleDate={sampleDate}
              selected={selected}
              onSelect={onSelect}
            />
          </div>
        );
      })}
      <div className="broken-board-distribution-note">
        <span>
          各行按关联家数展示，超出隐藏；均涨跌为本组关联炸板股票在 {sampleDate}
          的等权平均，仅统计可比样本，不随明细筛选改变。点击筛明细，再次点击取消。
        </span>
        {selected && (
          <button
            type="button"
            className="btn secondary"
            onClick={() => onSelect(null)}
          >
            清除分类筛选
          </button>
        )}
      </div>
      {data?.membership_reason && (
        <p className="broken-board-note">{data.membership_reason}</p>
      )}
    </div>
  );
}

function Metric({
  label,
  children,
  note,
  className = "",
}: {
  label: string;
  children: ReactNode;
  note?: ReactNode;
  className?: string;
}) {
  return (
    <div className="broken-board-metric">
      <span>{label}</span>
      <strong className={className}>{children}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

function CurrentMetrics({ data }: { data: Row }) {
  const current = brokenBoardPeriod(data.current);
  const complete = current.state === "complete";
  const brokenCount = complete ? current.broken : current.observedBroken;
  const upCount = complete ? current.up : current.observedUp;
  return (
    <>
      <div className="broken-board-metrics">
        <Metric
          label="收盘炸板比例"
          className={current.rate === null ? "muted" : "broken-board-warn"}
          note={complete ? "收盘未封住 / 触板家数" : "覆盖不足，比例待核验"}
        >
          {rateText(current.rate)}
        </Metric>
        <Metric
          label={complete ? "收盘未封住" : "已观察炸板样本"}
          className={brokenCount === null ? "muted" : "broken-board-warn"}
          note="按证券去重，不累计开板次数"
        >
          {countText(brokenCount)}
        </Metric>
        <Metric
          label={complete ? "收盘涨停" : "已观察涨停样本"}
          className={upCount === null ? "muted" : "up"}
          note="回封后涨停与最终炸板分开"
        >
          {countText(upCount)}
        </Metric>
        <Metric
          label="触板家数"
          className={current.touch === null ? "muted" : "broken-board-total"}
          note={complete ? "同源两池核验并去重" : "完整分母待核验"}
        >
          {countText(current.touch)}
        </Metric>
      </div>
      {current.reason && <p className="broken-board-note">{current.reason}</p>}
    </>
  );
}

/** Small overview entry; the complete research remains on market emotion. */
export function BrokenBoardSummary({ data, date, snapshotId }: Props) {
  const context = brokenBoardContext(data, date, snapshotId);
  if (!context) return null;
  const current = brokenBoardPeriod(context.current);
  const history = brokenBoardHistory(
    context.history,
    date,
    context.snapshot_id,
  );
  const feedback = brokenBoardFeedback(
    context.previous_feedback,
    date,
    context.snapshot_id,
  );
  const previous = history.find((row) => row.date === feedback.previousDate);
  const source = current.source || context.source;
  const scope = current.scopeKey || context.scope_key;
  const delta =
    current.rate !== null &&
    previous?.rate != null &&
    typeof source === "string" &&
    typeof scope === "string" &&
    previous.source === source &&
    previous.scopeKey === scope
      ? current.rate - previous.rate
      : null;
  const observations = brokenBoardStrings(context.observations);
  return (
    <Panel
      title="炸板结构观察"
      subtitle={`${date} · ${context.scope || "同日来源池核验"}`}
      actions={<Badge>{current.label}</Badge>}
      className="broken-board-analysis broken-board-summary"
    >
      <CurrentMetrics data={context} />
      {delta !== null && (
        <p className="broken-board-note">
          较前一交易日 {delta > 0 ? "+" : ""}
          {num(delta, 1)} 个百分点
          <span> · 比例升降不单独判定市场阶段</span>
        </p>
      )}
      {observations.length > 0 && (
        <p className="broken-board-observation">{observations[0]}</p>
      )}
      <a className="broken-board-detail-link" href="#emotion">
        查看市场情绪中的炸板结构与昨日样本反馈 →
      </a>
    </Panel>
  );
}

export default function BrokenBoardAnalysis({
  data,
  date,
  snapshotId,
  onStock,
}: Props) {
  const [detailMode, setDetailMode] = useState<"current" | "previous">(
    "current",
  );
  const scope = JSON.stringify([date, snapshotId]);
  const [historySelection, setHistorySelection] = useState<{
    scope: string;
    date: string;
  } | null>(null);
  const selectedDate =
    historySelection?.scope === scope ? historySelection.date : date;
  const [topicSelection, setTopicSelection] = useState<{
    scope: string;
    value: TopicFilter;
  } | null>(null);
  const filterScope = JSON.stringify([scope, selectedDate, detailMode]);
  const topicFilter =
    topicSelection?.scope === filterScope ? topicSelection.value : null;
  const [boardSelection, setBoardSelection] = useState<{
    scope: string;
    value: BrokenBoardBoardFilter;
  } | null>(null);
  const boardFilter =
    boardSelection?.scope === filterScope ? boardSelection.value : "all";
  const [heightSelection, setHeightSelection] = useState<{
    scope: string;
    value: BrokenBoardHeightFilter;
  } | null>(null);
  const heightFilter =
    heightSelection?.scope === filterScope ? heightSelection.value : "all";
  const detailRef = useRef<HTMLDetailsElement>(null);
  const context = brokenBoardContext(data, date, snapshotId);
  const report = useApi<Row>(
    context && selectedDate !== date
      ? `/market/broken-boards?date=${encodeURIComponent(selectedDate)}`
      : null,
    [context?.snapshot_id],
  );
  useEffect(() => {
    setDetailMode("current");
    setHistorySelection(null);
    setTopicSelection(null);
    setBoardSelection(null);
    setHeightSelection(null);
  }, [scope]);
  const selectHistory = (day: string) => {
    setHistorySelection({ scope, date: day });
    setDetailMode("current");
    setTopicSelection(null);
    setBoardSelection(null);
    setHeightSelection(null);
    if (detailRef.current) detailRef.current.open = true;
    requestAnimationFrame(() =>
      detailRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }),
    );
  };
  if (!context) return null;
  const detailContext = brokenBoardDetailContext(
    context,
    selectedDate,
    report.data,
  );
  const historyMismatch =
    selectedDate !== date && !!report.data && !detailContext;
  const detailLoading =
    selectedDate !== date &&
    !detailContext &&
    !report.error &&
    !historyMismatch;
  const current = brokenBoardPeriod(context.current);
  const history = brokenBoardHistory(
    context.history,
    date,
    context.snapshot_id,
  );
  const feedback = brokenBoardFeedback(
    context.previous_feedback,
    date,
    context.snapshot_id,
  );
  const currentItems = brokenBoardCurrentItems(
    detailContext?.current,
    selectedDate,
    context.snapshot_id,
  );
  const detailCurrent = brokenBoardPeriod(detailContext?.current);
  const drawdown = brokenBoardDrawdown(context.current);
  const membershipDate =
    detailMode === "current" ? selectedDate : feedback.previousDate || "";
  const allDetailItems =
    detailMode === "current" ? currentItems : feedback.items;
  const eventPrevious =
    detailMode === "current"
      ? detailContext?.current?.previous_event_date
      : feedback.previousDate;
  const detailItems = brokenBoardSortByPrevious(
    brokenBoardFilterPrevious(
      brokenBoardFilterItems(
        allDetailItems,
        membershipDate,
        topicFilter,
        boardFilter,
      ),
      selectedDate,
      heightFilter,
      eventPrevious,
    ),
    selectedDate,
    eventPrevious,
  );
  const heightCounts = brokenBoardHeightCounts(
    allDetailItems,
    selectedDate,
    eventPrevious,
  );
  const distributionData =
    detailMode === "current"
      ? detailContext?.current
      : context.previous_feedback;
  const observations = brokenBoardStrings(context.observations);
  const limits = brokenBoardStrings(context.limits);
  return (
    <section
      id="emotion-broken-board"
      aria-label="炸板结构与昨日炸板反馈"
      className="broken-board-analysis"
    >
      <Panel
        title="炸板结构与后续反馈"
        subtitle={`${date} · 冲板结果、价格回落与后续表现分开观察`}
        actions={<Badge>{current.label}</Badge>}
      >
        <CurrentMetrics data={context} />
        <p className="broken-board-note">
          今日炸板可核验回落 {countText(drawdown.valid)} · 收盘距当日高点平均
          <span className={tone(drawdown.average)}>
            {" "}
            {percent(drawdown.average)}
          </span>
          {" · "}回落至少5% {countText(drawdown.large)}
          <MetricHelp
            label="炸板回落程度"
            description="仅对已核验的今日收盘炸板样本，使用同日原始收盘/最高价−1计算，回落至少5%为项目透明观察阈值；不是实际追板盈亏，也不推断盘中先后或买卖意图。"
          />
        </p>
        {observations.length > 0 && (
          <ul className="broken-board-observations">
            {observations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
        <div className="broken-board-analysis-grid">
          <section
            className="broken-board-history"
            aria-label="近七交易日炸板结构"
          >
            <h3>
              近7个交易日
              <MetricHelp
                label="近期炸板结构"
                description="按已返回的严格市场交易日逐日展示。两池覆盖不全、价格未核验或来源冲突时，保留缺口，不补0，也不用更早日期代替缺失交易日。"
              />
            </h3>
            {history.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>交易日</th>
                      <th>收盘涨停</th>
                      <th>未封住</th>
                      <th>炸板比例</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((row) => (
                      <tr
                        key={row.date}
                        title={row.reason || row.label}
                        className={
                          selectedDate === row.date && detailMode === "current"
                            ? "selected"
                            : ""
                        }
                      >
                        <td>
                          <button
                            type="button"
                            className="broken-board-history-date"
                            aria-label={`查看${row.date}炸板股票与行业题材`}
                            aria-pressed={
                              selectedDate === row.date &&
                              detailMode === "current"
                            }
                            onClick={() => selectHistory(row.date)}
                          >
                            {row.date.slice(5)}
                          </button>
                        </td>
                        <td>{integer(row.up)}</td>
                        <td>{integer(row.broken)}</td>
                        <td>
                          {row.rate === null ? (
                            <span className="muted">— · {row.label}</span>
                          ) : (
                            rateText(row.rate)
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="近期同口径记录尚未核验" />
            )}
            <p className="broken-board-history-help">
              点击日期看对应日炸板股票与行业题材。
            </p>
          </section>
          <section
            className="broken-board-feedback"
            aria-label="昨日炸板今日反馈"
          >
            <h3>昨日炸板 · 今日反馈</h3>
            <p className="broken-board-subtitle">
              {feedback.previousDate || "昨日日期待核验"} → {date}
            </p>
            <div className="broken-board-feedback-metrics">
              <Metric label="今日平均涨幅" className={tone(feedback.average)}>
                {percent(feedback.average)}
              </Metric>
              <Metric label="今日涨幅中位数" className={tone(feedback.median)}>
                {percent(feedback.median)}
              </Metric>
              <Metric
                label="涨停反包比例"
                note="昨日炸板、今日确认涨停；完整事件证据"
              >
                {rateText(feedback.returnLimitRate)}
              </Metric>
            </div>
            <dl className="broken-board-feedback-counts">
              <div>
                <dt>上涨</dt>
                <dd className="up">{countText(feedback.up)}</dd>
              </div>
              <div>
                <dt>下跌</dt>
                <dd className="down">{countText(feedback.down)}</dd>
              </div>
              <div>
                <dt>平盘</dt>
                <dd>{countText(feedback.flat)}</dd>
              </div>
              <div>
                <dt title="今日可比收盘涨幅不高于−5%的价格观察，不代表实际账户盈亏">
                  大面
                </dt>
                <dd className="down">{countText(feedback.largeDrop)}</dd>
              </div>
              <div>
                <dt>今日确认涨停</dt>
                <dd className="up">
                  {feedback.limitUp === null &&
                  feedback.observedLimitUp !== null
                    ? `已观察 ${countText(feedback.observedLimitUp)}`
                    : countText(feedback.limitUp)}
                </dd>
              </div>
              <div>
                <dt>今日确认跌停</dt>
                <dd className="down">
                  {feedback.limitDown === null &&
                  feedback.observedLimitDown !== null
                    ? `已观察 ${countText(feedback.observedLimitDown)}`
                    : countText(feedback.limitDown)}
                </dd>
              </div>
            </dl>
            <p className="broken-board-note">
              {feedback.source === null
                ? `来源已观察 ${countText(feedback.observedSource)}`
                : `来源 ${countText(feedback.source)}`}{" "}
              · 已核验昨日样本 {countText(feedback.verified)} · 今日可比{" "}
              {countText(feedback.validCount)} · 待核验{" "}
              {countText(feedback.unknown)}
            </p>
            {(!!feedback.upUnknown || !!feedback.downUnknown) && (
              <p className="broken-board-note">
                事件状态待核验：涨停 {countText(feedback.upUnknown)} · 跌停{" "}
                {countText(feedback.downUnknown)}
                。价格可比与事件池覆盖分别统计。
              </p>
            )}
            {feedback.reason && (
              <p className="broken-board-note">{feedback.reason}</p>
            )}
          </section>
        </div>
        <details className="broken-board-stock-detail" ref={detailRef} open>
          <summary>
            {detailMode === "current"
              ? `${selectedDate} 炸板明细`
              : "昨日炸板今日反馈"}{" "}
            ·{" "}
            {brokenBoardDetailCount(
              detailMode === "current" ? detailCurrent.state : feedback.state,
              detailItems.length,
              detailMode === "current" && detailLoading,
            )}
          </summary>
          <div
            className="broken-board-detail-tabs"
            role="group"
            aria-label="选择炸板明细范围"
          >
            <button
              className={detailMode === "current" ? "active" : ""}
              aria-pressed={detailMode === "current"}
              onClick={() => {
                setDetailMode("current");
                setTopicSelection(null);
                setBoardSelection(null);
                setHeightSelection(null);
              }}
            >
              {selectedDate === date ? "今日炸板" : `${selectedDate} 炸板`}{" "}
              {brokenBoardDetailCount(
                detailCurrent.state,
                currentItems.length,
                detailLoading,
              )}
            </button>
            <button
              className={detailMode === "previous" ? "active" : ""}
              aria-pressed={detailMode === "previous"}
              onClick={() => {
                setHistorySelection(null);
                setDetailMode("previous");
                setTopicSelection(null);
                setBoardSelection(null);
                setHeightSelection(null);
              }}
            >
              昨日炸板今日反馈{" "}
              {brokenBoardDetailCount(feedback.state, feedback.items.length)}
            </button>
            {selectedDate !== date && (
              <button type="button" onClick={() => selectHistory(date)}>
                返回观察日 {date}
              </button>
            )}
          </div>
          <p className="broken-board-note">
            {detailMode === "current"
              ? `所选日归属 ${membershipDate}`
              : `炸板日归属 ${membershipDate}`}{" "}
            · 仅使用该日已留存成员，采集日见标签说明；关联不代表涨跌原因。
          </p>
          {detailMode === "current" &&
            selectedDate !== date &&
            report.error && (
              <ErrorBox message={report.error} retry={report.reload} />
            )}
          {detailMode === "current" && historyMismatch && (
            <ErrorBox
              message="该日报告与当前行情代际不一致，请重新读取，未混入旧明细。"
              retry={report.reload}
            />
          )}
          {detailMode === "current" && detailLoading ? (
            <Loading />
          ) : (
            <>
              {(detailMode === "previous" || detailContext) && (
                <Distribution
                  data={distributionData}
                  date={membershipDate}
                  sampleDate={selectedDate}
                  selected={topicFilter}
                  onSelect={(value) =>
                    setTopicSelection({ scope: filterScope, value })
                  }
                />
              )}
              <div
                className="broken-board-board-filter"
                role="group"
                aria-label="筛选炸板明细的上市板块"
              >
                <span>上市板块</span>
                {BROKEN_BOARD_BOARD_FILTERS.map((option) => (
                  <button
                    type="button"
                    key={option.value}
                    className={boardFilter === option.value ? "active" : ""}
                    aria-pressed={boardFilter === option.value}
                    onClick={() =>
                      setBoardSelection({
                        scope: filterScope,
                        value: option.value,
                      })
                    }
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <div
                className="broken-board-board-filter broken-board-height-filter"
                role="group"
                aria-label="筛选昨日板型"
              >
                <span>昨日板型</span>
                {heightCounts.map((option) => (
                  <button
                    type="button"
                    key={option.value}
                    className={heightFilter === option.value ? "active" : ""}
                    aria-pressed={heightFilter === option.value}
                    onClick={() =>
                      setHeightSelection({
                        scope: filterScope,
                        value:
                          heightFilter === option.value &&
                          option.value !== "all"
                            ? "all"
                            : option.value,
                      })
                    }
                  >
                    {option.label} <b>{integer(option.count)}</b>
                  </button>
                ))}
              </div>
              <p className="broken-board-note">
                只标记严格前一交易日已核验板型，不推算冲击几板；未标记包含昨日无事件及证据未齐，快捷数量保持本队列范围。
              </p>
              {(topicFilter ||
                boardFilter !== "all" ||
                heightFilter !== "all") && (
                <p className="broken-board-note">
                  明细筛选后 {integer(detailItems.length)} /{" "}
                  {integer(allDetailItems.length)} 只；上方总体统计保持原队列。
                </p>
              )}
              <SortableMarketTable
                key={`${selectedDate}:${snapshotId || ""}:${detailMode}:${boardFilter}:${heightFilter}:${topicFilter?.key || "all"}`}
                rows={detailItems}
                columns={[
                  { key: "name", title: "股票 / 代码" },
                  {
                    key: "previous_board_count",
                    title: "昨日板型",
                    render: (_value, row) => {
                      const prior = brokenBoardPreviousEvent(
                        row,
                        selectedDate,
                        eventPrevious,
                      );
                      return prior.label ? (
                        <Badge tone={prior.height == null ? "warn" : "neutral"}>
                          <span
                            title={`严格前一交易日 ${prior.date} 已核验事实，不代表今日晋级`}
                          >
                            {prior.label}
                          </span>
                        </Badge>
                      ) : (
                        <span
                          className="muted"
                          title={prior.reason || "昨日无已确认板型或证据未齐"}
                        >
                          —
                        </span>
                      );
                    },
                  },
                  {
                    key: "board_label",
                    title: "上市板块",
                    render: (_value, row) => <MemberBoardCell row={row} />,
                  },
                  {
                    key: "industries",
                    title: "行业",
                    render: (_value, row) => (
                      <MembershipTags
                        row={row}
                        date={membershipDate}
                        kind="industry"
                      />
                    ),
                  },
                  {
                    key: "topics",
                    title: "概念题材",
                    render: (_value, row) => (
                      <MembershipTags
                        row={row}
                        date={membershipDate}
                        kind="concept"
                      />
                    ),
                  },
                  {
                    key: "change_pct",
                    title:
                      detailMode === "current" && selectedDate !== date
                        ? "所选日涨幅"
                        : "今日涨幅",
                  },
                  ...(detailMode === "current"
                    ? [
                        {
                          key: "drawdown_from_high_pct",
                          title: "收盘距当日高点",
                          render: (value: unknown) => (
                            <span className={tone(value)}>
                              {percent(value)}
                            </span>
                          ),
                        },
                      ]
                    : []),
                  {
                    key: "current_state_label",
                    title:
                      detailMode === "current" && selectedDate !== date
                        ? "所选日收盘状态"
                        : "今日收盘状态",
                    render: (value, row) => {
                      if (detailMode === "current")
                        return <span className="muted">{value}</span>;
                      const price = brokenBoardPriceFeedback(row);
                      const event = [
                        "limit_up",
                        "limit_down",
                        "broken",
                        "limit_break",
                        "conflict",
                      ].includes(row.current_state);
                      return (
                        <div className="broken-board-status-tags">
                          <Badge tone={price.tone}>
                            <span title={price.reason || undefined}>
                              {price.label}
                            </span>
                          </Badge>
                          {event && (
                            <Badge
                              tone={
                                row.current_state === "limit_up"
                                  ? "up"
                                  : row.current_state === "limit_down"
                                    ? "down"
                                    : "neutral"
                              }
                            >
                              <span
                                title={
                                  row.current_state === "limit_up"
                                    ? "本页指昨日炸板、今日来源确认收盘涨停；不声称K线实体包住昨日全部高点"
                                    : undefined
                                }
                              >
                                {value}
                              </span>
                            </Badge>
                          )}
                          {row.current_state === "unknown" && (
                            <small className="muted">事件状态待核验</small>
                          )}
                        </div>
                      );
                    },
                  },
                ]}
                sortScope={
                  detailMode === "current"
                    ? `当前返回的${selectedDate}收盘炸板已核验样本；默认昨日板高降序、同高度今日涨幅降序`
                    : "当前返回的昨日炸板已核验样本；默认昨日板型及今日涨幅降序，统计不随排序和分页变化"
                }
                defaultSort={{ key: "previous_board_count", order: "desc" }}
                pageSize={10}
                emptyTitle={
                  detailMode === "current"
                    ? `暂无可核验的${selectedDate}收盘炸板明细`
                    : "暂无可核验的昨日炸板明细"
                }
                emptyDescription={
                  (detailMode === "current"
                    ? brokenBoardPeriod(detailContext?.current).reason
                    : feedback.reason) ||
                  "缺少来源样本与没有炸板是不同情况，缺档不补0。"
                }
                onStock={onStock}
                onStockNavigate={(id, orderedRows) =>
                  onStock(
                    id,
                    createStockNavigation(orderedRows, {
                      source: "market",
                      scopeKey: `emotion:broken-board:${selectedDate}:${snapshotId || ""}:${detailMode}:${boardFilter}:${heightFilter}:${topicFilter?.key || "all"}`,
                      label:
                        detailMode === "current"
                          ? `${selectedDate} 炸板 · 回落观察`
                          : "昨日炸板 · 今日反馈",
                      asOf: selectedDate,
                    }),
                  )
                }
              />
            </>
          )}
          {detailMode === "previous" && feedback.hiddenItems > 0 && (
            <p className="broken-board-note">
              另有 {feedback.hiddenItems}{" "}
              条证券身份、日期或重复记录待核验，未作为可点击明细展示。
            </p>
          )}
        </details>
        <details className="broken-board-method">
          <summary>统计口径与数据缺口</summary>
          <p>
            {context.scope ||
              "同日、同源、同范围的涨停与炸板来源池，按证券核验和去重。"}
          </p>
          <p>
            炸板比例＝收盘未封住家数
            ÷（收盘涨停家数＋收盘未封住家数）。仅在两池完整、互斥且价格核验通过后展示；分母为0时不计算。
          </p>
          <p>
            昨日样本的均值、中位数仅统计今日可比报价，不代表追板账户收益；涨停反包与跌停另用同日来源事件核验。本页“涨停反包”指昨日炸板、今日确认涨停，不声称今日K线实体已包住昨日全部高点。
          </p>
          {limits.map((item) => (
            <p key={item}>{item}</p>
          ))}
        </details>
      </Panel>
    </section>
  );
}
