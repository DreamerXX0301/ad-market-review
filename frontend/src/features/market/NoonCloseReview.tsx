import { useEffect, useRef, useState } from "react";
import type { ComponentProps } from "react";
import { ChevronDown, ChevronUp, Copy, RefreshCw } from "lucide-react";
import { api, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Panel as BasePanel,
} from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import {
  amount,
  dateTime,
  integer,
  num,
  percent,
  tone,
} from "../../lib/format";
import NoonBoardFilter, { NoonBoardTag } from "./NoonBoardFilter";
import type { NoonBoardFilterValue } from "./noonBoardPresentation";
import {
  CLOSE_SECTIONS,
  CLOSE_TRANSITIONS,
  closeCopyCodes,
  closeCountTone,
  closeDayEventLabel,
  closeItemsPath,
  closeMetric,
  closeNumber,
  closeRows,
  closeStateMessages,
  closeTransitionLabel,
  closeTransitionTone,
} from "./noonClosePresentation";
import type { CloseSection, CloseSort } from "./noonClosePresentation";
import NoonCloseFocusTable from "./NoonCloseFocusTable";
import "./NoonCloseReview.css";

type StockAction = (id: string, date: string) => void;
type SectorAction = (row: Row, view: "chart" | "members", date: string) => void;
const METRICS = [
  ["kept", "封板保持", "up"],
  ["resealed", "炸板回封", "up"],
  ["new_limit_up", "未封 → 收盘涨停", "up"],
  ["opened", "封板 → 收盘未封", "down"],
] as const;
function Panel({ children, ...props }: ComponentProps<typeof BasePanel>) {
  return (
    <BasePanel {...props}>
      <div className="noon-close-panel-body">{children}</div>
    </BasePanel>
  );
}

export function CloseMetricStrip({ metrics }: { metrics: Row }) {
  return (
    <div className="noon-close-metrics">
      {METRICS.map(([key, label, color]) => {
        const value = closeMetric(metrics?.[key]);
        return (
          <article key={key}>
            <span>{label}</span>
            <strong
              className={
                value.count == null
                  ? "close-warn"
                  : value.count === 0
                    ? "muted"
                    : color
              }
            >
              {integer(value.count)}
              {["kept", "resealed"].includes(key) &&
                value.denominator != null && (
                  <small> / {integer(value.denominator)}</small>
                )}
              {!["kept", "resealed"].includes(key) && <small>只</small>}
            </strong>
            <small>
              原名单 {integer(value.source)} · 待核验 {integer(value.unknown)}
              {["kept", "resealed"].includes(key) && value.rate != null && (
                <> · {num(value.rate)}%</>
              )}
            </small>
          </article>
        );
      })}
    </div>
  );
}
function CloseTags({ row }: { row: Row }) {
  const values = [...closeRows(row.industries), ...closeRows(row.topics)];
  if (!values.length) return <span className="muted">行业 / 概念未留存</span>;
  const tag = (item: Row, index: number) => (
    <span
      className="noon-close-tag"
      key={String(item.key || item.topic_code || item.name) + index}
      title={[item.source, item.topic_code, item.members_observed_date]
        .filter(Boolean)
        .join(" · ")}
    >
      {item.name || item.topic_name || item.topic_code}
    </span>
  );
  return (
    <div className="noon-close-tags">
      {values.slice(0, 2).map(tag)}
      {values.length > 2 && (
        <details>
          <summary>+{values.length - 2}</summary>
          <div>{values.slice(2).map(tag)}</div>
        </details>
      )}
    </div>
  );
}
const tableColumns: { key: CloseSort | null; label: string; text?: boolean }[] =
  [
    { key: "name", label: "股票 / 板别", text: true },
    { key: null, label: "行业 / 概念", text: true },
    { key: "final_board_count", label: "昨日 / 收盘高度" },
    { key: null, label: "午间 → 收盘", text: true },
    { key: "noon_change_pct", label: "午间涨幅" },
    { key: "close_change_pct", label: "今日收盘涨幅" },
    { key: "afternoon_change_pct", label: "午后涨跌幅" },
    { key: "amount", label: "收盘成交额" },
  ];
export function CloseRowsTable({
  rows,
  date,
  onStock,
  sort,
  direction,
  onSort,
  availableFields,
}: {
  rows: Row[];
  date: string;
  onStock?: StockAction;
  sort: CloseSort;
  direction: "asc" | "desc";
  onSort: (key: CloseSort) => void;
  availableFields?: string[];
}) {
  const available = (key: string) =>
    availableFields
      ? availableFields.includes(key)
      : rows.some((row) =>
          ["industries", "topics"].includes(key)
            ? closeRows(row[key]).length > 0
            : closeNumber(row[key]) != null,
        );
  const showClasses = available("industries") || available("topics");
  const hiddenColumns = [
    false,
    !showClasses,
    !(available("previous_board_count") || available("final_board_count")),
    false,
    false,
    false,
    !available("afternoon_change_pct"),
    !available("close_amount"),
  ];
  const widths = [132, 155, 105, 180, 100, 115, 100, 110];
  const sortButton = (key: CloseSort, label: string) => (
    <button
      type="button"
      className="noon-close-sort"
      onClick={() => onSort(key)}
      aria-label={
        "收盘回看按" +
        label +
        (sort === key && direction === "desc" ? "升序" : "降序")
      }
    >
      {label}
      <span aria-hidden="true">
        {sort === key ? (direction === "desc" ? " ↓" : " ↑") : " ↕"}
      </span>
    </button>
  );
  return (
    <div className="noon-table-wrap">
      <table
        className="noon-table noon-close-table"
        aria-label="午间与收盘股票核验明细"
        style={{
          minWidth: widths.reduce(
            (sum, width, i) => sum + (hiddenColumns[i] ? 0 : width),
            0,
          ),
        }}
      >
        <thead>
          <tr>
            {tableColumns.map((col, i) => (
              <th
                key={i}
                hidden={hiddenColumns[i]}
                className={col.text ? "noon-close-text" : ""}
                style={{ width: widths[i] }}
                aria-sort={
                  col.key === sort
                    ? direction === "desc"
                      ? "descending"
                      : "ascending"
                    : undefined
                }
              >
                {col.key ? sortButton(col.key, col.label) : col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const identity =
              typeof row.instrument_id === "string" &&
              /^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id);
            const canChart =
              identity && !!onStock && (closeNumber(row.close_price) || 0) > 0;
            const height = closeNumber(row.final_board_count);
            const heightConflict = row.relay_state === "height_conflict";
            return (
              <tr key={row.instrument_id}>
                <td className="noon-close-text">
                  <button
                    type="button"
                    className="noon-stock-link"
                    disabled={!canChart}
                    title={
                      canChart
                        ? "查看截至" + date + "的已发布收盘K线"
                        : "该日有效收盘行情尚未核验"
                    }
                    onClick={() => onStock?.(row.instrument_id, date)}
                  >
                    {row.name || row.instrument_id}
                  </button>
                  <div className="noon-close-identity">
                    <CopyStockCode instrumentId={row.instrument_id} />
                    <NoonBoardTag row={row} />
                  </div>
                </td>
                <td className="noon-close-text" hidden={hiddenColumns[1]}>
                  <CloseTags row={row} />
                </td>
                <td hidden={hiddenColumns[2]}>
                  <div className="muted">
                    昨日{" "}
                    {closeNumber(row.previous_board_count) != null &&
                    row.previous_board_count >= 1
                      ? row.previous_board_count + "板"
                      : "—"}
                  </div>
                  <div
                    className={
                      heightConflict
                        ? "close-warn"
                        : row.close_state === "limit_up"
                          ? "up"
                          : row.relay_state === "broken"
                            ? "down"
                            : "muted"
                    }
                  >
                    {row.close_state === "limit_up"
                      ? height != null && height >= 1
                        ? heightConflict
                          ? "来源" + height + "板 · 关系待核验"
                          : "收盘" + height + "板"
                        : "板数待核验"
                      : row.relay_state === "broken"
                        ? "确认断板"
                        : "—"}
                  </div>
                </td>
                <td className="noon-close-text">
                  <div className={closeTransitionTone(row)}>
                    {closeTransitionLabel(row)}
                  </div>
                  {Array.isArray(row.day_events) &&
                    row.day_events.length > 0 && (
                      <small>
                        {row.day_events.map((event: unknown, index: number) => (
                          <span
                            key={index}
                            className={
                              event === "break_event_semantics_unknown"
                                ? "close-warn"
                                : "muted"
                            }
                          >
                            {closeDayEventLabel(event)}
                          </span>
                        ))}
                      </small>
                    )}
                  {(row.reason ||
                    row.source_reason ||
                    (Array.isArray(row.day_events) &&
                      row.day_events.length > 0)) && (
                    <details className="noon-close-source">
                      <summary>价格与来源</summary>
                      <p>
                        午间价 {num(closeNumber(row.noon_price))} · 收盘价{" "}
                        {num(closeNumber(row.close_price))}
                      </p>
                      {row.reason && <p>{row.reason}</p>}
                      {row.source_reason && (
                        <p>午间来源原因：{row.source_reason}</p>
                      )}
                      {Array.isArray(row.day_events) &&
                        row.day_events.length > 0 && (
                          <p>
                            {row.day_events.map(closeDayEventLabel).join("、")}
                          </p>
                        )}
                    </details>
                  )}
                </td>
                <td className={tone(closeNumber(row.noon_change_pct))}>
                  {percent(closeNumber(row.noon_change_pct))}
                </td>
                <td
                  className={
                    "noon-close-day-change " +
                    tone(closeNumber(row.close_change_pct))
                  }
                >
                  {percent(closeNumber(row.close_change_pct))}
                </td>
                <td
                  hidden={hiddenColumns[6]}
                  className={tone(closeNumber(row.afternoon_change_pct))}
                >
                  {percent(closeNumber(row.afternoon_change_pct))}
                </td>
                <td hidden={hiddenColumns[7]}>
                  {amount(closeNumber(row.close_amount))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
function TemperatureLine({ value }: { value: Row }) {
  const before = closeNumber(value?.noon?.score),
    after = closeNumber(value?.close?.score);
  return (
    <div className="noon-close-inline">
      <span>
        午间温度 <b>{num(before, 1)}</b>
      </span>
      <span>
        收盘同口径温度 <b>{num(after, 1)}</b>
      </span>
      <span className={tone(closeNumber(value?.delta))}>
        {value?.delta == null
          ? value?.reason || "温差暂不可比"
          : "变化 " + (value.delta > 0 ? "+" : "") + num(value.delta, 1) + "分"}
      </span>
    </div>
  );
}
function Overview({ summary }: { summary: Row }) {
  const common = summary.breadth?.common || {};
  const before = common.noon || {},
    after = common.close || {};
  return (
    <>
      <CloseMetricStrip metrics={summary.metrics || {}} />
      <Panel
        title="指数与市场广度"
        subtitle="午间与收盘端点分列；涨幅差值按百分点显示"
      >
        <div className="noon-table-wrap">
          <table className="noon-table noon-close-indices">
            <thead>
              <tr>
                <th className="noon-close-text">指数</th>
                <th>午间涨幅</th>
                <th>收盘涨幅</th>
                <th>午后涨跌幅</th>
                <th>涨幅变化</th>
              </tr>
            </thead>
            <tbody>
              {closeRows(summary.indices).map((row) => (
                <tr key={row.instrument_id}>
                  <td className="noon-close-text">
                    {row.name || row.instrument_id}
                  </td>
                  <td className={tone(closeNumber(row.noon_change_pct))}>
                    {percent(closeNumber(row.noon_change_pct))}
                  </td>
                  <td className={tone(closeNumber(row.close_change_pct))}>
                    {percent(closeNumber(row.close_change_pct))}
                  </td>
                  <td className={tone(closeNumber(row.afternoon_change_pct))}>
                    {percent(closeNumber(row.afternoon_change_pct))}
                  </td>
                  <td className={tone(closeNumber(row.change_pct_points))}>
                    {closeNumber(row.change_pct_points) != null &&
                    row.change_pct_points > 0
                      ? "+"
                      : ""}
                    {num(closeNumber(row.change_pct_points))}
                    {closeNumber(row.change_pct_points) != null
                      ? "个百分点"
                      : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="noon-close-inline">
          <span>
            同股上涨{" "}
            <b className="up">
              {integer(closeNumber(before.up))} →{" "}
              {integer(closeNumber(after.up))}
            </b>
          </span>
          <span>
            同股下跌{" "}
            <b className="down">
              {integer(closeNumber(before.down))} →{" "}
              {integer(closeNumber(after.down))}
            </b>
          </span>
          <span>
            平盘 {integer(closeNumber(before.flat))} →{" "}
            {integer(closeNumber(after.flat))}
          </span>
          <span>共同可比 {integer(closeNumber(common.count))}只</span>
        </div>
        <div className="noon-close-inline">
          <span>
            {summary.amount?.complete === true
              ? "快照后成交额增量"
              : "可比范围成交额增量"}{" "}
            {amount(closeNumber(summary.amount?.delta))}
          </span>
          <span>
            成交额可比 {integer(closeNumber(summary.amount?.valid_count))} /{" "}
            {integer(closeNumber(summary.amount?.source_count))}只
          </span>
          <span className="muted">
            {summary.amount?.reason ||
              "同来源、同单位、同一批股票才可计算；不当作净流入。"}
          </span>
        </div>
      </Panel>
    </>
  );
}
function Classification({
  value,
  selected,
  onSelect,
  date,
  onSector,
}: {
  value: Row;
  selected: string;
  onSelect: (key: string) => void;
  date: string;
  onSector?: SectorAction;
}) {
  return (
    <Panel
      title="行业与概念变化"
      subtitle="沿用午间冻结归属；行业成员表现不当作板块指数收益"
    >
      <div className="noon-close-inline">
        <span>已归属 {integer(closeNumber(value.classified_count))}只</span>
        {closeNumber(value.unclassified_count) != null &&
          value.unclassified_count > 0 && (
            <span className="close-warn">
              归属待核验 {integer(value.unclassified_count)}只
            </span>
          )}
      </div>
      {(["industries", "topics"] as const).map((kind) => (
        <div className="noon-close-classifications" key={kind}>
          <h3>{kind === "industries" ? "行业" : "概念"}</h3>
          <div className="noon-table-wrap">
            <table className="noon-table">
              <thead>
                <tr>
                  <th className="noon-close-text">方向与成分股</th>
                  <th>板块午后涨跌幅</th>
                  <th>收盘涨停</th>
                  <th>回封 / 开板</th>
                  <th>成员午后涨 / 跌</th>
                  <th>成员午后平均涨跌幅</th>
                  <th>最高板数</th>
                  <th>可比</th>
                </tr>
              </thead>
              <tbody>
                {closeRows(value[kind])
                  .slice(0, 7)
                  .map((group) => (
                    <tr key={group.key}>
                      <td className="noon-close-text">
                        <button
                          className="noon-stock-link"
                          type="button"
                          disabled={
                            !onSector ||
                            !/^\d{6}\.(TI|SH|SZ)$/.test(group.topic_code || "")
                          }
                          onClick={() =>
                            onSector?.(
                              { ...group, instrument_id: group.topic_code },
                              "chart",
                              date,
                            )
                          }
                        >
                          {group.name || group.topic_code}
                        </button>
                        <button
                          className="noon-close-members"
                          type="button"
                          disabled={
                            !onSector ||
                            !/^\d{6}\.(TI|SH|SZ)$/.test(group.topic_code || "")
                          }
                          onClick={() =>
                            onSector?.(
                              { ...group, instrument_id: group.topic_code },
                              "members",
                              date,
                            )
                          }
                        >
                          成分股
                        </button>
                        <small>
                          名单日 {group.members_observed_date || "待核验"} ·{" "}
                          {group.source || "来源待核验"}
                        </small>
                      </td>
                      <td
                        className={tone(
                          closeNumber(group.index_afternoon_change_pct),
                        )}
                        title={
                          group.index_reason || "同来源代码板块指数端点涨跌幅"
                        }
                      >
                        {percent(closeNumber(group.index_afternoon_change_pct))}
                      </td>
                      <td
                        className={closeCountTone(
                          group.close_limit_up_count,
                          "up",
                        )}
                      >
                        {integer(closeNumber(group.close_limit_up_count))}
                      </td>
                      <td>
                        <span
                          className={closeCountTone(group.resealed_count, "up")}
                        >
                          {integer(closeNumber(group.resealed_count))}
                        </span>{" "}
                        /{" "}
                        <span
                          className={closeCountTone(group.opened_count, "down")}
                        >
                          {integer(closeNumber(group.opened_count))}
                        </span>
                      </td>
                      <td>
                        <span
                          className={closeCountTone(
                            group.afternoon_up_count,
                            "up",
                          )}
                        >
                          {integer(closeNumber(group.afternoon_up_count))}
                        </span>{" "}
                        /{" "}
                        <span
                          className={closeCountTone(
                            group.afternoon_down_count,
                            "down",
                          )}
                        >
                          {integer(closeNumber(group.afternoon_down_count))}
                        </span>
                      </td>
                      <td
                        className={tone(
                          closeNumber(group.mean_afternoon_change_pct),
                        )}
                      >
                        {percent(closeNumber(group.mean_afternoon_change_pct))}
                      </td>
                      <td
                        className={
                          closeNumber(group.max_board_count) != null &&
                          group.max_board_count >= 1
                            ? "up"
                            : "muted"
                        }
                      >
                        {closeNumber(group.max_board_count) != null &&
                        group.max_board_count >= 1
                          ? group.max_board_count + "板"
                          : "—"}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="noon-stock-link"
                          aria-pressed={selected === group.key}
                          onClick={() =>
                            onSelect(selected === group.key ? "" : group.key)
                          }
                        >
                          {integer(closeNumber(group.valid_count))} /{" "}
                          {integer(closeNumber(group.count))}只
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      <p className="noon-footnote">
        各列前7个方向；同股可归属多个行业或概念，家数不能相加。点击可比数量查看对应股票。
      </p>
    </Panel>
  );
}
export function CloseObservation({
  row,
  date,
  onStock,
  onSector,
}: {
  row: Row;
  date: string;
  onStock?: StockAction;
  onSector?: SectorAction;
}) {
  const rule = typeof row.rule_version === "string" && !!row.rule_version;
  const sector =
    row.sector_objective && typeof row.sector_objective === "object"
      ? row.sector_objective
      : null;
  return (
    <Panel
      title={(rule ? "午间规则观察 · " : "") + (row.label || "午间原关注")}
      subtitle={
        rule
          ? "根据午间保存事实整理"
          : row.recorded_at_ms
            ? "原记录 " + dateTime(row.recorded_at_ms)
            : "留存原文 · 记录时刻未提供"
      }
    >
      {rule ? (
        <p className="noon-close-rule-text">
          {typeof row.text === "string" ? row.text : "规则观察内容未留存"}
        </p>
      ) : (
        <blockquote className="noon-close-original">
          {typeof row.text === "string" ? row.text : "原文未留存"}
        </blockquote>
      )}
      {closeRows(row.objective).length > 0 && (
        <NoonCloseFocusTable
          rows={closeRows(row.objective)}
          date={date}
          label="原关注客观收盘结果"
          onStock={onStock}
        />
      )}
      {sector && (
        <div className="noon-close-objectives">
          <div>
            <button
              type="button"
              className="noon-stock-link"
              disabled={
                !onSector ||
                !/^\d{6}\.(TI|SH|SZ)$/.test(sector.topic_code || "")
              }
              onClick={() =>
                onSector?.(
                  { ...sector, instrument_id: sector.topic_code },
                  "chart",
                  date,
                )
              }
            >
              {sector.name || sector.topic_code}
            </button>
            <button
              type="button"
              className="noon-close-members"
              disabled={
                !onSector ||
                !/^\d{6}\.(TI|SH|SZ)$/.test(sector.topic_code || "")
              }
              onClick={() =>
                onSector?.(
                  { ...sector, instrument_id: sector.topic_code },
                  "members",
                  date,
                )
              }
            >
              成分股
            </button>
            <span>
              午间{" "}
              <span className={tone(closeNumber(sector.noon_change_pct))}>
                {percent(closeNumber(sector.noon_change_pct))}
              </span>{" "}
              → 收盘{" "}
              <span className={tone(closeNumber(sector.close_change_pct))}>
                {percent(closeNumber(sector.close_change_pct))}
              </span>
            </span>
            <span className={tone(closeNumber(sector.afternoon_change_pct))}>
              板块午后 {percent(closeNumber(sector.afternoon_change_pct))}
            </span>
          </div>
          {sector.reason && <p className="noon-footnote">{sector.reason}</p>}
        </div>
      )}
      <p className="noon-footnote">
        {row.scope || "并列客观收盘事实，不自动判自由文字兑现或证伪。"}
      </p>
    </Panel>
  );
}
export default function NoonCloseReview({
  date,
  onStock,
  onSector,
  noonStart,
  noonEnd,
}: {
  date: string;
  onStock?: StockAction;
  onSector?: SectorAction;
  noonStart?: string;
  noonEnd?: string;
}) {
  const active = usePageActive();
  const result = useApi<Row>(
    date ? "/market/noon/close-review?date=" + encodeURIComponent(date) : null,
  );
  const view = result.data?.date === date ? result.data : null;
  const review = view?.review?.version === "noon-close-v1" ? view.review : null;
  const [section, setSection] = useState<CloseSection>("overview");
  const [transition, setTransition] = useState("all");
  const [board, setBoard] = useState<NoonBoardFilterValue>("all");
  const [classification, setClassification] = useState("");
  const [sort, setSort] = useState<CloseSort>("final_board_count");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [pages, setPages] = useState<{ key: string; cursors: string[] }>({
    key: "",
    cursors: [],
  });
  const [copyState, setCopyState] = useState("");
  const copyToken = useRef(0),
    pageRef = useRef<HTMLDivElement>(null);
  const selected =
    CLOSE_SECTIONS.find((x) => x.key === section) || CLOSE_SECTIONS[0];
  const sectionIndex = CLOSE_SECTIONS.indexOf(selected);
  const query = {
    date,
    reviewId: review?.review_id || "",
    section: section === "themes" ? "all" : selected.section,
    transition,
    board,
    sort,
    direction,
    classification,
  };
  const pageKey = JSON.stringify(query);
  const copyQuery = useRef(pageKey);
  copyQuery.current = pageKey;
  const cursors = pages.key === pageKey ? pages.cursors : [];
  const canReadItems =
    !!review?.review_id && (section !== "themes" || !!classification);
  const items = useApi<Row>(
    canReadItems ? closeItemsPath({ ...query, cursor: cursors.at(-1) }) : null,
  );
  const itemData =
    items.data?.review_id === review?.review_id ? items.data : null;
  const rows = closeRows(itemData?.items);
  useEffect(() => {
    copyToken.current++;
    setCopyState("");
    return () => {
      copyToken.current++;
    };
  }, [pageKey]);
  const navigate = (next: CloseSection) => {
    setSection(next);
    setTransition("all");
    setClassification("");
    setCopyState("");
    pageRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
  };
  useEffect(() => {
    if (!active) return;
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        !event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        target?.closest(
          'input,textarea,select,[contenteditable=true],[role="dialog"]',
        )
      )
        return;
      const delta =
        event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
      const next = CLOSE_SECTIONS[sectionIndex + delta];
      if (!delta || !next) return;
      event.preventDefault();
      navigate(next.key);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [active, sectionIndex]);
  async function copy() {
    if (!review?.review_id || copyState === "copying") return;
    const token = ++copyToken.current;
    setCopyState("copying");
    try {
      const data = await api<Row>(
        closeItemsPath({ ...query, codesOnly: true }),
      );
      if (token !== copyToken.current || copyQuery.current !== pageKey) return;
      if (data.review_id !== query.reviewId)
        throw Error("复制范围与当前核验版本不一致，请重新读取");
      const text = closeCopyCodes(data.codes);
      if (!text) {
        setCopyState("当前范围无可复制代码");
        return;
      }
      if (!navigator.clipboard?.writeText)
        throw Error("浏览器未允许复制，请选中代码手动复制。");
      await navigator.clipboard.writeText(text);
      if (token === copyToken.current) setCopyState("已复制六位代码");
    } catch (error: any) {
      if (token === copyToken.current)
        setCopyState(error.message || "复制失败，请手动复制代码");
    }
  }
  function changeSort(key: CloseSort) {
    setDirection(sort === key && direction === "desc" ? "asc" : "desc");
    setSort(key);
  }
  const summary = review?.summary || {},
    emptyState = closeStateMessages[view?.state] || [
      "收盘回看暂不可用",
      view?.reason || "尚无可核验收盘结果",
    ];
  const start =
    review?.collected_start_at ||
    review?.noon_collected_start_at ||
    (view?.snapshot_id ? noonStart : null);
  const end =
    review?.collected_end_at ||
    review?.noon_collected_end_at ||
    (view?.snapshot_id ? noonEnd : null);
  return (
    <section
      className="noon-close-review"
      ref={pageRef}
      aria-label="午评收盘回看"
    >
      <div className="noon-close-layout">
        <aside className="noon-close-sidebar">
          <nav aria-label="收盘回看阅读导航">
            <h3>午后发生了什么</h3>
            {CLOSE_SECTIONS.map((item, index) => (
              <button
                type="button"
                key={item.key}
                aria-current={item.key === section ? "location" : undefined}
                title={item.question}
                onClick={() => navigate(item.key)}
              >
                <small>{String(index + 1).padStart(2, "0")}</small>
                {item.title}
              </button>
            ))}
            <p>
              午间事实保留
              <br />
              收盘结果另存
            </p>
          </nav>
        </aside>
        <div className="noon-close-content">
          <header className="noon-close-heading">
            <div>
              <h2>{selected.title}</h2>
              <p>{selected.question}</p>
            </div>
            <button
              type="button"
              className="btn small"
              onClick={() => {
                result.reload();
                items.reload();
              }}
              disabled={result.loading}
            >
              <RefreshCw size={13} />
              重新读取
            </button>
          </header>
          <div className="noon-close-meta">
            <span>观察日 {date || "—"}</span>
            {start && (
              <span>
                午评采集 {dateTime(start)} — {dateTime(end)}
              </span>
            )}
            {review && (
              <>
                <span>核验于 {dateTime(review.generated_at_ms)}</span>
                {closeNumber(view?.last_checked_at_ms) != null && (
                  <span>最近补核验 {dateTime(view!.last_checked_at_ms)}</span>
                )}
                <Badge tone={view?.state === "partial" ? "warn" : "neutral"}>
                  {view?.state === "partial" ? "部分结果可用" : "收盘已核验"} ·
                  版本{integer(closeNumber(review.revision))}
                </Badge>
                {review.backfilled && <Badge tone="warn">补充核验</Badge>}
                <span>
                  收盘状态核验{" "}
                  {integer(
                    closeNumber(review.coverage?.verified_close_state_count),
                  )}{" "}
                  /{" "}
                  {integer(closeNumber(review.coverage?.focus_requested_count))}
                  只
                </span>
                {closeNumber(review.coverage?.unknown_close_state_count) !=
                  null &&
                  review.coverage.unknown_close_state_count > 0 && (
                    <span className="close-warn">
                      状态待核验{" "}
                      {integer(review.coverage.unknown_close_state_count)}只
                    </span>
                  )}
                {closeNumber(review.coverage?.omitted_item_count) != null &&
                  review.coverage.omitted_item_count > 0 && (
                    <span className="close-warn">
                      未保存明细 {integer(review.coverage.omitted_item_count)}只
                      · 列表为部分覆盖
                    </span>
                  )}
              </>
            )}
          </div>
          {review &&
            view?.state === "partial" &&
            view.last_check_status === "warning" && (
              <div className="noon-close-check-warning" role="status">
                <strong>本次覆盖不足，保留原核验版本</strong>
                <span>
                  {view.last_check_reason ||
                    view.reason ||
                    "最新补核验未完成，当前展示上次保存结果。"}
                </span>
              </div>
            )}
          {result.error && (
            <ErrorBox message={result.error} retry={result.reload} />
          )}
          {result.loading && !view && <Loading />}
          {view && !review && (
            <Panel title={emptyState[0]}>
              <Empty
                title={emptyState[0]}
                description={view.reason || emptyState[1]}
              />
            </Panel>
          )}
          {review && (
            <>
              <div className="noon-close-colors">
                <span className="up">红 · 上涨 / 封板 / 修复</span>
                <span className="down">绿 · 下跌 / 跌停 / 走弱</span>
                <span className="close-warn">黄 · 待核验</span>
                <span>0保持中性</span>
              </div>
              <TemperatureLine value={summary.temperature || {}} />
              {section === "overview" && <Overview summary={summary} />}
              {section === "events" && (
                <CloseMetricStrip metrics={summary.metrics || {}} />
              )}
              {["overview", "events"].includes(section) && (
                <div className="noon-close-inline">
                  <span>
                    跌停打开{" "}
                    <b className="up">
                      {integer(closeMetric(summary.metrics?.down_opened).count)}
                      只
                    </b>
                  </span>
                  <span>
                    收盘新增跌停{" "}
                    <b className="down">
                      {integer(
                        closeMetric(summary.metrics?.new_limit_down).count,
                      )}
                      只
                    </b>
                  </span>
                  <span className="muted">
                    回封已计入“未封→涨停”，各项不能相加当涨停总数。
                  </span>
                </div>
              )}
              {section === "relay" && (
                <Panel
                  title="昨日接力的最终结果"
                  subtitle="午间未封不提前确认断板；收盘实际板数与晋级分别核验"
                >
                  <div className="noon-close-inline">
                    <span>
                      昨日名单{" "}
                      {integer(closeNumber(summary.relay?.source_count))}只
                    </span>
                    <span className="up">
                      确认晋级{" "}
                      {integer(closeNumber(summary.relay?.promoted_count))}只
                    </span>
                    <span className="down">
                      确认断板{" "}
                      {integer(closeNumber(summary.relay?.broken_count))}只
                    </span>
                    <span className="close-warn">
                      待核验{" "}
                      {integer(closeNumber(summary.relay?.unknown_count))}只
                    </span>
                  </div>
                  <div className="noon-close-inline">
                    {closeRows(summary.relay?.groups).map((group) => (
                      <span key={group.key || group.board_count}>
                        {group.label || "昨日" + group.board_count + "板"}{" "}
                        {integer(closeNumber(group.count))}只
                      </span>
                    ))}
                  </div>
                </Panel>
              )}
              {section === "themes" && (
                <Classification
                  value={review.classification_counts || {}}
                  selected={classification}
                  date={date}
                  onSector={onSector}
                  onSelect={setClassification}
                />
              )}
              {section === "watch" && (
                <>
                  {closeRows(review.observations).length ? (
                    closeRows(review.observations).map((row, index) => (
                      <CloseObservation
                        key={row.key || index}
                        row={row}
                        date={date}
                        onStock={onStock}
                        onSector={onSector}
                      />
                    ))
                  ) : (
                    <p className="noon-unknown">
                      该午评未留存人工观察原文，客观行情核验仍正常展示。
                    </p>
                  )}
                  {(["amount", "hot"] as const).map((kind) => (
                    <Panel
                      key={kind}
                      title={
                        kind === "amount"
                          ? "午间成交额前列回看"
                          : "午间热榜回看"
                      }
                      subtitle="固定午间名单和原排名，不以收盘榜替换；可按今日收盘涨幅排序，原排名不变"
                    >
                      <NoonCloseFocusTable
                        rows={closeRows(summary.leaders?.[kind])}
                        date={date}
                        label={
                          kind === "amount"
                            ? "午间成交额前列收盘结果"
                            : "午间热榜收盘结果"
                        }
                        ranked
                        onStock={onStock}
                      />
                    </Panel>
                  ))}
                </>
              )}
              {section === "tomorrow" && (
                <Panel
                  title="下一交易日附加观察"
                  subtitle="正常昨日梯队继续独立读取今日已发布收盘事实，不依赖回看生成"
                >
                  <div className="noon-close-next-groups">
                    {closeRows(review.tomorrow?.groups).map((group) => (
                      <button
                        type="button"
                        className="btn small"
                        key={group.key}
                        onClick={() => {
                          setSection("events");
                          setTransition(
                            group.key === "resealed"
                              ? "resealed"
                              : group.key === "opened"
                                ? "opened"
                                : "new_limit_up",
                          );
                        }}
                      >
                        {group.label} {integer(closeNumber(group.count))}只
                      </button>
                    ))}
                  </div>
                  <p className="noon-footnote">
                    {review.tomorrow?.scope ||
                      "附加观察名单互有交集，不重复累加为昨日涨停总数；缺午评时不补造转变名单。"}
                  </p>
                </Panel>
              )}
              {canReadItems && (
                <Panel
                  title={
                    section === "themes"
                      ? "方向对应股票"
                      : section === "relay"
                        ? "昨日接力明细"
                        : section === "watch"
                          ? "原关注股票"
                          : section === "tomorrow"
                            ? "明日衔接名单"
                            : "午间与收盘变化股票"
                  }
                  actions={
                    <button
                      type="button"
                      className="btn small"
                      disabled={!active || copyState === "copying"}
                      onClick={copy}
                    >
                      <Copy size={12} />
                      {copyState === "copying"
                        ? "正在读取代码…"
                        : "复制当前范围代码"}
                    </button>
                  }
                >
                  <p className="noon-footnote">
                    今日收盘涨幅为 {date}{" "}
                    收盘相对该日前收的涨跌幅；午间涨幅与午后涨跌幅独立列示。缺失显示—，不使用后续日期补填。
                  </p>
                  <NoonBoardFilter
                    value={board}
                    onChange={setBoard}
                    label="收盘回看上市板块筛选"
                  />
                  <div
                    className="noon-close-filters"
                    role="group"
                    aria-label="收盘转变筛选"
                  >
                    {CLOSE_TRANSITIONS.map(([key, label]) => (
                      <button
                        type="button"
                        className={
                          "btn small" + (transition === key ? " active" : "")
                        }
                        key={key}
                        aria-pressed={transition === key}
                        onClick={() => setTransition(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  {copyState && copyState !== "copying" && (
                    <p className="noon-footnote" role="status">
                      {copyState}
                    </p>
                  )}
                  {items.error && (
                    <ErrorBox message={items.error} retry={items.reload} />
                  )}
                  {items.loading && !itemData ? (
                    <Loading />
                  ) : rows.length ? (
                    <CloseRowsTable
                      rows={rows}
                      date={date}
                      onStock={onStock}
                      sort={sort}
                      direction={direction}
                      onSort={changeSort}
                      availableFields={
                        Array.isArray(itemData?.available_fields)
                          ? itemData.available_fields
                          : undefined
                      }
                    />
                  ) : (
                    !items.error && (
                      <p className="noon-unknown">
                        该筛选范围暂无已保存股票；原分组统计保持不变。
                      </p>
                    )
                  )}
                  <footer className="noon-close-pagination">
                    <span>
                      当前筛选 {integer(closeNumber(itemData?.total))}只 ·
                      本节已保存 {integer(closeNumber(itemData?.scope_total))}只
                    </span>
                    <div>
                      <button
                        type="button"
                        className="btn small"
                        disabled={!cursors.length || items.loading}
                        onClick={() =>
                          setPages({
                            key: pageKey,
                            cursors: cursors.slice(0, -1),
                          })
                        }
                      >
                        上一页
                      </button>
                      <span>{cursors.length + 1}</span>
                      <button
                        type="button"
                        className="btn small"
                        disabled={!itemData?.next_cursor || items.loading}
                        onClick={() =>
                          setPages({
                            key: pageKey,
                            cursors: [...cursors, itemData!.next_cursor],
                          })
                        }
                      >
                        下一页
                      </button>
                    </div>
                  </footer>
                </Panel>
              )}
              {closeRows(review.gaps).length > 0 ||
              (Array.isArray(review.gaps) && review.gaps.length > 0) ? (
                <details className="noon-details">
                  <summary>收盘核验缺口 · {review.gaps.length}项</summary>
                  <ul className="noon-lines">
                    {review.gaps.map((gap: any, index: number) => (
                      <li key={index}>
                        {typeof gap === "string"
                          ? gap
                          : gap.reason || gap.label}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
              <details className="noon-details">
                <summary>来源与保存依据</summary>
                <p>
                  收盘来源 {review.source_snapshot_id || "未提供"} · 版本{" "}
                  {review.version}
                </p>
                <p>午评SHA {review.noon_sha256 || "未提供"}</p>
                <p>同日收盘输入SHA {review.close_input_sha256 || "未提供"}</p>
                <p className="noon-footnote">
                  原午评保持不变；回看仅使用已保存同日事实，没有分时证据时不推断回封时刻、次数或稳定性。随午评保留28天，后补结果标实际生成时间。
                </p>
              </details>
            </>
          )}
        </div>
      </div>
      {active && (
        <div
          className="noon-quick-navigation noon-close-quick"
          aria-label="收盘回看上下节快捷导航"
        >
          <button
            type="button"
            aria-label="收盘回看上一节"
            disabled={sectionIndex === 0}
            onClick={() => navigate(CLOSE_SECTIONS[sectionIndex - 1].key)}
          >
            <ChevronUp size={18} />
          </button>
          <span>
            {sectionIndex + 1}/{CLOSE_SECTIONS.length}
          </span>
          <button
            type="button"
            aria-label="收盘回看下一节"
            disabled={sectionIndex === CLOSE_SECTIONS.length - 1}
            onClick={() => navigate(CLOSE_SECTIONS[sectionIndex + 1].key)}
          >
            <ChevronDown size={18} />
          </button>
        </div>
      )}
    </section>
  );
}
