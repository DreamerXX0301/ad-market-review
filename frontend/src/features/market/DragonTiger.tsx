import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { post, requestKey, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Panel,
  Status,
  useToast,
} from "../../components/UI";
import { createStockNavigation } from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import CopyStockCode from "../../components/CopyStockCode";
import { integer, percent, tone } from "../../lib/format";
import { marketMoney } from "./marketPresentation";
import DragonTigerTable from "./DragonTigerTable";
import {
  dragonTigerBoardAvailable,
  dragonTigerDisclosure,
  dragonTigerHotStatistics,
  dragonTigerJobId,
  dragonTigerLabels,
  dragonTigerNumber,
  dragonTigerRows,
  dragonTigerSummary,
  filterDragonTigerRows,
  sortDragonTigerRows,
  uniqueDragonTigerStocks,
} from "./dragonTigerPresentation";
import type {
  DragonTigerBoard,
  DragonTigerPeriod,
  DragonTigerRow,
  DragonTigerSort,
  DragonTigerSortKey,
} from "./dragonTigerPresentation";
import "./DragonTiger.css";

const names = { all: "全部榜", org: "机构榜", hot_money: "游资榜" };
const finished = new Set([
  "completed",
  "succeeded",
  "no_change",
  "partial",
  "failed",
  "cancelled",
  "paused",
]);

function SeatTable({ rows, side }: { rows: unknown; side: "buy" | "sell" }) {
  const items: Row[] = Array.isArray(rows) ? rows : [];
  return (
    <section className={`dragon-tiger-seat-side ${side}`}>
      <h3>{side === "buy" ? "买方席位前五" : "卖方席位前五"}</h3>
      {items.length ? (
        <div className="dragon-tiger-seat-scroll">
          <table>
            <thead>
              <tr>
                <th>席位名称</th>
                <th>买入（万元）</th>
                <th>卖出（万元）</th>
                <th>净买额（万元）</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row, index) => (
                <tr key={`${row.source_id ?? "unknown"}:${index}`}>
                  <td>
                    <span className="dragon-tiger-seat-rank">
                      {row.rank ?? index + 1}
                    </span>
                    {row.name || "来源未提供名称"}
                  </td>
                  <td className="up">
                    {dragonTigerNumber(row.buy) == null
                      ? "—"
                      : (row.buy / 1e4).toLocaleString("zh-CN", {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                  </td>
                  <td className="down">
                    {dragonTigerNumber(row.sell) == null
                      ? "—"
                      : (row.sell / 1e4).toLocaleString("zh-CN", {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                  </td>
                  <td className={tone(row.net)}>
                    {dragonTigerNumber(row.net) == null
                      ? "—"
                      : (row.net > 0 ? "+" : "") +
                        (row.net / 1e4).toLocaleString("zh-CN", {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">本次未返回该方向的可核验席位。</p>
      )}
    </section>
  );
}

export default function DragonTiger({
  date,
  onStock,
  onNavigate,
}: {
  date: string;
  onStock: (id: string, navigation?: StockNavigation) => void;
  onNavigate: (route: string, context?: Row) => void;
}) {
  const result = useApi<Row>(
    date ? `/market/dragon-tiger?date=${encodeURIComponent(date)}` : null,
  );
  const data = result.data?.date === date ? result.data : null;
  const [board, setBoard] = useState<DragonTigerBoard>("all"),
    [period, setPeriod] = useState<DragonTigerPeriod>(1);
  const [query, setQuery] = useState(""),
    [label, setLabel] = useState<string | null>(null);
  const [sort, setSort] = useState<DragonTigerSort>({
      key: "net",
      order: "desc",
    }),
    [page, setPage] = useState(1);
  const [selectedKey, setSelectedKey] = useState<string | null>(null),
    [seatOpen, setSeatOpen] = useState(false),
    [recordId, setRecordId] = useState<string | null>(null);
  const [locateRevision, setLocateRevision] = useState(0),
    [busy, setBusy] = useState(false),
    [actionError, setActionError] = useState("");
  const [job, setJob] = useState<{ id: string; date: string } | null>(null);
  const toast = useToast(),
    active = usePageActive();
  const searchRef = useRef<HTMLInputElement>(null),
    seatRef = useRef<HTMLDivElement>(null),
    seatTitleRef = useRef<HTMLSpanElement>(null);
  const rowRefs = useRef(new Map<string, HTMLTableRowElement>()),
    completedJob = useRef("");
  const sourceRows = useMemo(() => dragonTigerRows(data, board), [data, board]);
  const allLabels = useMemo(
    () => dragonTigerLabels(dragonTigerRows(data, "hot_money"), period),
    [data, period],
  );
  const rows = useMemo(
    () =>
      sortDragonTigerRows(
        filterDragonTigerRows(
          sourceRows,
          period,
          query,
          board === "hot_money" ? label : null,
        ),
        sort,
        board,
      ),
    [sourceRows, period, query, label, sort, board],
  );
  const selected = rows.find((row) => row.key === selectedKey) ?? null;
  const currentPage = Math.max(
    1,
    Math.min(page, Math.max(1, Math.ceil(rows.length / 25))),
  );
  const boardSource = data?.boards?.[board];
  const boardAvailable = dragonTigerBoardAvailable(data, board);
  const hotStatistics = dragonTigerHotStatistics(data);
  const summary = dragonTigerSummary(rows, board, label, boardAvailable);
  const seatRequest =
    seatOpen && selected?.instrument_id
      ? `/market/dragon-tiger/${encodeURIComponent(selected.instrument_id)}?date=${encodeURIComponent(date)}&range_days=${period}`
      : null;
  const seats = useApi<Row>(seatRequest);
  const seatData =
    seats.data?.date === date &&
    seats.data?.instrument_id === selected?.instrument_id &&
    seats.data?.range_days === period
      ? seats.data
      : null;
  const disclosure = dragonTigerDisclosure(seatData?.records, recordId);
  const seatBuy = dragonTigerNumber(disclosure?.summary?.buy_value);
  const seatSell = dragonTigerNumber(disclosure?.summary?.sell_value);
  const seatBuyRatio =
    seatBuy != null &&
    seatSell != null &&
    seatBuy >= 0 &&
    seatSell >= 0 &&
    seatBuy + seatSell > 0
      ? (seatBuy / (seatBuy + seatSell)) * 100
      : null;
  const task = useApi<Row>(job ? `/tasks/${encodeURIComponent(job.id)}` : null);
  const taskData = task.data?.task || task.data;
  const coverage = data?.coverage || {};
  const expectedDates: string[] = Array.isArray(coverage.expected_dates)
    ? coverage.expected_dates
    : [];
  const retainedDates = new Set<string>(
    Array.isArray(coverage.retained_dates) ? coverage.retained_dates : [],
  );
  const excluded = sourceRows.filter(
    (row) => !row.instrument_id && row.range_days === period,
  );

  function clearSelection() {
    setSelectedKey(null);
    setSeatOpen(false);
    setRecordId(null);
  }
  useEffect(() => {
    clearSelection();
    setPage(1);
  }, [date]);
  useEffect(() => {
    if (selectedKey && !selected) clearSelection();
  }, [selectedKey, selected]);
  useLayoutEffect(() => {
    if (!seatOpen || !selected) return;
    seatRef.current?.scrollIntoView({ block: "start" });
    seatTitleRef.current?.focus({ preventScroll: true });
  }, [selectedKey, period, locateRevision]);
  useEffect(() => {
    if (!job || !active || finished.has(taskData?.status)) return;
    const timer = setInterval(task.reload, 1500);
    return () => clearInterval(timer);
  }, [job?.id, active, taskData?.status, task.reload]);
  useEffect(() => {
    if (
      !job ||
      !finished.has(taskData?.status) ||
      completedJob.current === job.id
    )
      return;
    completedJob.current = job.id;
    result.reload();
  }, [job?.id, taskData?.status, result.reload]);

  function showSeats(row: DragonTigerRow) {
    setSelectedKey(row.key);
    setSeatOpen(true);
    setRecordId(null);
    setLocateRevision((value) => value + 1);
  }
  function changeBoard(value: DragonTigerBoard) {
    setBoard(value);
    setLabel(null);
    setPage(1);
    clearSelection();
  }
  function changePeriod(value: DragonTigerPeriod) {
    const replacement =
      selected &&
      sourceRows.find(
        (row) =>
          row.instrument_id === selected.instrument_id &&
          row.label_name === selected.label_name &&
          row.range_days === value,
      );
    setPeriod(value);
    setPage(1);
    setRecordId(null);
    if (replacement) setSelectedKey(replacement.key);
    else clearSelection();
    if (seatOpen) setLocateRevision((value) => value + 1);
  }
  function changeSort(key: DragonTigerSortKey) {
    setSort((current) => ({
      key,
      order:
        current.key === key && current.order === "desc"
          ? "asc"
          : key === "name" && current.key !== key
            ? "asc"
            : "desc",
    }));
    setPage(1);
  }
  function openChart(row: DragonTigerRow) {
    if (!row.instrument_id) return;
    onStock(
      row.instrument_id,
      createStockNavigation(uniqueDragonTigerStocks(rows), {
        source: "market",
        scopeKey: `dragon-tiger:${date}:${board}:${period}:${label || "all"}:${query}:${sort.key}:${sort.order}`,
        label: "龙虎榜当前筛选",
        asOf: date,
      }),
    );
  }
  function returnToRow() {
    const index = rows.findIndex((row) => row.key === selectedKey);
    if (index >= 0) setPage(Math.floor(index / 25) + 1);
    setSeatOpen(false);
    requestAnimationFrame(() => {
      const target = selectedKey ? rowRefs.current.get(selectedKey) : null;
      target?.scrollIntoView({ block: "center" });
      target?.focus({ preventScroll: true });
    });
  }
  async function backfill() {
    if (busy || coverage.can_backfill !== true) return;
    setBusy(true);
    setActionError("");
    try {
      const response = await post("/market/dragon-tiger/jobs", {
        date,
        backfill: true,
        request_key: requestKey(),
      });
      const id = dragonTigerJobId(response);
      if (!id)
        throw new Error(
          "任务已返回，但未提供可跟踪的任务编号，请到数据中心核对",
        );
      setJob({ id, date });
      toast("龙虎榜补齐任务已创建，可查看实际采集进度");
    } catch (error: any) {
      setActionError(error.message);
    } finally {
      setBusy(false);
    }
  }
  const taskRunning = !!job && !finished.has(taskData?.status);
  const boardUnavailable = !boardAvailable;
  return (
    <div className="dragon-tiger-page">
      <div className="scopebar">
        <span>按观察日核对龙虎榜与买卖席位，单日和三日累计分别查看。</span>
        <Badge>{date}</Badge>
      </div>
      <Panel
        title="龙虎榜"
        subtitle="只读本地已留存数据 · 名称查看K线，整行展开买卖席位"
        actions={
          <div className="button-group">
            <button
              className="btn small"
              onClick={result.reload}
              aria-label="刷新本地龙虎榜"
            >
              <RefreshCw size={14} />
            </button>
            <button
              className="btn small primary"
              onClick={() => void backfill()}
              disabled={busy || taskRunning || coverage.can_backfill !== true}
              title={coverage.reason || "手动补齐最近30个市场交易日"}
            >
              {busy
                ? "正在创建任务…"
                : coverage.complete === true
                  ? "近30日响应已留存"
                  : "补齐最近30个交易日"}
            </button>
          </div>
        }
      >
        <div className="dragon-tiger-controls">
          <div className="segmented">
            {(["all", "org", "hot_money"] as const).map((value) => (
              <button
                key={value}
                className={board === value ? "active" : ""}
                aria-pressed={board === value}
                onClick={() => changeBoard(value)}
              >
                {names[value]}
              </button>
            ))}
          </div>
          <div className="segmented">
            {([1, 3] as const).map((value) => (
              <button
                key={value}
                className={period === value ? "active" : ""}
                aria-pressed={period === value}
                onClick={() => changePeriod(value)}
              >
                {value === 1 ? "单日榜" : "三日榜"}
              </button>
            ))}
          </div>
          {board === "hot_money" && label && (
            <button
              className="btn small"
              aria-label={`取消游资标签筛选 ${label}`}
              onClick={() => {
                setLabel(null);
                setPage(1);
                clearSelection();
              }}
            >
              标签：{label}
              <X size={13} />
            </button>
          )}
          <div className="dragon-tiger-search">
            <input
              ref={searchRef}
              className="search-input"
              aria-label="搜索龙虎榜股票或概念"
              placeholder="股票、代码或来源概念…"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
                clearSelection();
              }}
            />
            {query && (
              <button
                type="button"
                className="icon-btn"
                aria-label="清除龙虎榜搜索"
                title="清除搜索"
                onClick={() => {
                  setQuery("");
                  setPage(1);
                  clearSelection();
                  searchRef.current?.focus();
                }}
              >
                <X size={15} />
              </button>
            )}
          </div>
        </div>
        {coverage.reason && (
          <p className="dragon-tiger-scope">{coverage.reason}</p>
        )}
        {actionError && <ErrorBox message={actionError} />}
        {job && (
          <div className="dragon-tiger-task">
            <span>截至{job.date}的30日补齐</span>
            <Status value={taskData?.status || "queued"} />
            {task.error && <span className="muted">{task.error}</span>}
            <button
              className="btn small"
              onClick={() => onNavigate("data", { task_id: job.id })}
            >
              查看任务进度
            </button>
          </div>
        )}
      </Panel>
      {result.error ? (
        <ErrorBox message={result.error} retry={result.reload} />
      ) : result.loading && !data ? (
        <Loading />
      ) : (
        <>
          <div className="dragon-tiger-summary">
            {[
              [
                "已留存上榜A股",
                summary.count == null ? "—" : `${integer(summary.count)}只`,
                "",
              ],
              [summary.netLabel, marketMoney(summary.net), tone(summary.net)],
              ["龙虎榜买入额（股票去重）", marketMoney(summary.buy), ""],
              ["龙虎榜卖出额（股票去重）", marketMoney(summary.sell), ""],
            ].map(([title, value, color]) => (
              <section key={title}>
                <span>{title}</span>
                <strong className={color}>{value}</strong>
              </section>
            ))}
          </div>
          <div className="dragon-tiger-grid">
            <Panel
              title={`${names[board]} · ${period === 1 ? "单日榜" : "三日榜"}`}
              subtitle={`${boardAvailable ? `${rows.length}条当前筛选记录` : "该日响应未留存"} · ${board === "hot_money" ? label || "全部标签" : "当前筛选"}`}
            >
              {rows.length ? (
                <DragonTigerTable
                  rows={rows}
                  board={board}
                  page={currentPage}
                  sort={sort}
                  selectedKey={selectedKey}
                  seatOpen={seatOpen}
                  onSort={changeSort}
                  onSeat={showSeats}
                  onStock={openChart}
                  registerRow={(key, element) => {
                    if (element) rowRefs.current.set(key, element);
                    else rowRefs.current.delete(key);
                  }}
                  onPage={(value) => {
                    setPage(value);
                    clearSelection();
                  }}
                />
              ) : (
                <Empty
                  title={
                    boardUnavailable
                      ? "该日龙虎榜数据尚未就绪"
                      : "当前筛选无匹配记录"
                  }
                  description={
                    boardUnavailable
                      ? boardSource?.reason ||
                        data?.availability?.reason ||
                        "未取得或尚未留存的数据保持未知；可在最新行情日手动补齐。"
                      : "可清除搜索或取消游资标签筛选。"
                  }
                />
              )}
              <p className="panel-footnote">
                {board === "hot_money" ? (
                  <>
                    来源统计 count={hotStatistics.sourceCount ?? "—"} /
                    stock_count=
                    {hotStatistics.sourceStockCount ?? "—"}（口径未核验）；
                    实际已观察{hotStatistics.labelCount ?? "—"}个标签、
                    {hotStatistics.rowCount ?? "—"}条标签记录、
                    {hotStatistics.stockCount ?? "—"}个来源代码去重标的、
                    {hotStatistics.stockRangeCount ?? "—"}
                    个标的×区间（所选日全部来源区间，含未匹配名录的来源标的；其他区间另行留存，不混入单日或三日榜）。
                  </>
                ) : (
                  <>
                    源标的{boardSource?.source_stock_count ?? "—"}个 / 原始记录
                    {boardSource?.source_count ?? "—"}条；
                  </>
                )}
                表格与顶部统计仅纳入本地名录已核验A股，按当前区间与筛选计算。来源概念资料不等同当日上涨归因。
                {excluded.length > 0 &&
                  ` 当前区间另有${excluded.length}条未匹配记录：${excluded.map((row) => row.name || row.code || "标识未知").join("、")}。`}
              </p>
            </Panel>
            <div className="dragon-tiger-side">
              <Panel
                title="游资标签关联"
                subtitle="来源分类 · 再点同一标签可取消"
              >
                <div className="dragon-tiger-labels">
                  {allLabels.length ? (
                    allLabels.map((item) => (
                      <button
                        key={item.name}
                        className={label === item.name ? "active" : ""}
                        aria-pressed={label === item.name}
                        onClick={() => {
                          setBoard("hot_money");
                          setLabel((current) =>
                            current === item.name ? null : item.name,
                          );
                          setPage(1);
                          clearSelection();
                        }}
                      >
                        {item.name}
                        <span>{item.count}股</span>
                      </button>
                    ))
                  ) : (
                    <span className="muted">当前区间未留存可核验标签。</span>
                  )}
                </div>
                <p className="panel-footnote">
                  标签由来源提供，不等同实际账户身份。全部标签的股票总额按股票与区间去重，标签净额不跨组相加。
                </p>
              </Panel>
              {selected && (
                <Panel title="当前个股记录">
                  <div className="dragon-tiger-selected">
                    <button
                      className="stock-name-link"
                      onClick={() => openChart(selected)}
                    >
                      {selected.name || selected.instrument_id}
                    </button>
                    <CopyStockCode
                      code={selected.code}
                      instrumentId={selected.instrument_id}
                    />
                    <span>
                      {date} · {period === 1 ? "单日榜" : "三日累计榜"}
                    </span>
                    <dl>
                      <div>
                        <dt>股票龙虎榜净买额</dt>
                        <dd className={tone(selected.net_value)}>
                          {marketMoney(selected.net_value)}
                        </dd>
                      </div>
                      <div>
                        <dt>机构净买额</dt>
                        <dd className={tone(selected.org_net_value)}>
                          {marketMoney(selected.org_net_value)}
                        </dd>
                      </div>
                      <div>
                        <dt>当日涨幅</dt>
                        <dd className={tone(selected.change_pct)}>
                          {percent(selected.change_pct)}
                        </dd>
                      </div>
                    </dl>
                    <button
                      className="btn small"
                      onClick={() => showSeats(selected)}
                    >
                      定位买卖席位
                    </button>
                  </div>
                </Panel>
              )}
            </div>
          </div>
          <div
            id="dragon-tiger-seats"
            ref={seatRef}
            hidden={!seatOpen || !selected}
            className="dragon-tiger-seats"
          >
            <Panel
              title={
                <span ref={seatTitleRef} tabIndex={-1}>
                  买卖席位 · {selected?.name || selected?.instrument_id}{" "}
                  {selected?.code}
                </span>
              }
              subtitle={`${date} · ${period === 1 ? "单日榜" : "三日累计榜"}`}
              actions={
                <button className="btn small" onClick={returnToRow}>
                  返回榜单并收起
                </button>
              }
            >
              {seats.error ? (
                <ErrorBox message={seats.error} retry={seats.reload} />
              ) : seats.loading && !seatData ? (
                <Loading />
              ) : disclosure ? (
                <div className="dragon-tiger-seat-content">
                  {seatData!.records.length > 1 && (
                    <label className="dragon-tiger-record-choice">
                      披露原因
                      <select
                        aria-label="选择龙虎榜披露记录"
                        value={disclosure.record_id}
                        onChange={(event) => setRecordId(event.target.value)}
                      >
                        {seatData!.records.map((record: Row) => (
                          <option
                            key={record.record_id}
                            value={record.record_id}
                          >
                            {record.reason || record.record_id}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <p className="dragon-tiger-scope">
                    {disclosure.reason || "来源未提供披露原因"}
                  </p>
                  {disclosure.summary && (
                    <div className="dragon-tiger-seat-summary">
                      {[
                        ["龙虎榜买入", disclosure.summary.buy_value, "up"],
                        ["龙虎榜卖出", disclosure.summary.sell_value, "down"],
                        [
                          "龙虎榜净额",
                          disclosure.summary.net_value,
                          tone(disclosure.summary.net_value),
                        ],
                      ].map(([label, value, color]) => (
                        <div key={String(label)}>
                          <span>{label}</span>
                          <strong className={String(color)}>
                            {marketMoney(value)}
                          </strong>
                        </div>
                      ))}
                    </div>
                  )}
                  {seatBuyRatio != null && (
                    <div
                      className="dragon-tiger-seat-meter"
                      aria-label="该披露记录买入与卖出金额比例"
                    >
                      <span style={{ width: `${seatBuyRatio}%` }} />
                      <span style={{ width: `${100 - seatBuyRatio}%` }} />
                    </div>
                  )}
                  <div className="dragon-tiger-seat-columns">
                    <SeatTable rows={disclosure.buy} side="buy" />
                    <SeatTable rows={disclosure.sell} side="sell" />
                  </div>
                  <p className="panel-footnote">
                    同名匿名机构及买卖双向上榜按披露行分别保留，不直接相加；不足五行按实际来源展示。披露记录：
                    {disclosure.record_id}。
                    {disclosure.source
                      ? ` 来源：${typeof disclosure.source === "string" ? disclosure.source : disclosure.source.name || disclosure.source.provider || "已留存原始来源"}。`
                      : ""}
                    {disclosure.reconciliation?.reason || ""}
                  </p>
                  {Array.isArray(seatData?.warnings) &&
                    seatData.warnings.map((warning: unknown, index: number) => (
                      <p className="dragon-tiger-scope" key={index}>
                        {typeof warning === "string"
                          ? warning
                          : (warning as Row)?.reason || "来源范围待核验"}
                      </p>
                    ))}
                </div>
              ) : (
                <Empty
                  title="当前区间未取得可核验席位"
                  description={
                    seatData?.reason ||
                    seatData?.availability?.reason ||
                    "席位缺口保持未知，不用其他日期或三日累计记录替代。"
                  }
                />
              )}
            </Panel>
          </div>
          <Panel
            title="来源与30日留存"
            subtitle="按市场交易日滚动；读取页面不发起采集"
          >
            <div className="dragon-tiger-coverage">
              {expectedDates.length > 0 && (
                <div
                  className="dragon-tiger-date-strip"
                  aria-label="龙虎榜留存覆盖"
                >
                  {expectedDates.map((value) => (
                    <span
                      key={value}
                      title={`${value} · ${retainedDates.has(value) ? "已留存" : "缺口待补"}`}
                      className={
                        retainedDates.has(value) ? "retained" : "missing"
                      }
                    >
                      {value.slice(5)}
                    </span>
                  ))}
                </div>
              )}
              <p>
                最新已发布龙虎榜日：{coverage.anchor_date || "尚未留存"}
                ；窗口内已留存
                {expectedDates.length
                  ? expectedDates.filter((value) => retainedDates.has(value))
                      .length
                  : "—"}
                /{expectedDates.length || "—"}
                个市场交易日。单日、三日与不同披露原因分别留存，不将重叠区间累计。
              </p>
              {Array.isArray(data?.warnings) &&
                data.warnings.map((warning: unknown, index: number) => (
                  <p key={index}>
                    {typeof warning === "string"
                      ? warning
                      : (warning as Row)?.reason || "来源范围待核验"}
                  </p>
                ))}
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}
