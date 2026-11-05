import NoonStockChange from "./NoonStockChange";
import MarketSuspensionBadge from "./MarketSuspensionBadge";
import { useRef, useState } from "react";
import type { ComponentProps, ReactNode } from "react";
import CopyStockCode from "../../components/CopyStockCode";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Badge, Panel as BasePanel } from "../../components/UI";
import type { Row } from "../../lib/api";
import { amount, integer, num, percent, tone } from "../../lib/format";
import { noonLines, noonNumber, noonRows } from "./noonPresentation";
import {
  noonConfirmedHeight,
  noonDirectionState,
  noonFeedbackPage,
  noonFeedbackRows,
  noonLadderRows,
  noonFeedbackState,
} from "./noonWorkbenchPresentation";
import type { NoonFeedbackFilter } from "./noonWorkbenchPresentation";
import NoonLadderTopics from "./NoonLadderTopics";
import { noonLadderCopy } from "./noonCopyPresentation";
import { noonSortRows } from "./noonQuotePresentation";
import { NoonBoardTag } from "./NoonBoardFilter";
import { noonFilterBoards } from "./noonBoardPresentation";
import type { NoonBoardFilterValue } from "./noonBoardPresentation";
import NoonSectorDialog from "./NoonSectorDialog";
import { noonSectorEndpoint } from "./noonSectorPresentation";
import "./NoonWorkbenchAnalysis.css";

type StockProps = {
  onStock?: (id: string) => void;
  chartDate?: string;
  boardFilter?: NoonBoardFilterValue;
  stockSuspensions?: Record<string, Row>;
};
type SectorProps = {
  onSector?: (row: Row, view?: "chart" | "members") => void;
  snapshotId?: string;
};
const sourceReason = (row: Row) =>
  typeof row.limit_up_reason === "string" && row.limit_up_reason.trim()
    ? row.limit_up_reason
    : null;
function sectorLimitUpText(row: Row): string {
  const count = noonNumber(row.member_limit_up_count);
  if (count == null || !Number.isInteger(count) || count < 0) return "—";
  if (row.member_limit_up_state === "observed") return integer(count);
  if (row.member_limit_up_state === "partial" && count > 0)
    return `≥${integer(count)}`;
  return "—";
}
function Panel({ children, ...props }: ComponentProps<typeof BasePanel>) {
  return (
    <BasePanel {...props}>
      <div className="noon-panel-body">{children}</div>
    </BasePanel>
  );
}
function Note({ children }: { children: ReactNode }) {
  return <p className="noon-footnote">{children}</p>;
}
function Stock({
  row,
  onStock,
  chartDate,
  stockSuspensions,
}: StockProps & { row: Row }) {
  return (
    <>
      {onStock && /^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id || "") ? (
        <button
          className="noon-stock-link"
          type="button"
          disabled={!chartDate}
          title={
            chartDate
              ? `正式行情截至 ${chartDate}，同日收盘发布后优先显示完整日 K`
              : "此前暂无已发布收盘行情"
          }
          onClick={() => onStock(row.instrument_id)}
        >
          {row.name || row.instrument_id}
        </button>
      ) : (
        <span>{row.name || row.instrument_id || "身份待核验"}</span>
      )}
      <small className="noon-stock-code-line">
        <CopyStockCode instrumentId={row.instrument_id} />
        <NoonBoardTag row={row} />
        <MarketSuspensionBadge
          evidence={stockSuspensions?.[row.instrument_id]}
        />
      </small>
    </>
  );
}
function State({ value }: { value: unknown }) {
  return (
    <Badge
      tone={
        value === "limit_up"
          ? "good"
          : value === "limit_down"
            ? "danger"
            : ["unknown", "conflict"].includes(String(value))
              ? "warn"
              : "neutral"
      }
    >
      {noonFeedbackState(value)}
    </Badge>
  );
}
type SortOrder = { key: string; direction: "asc" | "desc" };
function SortHeading({
  column,
  sort,
  onSort,
}: {
  column: { key: string; label: string; text?: boolean };
  sort: SortOrder;
  onSort: (key: string) => void;
}) {
  const active = sort.key === column.key;
  return (
    <th
      className={column.text ? "noon-text-column" : "noon-number-column"}
      aria-sort={
        active
          ? sort.direction === "asc"
            ? "ascending"
            : "descending"
          : "none"
      }
    >
      <button
        type="button"
        className="noon-sort-button"
        onClick={() => onSort(column.key)}
        title={`按${column.label}${active && sort.direction === "desc" ? "从低到高" : "从高到低"}排序`}
      >
        {column.label}
        <span aria-hidden="true">
          {active ? (sort.direction === "desc" ? "↓" : "↑") : "↕"}
        </span>
      </button>
    </th>
  );
}
function FeedbackTable({
  rows,
  label,
  filters = false,
  current = false,
  onStock,
  chartDate,
  stockSuspensions,
  boardFilter = "all",
}: StockProps & {
  rows: Row[];
  label: string;
  filters?: boolean;
  current?: boolean;
}) {
  const [filter, setFilter] = useState<NoonFeedbackFilter>("all");
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<SortOrder>({
    key: "height",
    direction: "desc",
  });
  const heightOf = (row: Row) =>
    noonConfirmedHeight(
      current
        ? (row.continue_day_cnt ?? row.board_count)
        : row.previous_board_count,
    );
  const available = (field: string) =>
    rows.some((row) => noonNumber(row[field]) != null);
  const hasHeight = rows.some((row) => heightOf(row) != null);
  const columns = [
    { key: "name", label: "股票", text: true, width: 130 },
    ...(rows.some(
      (row) => noonRows(row.industries).length || noonRows(row.topics).length,
    )
      ? [{ key: "membership", label: "行业 / 概念", text: true, width: 240 }]
      : []),
    ...(hasHeight
      ? [
          {
            key: "height",
            label: current ? "本次来源板数" : "昨日高度",
            width: 100,
          },
        ]
      : []),
    ...(available("change_pct")
      ? [{ key: "change_pct", label: "午间涨跌幅", width: 110 }]
      : []),
    ...(available("latest_price")
      ? [{ key: "latest_price", label: "午间最新价", width: 100 }]
      : []),
    ...(available("high") || available("low")
      ? [{ key: "high", label: "早盘高 / 低", width: 140 }]
      : []),
    ...(available("amount")
      ? [{ key: "amount", label: "累计成交额", width: 120 }]
      : []),
    ...(rows.some((row) => row.current_state && row.current_state !== "unknown")
      ? [{ key: "current_state", label: "本次池状态", text: true, width: 150 }]
      : []),
    ...(current && rows.some((row) => sourceReason(row))
      ? [{ key: "reason", label: "题材/原因", text: true, width: 240 }]
      : []),
  ];
  const effectiveSort = {
    ...sort,
    key: columns.some((column) => column.key === sort.key)
      ? sort.key
      : available("change_pct")
        ? "change_pct"
        : "name",
  };
  const boardRows = noonFilterBoards(rows, boardFilter);
  const filtered = current
    ? noonLadderRows(boardRows)
    : noonFeedbackRows(boardRows, filter);
  const ordered = noonSortRows(
    filtered,
    effectiveSort.key,
    effectiveSort.direction,
    (row, key) =>
      key === "height"
        ? heightOf(row)
        : key === "membership"
          ? [...noonRows(row.industries), ...noonRows(row.topics)]
              .map((item) => item.name)
              .join("、") || null
          : key === "reason"
            ? sourceReason(row)
            : key === "name"
              ? row.name || row.instrument_id
              : key === "current_state"
                ? row.current_state === "unknown"
                  ? null
                  : noonFeedbackState(row.current_state)
                : noonNumber(row[key]),
  );
  const visible = noonFeedbackPage(ordered, page);
  const selectSort = (key: string) => {
    setSort({
      key,
      direction:
        effectiveSort.key === key && effectiveSort.direction === "desc"
          ? "asc"
          : "desc",
    });
    setPage(0);
  };
  const cell = (row: Row, key: string) => {
    if (key === "name")
      return (
        <Stock
          row={row}
          onStock={onStock}
          chartDate={chartDate}
          stockSuspensions={stockSuspensions}
        />
      );
    if (key === "membership") return <MembershipTags row={row} />;
    if (key === "height")
      return heightOf(row) == null ? "—" : `${heightOf(row)}板`;
    if (key === "change_pct")
      return (
        <NoonStockChange value={row.change_pct} state={row.current_state} />
      );
    if (key === "latest_price") return num(noonNumber(row.latest_price));
    if (key === "high")
      return (
        <>
          {num(noonNumber(row.high))} / {num(noonNumber(row.low))}
        </>
      );
    if (key === "amount") return amount(noonNumber(row.amount));
    if (key === "reason") return sourceReason(row) || "—";
    return row.current_state && row.current_state !== "unknown" ? (
      <State value={row.current_state} />
    ) : (
      "—"
    );
  };
  return (
    <div className="noon-feedback-detail">
      {filters && hasHeight && (
        <div
          className="noon-feedback-filters"
          role="group"
          aria-label="昨日高度筛选"
        >
          {(
            [
              ["all", "全部"],
              ["multi", "昨日连板"],
              ["first", "昨日首板"],
              ["unknown", "高度未知"],
            ] as const
          )
            .filter(
              ([key]) =>
                key !== "unknown" || rows.some((row) => heightOf(row) == null),
            )
            .map(([key, title]) => (
              <button
                key={key}
                type="button"
                className="btn small"
                aria-pressed={filter === key}
                onClick={() => {
                  setFilter(key);
                  setPage(0);
                }}
              >
                {title}
              </button>
            ))}
        </div>
      )}
      {!visible.total ? (
        <p className="noon-unknown">
          {boardFilter !== "all"
            ? "该板别暂无展示样本。"
            : "该范围没有可核验样本；来源缺失不按零事件解释。"}
        </p>
      ) : (
        <>
          <div className="noon-table-wrap">
            <table
              className="noon-table noon-feedback-table"
              aria-label={label}
              style={{
                minWidth: columns.reduce(
                  (total, column) => total + column.width,
                  0,
                ),
              }}
            >
              <colgroup>
                {columns.map((column) => (
                  <col key={column.key} style={{ width: column.width }} />
                ))}
              </colgroup>
              <thead>
                <tr>
                  {columns.map((column) => (
                    <SortHeading
                      key={column.key}
                      column={column}
                      sort={effectiveSort}
                      onSort={selectSort}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.rows.map((row, index) => (
                  <tr key={row.instrument_id || index}>
                    {columns.map((column) => (
                      <td
                        key={column.key}
                        className={`${column.text ? "noon-text-column" : "noon-number-column"} ${column.key === "membership" ? "noon-member-classification-cell" : ""} ${column.key === "reason" ? "noon-event-reason-cell" : ""}`}
                      >
                        {cell(row, column.key)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="noon-feedback-pagination">
            <span>
              共 {visible.total} 只
              {boardFilter !== "all" ? ` / 全样本${rows.length}只` : ""} · 显示{" "}
              {visible.start}—{visible.end} · 按
              {
                columns.find((column) => column.key === effectiveSort.key)
                  ?.label
              }
              {effectiveSort.direction === "desc" ? "从高到低" : "从低到高"}排列
            </span>
            <div>
              <button
                className="btn small"
                type="button"
                aria-label={`${label}上一页`}
                disabled={visible.page === 0}
                onClick={() => setPage(visible.page - 1)}
              >
                <ChevronLeft size={13} />
                上一页
              </button>
              <span>
                {visible.page + 1} / {visible.pages}
              </span>
              <button
                className="btn small"
                type="button"
                aria-label={`${label}下一页`}
                disabled={visible.page + 1 >= visible.pages}
                onClick={() => setPage(visible.page + 1)}
              >
                下一页
                <ChevronRight size={13} />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
function GroupCard({ group, ...stockProps }: StockProps & { group: Row }) {
  const boardRows = noonFilterBoards(
    noonRows(group.items),
    stockProps.boardFilter || "all",
  );
  if (noonNumber(group.count) == null)
    return (
      <article className="noon-feedback-group">
        <div className="noon-feedback-group-head">
          <h3>{group.label || "分组待核验"}</h3>
          <Badge tone="warn">未知</Badge>
        </div>
        <p className="noon-unknown">
          昨日事件样本未留存或未通过核验，分组表现保持未知。
        </p>
      </article>
    );
  const pools = group.pool_counts || {};
  return (
    <article className="noon-feedback-group">
      <div className="noon-feedback-group-head">
        <h3>{group.label || "分组待核验"}</h3>
        <span>{integer(noonNumber(group.count))}只</span>
      </div>
      <strong className={tone(noonNumber(group.mean_pct))}>
        {percent(noonNumber(group.mean_pct))}
      </strong>
      <span className="noon-feedback-average">
        平均涨跌幅 · 中位 {percent(noonNumber(group.median_pct))}
      </span>
      <div className="noon-feedback-split">
        <span className="up">涨 {integer(noonNumber(group.up))}</span>
        <span className="down">跌 {integer(noonNumber(group.down))}</span>
        <span>平 {integer(noonNumber(group.flat))}</span>
      </div>
      <div className="noon-feedback-pools">
        <span>涨停池 {integer(noonNumber(pools.limit_up))}</span>
        <span>炸板池 {integer(noonNumber(pools.limit_break))}</span>
        <span>跌停池 {integer(noonNumber(pools.limit_down))}</span>
        <span>未列三池 {integer(noonNumber(pools.not_listed))}</span>
      </div>
      {((noonNumber(pools.unknown) ?? 0) > 0 ||
        (noonNumber(pools.conflict) ?? 0) > 0) && (
        <div className="noon-feedback-pool-gaps">
          <span>池状态未知 {integer(noonNumber(pools.unknown))}</span>
          <span>池状态冲突 {integer(noonNumber(pools.conflict))}</span>
        </div>
      )}
      <Note>
        有效报价 {integer(noonNumber(group.valid_count))} · 未知{" "}
        {integer(noonNumber(group.unknown_count))} · 跌幅≥5%{" "}
        {integer(noonNumber(group.large_drop_count))}
      </Note>
      {stockProps.boardFilter && stockProps.boardFilter !== "all" && (
        <Note>当前板别显示{boardRows.length}只，以上统计仍为完整分组。</Note>
      )}
      {boardRows.length > 0 && (
        <ul
          className="noon-feedback-group-stocks"
          aria-label={`${group.label || "该高度"}对应股票`}
        >
          {noonSortRows(boardRows, "change_pct", "desc")
            .sort(
              (a, b) =>
                (noonConfirmedHeight(b.previous_board_count) || 0) -
                (noonConfirmedHeight(a.previous_board_count) || 0),
            )
            .map((row, index) => {
              const height = noonConfirmedHeight(row.previous_board_count);
              return (
                <li key={row.instrument_id || index}>
                  <div className="noon-feedback-member-identity">
                    <Stock row={row} {...stockProps} />
                    <span className="noon-feedback-member-height">
                      {height == null ? "昨日高度未知" : `昨日${height}板`}
                    </span>
                  </div>
                  <PreviousTheme row={row} />
                  <b>
                    <NoonStockChange
                      value={row.change_pct}
                      state={row.current_state}
                    />
                  </b>
                </li>
              );
            })}
        </ul>
      )}
    </article>
  );
}
function PreviousFeedback({
  workbench,
  ...stockProps
}: StockProps & { workbench: Row }) {
  const feedback = workbench.previous_feedback || {};
  const negative = workbench.negative_feedback || {};
  const groupHeight = (group: Row) =>
    group.key === "board_5_plus"
      ? 5
      : noonConfirmedHeight(group.board_count) ||
        (
          { first: 1, board_2: 2, board_3: 3, board_4: 4 } as Record<
            string,
            number
          >
        )[group.key] ||
        0;
  const groups = noonRows(feedback.groups)
    .filter(
      (group) =>
        group.key !== "height_unknown" || noonNumber(group.count) !== 0,
    )
    .sort((a, b) => groupHeight(b) - groupHeight(a));
  const maximum = noonConfirmedHeight(feedback.max_confirmed_height);
  const cohorts = noonRows(negative.cohorts);
  const verified = noonNumber(feedback.verified_count) != null;
  return (
    <Panel
      title="昨日接力反馈"
      subtitle={`${feedback.date || "前一交易日待核验"} 的强势样本，在本次午间报价中的表现`}
      actions={<Badge>首板与连板分开</Badge>}
    >
      <div className="noon-feedback-coverage">
        <span>
          昨日来源样本 <b>{integer(noonNumber(feedback.source_count))}</b>
        </span>
        <span>
          前日已核验 <b>{integer(noonNumber(feedback.verified_count))}</b>
        </span>
        <span>
          本次有效报价{" "}
          <b>{integer(verified ? noonNumber(feedback.valid_count) : null)}</b>
        </span>
        <span>
          报价未知{" "}
          <b>{integer(verified ? noonNumber(feedback.unknown_count) : null)}</b>
        </span>
      </div>
      <div className="noon-previous-maximum">
        <span>昨日已核验样本最高</span>
        <strong>{maximum == null ? "未知" : `${maximum}板`}</strong>
        {noonRows(feedback.highest_items).map((row, index) => (
          <span
            className="noon-previous-maximum-stock"
            key={row.instrument_id || index}
          >
            <Stock row={row} {...stockProps} />
          </span>
        ))}
      </div>
      <NoonLadderTopics
        value={feedback.classification_counts}
        sampleLabel="昨日涨停"
        {...stockProps}
      />
      {groups.length ? (
        <div className="noon-feedback-groups">
          {groups.map((group, index) => (
            <GroupCard group={group} key={group.key || index} {...stockProps} />
          ))}
        </div>
      ) : (
        <p className="noon-unknown">
          没有可核验的昨日分组样本。未保存复盘原文，不影响已有事件样本的事实反馈。
        </p>
      )}
      <Note>
        均值、中位数使用各组全部有效样本，切换筛选或翻页不会改变统计。午间池状态不认定收盘晋级、断板或收益兑现。
      </Note>
      {feedback.reason && <Note>{feedback.reason}</Note>}
      <details className="noon-details">
        <summary>
          查看昨日首板、连板完整样本 ·{" "}
          {integer(noonNumber(feedback.verified_count))} 只
        </summary>
        <FeedbackTable
          label="昨日接力完整样本"
          rows={noonRows(feedback.items)}
          filters
          {...stockProps}
        />
      </details>
      <div className="noon-negative-grid">
        {cohorts.map((cohort, index) => (
          <article className="noon-negative-cohort" key={cohort.key || index}>
            <h3>{cohort.label || "昨日失败样本反馈"}</h3>
            {cohort.state === "unknown" ||
            noonNumber(cohort.verified_count) == null ? (
              <p className="noon-unknown">
                {cohort.reason || "昨日指定日事件样本未留存，保持未知。"}
              </p>
            ) : (
              <>
                <div className="noon-negative-metrics">
                  <span>
                    已核验 <b>{integer(noonNumber(cohort.verified_count))}</b>
                  </span>
                  <span>
                    午间上涨{" "}
                    <b className="up">{integer(noonNumber(cohort.up))}</b>
                  </span>
                  <span>
                    午间下跌{" "}
                    <b className="down">{integer(noonNumber(cohort.down))}</b>
                  </span>
                  <span>
                    平均{" "}
                    <b className={tone(noonNumber(cohort.mean_pct))}>
                      {percent(noonNumber(cohort.mean_pct))}
                    </b>
                  </span>
                </div>
                <Note>
                  本次有效报价 {integer(noonNumber(cohort.valid_count))} · 未知{" "}
                  {integer(noonNumber(cohort.unknown_count))} · 中位{" "}
                  {percent(noonNumber(cohort.median_pct))}
                </Note>
                <NoonLadderTopics
                  value={cohort.classification_counts}
                  limit={5}
                  showSampleChange
                  sampleLabel={
                    cohort.key === "limit_down" ? "昨日跌停" : "昨日炸板"
                  }
                  {...stockProps}
                />
                <details className="noon-details" open>
                  <summary>查看全部样本</summary>
                  <FeedbackTable
                    label={cohort.label || "昨日失败样本反馈"}
                    rows={noonRows(cohort.items)}
                    {...stockProps}
                  />
                </details>
                {cohort.reason && <Note>{cohort.reason}</Note>}
              </>
            )}
          </article>
        ))}
      </div>
      {noonRows(negative.high_position_drops).length > 0 && (
        <details className="noon-details" open>
          <summary>
            昨日高位样本明显回落 ·{" "}
            {noonRows(negative.high_position_drops).length} 只
          </summary>
          <FeedbackTable
            label="昨日高位明显回落"
            rows={noonRows(negative.high_position_drops)}
            {...stockProps}
          />
        </details>
      )}
      {negative.reason && <Note>{negative.reason}</Note>}
    </Panel>
  );
}
function SourceClues({
  reason,
  limit = 3,
  empty = "题材未提供",
  note = "仅按来源中的“+”分隔展示，不代表已核验归因。",
  heading = "本次来源原文",
}: {
  reason: string | null;
  limit?: number;
  empty?: string;
  note?: string;
  heading?: string;
}) {
  if (!reason)
    return <span className="noon-ladder-clues-missing">{empty}</span>;
  const labels = reason
    .split("+")
    .map((label) => label.trim())
    .filter(Boolean);
  if (!labels.length) labels.push(reason);
  const remaining = Math.max(0, labels.length - limit);
  return (
    <div
      className="noon-ladder-clues"
      title={`${note}\n${reason}`}
      aria-label={`来源题材线索：${reason}`}
    >
      {labels.slice(0, limit).map((label, index) => (
        <span className="noon-ladder-clue" key={index} title={label}>
          {label}
        </span>
      ))}
      {remaining > 0 && (
        <details className="noon-ladder-source">
          <summary title="展开完整来源原文">+{remaining}</summary>
          <div>
            <span>{heading}</span>
            <p>{reason}</p>
            <small>{note}</small>
          </div>
        </details>
      )}
    </div>
  );
}
function LadderClues({ row }: { row: Row }) {
  return (
    <div className="noon-member-classification">
      <MembershipTags row={row} />
      {sourceReason(row) && (
        <details className="noon-reason-details">
          <summary>涨停原因</summary>
          <SourceClues reason={sourceReason(row)} />
        </details>
      )}
    </div>
  );
}
function MembershipTags({ row, limit = 3 }: { row: Row; limit?: number }) {
  const tags = [...noonRows(row.industries), ...noonRows(row.topics)].filter(
    (tag) => typeof tag.name === "string" && tag.name.trim(),
  );
  if (!tags.length)
    return (
      <span
        className="noon-membership-missing"
        title={row.membership_reason || "未取得原采集代际成员归属"}
      >
        行业/概念未留存
      </span>
    );
  const firstIndustry = tags.find((item) => item.kind === "industry");
  const firstConcept = tags.find((item) => item.kind === "concept");
  const leading = [firstIndustry, firstConcept].filter(
    (item): item is Row => !!item,
  );
  const featured = [
    ...leading,
    ...tags.filter((item) => !leading.includes(item)),
  ].slice(0, limit);
  const tag = (item: Row, index: number) => (
    <span
      key={`${item.topic_code}-${index}`}
      className={`noon-membership-tag ${item.kind === "industry" ? "industry" : "concept"}`}
      title={`${item.kind === "industry" ? "行业" : "概念"}：${item.name}；成员名单日 ${item.members_observed_date || "未知"}；成员归属不是涨停原因`}
    >
      {item.name}
    </span>
  );
  return (
    <div className="noon-membership-tags" aria-label="原采集代际行业与概念归属">
      {featured.map(tag)}
      {tags.length > limit && (
        <details className="noon-membership-more">
          <summary title="展开全部行业与概念">+{tags.length - limit}</summary>
          <div>
            {tags.map(tag)}
            <small>
              名单日{" "}
              {Array.from(
                new Set(
                  tags.map((item) => item.members_observed_date || "未知"),
                ),
              ).join("、")}{" "}
              · 成员归属，不代表上涨原因
            </small>
          </div>
        </details>
      )}
    </div>
  );
}
function PreviousTheme({ row }: { row: Row }) {
  const reason =
    typeof row.theme_text === "string" && row.theme_text.trim()
      ? row.theme_text
      : null;
  if (!reason)
    return (
      <div className="noon-feedback-member-theme">
        <MembershipTags row={row} limit={2} />
      </div>
    );
  const origins: Record<string, { label: string; note: string }> = {
    current_noon_pool: {
      label: "午间来源",
      note: "本次午间涨停池来源原文，不是昨日归因。",
    },
    previous_session_pool: {
      label: "昨日来源",
      note: "严格前一交易日事件池留存原文，不代表已核验归因。",
    },
    previous_manual: {
      label: "人工判断",
      note: "昨日留存的人工判断，不代表已核验归因。",
    },
  };
  const origin = origins[row.theme_source] || {
    label: "来源待核验",
    note: "题材来源类别未提供。",
  };
  const note = `${origin.note} 来源日期：${typeof row.theme_date === "string" ? row.theme_date : "未提供"}`;
  return (
    <div className="noon-feedback-member-theme">
      <MembershipTags row={row} limit={2} />
      <details className="noon-reason-details">
        <summary>来源原因</summary>
        <span className="noon-feedback-theme-origin" title={note}>
          {origin.label}
        </span>
        <SourceClues
          reason={reason}
          limit={2}
          note={note}
          heading="留存来源原文"
        />
      </details>
    </div>
  );
}
function CopyLadder({
  rows,
  label = "复制本组代码",
}: {
  rows: Row[];
  label?: string;
}) {
  const codes = noonLadderCopy(rows);
  const [feedback, setFeedback] = useState({ codes: "", status: "" });
  const status = feedback.codes === codes ? feedback.status : "";
  return (
    <span className="noon-copy-control">
      <button
        type="button"
        className="btn small"
        disabled={!codes}
        aria-label={label}
        title="仅复制六位股票代码，每行一个"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(codes);
            setFeedback({ codes, status: "已复制" });
          } catch {
            setFeedback({ codes, status: "复制失败，请选中文本复制" });
          }
        }}
      >
        {status === "已复制" ? status : label}
      </button>
      {status && status !== "已复制" && <small role="alert">{status}</small>}
    </span>
  );
}
function LadderGroup({ group, ...stockProps }: StockProps & { group: Row }) {
  const [sort, setSort] = useState<SortOrder>({
    key: "change_pct",
    direction: "desc",
  });
  const rows = noonFilterBoards(
    noonRows(group.items),
    stockProps.boardFilter || "all",
  );
  const hasClues = rows.some(
    (row) =>
      noonRows(row.industries).length ||
      noonRows(row.topics).length ||
      sourceReason(row),
  );
  const columns = [
    { key: "name", label: "股票", text: true },
    { key: "change_pct", label: "午间涨幅" },
    ...(hasClues
      ? [{ key: "membership", label: "行业 / 概念与原因", text: true }]
      : []),
  ];
  const ordered = noonSortRows(rows, sort.key, sort.direction, (row, key) =>
    key === "membership"
      ? [...noonRows(row.industries), ...noonRows(row.topics)]
          .map((item) => item.name)
          .join("、") || sourceReason(row)
      : key === "name"
        ? row.name || row.instrument_id
        : noonNumber(row.change_pct),
  );
  const first = Number(group.board_count) === 1;
  const middle = Math.ceil(ordered.length / 2);
  const halves =
    first && ordered.length > 1
      ? [ordered.slice(0, middle), ordered.slice(middle)]
      : [ordered];
  const onSort = (key: string) =>
    setSort({
      key,
      direction: sort.key === key && sort.direction === "desc" ? "asc" : "desc",
    });
  return (
    <article
      className={`noon-ladder-group ${first ? "noon-ladder-first" : "noon-ladder-high"}`}
    >
      <h3 className="noon-ladder-height">
        <strong>
          {integer(noonNumber(group.board_count))}
          <small>板</small>
        </strong>
        <span>
          {stockProps.boardFilter && stockProps.boardFilter !== "all"
            ? `${rows.length} / `
            : ""}
          {integer(noonNumber(group.count))}只
        </span>
        <CopyLadder rows={ordered} />
      </h3>
      <div
        className={`noon-ladder-table-wrap ${first ? "noon-ladder-first-columns" : ""}`}
      >
        {halves.map((items, half) => (
          <table
            key={half}
            className="noon-ladder-table"
            aria-label={`${group.board_count}板股票与题材线索${halves.length > 1 ? `第${half + 1}列` : ""}`}
          >
            <colgroup>
              <col className="noon-ladder-stock-col" />
              <col className="noon-ladder-change-col" />
              {hasClues && <col />}
            </colgroup>
            <thead>
              <tr>
                {columns.map((column) => (
                  <SortHeading
                    key={column.key}
                    column={column}
                    sort={sort}
                    onSort={onSort}
                  />
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((row, index) => (
                <tr key={row.instrument_id || index}>
                  <td className="noon-ladder-stock-cell noon-text-column">
                    <Stock row={row} {...stockProps} />
                  </td>
                  <td className="noon-number-column">
                    <NoonStockChange
                      value={row.change_pct}
                      state={row.current_state}
                    />
                  </td>
                  {hasClues && (
                    <td className="noon-text-column">
                      <LadderClues row={row} />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      </div>
    </article>
  );
}
function TodayLadder({ value, ...stockProps }: StockProps & { value: Row }) {
  const groups = noonRows(value.distribution).sort(
    (a, b) =>
      (noonNumber(b.board_count) || 0) - (noonNumber(a.board_count) || 0),
  );
  const rows = noonFilterBoards(
    noonRows(value.items),
    stockProps.boardFilter || "all",
  );
  const boardGroups = groups.filter(
    (group) =>
      noonFilterBoards(noonRows(group.items), stockProps.boardFilter || "all")
        .length > 0,
  );
  return (
    <Panel
      title="今日连板梯队"
      subtitle="仅描述本次已核验涨停池及来源连续板数"
      actions={
        <div className="noon-ladder-actions">
          <Badge>午间状态</Badge>
          <CopyLadder rows={noonLadderRows(rows)} label="复制全部代码" />
        </div>
      }
    >
      <NoonLadderTopics value={value.classification_counts} {...stockProps} />
      {boardGroups.length ? (
        <div className="noon-ladder-list">
          {boardGroups.map((group, index) => (
            <LadderGroup
              key={group.board_count || index}
              group={group}
              {...stockProps}
            />
          ))}
        </div>
      ) : (
        <p className="noon-unknown">
          {stockProps.boardFilter && stockProps.boardFilter !== "all"
            ? "该板别暂无展示样本。"
            : value.reason || "本次涨停池或连续板数尚未通过核验。"}
        </p>
      )}
      {(value.state === "unknown" ||
        (noonNumber(value.unknown_count) || 0) > 0) && (
        <Note>
          高度未知{" "}
          {integer(
            value.state === "unknown" ? null : noonNumber(value.unknown_count),
          )}{" "}
          只。来源“几天几板”文本不替代连续板数。
        </Note>
      )}
      <details className="noon-details">
        <summary>
          查看今日梯队完整样本 ·{" "}
          {value.state === "unknown" && !rows.length
            ? "样本未知"
            : `${rows.length} 只`}
        </summary>
        <FeedbackTable
          label="今日梯队完整样本"
          rows={rows}
          current
          {...stockProps}
        />
      </details>
      <Note>
        {value.scope || value.reason || "本次池范围不代表全市场收盘确认高度。"}
      </Note>
      <Note>
        行业/概念为带名单日期的成员归属；涨停原因保留来源原文，不代表已核验归因。
      </Note>
    </Panel>
  );
}
function SectorTable({
  rows,
  label,
  compare = false,
  onSector,
  selected,
  onSelect,
}: {
  rows: Row[];
  label: string;
  compare?: boolean;
  selected: Row | undefined;
  onSelect: (row: Row) => void;
} & SectorProps) {
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<SortOrder>({
    key: "change_pct",
    direction: label.includes("偏弱") ? "asc" : "desc",
  });
  const columns = [
    { key: "name", label: "方向与成分股", text: true },
    ...(compare &&
    rows.some((row) => noonNumber(row.previous_change_pct) != null)
      ? [{ key: "previous_change_pct", label: "前日收盘涨跌幅" }]
      : []),
    ...(rows.some((row) => noonNumber(row.change_pct) != null)
      ? [{ key: "change_pct", label: "本次午间涨跌幅" }]
      : []),
    ...(rows.some((row) => sectorLimitUpText(row) !== "—")
      ? [{ key: "member_limit_up_count", label: "样本涨停" }]
      : []),
  ];
  const ordered = noonSortRows(rows, sort.key, sort.direction, (row, key) =>
    key === "name" ? row.name || row.instrument_id : noonNumber(row[key]),
  );
  const visible = noonFeedbackPage(ordered, page);
  const selectSort = (key: string) => {
    setSort({
      key,
      direction: sort.key === key && sort.direction === "desc" ? "asc" : "desc",
    });
    setPage(0);
  };
  if (!rows.length)
    return <p className="noon-unknown">该范围暂无可核验的同来源代码报价。</p>;
  return (
    <>
      <div className="noon-table-wrap">
        <table className="noon-table noon-direction-table" aria-label={label}>
          <thead>
            <tr>
              {columns.map((column) => (
                <SortHeading
                  key={column.key}
                  column={column}
                  sort={sort}
                  onSort={selectSort}
                />
              ))}
            </tr>
          </thead>
          <tbody>
            {(compare ? visible.rows : ordered).map((row, index) => (
              <tr
                key={row.instrument_id || index}
                aria-selected={row === selected}
                className={row === selected ? "selected" : ""}
                onClick={() => onSelect(row)}
              >
                {columns.map((column) => {
                  const key = column.key;
                  return (
                    <td
                      key={key}
                      className={`${column.text ? "noon-text-column" : "noon-number-column"} ${key === "member_limit_up_count" ? "noon-sector-limit-up" : ""} ${key.includes("change_pct") ? tone(noonNumber(row[key])) : ""}`}
                      title={
                        key === "member_limit_up_count"
                          ? row.member_limit_up_reason ||
                            "成员涨停样本或覆盖范围尚未核验"
                          : undefined
                      }
                    >
                      {key === "name" ? (
                        <>
                          <div className="noon-sector-actions">
                            {onSector ? (
                              <button
                                type="button"
                                className="noon-sector-link"
                                title="查看该来源板块K线"
                                aria-label={`查看${row.name || row.instrument_id}板块K线`}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  onSector(row, "chart");
                                }}
                              >
                                {row.name || row.instrument_id}
                              </button>
                            ) : (
                              <span>{row.name || row.instrument_id}</span>
                            )}
                            <button
                              type="button"
                              className="noon-sector-link noon-sector-members-chip"
                              aria-label={`查看${row.name || row.instrument_id}成分股`}
                              title="在右侧查看该来源板块成分股"
                              aria-pressed={row === selected}
                              onClick={(event) => {
                                event.stopPropagation();
                                onSelect(row);
                              }}
                            >
                              成分股
                            </button>
                          </div>
                          <small>
                            {row.kind === "industry"
                              ? "行业"
                              : row.kind === "concept"
                                ? "概念"
                                : "分类待核验"}{" "}
                            · {row.instrument_id}
                          </small>
                        </>
                      ) : key.includes("change_pct") ? (
                        percent(noonNumber(row[key]))
                      ) : (
                        sectorLimitUpText(row)
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {compare && rows.length > 12 && (
        <div className="noon-feedback-pagination">
          <span>
            共 {visible.total} 个方向 · 显示 {visible.start}—{visible.end}
          </span>
          <div>
            <button
              type="button"
              className="btn small"
              aria-label={`${label}上一页`}
              disabled={visible.page === 0}
              onClick={() => setPage(visible.page - 1)}
            >
              <ChevronLeft size={13} />
              上一页
            </button>
            <span>
              {visible.page + 1} / {visible.pages}
            </span>
            <button
              type="button"
              className="btn small"
              aria-label={`${label}下一页`}
              disabled={visible.page + 1 >= visible.pages}
              onClick={() => setPage(visible.page + 1)}
            >
              下一页
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
export function NoonDirections({
  value,
  onSector,
  snapshotId,
  ...stockProps
}: StockProps & SectorProps & { value: Row }) {
  const watched = noonRows(value.watched);
  const modes = [
    ["current_leaders", "本次领涨方向"],
    ["current_laggards", "本次偏弱方向"],
    ["comparison", "板块跨日对照"],
  ] as const;
  const [mode, setMode] = useState<string>("current_leaders");
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState("");
  const detailRef = useRef<HTMLElement>(null);
  const scope = `${snapshotId || ""}:${mode}:${kind}:${query}`;
  const identity = (row: Row) =>
    `${row.kind}:${row.instrument_id || row.topic_code}`;
  const rows = noonSortRows(
    noonRows(value[mode]).filter(
      (row) =>
        (kind === "all" || row.kind === kind) &&
        `${row.name || ""} ${row.instrument_id || row.topic_code || ""}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
    ),
    "change_pct",
    mode === "current_laggards" ? "asc" : "desc",
    (row, key) => noonNumber(row[key]),
  );
  const selected =
    rows.find((row) => selection === `${scope}:${identity(row)}`) || rows[0];
  const code = selected?.topic_code || selected?.instrument_id;
  const canReadMembers =
    !!selected &&
    !!snapshotId &&
    ["industry", "concept"].includes(selected.kind) &&
    !(
      selected.topic_code &&
      selected.instrument_id &&
      selected.topic_code !== selected.instrument_id
    ) &&
    !!noonSectorEndpoint(snapshotId, code);
  const selectRow = (row: Row) => {
    setSelection(`${scope}:${identity(row)}`);
    const detail = detailRef.current;
    detail?.scrollTo({ top: 0 });
    if (
      detail &&
      detail.parentElement &&
      detail.getBoundingClientRect().width >
        detail.parentElement.getBoundingClientRect().width * 0.9
    )
      detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  return (
    <Panel
      title="主线与板块对照"
      subtitle="左侧选方向，右侧看午间成分股；点击板块名称查看K线"
    >
      <div className="noon-direction-controls">
        <div className="segmented" role="group" aria-label="午间板块观察方式">
          {modes.map(([key, label]) => (
            <button
              type="button"
              key={key}
              className={mode === key ? "active" : ""}
              aria-pressed={mode === key}
              onClick={() => setMode(key)}
            >
              {label}
              <span>{noonRows(value[key]).length}</span>
            </button>
          ))}
        </div>
        <div className="noon-direction-filters">
          <div className="segmented" role="group" aria-label="午间方向类型">
            {[
              ["all", "全部"],
              ["industry", "行业"],
              ["concept", "概念"],
            ].map(([key, label]) => (
              <button
                type="button"
                key={key}
                className={kind === key ? "active" : ""}
                aria-pressed={kind === key}
                onClick={() => setKind(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <input
            className="search-input"
            aria-label="搜索午间板块"
            placeholder="板块名称 / 代码"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query && (
            <button
              className="btn small"
              type="button"
              onClick={() => setQuery("")}
            >
              清除搜索
            </button>
          )}
        </div>
      </div>
      <div className="noon-direction-explorer">
        <section className="noon-direction-ranking" aria-label="午间板块榜单">
          <div className="noon-direction-heading">
            <h3>{modes.find(([key]) => key === mode)?.[1]}</h3>
            <Badge>{rows.length} 个方向</Badge>
          </div>
          <Note>
            {mode === "comparison"
              ? `同来源代码 · 前日 ${value.previous_date || "待核验"}`
              : "行业、概念各取本份快照前三项"}{" "}
            · 点行看成分股
          </Note>
          <SectorTable
            key={scope}
            rows={rows}
            label={modes.find(([key]) => key === mode)?.[1] || "午间板块"}
            compare={mode === "comparison"}
            onSector={onSector}
            selected={selected}
            onSelect={selectRow}
          />
        </section>
        <section
          className="noon-direction-detail"
          aria-label="所选午间方向与成员"
          ref={detailRef}
        >
          <div className="noon-direction-heading">
            <div>
              <h3>{selected?.name || selected?.instrument_id || "方向展开"}</h3>
              <small>
                {selected
                  ? `${selected.kind === "industry" ? "行业" : selected.kind === "concept" ? "概念" : "分类待核验"} · ${code || "代码未核验"}`
                  : "选中左侧方向查看本份午间成员"}
              </small>
            </div>
            {selected && onSector && (
              <button
                className="btn small"
                type="button"
                onClick={() => onSector(selected, "chart")}
              >
                板块K线
              </button>
            )}
          </div>
          {selected ? (
            <>
              <div className="noon-direction-metrics">
                <span>
                  午间涨幅{" "}
                  <b className={tone(noonNumber(selected.change_pct))}>
                    {percent(noonNumber(selected.change_pct))}
                  </b>
                </span>
                <span
                  title={
                    selected.member_limit_up_reason ||
                    "成员涨停样本或覆盖范围尚未核验"
                  }
                >
                  样本涨停 <b>{sectorLimitUpText(selected)}</b>
                </span>
                <span>
                  成员上涨 / 下跌{" "}
                  <b className="up">
                    {integer(noonNumber(selected.member_up))}
                  </b>{" "}
                  /{" "}
                  <b className="down">
                    {integer(noonNumber(selected.member_down))}
                  </b>
                </span>
                {mode === "comparison" && (
                  <>
                    <span>
                      前日收盘涨幅{" "}
                      <b
                        className={tone(
                          noonNumber(selected.previous_change_pct),
                        )}
                      >
                        {percent(noonNumber(selected.previous_change_pct))}
                      </b>
                    </span>
                    <span>
                      跨日表现 <b>{noonDirectionState(selected.state)}</b>
                    </span>
                  </>
                )}
              </div>
              <Note>
                名单日 {selected.members_observed_date || "未核验"} · 有效{" "}
                {integer(noonNumber(selected.member_coverage?.valid))} /{" "}
                {integer(noonNumber(selected.member_coverage?.total))}
                ；成员缺档不影响板块自身报价。
              </Note>
              {canReadMembers ? (
                <NoonSectorDialog
                  key={`${snapshotId}:${identity(selected)}`}
                  topic={{ ...selected, topic_code: code }}
                  snapshotId={snapshotId!}
                  closeChartDate={stockProps.chartDate}
                  embedded
                  onStock={stockProps.onStock}
                />
              ) : (
                <p className="noon-unknown">
                  未取得本份午评身份或明确来源代码，成分股暂不可用。
                </p>
              )}
            </>
          ) : (
            <p className="noon-unknown">
              当前范围没有匹配方向，可切换榜单、类型或清除搜索。
            </p>
          )}
        </section>
      </div>
      {watched.length > 0 && (
        <div className="noon-watched-groups">
          {watched.map((row, index) => (
            <article key={row.mainline || index}>
              <h3>昨日原关注 · {row.mainline || "归属待核验"}</h3>
              <div className="noon-negative-metrics">
                <span>样本 {integer(noonNumber(row.count))}</span>
                <span>
                  上涨 <b className="up">{integer(noonNumber(row.up))}</b>
                </span>
                <span>
                  下跌 <b className="down">{integer(noonNumber(row.down))}</b>
                </span>
                <span>
                  均值{" "}
                  <b className={tone(noonNumber(row.mean_pct))}>
                    {percent(noonNumber(row.mean_pct))}
                  </b>
                </span>
              </div>
              <Note>
                有效 {integer(noonNumber(row.valid_count))} · 未知{" "}
                {integer(noonNumber(row.unknown_count))}
                。仅对照已明确关联股票，不自动判定原判断兑现。
              </Note>
              <details className="noon-details" open>
                <summary>查看原关注股票</summary>
                <FeedbackTable
                  label={`原关注${row.mainline || "股票"}`}
                  rows={noonRows(row.items)}
                  {...stockProps}
                />
              </details>
            </article>
          ))}
        </div>
      )}
      <Note>
        {value.reason ||
          "未留存前日同代码板块行情时，仅展示本次方向，不推断轮动或新主线。"}
      </Note>
    </Panel>
  );
}
function Afternoon({ rows }: { rows: Row[] }) {
  return (
    <Panel
      title="午后观察清单"
      subtitle="对象、当前证据和待验证条件一并保留"
      actions={<Badge>事实观察</Badge>}
    >
      {!rows.length ? (
        <p className="noon-unknown">
          当前保存事实不足，尚不能形成具体的午后观察项。
        </p>
      ) : (
        <ol className="noon-afternoon-list">
          {rows.map((row, index) => (
            <li key={`${row.kind || "item"}-${row.object || index}`}>
              <div>
                <span className="noon-afternoon-index">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <h3>{row.label || row.object || "观察对象待核验"}</h3>
              </div>
              <p>{row.evidence || "当前证据缺失"}</p>
              <p className="noon-afternoon-condition">
                <b>午后核对</b>
                {row.condition || "验证条件尚未形成"}
              </p>
              {row.scope && <Note>{row.scope}</Note>}
            </li>
          ))}
        </ol>
      )}
      <Note>
        这是基于本次快照的观察清单；不自动取数，不触发策略、跟踪或回测事件。
      </Note>
    </Panel>
  );
}
function PreviousCloseFeedback({
  value,
  ...stockProps
}: StockProps & { value: Row }) {
  const groups = noonRows(value?.groups).filter(
    (group) => noonNumber(group.source_count) != null && group.source_count > 0,
  );
  if (!groups.length) return null;
  return (
    <Panel
      title="昨日午后变化反馈"
      subtitle={
        (value.date || "前一交易日") +
        "留存的回封、开板名单，在本次午间报价中的表现"
      }
    >
      <div className="noon-close-feedback-groups">
        {groups.map((group, index) => (
          <article key={group.key || index} className="noon-feedback-group">
            <h3>{group.label || "昨日午后关注"}</h3>
            <div className="noon-inline-metrics">
              <span>
                原名单 <b>{integer(noonNumber(group.source_count))}</b>
              </span>
              <span>
                上涨 <b className="up">{integer(noonNumber(group.up))}</b>
              </span>
              <span>
                下跌 <b className="down">{integer(noonNumber(group.down))}</b>
              </span>
              <span>
                平均{" "}
                <b className={tone(noonNumber(group.mean_pct))}>
                  {percent(noonNumber(group.mean_pct))}
                </b>
              </span>
            </div>
            <Note>
              有效 {integer(noonNumber(group.valid_count))} · 未知{" "}
              {integer(noonNumber(group.unknown_count))}
              。附加名单可能重叠，不计入原五组家数或温度。
            </Note>
            <details className="noon-details" open>
              <summary>查看对应股票</summary>
              <FeedbackTable
                label={group.label || "昨日午后变化"}
                rows={noonRows(group.items)}
                {...stockProps}
              />
            </details>
          </article>
        ))}
      </div>
    </Panel>
  );
}
export default function NoonWorkbenchAnalysis({
  workbench,
  onSector,
  snapshotId,
  ...stockProps
}: StockProps & SectorProps & { workbench: Row | null | undefined }) {
  if (!workbench || workbench.version !== "noon-workbench-v1")
    return (
      <Panel title="跨日接力分析">
        <p className="noon-unknown">
          该快照尚未提供可核验的跨日分析，原始午间事实与昨日留存仍可查看。
        </p>
      </Panel>
    );
  return (
    <section className="noon-cross-session" aria-label="午评跨日接力分析">
      <section id="noon-prior-feedback">
        <PreviousFeedback workbench={workbench} {...stockProps} />
        <PreviousCloseFeedback
          value={workbench.previous_close_feedback || {}}
          {...stockProps}
        />
      </section>
      <section id="noon-today-ladder">
        <TodayLadder value={workbench.today_ladder || {}} {...stockProps} />
      </section>
      <section id="noon-directions">
        <NoonDirections
          key={snapshotId}
          value={workbench.directions || {}}
          onSector={onSector}
          snapshotId={snapshotId}
          {...stockProps}
        />
      </section>
      <section id="noon-afternoon">
        <Afternoon rows={noonRows(workbench.afternoon)} />
      </section>
      {noonLines(workbench.gaps).length > 0 && (
        <details className="noon-details noon-analysis-gaps">
          <summary>
            本次接力分析缺口 · {noonLines(workbench.gaps).length} 项
          </summary>
          <ul className="noon-lines">
            {noonLines(workbench.gaps).map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
