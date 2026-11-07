import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpToLine,
  ChartCandlestick,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  X,
} from "lucide-react";
import type { Row } from "../../lib/api";
import { createStockNavigation } from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import CopyStockCode from "../../components/CopyStockCode";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Panel,
  Warning,
  Warnings,
} from "../../components/UI";
import { integer, num, percent, tone } from "../../lib/format";
import { CurrencyCell, MarketSortHeader } from "./MarketTable";
import { sortMarketRows } from "./marketPresentation";
import type { MarketSort } from "./marketPresentation";
import { SectorActivityCell, SectorActivityCoverage } from "./SectorActivity";
import MarketDataCoverage from "./MarketDataCoverage";
import { TopicMembersContent } from "./TopicMembersDialog";
import SectorChartDialog from "./SectorChartDialog";
import { sectorChartTarget } from "./sectorChartPresentation";
import type { SectorChartSelection } from "./sectorChartPresentation";
import {
  sectorExplorerDefaultSort,
  sectorExplorerGroups,
  sectorExplorerId,
  sectorExplorerRows,
  sectorBrokenBoard,
} from "./sectorExplorerPresentation";
import type {
  SectorActivityMetric,
  SectorExplorerKind,
  SectorExplorerMode,
} from "./sectorExplorerPresentation";
import "./SectorExplorer.css";

const modes: [SectorExplorerMode, string][] = [
  ["all", "全部行情"],
  ["rising", "上涨榜"],
  ["falling", "下跌榜"],
  ["amount", "成交额榜"],
  ["limit", "涨停活跃"],
  ["broken", "炸板分布"],
  ["activity", "持续活跃"],
];
const pageSize = 20;
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const count = (value: unknown): value is number =>
  finite(value) && Number.isInteger(value) && value >= 0;

function ScrollEnds({
  label,
  onJump,
}: {
  label: string;
  onJump: (edge: "top" | "bottom") => void;
}) {
  return (
    <div
      className="sector-explorer-scroll-ends"
      role="group"
      aria-label={`${label}快速滚动`}
    >
      <button
        className="icon-btn"
        aria-label={`${label}回到顶部`}
        title="回到顶部"
        onClick={() => onJump("top")}
      >
        <ArrowUpToLine size={15} />
      </button>
      <button
        className="icon-btn"
        aria-label={`${label}回到底部`}
        title="回到底部"
        onClick={() => onJump("bottom")}
      >
        <ArrowDownToLine size={15} />
      </button>
    </div>
  );
}

function scrollPane(element: HTMLElement | null, edge: "top" | "bottom") {
  if (!element) return;
  if (element.scrollHeight > element.clientHeight) {
    element.scrollTo({ top: edge === "top" ? 0 : element.scrollHeight });
  } else if (
    window.matchMedia("(max-width: 1100px), (max-height: 639px)").matches
  ) {
    element.scrollIntoView({ block: edge === "top" ? "start" : "end" });
  }
}

function LimitCount({ row }: { row: Row }) {
  return count(row.limit_up_count) ? (
    <>{integer(row.limit_up_count)}只</>
  ) : (
    <span
      className="muted"
      title="成员或逐股涨停状态未完全核验，观察数不参与精确家数排序"
    >
      {count(row.limit_up_observed_count)
        ? `已观察${integer(row.limit_up_observed_count)}只`
        : "—"}
    </span>
  );
}

function BrokenCount({
  evidence,
  field,
}: {
  evidence: ReturnType<typeof sectorBrokenBoard>;
  field: "limit_up_count" | "broken_count" | "touch_count";
}) {
  const value = evidence[field];
  if (count(value)) return <>{integer(value)}只</>;
  const observed =
    field === "limit_up_count"
      ? evidence.observed_limit_up_count
      : field === "broken_count"
        ? evidence.observed_broken_count
        : null;
  return (
    <span
      className="muted sector-explorer-observed-count"
      title={evidence.reason}
    >
      {count(observed) ? (
        <>
          已观察 <span>{integer(observed)}只</span>
        </>
      ) : (
        "—"
      )}
    </span>
  );
}

function SectorBrokenStructure({
  evidence,
  date,
  scope,
  onStock,
}: {
  evidence: ReturnType<typeof sectorBrokenBoard>;
  date: string;
  scope: string;
  onStock: (id: string, navigation?: StockNavigation) => void;
}) {
  return (
    <section
      className="sector-explorer-broken-structure"
      aria-label="所选方向炸板结构"
    >
      <div className="sector-explorer-broken-heading">
        <strong>触板结果</strong>
        <span>
          {date} ·{" "}
          {evidence.state === "complete"
            ? "完整核验"
            : evidence.state === "partial"
              ? "部分覆盖"
              : "待核验"}
        </span>
      </div>
      <div className="sector-explorer-broken-metrics">
        <div>
          <span>收盘涨停</span>
          <strong>
            <BrokenCount evidence={evidence} field="limit_up_count" />
          </strong>
        </div>
        <div>
          <span>收盘未封住</span>
          <strong>
            <BrokenCount evidence={evidence} field="broken_count" />
          </strong>
        </div>
        <div>
          <span>触板样本</span>
          <strong>
            <BrokenCount evidence={evidence} field="touch_count" />
          </strong>
        </div>
        <div>
          <span>未封住比例</span>
          <strong>
            {finite(evidence.broken_rate_pct)
              ? `${num(evidence.broken_rate_pct, 1)}%`
              : "—"}
          </strong>
        </div>
      </div>
      <p>{evidence.reason}</p>
      <small>
        同一股票可关联多个概念，分类家数不能相加当作全市场家数；小样本比例不等于方向强弱结论。
      </small>
      {evidence.details.length > 0 && (
        <details className="sector-explorer-broken-samples">
          <summary>
            查看已核验触板样本 · {integer(evidence.details.length)}只
          </summary>
          <div className="sector-explorer-broken-sample-list">
            {evidence.details.map((item) => (
              <div
                className="sector-explorer-broken-sample"
                key={item.instrument_id}
              >
                <div>
                  <button
                    className="link"
                    onClick={() =>
                      onStock(
                        item.instrument_id,
                        createStockNavigation(evidence.details, {
                          source: "market",
                          scopeKey: `sector-broken:${scope}`,
                          label: "所选方向已核验触板样本",
                          asOf: date,
                        }),
                      )
                    }
                  >
                    {item.name || item.code || item.instrument_id}
                  </button>
                  <CopyStockCode
                    code={item.code}
                    instrumentId={item.instrument_id}
                  />
                </div>
                <span>
                  {item.outcome === "broken" ? "收盘未封住" : "收盘涨停"}
                </span>
                <span className={tone(item.change_pct)}>
                  {percent(item.change_pct)}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

function RankingValue({ row, field }: { row: Row; field: string }) {
  const broken: ReturnType<typeof sectorBrokenBoard> = row._sector_broken;
  switch (field) {
    case "change_pct":
      return (
        <span className={tone(row.change_pct)}>{percent(row.change_pct)}</span>
      );
    case "amount":
      return <CurrencyCell value={row.amount} />;
    case "_sector_limit_sort":
      return <LimitCount row={row} />;
    case "_sector_up_ratio":
      return (
        <span title="已观察成员报价中上涨的比例；覆盖与成员总量另行披露">
          {finite(row._sector_up_ratio)
            ? `${num(row._sector_up_ratio, 1)}%`
            : "—"}
        </span>
      );
    case "_sector_broken_up_sort":
      return <BrokenCount evidence={broken} field="limit_up_count" />;
    case "_sector_broken_sort":
      return <BrokenCount evidence={broken} field="broken_count" />;
    case "_sector_touch_sort":
      return <BrokenCount evidence={broken} field="touch_count" />;
    case "_sector_broken_rate_sort":
      return (
        <span title={broken.reason}>
          {finite(broken.broken_rate_pct)
            ? `${num(broken.broken_rate_pct, 1)}%`
            : "—"}
        </span>
      );
    default:
      return (
        <span
          title={row._sector_activity_label}
          className={`sector-explorer-activity-count${row._sector_activity_complete ? "" : " muted"}`}
        >
          {count(row._sector_activity_count) ? (
            <>
              {!row._sector_activity_complete && <small>已观察</small>}
              <span>{row._sector_activity_count}次</span>
            </>
          ) : (
            "—"
          )}
        </span>
      );
  }
}

/** Reuse the published sector read model and shared membership view, without new collection. */
export default function SectorExplorer({
  date,
  latestDate,
  data,
  loading,
  error,
  onReload,
  onStock,
}: {
  date: string;
  latestDate?: string;
  data: Row | null;
  loading: boolean;
  error: string;
  onReload: () => void;
  onStock: (id: string, navigation?: StockNavigation) => void;
}) {
  const [kind, setKind] = useState<SectorExplorerKind>("concept");
  const [groupKey, setGroupKey] = useState("");
  const [mode, setMode] = useState<SectorExplorerMode>("all");
  const [activityMetric, setActivityMetric] =
    useState<SectorActivityMetric>("change_pct");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<MarketSort | null>(null);
  const [page, setPage] = useState(0);
  const [fullColumns, setFullColumns] = useState(false);
  const [selection, setSelection] = useState<{
    scope: string;
    id: string;
  } | null>(null);
  const [chart, setChart] = useState<
    (SectorChartSelection & { date: string }) | null
  >(null);
  const detailRef = useRef<HTMLElement>(null);
  const rankingRef = useRef<HTMLDivElement>(null);
  // A date change must not attach the previous response to the new view date.
  const current = data?.date === date ? data : null;
  const rows: Row[] = Array.isArray(current?.items) ? current.items : [];
  const groups = useMemo(() => sectorExplorerGroups(rows, kind), [rows, kind]);
  const group = groups.find((item) => item.key === groupKey) || groups[0];
  const effectiveGroup = group?.key || "";
  const scope = JSON.stringify([
    date,
    current?.snapshot_id,
    kind,
    effectiveGroup,
  ]);
  const options = {
    kind,
    groupKey: effectiveGroup,
    query,
    mode,
    activityMetric,
    observationDate: date,
    snapshotId: current?.snapshot_id,
  };
  const filtered = useMemo(
    () => sectorExplorerRows(rows, options),
    [
      rows,
      kind,
      effectiveGroup,
      query,
      mode,
      activityMetric,
      date,
      current?.snapshot_id,
    ],
  );
  const activeSort = sort || sectorExplorerDefaultSort(mode);
  const ordered = useMemo(
    () => sortMarketRows(filtered, activeSort),
    [filtered, activeSort.key, activeSort.order],
  );
  const selected =
    (selection?.scope === scope
      ? ordered.find((row) => sectorExplorerId(row) === selection.id)
      : null) || ordered[0];
  const selectedId = selected ? sectorExplorerId(selected) : "";
  const allGroupRows = useMemo(
    () => sectorExplorerRows(rows, { ...options, mode: "all", query: "" }),
    [rows, kind, effectiveGroup, activityMetric, date, current?.snapshot_id],
  );
  const up = allGroupRows.filter(
    (row) => finite(row.change_pct) && row.change_pct > 0,
  ).length;
  const down = allGroupRows.filter(
    (row) => finite(row.change_pct) && row.change_pct < 0,
  ).length;
  const unknown = allGroupRows.filter((row) => !finite(row.change_pct)).length;
  const pages = Math.max(1, Math.ceil(ordered.length / pageSize));
  const safePage = Math.min(page, pages - 1);
  const visible = ordered.slice(safePage * pageSize, (safePage + 1) * pageSize);
  const target = selected ? sectorChartTarget(selected, rows) : null;
  const groupLevel = group?.level == null ? "unknown" : String(group.level);
  const activityCaption =
    activityMetric === "amount" ? "近7日成交额榜" : "近7日上涨榜";
  const brokenMode = mode === "broken";
  const selectedBroken = selected
    ? sectorBrokenBoard(selected, date, current?.snapshot_id)
    : null;
  const rankingColumns: Array<[string, string]> = brokenMode
    ? [
        ["_sector_broken_up_sort", "涨停家数"],
        ["_sector_broken_sort", "炸板家数"],
        ["_sector_touch_sort", "触板家数"],
        ["_sector_broken_rate_sort", "未封住比例"],
      ]
    : [
        ["change_pct", "当日涨幅"],
        ["amount", "成交额"],
        ["_sector_limit_sort", "涨停家数"],
        ["_sector_up_ratio", "样本上涨比"],
        ...(fullColumns
          ? ([
              ["_sector_broken_sort", "炸板家数"],
              ["_sector_broken_rate_sort", "未封住比例"],
            ] as Array<[string, string]>)
          : []),
        ["_sector_activity_count", activityCaption],
      ];

  useEffect(() => {
    setPage(0);
  }, [scope, query, mode, activityMetric, activeSort.key, activeSort.order]);
  useEffect(() => {
    detailRef.current?.scrollTo({ top: 0 });
  }, [scope, selectedId]);
  function selectKind(next: SectorExplorerKind) {
    setKind(next);
    setGroupKey("");
    setSort(null);
    setPage(0);
  }
  function selectMode(next: SectorExplorerMode) {
    setMode(next);
    setSort(null);
    setPage(0);
    if (next === "amount") setActivityMetric("amount");
  }
  function changeSort(key: string) {
    setSort({
      key,
      order:
        activeSort.key === key && activeSort.order === "desc" ? "asc" : "desc",
    });
    setPage(0);
  }
  function selectRow(row: Row) {
    setSelection({ scope, id: sectorExplorerId(row) });
    if (window.matchMedia("(max-width: 1100px)").matches)
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function openChart() {
    if (selected) openRowChart(selected);
  }
  function openRowChart(row: Row) {
    const rowTarget = sectorChartTarget(row, rows);
    if (rowTarget.topicCode)
      setChart({ ...rowTarget, topicCode: rowTarget.topicCode, date });
  }

  return (
    <div className="page-stack sector-explorer">
      <div className="sector-explorer-controls">
        <div className="sector-explorer-control-row">
          <div className="segmented" role="group" aria-label="行业或概念">
            <button
              className={kind === "concept" ? "active" : ""}
              aria-pressed={kind === "concept"}
              onClick={() => selectKind("concept")}
            >
              概念题材
            </button>
            <button
              className={kind === "industry" ? "active" : ""}
              aria-pressed={kind === "industry"}
              onClick={() => selectKind("industry")}
            >
              行业分类
            </button>
          </div>
          {groups.length > 1 && (
            <label className="sector-explorer-source">
              <span>来源与层级</span>
              <select
                aria-label="选择同源分类与行业层级"
                value={effectiveGroup}
                onChange={(e) => {
                  setGroupKey(e.target.value);
                  setSort(null);
                }}
              >
                {groups.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="sector-explorer-search">
            <input
              className="search-input"
              aria-label="搜索行业与概念"
              placeholder="搜索名称或板块代码…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button
              className="icon-btn"
              aria-label="清除题材搜索"
              disabled={!query}
              onClick={() => setQuery("")}
            >
              <X size={14} />
            </button>
          </div>
          <button
            className="icon-btn"
            onClick={onReload}
            aria-label="刷新行业与概念"
          >
            <RefreshCw size={15} />
          </button>
        </div>
        <div className="sector-explorer-control-row">
          <div
            className="ranking-controls sector-explorer-ranks"
            role="group"
            aria-label="板块观察方式"
          >
            {modes.map(([value, caption]) => (
              <button
                key={value}
                className={mode === value ? "active" : ""}
                aria-pressed={mode === value}
                onClick={() => selectMode(value)}
              >
                {caption}
              </button>
            ))}
          </div>
          <label className="sector-explorer-source">
            <span>活跃口径</span>
            <select
              aria-label="近期活跃榜单口径"
              value={activityMetric}
              onChange={(e) =>
                setActivityMetric(e.target.value as SectorActivityMetric)
              }
            >
              <option value="change_pct">近7日上涨前10</option>
              <option value="amount">近7日成交额前10</option>
            </select>
          </label>
        </div>
      </div>
      {error && <ErrorBox message={error} retry={onReload} />}
      {current?.unavailable_reason && (
        <Warning>{current.unavailable_reason}</Warning>
      )}
      {current && (
        <div className="sector-explorer-scope">
          <span>
            {group?.label || "所选分类"} · 已返回 {integer(allGroupRows.length)}{" "}
            项
          </span>
          <span className="up">上涨 {integer(up)}</span>
          <span className="down">下跌 {integer(down)}</span>
          <span>报价未知 {integer(unknown)}</span>
          <span>成交额按元核验，不代表净流入</span>
        </div>
      )}
      {kind === "industry" && group && group.level == null && (
        <Warning>
          当前来源行业层级未核验，本组是独立目录观察，不等于一级行业完整排名。
        </Warning>
      )}
      <div
        className={`sector-explorer-grid sector-mode-${mode}${fullColumns ? " sector-explorer-full-columns" : ""}`}
      >
        <Panel
          className="sector-explorer-ranking"
          title={`${kind === "concept" ? "概念" : "行业"} · ${modes.find(([value]) => value === mode)?.[1]}`}
          subtitle="名称看K线 · 行内其他位置展开个股"
          actions={
            <>
              <Badge>{integer(ordered.length)}项</Badge>
              {!brokenMode && (
                <label className="checkbox-label sector-explorer-column-toggle">
                  <input
                    type="checkbox"
                    checked={fullColumns}
                    onChange={(event) => setFullColumns(event.target.checked)}
                  />
                  完整列
                </label>
              )}
              <ScrollEnds
                label="左侧榜单"
                onJump={(edge) => scrollPane(rankingRef.current, edge)}
              />
            </>
          }
        >
          {loading && !current ? (
            <Loading />
          ) : !ordered.length ? (
            <Empty
              title={
                current?.unavailable_reason
                  ? "此数据尚未就绪"
                  : mode === "rising"
                    ? "当前范围没有已确认上涨记录"
                    : mode === "falling"
                      ? "当前范围没有已确认下跌记录"
                      : "当前范围暂无匹配方向"
              }
              description={
                current?.unavailable_reason ||
                "可切换观察方式或清除搜索；报价与成员缺档分别保留未知。"
              }
            />
          ) : (
            <>
              <div ref={rankingRef} className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>
                        <MarketSortHeader
                          field="name"
                          caption="方向"
                          sort={activeSort}
                          onSort={changeSort}
                        />
                      </th>
                      {rankingColumns.map(([field, caption]) => (
                        <th key={field}>
                          <MarketSortHeader
                            field={field}
                            caption={caption}
                            sort={activeSort}
                            onSort={changeSort}
                          />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((row, i) => (
                      <tr
                        key={sectorExplorerId(row)}
                        aria-selected={sectorExplorerId(row) === selectedId}
                        className={
                          sectorExplorerId(row) === selectedId ? "selected" : ""
                        }
                        onClick={() => selectRow(row)}
                      >
                        <td>
                          <div className="sector-explorer-direction">
                            <button
                              className="sector-explorer-row-select"
                              aria-label={`展开${row.name || "分类"}的关联个股`}
                              aria-pressed={
                                sectorExplorerId(row) === selectedId
                              }
                              onClick={(event) => {
                                event.stopPropagation();
                                selectRow(row);
                              }}
                            >
                              {safePage * pageSize + i + 1}
                            </button>
                            <div>
                              <button
                                className="market-topic-link"
                                aria-label={`查看${row.name || "分类"}的板块K线`}
                                disabled={
                                  !sectorChartTarget(row, rows).topicCode
                                }
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openRowChart(row);
                                }}
                              >
                                <strong>
                                  {row.name || row.topic_code || "未知分类"}
                                </strong>
                              </button>
                              {row.members_available === false && (
                                <small className="market-gap-note">
                                  {date === latestDate
                                    ? "历史家数待核验"
                                    : "该日历史成员缺档"}
                                </small>
                              )}
                            </div>
                          </div>
                        </td>
                        {rankingColumns.map(([field]) => (
                          <td key={field}>
                            <RankingValue row={row} field={field} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="sector-explorer-pagination">
                <span>
                  {safePage * pageSize + 1}–
                  {Math.min((safePage + 1) * pageSize, ordered.length)} /{" "}
                  {integer(ordered.length)}
                </span>
                <div className="button-group">
                  <button
                    className="btn small"
                    aria-label="上一页方向"
                    disabled={safePage === 0}
                    onClick={() => setPage(safePage - 1)}
                  >
                    <ChevronLeft size={13} />
                    上一页
                  </button>
                  <button
                    className="btn small"
                    aria-label="下一页方向"
                    disabled={safePage + 1 >= pages}
                    onClick={() => setPage(safePage + 1)}
                  >
                    下一页
                    <ChevronRight size={13} />
                  </button>
                </div>
              </div>
            </>
          )}
          <details className="sector-explorer-ranking-note">
            <summary>榜单统计口径</summary>
            {brokenMode ? (
              <p>
                炸板家数指同日已核验触板但收盘未封住的股票数；按证券去重。只有两池和历史成员完整核验时才计算未封住比例；部分覆盖的观察数不参与精确家数排序。多重概念关联不代表涨停原因，小样本比例不用于认定题材强弱。
              </p>
            ) : (
              <p>
                近期活跃使用固定近7个市场交易日的同源同类Top10记录；部分覆盖仅显示已观察次数，补齐竞争者可能改变次数和顺序。表头排序不重定义榜单。
                完整列中的炸板结构使用所选日留存成员与同日来源池，部分覆盖不补0。
              </p>
            )}
          </details>
        </Panel>
        <section
          ref={detailRef}
          className="sector-explorer-detail"
          aria-label="所选方向与成员"
        >
          <Panel
            title={selected?.name || "方向展开"}
            subtitle={
              selected
                ? `${kind === "industry" ? "行业" : "概念"} · ${date} 已留存行情`
                : "选中左侧方向查看关联个股"
            }
            actions={
              selected && (
                <>
                  <button
                    className="btn small"
                    disabled={!target?.topicCode}
                    title={target?.reason || target?.notice}
                    aria-label={`查看${selected.name || "分类"}的板块K线`}
                    onClick={openChart}
                  >
                    <ChartCandlestick size={14} />
                    板块K线
                  </button>
                  <ScrollEnds
                    label="右侧个股"
                    onJump={(edge) => scrollPane(detailRef.current, edge)}
                  />
                </>
              )
            }
          >
            {selected ? (
              <>
                <div className="sector-explorer-detail-metrics">
                  <div>
                    <span>当日涨幅</span>
                    <strong className={tone(selected.change_pct)}>
                      {percent(selected.change_pct)}
                    </strong>
                  </div>
                  <div>
                    <span>来源成交额</span>
                    <strong>
                      <CurrencyCell value={selected.amount} />
                    </strong>
                  </div>
                  <div>
                    <span>收盘涨停</span>
                    <strong>
                      <LimitCount row={selected} />
                    </strong>
                  </div>
                </div>
                {selectedBroken &&
                  (selectedBroken.state !== "unknown" || brokenMode) && (
                    <SectorBrokenStructure
                      evidence={selectedBroken}
                      date={date}
                      scope={scope + selectedId}
                      onStock={onStock}
                    />
                  )}
                <TopicMembersContent
                  key={scope + selectedId}
                  topic={selected}
                  date={date}
                  latestDate={latestDate}
                  onStock={onStock}
                  compact
                />
                <details className="sector-explorer-evidence">
                  <summary>近7日上榜日期与双榜明细</summary>
                  <SectorActivityCell activity={selected.sector_activity_7d} />
                </details>
              </>
            ) : loading ? (
              <Loading />
            ) : (
              <Empty
                title="请选择一个方向"
                description="板块自身行情与历史成员独立，历史成员缺失不影响查看已留存板块K线。"
              />
            )}
          </Panel>
        </section>
      </div>
      <details className="market-members-details sector-explorer-sources">
        <summary>数据范围、近7日覆盖与来源说明</summary>
        <MarketDataCoverage
          coverage={current?.data_coverage}
          loading={loading}
        />
        <MarketDataCoverage
          coverage={current?.membership_coverage}
          loading={loading}
        />
        <SectorActivityCoverage
          summary={current?.sector_activity_7d}
          kind={kind}
          level={groupLevel}
        />
        <Warnings items={current?.warnings} />
      </details>
      {chart?.date === date && (
        <SectorChartDialog
          key={date + chart.topicCode}
          topic={chart}
          date={date}
          onClose={() => setChart(null)}
        />
      )}
    </div>
  );
}
